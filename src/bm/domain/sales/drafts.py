"""Draft bills (M8).

A draft is **not** a sale. It reserves no stock (ADR-006), holds no number, and saving
one can never finalise anything. `test_draft_edit_creates_no_movements` counts movement
rows before and after a draft edit for exactly this reason.

Concurrency is optimistic via `version` with `If-Match`, and there is **no server-side
merge** (ADR-007): a conflict returns `409 DRAFT_VERSION_CONFLICT` with
`current_state` so the user chooses. Silently merging two cashiers' edits to one bill is
how a line goes missing without anyone noticing.

The draft limit **never discards a bill** (FR-DRAFT-002). At the limit the server
refuses the new draft and says so; it does not make room.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.settings.defaults import SettingKey
from bm.domain.settings.service import SettingsService
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    DraftAlreadyCancelled,
    DraftAlreadyFinalized,
    DraftLimitReached,
    DraftOwnedByOtherCounter,
    DraftVersionConflict,
    NotFound,
    PreconditionRequired,
    ValidationFailed,
)
from bm.domain.shared.money import (
    ZERO,
    LineInput,
    assert_scale_within,
    compute_line,
)
from bm.infra.db.enums import DraftStatus, PriceSource
from bm.infra.db.models.sales import DraftBill, DraftBillItem


@dataclass(frozen=True, slots=True)
class DraftItemInput:
    id: uuid.UUID
    line_no: int
    product_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal
    catalog_price_at_add: Decimal
    price_source: str
    discount_percent: Decimal = ZERO
    discount_amount: Decimal = ZERO
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class DraftItemView:
    id: uuid.UUID
    line_no: int
    product_id: uuid.UUID
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    catalog_price_at_add: Decimal
    price_source: str
    discount_percent: Decimal
    discount_amount: Decimal
    line_total_preview: Decimal


@dataclass(frozen=True, slots=True)
class DraftView:
    id: uuid.UUID
    counter_id: uuid.UUID
    label: str | None
    status: str
    version: int
    customer_id: uuid.UUID | None
    customer_name: str | None
    customer_business_name: str | None
    vehicle_number: str | None
    mechanic_name: str | None
    phone: str | None
    notes: str | None
    is_credit_intent: bool
    items: tuple[DraftItemView, ...]
    #: **Advisory only** (FR-DRAFT-011). The server recomputes every figure at
    #: finalisation; this exists so a reopened tab shows something sensible.
    preview_total: Decimal
    invoice_id: uuid.UUID | None
    updated_at: datetime


class DraftService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._settings = SettingsService(session)

    # --- reads ----------------------------------------------------------------

    def list_open(
        self, ctx: AuthContext, *, counter_id: uuid.UUID | None = None
    ) -> list[DraftView]:
        ctx.require(Permission.BILLING_DRAFT_MANAGE)
        query = (
            select(DraftBill.id)
            .where(
                DraftBill.business_id == ctx.business_id,
                DraftBill.status == DraftStatus.OPEN,
            )
            .order_by(DraftBill.created_at)
        )
        target = counter_id or ctx.counter_id
        if target is not None:
            query = query.where(DraftBill.counter_id == target)
        return [self._view(ctx, did) for did in self._session.execute(query).scalars().all()]

    def get(self, ctx: AuthContext, draft_id: uuid.UUID) -> DraftView:
        ctx.require(Permission.BILLING_DRAFT_MANAGE)
        return self._view(ctx, draft_id)

    def _view(self, ctx: AuthContext, draft_id: uuid.UUID) -> DraftView:
        row = self._session.execute(
            select(DraftBill).where(
                DraftBill.id == draft_id, DraftBill.business_id == ctx.business_id
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("That bill does not exist.")

        items = list(
            self._session.execute(
                select(DraftBillItem)
                .where(DraftBillItem.draft_bill_id == draft_id)
                .order_by(DraftBillItem.line_no)
            )
            .scalars()
            .all()
        )
        views = tuple(
            DraftItemView(
                id=i.id,
                line_no=i.line_no,
                product_id=i.product_id,
                product_name=i.product_name_snapshot,
                quantity=i.quantity,
                unit_price=i.unit_price,
                catalog_price_at_add=i.catalog_price_at_add,
                price_source=str(i.price_source),
                discount_percent=i.discount_percent,
                discount_amount=i.discount_amount,
                line_total_preview=i.line_total_preview,
            )
            for i in items
        )
        return DraftView(
            id=row.id,
            counter_id=row.counter_id,
            label=row.label,
            status=str(row.status),
            version=row.version,
            customer_id=row.customer_id,
            customer_name=row.customer_name,
            customer_business_name=row.customer_business_name,
            vehicle_number=row.vehicle_number,
            mechanic_name=row.mechanic_name,
            phone=row.phone,
            notes=row.notes,
            is_credit_intent=row.is_credit_intent,
            items=views,
            preview_total=row.preview_total if row.preview_total is not None else ZERO,
            invoice_id=row.invoice_id,
            updated_at=row.updated_at,
        )

    def load_for_update(self, business_id: uuid.UUID, draft_id: uuid.UUID) -> DraftBill | None:
        """Lock the draft row. Used by finalisation so two submits of the same bill
        serialise behind each other rather than both proceeding."""
        return self._session.execute(
            select(DraftBill)
            .where(DraftBill.id == draft_id, DraftBill.business_id == business_id)
            .with_for_update()
        ).scalar_one_or_none()

    def items_of(self, draft_id: uuid.UUID) -> list[DraftBillItem]:
        return list(
            self._session.execute(
                select(DraftBillItem)
                .where(DraftBillItem.draft_bill_id == draft_id)
                .order_by(DraftBillItem.line_no)
            )
            .scalars()
            .all()
        )

    # --- upsert ---------------------------------------------------------------

    def upsert(
        self,
        ctx: AuthContext,
        draft_id: uuid.UUID,
        *,
        counter_id: uuid.UUID,
        items: list[DraftItemInput],
        product_names: dict[uuid.UUID, str],
        if_match_version: int | None,
        label: str | None = None,
        customer_id: uuid.UUID | None = None,
        customer_name: str | None = None,
        customer_business_name: str | None = None,
        vehicle_number: str | None = None,
        mechanic_name: str | None = None,
        phone: str | None = None,
        notes: str | None = None,
        is_credit_intent: bool = False,
        client_created_at: datetime | None = None,
    ) -> DraftView:
        """Create or update a draft on a **client-generated id** (FR-DRAFT-001).

        `If-Match` is required for an update and absent for a create. A blind write to an
        existing draft is refused with `428 PRECONDITION_REQUIRED`: the server will not
        overwrite a bill somebody else may be editing.
        """
        ctx.require(Permission.BILLING_DRAFT_MANAGE)

        existing = self._session.execute(
            select(DraftBill)
            .where(DraftBill.id == draft_id, DraftBill.business_id == ctx.business_id)
            .with_for_update()
        ).scalar_one_or_none()

        if existing is None:
            self._assert_within_limit(ctx, counter_id)
            self._insert_draft(
                ctx,
                draft_id,
                counter_id=counter_id,
                label=label,
                customer_id=customer_id,
                customer_name=customer_name,
                customer_business_name=customer_business_name,
                vehicle_number=vehicle_number,
                mechanic_name=mechanic_name,
                phone=phone,
                notes=notes,
                is_credit_intent=is_credit_intent,
                client_created_at=client_created_at,
            )
            new_version = 1
        else:
            if existing.status == DraftStatus.FINALIZED:
                raise DraftAlreadyFinalized(
                    details={"invoice_id": str(existing.invoice_id)} if existing.invoice_id else {}
                )
            if existing.status == DraftStatus.CANCELLED:
                # The client offers "recreate as a new bill" with a fresh id, so the
                # typed lines are not lost (`§4.2a`).
                raise DraftAlreadyCancelled()
            if existing.counter_id != counter_id and not ctx.has(Permission.BILLING_DRAFT_TAKEOVER):
                raise DraftOwnedByOtherCounter()
            if if_match_version is None:
                raise PreconditionRequired()
            if if_match_version != existing.version:
                raise DraftVersionConflict(
                    details={
                        "current_version": existing.version,
                        "current_state": self._state_payload(ctx, draft_id),
                    }
                )

            new_version = existing.version + 1
            values: dict[str, object] = {
                "version": new_version,
                "counter_id": counter_id,
                "label": label,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "customer_business_name": customer_business_name,
                "vehicle_number": vehicle_number,
                "mechanic_name": mechanic_name,
                "phone": phone,
                "notes": notes,
                "is_credit_intent": is_credit_intent,
                "last_edited_by_user_id": ctx.user_id,
                "device_id": ctx.device_id,
            }
            self._session.execute(
                update(DraftBill).where(DraftBill.id == draft_id).values(**values)
            )

        self._replace_items(ctx, draft_id, items, product_names)
        self._session.flush()
        return self._view(ctx, draft_id)

    def _insert_draft(
        self,
        ctx: AuthContext,
        draft_id: uuid.UUID,
        *,
        counter_id: uuid.UUID,
        **fields: object,
    ) -> None:
        self._session.execute(
            insert(DraftBill).values(
                id=draft_id,
                business_id=ctx.business_id,
                branch_id=ctx.requires_branch,
                counter_id=counter_id,
                status=DraftStatus.OPEN,
                version=1,
                created_by_user_id=ctx.user_id,
                last_edited_by_user_id=ctx.user_id,
                device_id=ctx.device_id,
                **fields,
            )
        )

    def _assert_within_limit(self, ctx: AuthContext, counter_id: uuid.UUID) -> None:
        """At the limit the server refuses; it **never** closes an existing bill
        (FR-DRAFT-002)."""
        max_open = int(
            self._settings.get(
                ctx.business_id, ctx.branch_id, SettingKey.DRAFTS_MAX_OPEN_PER_COUNTER
            )
        )
        current = int(
            self._session.execute(
                select(func.count())
                .select_from(DraftBill)
                .where(
                    DraftBill.counter_id == counter_id,
                    DraftBill.status == DraftStatus.OPEN,
                )
            ).scalar_one()
        )
        if current >= max_open:
            raise DraftLimitReached(details={"max_open_drafts": max_open, "current_open": current})

    def _replace_items(
        self,
        ctx: AuthContext,
        draft_id: uuid.UUID,
        items: list[DraftItemInput],
        product_names: dict[uuid.UUID, str],
    ) -> None:
        """Replace the line set wholesale.

        A draft is small (25 lines at the workbook's capacity) and the client sends the
        complete set, so replacing is simpler and less error-prone than diffing — and it
        cannot leave an orphaned line behind.
        """
        self._session.execute(delete(DraftBillItem).where(DraftBillItem.draft_bill_id == draft_id))

        seen_lines: set[int] = set()
        total = ZERO
        for item in items:
            if item.line_no in seen_lines:
                raise ValidationFailed(
                    {f"items.{item.line_no}": "Duplicate line number on this bill."}
                )
            seen_lines.add(item.line_no)

            quantity = assert_scale_within(
                item.quantity, max_places=3, field_name=f"items.{item.line_no}.quantity"
            )
            unit_price = assert_scale_within(
                item.unit_price, max_places=4, field_name=f"items.{item.line_no}.unit_price"
            )
            if quantity <= ZERO:
                raise ValidationFailed(
                    {f"items.{item.line_no}.quantity": "Must be greater than zero."}
                )
            if unit_price < ZERO:
                raise ValidationFailed(
                    {f"items.{item.line_no}.unit_price": "Must not be negative."}
                )

            # The preview is computed with the same engine the invoice will use, but it
            # is stored as advisory: nothing downstream trusts it.
            try:
                line_total = compute_line(
                    LineInput(
                        quantity=quantity,
                        unit_price=unit_price,
                        discount_percent=item.discount_percent,
                        discount_amount=item.discount_amount,
                        line_no=item.line_no,
                    )
                ).taxable_amount
            except Exception:  # noqa: BLE001 - see the comment below
                # A draft must be savable even when its figures could not be finalised
                # — refusing to save would lose the cashier's typing, which is the one
                # thing this system must never do. Finalisation will reject it properly.
                line_total = quantity * unit_price

            total += line_total
            self._session.execute(
                insert(DraftBillItem).values(
                    id=item.id,
                    draft_bill_id=draft_id,
                    business_id=ctx.business_id,
                    line_no=item.line_no,
                    product_id=item.product_id,
                    product_name_snapshot=product_names.get(item.product_id, ""),
                    quantity=quantity,
                    unit_price=unit_price,
                    catalog_price_at_add=item.catalog_price_at_add,
                    price_source=item.price_source or str(PriceSource.CATALOG),
                    discount_percent=item.discount_percent,
                    discount_amount=item.discount_amount,
                    line_total_preview=line_total,
                    notes=item.notes,
                )
            )

        self._session.execute(
            update(DraftBill).where(DraftBill.id == draft_id).values(preview_total=total)
        )

    def _state_payload(self, ctx: AuthContext, draft_id: uuid.UUID) -> dict[str, object]:
        """The server's current view of the draft, returned with a version conflict so
        the client can offer keep-mine / keep-theirs without a second round trip."""
        view = self._view(ctx, draft_id)
        return {
            "id": str(view.id),
            "version": view.version,
            "status": view.status,
            "preview_total": format(view.preview_total, "f"),
            "items": [
                {
                    "line_no": i.line_no,
                    "product_id": str(i.product_id),
                    "product_name": i.product_name,
                    "quantity": format(i.quantity, "f"),
                    "unit_price": format(i.unit_price, "f"),
                    "line_total_preview": format(i.line_total_preview, "f"),
                }
                for i in view.items
            ],
        }

    # --- clear ----------------------------------------------------------------

    def clear(self, ctx: AuthContext, draft_id: uuid.UUID) -> None:
        """`Clear Draft`. Marks the draft `cancelled` and keeps the row.

        It **never** touches a finalised sale (T3-09). The row is retained rather than
        deleted so a cleared bill can still be explained later, and so a late offline
        edit arriving for it gets `DRAFT_ALREADY_CANCELLED` rather than silently
        recreating it.
        """
        ctx.require(Permission.BILLING_DRAFT_MANAGE)

        row = self._session.execute(
            select(DraftBill)
            .where(DraftBill.id == draft_id, DraftBill.business_id == ctx.business_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("That bill does not exist.")
        if row.status == DraftStatus.FINALIZED:
            # The hard rule: clearing can never remove a completed sale.
            raise DraftAlreadyFinalized(
                "This bill was already completed and cannot be cleared.",
                details={"invoice_id": str(row.invoice_id)} if row.invoice_id else {},
            )
        if row.status == DraftStatus.CANCELLED:
            return  # already cleared; the caller's intent is satisfied

        self._session.execute(
            update(DraftBill)
            .where(DraftBill.id == draft_id)
            .values(status=DraftStatus.CANCELLED, version=row.version + 1)
        )
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.DRAFT_CANCELLED,
            entity_type="draft_bill",
            entity_id=draft_id,
            before={"status": str(row.status), "line_count": len(self.items_of(draft_id))},
            after={"status": str(DraftStatus.CANCELLED)},
        )

    def takeover(self, ctx: AuthContext, draft_id: uuid.UUID) -> DraftView:
        """Reassign a draft to this counter (FR-DRAFT-010). Audited, because it moves
        someone else's work."""
        ctx.require(Permission.BILLING_DRAFT_TAKEOVER)

        row = self._session.execute(
            select(DraftBill)
            .where(DraftBill.id == draft_id, DraftBill.business_id == ctx.business_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("That bill does not exist.")
        if row.status != DraftStatus.OPEN:
            raise DraftAlreadyFinalized("Only an open bill can be taken over.")
        if ctx.counter_id is None:
            raise ValidationFailed({"X-Counter-Id": "required"})

        previous_counter = row.counter_id
        self._session.execute(
            update(DraftBill)
            .where(DraftBill.id == draft_id)
            .values(
                counter_id=ctx.counter_id,
                device_id=ctx.device_id,
                last_edited_by_user_id=ctx.user_id,
                version=row.version + 1,
            )
        )
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.DRAFT_TAKEN_OVER,
            entity_type="draft_bill",
            entity_id=draft_id,
            before={"counter_id": str(previous_counter)},
            after={"counter_id": str(ctx.counter_id)},
        )
        return self._view(ctx, draft_id)

    def mark_finalized(self, draft_id: uuid.UUID, invoice_id: uuid.UUID) -> None:
        """Called inside the finalisation transaction.

        `uq_draft_bills__invoice` and `ck_draft_bills__finalized` make a second
        finalisation of the same draft impossible at the database level, so this is safe
        to call without re-checking.
        """
        self._session.execute(
            update(DraftBill)
            .where(DraftBill.id == draft_id)
            .values(status=DraftStatus.FINALIZED, invoice_id=invoice_id)
        )
