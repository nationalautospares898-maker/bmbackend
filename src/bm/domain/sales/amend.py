"""Editing a finalised invoice, in place (owner Decision A, 2026-11-07).

## What changed, and what did not

OD-10 used to say "there is no edit path, ever", and `0011` enforced it by revoking
`UPDATE` on `invoices`. The owner reversed that decision on 2026-11-07. What replaces it
is **not** "invoices are mutable now" — it is:

> Every state an invoice has ever been in is permanently recorded.

So an edit here keeps the invoice's identity, rewrites its current figures, and writes
the complete before-state into `invoice_revisions` / `invoice_revision_items` first.
Those tables are `SELECT, INSERT` only at the database level (migration 0024), so the
history cannot be rewritten by this code or any other.

Four things an edit still cannot do, and each is enforced rather than merely intended:

1. **Change the invoice's identity.** `invoice_number`, `sequence_number`,
   `invoice_series_id` and `issued_at` are absent from the column-level `UPDATE` grant,
   so the database refuses even if this module tried.
2. **Touch a payment.** Nothing here writes `payments` or `payment_allocations`.
   `amount_paid` is re-derived *from* the allocations, never set independently.
3. **Rewrite the stock ledger.** A quantity change writes a **further** movement;
   `stock_movements` keeps its blanket revoke from `0007`.
4. **Reduce a bill below what has been paid.** Blocked outright — see
   `EditBelowPaidTotal` and §"The paid-total rule" below.

## The order of operations, and why it is this order

```
lock the invoice row (FOR UPDATE)
  → check it is editable, and that the caller's version matches
  → snapshot the before-state into the revision tables
  → recompute the money from the new lines, server-side
  → refuse if the new total is below amount_paid
  → lock the affected stock balances (ascending product_id — ADR-008)
  → check stock for every net increase
  → write the stock deltas as new movements
  → rewrite invoice_items to the new line set
  → write the new totals and bump version
  → re-derive payment_status from the untouched amount_paid
  → audit
```

The version check happens **under the row lock**, not before it. Checking first and
locking after is the classic read-modify-write race: two counters both read version 3,
both pass, and the second silently overwrites the first.

The paid-total check happens **after** the money is recomputed and **before** any stock
moves, so a refused edit leaves nothing behind.

## Stock reconciliation

For each product, the edit computes `new_total_quantity - old_total_quantity` and writes
one movement for the difference:

* a **net increase** consumes stock — `MovementReason.SALE`, because more goods left the
  shop on this bill;
* a **net decrease** returns stock — `MovementReason.SALE_CANCELLATION`, the existing
  reason for goods coming back off an invoice.

Those two reasons are reused deliberately rather than new ones being added. The physical
truth is identical to a sale and to a cancellation reversal, and — importantly — the
movement report already classifies `kind=sold` as `reason = 'sale'` and `kind=added` by
positive delta, so reusing them keeps every existing report correct with no change. The
*reason for the edit* is not lost: it is on the revision row, in the audit log, and every
movement carries `source_id` pointing at the invoice.

A product dropped from the bill is a net decrease of its whole quantity; a product added
is a net increase. Changing product A to product B is both, which is why the diff is
computed per product over the whole line set rather than per line.

## The paid-total rule

If the recomputed total is **below** `amount_paid`, the edit is refused with
`EDIT_BELOW_PAID_TOTAL`. The owner's instruction was explicit: do not create a negative
balance, do not silently refund, do not silently convert the difference to customer
credit, and do not alter payment history. There is no refund entity in this schema and
inventing one was expressly excluded, so the only honest answer is to refuse and point
at the existing return / credit-note workflow (ADR-015, Decision C).

Equal to `amount_paid` is allowed — that is a bill edited down to exactly what was
paid, which settles it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.inventory.service import InventoryService, MovementRequest
from bm.domain.shared.clock import Clock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    EditBelowPaidTotal,
    EmptySale,
    InsufficientStock,
    InvoiceNotEditable,
    InvoiceVersionConflict,
    NotFound,
    OversellNotPermitted,
    OversellReasonRequired,
    ValidationFailed,
)
from bm.domain.shared.money import (
    ZERO,
    InvoiceDiscount,
    InvoiceTotals,
    LineInput,
    LineTotals,
    MoneyPolicy,
    assert_not_negative,
    assert_scale_within,
    compute_invoice_totals,
    compute_line,
    parse_decimal,
)
from bm.infra.db.enums import MovementReason, PaymentStatus


@dataclass(frozen=True, slots=True)
class AmendLine:
    """One line of the **new** invoice, as requested."""

    product_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class AmendHeader:
    """Header fields an edit may change. Absent fields are left as they are."""

    customer_name: str | None = None
    customer_business_name: str | None = None
    vehicle_number: str | None = None
    mechanic_name: str | None = None
    phone: str | None = None
    notes: str | None = None
    due_date: date | None = None
    #: Sentinel-free: the API layer decides which keys were actually supplied and passes
    #: only those, so `None` here means "not supplied" rather than "clear it".
    supplied: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AmendCommand:
    invoice_id: uuid.UUID
    #: The version the editor read. A mismatch under the lock is a stale edit.
    expected_version: int
    reason: str
    lines: list[AmendLine]
    header: AmendHeader = field(default_factory=AmendHeader)
    invoice_discount: InvoiceDiscount | None = None
    oversell_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AmendResult:
    invoice_id: uuid.UUID
    revision_number: int
    version: int
    total: Decimal
    amount_paid: Decimal
    outstanding: Decimal
    payment_status: str


#: The minimum a reason has to say. Mirrors the cancellation reason floor and the
#: database `CHECK`, so a short reason is refused with a field error rather than an
#: integrity error.
_MIN_REASON = 5


class InvoiceAmendService:
    """In-place editing of a finalised invoice."""

    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock
        self._inventory = InventoryService(session)

    # --- reads ----------------------------------------------------------------

    def revisions(self, ctx: AuthContext, invoice_id: uuid.UUID) -> list[dict[str, object]]:
        """The edit history, newest first. Readable by anyone who may read a bill."""
        ctx.require(Permission.REPORTS_SALES_READ)
        self._load_header(ctx, invoice_id)  # 404s a cross-tenant id before any read

        rows = self._session.execute(
            text(
                "SELECT r.id, r.revision_number, r.reason, r.created_at,"
                "       r.total, r.amount_paid, r.payment_status, r.status,"
                "       r.customer_name_snapshot, r.mechanic_name_snapshot,"
                "       r.vehicle_number_snapshot, r.notes, u.full_name AS edited_by"
                "  FROM invoice_revisions r"
                "  LEFT JOIN users u ON u.id = r.edited_by_user_id"
                " WHERE r.invoice_id = :id AND r.business_id = :business_id"
                " ORDER BY r.revision_number DESC"
            ),
            {"id": invoice_id, "business_id": ctx.business_id},
        ).all()

        history: list[dict[str, object]] = []
        for row in rows:
            items = self._session.execute(
                text(
                    "SELECT line_no, product_name_snapshot, sku_snapshot,"
                    "       unit_code_snapshot, quantity, unit_price, line_total"
                    "  FROM invoice_revision_items WHERE revision_id = :rid"
                    " ORDER BY line_no"
                ),
                {"rid": row.id},
            ).all()
            history.append(
                {
                    "revision_number": row.revision_number,
                    "reason": row.reason,
                    "created_at": row.created_at.isoformat(),
                    "edited_by": row.edited_by,
                    "total": format(row.total, "f"),
                    "amount_paid": format(row.amount_paid, "f"),
                    "payment_status": row.payment_status,
                    "status": row.status,
                    "customer_name": row.customer_name_snapshot,
                    "mechanic_name": row.mechanic_name_snapshot,
                    "vehicle_number": row.vehicle_number_snapshot,
                    "notes": row.notes,
                    "items": [
                        {
                            "line_no": item.line_no,
                            "product_name": item.product_name_snapshot,
                            "sku": item.sku_snapshot,
                            "unit_code": item.unit_code_snapshot,
                            "quantity": format(item.quantity, "f"),
                            "unit_price": format(item.unit_price, "f"),
                            "line_total": format(item.line_total, "f"),
                        }
                        for item in items
                    ],
                }
            )
        return history

    # --- the edit -------------------------------------------------------------

    def amend(self, ctx: AuthContext, cmd: AmendCommand, *, policy: MoneyPolicy) -> AmendResult:
        """Apply an edit. One transaction; the caller commits."""
        ctx.require(Permission.BILLING_SALE_EDIT)

        reason = cmd.reason.strip()
        if len(reason) < _MIN_REASON:
            raise ValidationFailed(
                {"reason": f"Say why this bill is being changed ({_MIN_REASON}+ characters)."}
            )
        if not cmd.lines:
            raise EmptySale()

        # 1. Lock the invoice. Everything after this is serialised against another edit,
        #    a cancellation and a payment on the same bill.
        invoice = self._lock_invoice(ctx, cmd.invoice_id)

        if str(invoice.status) == "cancelled":
            raise InvoiceNotEditable()

        # 2. The version check, **under the lock**. Before it would be a race.
        if invoice.version != cmd.expected_version:
            raise InvoiceVersionConflict(
                details={"expected": cmd.expected_version, "current": invoice.version}
            )

        old_items = self._load_items(cmd.invoice_id)

        # 3. The before-state, written before anything changes.
        revision_number = self._snapshot(ctx, invoice, old_items, reason=reason)

        # 4. The money, recomputed server-side from the requested lines.
        resolved = self._resolve_lines(ctx, cmd.lines, policy=policy)
        totals = compute_invoice_totals(
            [line.money_input for line in resolved],
            invoice_discount=cmd.invoice_discount,
            policy=policy,
        )

        # 5. The paid-total rule, before any stock moves — so a refusal leaves nothing
        #    behind. Equal is permitted; below is not.
        if totals.total < invoice.amount_paid:
            raise EditBelowPaidTotal(
                details={
                    "new_total": format(totals.total, "f"),
                    "amount_paid": format(invoice.amount_paid, "f"),
                }
            )

        # 6. Stock. Per **product**, over the whole line set, because changing product A
        #    to product B is a decrease of one and an increase of the other.
        self._reconcile_stock(
            ctx,
            invoice=invoice,
            old_items=old_items,
            resolved=resolved,
            oversell_reason=cmd.oversell_reason,
        )

        # 7. The new line set, and the new totals.
        self._rewrite_items(ctx, invoice, resolved)
        new_version = invoice.version + 1
        self._write_header(ctx, invoice, cmd.header, totals=totals, version=new_version)

        # 8. `payment_status` re-derived from the **untouched** amount_paid. No payment
        #    row is read, written or allocated by this path.
        status = self._derive_status(totals.total, invoice.amount_paid)
        self._session.execute(
            text(
                "UPDATE invoices SET payment_status = CAST(:status AS payment_status),"
                "   updated_at = now() WHERE id = :id"
            ),
            {"status": str(status), "id": cmd.invoice_id},
        )

        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.INVOICE_AMENDED,
            entity_type="invoice",
            entity_id=cmd.invoice_id,
            reason=reason,
            before={
                "total": format(invoice.total, "f"),
                "version": invoice.version,
                "line_count": len(old_items),
            },
            after={
                "total": format(totals.total, "f"),
                "version": new_version,
                "line_count": len(resolved),
                "revision_number": revision_number,
            },
        )

        return AmendResult(
            invoice_id=cmd.invoice_id,
            revision_number=revision_number,
            version=new_version,
            total=totals.total,
            amount_paid=invoice.amount_paid,
            outstanding=totals.total - invoice.amount_paid,
            payment_status=str(status),
        )

    # --- steps ----------------------------------------------------------------

    def _lock_invoice(self, ctx: AuthContext, invoice_id: uuid.UUID) -> _InvoiceRow:
        row = self._session.execute(
            text(
                "SELECT id, business_id, branch_id, status, version, total, amount_paid,"
                "       payment_status, customer_id, customer_name_snapshot,"
                "       customer_business_name_snapshot, vehicle_number_snapshot,"
                "       mechanic_name_snapshot, phone_snapshot, notes, due_date,"
                "       subtotal, discount_total, tax_total, rounding_adjustment"
                "  FROM invoices WHERE id = :id AND business_id = :business_id"
                " FOR UPDATE"
            ),
            {"id": invoice_id, "business_id": ctx.business_id},
        ).first()
        if row is None:
            # A cross-tenant id is indistinguishable from an absent one.
            raise NotFound("That bill does not exist.")
        return _InvoiceRow.of(row)

    def _load_header(self, ctx: AuthContext, invoice_id: uuid.UUID) -> None:
        found = self._session.execute(
            text("SELECT 1 FROM invoices WHERE id = :id AND business_id = :business_id"),
            {"id": invoice_id, "business_id": ctx.business_id},
        ).first()
        if found is None:
            raise NotFound("That bill does not exist.")

    def _load_items(self, invoice_id: uuid.UUID) -> list[_ItemRow]:
        rows = self._session.execute(
            text(
                "SELECT id, line_no, product_id, product_name_snapshot, sku_snapshot,"
                "       unit_code_snapshot, quantity, unit_price, discount_percent,"
                "       discount_amount, tax_rate_snapshot, tax_amount, line_total,"
                "       returned_quantity"
                "  FROM invoice_items WHERE invoice_id = :id ORDER BY line_no"
            ),
            {"id": invoice_id},
        ).all()
        return [_ItemRow.of(row) for row in rows]

    def _snapshot(
        self,
        ctx: AuthContext,
        invoice: _InvoiceRow,
        items: list[_ItemRow],
        *,
        reason: str,
    ) -> int:
        """Write the complete before-state. Append-only; the next number in sequence."""
        next_number = self._session.execute(
            text(
                "SELECT coalesce(max(revision_number), 0) + 1 FROM invoice_revisions"
                " WHERE invoice_id = :id"
            ),
            {"id": invoice.id},
        ).scalar_one()
        revision_id = uuid.uuid4()
        self._session.execute(
            text(
                "INSERT INTO invoice_revisions"
                " (id, business_id, invoice_id, revision_number, reason, edited_by_user_id,"
                "  device_id, customer_id, customer_name_snapshot,"
                "  customer_business_name_snapshot, vehicle_number_snapshot,"
                "  mechanic_name_snapshot, phone_snapshot, notes, due_date, subtotal,"
                "  discount_total, tax_total, rounding_adjustment, total, amount_paid,"
                "  payment_status, status)"
                " VALUES (:id, :business_id, :invoice_id, :number, :reason, :user_id,"
                "  :device_id, :customer_id, :customer_name, :business_name, :vehicle,"
                "  :mechanic, :phone, :notes, :due_date, :subtotal, :discount_total,"
                "  :tax_total, :rounding, :total, :amount_paid, :payment_status, :status)"
            ),
            {
                "id": revision_id,
                "business_id": ctx.business_id,
                "invoice_id": invoice.id,
                "number": next_number,
                "reason": reason,
                "user_id": ctx.user_id,
                "device_id": ctx.device_id,
                "customer_id": invoice.customer_id,
                "customer_name": invoice.customer_name_snapshot,
                "business_name": invoice.customer_business_name_snapshot,
                "vehicle": invoice.vehicle_number_snapshot,
                "mechanic": invoice.mechanic_name_snapshot,
                "phone": invoice.phone_snapshot,
                "notes": invoice.notes,
                "due_date": invoice.due_date,
                "subtotal": invoice.subtotal,
                "discount_total": invoice.discount_total,
                "tax_total": invoice.tax_total,
                "rounding": invoice.rounding_adjustment,
                "total": invoice.total,
                "amount_paid": invoice.amount_paid,
                "payment_status": str(invoice.payment_status),
                "status": str(invoice.status),
            },
        )
        for item in items:
            self._session.execute(
                text(
                    "INSERT INTO invoice_revision_items"
                    " (id, revision_id, business_id, invoice_item_id, line_no, product_id,"
                    "  product_name_snapshot, sku_snapshot, unit_code_snapshot, quantity,"
                    "  unit_price, discount_percent, discount_amount, tax_rate_snapshot,"
                    "  tax_amount, line_total, returned_quantity)"
                    " VALUES (:id, :revision_id, :business_id, :item_id, :line_no,"
                    "  :product_id, :name, :sku, :unit, :quantity, :unit_price,"
                    "  :discount_percent, :discount_amount, :tax_rate, :tax_amount,"
                    "  :line_total, :returned)"
                ),
                {
                    "id": uuid.uuid4(),
                    "revision_id": revision_id,
                    "business_id": ctx.business_id,
                    "item_id": item.id,
                    "line_no": item.line_no,
                    "product_id": item.product_id,
                    "name": item.product_name_snapshot,
                    "sku": item.sku_snapshot,
                    "unit": item.unit_code_snapshot,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "discount_percent": item.discount_percent,
                    "discount_amount": item.discount_amount,
                    "tax_rate": item.tax_rate_snapshot,
                    "tax_amount": item.tax_amount,
                    "line_total": item.line_total,
                    "returned": item.returned_quantity,
                },
            )
        return int(next_number)

    def _resolve_lines(
        self, ctx: AuthContext, lines: list[AmendLine], *, policy: MoneyPolicy
    ) -> list[_ResolvedLine]:
        """Attach each line to its product and extend it in exact decimal arithmetic."""
        resolved: list[_ResolvedLine] = []
        for index, line in enumerate(lines, start=1):
            product = self._session.execute(
                text(
                    "SELECT p.id, p.name, p.sku, p.hsn_code, u.code AS unit_code,"
                    "       u.allows_fraction, p.default_selling_price, p.last_cost_price"
                    "  FROM products p JOIN units u ON u.id = p.unit_id"
                    " WHERE p.id = :id AND p.business_id = :business_id"
                ),
                {"id": line.product_id, "business_id": ctx.business_id},
            ).first()
            if product is None:
                raise ValidationFailed({f"lines.{index - 1}.product_id": "Unknown product."})

            quantity = assert_not_negative(
                assert_scale_within(
                    parse_decimal(line.quantity, field_name=f"lines.{index - 1}.quantity"),
                    max_places=3,
                    field_name=f"lines.{index - 1}.quantity",
                ),
                field_name=f"lines.{index - 1}.quantity",
            )
            if quantity <= ZERO:
                raise ValidationFailed(
                    {f"lines.{index - 1}.quantity": "Must be greater than zero."}
                )
            if not product.allows_fraction and quantity != quantity.to_integral_value():
                raise ValidationFailed(
                    {f"lines.{index - 1}.quantity": "This item is sold in whole units."}
                )

            unit_price = assert_not_negative(
                assert_scale_within(
                    parse_decimal(line.unit_price, field_name=f"lines.{index - 1}.unit_price"),
                    max_places=4,
                    field_name=f"lines.{index - 1}.unit_price",
                ),
                field_name=f"lines.{index - 1}.unit_price",
            )

            money_input = LineInput(
                quantity=quantity,
                unit_price=unit_price,
                discount_percent=line.discount_percent,
                line_no=index,
            )
            resolved.append(
                _ResolvedLine(
                    line_no=index,
                    product_id=product.id,
                    product_name=product.name,
                    sku=product.sku,
                    hsn=product.hsn_code,
                    unit_code=product.unit_code,
                    quantity=quantity,
                    unit_price=unit_price,
                    discount_percent=line.discount_percent,
                    cost_price=product.last_cost_price,
                    money_input=money_input,
                    totals=compute_line(money_input, policy),
                )
            )
        return resolved

    def _reconcile_stock(
        self,
        ctx: AuthContext,
        *,
        invoice: _InvoiceRow,
        old_items: list[_ItemRow],
        resolved: list[_ResolvedLine],
        oversell_reason: str | None,
    ) -> None:
        """One movement per product, for the net change only."""
        before: dict[uuid.UUID, Decimal] = {}
        for item in old_items:
            before[item.product_id] = before.get(item.product_id, ZERO) + item.quantity
        after: dict[uuid.UUID, Decimal] = {}
        for line in resolved:
            after[line.product_id] = after.get(line.product_id, ZERO) + line.quantity

        deltas = {
            product_id: after.get(product_id, ZERO) - before.get(product_id, ZERO)
            for product_id in set(before) | set(after)
        }
        # A line whose quantity and product are unchanged contributes nothing, so no
        # movement is written for it — the ledger does not gain a row saying "0".
        increases = {p: d for p, d in deltas.items() if d > ZERO}
        decreases = {p: -d for p, d in deltas.items() if d < ZERO}
        if not increases and not decreases:
            return

        # Only the increases need stock; a decrease gives stock back.
        oversold: set[uuid.UUID] = set()
        if increases:
            check = self._inventory.lock_and_check(
                business_id=ctx.business_id,
                branch_id=invoice.branch_id,
                required=increases,
            )
            if check.shortfalls:
                if not ctx.has(Permission.INVENTORY_OVERSELL_OVERRIDE):
                    raise InsufficientStock(
                        details={
                            "lines": [
                                {
                                    "product_id": str(s.product_id),
                                    "requested": format(s.requested, "f"),
                                    "available": format(s.available, "f"),
                                }
                                for s in check.shortfalls
                            ]
                        }
                    )
                if not oversell_reason or len(oversell_reason.strip()) < 10:
                    raise OversellReasonRequired()
                oversold = {s.product_id for s in check.shortfalls}
        elif oversell_reason is not None and not oversell_reason.strip():
            raise OversellNotPermitted()

        movements: list[MovementRequest] = []
        cost_by_product = {line.product_id: line.cost_price for line in resolved}
        for product_id, quantity in sorted(increases.items()):
            movements.append(
                MovementRequest(
                    product_id=product_id,
                    quantity=quantity,
                    # More goods left the shop on this bill. `sale` keeps the movement
                    # report's "sold" figure truthful without touching the report.
                    reason=MovementReason.SALE,
                    source_type="invoice",
                    source_id=invoice.id,
                    unit_cost=cost_by_product.get(product_id),
                    oversell_override=product_id in oversold,
                    override_reason=oversell_reason if product_id in oversold else None,
                )
            )
        for product_id, quantity in sorted(decreases.items()):
            movements.append(
                MovementRequest(
                    product_id=product_id,
                    quantity=quantity,
                    # Goods came back off the invoice. The same reason a cancellation
                    # uses, and `kind=added` picks it up by positive delta regardless.
                    reason=MovementReason.SALE_CANCELLATION,
                    source_type="invoice",
                    source_id=invoice.id,
                )
            )
        self._inventory.apply_movements(ctx, branch_id=invoice.branch_id, movements=movements)

    def _rewrite_items(
        self, ctx: AuthContext, invoice: _InvoiceRow, resolved: list[_ResolvedLine]
    ) -> None:
        """Replace the current line set.

        Every removed and changed line is already in `invoice_revision_items`, which is
        what makes this safe: the rows here are the bill's *current* state, not its
        history.
        """
        self._session.execute(
            text("DELETE FROM invoice_items WHERE invoice_id = :id"), {"id": invoice.id}
        )
        for line in resolved:
            self._session.execute(
                text(
                    "INSERT INTO invoice_items"
                    " (id, invoice_id, business_id, line_no, product_id,"
                    "  product_name_snapshot, sku_snapshot, unit_code_snapshot,"
                    "  hsn_snapshot, quantity, unit_price, discount_percent,"
                    "  discount_amount, taxable_amount, tax_rate_snapshot, tax_amount,"
                    "  line_total, unit_cost_snapshot, returned_quantity, price_source,"
                    "  catalog_price_at_sale)"
                    " VALUES (:id, :invoice_id, :business_id, :line_no, :product_id,"
                    "  :name, :sku, :unit, :hsn, :quantity, :unit_price,"
                    "  :discount_percent, :discount_amount, :taxable, :tax_rate,"
                    "  :tax_amount, :line_total, :cost, 0,"
                    "  CAST(:price_source AS price_source), :catalog_price)"
                ),
                {
                    "id": uuid.uuid4(),
                    "invoice_id": invoice.id,
                    "business_id": ctx.business_id,
                    "line_no": line.line_no,
                    "product_id": line.product_id,
                    "name": line.product_name,
                    "sku": line.sku,
                    "unit": line.unit_code,
                    "hsn": line.hsn,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "discount_percent": line.discount_percent,
                    "discount_amount": line.totals.discount_total,
                    "taxable": line.totals.taxable_amount,
                    "tax_rate": ZERO,
                    "tax_amount": line.totals.tax_amount,
                    "line_total": line.totals.line_total,
                    "cost": line.cost_price,
                    "price_source": "manual_override",
                    "catalog_price": line.unit_price,
                },
            )

    def _write_header(
        self,
        ctx: AuthContext,
        invoice: _InvoiceRow,
        header: AmendHeader,
        *,
        totals: InvoiceTotals,
        version: int,
    ) -> None:
        """Write the new totals, and any header field the caller actually supplied.

        ## One fixed statement, not an assembled one

        This built its `SET` list at runtime and interpolated it into `text(...)`. The
        structural check in `tests/structural/test_module_boundaries.py` rejected that,
        correctly: it cannot tell a whitelist-derived fragment from a user-derived one,
        and a rule that has to reason about provenance is a rule that eventually lets
        something through.

        So every editable column is written unconditionally, and a per-field boolean
        decides whether the new value or the existing one lands:

            mechanic_name_snapshot = CASE WHEN :set_mechanic
                                     THEN :mechanic_name
                                     ELSE mechanic_name_snapshot END

        Nothing is interpolated, every value is a bound parameter, and "omitted means
        unchanged" stays true — which is what stops an edit that only changes a quantity
        from wiping the mechanic.

        `invoice_number`, `sequence_number`, `invoice_series_id` and `issued_at` are
        absent here by design, and would be refused by migration 0024's column-level
        grant even if this method tried.
        """
        self._session.execute(
            text(
                "UPDATE invoices SET"
                "   subtotal = :subtotal,"
                "   discount_total = :discount_total,"
                "   tax_total = :tax_total,"
                "   rounding_adjustment = :rounding,"
                "   total = :total,"
                "   version = :version,"
                "   updated_at = now(),"
                "   customer_name_snapshot = CASE WHEN :set_customer_name"
                "     THEN :customer_name ELSE customer_name_snapshot END,"
                "   customer_business_name_snapshot = CASE WHEN :set_business_name"
                "     THEN :customer_business_name ELSE customer_business_name_snapshot END,"
                "   vehicle_number_snapshot = CASE WHEN :set_vehicle"
                "     THEN :vehicle_number ELSE vehicle_number_snapshot END,"
                "   mechanic_name_snapshot = CASE WHEN :set_mechanic"
                "     THEN :mechanic_name ELSE mechanic_name_snapshot END,"
                "   phone_snapshot = CASE WHEN :set_phone"
                "     THEN :phone ELSE phone_snapshot END,"
                "   notes = CASE WHEN :set_notes THEN :notes ELSE notes END,"
                "   due_date = CASE WHEN :set_due_date THEN :due_date ELSE due_date END"
                " WHERE id = :id"
            ),
            {
                "id": invoice.id,
                "subtotal": totals.subtotal,
                "discount_total": totals.discount_total,
                "tax_total": totals.tax_total,
                "rounding": totals.rounding_adjustment,
                "total": totals.total,
                "version": version,
                "set_customer_name": "customer_name" in header.supplied,
                "customer_name": header.customer_name,
                "set_business_name": "customer_business_name" in header.supplied,
                "customer_business_name": header.customer_business_name,
                "set_vehicle": "vehicle_number" in header.supplied,
                "vehicle_number": header.vehicle_number,
                "set_mechanic": "mechanic_name" in header.supplied,
                "mechanic_name": header.mechanic_name,
                "set_phone": "phone" in header.supplied,
                "phone": header.phone,
                "set_notes": "notes" in header.supplied,
                "notes": header.notes,
                "set_due_date": "due_date" in header.supplied,
                "due_date": header.due_date,
            },
        )

    @staticmethod
    def _derive_status(total: Decimal, paid: Decimal) -> PaymentStatus:
        """Identical to finalisation's rule, so one bill cannot be described two ways."""
        if paid <= ZERO:
            return PaymentStatus.UNPAID
        if paid >= total:
            return PaymentStatus.PAID
        return PaymentStatus.PARTIALLY_PAID


