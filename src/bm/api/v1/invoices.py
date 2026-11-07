"""Invoice read, print payload, cancellation and in-place editing (M12, M28).

**There is deliberately no `DELETE` on invoices or invoice items.** An invoice is never
removed — it is cancelled, returned against, or credit-noted (ADR-015), and Decision C
(2026-11-07) preserves all three.

`POST /invoices/{id}:amend` **does** edit a finalised bill, which this module forbade
until the owner reversed OD-10 on 2026-11-07 (Decision A). It keeps the invoice's
identity and writes the complete before-state to `invoice_revisions` first; see
`domain/sales/amend.py` for what that buys and what it still refuses. `GET
/invoices/{id}/revisions` reads that history back.

`GET /invoices/{id}/print-payload` is a plain read. **Reprinting creates nothing** — no
sale, no number, no movement — and a failure to log the print event never blocks the
print itself.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from bm.api.deps import (
    ClockDep,
    CurrentContext,
    IdempotencyKey,
    ReadSession,
    WriteSession,
    require,
    require_any,
)
from bm.api.v1.schemas.common import MoneyStr, PercentStr, QuantityStr, StrictModel
from bm.api.v1.schemas.responses import (
    AmendOut,
    CancelOut,
    InvoiceListOut,
    InvoiceOut,
    InvoiceRevisionsOut,
    PrintPayloadOut,
)
from bm.api.v1.serialize import invoice_dict
from bm.domain.auth.permissions import Permission
from bm.domain.sales.amend import (
    AmendCommand,
    AmendHeader,
    AmendLine,
    InvoiceAmendService,
)
from bm.domain.sales.cancellation import CancellationService
from bm.domain.sales.print_payload import PrintPayloadService
from bm.domain.sales.reader import InvoiceReader
from bm.domain.shared.money import ZERO, parse_decimal
from bm.domain.sync.idempotency import IdempotencyService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])


class CancelRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=500)


class PrintEventRequest(StrictModel):
    is_duplicate: bool = False


class AmendLineIn(StrictModel):
    product_id: uuid.UUID
    quantity: QuantityStr
    unit_price: MoneyStr
    discount_percent: PercentStr | None = None


class AmendHeaderIn(StrictModel):
    """Header fields an edit may change.

    Every field is optional, and **omitting one leaves it alone** — `model_fields_set` is
    what distinguishes "not supplied" from "clear it", so sending `null` deliberately
    blanks a field while sending nothing keeps it.
    """

    customer_name: str | None = Field(default=None, max_length=200)
    customer_business_name: str | None = Field(default=None, max_length=200)
    vehicle_number: str | None = Field(default=None, max_length=32)
    mechanic_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None


class AmendRequest(StrictModel):
    #: The version the editor read. A mismatch is a stale edit and is refused.
    expected_version: int = Field(ge=1)
    #: Mandatory. The database `CHECK` enforces a floor too, so a short reason is a
    #: field error here rather than an integrity error there.
    reason: str = Field(min_length=5, max_length=500)
    lines: list[AmendLineIn] = Field(min_length=1)
    header: AmendHeaderIn | None = None
    oversell_reason: str | None = Field(default=None, min_length=10, max_length=500)


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
    ctx: Annotated[
        CurrentContext,
        Depends(require_any(Permission.REPORTS_SALES_READ, Permission.CREDIT_READ)),
    ],
) -> dict[str, Any]:
    """One bill with its lines.

    `credit.read` is accepted as well as `reports.sales.read` because this is how the
    Credit Bills folder shows what a customer actually bought on an invoice. Every
    seeded role holding `credit.read` happens to hold `reports.sales.read` too, so this
    changes nothing for them — it stops a *custom* credit-only role from seeing a
    customer's bill list it may read and then a 403 on every bill in it.

    No widening of what is returned: the cost snapshot still needs `catalog.cost.read`
    and is omitted, not nulled (FR-CAT-004), and `InvoiceReader.get` predicates on the
    caller's `business_id` so a cross-tenant id is a 404.
    """
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


@router.post("/{invoice_id}:amend", responses={200: {"model": AmendOut}})
def amend_invoice(
    invoice_id: uuid.UUID,
    body: AmendRequest,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.BILLING_SALE_EDIT))],
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    """Edit a finalised bill **in place** (owner Decision A, 2026-11-07).

    Keeps the invoice's id and internal reference, writes the complete before-state to
    `invoice_revisions`, recomputes the money server-side, reconciles stock with new
    append-only movements, and never touches a payment.

    `Idempotency-Key` is required, so a retried save applies once rather than writing a
    second revision and a second set of stock movements.

    Refuses, with its own error code each time: a stale `expected_version`
    (`409 INVOICE_VERSION_CONFLICT`), a cancelled bill (`409 INVOICE_NOT_EDITABLE`), and
    a new total below what has already been paid (`409 EDIT_BELOW_PAID_TOTAL` — the
    owner's explicit rule; the correction there is a return or a credit note).
    """
    from bm.domain.settings.service import SettingsService

    # The key is **claimed**, not merely required. Requiring the header and ignoring it
    # is worse than not asking for it: the caller is told the retry is safe while the
    # second attempt writes a second revision and a second set of stock movements — and
    # because `stock_movements` can never be deleted, the only correction left would be a
    # third, opposite movement. Found by a test that retried a save and got
    # `INVOICE_VERSION_CONFLICT`, which is what re-execution looks like from outside.
    #
    # `claim` runs before the invoice is locked, and `store_response` commits in the same
    # transaction as the edit, so a replay returns the first outcome verbatim.
    idempotency = IdempotencyService(session)
    claim = idempotency.claim(
        business_id=ctx.business_id,
        endpoint="POST /invoices/{invoice_id}:amend",
        key=idempotency_key,
        body={"invoice_id": str(invoice_id), **body.model_dump(mode="json")},
        user_id=ctx.user_id,
        device_id=ctx.device_id,
    )
    if claim.replayed:
        return cast("dict[str, Any]", claim.stored_body)

    policy = SettingsService(session).money_policy(ctx.business_id, ctx.branch_id)
    header = body.header or AmendHeaderIn()
    result = InvoiceAmendService(session, clock).amend(
        ctx,
        AmendCommand(
            invoice_id=invoice_id,
            expected_version=body.expected_version,
            reason=body.reason,
            lines=[
                AmendLine(
                    product_id=line.product_id,
                    quantity=parse_decimal(line.quantity, field_name="quantity"),
                    unit_price=parse_decimal(line.unit_price, field_name="unit_price"),
                    discount_percent=(
                        parse_decimal(line.discount_percent, field_name="discount_percent")
                        if line.discount_percent is not None
                        else ZERO
                    ),
                )
                for line in body.lines
            ],
            header=AmendHeader(
                customer_name=header.customer_name,
                customer_business_name=header.customer_business_name,
                vehicle_number=header.vehicle_number,
                mechanic_name=header.mechanic_name,
                phone=header.phone,
                notes=header.notes,
                due_date=header.due_date,
                # Only the keys the client actually sent, so omitting a field leaves it
                # alone and sending `null` clears it.
                supplied=frozenset(header.model_fields_set),
            ),
            oversell_reason=body.oversell_reason,
        ),
        policy=policy,
    )
    payload = {
        "invoice_id": str(result.invoice_id),
        "revision_number": result.revision_number,
        "version": result.version,
        "total": format(result.total, "f"),
        "amount_paid": format(result.amount_paid, "f"),
        "outstanding": format(result.outstanding, "f"),
        "payment_status": result.payment_status,
    }
    idempotency.store_response(claim, status_code=200, body=payload)
    return payload


def _clock() -> Any:
    """A read-only route takes no `ClockDep`; the reader needs one anyway."""
    from bm.domain.shared.clock import SystemClock

    return SystemClock()


@router.get("/{invoice_id}/revisions", responses={200: {"model": InvoiceRevisionsOut}})
def invoice_revisions(
    invoice_id: uuid.UUID,
    session: ReadSession,
    ctx: Annotated[
        CurrentContext,
        # The same pair `GET /invoices/{id}` accepts, so a credit-only role that may read
        # a bill may also read why it changed.
        Depends(require_any(Permission.REPORTS_SALES_READ, Permission.CREDIT_READ)),
    ],
) -> dict[str, Any]:
    """The edit history of one bill, newest first, with each before-state's lines."""
    return {
        "items": InvoiceAmendService(session, _clock()).revisions(ctx, invoice_id),
    }
