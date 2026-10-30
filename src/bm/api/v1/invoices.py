"""Invoice read, print payload and cancellation (M12).

**There is deliberately no `PUT`/`PATCH`/`DELETE` on invoices or invoice items**
(FR-SALE-010, ADR-015). A correction is a cancellation, a return, a credit note or a
reversal — all of which add a record rather than rewriting one.

`GET /invoices/{id}/print-payload` is a plain read. **Reprinting creates nothing** — no
sale, no number, no movement — and a failure to log the print event never blocks the
print itself.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from bm.api.deps import (
    ClockDep,
    CurrentContext,
    IdempotencyKey,
    ReadSession,
    WriteSession,
    require,
)
from bm.api.v1.schemas.common import StrictModel
from bm.api.v1.schemas.responses import CancelOut, InvoiceListOut, InvoiceOut, PrintPayloadOut
from bm.api.v1.serialize import invoice_dict
from bm.domain.auth.permissions import Permission
from bm.domain.sales.cancellation import CancellationService
from bm.domain.sales.print_payload import PrintPayloadService
from bm.domain.sales.reader import InvoiceReader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])


class CancelRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=500)


class PrintEventRequest(StrictModel):
    is_duplicate: bool = False


@router.get("", responses={200: {"model": InvoiceListOut}})
def list_invoices(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.REPORTS_SALES_READ))],
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    payment_status: str | None = None,
    customer_id: uuid.UUID | None = None,
    counter_id: uuid.UUID | None = None,
    is_credit: bool | None = None,
    idempotency_key: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Backs Daily Bills **and** Credit Bills — one dataset, two views (FR-RPT-003)."""
    rows = InvoiceReader(session).list_invoices(
        ctx,
        from_date=from_date,
        to_date=to_date,
        payment_status=payment_status,
        customer_id=customer_id,
        counter_id=counter_id,
        is_credit=is_credit,
        idempotency_key=idempotency_key,
        limit=limit,
    )
    return {
        "items": [
            {
                "id": str(r.id),
                "internal_reference": r.internal_reference,
                "issued_at": r.issued_at.isoformat(),
                "status": r.status,
                "is_credit": r.is_credit,
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "customer_id": str(r.customer_id) if r.customer_id else None,
                "customer_name": r.customer_name,
                "counter": r.counter_code,
                "total": format(r.total, "f"),
                "amount_paid": format(r.amount_paid, "f"),
                "outstanding": format(r.outstanding, "f"),
                "payment_status": r.payment_status,
            }
            for r in rows
        ],
        "page": {"next_cursor": None, "has_more": len(rows) == limit, "limit": limit},
        "as_of": datetime.now(UTC).isoformat(),
    }


@router.get("/{invoice_id}", responses={200: {"model": InvoiceOut}})
def get_invoice(
    invoice_id: uuid.UUID,
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.REPORTS_SALES_READ))],
) -> dict[str, Any]:
    record = InvoiceReader(session).get(ctx, invoice_id)
    return invoice_dict(record, may_see_cost=ctx.has(Permission.CATALOG_COST_READ))


@router.get("/{invoice_id}/print-payload", responses={200: {"model": PrintPayloadOut}})
def get_print_payload(
    invoice_id: uuid.UUID,
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.REPORTS_SALES_READ))],
    copy: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Everything needed to render the document (FR-PRINT-001).

    **A plain read.** It never calls a finalisation endpoint and creates nothing — no
    sale, no number, no movement.
    """
    service = PrintPayloadService(session)
    is_duplicate = copy == "reprint" and service.reprint_is_duplicate(ctx, invoice_id)
    return service.build(ctx, invoice_id, is_duplicate=is_duplicate).as_dict()


@router.post("/{invoice_id}/print-events", status_code=status.HTTP_204_NO_CONTENT)
def record_print_event(
    invoice_id: uuid.UUID,
    body: PrintEventRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.REPORTS_SALES_READ))],
) -> None:
    """Records a print attempt (FR-PRINT-004).

    A logging failure **never blocks printing**: a customer waiting for their bill is
    not a good moment to fail on an audit nicety, so the error is logged server-side and
    the route still succeeds.
    """
    try:
        PrintPayloadService(session).log_print_event(
            ctx, invoice_id, is_duplicate=body.is_duplicate
        )
    except Exception:  # noqa: BLE001 - deliberate: logging must never block printing
        logger.warning("print_event_log_failed", extra={"invoice_id": str(invoice_id)})


@router.post("/{invoice_id}:cancel", responses={200: {"model": CancelOut}})
def cancel_invoice(
    invoice_id: uuid.UUID,
    body: CancelRequest,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.BILLING_SALE_CANCEL))],
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    """Reverses stock exactly, issues a full-value credit note for a credit sale,
    **retains** the internal reference and keeps the record listed."""
    result = CancellationService(session, clock).cancel(ctx, invoice_id, reason=body.reason)
    return {
        "invoice_id": str(result.invoice_id),
        "credit_note_id": str(result.credit_note_id) if result.credit_note_id else None,
        "movements_reversed": result.movements_reversed,
        "status": "cancelled",
    }