# --- row wrappers -------------------------------------------------------------
#
# Thin attribute holders so the steps above read as domain code rather than as tuple
# indexing. Deliberately not ORM entities: `ARCHITECTURE.md §3` keeps mappers inside
# repositories, and these never leave this module.


@dataclass(frozen=True, slots=True)
class _InvoiceRow:
    """The locked invoice header.

    Built field by field from the `SELECT ... FOR UPDATE` rather than by looping over
    `__slots__` with `setattr`: the explicit version is longer, and it is the only one
    `mypy` can check — a renamed column becomes a build failure here instead of an
    `AttributeError` in the middle of an edit.
    """

    id: uuid.UUID
    business_id: uuid.UUID
    branch_id: uuid.UUID
    status: str
    version: int
    total: Decimal
    amount_paid: Decimal
    payment_status: str
    customer_id: uuid.UUID | None
    customer_name_snapshot: str | None
    customer_business_name_snapshot: str | None
    vehicle_number_snapshot: str | None
    mechanic_name_snapshot: str | None
    phone_snapshot: str | None
    notes: str | None
    due_date: date | None
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    rounding_adjustment: Decimal

    @classmethod
    def of(cls, row: Any) -> _InvoiceRow:
        return cls(
            id=row.id,
            business_id=row.business_id,
            branch_id=row.branch_id,
            status=str(row.status),
            version=row.version,
            total=row.total,
            amount_paid=row.amount_paid,
            payment_status=str(row.payment_status),
            customer_id=row.customer_id,
            customer_name_snapshot=row.customer_name_snapshot,
            customer_business_name_snapshot=row.customer_business_name_snapshot,
            vehicle_number_snapshot=row.vehicle_number_snapshot,
            mechanic_name_snapshot=row.mechanic_name_snapshot,
            phone_snapshot=row.phone_snapshot,
            notes=row.notes,
            due_date=row.due_date,
            subtotal=row.subtotal,
            discount_total=row.discount_total,
            tax_total=row.tax_total,
            rounding_adjustment=row.rounding_adjustment,
        )


