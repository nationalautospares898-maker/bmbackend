"""Sale finalisation — the critical endpoint (M10).

**One endpoint, one engine, for cash and credit** (FR-SALE-009, FR-CREDIT-008,
ADR-033). `sale_type` selects the behaviour; it does not select a code path. There is no
"prepare credit" endpoint and no second finalisation route, and a structural test
asserts the OpenAPI document contains neither.

`Idempotency-Key` is **required**. A missing key is `422 VALIDATION_ERROR` with
`fields: {"Idempotency-Key": "required"}` — never a silent pass, because an unkeyed
retry is how a duplicate invoice gets created.

A replay returns the stored response **verbatim** with `Idempotent-Replay: true`.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import Field, model_validator

from bm.api.deps import ClockDep, CurrentContext, IdempotencyKey, WriteSession, require
from bm.api.v1.schemas.common import MoneyStr, PercentStr, QuantityStr, StrictModel
from bm.api.v1.schemas.responses import FinalizeOut
from bm.api.v1.serialize import invoice_dict
from bm.domain.auth.permissions import Permission
from bm.domain.sales.print_payload import PrintPayloadService
from bm.domain.sales.reader import InvoiceReader
from bm.domain.sales.service import (
    CustomerSelection,
    FinalizeCommand,
    PaymentCommand,
    SaleHeader,
    SaleLineCommand,
    SalesService,
)
from bm.domain.shared.errors import ValidationFailed
from bm.domain.shared.money import ZERO, InvoiceDiscount

router = APIRouter(tags=["sales"])


# --- schemas -----------------------------------------------------------------


class CustomerSelectionIn(StrictModel):
    #: `none` (cash, free-text name only) | `existing` | `new`.
    mode: str = "none"
    #: For `new`, a **client-generated** id so the creation is idempotent and
    #: offline-safe (FR-CUST-003).
    customer_id: uuid.UUID | None = None
    #: For `new`, **only `name` is required** (FR-CUST-010).
    name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check_mode(self) -> CustomerSelectionIn:
        if self.mode not in {"none", "existing", "new"}:
            raise ValueError("mode must be none, existing or new")
        return self


class SaleHeaderIn(StrictModel):
    """Optional for **both** sale types, and persisted when present (FR-CREDIT-012)."""

    customer_name: str | None = Field(default=None, max_length=200)
    customer_business_name: str | None = Field(default=None, max_length=200)
    vehicle_number: str | None = Field(default=None, max_length=32)
    mechanic_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None


class SaleLineIn(StrictModel):
    product_id: uuid.UUID
    quantity: QuantityStr
    unit_price: MoneyStr
    price_source: str = "catalog"
    discount_percent: PercentStr | None = None


class InvoiceDiscountIn(StrictModel):
    percent: PercentStr | None = None
    amount: MoneyStr | None = None


class PaymentIn(StrictModel):
    method: str
    amount: MoneyStr
    reference: str | None = Field(default=None, max_length=64)


class OversellOverrideIn(StrictModel):
    reason: str = Field(min_length=10, max_length=500)


class FinalizeRequest(StrictModel):
    counter_id: uuid.UUID
    sale_type: str
    lines: list[SaleLineIn] = Field(min_length=1)
    draft_id: uuid.UUID | None = None
    draft_version: int | None = None
    customer: CustomerSelectionIn = Field(default_factory=CustomerSelectionIn)
    header: SaleHeaderIn = Field(default_factory=SaleHeaderIn)
    invoice_discount: InvoiceDiscountIn | None = None
    #: Optional for `credit` and **not requested at save time** (FR-CREDIT-025);
    #: required for `cash` and must cover the total.
    payment: PaymentIn | None = None
    #: A **cross-check only** (FR-SALE-003). Never a value to trust.
    client_computed_total: MoneyStr | None = None
    accept_price_changes: bool = False
    oversell_override: OversellOverrideIn | None = None

    @model_validator(mode="after")
    def _check_sale_type(self) -> FinalizeRequest:
        if self.sale_type not in {"cash", "credit"}:
            raise ValueError("sale_type must be cash or credit")
        return self


# --- route -------------------------------------------------------------------


@router.post(
    "/sales:finalize",
    status_code=status.HTTP_201_CREATED,
    responses={201: {"model": FinalizeOut}},
)
def finalize_sale(
    body: FinalizeRequest,
    response: Response,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.BILLING_SALE_FINALIZE))],
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    """Atomically finalise a cash or credit sale.

    Everything between BEGIN and COMMIT either all happens or none of it does
    (FR-SALE-001): the invoice, its item snapshots, the stock movements, any payment, the
    draft's finalised state, the audit row and the outbox event. Stock is deducted
    **exactly once**.
    """
    service = SalesService(session, clock)

    # `cash` requires a payment that covers the total; `credit` does not require one at
    # all (FR-CREDIT-025). Checked here because it is a contract rule about the request,
    # not a rule about money.
    if body.sale_type == "cash" and body.payment is None:
        raise ValidationFailed({"payment": "A cash bill needs a payment."})

    command = FinalizeCommand(
        draft_id=body.draft_id,
        draft_version=body.draft_version,
        counter_id=body.counter_id,
        sale_type=body.sale_type,
        customer=CustomerSelection(
            mode=body.customer.mode,
            customer_id=body.customer.customer_id,
            name=body.customer.name,
        ),
        header=SaleHeader(
            customer_name=body.header.customer_name,
            customer_business_name=body.header.customer_business_name,
            vehicle_number=body.header.vehicle_number,
            mechanic_name=body.header.mechanic_name,
            phone=body.header.phone,
            notes=body.header.notes,
            due_date=body.header.due_date,
        ),
        lines=[
            SaleLineCommand(
                product_id=line.product_id,
                quantity=line.quantity,
                unit_price=line.unit_price,
                price_source=line.price_source,
                discount_percent=line.discount_percent or ZERO,
            )
            for line in body.lines
        ],
        invoice_discount=InvoiceDiscount(
            percent=(body.invoice_discount.percent or ZERO) if body.invoice_discount else ZERO,
            amount=(body.invoice_discount.amount or ZERO) if body.invoice_discount else ZERO,
        ),
        payment=(
            PaymentCommand(
                method=body.payment.method,
                amount=body.payment.amount,
                reference=body.payment.reference,
            )
            if body.payment
            else None
        ),
        client_computed_total=body.client_computed_total,
        accept_price_changes=body.accept_price_changes,
        oversell_reason=body.oversell_override.reason if body.oversell_override else None,
    )

    result = service.finalize(
        ctx,
        command,
        idempotency_key=idempotency_key,
        # The raw body drives the canonical fingerprint, so an equivalent retry replays
        # (`Blueprint §5.2.3`).
        raw_body=body.model_dump(mode="json"),
    )

    if result.replayed:
        response.headers["Idempotent-Replay"] = "true"
        # The stored response, byte-for-byte (`§5.2.2`). It was serialised from this
        # same route, so it is always a JSON object.
        stored = result.replayed_body
        return stored if isinstance(stored, dict) else {}

    payload = _invoice_response(ctx, session, result)
    # Stored in the **same transaction** that commits the sale, so a replay can never
    # describe work that did not commit (`§5.2.8` rule 3).
    service.store_idempotent_response(
        idempotency_key,
        business_id=ctx.business_id,
        body=payload,
        invoice_id=result.invoice_id,
    )
    return payload


def _invoice_response(ctx: Any, session: Any, result: Any) -> dict[str, Any]:
    """Build the `201` body.

    The invoice is serialised by `serialize.invoice_dict`, the **same** function
    `GET /invoices/{id}` uses, read back through `InvoiceReader.record_of`. That is
    deliberate: the confirmation card the cashier sees and the row they later open in
    Daily Bills describe one bill, and two serialisers for one resource is how those
    two views start disagreeing.

    `record_of` skips the `reports.sales.read` check on purpose — this caller holds
    `billing.sale.finalize` and has just created the row. Refusing here would mean a
    `403` *after* the sale committed.

    The internal reference appears on the `invoice` resource for audit and support. It
    is **not** in `print_payload` (OD-3, T3-06).
    """
    record = InvoiceReader(session).record_of(ctx.business_id, result.invoice_id)
    print_payload = PrintPayloadService(session).build(ctx, result.invoice_id)

    return {
        "invoice": invoice_dict(record, may_see_cost=ctx.has(Permission.CATALOG_COST_READ)),
        "print_payload": print_payload.as_dict(),
        "warnings": result.warnings,
    }
