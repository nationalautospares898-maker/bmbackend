"""Infrastructure tables (migration 0015).

`idempotency_keys`, `sync_operations`, `jobs`, `outbox_events`, `notifications`,
`import_jobs`, `import_rows`.

`uq_idempotency__scope` is the serialisation point for concurrent same-key requests
(`Blueprint §5.2.2`) — it must be **UNIQUE and non-deferrable**, because the whole
duplicate-prevention design depends on one of two simultaneous inserts losing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from bm.infra.db.base import (
    NAME,
    NOTES,
    QUANTITY,
    SHORT_TEXT,
    UNIT_PRICE,
    Base,
    created_at_column,
    updated_at_column,
)
from bm.infra.db.enums import (
    IDEMPOTENCY_STATUS,
    IMPORT_APPLY_RESULT,
    IMPORT_DECISION,
    IMPORT_INTENT,
    IMPORT_MATCH_STATUS,
    IMPORT_STATUS,
    JOB_STATUS,
    NOTIFICATION_KIND,
    NOTIFICATION_SEVERITY,
    OUTBOX_STATUS,
    SYNC_OPERATION_STATUS,
)


class IdempotencyKey(Base):
    """One row per (business, endpoint, key).

    Inserted as the **first write** of the business transaction and before any inventory
    lock (`P2-D-06`), so a duplicate submit fails in microseconds holding no business
    locks instead of blocking a genuine sale of the same product for `lock_timeout`.

    Because the insert is inside the transaction, a rolled-back attempt leaves the key
    **reusable** — which is exactly what makes a client retry safe after a failure.
    """

    __tablename__ = "idempotency_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    #: Key scope is per business **and per endpoint**: the same value on `/payments` and
    #: `/sales:finalize` are different keys (`§5.2.8` rule 4).
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    #: SHA-256 of the canonicalised body (`§5.2.3`).
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(IDEMPOTENCY_STATUS, nullable=False)
    #: Written in the **same** transaction that commits the business effect, so a stored
    #: response can never describe work that did not commit (`§5.2.8` rule 3).
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("business_id", "endpoint", "key", name="uq_idempotency__scope"),
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="ck_idempotency__completed",
        ),
    )


class SyncOperation(Base):
    """Operation intake for the bounded offline scope.

    ⚠️ The registry contains exactly `draft.upsert`, `draft.delete`, `customer.upsert`
    (ADR-011). **No operation type finalises a sale, records a payment, confirms a
    purchase, creates a return or applies an import** — adding one requires revisiting
    ADR-011 **and** owner approval. A structural test asserts this (AC-OFF-8).
    """

    __tablename__ = "sync_operations"

    #: Equals the client's `op_id`, which is what makes replay free.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    client_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(SYNC_OPERATION_STATUS, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    #: Diagnostics only.
    client_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: **Authoritative** ordering time (FR-OFFLINE-007). A client clock a year out
    #: changes nothing.
    server_received_at: Mapped[datetime] = created_at_column()


class Job(Base):
    """Postgres-backed queue, claimed with `FOR UPDATE SKIP LOCKED` (ADR-004).

    Jobs are rows, so enqueueing is transactional with business data: an import job
    cannot exist for a file whose upload rolled back.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        JOB_STATUS, nullable=False, server_default=text("'queued'::job_status")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    run_after: Mapped[datetime] = created_at_column()
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(NOTES, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class OutboxEvent(Base):
    """Transactional outbox.

    Anything that must happen "because the sale committed" is a row written in the same
    transaction and dispatched after commit — which is how rule 3 of
    `ARCHITECTURE.md §5.1` (no side effects inside a write transaction) is kept without
    losing the side effect.
    """

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        OUTBOX_STATUS, nullable=False, server_default=text("'pending'::outbox_status")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(NOTIFICATION_KIND, nullable=False)
    severity: Mapped[str] = mapped_column(NOTIFICATION_SEVERITY, nullable=False)
    title: Mapped[str] = mapped_column(NAME, nullable=False)
    body: Mapped[str | None] = mapped_column(NOTES, nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()


# --- imports -----------------------------------------------------------------


class ImportJob(Base):
    """`intent: price_list` must produce **zero** stock movements, ever (FR-IMP-002).

    The original filename is metadata only — `stored_object_key` is generated, so no
    user-controlled path segment ever reaches the filesystem (`SECURITY.md §5` rule 3).
    """

    __tablename__ = "import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    #: Monotonic upload order (migration `0020`).
    #:
    #: `created_at` cannot carry this: it comes from `now()` at the OS clock's
    #: resolution, so two uploads under load tie — and `id` is `uuid4`, so a tiebreak on
    #: it is random. The list promised newest-first and could not keep it, and the
    #: `before=<created_at>` cursor could skip or repeat a row on the same tie.
    #: A sequence is monotonic per insert, which is what "newest" actually means.
    ordinal: Mapped[int] = mapped_column(BigInteger, nullable=False)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    intent: Mapped[str] = mapped_column(IMPORT_INTENT, nullable=False)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=True
    )
    original_filename: Mapped[str] = mapped_column(NAME, nullable=False)
    stored_object_key: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(IMPORT_STATUS, nullable=False)
    duplicate_of_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="RESTRICT"), nullable=True
    )
    duplicate_override_reason: Mapped[str | None] = mapped_column(SHORT_TEXT, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_applied: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_summary: Mapped[str | None] = mapped_column(NOTES, nullable=True)
    created_purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchases.id", ondelete="RESTRICT"), nullable=True
    )
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    applied_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        # FR-IMP-004 / E-9: the same bytes twice is a 409, not a second import.
        #
        # **Partial**, on `duplicate_of_job_id IS NULL` (migration `0019`). The same
        # requirement also says an override "requires an explicit flag and a reason, and
        # is audited" — so the second row has to be creatable. A total constraint would
        # reject the very row the requirement describes, which is why the first
        # implementation ended up doctoring the digest to get around it.
        Index(
            "uq_import_jobs__file",
            "business_id",
            "file_sha256",
            unique=True,
            postgresql_where=text("duplicate_of_job_id IS NULL"),
        ),
        CheckConstraint(
            "intent <> 'purchase_receipt' OR supplier_id IS NOT NULL",
            name="ck_import_jobs__supplier",
        ),
    )