@dataclass(frozen=True, slots=True)
class _ItemRow:
    """One of the invoice's current lines, as it stands before the edit."""

    id: uuid.UUID
    line_no: int
    product_id: uuid.UUID
    product_name_snapshot: str
    sku_snapshot: str | None
    unit_code_snapshot: str
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal
    discount_amount: Decimal
    tax_rate_snapshot: Decimal
    tax_amount: Decimal
    line_total: Decimal
    returned_quantity: Decimal

    @classmethod
    def of(cls, row: Any) -> _ItemRow:
        return cls(
            id=row.id,
            line_no=row.line_no,
            product_id=row.product_id,
            product_name_snapshot=row.product_name_snapshot,
            sku_snapshot=row.sku_snapshot,
            unit_code_snapshot=row.unit_code_snapshot,
            quantity=row.quantity,
            unit_price=row.unit_price,
            discount_percent=row.discount_percent,
            discount_amount=row.discount_amount,
            tax_rate_snapshot=row.tax_rate_snapshot,
            tax_amount=row.tax_amount,
            line_total=row.line_total,
            returned_quantity=row.returned_quantity,
        )


@dataclass(frozen=True, slots=True)
class _ResolvedLine:
    line_no: int
    product_id: uuid.UUID
    product_name: str
    sku: str | None
    hsn: str | None
    unit_code: str
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal
    cost_price: Decimal | None
    money_input: LineInput
    totals: LineTotals
