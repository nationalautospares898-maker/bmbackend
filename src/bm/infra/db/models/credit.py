"""Credit, payments, purchases and returns (migrations 0010, 0013–0014).

`payments`, `payment_allocations`, `credit_notes`, `credit_note_allocations`,
`suppliers`, `purchases`, `purchase_items`, `sales_returns`, `sales_return_items`.

Two rules are worth stating where the tables are defined:

* **Recording a payment never moves stock** (FR-CREDIT-010). `payments` and
  `payment_allocations` are the only tables a payment touches. Sale finalisation and
  payment collection are distinct financial events, and conflating them is how a sale's
  inventory gets deducted twice.
* **`payments` is append-only.** A mistake is corrected by a `reversal` row that points
  at the original (FR-PAY-005); the original is never edited.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from bm.infra.db.base import (
    AMOUNT,
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
    ALLOCATION_STRATEGY,
    CREDIT_NOTE_SOURCE,
    CREDIT_NOTE_STATUS,
    PAYMENT_DIRECTION,
    PAYMENT_KIND,
    PAYMENT_METHOD,
    PURCHASE_STATUS,
    RETURN_CONDITION,
)


class Payment(Base):
    """Append-only. No payment instrument data is ever stored (`SECURITY.md §10.1`) —
    only a method and a free-text reference such as a UPI transaction id, which is what
    keeps this system out of PCI scope entirely rather than managing it."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    counter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counters.id", ondelete="RESTRICT"), nullable=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=True
    )
    direction: Mapped[str] = mapped_column(
        PAYMENT_DIRECTION, nullable=False, server_default=text("'received'::payment_direction")
    )
    kind: Mapped[str] = mapped_column(
        PAYMENT_KIND, nullable=False, server_default=text("'payment'::payment_kind")
    )
    reverses_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    method: Mapped[str] = mapped_column(PAYMENT_METHOD, nullable=False)
    #: UPI reference or cheque number. No instrument data.
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    allocation_strategy: Mapped[str] = mapped_column(ALLOCATION_STRATEGY, nullable=False)
    received_at: Mapped[datetime] = created_at_column()
    received_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(NOTES, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments__amount_positive"),
        CheckConstraint(
            "(kind = 'reversal') = (reverses_payment_id IS NOT NULL)",
            name="ck_payments__reversal",
        ),
    )


class PaymentAllocation(Base):
    """NFR-DATA-003/004 cannot be single-row `CHECK`s, so they are held by a
    same-transaction write under lock, invariant tests, and a nightly job that alerts."""

    __tablename__ = "payment_allocations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        UniqueConstraint("payment_id", "invoice_id", name="uq_payment_allocations__pair"),
        CheckConstraint("amount > 0", name="ck_payment_allocations__amount"),
    )


class CreditNote(Base):
    __tablename__ = "credit_notes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=True
    )
    #: Internal reference, like the invoice reference. Not customer-visible.
    note_number: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(CREDIT_NOTE_SOURCE, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    original_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    reason: Mapped[str] = mapped_column(SHORT_TEXT, nullable=False)
    issued_at: Mapped[datetime] = created_at_column()
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        CREDIT_NOTE_STATUS, nullable=False, server_default=text("'open'::credit_note_status")
    )

    __table_args__ = (
        UniqueConstraint("business_id", "note_number", name="uq_credit_notes__number"),
        CheckConstraint("amount > 0", name="ck_credit_notes__amount"),
    )


class CreditNoteAllocation(Base):
    __tablename__ = "credit_note_allocations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    credit_note_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credit_notes.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        UniqueConstraint("credit_note_id", "invoice_id", name="uq_credit_note_allocations__pair"),
        CheckConstraint("amount > 0", name="ck_credit_note_allocations__amount"),
    )


# --- purchases ---------------------------------------------------------------


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(NAME, nullable=False)
    normalized_name: Mapped[str] = mapped_column(NAME, nullable=False)
    business_name: Mapped[str | None] = mapped_column(NAME, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address_lines: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    tax_identifier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(NOTES, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint("business_id", "normalized_name", name="uq_suppliers__business_name"),
    )


class Purchase(Base):
    """Goods receipt. **Only the `draft → confirmed` transition creates stock
    movements** (FR-PUR-003) — a recorded purchase is not received stock."""

    __tablename__ = "purchases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_document_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_at: Mapped[datetime] = created_at_column()
    status: Mapped[str] = mapped_column(
        PURCHASE_STATUS, nullable=False, server_default=text("'draft'::purchase_status")
    )
    subtotal: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, server_default=text("0"))
    tax_total: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, server_default=text("0"))
    total: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, server_default=text("0"))
    notes: Mapped[str | None] = mapped_column(NOTES, nullable=True)
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        CheckConstraint(
            "(status = 'confirmed') = (confirmed_at IS NOT NULL)",
            name="ck_purchases__confirmed",
        ),
    )


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    purchase_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    product_name_snapshot: Mapped[str] = mapped_column(NAME, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(UNIT_PRICE, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("purchase_id", "line_no", name="uq_purchase_items__line"),
        CheckConstraint("quantity > 0", name="ck_purchase_items__quantity"),
        CheckConstraint("unit_cost >= 0", name="ck_purchase_items__cost"),
    )


# --- returns -----------------------------------------------------------------


class SalesReturn(Base):
    __tablename__ = "sales_returns"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    return_number: Mapped[str] = mapped_column(String(64), nullable=False)
    returned_at: Mapped[datetime] = created_at_column()
    reason: Mapped[str] = mapped_column(SHORT_TEXT, nullable=False)
    #: `false` sends the goods to scrap rather than back into sellable stock.
    restock: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    credit_note_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credit_notes.id", ondelete="RESTRICT"), nullable=True
    )
    total_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("business_id", "return_number", name="uq_sales_returns__number"),
        CheckConstraint("total_amount > 0", name="ck_sales_returns__amount"),
    )


class SalesReturnItem(Base):
    __tablename__ = "sales_return_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sales_return_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_returns.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoice_items.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    #: The price the item was **sold** at, not today's price (AC-12).
    unit_price_snapshot: Mapped[Decimal] = mapped_column(UNIT_PRICE, nullable=False)
    line_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    condition: Mapped[str] = mapped_column(RETURN_CONDITION, nullable=False)

    __table_args__ = (
        UniqueConstraint("sales_return_id", "invoice_item_id", name="uq_sales_return_items__pair"),
        CheckConstraint("quantity > 0", name="ck_sales_return_items__quantity"),
    )


Index(
    "uq_purchases__supplier_doc",
    Purchase.business_id,
    Purchase.supplier_id,
    Purchase.supplier_document_number,
    unique=True,
    postgresql_where=text("supplier_document_number IS NOT NULL"),
)
Index("ix_payments__customer_time", Payment.customer_id, Payment.received_at.desc())
Index("ix_payments__branch_day", Payment.branch_id, Payment.received_at)
Index("ix_payment_allocations__invoice", PaymentAllocation.invoice_id)
Index("ix_credit_notes__customer", CreditNote.customer_id, CreditNote.issued_at.desc())
Index("ix_purchases__supplier_time", Purchase.supplier_id, Purchase.received_at.desc())
Index("ix_sales_returns__invoice", SalesReturn.invoice_id)
