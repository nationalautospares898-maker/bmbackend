"""Reports (M13).

Daily sales and credit outstanding are **two views of one dataset** and must reconcile
exactly (FR-RPT-003). `test_daily_and_credit_reconcile` is the single most valuable test
in this module: if the two disagree, one of them is lying to the owner about how much
money the shop is owed.

Three details that are requirements, not polish:

* **Credit sales are included in daily sales**, on their sale date (FR-RPT-001). A
  credit sale is a sale; excluding it would understate the day's trading.
* **Cancelled invoices are excluded from revenue but reported in an `excluded` block**
  (FR-RPT-008), so the numbers reconcile and nothing silently disappears.
* **Day boundaries use business-local time** (T3-02). A sale at 23:59:59 and one at
  00:00:00 land on different days, which is false if you truncate UTC.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import Permission
from bm.domain.shared.clock import Clock, business_date
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import ValidationFailed

#: Leading characters a spreadsheet interprets as a formula. A product named `=cmd()`
#: must export as text, not execute in the accountant's spreadsheet
#: (`SECURITY.md §4.1`).
_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def escape_csv_value(value: Any) -> str:
    """Neutralise a formula-shaped cell by prefixing an apostrophe."""
    text_value = "" if value is None else str(value)
    if text_value.startswith(_CSV_INJECTION_PREFIXES):
        return "'" + text_value
    return text_value


@dataclass(frozen=True, slots=True)
class ReportEnvelope:
    """Every report carries `as_of` and its `filters` (`API-SPECIFICATION.md §3.11`)."""

    as_of: str
    filters: dict[str, Any]
    data: dict[str, Any]
    excluded: dict[str, Any]


class ReportService:
    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    def _business_timezone(self, business_id: uuid.UUID) -> str:
        return (
            self._session.execute(
                text("SELECT timezone FROM businesses WHERE id = :id"), {"id": business_id}
            ).scalar_one_or_none()
            or "Asia/Kolkata"
        )

    # --- daily sales ----------------------------------------------------------

    def daily_sales(
        self,
        ctx: AuthContext,
        *,
        from_date: date,
        to_date: date,
        counter_id: uuid.UUID | None = None,
    ) -> ReportEnvelope:
        ctx.require(Permission.REPORTS_SALES_READ)
        if to_date < from_date:
            raise ValidationFailed({"to": "The end date cannot be before the start date."})

        timezone = self._business_timezone(ctx.business_id)

        # A cashier sees only their own counter/shift totals (`PRD.md §3.2` note 2).
        effective_counter = counter_id
        if not ctx.has(Permission.REPORTS_FINANCIAL_READ) and ctx.counter_id is not None:
            effective_counter = ctx.counter_id

        params: dict[str, Any] = {
            "business_id": ctx.business_id,
            "from_date": from_date,
            "to_date": to_date,
            "timezone": timezone,
            "counter_id": effective_counter,
        }

        # `(issued_at AT TIME ZONE :timezone)::date` is the business-local day boundary.
        totals = self._session.execute(
            text(
                """
                SELECT count(*) AS bill_count,
                       coalesce(sum(subtotal), 0)::numeric(14,2) AS gross,
                       coalesce(sum(discount_total), 0)::numeric(14,2) AS discount,
                       coalesce(sum(tax_total), 0)::numeric(14,2) AS tax,
                       coalesce(sum(total), 0)::numeric(14,2) AS net,
                       coalesce(sum(total) FILTER (WHERE is_credit), 0) AS credit_total,
                       coalesce(sum(total) FILTER (WHERE NOT is_credit), 0) AS cash_total,
                       count(*) FILTER (WHERE is_credit) AS credit_count,
                       coalesce(sum(amount_paid), 0)::numeric(14,2) AS collected
                  FROM invoices
                 WHERE business_id = :business_id
                   AND status <> 'cancelled'
                   AND (issued_at AT TIME ZONE :timezone)::date
                       BETWEEN :from_date AND :to_date
                   AND (CAST(:counter_id AS uuid) IS NULL OR counter_id = CAST(:counter_id AS uuid))
                """
            ),
            params,
        ).one()

        by_day = self._session.execute(
            text(
                """
                SELECT (issued_at AT TIME ZONE :timezone)::date AS day,
                       count(*) AS bill_count,
                       coalesce(sum(total), 0)::numeric(14,2) AS net,
                       coalesce(sum(total) FILTER (WHERE is_credit), 0) AS credit_total
                  FROM invoices
                 WHERE business_id = :business_id
                   AND status <> 'cancelled'
                   AND (issued_at AT TIME ZONE :timezone)::date
                       BETWEEN :from_date AND :to_date
                   AND (CAST(:counter_id AS uuid) IS NULL OR counter_id = CAST(:counter_id AS uuid))
                 GROUP BY day
                 ORDER BY day
                """
            ),
            params,
        ).all()

        by_method = self._session.execute(
            text(
                """
                SELECT p.method, coalesce(sum(a.amount), 0)::numeric(14,2) AS collected
                  FROM payment_allocations a
                  JOIN payments p ON p.id = a.payment_id
                  JOIN invoices i ON i.id = a.invoice_id
                 WHERE i.business_id = :business_id
                   AND i.status <> 'cancelled'
                   AND p.kind = 'payment'
                   AND (p.received_at AT TIME ZONE :timezone)::date
                       BETWEEN :from_date AND :to_date
                   AND (CAST(:counter_id AS uuid) IS NULL OR i.counter_id = CAST(:counter_id AS uuid))
                 GROUP BY p.method
                 ORDER BY p.method
                """
            ),
            params,
        ).all()

        # Counted, not hidden (FR-RPT-008).
        cancelled = self._session.execute(
            text(
                """
                SELECT count(*) AS cancelled_count,
                       coalesce(sum(total), 0)::numeric(14,2) AS cancelled_total
                  FROM invoices
                 WHERE business_id = :business_id
                   AND status = 'cancelled'
                   AND (issued_at AT TIME ZONE :timezone)::date
                       BETWEEN :from_date AND :to_date
                   AND (CAST(:counter_id AS uuid) IS NULL OR counter_id = CAST(:counter_id AS uuid))
                """
            ),
            params,
        ).one()

        return ReportEnvelope(
            as_of=self._clock.now().isoformat(),
            filters={
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "counter_id": str(effective_counter) if effective_counter else None,
                "timezone": timezone,
            },
            data={
                "bill_count": totals.bill_count,
                "gross": format(totals.gross, "f"),
                "discount": format(totals.discount, "f"),
                "tax": format(totals.tax, "f"),
                "net": format(totals.net, "f"),
                "cash_sales": format(totals.cash_total, "f"),
                # Credit sales are part of the day's trading (FR-RPT-001).
                "credit_sales": format(totals.credit_total, "f"),
                "credit_bill_count": totals.credit_count,
                "collected": format(totals.collected, "f"),
                "by_day": [
                    {
                        "date": r.day.isoformat(),
                        "bill_count": r.bill_count,
                        "net": format(r.net, "f"),
                        "credit_sales": format(r.credit_total, "f"),
                    }
                    for r in by_day
                ],
                "by_payment_method": [
                    {"method": r.method, "collected": format(r.collected, "f")} for r in by_method
                ],
            },
            excluded={
                "cancelled_bill_count": cancelled.cancelled_count,
                "cancelled_total": format(cancelled.cancelled_total, "f"),
                "reason": "cancelled bills are excluded from revenue and retained for audit",
            },
        )

    # --- credit outstanding ---------------------------------------------------

    def credit_outstanding(self, ctx: AuthContext) -> ReportEnvelope:
        """The other view of the same dataset. Must reconcile with `daily_sales`."""
        ctx.require(Permission.CREDIT_READ)
        # Business-local, because `due_date` is a business-local `DATE` (T3-02). With the
        # UTC date here, this report's `overdue` total disagreed with the ageing buckets
        # in `CreditService` for the first 5½ hours of every Asia/Kolkata day — two
        # views of the same rows, reconciling only after 05:30.
        today = business_date(self._clock.now(), self._business_timezone(ctx.business_id))

        row = self._session.execute(
            text(
                """
                SELECT count(*) AS open_count,
                       coalesce(sum(total), 0)::numeric(14,2) AS invoiced,
                       coalesce(sum(amount_paid), 0)::numeric(14,2) AS paid,
                       coalesce(sum(total - amount_paid), 0)::numeric(14,2) AS outstanding,
                       coalesce(sum(total - amount_paid) FILTER (
                           WHERE due_date < :today), 0) AS overdue,
                       count(DISTINCT customer_id) AS customers
                  FROM invoices
                 WHERE business_id = :business_id
                   AND is_credit
                   AND status <> 'cancelled'
                   AND payment_status IN ('unpaid','partially_paid')
                """
            ),
            {"business_id": ctx.business_id, "today": today},
        ).one()

        return ReportEnvelope(
            as_of=self._clock.now().isoformat(),
            filters={"as_of": today.isoformat()},
            data={
                "open_invoice_count": row.open_count,
                "total_invoiced": format(row.invoiced, "f"),
                "total_paid": format(row.paid, "f"),
                "outstanding": format(row.outstanding, "f"),
                "overdue": format(row.overdue, "f"),
                "customers_with_balance": row.customers,
            },
            excluded={
                "reason": "cancelled credit bills are excluded from outstanding",
            },
        )

    def reconciliation_check(self, ctx: AuthContext) -> dict[str, Any]:
        """Prove the two reports describe the same money.

        Credit sales minus what has been collected against them must equal the
        outstanding figure. A mismatch means `amount_paid` has drifted from the
        allocations, which is NFR-DATA-003 failing.
        """
        ctx.require(Permission.REPORTS_SALES_READ)
        row = self._session.execute(
            text(
                """
                SELECT coalesce(sum(total), 0)::numeric(14,2) AS credit_invoiced,
                       coalesce(sum(amount_paid), 0)::numeric(14,2) AS credit_collected,
                       coalesce(sum(total - amount_paid), 0)::numeric(14,2) AS credit_outstanding
                  FROM invoices
                 WHERE business_id = :business_id AND is_credit AND status <> 'cancelled'
                """
            ),
            {"business_id": ctx.business_id},
        ).one()

        allocation_total = self._session.execute(
            text(
                """
                SELECT coalesce(sum(CASE WHEN p.kind = 'payment' THEN a.amount
                                         ELSE -a.amount END), 0)::numeric(14,2)
                     + coalesce((SELECT sum(c.amount) FROM credit_note_allocations c
                                  JOIN invoices i2 ON i2.id = c.invoice_id
                                 WHERE i2.business_id = :business_id AND i2.is_credit), 0)
                  FROM payment_allocations a
                  JOIN payments p ON p.id = a.payment_id
                  JOIN invoices i ON i.id = a.invoice_id
                 WHERE i.business_id = :business_id AND i.is_credit
                   AND i.status <> 'cancelled'
                """
            ),
            {"business_id": ctx.business_id},
        ).scalar_one()

        reconciles = (
            row.credit_invoiced - row.credit_collected == row.credit_outstanding
            and row.credit_collected == allocation_total
        )
        return {
            "credit_invoiced": format(row.credit_invoiced, "f"),
            "credit_collected": format(row.credit_collected, "f"),
            "credit_outstanding": format(row.credit_outstanding, "f"),
            "allocation_total": format(allocation_total, "f"),
            "reconciles": reconciles,
        }

    # --- stock and item sales -------------------------------------------------

    def stock_position(self, ctx: AuthContext, *, low_stock_only: bool = False) -> ReportEnvelope:
        ctx.require(Permission.CATALOG_READ)
        rows = self._session.execute(
            text(
                """
                SELECT p.name, p.sku, u.code AS unit_code,
                       coalesce(b.quantity, 0) AS quantity, p.reorder_level
                  FROM products p
                  JOIN units u ON u.id = p.unit_id
                  LEFT JOIN inventory_balances b
                         ON b.product_id = p.id AND b.branch_id = :branch_id
                 WHERE p.business_id = :business_id AND p.is_active
                   AND (NOT :low_only
                        OR (p.reorder_level IS NOT NULL
                            AND coalesce(b.quantity, 0) <= p.reorder_level))
                 ORDER BY p.name
                 LIMIT 500
                """
            ),
            {
                "business_id": ctx.business_id,
                "branch_id": ctx.requires_branch,
                "low_only": low_stock_only,
            },
        ).all()

        return ReportEnvelope(
            as_of=self._clock.now().isoformat(),
            filters={"low_stock_only": low_stock_only},
            data={
                "items": [
                    {
                        "name": r.name,
                        "sku": r.sku,
                        "unit": r.unit_code,
                        "quantity": format(r.quantity, "f"),
                        "reorder_level": (
                            format(r.reorder_level, "f") if r.reorder_level is not None else None
                        ),
                    }
                    for r in rows
                ]
            },
            excluded={"reason": "inactive products are excluded"},
        )

    def item_sales(
        self,
        ctx: AuthContext,
        *,
        from_date: date,
        to_date: date,
        include_margin: bool = False,
    ) -> ReportEnvelope:
        ctx.require(Permission.REPORTS_FINANCIAL_READ)
        # Margin needs `catalog.cost.read` on top of the report permission, and the
        # column is **omitted** rather than nulled when absent.
        show_margin = include_margin and ctx.has(Permission.CATALOG_COST_READ)
        timezone = self._business_timezone(ctx.business_id)

        rows = self._session.execute(
            text(
                """
                SELECT it.product_name_snapshot AS name,
                       sum(it.quantity) AS quantity,
                       sum(it.line_total) AS revenue,
                       sum(coalesce(it.unit_cost_snapshot, 0) * it.quantity) AS cost
                  FROM invoice_items it
                  JOIN invoices i ON i.id = it.invoice_id
                 WHERE i.business_id = :business_id
                   AND i.status <> 'cancelled'
                   AND (i.issued_at AT TIME ZONE :timezone)::date
                       BETWEEN :from_date AND :to_date
                 GROUP BY it.product_name_snapshot
                 ORDER BY revenue DESC
                 LIMIT 200
                """
            ),
            {
                "business_id": ctx.business_id,
                "timezone": timezone,
                "from_date": from_date,
                "to_date": to_date,
            },
        ).all()

        items: list[dict[str, Any]] = []
        for r in rows:
            entry: dict[str, Any] = {
                "name": r.name,
                "quantity": format(r.quantity, "f"),
                "revenue": format(r.revenue, "f"),
            }
            if show_margin:
                entry["cost"] = format(r.cost, "f")
                entry["margin"] = format(r.revenue - r.cost, "f")
            items.append(entry)

        return ReportEnvelope(
            as_of=self._clock.now().isoformat(),
            filters={
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "include_margin": show_margin,
            },
            data={"items": items},
            excluded={"reason": "cancelled bills are excluded"},
        )

    def counter_reconciliation(
        self, ctx: AuthContext, *, on_date: date, counter_id: uuid.UUID | None = None
    ) -> ReportEnvelope:
        """Cash expected versus collected, per counter per day."""
        ctx.require(Permission.REPORTS_SALES_READ)
        timezone = self._business_timezone(ctx.business_id)
        effective = counter_id or ctx.counter_id

        rows = self._session.execute(
            text(
                """
                SELECT c.code AS counter_code,
                       coalesce(sum(i.total) FILTER (WHERE NOT i.is_credit), 0) AS cash_sales,
                       coalesce(sum(a.amount) FILTER (WHERE p.method = 'cash'), 0) AS cash_collected,
                       coalesce(sum(a.amount) FILTER (WHERE p.method <> 'cash'), 0) AS other_collected
                  FROM invoices i
                  JOIN counters c ON c.id = i.counter_id
                  LEFT JOIN payment_allocations a ON a.invoice_id = i.id
                  LEFT JOIN payments p ON p.id = a.payment_id AND p.kind = 'payment'
                 WHERE i.business_id = :business_id
                   AND i.status <> 'cancelled'
                   AND (i.issued_at AT TIME ZONE :timezone)::date = :on_date
                   AND (CAST(:counter_id AS uuid) IS NULL OR i.counter_id = CAST(:counter_id AS uuid))
                 GROUP BY c.code
                 ORDER BY c.code
                """
            ),
            {
                "business_id": ctx.business_id,
                "timezone": timezone,
                "on_date": on_date,
                "counter_id": effective,
            },
        ).all()

        return ReportEnvelope(
            as_of=self._clock.now().isoformat(),
            filters={
                "date": on_date.isoformat(),
                "counter_id": str(effective) if effective else None,
            },
            data={
                "counters": [
                    {
                        "counter": r.counter_code,
                        "cash_sales": format(r.cash_sales, "f"),
                        "cash_collected": format(r.cash_collected, "f"),
                        "other_collected": format(r.other_collected, "f"),
                        "difference": format(r.cash_sales - r.cash_collected, "f"),
                    }
                    for r in rows
                ]
            },
            excluded={"reason": "cancelled bills are excluded"},
        )

    # --- export ---------------------------------------------------------------

    def export_csv(self, rows: list[dict[str, Any]], *, columns: list[str]) -> str:
        """Stream a report as CSV with formula-injection escaping.

        A product named `=cmd()` must arrive in the accountant's spreadsheet as text
        (`test_csv_injection_escaped`).
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([escape_csv_value(row.get(column)) for column in columns])
        return buffer.getvalue()
