"""Credit and payment endpoints (M11).

**There is no endpoint, anywhere, that sets `payment_status` or `amount_paid`** — they
are derived (FR-CREDIT-003, ADR-022). There is likewise no "mark as paid" endpoint: full
payment is reached only by recording payments whose allocations sum to the total.

**Recording a payment never moves stock** (FR-CREDIT-010).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from bm.api.deps import (
    ClockDep,
    CurrentContext,
    IdempotencyKey,
    ReadSession,
    WriteSession,
    require,
)
from bm.api.v1.schemas.common import MoneyStr, StrictModel
from bm.api.v1.schemas.responses import CreditCustomersOut, PaymentOut, ReversalOut
from bm.domain.auth.permissions import Permission
from bm.domain.credit.service import AllocationTarget, CreditService

router = APIRouter(tags=["credit"])


class AllocationIn(StrictModel):
    invoice_id: uuid.UUID
    amount: MoneyStr


class AllocationSpec(StrictModel):
    #: `oldest_first` | `invoice_specific` | `manual`.
    strategy: str = "oldest_first"
    allocations: list[AllocationIn] | None = None


class PaymentRequest(StrictModel):
    customer_id: uuid.UUID
    amount: MoneyStr
    method: str
    reference: str | None = Field(default=None, max_length=64)
    received_at: datetime | None = None
    allocation: AllocationSpec = Field(default_factory=AllocationSpec)
    notes: str | None = Field(default=None, max_length=2000)


class ReverseRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=500)


class WriteOffRequest(StrictModel):
    customer_id: uuid.UUID
    amount: MoneyStr
    #: A write-off removes money the shop is owed, so the explanation floor is higher.
    reason: str = Field(min_length=10, max_length=500)


class CreditNoteAllocationRequest(StrictModel):
    allocations: list[AllocationIn] = Field(min_length=1)


# --- payments ----------------------------------------------------------------


@router.post("/payments", responses={200: {"model": PaymentOut}})
def record_payment(
    body: PaymentRequest,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CREDIT_PAYMENT_RECORD))],
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    """Writes payment and allocation rows **only**.

    `Idempotency-Key` is required (FR-PAY-004), so a retried submission records one
    payment rather than two.
    """
    result = CreditService(session, clock).record_payment(
        ctx,
        customer_id=body.customer_id,
        amount=body.amount,
        method=body.method,
        strategy=body.allocation.strategy,
        reference=body.reference,
        allocations=[
            AllocationTarget(invoice_id=a.invoice_id, amount=a.amount)
            for a in (body.allocation.allocations or [])
        ],
        notes=body.notes,
        received_at=body.received_at,
    )
    return {
        "payment_id": str(result.payment_id),
        "allocations": [
            {"invoice_id": str(a.invoice_id), "amount": format(a.amount, "f")}
            for a in result.allocated
        ],
        # Any remainder becomes on-account credit, allocatable later (FR-PAY-003) —
        # never silently discarded.
        "on_account": format(result.on_account, "f"),
    }


@router.get("/payments")
def list_payments(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CREDIT_READ))],
    invoice_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    return {
        "items": CreditService(session, _clock()).payments_for(
            ctx, invoice_id=invoice_id, customer_id=customer_id, limit=limit
        )
    }


@router.post("/payments/{payment_id}:reverse", responses={200: {"model": ReversalOut}})
def reverse_payment(
    payment_id: uuid.UUID,
    body: ReverseRequest,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CREDIT_PAYMENT_RECORD))],
    idempotency_key: IdempotencyKey,
) -> dict[str, str]:
    """Creates a reversal row. The original is **never** edited (FR-PAY-005)."""
    reversal_id = CreditService(session, clock).reverse_payment(ctx, payment_id, reason=body.reason)
    return {"reversal_payment_id": str(reversal_id)}


# --- credit views -------------------------------------------------------------


@router.get("/credit/outstanding")
def credit_outstanding(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CREDIT_READ))],
    customer_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> dict[str, Any]:
    """Derived from authoritative data (FR-CREDIT-002), never from a cache table."""
    rows = CreditService(session, clock).outstanding(ctx, customer_id=customer_id, limit=limit)
    return {
        "items": [
            {
                "invoice_id": str(r.invoice_id),
                "internal_reference": r.internal_reference,
                "issued_at": r.issued_at.isoformat(),
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "total": format(r.total, "f"),
                "amount_paid": format(r.amount_paid, "f"),
                "outstanding": format(r.outstanding, "f"),
                "payment_status": r.payment_status,
                "days_overdue": r.days_overdue,
            }
            for r in rows
        ],
        "as_of": clock.now().isoformat(),
    }


@router.get("/credit/ageing-summary")
def ageing_summary(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CREDIT_READ))],
) -> dict[str, Any]:
    """Buckets: current, 1-30, 31-60, 61-90, 90+."""
    return {
        "buckets": CreditService(session, clock).ageing_summary(ctx),
        "as_of": clock.now().isoformat(),
    }


@router.get("/credit/customers", responses={200: {"model": CreditCustomersOut}})
def credit_customers(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CREDIT_READ))],
    include_settled: bool = False,
    query: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict[str, Any]:
    """The top level of the Credit Bills view (FR-CREDIT-011, FR-RPT-028).

    Every aggregate is **server-computed** — the client never sums these as
    authoritative (FR-RPT-030, ADR-032). Grouping keys on `customer_id`, so a rename
    changes only the label (FR-RPT-029).
    """
    items, summary = CreditService(session, clock).customers_with_credit(
        ctx, include_settled=include_settled, query=query, limit=limit
    )
    return {
        "items": [
            {
                "customer": {
                    "id": str(c.customer_id),
                    "name": c.name,
                    "primary_phone": c.primary_phone,
                },
                "invoice_count": c.invoice_count,
                "open_invoice_count": c.open_invoice_count,
                "total_invoiced": format(c.total_invoiced, "f"),
                "total_paid": format(c.total_paid, "f"),
                "outstanding": format(c.outstanding, "f"),
                "oldest_due_date": (c.oldest_due_date.isoformat() if c.oldest_due_date else None),
                "days_overdue": c.days_overdue,
                "ageing": {k: format(v, "f") for k, v in c.ageing.items()},
            }
            for c in items
        ],
        "summary": summary,
        "page": {"next_cursor": None, "has_more": len(items) == limit, "limit": limit},
        "as_of": clock.now().isoformat(),
    }


@router.post("/credit/write-off")
def write_off(
    body: WriteOffRequest,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CREDIT_WRITEOFF))],
) -> dict[str, str]:
    """Owner-only. Produces a credit note carrying the reason."""
    note_id = CreditService(session, clock).write_off(
        ctx, customer_id=body.customer_id, amount=body.amount, reason=body.reason
    )
    return {"credit_note_id": str(note_id)}


@router.post("/credit-notes/{note_id}/allocations")
def allocate_credit_note(
    note_id: uuid.UUID,
    body: CreditNoteAllocationRequest,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CREDIT_PAYMENT_RECORD))],
) -> dict[str, str]:
    CreditService(session, clock).allocate_credit_note(
        ctx,
        note_id,
        [AllocationTarget(invoice_id=a.invoice_id, amount=a.amount) for a in body.allocations],
    )
    return {"credit_note_id": str(note_id), "status": "allocated"}


def _clock() -> Any:
    from bm.domain.shared.clock import SystemClock

    return SystemClock()
