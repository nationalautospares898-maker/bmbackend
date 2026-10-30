"""Returns and credit notes (M24).

A return credits the **snapshot price** — what the customer actually paid — not today's
price (AC-12). Using the current price would mean a price rise turns every old return
into a windfall for the customer and a loss for the shop.

`ck_invoice_items__returned` is what makes NFR-DATA-008 enforceable: cumulative returns
cannot exceed the quantity sold. The row is locked before the check, so two concurrent
returns of the same line cannot jointly exceed it — and the constraint is the backstop if
they somehow do.

`condition: damaged` with `restock: false` produces a **scrap** movement, not sellable
stock. Putting a damaged part back on the shelf is the failure this distinction prevents.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.credit.service import AllocationTarget, CreditService
from bm.domain.inventory.service import InventoryService, MovementRequest
from bm.domain.settings.defaults import SettingKey
from bm.domain.settings.service import SettingsService
from bm.domain.shared.clock import Clock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    InvoiceCancelled,
    NotFound,
    ReturnQuantityExceeded,
    ReturnWindowExpired,
    ValidationFailed,
)
from bm.domain.shared.money import DEFAULT_POLICY, ZERO, assert_scale_within, quantize_amount
from bm.infra.db.enums import CreditNoteSource, MovementReason, ReturnCondition
from bm.infra.db.models.credit import SalesReturn, SalesReturnItem
from bm.infra.db.models.sales import Invoice

#: Returns use the stored amount scale; the credited figure is never rounded, only
#: represented at the scale `NUMERIC(14,2)` holds.
_POLICY = DEFAULT_POLICY


@dataclass(frozen=True, slots=True)
class ReturnLine:
    invoice_item_id: uuid.UUID
    quantity: Decimal
    condition: str = str(ReturnCondition.RESALABLE)


@dataclass(frozen=True, slots=True)
class ReturnResult:
    return_id: uuid.UUID
    credit_note_id: uuid.UUID | None
    total_amount: Decimal
    movements: int


class ReturnService:
    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock
        self._settings = SettingsService(session)
        self._inventory = InventoryService(session)
        self._credit = CreditService(session, clock)

    def create(
        self,
        ctx: AuthContext,
        *,
        invoice_id: uuid.UUID,
        lines: list[ReturnLine],
        reason: str,
        restock: bool = True,
        credit_handling: str = "allocate_to_invoice",
    ) -> ReturnResult:
        """`credit_handling` is **explicit, never inferred** (FR-RET-003)."""
        ctx.require(Permission.RETURNS_CREATE)

        if not lines:
            raise ValidationFailed({"items": "A return needs at least one line."})
        if len(reason.strip()) < 3:
            raise ValidationFailed({"reason": "Say briefly why the goods came back."})
        if credit_handling not in {"allocate_to_invoice", "refund_cash", "hold_on_account"}:
            raise ValidationFailed(
                {"credit_handling": "Choose allocate_to_invoice, refund_cash or hold_on_account."}
            )

        invoice = self._session.execute(
            select(Invoice).where(Invoice.id == invoice_id, Invoice.business_id == ctx.business_id)
        ).scalar_one_or_none()
        if invoice is None:
            raise NotFound("That bill does not exist.")
        if str(invoice.status) == "cancelled":
            raise InvoiceCancelled("That bill was cancelled; there is nothing to return.")

        window_days = int(
            self._settings.get(ctx.business_id, ctx.branch_id, SettingKey.RETURNS_WINDOW_DAYS)
        )
        now = self._clock.now()
        if now - invoice.issued_at > timedelta(days=window_days):
            raise ReturnWindowExpired(details={"window_days": window_days})

        return_id = uuid.uuid4()
        total_amount = quantize_amount(ZERO, _POLICY)
        movements: list[MovementRequest] = []
        prepared: list[dict[str, object]] = []

        for index, line in enumerate(lines, start=1):
            quantity = assert_scale_within(
                line.quantity, max_places=3, field_name=f"items.{index}.quantity"
            )
            if quantity <= ZERO:
                raise ValidationFailed({f"items.{index}.quantity": "Must be greater than zero."})

            # Locked before the cumulative check, so two concurrent returns of the same
            # line serialise instead of both seeing the same remaining quantity.
            invoice_line = self._session.execute(
                text(
                    "SELECT id, product_id, quantity, returned_quantity, unit_price,"
                    "       taxable_amount"
                    "  FROM invoice_items"
                    " WHERE id = :id AND invoice_id = :invoice_id"
                    " FOR UPDATE"
                ),
                {"id": line.invoice_item_id, "invoice_id": invoice_id},
            ).first()
            if invoice_line is None:
                raise ValidationFailed(
                    {f"items.{index}.invoice_item_id": "That line is not on this bill."}
                )

            remaining = invoice_line.quantity - invoice_line.returned_quantity
            if quantity > remaining:
                raise ReturnQuantityExceeded(
                    details={
                        "invoice_item_id": str(invoice_line.id),
                        "sold": format(invoice_line.quantity, "f"),
                        "already_returned": format(invoice_line.returned_quantity, "f"),
                        "remaining": format(remaining, "f"),
                    }
                )

            # The **snapshot** price, not today's (AC-12).
            unit_price = invoice_line.unit_price
            # Quantised to the stored scale so the credited figure matches what the
            # column holds and what every other amount in the API looks like.
            line_amount = quantize_amount(quantity * unit_price, _POLICY)
            total_amount += line_amount

            try:
                condition = ReturnCondition(line.condition)
            except ValueError as exc:
                raise ValidationFailed(
                    {f"items.{index}.condition": "Choose resalable or damaged."}
                ) from exc

            # Damaged goods, or an explicit no-restock, become scrap — never sellable
            # stock.
            if condition is ReturnCondition.DAMAGED or not restock:
                reason_code = MovementReason.SCRAP
            else:
                reason_code = MovementReason.CUSTOMER_RETURN

            movements.append(
                MovementRequest(
                    product_id=invoice_line.product_id,
                    quantity=quantity,
                    reason=reason_code,
                    source_type="sales_return",
                    source_id=return_id,
                )
            )
            prepared.append(
                {
                    "invoice_item_id": invoice_line.id,
                    "product_id": invoice_line.product_id,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "line_amount": line_amount,
                    "condition": str(condition),
                }
            )

        sequence = self._session.execute(
            text("SELECT count(*) + 1 FROM sales_returns WHERE business_id = :business_id"),
            {"business_id": ctx.business_id},
        ).scalar_one()

        self._session.execute(
            insert(SalesReturn).values(
                id=return_id,
                business_id=ctx.business_id,
                branch_id=invoice.branch_id,
                invoice_id=invoice_id,
                return_number=f"RET/{sequence:05d}",
                returned_at=now,
                reason=reason.strip(),
                restock=restock,
                total_amount=total_amount,
                created_by_user_id=ctx.user_id,
                device_id=ctx.device_id,
            )
        )
        for item in prepared:
            self._session.execute(
                insert(SalesReturnItem).values(
                    id=uuid.uuid4(),
                    sales_return_id=return_id,
                    business_id=ctx.business_id,
                    invoice_item_id=item["invoice_item_id"],
                    product_id=item["product_id"],
                    quantity=item["quantity"],
                    unit_price_snapshot=item["unit_price"],
                    line_amount=item["line_amount"],
                    condition=item["condition"],
                )
            )
            # `returned_quantity` is the only mutable column on `invoice_items`, and the
            # database CHECK is the backstop if this arithmetic is ever wrong.
            self._session.execute(
                text(
                    "UPDATE invoice_items"
                    "   SET returned_quantity = returned_quantity + :quantity"
                    " WHERE id = :id"
                ),
                {"quantity": item["quantity"], "id": item["invoice_item_id"]},
            )

        created = self._inventory.apply_movements(
            ctx, branch_id=invoice.branch_id, movements=movements
        )

        credit_note_id: uuid.UUID | None = None
        if credit_handling != "refund_cash":
            credit_note_id = self._credit.issue_credit_note(
                ctx,
                customer_id=invoice.customer_id,
                amount=total_amount,
                reason=f"Return: {reason.strip()}",
                source_type=CreditNoteSource.SALES_RETURN,
                source_id=return_id,
                original_invoice_id=invoice_id,
            )
            self._session.execute(
                text("UPDATE sales_returns SET credit_note_id = :note WHERE id = :id"),
                {"note": credit_note_id, "id": return_id},
            )
            if credit_handling == "allocate_to_invoice":
                outstanding = invoice.total - invoice.amount_paid
                allocatable = min(total_amount, outstanding)
                if allocatable > ZERO:
                    self._credit.allocate_credit_note(
                        ctx,
                        credit_note_id,
                        [AllocationTarget(invoice_id=invoice_id, amount=allocatable)],
                    )

        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.RETURN_CREATED,
            entity_type="sales_return",
            entity_id=return_id,
            reason=reason.strip(),
            after={
                "invoice_id": str(invoice_id),
                "total": format(total_amount, "f"),
                "restock": restock,
                "credit_handling": credit_handling,
                "movements": len(created),
            },
        )

        return ReturnResult(
            return_id=return_id,
            credit_note_id=credit_note_id,
            total_amount=total_amount,
            movements=len(created),
        )

    def list_returns(
        self, ctx: AuthContext, *, invoice_id: uuid.UUID | None = None, limit: int = 100
    ) -> list[dict[str, object]]:
        if not (ctx.has(Permission.RETURNS_CREATE) or ctx.has(Permission.REPORTS_SALES_READ)):
            ctx.require(Permission.RETURNS_CREATE)

        sql = """
            SELECT r.id, r.return_number, r.returned_at, r.reason, r.restock,
                   r.total_amount, r.invoice_id, r.credit_note_id
              FROM sales_returns r
             WHERE r.business_id = :business_id
        """
        params: dict[str, object] = {"business_id": ctx.business_id, "limit": min(limit, 200)}
        if invoice_id is not None:
            sql += " AND r.invoice_id = :invoice_id"
            params["invoice_id"] = invoice_id
        sql += " ORDER BY r.returned_at DESC LIMIT :limit"

        return [
            {
                "id": str(r.id),
                # Internal reference, shown on a detail view only.
                "internal_reference": r.return_number,
                "returned_at": r.returned_at.isoformat(),
                "reason": r.reason,
                "restock": r.restock,
                "total_amount": format(r.total_amount, "f"),
                "invoice_id": str(r.invoice_id),
                "credit_note_id": str(r.credit_note_id) if r.credit_note_id else None,
            }
            for r in self._session.execute(text(sql), params).all()
        ]
