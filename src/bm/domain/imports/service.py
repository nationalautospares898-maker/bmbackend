"""Imports (M23): upload, parse, review, apply (FR-IMP-001…010).

The shape of this milestone is set by one requirement:

> Nothing auto-creates products or stock from a fuzzy guess (FR-IMP-007).

So an import is **four separate acts**, not one: the file arrives, a worker reads it, a
human rules on the rows the software could not resolve, and only then is anything
applied. Each act leaves the job in a state the next one guards on, which is what makes
the whole thing safe to retry at any point.

The invariants worth stating outright:

1. **`price_list` produces zero stock movements, ever** (FR-IMP-002). Not "none in
   practice" — the apply path for a price list never constructs a purchase and never
   calls the inventory service, and a test asserts the movement count is unchanged.
2. **A `purchase_receipt` receives stock only through `PurchaseService`** — it builds a
   purchase and confirms it, exactly as a counter hand would. Imports do not get their
   own stock-writing path, because then there would be two, and one of them would
   eventually diverge (FR-PUR-003).
3. **`applied` is terminal** (FR-IMP-005). The transition into `applying` is a
   conditional `UPDATE` with a status guard, so two concurrent applies cannot both
   proceed; the loser sees the job already moving and is told so.
4. **Apply is one transaction** (FR-IMP-008). A row that fails is recorded as `failed`
   and the *whole* job rolls back — a half-applied supplier bill is worse than a
   rejected one, because nobody can tell which half.
5. **Every row's outcome is recorded** (FR-IMP-009), including the reason it was
   skipped, and the counts on the job are derived from the rows rather than incremented
   as it goes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bm.config import Settings
from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.catalog.service import CatalogService
from bm.domain.imports.matcher import RowMatcher
from bm.domain.imports.parser import ParsedRow, ParseFailed, parse
from bm.domain.purchases.service import PurchaseLine, PurchaseService
from bm.domain.shared.clock import Clock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    DuplicateFile,
    FileTooLarge,
    ImportAlreadyApplied,
    ImportRowsPendingDecision,
    NotFound,
    UnsupportedFileType,
    ValidationFailed,
)
from bm.infra.db.enums import (
    DECISION_REQUIRED_MATCH_STATUSES,
    ImportApplyResult,
    ImportDecision,
    ImportIntent,
    ImportMatchStatus,
    ImportStatus,
    JobKind,
)
from bm.infra.db.models.catalog import Product
from bm.infra.db.models.infra import ImportJob, ImportRow
from bm.infra.db.rows import rows_affected
from bm.infra.storage.objects import ObjectStore, sha256_of, sniff_extension

#: States an apply may start from. `parsed` and `needs_review` are both legal: a file
#: whose every row resolved exactly never needs review, and refusing to apply it until
#: someone visits a review screen with nothing on it would be theatre.
_APPLIABLE: tuple[ImportStatus, ...] = (ImportStatus.PARSED, ImportStatus.NEEDS_REVIEW)

#: States a cancel may start from — anything before the work begins (FR-IMP-005).
_CANCELLABLE: tuple[ImportStatus, ...] = (
    ImportStatus.UPLOADED,
    ImportStatus.PARSING,
    ImportStatus.PARSED,
    ImportStatus.NEEDS_REVIEW,
    ImportStatus.FAILED,
)

#: Hard cap on rows in one file.
#:
#: Not a performance limit — a guard against a 400 MB CSV becoming 2 million review
#: rows nobody will ever rule on. A supplier price list that genuinely exceeds this
#: should be split, and the message says so.
MAX_ROWS = 20_000


@dataclass(frozen=True, slots=True)
class JobView:
    id: uuid.UUID
    #: Monotonic upload order, and the pagination cursor (migration `0020`).
    ordinal: int
    intent: str
    status: str
    original_filename: str
    file_size_bytes: int
    supplier_id: uuid.UUID | None
    row_count: int
    rows_applied: int
    rows_skipped: int
    rows_failed: int
    rows_pending_decision: int
    error_summary: str | None
    created_purchase_id: uuid.UUID | None
    duplicate_of_job_id: uuid.UUID | None
    created_at: datetime
    applied_at: datetime | None


@dataclass(frozen=True, slots=True)
class RowView:
    id: uuid.UUID
    row_index: int
    raw_values: dict[str, Any]
    parsed_name: str | None
    parsed_sku: str | None
    parsed_quantity: Decimal | None
    parsed_unit_cost: Decimal | None
    parsed_selling_price: Decimal | None
    match_status: str
    matched_product_id: uuid.UUID | None
    matched_product_name: str | None
    candidate_product_ids: list[uuid.UUID]
    match_confidence: Decimal | None
    decision: str
    apply_result: str | None
    apply_error: str | None
    needs_decision: bool


@dataclass(frozen=True, slots=True)
class UploadResult:
    job_id: uuid.UUID
    status: str
    #: Set when the same bytes were already uploaded and no override was given
    #: (FR-IMP-004). The caller gets the *existing* job, not a second one.
    duplicate_of_job_id: uuid.UUID | None = None


class ImportService:
    def __init__(
        self,
        session: Session,
        clock: Clock,
        *,
        store: ObjectStore,
        max_upload_bytes: int,
    ) -> None:
        self._session = session
        self._clock = clock
        self._store = store
        self._max_upload_bytes = max_upload_bytes

    @classmethod
    def from_settings(cls, session: Session, clock: Clock, settings: Settings) -> ImportService:
        return cls(
            session,
            clock,
            store=ObjectStore(settings.storage_path),
            max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
        )

    # --- upload ---------------------------------------------------------------

    def upload(
        self,
        ctx: AuthContext,
        *,
        filename: str,
        content: bytes,
        intent: ImportIntent,
        supplier_id: uuid.UUID | None = None,
        allow_duplicate: bool = False,
        duplicate_reason: str | None = None,
    ) -> UploadResult:
        """Validate, store and record the file; enqueue the parse (FR-IMP-001/003/004).

        The order is deliberate: everything that can refuse the upload runs *before* a
        single byte is written, so a rejected file leaves nothing behind.
        """
        ctx.require(Permission.IMPORTS_MANAGE)

        if len(content) == 0:
            raise ValidationFailed({"file": "The file is empty."})
        if len(content) > self._max_upload_bytes:
            raise FileTooLarge(
                details={
                    "size_bytes": len(content),
                    "max_bytes": self._max_upload_bytes,
                }
            )

        extension = sniff_extension(filename, content)
        if extension is None:
            raise UnsupportedFileType(
                details={"filename": filename, "accepted": [".csv", ".xlsx", ".pdf"]}
            )

        if intent is ImportIntent.PURCHASE_RECEIPT and supplier_id is None:
            # FR-IMP-002. Also a CHECK constraint, but refusing here gives the user a
            # field-level message instead of an integrity error.
            raise ValidationFailed(
                {"supplier_id": "A supplier bill import has to say which supplier it is from."}
            )
        if supplier_id is not None:
            exists = self._session.execute(
                text("SELECT 1 FROM suppliers WHERE id = :id AND business_id = :business_id"),
                {"id": supplier_id, "business_id": ctx.business_id},
            ).scalar_one_or_none()
            if exists is None:
                raise ValidationFailed({"supplier_id": "That supplier does not exist."})

        digest = sha256_of(content)
        existing = self._session.execute(
            select(ImportJob.id, ImportJob.status).where(
                ImportJob.business_id == ctx.business_id,
                ImportJob.file_sha256 == digest,
                # The **primary** job for these bytes. An override row carries the same
                # digest, so without this a third upload would be reported as a
                # duplicate of the override rather than of the original.
                ImportJob.duplicate_of_job_id.is_(None),
            )
        ).first()
        if existing is not None and not allow_duplicate:
            # FR-IMP-004: return the *existing* job rather than a second one. The client
            # shows the warning and may re-submit with an override and a reason.
            raise DuplicateFile(
                details={"existing_job": {"id": str(existing.id), "status": str(existing.status)}}
            )
        if existing is not None and allow_duplicate:
            reason = (duplicate_reason or "").strip()
            if not reason:
                raise ValidationFailed(
                    {
                        "duplicate_override_reason": "Say why this file is being uploaded again.",
                    }
                )

        job_id = uuid.uuid4()
        key = ObjectStore.generate_key(extension=extension, now=self._clock.now())
        size = self._store.put(key, content)

        try:
            self._session.execute(
                insert(ImportJob).values(
                    id=job_id,
                    business_id=ctx.business_id,
                    branch_id=ctx.requires_branch,
                    intent=intent.value,
                    supplier_id=supplier_id,
                    original_filename=filename[:200],
                    stored_object_key=key,
                    content_type=extension,
                    file_size_bytes=size,
                    # The real digest, always. `uq_import_jobs__file` is partial on
                    # `duplicate_of_job_id IS NULL` (migration `0019`), so an audited
                    # override coexists with the primary job instead of needing a
                    # doctored value in a column named after a hash.
                    file_sha256=digest,
                    status=ImportStatus.UPLOADED.value,
                    duplicate_of_job_id=existing.id if existing is not None else None,
                    duplicate_override_reason=(
                        duplicate_reason.strip()
                        if existing is not None and duplicate_reason
                        else None
                    ),
                    uploaded_by_user_id=ctx.user_id,
                )
            )
            self._session.flush()
        except IntegrityError:
            # The blob is already on disk and the row will not exist; remove it rather
            # than leaving an orphan nobody will ever look for.
            self._store.delete(key)
            raise

        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.IMPORT_UPLOADED,
            entity_type="import_job",
            entity_id=job_id,
            after={
                "filename": filename,
                "intent": intent.value,
                "size_bytes": size,
                "sha256": digest,
            },
        )
        if existing is not None:
            # An override is a decision somebody made; it is recorded as one.
            audit.record_for(
                self._session,
                ctx,
                action=AuditAction.IMPORT_DUPLICATE_OVERRIDDEN,
                entity_type="import_job",
                entity_id=job_id,
                reason=(duplicate_reason or "").strip() or None,
                after={"duplicate_of_job_id": str(existing.id)},
            )

        self._enqueue_parse(job_id)
        return UploadResult(
            job_id=job_id,
            status=ImportStatus.UPLOADED.value,
            duplicate_of_job_id=existing.id if existing is not None else None,
        )

    def _enqueue_parse(self, job_id: uuid.UUID) -> None:
        """Queue the parse (FR-IMP-005).

        `dedupe_key` is the job id, so a retried upload path cannot enqueue two parses
        for the same job. `ON CONFLICT DO NOTHING` because the queue's uniqueness is the
        authority here, not a prior read.
        """
        self._session.execute(
            text(
                """
                INSERT INTO jobs (id, kind, payload, status, dedupe_key)
                VALUES (:id, :kind, :payload, 'queued', :dedupe)
                ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
                """
            ),
            {
                "id": uuid.uuid4(),
                "kind": JobKind.IMPORT_PARSE.value,
                "payload": f'{{"import_job_id": "{job_id}"}}',
                "dedupe": f"import_parse:{job_id}",
            },
        )

    # --- parse ----------------------------------------------------------------

    def parse_job(self, job_id: uuid.UUID) -> None:
        """Read the file and write its rows. Runs in the worker (FR-IMP-005).

        Takes no `AuthContext`: this is the queue acting on a job somebody already had
        permission to create. The business scope comes from the job row itself.

        Idempotent. The guard moves `uploaded → parsing`; a second delivery of the same
        job finds nothing to move and returns, which is what at-least-once delivery
        requires of every handler (`ARCHITECTURE.md §7`).
        """
        moved = rows_affected(
            self._session.execute(
                update(ImportJob)
                .where(
                    ImportJob.id == job_id,
                    ImportJob.status == ImportStatus.UPLOADED.value,
                )
                .values(status=ImportStatus.PARSING.value)
            )
        )
        if not moved:
            return

        job = self._session.execute(
            select(ImportJob).where(ImportJob.id == job_id)
        ).scalar_one_or_none()
        if job is None:  # pragma: no cover - the UPDATE above just matched it
            return

        try:
            content = self._store.get(job.stored_object_key)
            result = parse(job.content_type, content)
        except ParseFailed as exc:
            self._fail(job_id, str(exc))
            return
        except OSError as exc:
            self._fail(job_id, f"The stored file could not be read: {exc}")
            return

        if len(result.rows) > MAX_ROWS:
            self._fail(
                job_id,
                f"This file has {len(result.rows)} rows, more than the {MAX_ROWS} that can "
                "be reviewed in one import. Split it and upload the parts separately.",
            )
            return

        matcher = RowMatcher(self._session, job.business_id, intent=ImportIntent(str(job.intent)))
        seen: dict[str, int] = {}
        needs_review = False

        for row in result.rows:
            match = matcher.classify(row, seen_keys=seen)
            if result.low_confidence and match.match_status in (
                ImportMatchStatus.EXACT_SKU,
                ImportMatchStatus.EXACT_ALIAS,
                ImportMatchStatus.FUZZY_CANDIDATE,
            ):
                # FR-IMP-010: a PDF's columns are a guess, so even an exact code match
                # is only evidence that *some* row mentioned that code. Demote to
                # ambiguous so every row gets a human decision.
                match = type(match)(
                    match_status=ImportMatchStatus.AMBIGUOUS,
                    matched_product_id=None,
                    candidate_product_ids=(
                        [match.matched_product_id]
                        if match.matched_product_id
                        else match.candidate_product_ids
                    ),
                    match_confidence=match.match_confidence,
                    reason="Read from a PDF — check this row against the supplier's document.",
                )
            self._insert_row(job, row, match)
            needs_review = needs_review or match.needs_decision

        self._session.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(
                status=(
                    ImportStatus.NEEDS_REVIEW.value if needs_review else ImportStatus.PARSED.value
                ),
                row_count=len(result.rows),
                error_summary="\n".join(result.notes) or None,
            )
        )

    def _insert_row(self, job: ImportJob, row: ParsedRow, match: Any) -> None:
        self._session.execute(
            insert(ImportRow).values(
                id=uuid.uuid4(),
                import_job_id=job.id,
                business_id=job.business_id,
                row_index=row.row_index,
                raw_values=row.raw_values,
                parsed_name=row.name,
                parsed_sku=row.sku,
                parsed_quantity=row.quantity,
                parsed_unit_cost=row.unit_cost,
                parsed_selling_price=row.selling_price,
                match_status=match.match_status.value,
                matched_product_id=match.matched_product_id,
                candidate_product_ids=match.candidate_product_ids,
                match_confidence=match.match_confidence,
                decision=ImportDecision.PENDING.value,
                apply_error=match.reason,
            )
        )

    def _fail(self, job_id: uuid.UUID, reason: str) -> None:
        self._session.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(status=ImportStatus.FAILED.value, error_summary=reason[:2000])
        )

    # --- read -----------------------------------------------------------------

    def get(self, ctx: AuthContext, job_id: uuid.UUID) -> JobView:
        ctx.require(Permission.IMPORTS_MANAGE)
        job = self._session.execute(
            select(ImportJob).where(
                ImportJob.id == job_id, ImportJob.business_id == ctx.business_id
            )
        ).scalar_one_or_none()
        if job is None:
            raise NotFound("That import does not exist.")
        return self._view(job)

    def _view(self, job: ImportJob) -> JobView:
        pending = (
            self._session.execute(
                select(text("count(*)"))
                .select_from(ImportRow)
                .where(
                    ImportRow.import_job_id == job.id,
                    ImportRow.decision == ImportDecision.PENDING.value,
                    ImportRow.match_status.in_([s.value for s in DECISION_REQUIRED_MATCH_STATUSES]),
                )
            ).scalar()
            or 0
        )
        return JobView(
            id=job.id,
            ordinal=job.ordinal,
            intent=str(job.intent),
            status=str(job.status),
            original_filename=job.original_filename,
            file_size_bytes=job.file_size_bytes,
            supplier_id=job.supplier_id,
            row_count=job.row_count,
            rows_applied=job.rows_applied,
            rows_skipped=job.rows_skipped,
            rows_failed=job.rows_failed,
            rows_pending_decision=int(pending),
            error_summary=job.error_summary,
            created_purchase_id=job.created_purchase_id,
            duplicate_of_job_id=job.duplicate_of_job_id,
            created_at=job.created_at,
            applied_at=job.applied_at,
        )

    def list_jobs(
        self, ctx: AuthContext, *, limit: int = 20, before: int | None = None
    ) -> list[JobView]:
        """Import history (FR-IMP-009). Newest first, cursor on `ordinal`.

        **Ordered and paginated on `ordinal`, not `created_at`** (migration `0020`).
        `created_at` is not unique — it comes from `now()` at the OS clock's resolution,
        so two uploads under load tie — and the old `id DESC` tiebreak was a `uuid4`,
        which is to say random. That made "newest first" a coin flip on a tie, and made
        the `before=<created_at>` cursor able to **skip or repeat** a row depending on
        which side of the boundary it landed. A sequence has neither problem.
        """
        ctx.require(Permission.IMPORTS_MANAGE)
        statement = (
            select(ImportJob)
            .where(ImportJob.business_id == ctx.business_id)
            .order_by(ImportJob.ordinal.desc())
            .limit(limit)
        )
        if before is not None:
            statement = statement.where(ImportJob.ordinal < before)
        return [self._view(job) for job in self._session.execute(statement).scalars().all()]

    def rows(
        self,
        ctx: AuthContext,
        job_id: uuid.UUID,
        *,
        match_status: str | None = None,
        decision: str | None = None,
        limit: int = 50,
        after_index: int | None = None,
    ) -> list[RowView]:
        """The review queue (FR-IMP-006).

        Ordered by `row_index` so the screen matches the file the user is looking at,
        and paginated on it — a cursor on a stable, user-visible ordinal rather than on
        a timestamp two rows can share.
        """
        ctx.require(Permission.IMPORTS_MANAGE)
        self.get(ctx, job_id)  # scope check; raises NotFound for another business

        # The product name is joined rather than fetched per row: the review screen
        # shows it on every line, and an N+1 here would be one query per row of a
        # 5 000-row file.
        statement = (
            select(ImportRow, Product.name)
            .outerjoin(Product, Product.id == ImportRow.matched_product_id)
            .where(ImportRow.import_job_id == job_id)
            .order_by(ImportRow.row_index)
            .limit(limit)
        )
        if match_status:
            statement = statement.where(ImportRow.match_status == match_status)
        if decision:
            statement = statement.where(ImportRow.decision == decision)
        if after_index is not None:
            statement = statement.where(ImportRow.row_index > after_index)

        return [
            RowView(
                id=row.id,
                row_index=row.row_index,
                raw_values=dict(row.raw_values or {}),
                parsed_name=row.parsed_name,
                parsed_sku=row.parsed_sku,
                parsed_quantity=row.parsed_quantity,
                parsed_unit_cost=row.parsed_unit_cost,
                parsed_selling_price=row.parsed_selling_price,
                match_status=str(row.match_status),
                matched_product_id=row.matched_product_id,
                matched_product_name=product_name,
                candidate_product_ids=list(row.candidate_product_ids or []),
                match_confidence=row.match_confidence,
                decision=str(row.decision),
                apply_result=str(row.apply_result) if row.apply_result else None,
                apply_error=row.apply_error,
                needs_decision=(
                    ImportMatchStatus(str(row.match_status)) in DECISION_REQUIRED_MATCH_STATUSES
                    and str(row.decision) == ImportDecision.PENDING.value
                ),
            )
            for row, product_name in self._session.execute(statement).all()
        ]

    # --- review ---------------------------------------------------------------

    def decide(
        self,
        ctx: AuthContext,
        job_id: uuid.UUID,
        row_id: uuid.UUID,
        *,
        decision: ImportDecision,
        matched_product_id: uuid.UUID | None = None,
    ) -> RowView:
        """Record one human decision (FR-IMP-007).

        Only legal before the job is applied. A decision on an applied job would be a
        lie — the row's outcome is already recorded — so it is refused rather than
        stored and ignored.
        """
        ctx.require(Permission.IMPORTS_MANAGE)
        job = self._session.execute(
            select(ImportJob)
            .where(ImportJob.id == job_id, ImportJob.business_id == ctx.business_id)
            .with_for_update()
        ).scalar_one_or_none()
        if job is None:
            raise NotFound("That import does not exist.")
        if str(job.status) == ImportStatus.APPLIED.value:
            raise ImportAlreadyApplied()
        if ImportStatus(str(job.status)) not in _APPLIABLE:
            raise ValidationFailed(
                {"status": f"An import that is {job.status} cannot be reviewed."}
            )

        row = self._session.execute(
            select(ImportRow).where(ImportRow.id == row_id, ImportRow.import_job_id == job_id)
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("That row does not exist in this import.")

        if decision is ImportDecision.PENDING:
            raise ValidationFailed({"decision": "Choose accept, create or skip."})

        if decision is ImportDecision.ACCEPT_MATCH:
            chosen = matched_product_id or row.matched_product_id
            if chosen is None:
                raise ValidationFailed({"matched_product_id": "Say which product this row is."})
            if matched_product_id is not None:
                candidates = list(row.candidate_product_ids or [])
                if row.matched_product_id is not None:
                    candidates.append(row.matched_product_id)
                if matched_product_id not in candidates:
                    # The user may only pick from what the matcher offered. Accepting an
                    # arbitrary id would make the review screen a product picker with no
                    # validation, and a mistyped id would silently reprice a part
                    # nobody mentioned.
                    raise ValidationFailed(
                        {
                            "matched_product_id": "That product was not one of the "
                            "candidates for this row."
                        }
                    )
            self._session.execute(
                update(ImportRow)
                .where(ImportRow.id == row_id)
                .values(
                    decision=decision.value,
                    matched_product_id=chosen,
                    decided_by_user_id=ctx.user_id,
                )
            )
        else:
            if decision is ImportDecision.CREATE_NEW:
                if str(row.match_status) == ImportMatchStatus.INVALID.value:
                    raise ValidationFailed(
                        {
                            "decision": "This row could not be read, so there is nothing "
                            "to create from it. Skip it, or fix the file and upload again.",
                        }
                    )
                if not row.parsed_name:
                    raise ValidationFailed(
                        {"decision": "A new product needs a name, and this row has none."}
                    )
                if ImportIntent(str(job.intent)) is ImportIntent.PRICE_LIST and (
                    row.parsed_selling_price is None
                ):
                    raise ValidationFailed(
                        {
                            "decision": "A new product needs a selling price, and this "
                            "row has only a cost.",
                        }
                    )
            self._session.execute(
                update(ImportRow)
                .where(ImportRow.id == row_id)
                .values(
                    decision=decision.value,
                    matched_product_id=None
                    if decision is ImportDecision.CREATE_NEW
                    else row.matched_product_id,
                    decided_by_user_id=ctx.user_id,
                )
            )

        self._session.flush()
        found = [r for r in self.rows(ctx, job_id, limit=MAX_ROWS) if r.id == row_id]
        return found[0]

    def decide_bulk(
        self,
        ctx: AuthContext,
        job_id: uuid.UUID,
        *,
        match_status: ImportMatchStatus,
        decision: ImportDecision,
    ) -> int:
        """Apply one decision to every pending row in a status. Returns the count.

        Reviewing 400 `new_product` rows one at a time is not review, it is data entry,
        and a user faced with it will click through without reading. A bulk decision on
        a *whole status* is an honest unit of judgement — "yes, create all the parts this
        supplier lists that we don't carry" is a sentence someone can actually mean.
        Per-row overrides still work, and `invalid` is excluded because there is nothing
        to decide.
        """
        ctx.require(Permission.IMPORTS_MANAGE)
        job = self._session.execute(
            select(ImportJob)
            .where(ImportJob.id == job_id, ImportJob.business_id == ctx.business_id)
            .with_for_update()
        ).scalar_one_or_none()
        if job is None:
            raise NotFound("That import does not exist.")
        if str(job.status) == ImportStatus.APPLIED.value:
            raise ImportAlreadyApplied()
        if ImportStatus(str(job.status)) not in _APPLIABLE:
            raise ValidationFailed(
                {"status": f"An import that is {job.status} cannot be reviewed."}
            )
        if decision is ImportDecision.PENDING:
            raise ValidationFailed({"decision": "Choose accept, create or skip."})
        if match_status is ImportMatchStatus.INVALID and decision is not ImportDecision.SKIP:
            raise ValidationFailed({"decision": "Rows that could not be read can only be skipped."})
        if decision is ImportDecision.ACCEPT_MATCH and match_status in (
            ImportMatchStatus.NEW_PRODUCT,
            ImportMatchStatus.INVALID,
        ):
            raise ValidationFailed({"decision": "These rows have no matched product to accept."})

        statement = (
            update(ImportRow)
            .where(
                ImportRow.import_job_id == job_id,
                ImportRow.match_status == match_status.value,
                ImportRow.decision == ImportDecision.PENDING.value,
            )
            .values(decision=decision.value, decided_by_user_id=ctx.user_id)
        )
        if decision is ImportDecision.ACCEPT_MATCH:
            statement = statement.where(ImportRow.matched_product_id.is_not(None))
        return rows_affected(self._session.execute(statement))

    # --- apply ----------------------------------------------------------------

    def apply(self, ctx: AuthContext, job_id: uuid.UUID) -> JobView:
        """Apply the accepted rows in **one transaction** (FR-IMP-008).

        The caller's transaction is the unit of work; this method does not commit. If
        anything raises, the whole import is undone — including the purchase and its
        stock movements — and the job stays reviewable.
        """
        ctx.require(Permission.IMPORTS_MANAGE)

        job = self._session.execute(
            select(ImportJob)
            .where(ImportJob.id == job_id, ImportJob.business_id == ctx.business_id)
            .with_for_update()
        ).scalar_one_or_none()
        if job is None:
            raise NotFound("That import does not exist.")
        if str(job.status) == ImportStatus.APPLIED.value:
            raise ImportAlreadyApplied(
                details={
                    "applied_at": job.applied_at.isoformat() if job.applied_at else None,
                    "rows_applied": job.rows_applied,
                }
            )
        if ImportStatus(str(job.status)) not in _APPLIABLE:
            raise ValidationFailed({"status": f"An import that is {job.status} cannot be applied."})

        # FR-IMP-007, belt and braces with `ck_import_rows__decision_required`.
        pending = (
            self._session.execute(
                select(text("count(*)"))
                .select_from(ImportRow)
                .where(
                    ImportRow.import_job_id == job_id,
                    ImportRow.decision == ImportDecision.PENDING.value,
                    ImportRow.match_status.in_([s.value for s in DECISION_REQUIRED_MATCH_STATUSES]),
                )
            ).scalar()
            or 0
        )
        if pending:
            raise ImportRowsPendingDecision(details={"rows_pending": int(pending)})

        # The status guard is what makes two concurrent applies safe: exactly one
        # `UPDATE` matches, and the loser is told the job is already moving.
        claimed = rows_affected(
            self._session.execute(
                update(ImportJob)
                .where(
                    ImportJob.id == job_id,
                    ImportJob.status.in_([s.value for s in _APPLIABLE]),
                )
                .values(status=ImportStatus.APPLYING.value)
            )
        )
        if not claimed:  # pragma: no cover - the FOR UPDATE above serialises this
            raise ValidationFailed({"status": "This import is already being applied."})

        rows = list(
            self._session.execute(
                select(ImportRow)
                .where(ImportRow.import_job_id == job_id)
                .order_by(ImportRow.row_index)
            )
            .scalars()
            .all()
        )

        intent = ImportIntent(str(job.intent))
        catalog = CatalogService(self._session)
        applied = skipped = 0
        receipt_lines: list[PurchaseLine] = []

        for row in rows:
            outcome, error = self._apply_row(ctx, job, row, intent=intent, catalog=catalog)
            if outcome is ImportApplyResult.APPLIED:
                applied += 1
                if intent is ImportIntent.PURCHASE_RECEIPT and row.matched_product_id:
                    quantity = row.parsed_quantity
                    if quantity is not None and quantity > 0:
                        receipt_lines.append(
                            PurchaseLine(
                                product_id=row.matched_product_id,
                                quantity=quantity,
                                unit_cost=row.parsed_unit_cost or Decimal("0"),
                            )
                        )
            else:
                skipped += 1
            self._session.execute(
                update(ImportRow)
                .where(ImportRow.id == row.id)
                .values(apply_result=outcome.value, apply_error=error)
            )

        purchase_id: uuid.UUID | None = None
        if receipt_lines:
            # Stock moves **only** through PurchaseService.confirm (FR-PUR-003). The
            # import does not get its own ledger write.
            purchases = PurchaseService(self._session, self._clock)
            assert job.supplier_id is not None  # ck_import_jobs__supplier guarantees it
            purchase_id = purchases.create(
                ctx,
                supplier_id=job.supplier_id,
                lines=receipt_lines,
                notes=f"Imported from {job.original_filename}",
            )
            purchases.confirm(ctx, purchase_id)

        now = self._clock.now()
        self._session.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(
                status=ImportStatus.APPLIED.value,
                rows_applied=applied,
                rows_skipped=skipped,
                rows_failed=0,
                created_purchase_id=purchase_id,
                applied_by_user_id=ctx.user_id,
                applied_at=now,
            )
        )
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.IMPORT_APPLIED,
            entity_type="import_job",
            entity_id=job_id,
            after={
                "rows_applied": applied,
                "rows_skipped": skipped,
                "intent": intent.value,
                "created_purchase_id": str(purchase_id) if purchase_id else None,
            },
        )
        self._session.flush()
        return self.get(ctx, job_id)

    def _apply_row(
        self,
        ctx: AuthContext,
        job: ImportJob,
        row: ImportRow,
        *,
        intent: ImportIntent,
        catalog: CatalogService,
    ) -> tuple[ImportApplyResult, str | None]:
        """Apply one row. Returns its recorded outcome and reason (FR-IMP-009).

        A raise here aborts the whole import, by design (FR-IMP-008). The outcomes
        returned are the *expected* ones — skipped because the user said so, skipped
        because the row was unreadable — not error handling.
        """
        decision = ImportDecision(str(row.decision))
        status = ImportMatchStatus(str(row.match_status))

        if decision is ImportDecision.SKIP:
            return ImportApplyResult.SKIPPED, "Skipped by the person reviewing the import."
        if status is ImportMatchStatus.INVALID:
            return ImportApplyResult.SKIPPED, row.apply_error or "This row could not be read."
        if status is ImportMatchStatus.DUPLICATE_IN_FILE and decision is ImportDecision.PENDING:
            return ImportApplyResult.SKIPPED, row.apply_error

        if decision is ImportDecision.CREATE_NEW:
            product_id = self._create_product(ctx, job, row, catalog=catalog, intent=intent)
            self._session.execute(
                update(ImportRow)
                .where(ImportRow.id == row.id)
                .values(matched_product_id=product_id)
            )
            row.matched_product_id = product_id
            return ImportApplyResult.APPLIED, "Created as a new product."

        if row.matched_product_id is None:
            # `new_product` left pending is impossible (it is decision-required and the
            # pending check ran), so this is a `fuzzy_candidate` with no match, which the
            # matcher never produces. Recorded rather than raised: it is a skip, not a
            # corruption.
            return ImportApplyResult.SKIPPED, "No product to apply this row to."

        if intent is ImportIntent.PRICE_LIST:
            updated = self._update_prices(ctx, row, catalog=catalog)
            if not updated:
                return ImportApplyResult.SKIPPED, "No price in this row to apply."
            return ImportApplyResult.APPLIED, updated

        # purchase_receipt: the line is collected by the caller and received through
        # PurchaseService. The cost is still worth recording on the product.
        if row.parsed_unit_cost is not None:
            catalog.update_product(ctx, row.matched_product_id, cost_price=row.parsed_unit_cost)
        if row.parsed_quantity is None or row.parsed_quantity <= 0:
            return ImportApplyResult.SKIPPED, "No quantity in this row to receive."
        return ImportApplyResult.APPLIED, None

    def _update_prices(
        self, ctx: AuthContext, row: ImportRow, *, catalog: CatalogService
    ) -> str | None:
        """Price-list application. **Never touches stock** (FR-IMP-002)."""
        changes: dict[str, Any] = {}
        if row.parsed_selling_price is not None:
            changes["default_selling_price"] = row.parsed_selling_price
        if row.parsed_unit_cost is not None:
            changes["cost_price"] = row.parsed_unit_cost
        if not changes:
            return None
        assert row.matched_product_id is not None
        catalog.update_product(ctx, row.matched_product_id, **changes)
        return ", ".join(f"{k} set" for k in sorted(changes))

    def _create_product(
        self,
        ctx: AuthContext,
        job: ImportJob,
        row: ImportRow,
        *,
        catalog: CatalogService,
        intent: ImportIntent,
    ) -> uuid.UUID:
        """Create a product from a row the user explicitly chose to create.

        Only reachable through `decision = create_new`, which only a human can set
        (FR-IMP-007). The selling price falls back to the cost for a supplier bill —
        receiving stock at a known cost with no retail price set is normal, and a zero
        price would be worse than a placeholder equal to cost, which is visibly wrong
        and gets corrected.
        """
        assert row.parsed_name is not None  # validated when the decision was recorded
        price = row.parsed_selling_price
        if price is None:
            price = row.parsed_unit_cost
        assert price is not None, "decide() refuses create_new without a price"

        units = catalog.list_units(ctx)
        unit_code = next(
            (u.code for u in units if row.parsed_name and u.code.lower() == "pcs"),
            units[0].code if units else "pcs",
        )
        product = catalog.create_product(
            ctx,
            name=row.parsed_name,
            unit_code=unit_code,
            default_selling_price=price,
            sku=row.parsed_sku,
            cost_price=row.parsed_unit_cost,
        )
        _ = intent, job  # retained for signature symmetry with _apply_row
        return product.id

    # --- cancel ---------------------------------------------------------------

    def cancel(self, ctx: AuthContext, job_id: uuid.UUID) -> JobView:
        """Cancel a job that has not been applied (FR-IMP-005)."""
        ctx.require(Permission.IMPORTS_MANAGE)
        job = self._session.execute(
            select(ImportJob)
            .where(ImportJob.id == job_id, ImportJob.business_id == ctx.business_id)
            .with_for_update()
        ).scalar_one_or_none()
        if job is None:
            raise NotFound("That import does not exist.")
        if str(job.status) == ImportStatus.APPLIED.value:
            raise ImportAlreadyApplied()
        if ImportStatus(str(job.status)) not in _CANCELLABLE:
            raise ValidationFailed(
                {"status": f"An import that is {job.status} cannot be cancelled."}
            )
        self._session.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(status=ImportStatus.CANCELLED.value)
        )
        self._session.flush()
        return self.get(ctx, job_id)
