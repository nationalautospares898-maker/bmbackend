"""Report endpoints (M13).

Every response carries `as_of` and its `filters`, and excludes cancelled invoices from
revenue while still counting them in an `excluded` block (FR-RPT-008).

Day boundaries use **business-local time** (T3-02), so a sale at 23:59:59 and one at
00:00:00 land on different days.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from bm.api.deps import ClockDep, CurrentContext, ReadSession, require
from bm.api.v1.schemas.responses import DailySalesOut, ReportOut
from bm.domain.auth.permissions import Permission
from bm.domain.reports.service import ReportEnvelope, ReportService
from bm.domain.shared.clock import Clock, business_date
from bm.domain.shared.errors import NotFound, ValidationFailed

router = APIRouter(prefix="/reports", tags=["reports"])


def _today(clock: Clock, ctx: CurrentContext) -> date:
    """Today, in the **business's** timezone.

    This module's own docstring promises that day boundaries use business-local time
    (T3-02), and the report SQL keeps that promise — it groups on
    `(issued_at AT TIME ZONE :timezone)::date`. The *default date* did not: every
    endpoint below passed `clock.now().date()`, which is the **UTC** date, because
    `SystemClock` is UTC by design.

    For `Asia/Kolkata` (UTC+5:30) the two disagree for the first five and a half hours
    of every business day. Between 00:00 and 05:30 local, a report asked for "today"
    defaulted to *yesterday's* business date and came back empty — so an owner cashing up
    after midnight saw no bills for the ones they had just rung through, and the
    `daily-sales`/`credit-outstanding` reconciliation reported zero against a non-zero
    credit balance. Reproduced at 2026-10-31 00:22 IST, which is what made two
    integration tests fail that had passed earlier the same day.

    `business_date` already existed for exactly this, and is documented for exactly this;
    it simply was not called here.
    """
    return business_date(clock.now(), ctx.business_timezone)


def _envelope(report: ReportEnvelope) -> dict[str, Any]:
    return {
        "as_of": report.as_of,
        "filters": report.filters,
        **report.data,
        "excluded": report.excluded,
    }


def _date_range(
    on_date: date | None, from_date: date | None, to_date: date | None, today: date
) -> tuple[date, date]:
    if on_date is not None:
        return on_date, on_date
    if from_date is not None and to_date is not None:
        return from_date, to_date
    if from_date is not None:
        return from_date, from_date
    return today, today


@router.get("/daily-sales", responses={200: {"model": DailySalesOut}})
def daily_sales(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.REPORTS_SALES_READ))],
    report_date: Annotated[date | None, Query(alias="date")] = None,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    counter_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Counts, gross, discount, tax, net, and a breakdown by payment method.

    **Credit sales are included**, on their sale date (FR-RPT-001) — a credit sale is a
    sale, and excluding it would understate the day's trading.
    """
    start, end = _date_range(report_date, from_date, to_date, _today(clock, ctx))
    return _envelope(
        ReportService(session, clock).daily_sales(
            ctx, from_date=start, to_date=end, counter_id=counter_id
        )
    )


@router.get("/credit-outstanding", responses={200: {"model": ReportOut}})
def credit_outstanding_report(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CREDIT_READ))],
) -> dict[str, Any]:
    """The other view of the same dataset as `daily-sales`. The two must reconcile
    exactly (FR-RPT-003)."""
    return _envelope(ReportService(session, clock).credit_outstanding(ctx))


@router.get("/reconciliation-check")
def reconciliation_check(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.REPORTS_SALES_READ))],
) -> dict[str, Any]:
    """Proves daily sales and credit outstanding describe the same money.

    A `reconciles: false` means `amount_paid` has drifted from the allocations, which is
    NFR-DATA-003 failing — it is reported, never auto-corrected.
    """
    return ReportService(session, clock).reconciliation_check(ctx)


@router.get("/stock-position", responses={200: {"model": ReportOut}})
def stock_position(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
    low_stock_only: bool = False,
) -> dict[str, Any]:
    return _envelope(
        ReportService(session, clock).stock_position(ctx, low_stock_only=low_stock_only)
    )


@router.get("/item-sales", responses={200: {"model": ReportOut}})
def item_sales(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.REPORTS_FINANCIAL_READ))],
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    include_margin: bool = False,
) -> dict[str, Any]:
    """Margin requires `catalog.cost.read` on top of this report's permission; the
    columns are **omitted** rather than nulled when it is absent."""
    start, end = _date_range(None, from_date, to_date, _today(clock, ctx))
    return _envelope(
        ReportService(session, clock).item_sales(
            ctx, from_date=start, to_date=end, include_margin=include_margin
        )
    )


@router.get("/counter-reconciliation", responses={200: {"model": ReportOut}})
def counter_reconciliation(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.REPORTS_SALES_READ))],
    report_date: Annotated[date | None, Query(alias="date")] = None,
    counter_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Cash expected versus collected, per counter per day."""
    on_date = report_date or _today(clock, ctx)
    return _envelope(
        ReportService(session, clock).counter_reconciliation(
            ctx, on_date=on_date, counter_id=counter_id
        )
    )


@router.get("/{name}/export")
def export_report(
    name: str,
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.REPORTS_SALES_READ))],
    export_format: Annotated[str, Query(alias="format")] = "csv",
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> Response:
    """Streaming CSV (FR-RPT-007).

    Leading `=`, `+`, `-` and `@` are escaped, so a product named `=cmd()` arrives in
    the accountant's spreadsheet as text rather than a formula.
    """
    if export_format != "csv":
        raise ValidationFailed({"format": "Only `csv` is supported."})

    service = ReportService(session, clock)
    start, end = _date_range(None, from_date, to_date, _today(clock, ctx))

    if name == "daily-sales":
        report = service.daily_sales(ctx, from_date=start, to_date=end)
        rows = report.data["by_day"]
        columns = ["date", "bill_count", "net", "credit_sales"]
    elif name == "stock-position":
        report = service.stock_position(ctx)
        rows = report.data["items"]
        columns = ["name", "sku", "unit", "quantity", "reorder_level"]
    elif name == "item-sales":
        ctx.require(Permission.REPORTS_FINANCIAL_READ)
        report = service.item_sales(ctx, from_date=start, to_date=end)
        rows = report.data["items"]
        columns = ["name", "quantity", "revenue"]
    else:
        # Only the reports in FR-RPT-001..008 / FR-RPT-020..033 exist. A new report is
        # a specification change, not an export parameter.
        raise NotFound(f"`{name}` is not an exportable report.")

    body = service.export_csv(rows, columns=columns)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )
