"""The authoritative print payload (M12).

**The backend decides the content; the frontend decides the pixels.** The client renders
this verbatim, adds no field, computes no total and invents no header text (FR-PRINT-010).

Three rules are enforced here because the client cannot be the place they are decided:

* ❌ **No invoice number.** [OD-3] is re-answered — *"No need of invoice numbering."*
  The payload carries **no** invoice-number field at all, and `test_no_invoice_number_in_print_payload`
  asserts it (T3-06). The internal reference stays on the `invoice` resource for audit.
* **`totals.tax` is `null`, not `"0.00"`**, while `tax.mode = estimate_only`, so the
  client renders **no tax row at all** rather than a misleading zero (ADR-020).
* **`document_title` comes from the server only** and is `ESTIMATE` for cash and credit
  alike (OD-24, FR-PRINT-021). It can never be `TAX INVOICE` until the FR-TAX-003 gate
  passes.

Amounts are emitted as **exact decimal strings**. The server never pre-formats, pads,
truncates or rounds for presentation (OD-23) — zero-decimal rendering is the client's
job, and a fractional value reaching it is a blocking error there.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import Permission
from bm.domain.settings.defaults import SettingKey
from bm.domain.settings.service import SettingsService
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import NotFound
from bm.domain.shared.money import amount_in_words
from bm.infra.db.models.sales import Invoice, InvoiceItem
from bm.infra.db.models.tenancy import Business

#: The workbook's own column headers, read from the file on 2026-10-30
#: (`UI-UX-SPECIFICATION.md §12.1`). Deliberately **not** the on-screen labels: the app
#: is not a spreadsheet replica, the bill is.
PRINT_COLUMNS = ("sl_no", "item", "quantity", "price", "total")

#: The observed `Table 1` range is A1:E37 with **25** item rows. Unused rows print
#: blank — never the reference's hand-typed `0` (FR-PRINT-018).
ITEM_ROWS = 25


@dataclass(frozen=True, slots=True)
class PrintPayload:
    document_title: str
    is_duplicate: bool
    business: dict[str, Any]
    issued_at: str
    customer: dict[str, Any]
    columns: tuple[str, ...]
    lines: list[dict[str, Any]]
    totals: dict[str, Any]
    amount_in_words: str
    payment: dict[str, Any]
    footer_notes: list[str]
    tax_mode: str
    #: Print profile and date order, both from a single configurable source so the
    #: client never carries its own copy (T3-11, OD-6).
    print_profile: str
    date_format: str
    #: `print.show_credit_block` keeps OD-22's placement switchable until the physical
    #: sample is accepted at M27.
    show_credit_block: bool
    item_row_capacity: int = ITEM_ROWS

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "document_title": self.document_title,
            "is_duplicate": self.is_duplicate,
            "business": self.business,
            "issued_at": self.issued_at,
            "customer": self.customer,
            "columns": list(self.columns),
            "lines": self.lines,
            "totals": self.totals,
            "amount_in_words": self.amount_in_words,
            "payment": self.payment,
            "footer_notes": self.footer_notes,
            "tax_mode": self.tax_mode,
            "print_profile": self.print_profile,
            "date_format": self.date_format,
            "show_credit_block": self.show_credit_block,
            "item_row_capacity": self.item_row_capacity,
        }
        # Asserted by a test rather than trusted: no key anywhere in this payload may
        # carry an invoice number (T3-06).
        assert "invoice_number" not in payload
        return payload


class PrintPayloadService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._settings = SettingsService(session)

    def build(
        self, ctx: AuthContext, invoice_id: uuid.UUID, *, is_duplicate: bool = False
    ) -> PrintPayload:
        ctx.require(Permission.REPORTS_SALES_READ)

        invoice = self._session.execute(
            select(Invoice).where(Invoice.id == invoice_id, Invoice.business_id == ctx.business_id)
        ).scalar_one_or_none()
        if invoice is None:
            raise NotFound("That bill does not exist.")

        business = self._session.execute(
            select(Business).where(Business.id == ctx.business_id)
        ).scalar_one()

        items = list(
            self._session.execute(
                select(InvoiceItem)
                .where(InvoiceItem.invoice_id == invoice_id)
                .order_by(InvoiceItem.line_no)
            )
            .scalars()
            .all()
        )

        tax_mode = str(self._settings.get(ctx.business_id, ctx.branch_id, SettingKey.TAX_MODE))
        profile = str(
            self._settings.get(ctx.business_id, ctx.branch_id, SettingKey.PRINT_PAPER_PROFILE)
        )
        date_format = str(
            self._settings.get(ctx.business_id, ctx.branch_id, SettingKey.PRINT_DATE_FORMAT)
        )
        show_credit_block = bool(
            self._settings.get(ctx.business_id, ctx.branch_id, SettingKey.PRINT_SHOW_CREDIT_BLOCK)
        )

        outstanding = invoice.total - invoice.amount_paid

        return PrintPayload(
            # Never `TAX INVOICE` while estimate_only, and never `BILL`/`INVOICE` at all.
            document_title=str(invoice.document_kind).upper().replace("_", " ")
            if tax_mode != "estimate_only"
            else "ESTIMATE",
            is_duplicate=is_duplicate,
            business={
                # Empty optional fields print nothing — **never a placeholder**
                # (FR-PRINT-016). The owner has not supplied a legal name (OD-4) and
                # inventing one is forbidden.
                "display_name": business.display_name,
                "address_lines": list(business.address_lines or ()),
                "phone_numbers": list(business.phone_numbers or ()),
                "tax_identifier": business.tax_identifier,
                "tax_identifier_label": (
                    business.tax_identifier_type.upper()
                    if business.tax_identifier and business.tax_identifier_type
                    else None
                ),
            },
            issued_at=invoice.issued_at.isoformat(),
            customer={
                "name": invoice.customer_name_snapshot,
                "business_name": invoice.customer_business_name_snapshot,
                "vehicle_number": invoice.vehicle_number_snapshot,
                "mechanic_name": invoice.mechanic_name_snapshot,
                "phone": invoice.phone_snapshot,
            },
            columns=PRINT_COLUMNS,
            lines=[
                {
                    "sl_no": item.line_no,
                    "item": item.product_name_snapshot,
                    # Exact decimal strings. The client formats; the server never does.
                    "quantity": format(item.quantity, "f"),
                    "price": format(item.unit_price, "f"),
                    "total": format(item.line_total, "f"),
                }
                for item in items
            ],
            totals={
                "subtotal": format(invoice.subtotal, "f"),
                "discount": format(invoice.discount_total, "f"),
                # `null`, not `"0.00"`, so the client renders no tax row (ADR-020).
                "tax": format(invoice.tax_total, "f") if tax_mode != "estimate_only" else None,
                "rounding": format(invoice.rounding_adjustment, "f"),
                "grand_total": format(invoice.total, "f"),
            },
            # ⚠️ Carried in the payload per the API contract, but the printed bill must
            # **not** render it: an amount-in-words line is verified **absent** from the
            # owner's workbook (OD-5).
            amount_in_words=amount_in_words(invoice.total),
            payment={
                "status": str(invoice.payment_status),
                # The **true recorded** figures (FR-CREDIT-002). The client renders them
                # for credit bills and never recomputes them (FR-PRINT-020).
                "amount_paid": format(invoice.amount_paid, "f"),
                "outstanding": format(outstanding, "f"),
                "method": None,
                "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
                # Cash bills omit the block entirely (OD-22).
                "is_credit": invoice.is_credit,
            },
            footer_notes=[],
            tax_mode=tax_mode,
            print_profile=profile,
            date_format=date_format,
            show_credit_block=show_credit_block and invoice.is_credit,
        )

    def log_print_event(
        self, ctx: AuthContext, invoice_id: uuid.UUID, *, is_duplicate: bool
    ) -> None:
        """Record a print attempt (FR-PRINT-004).

        A logging failure must **never** block printing — a customer waiting for their
        bill is not a good moment to fail on an audit nicety — so the caller swallows
        errors from this and the route still returns success.
        """

        copy_number = self._session.execute(
            text("SELECT count(*) + 1 FROM print_log WHERE invoice_id = :id"),
            {"id": invoice_id},
        ).scalar_one()
        self._session.execute(
            text(
                "INSERT INTO print_log"
                " (id, business_id, invoice_id, printed_by_user_id, device_id,"
                "  copy_number, is_duplicate)"
                " VALUES (gen_random_uuid(), :business_id, :invoice_id, :user_id,"
                "         :device_id, :copy_number, :is_duplicate)"
            ),
            {
                "business_id": ctx.business_id,
                "invoice_id": invoice_id,
                "user_id": ctx.user_id,
                "device_id": ctx.device_id,
                "copy_number": copy_number,
                "is_duplicate": is_duplicate,
            },
        )

    def reprint_is_duplicate(self, ctx: AuthContext, invoice_id: uuid.UUID) -> bool:
        """`print.mark_reprints_duplicate`, default true (FR-PRINT-004)."""
        if not bool(
            self._settings.get(
                ctx.business_id, ctx.branch_id, SettingKey.PRINT_MARK_REPRINTS_DUPLICATE
            )
        ):
            return False
        printed = self._session.execute(
            text("SELECT count(*) FROM print_log WHERE invoice_id = :id"),
            {"id": invoice_id},
        ).scalar_one()
        return bool(printed and printed > 0)