class ImportRow(Base):
    __tablename__ = "import_rows"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    import_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_values: Mapped[Any] = mapped_column(JSONB, nullable=False)
    parsed_name: Mapped[str | None] = mapped_column(NAME, nullable=True)
    parsed_sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parsed_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    parsed_unit_cost: Mapped[Decimal | None] = mapped_column(UNIT_PRICE, nullable=True)
    parsed_selling_price: Mapped[Decimal | None] = mapped_column(UNIT_PRICE, nullable=True)
    match_status: Mapped[str] = mapped_column(IMPORT_MATCH_STATUS, nullable=False)
    matched_product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    candidate_product_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=True
    )
    match_confidence: Mapped[Decimal | None] = mapped_column(nullable=True)
    decision: Mapped[str] = mapped_column(
        IMPORT_DECISION, nullable=False, server_default=text("'pending'::import_decision")
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    apply_result: Mapped[str | None] = mapped_column(IMPORT_APPLY_RESULT, nullable=True)
    apply_error: Mapped[str | None] = mapped_column(NOTES, nullable=True)

    __table_args__ = (
        UniqueConstraint("import_job_id", "row_index", name="uq_import_rows__row"),
        # FR-IMP-007: a row needing a human decision cannot be applied without one.
        CheckConstraint(
            "match_status NOT IN ('ambiguous','new_product','duplicate_in_file','invalid')"
            " OR decision <> 'pending' OR apply_result IS NULL",
            name="ck_import_rows__decision_required",
        ),
    )


Index("ix_sync_operations__device_seq", SyncOperation.device_id, SyncOperation.client_seq)
Index(
    "ix_sync_operations__entity",
    SyncOperation.entity_type,
    SyncOperation.entity_id,
    SyncOperation.server_received_at.desc(),
)
Index(
    "ix_jobs__claim",
    Job.status,
    Job.run_after,
    postgresql_where=text("status = 'queued'"),
)
Index(
    "uq_jobs__dedupe",
    Job.dedupe_key,
    unique=True,
    postgresql_where=text("dedupe_key IS NOT NULL"),
)
Index(
    "ix_outbox_events__pending",
    OutboxEvent.status,
    OutboxEvent.created_at,
    postgresql_where=text("status = 'pending'"),
)
Index("ix_idempotency_keys__created", IdempotencyKey.created_at)
Index("ix_import_rows__status", ImportRow.import_job_id, ImportRow.match_status)
Index(
    "ix_notifications__unread",
    Notification.business_id,
    Notification.created_at.desc(),
    postgresql_where=text("read_at IS NULL"),
)
#: The `0017` child-table tenancy index. See the note in `catalog.py`.
Index("ix_import_rows__business", ImportRow.business_id, ImportRow.import_job_id)
#: Migration `0020`'s uniqueness guarantee for the monotonic upload ordinal: "newest
#: first" is only a guarantee if no two jobs in one business can share an ordinal.
Index("ix_import_jobs__ordinal", ImportJob.business_id, ImportJob.ordinal, unique=True)
