"""Invoice reads for the API layer.

Exists so `api/v1/` never imports an ORM model (`ARCHITECTURE.md §3` rule 4). The
routers were reaching into `bm.infra.db.models.sales` directly, which the import linter
correctly rejected: a column rename would then have become an API change.

Everything here returns plain dataclasses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import Permission
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import NotFound
from bm.infra.db.models.sales import Invoice, InvoiceItem


@dataclass(frozen=True, slots=True)
class InvoiceItemRecord:
    line_no: int
    product_name_snapshot: str
    sku_snapshot: str | None
    unit_code_snapshot: str
    quantity: Decimal
    unit_price: Decimal
    discount_amount: Decimal
    tax_rate_snapshot: Decimal
    tax_amount: Decimal
    line_total: Decimal
    returned_quantity: Decimal
    #: Read regardless of permission; the **router** omits it without
    #: `catalog.cost.read`, because redaction is about what leaves the API.
    unit_cost_snapshot: Decimal | None


@dataclass(frozen=True, slots=True)
class InvoiceRecord:
    id: uuid.UUID
    #: ⚠️ Internal reference. Never rendered to a customer, never in `print_payload`.
    internal_reference: str
    sequence_number: int
    document_kind: str
    status: str
    issued_at: datetime
    is_credit: bool
    due_date: date | None
    customer_id: uuid.UUID | None
    customer_name_snapshot: str | None
    customer_business_name_snapshot: str | None
    vehicle_number_snapshot: str | None
    mechanic_name_snapshot: str | None
    phone_snapshot: str | None
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    rounding_adjustment: Decimal
    total: Decimal
    amount_paid: Decimal
    payment_status: str
    oversell_override: bool
    oversell_reason: str | None
    money_policy_version: int
    cancelled_at: datetime | None
    cancellation_reason: str | None
    items: tuple[InvoiceItemRecord, ...]

    @property
    def outstanding(self) -> Decimal:
        return self.total - self.amount_paid


@dataclass(frozen=True, slots=True)
class InvoiceSummary:
    id: uuid.UUID
    internal_reference: str
    issued_at: datetime
    status: str
    is_credit: bool
    due_date: date | None
    customer_id: uuid.UUID | None
    customer_name: str | None
    counter_code: str
    total: Decimal
    amount_paid: Decimal
    outstanding: Decimal
    payment_status: str


class InvoiceReader:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _timezone(self, business_id: uuid.UUID) -> str:
        return (
            self._session.execute(
                text("SELECT timezone FROM businesses WHERE id = :id"), {"id": business_id}
            ).scalar_one_or_none()
            or "Asia/Kolkata"
        )

    def get(self, ctx: AuthContext, invoice_id: uuid.UUID) -> InvoiceRecord:
        ctx.require(Permission.REPORTS_SALES_READ)
        return self.record_of(ctx.business_id, invoice_id)

    def record_of(self, business_id: uuid.UUID, invoice_id: uuid.UUID) -> InvoiceRecord:
        """Read an invoice **without** a permission check.

        For the caller that has already been authorised for something stronger — the
        finalisation route holds `billing.sale.finalize` and has just created this
        invoice. Requiring `reports.sales.read` there would mean a cashier who may bill
        but may not read reports gets a `403` *after* the sale has committed, which is
        the worst possible moment to refuse.

        It exists so the `201` body and a later `GET /invoices/{id}` are built from one
        record by one serialiser: the confirmation card and the Daily Bills row must
        never disagree about the same bill.
        """
        ctx_business_id = business_id
        invoice = self._session.execute(
            select(Invoice).where(Invoice.id == invoice_id, Invoice.business_id == ctx_business_id)
        ).scalar_one_or_none()
        if invoice is None:
            # A cross-tenant id is indistinguishable from an absent one (§4.1).
            raise NotFound("That bill does not exist.")

        items = (
            self._session.execute(
                select(InvoiceItem)
                .where(InvoiceItem.invoice_id == invoice_id)
                .order_by(InvoiceItem.line_no)
            )
            .scalars()
            .all()
        )

        return InvoiceRecord(
            id=invoice.id,
            internal_reference=invoice.invoice_number,
            sequence_number=invoice.sequence_number,
            document_kind=str(invoice.document_kind),
            status=str(invoice.status),
            issued_at=invoice.issued_at,
            is_credit=invoice.is_credit,
            due_date=invoice.due_date,
            customer_id=invoice.customer_id,
            customer_name_snapshot=invoice.customer_name_snapshot,
            customer_business_name_snapshot=invoice.customer_business_name_snapshot,
            vehicle_number_snapshot=invoice.vehicle_number_snapshot,
            mechanic_name_snapshot=invoice.mechanic_name_snapshot,
            phone_snapshot=invoice.phone_snapshot,
            subtotal=invoice.subtotal,
            discount_total=invoice.discount_total,
            tax_total=invoice.tax_total,
            rounding_adjustment=invoice.rounding_adjustment,
            total=invoice.total,
            amount_paid=invoice.amount_paid,
            payment_status=str(invoice.payment_status),
            oversell_override=invoice.oversell_override,
            oversell_reason=invoice.oversell_reason,
            money_policy_version=invoice.money_policy_version,
            cancelled_at=invoice.cancelled_at,
            cancellation_reason=invoice.cancellation_reason,
            items=tuple(
                InvoiceItemRecord(
                    line_no=i.line_no,
                    product_name_snapshot=i.product_name_snapshot,
                    sku_snapshot=i.sku_snapshot,
                    unit_code_snapshot=i.unit_code_snapshot,
                    quantity=i.quantity,
                    unit_price=i.unit_price,
                    discount_amount=i.discount_amount,
                    tax_rate_snapshot=i.tax_rate_snapshot,
                    tax_amount=i.tax_amount,
                    line_total=i.line_total,
                    returned_quantity=i.returned_quantity,
                    unit_cost_snapshot=i.unit_cost_snapshot,
                )
                for i in items
            ),
        )

    def items_of(self, invoice_id: uuid.UUID) -> tuple[InvoiceItemRecord, ...]:
        rows = (
            self._session.execute(
                select(InvoiceItem)
                .where(InvoiceItem.invoice_id == invoice_id)
                .order_by(InvoiceItem.line_no)
            )
            .scalars()
            .all()
        )
        return tuple(
            InvoiceItemRecord(
                line_no=i.line_no,
                product_name_snapshot=i.product_name_snapshot,
                sku_snapshot=i.sku_snapshot,
                unit_code_snapshot=i.unit_code_snapshot,
                quantity=i.quantity,
                unit_price=i.unit_price,
                discount_amount=i.discount_amount,
                tax_rate_snapshot=i.tax_rate_snapshot,
                tax_amount=i.tax_amount,
                line_total=i.line_total,
                returned_quantity=i.returned_quantity,
                unit_cost_snapshot=i.unit_cost_snapshot,
            )
            for i in rows
        )

    def list_invoices(
        self,
        ctx: AuthContext,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        payment_status: str | None = None,
        customer_id: uuid.UUID | None = None,
        counter_id: uuid.UUID | None = None,
        is_credit: bool | None = None,
        idempotency_key: str | None = None,
        limit: int = 50,
    ) -> list[InvoiceSummary]:
        """Backs Daily Bills **and** Credit Bills — one dataset, two views (FR-RPT-003)."""
        ctx.require(Permission.REPORTS_SALES_READ)

        sql = """
            SELECT i.id, i.invoice_number, i.issued_at, i.status, i.is_credit, i.due_date,
                   i.customer_id, i.customer_name_snapshot, i.total, i.amount_paid,
                   i.total - i.amount_paid AS outstanding, i.payment_status,
                   c.code AS counter_code
              FROM invoices i
              JOIN counters c ON c.id = i.counter_id
             WHERE i.business_id = :business_id
        """
        params: dict[str, Any] = {
            "business_id": ctx.business_id,
            "timezone": self._timezone(ctx.business_id),
            "limit": min(limit, 200),
        }

        # A cashier sees only their own counter (`PRD.md §3.2` note 2).
        effective_counter = counter_id
        if not ctx.has(Permission.REPORTS_FINANCIAL_READ) and ctx.counter_id is not None:
            effective_counter = ctx.counter_id

        if from_date is not None:
            sql += " AND (i.issued_at AT TIME ZONE :timezone)::date >= :from_date"
            params["from_date"] = from_date
        if to_date is not None:
            sql += " AND (i.issued_at AT TIME ZONE :timezone)::date <= :to_date"
            params["to_date"] = to_date
        if payment_status:
            sql += " AND i.payment_status = CAST(:payment_status AS payment_status)"
            params["payment_status"] = payment_status
        if customer_id is not None:
            sql += " AND i.customer_id = :customer_id"
            params["customer_id"] = customer_id
        if effective_counter is not None:
            sql += " AND i.counter_id = :counter_id"
            params["counter_id"] = effective_counter
        if is_credit is not None:
            sql += " AND i.is_credit = :is_credit"
            params["is_credit"] = is_credit
        if idempotency_key:
            # T3-01: exact lookup so §5.4.5 case-12 arbitration is exact rather than
            # heuristic. Restricted at the route and never in `print_payload`.
            sql += " AND i.idempotency_key = :idempotency_key"
            params["idempotency_key"] = idempotency_key

        sql += " ORDER BY i.issued_at DESC LIMIT :limit"

        return [
            InvoiceSummary(
                id=r.id,
                internal_reference=r.invoice_number,
                issued_at=r.issued_at,
                status=r.status,
                is_credit=r.is_credit,
                due_date=r.due_date,
                customer_id=r.customer_id,
                customer_name=r.customer_name_snapshot,
                counter_code=r.counter_code,
                total=r.total,
                amount_paid=r.amount_paid,
                outstanding=r.outstanding,
                payment_status=r.payment_status,
            )
            for r in self._session.execute(text(sql), params).all()
        ]
