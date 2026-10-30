"""Invoice cancellation with stock reversal (M12).

Cancellation is a **correction, not a deletion** (ADR-015). The invoice row stays, keeps
its internal reference, and remains listed — it is simply `cancelled`, excluded from
revenue, and present in the `excluded` block of every report that counts money
(FR-RPT-008).

What it does, atomically:

1. Reverses the stock **exactly** — one `sale_cancellation` movement per original sale
   movement, same quantity. Not a recomputation from the invoice items, because a
   partial return may already have moved some of it back.
2. Issues a full-value credit note, so a credit customer's balance reflects it.
3. Records who, when and **why** — the reason is mandatory.

The number is **not** released. Reusing it would make the internal reference ambiguous
for audit, which is the one job it has.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.credit.service import CreditService
from bm.domain.inventory.service import InventoryService, MovementRequest
from bm.domain.settings.defaults import SettingKey
from bm.domain.settings.service import SettingsService
from bm.domain.shared.clock import Clock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    CancelWindowExpired,
    InvoiceAlreadyCancelled,
    NotFound,
    ValidationFailed,
)
from bm.infra.db.enums import CreditNoteSource, MovementReason, PaymentStatus
from bm.infra.db.models.sales import Invoice

MIN_CANCEL_REASON_LENGTH = 3


@dataclass(frozen=True, slots=True)
class CancellationResult:
    invoice_id: uuid.UUID
    credit_note_id: uuid.UUID | None
    movements_reversed: int


class CancellationService:
    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock
        self._settings = SettingsService(session)
        self._inventory = InventoryService(session)
        self._credit = CreditService(session, clock)

    def cancel(self, ctx: AuthContext, invoice_id: uuid.UUID, *, reason: str) -> CancellationResult:
        ctx.require(Permission.BILLING_SALE_CANCEL)

        if len(reason.strip()) < MIN_CANCEL_REASON_LENGTH:
            raise ValidationFailed({"reason": "Say briefly why this bill is being cancelled."})

        invoice = self._session.execute(
            select(Invoice)
            .where(Invoice.id == invoice_id, Invoice.business_id == ctx.business_id)
            .with_for_update()
        ).scalar_one_or_none()
        if invoice is None:
            raise NotFound("That bill does not exist.")
        if str(invoice.status) == "cancelled":
            raise InvoiceAlreadyCancelled()

        window_hours = int(
            self._settings.get(ctx.business_id, ctx.branch_id, SettingKey.SALES_CANCEL_WINDOW_HOURS)
        )
        now = self._clock.now()
        if now - invoice.issued_at > timedelta(hours=window_hours):
            # Outside the window a return is the right instrument: it is a separate,
            # dated event rather than a rewrite of an old day's takings.
            raise CancelWindowExpired(
                details={
                    "window_hours": window_hours,
                    "issued_at": invoice.issued_at.isoformat(),
                }
            )

        # Reverse what actually moved, read from the ledger rather than recomputed from
        # the invoice items — a partial return may already have put some back.
        sale_movements = self._session.execute(
            text(
                """
                SELECT product_id, sum(-quantity_delta) AS sold
                  FROM stock_movements
                 WHERE source_type = 'invoice' AND source_id = :invoice_id
                   AND reason = 'sale'
                 GROUP BY product_id
                HAVING sum(-quantity_delta) > 0
                """
            ),
            {"invoice_id": invoice_id},
        ).all()

        movements = [
            MovementRequest(
                product_id=row.product_id,
                quantity=row.sold,
                reason=MovementReason.SALE_CANCELLATION,
                source_type="invoice",
                source_id=invoice_id,
            )
            for row in sale_movements
        ]
        created = self._inventory.apply_movements(
            ctx, branch_id=invoice.branch_id, movements=movements
        )

        credit_note_id: uuid.UUID | None = None
        if invoice.is_credit and invoice.customer_id is not None:
            credit_note_id = self._credit.issue_credit_note(
                ctx,
                customer_id=invoice.customer_id,
                amount=invoice.total,
                reason=f"Cancellation: {reason.strip()}",
                source_type=CreditNoteSource.CANCELLATION,
                source_id=invoice_id,
                original_invoice_id=invoice_id,
            )

        # Only the enumerated cancellation columns are writable by the app role
        # (`SECURITY.md §7`), so this statement is the complete set of what may change.
        self._session.execute(
            text(
                "UPDATE invoices"
                "   SET status = 'cancelled',"
                "       cancelled_at = :now,"
                "       cancelled_by_user_id = :user_id,"
                "       cancellation_reason = :reason,"
                "       payment_status = CAST(:status AS payment_status),"
                "       updated_at = now()"
                " WHERE id = :id"
            ),
            {
                "now": now,
                "user_id": ctx.user_id,
                "reason": reason.strip(),
                "status": str(PaymentStatus.CANCELLED),
                "id": invoice_id,
            },
        )

        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.INVOICE_CANCELLED,
            entity_type="invoice",
            entity_id=invoice_id,
            reason=reason.strip(),
            before={
                "status": "issued",
                "total": format(invoice.total, "f"),
                "payment_status": str(invoice.payment_status),
            },
            after={
                "status": "cancelled",
                "movements_reversed": len(created),
                "credit_note_id": str(credit_note_id) if credit_note_id else None,
                # Stated so the audit row answers the obvious question directly.
                "internal_reference_retained": invoice.invoice_number,
            },
        )

        return CancellationResult(
            invoice_id=invoice_id,
            credit_note_id=credit_note_id,
            movements_reversed=len(created),
        )
