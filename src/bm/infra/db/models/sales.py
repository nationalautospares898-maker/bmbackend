"""Sales tables (migrations 0011–0012).

`invoice_series`, `draft_bills`, `draft_bill_items`, `invoices`, `invoice_items`,
`print_log`.

The constraints here are the business rules, expressed where they cannot be bypassed:

| Constraint | What it makes impossible |
|---|---|
| `uq_draft_bills__invoice` | Finalising one draft twice |
| `ck_draft_bills__finalized` | A draft marked finalised with no invoice, or the reverse |
| `uq_invoices__series_sequence` | Two invoices sharing a sequence number (NFR-DATA-005) |
| `ck_invoices__total` | A total that does not reconcile (NFR-DATA-002) |
| `ck_invoices__credit_customer` | A credit invoice with no customer (FR-CREDIT-009) |
| `ck_invoice_items__returned` | Returning more than was sold (NFR-DATA-008) |

⚠️ **`invoices.invoice_number` is an INTERNAL reference only.** OD-3 is re-answered —
*"No need of invoice numbering."* It must never be rendered to a customer and must not
appear in `print_payload`. It is retained for audit, reconciliation, stable ordering and
the OD-25 contingency.
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
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from bm.infra.db.base import (
    AMOUNT,
    NAME,
    NOTES,
    PERCENT,
    QUANTITY,
    SHORT_TEXT,
    UNIT_PRICE,
    Base,
    created_at_column,
    updated_at_column,
)
from bm.infra.db.enums import (
    DOCUMENT_KIND,
    DRAFT_STATUS,
    INVOICE_STATUS,
    PAYMENT_STATUS,
    PRICE_SOURCE,
)


class InvoiceSeries(Base):
    """One row per (business, branch, series, financial year).

    `next_number` is locked `FOR UPDATE` inside the finalisation transaction, so a
    rolled-back sale consumes no number. The row for a new financial year is created
    **lazily inside that transaction** (`P2-D-09`), so the first sale after 1 April
    cannot fail for want of a row and two concurrent first-sales cannot conflict.
    """

    __tablename__ = "invoice_series"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    series_code: Mapped[str] = mapped_column(String(32), nullable=False)
    #: e.g. `2026-27`, derived from `businesses.financial_year_start_month` in
    #: business-local time (T3-02).
    financial_year: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Formats the **internal** reference only. Not customer-visible.
    prefix_template: Mapped[str] = mapped_column(String(64), nullable=False)
    next_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    #: Follows `tax.mode`. A series never changes kind mid-year.
    document_kind: Mapped[str] = mapped_column(
        DOCUMENT_KIND, nullable=False, server_default=text("'estimate'::document_kind")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "branch_id",
            "series_code",
            "financial_year",
            name="uq_invoice_series__scope",
        ),
        CheckConstraint("next_number >= 1", name="ck_invoice_series__next_number"),
    )


class DraftBill(Base):
    """A draft is **never** a sale. It reserves no stock (ADR-006) and holds no number."""

    __tablename__ = "draft_bills"

    #: Client-generated (FR-DRAFT-001), so an offline-created draft syncs idempotently.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    counter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("counters.id", ondelete="RESTRICT"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(NAME, nullable=True)
    status: Mapped[str] = mapped_column(
        DRAFT_STATUS, nullable=False, server_default=text("'open'::draft_status")
    )
    #: Optimistic concurrency (FR-DRAFT-004). There is **no** server-side merge
    #: (ADR-007) — a conflict returns `current_state` for the user to resolve.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=True
    )
    customer_name: Mapped[str | None] = mapped_column(NAME, nullable=True)
    customer_business_name: Mapped[str | None] = mapped_column(NAME, nullable=True)
    vehicle_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mechanic_name: Mapped[str | None] = mapped_column(NAME, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(NOTES, nullable=True)
    #: A hint only. Finalisation decides the sale type from the request, not from here.
    is_credit_intent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    #: Advisory (FR-DRAFT-011). The server recomputes everything at finalisation.
    preview_total: Mapped[Decimal | None] = mapped_column(AMOUNT, nullable=True)
    #: Set on finalisation. The partial unique index prevents double-finalisation.
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=True
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    last_edited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=True
    )
    #: Diagnostics only. Server time is authoritative (FR-OFFLINE-007).
    client_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        CheckConstraint(
            "(status = 'finalized') = (invoice_id IS NOT NULL)",
            name="ck_draft_bills__finalized",
        ),
        CheckConstraint("version >= 1", name="ck_draft_bills__version"),
    )


class DraftBillItem(Base):
    __tablename__ = "draft_bill_items"

    #: Client-generated.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    draft_bill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("draft_bills.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    product_name_snapshot: Mapped[str] = mapped_column(NAME, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(UNIT_PRICE, nullable=False)
    #: What the catalog said when the line was added, so a later price change is
    #: detectable at finalisation (FR-SALE-006).
    catalog_price_at_add: Mapped[Decimal] = mapped_column(UNIT_PRICE, nullable=False)
    price_source: Mapped[str] = mapped_column(PRICE_SOURCE, nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(
        PERCENT, nullable=False, server_default=text("0")
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        AMOUNT, nullable=False, server_default=text("0")
    )
    line_total_preview: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    notes: Mapped[str | None] = mapped_column(SHORT_TEXT, nullable=True)

    __table_args__ = (
        UniqueConstraint("draft_bill_id", "line_no", name="uq_draft_bill_items__line"),
        CheckConstraint("quantity > 0", name="ck_draft_bill_items__quantity"),
        CheckConstraint("unit_price >= 0", name="ck_draft_bill_items__price"),
    )


class Invoice(Base):
    """Immutable once issued.

    `UPDATE` is granted at column level only for the cancellation fields and the derived
    payment cache; `DELETE` is revoked entirely (`SECURITY.md §7`). Corrections are
    cancellation, return, credit note or reversal (ADR-015).
    """

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    counter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("counters.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_series_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoice_series.id", ondelete="RESTRICT"), nullable=False
    )
    #: ⚠️ INTERNAL reference string only — e.g. `NAS/2026-27/00412`. **Never** rendered
    #: to a customer and **never** present in `print_payload` (OD-3 re-answered).
    invoice_number: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Numeric part. Supports uniqueness and stable ordering.
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Snapshot at issue time (FR-PRINT-002).
    document_kind: Mapped[str] = mapped_column(DOCUMENT_KIND, nullable=False)
    status: Mapped[str] = mapped_column(
        INVOICE_STATUS, nullable=False, server_default=text("'issued'::invoice_status")
    )
    #: Server time. The sale date for reports, bucketed in business-local time (T3-02).
    issued_at: Mapped[datetime] = created_at_column()

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=True
    )
    #: Printed exactly as sold. A later customer rename never changes a past bill.
    customer_name_snapshot: Mapped[str | None] = mapped_column(NAME, nullable=True)
    customer_business_name_snapshot: Mapped[str | None] = mapped_column(NAME, nullable=True)
    vehicle_number_snapshot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mechanic_name_snapshot: Mapped[str | None] = mapped_column(NAME, nullable=True)
    phone_snapshot: Mapped[str | None] = mapped_column(String(32), nullable=True)

    is_credit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    subtotal: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(
        AMOUNT, nullable=False, server_default=text("0")
    )
    tax_total: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, server_default=text("0"))
    #: Keeps the arithmetic reconcilable. Exactly zero under the v1 money policy.
    rounding_adjustment: Mapped[Decimal] = mapped_column(
        AMOUNT, nullable=False, server_default=text("0")
    )
    total: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)

    #: **Derived cache.** Written only in the same transaction as the allocations; no
    #: endpoint sets it (ADR-022).
    amount_paid: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, server_default=text("0"))
    #: Computed, never user-set. There is no "paid" checkbox in this system.
    payment_status: Mapped[str] = mapped_column(
        PAYMENT_STATUS, nullable=False, server_default=text("'unpaid'::payment_status")
    )

    cost_total: Mapped[Decimal | None] = mapped_column(AMOUNT, nullable=True)
    oversell_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    oversell_reason: Mapped[str | None] = mapped_column(SHORT_TEXT, nullable=True)
    price_changes_accepted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    tax_config_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tax_config_versions.id", ondelete="RESTRICT"), nullable=True
    )
    #: T3-03. Persisted at issue time so a disputed historical bill can be explained
    #: under the rule that produced it. A policy change never alters a stored invoice.
    money_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(SHORT_TEXT, nullable=True)

    draft_bill_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=True
    )
    #: For forensic tracing, and for the exact lookup in T3-01. **Not** in
    #: `print_payload`.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(NOTES, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint(
            "invoice_series_id", "sequence_number", name="uq_invoices__series_sequence"
        ),
        UniqueConstraint("business_id", "invoice_number", name="uq_invoices__business_number"),
        # NFR-DATA-002 as a database guarantee, not just a test.
        CheckConstraint(
            "total = subtotal - discount_total + tax_total + rounding_adjustment",
            name="ck_invoices__total",
        ),
        CheckConstraint(
            "NOT is_credit OR customer_id IS NOT NULL", name="ck_invoices__credit_customer"
        ),
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
            name="ck_invoices__cancel_fields",
        ),
        CheckConstraint(
            "NOT oversell_override OR length(oversell_reason) >= 10",
            name="ck_invoices__oversell_reason",
        ),
        CheckConstraint("amount_paid >= 0", name="ck_invoices__amount_paid"),
    )


class InvoiceItem(Base):
    """Immutable snapshots.

    `returned_quantity` is the **only** mutable column, updated solely by the returns
    module under a row lock — which is what makes NFR-DATA-008 enforceable at the
    database level rather than by convention.
    """

    __tablename__ = "invoice_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Reference only. The snapshots below are what the bill shows, so editing the
    #: catalog never changes a past invoice.
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    product_name_snapshot: Mapped[str] = mapped_column(NAME, nullable=False)
    sku_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit_code_snapshot: Mapped[str] = mapped_column(String(16), nullable=False)
    hsn_snapshot: Mapped[str | None] = mapped_column(String(16), nullable=True)

    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(UNIT_PRICE, nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(
        PERCENT, nullable=False, server_default=text("0")
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        AMOUNT, nullable=False, server_default=text("0")
    )
    taxable_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    tax_rate_snapshot: Mapped[Decimal] = mapped_column(
        PERCENT, nullable=False, server_default=text("0")
    )
    tax_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, server_default=text("0"))
    line_total: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    unit_cost_snapshot: Mapped[Decimal | None] = mapped_column(UNIT_PRICE, nullable=True)
    returned_quantity: Mapped[Decimal] = mapped_column(
        QUANTITY, nullable=False, server_default=text("0")
    )
    price_source: Mapped[str] = mapped_column(PRICE_SOURCE, nullable=False)
    catalog_price_at_sale: Mapped[Decimal] = mapped_column(UNIT_PRICE, nullable=False)

    __table_args__ = (
        UniqueConstraint("invoice_id", "line_no", name="uq_invoice_items__line"),
        CheckConstraint("quantity > 0", name="ck_invoice_items__quantity"),
        CheckConstraint("unit_price >= 0", name="ck_invoice_items__price"),
        CheckConstraint(
            "returned_quantity >= 0 AND returned_quantity <= quantity",
            name="ck_invoice_items__returned",
        ),
    )


class PrintLog(Base):
    """A print attempt (FR-PRINT-004).

    Logging a print **never** blocks printing: a failure here must not stop a customer
    receiving their bill.
    """

    __tablename__ = "print_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    printed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=True
    )
    copy_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_duplicate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    printed_at: Mapped[datetime] = created_at_column()


Index(
    "uq_draft_bills__invoice",
    DraftBill.invoice_id,
    unique=True,
    postgresql_where=text("invoice_id IS NOT NULL"),
)
Index(
    "ix_draft_bills__open",
    DraftBill.counter_id,
    DraftBill.status,
    postgresql_where=text("status = 'open'"),
)
Index("ix_invoices__branch_issued", Invoice.branch_id, Invoice.issued_at.desc())
Index(
    "ix_invoices__customer_status",
    Invoice.customer_id,
    Invoice.payment_status,
    postgresql_where=text("is_credit"),
)
Index(
    "ix_invoices__outstanding",
    Invoice.business_id,
    Invoice.due_date,
    postgresql_where=text("payment_status IN ('unpaid','partially_paid')"),
)
# S-2: the Credit Bills view's top-level query.
Index(
    "ix_invoices__customer_outstanding",
    Invoice.business_id,
    Invoice.customer_id,
    Invoice.issued_at.desc(),
    postgresql_where=text("is_credit AND payment_status IN ('unpaid','partially_paid')"),
)
Index("ix_invoices__idempotency_key", Invoice.business_id, Invoice.idempotency_key)
Index("ix_invoice_items__product", InvoiceItem.product_id, InvoiceItem.invoice_id)
Index("ix_print_log__invoice", PrintLog.invoice_id, PrintLog.printed_at.desc())
#: Migration `0017` gave this child table its own `business_id` so a tenant-scoped scan
#: does not need the parent join, and indexed it alongside the parent key it is filtered
#: with. Mirrored into metadata so autogenerate cannot propose dropping it — see the note
#: in `catalog.py`.
Index(
    "ix_draft_bill_items__business",
    DraftBillItem.business_id,
    DraftBillItem.draft_bill_id,
)
