"""Invoice revision history (migration 0024).

The immutable half of the editing feature. `invoices` and `invoice_items` hold what a
bill says **now**; these two tables hold what it said **before** every edit, and they are
`SELECT, INSERT` only at the database level — the application role cannot update or
delete a revision even by mistake.

That is the guarantee that replaces `0011`'s blanket revoke after the owner reversed
OD-10: not "a finalised invoice never changes", but "every state it has ever been in is
permanently recorded, with who changed it, when and why".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from bm.infra.db.base import (
    AMOUNT,
    NAME,
    NOTES,
    PERCENT,
    QUANTITY,
    UNIT_PRICE,
    Base,
    created_at_column,
)


class InvoiceRevision(Base):
    __tablename__ = "invoice_revisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    #: 1 for the first edit's before-state. Unique per invoice, so the history can
    #: neither gain a gap nor a duplicate.
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Mandatory and length-checked. "edit" is not a reason.
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    edited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # --- the header, as it was -------------------------------------------------
    customer_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    customer_name_snapshot: Mapped[str | None] = mapped_column(NAME, nullable=True)
    customer_business_name_snapshot: Mapped[str | None] = mapped_column(NAME, nullable=True)
    vehicle_number_snapshot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mechanic_name_snapshot: Mapped[str | None] = mapped_column(NAME, nullable=True)
    phone_snapshot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(NOTES, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    rounding_adjustment: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    total: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    #: What had been paid at the moment of the edit. Payments themselves are never
    #: touched — this is what makes the paid-total rule auditable after the fact.
    amount_paid: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("revision_number >= 1", name="ck_invoice_revisions__number"),
        CheckConstraint("length(btrim(reason)) >= 5", name="ck_invoice_revisions__reason_present"),
        UniqueConstraint(
            "invoice_id", "revision_number", name="uq_invoice_revisions__invoice_number"
        ),
        Index("ix_invoice_revisions__invoice", "invoice_id", "revision_number"),
    )


class InvoiceRevisionItem(Base):
    __tablename__ = "invoice_revision_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoice_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    #: The original line's id, so a reader can follow one line across revisions. Nullable
    #: because a line added and then removed in later edits still belongs in history.
    invoice_item_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    product_name_snapshot: Mapped[str] = mapped_column(NAME, nullable=False)
    sku_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit_code_snapshot: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(UNIT_PRICE, nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(PERCENT, nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    tax_rate_snapshot: Mapped[Decimal] = mapped_column(PERCENT, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    returned_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)

    __table_args__ = (Index("ix_invoice_revision_items__revision", "revision_id"),)
