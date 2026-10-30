"""Supplier and purchase endpoints (M15), plus returns (M24).

`POST /purchases` creates a `draft` — **no stock change**. `POST /purchases/{id}:confirm`
is **the only path that receives stock** (FR-PUR-003), and it is idempotent, so a
retried confirm cannot receive the same goods twice.
"""

from __future__ import annotations

import uuid
from datetime import date
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
    require_any,
)
from bm.api.v1.schemas.common import MoneyStr, QuantityStr, StrictModel
from bm.domain.auth.permissions import Permission
from bm.domain.purchases.service import PurchaseLine, PurchaseService
from bm.domain.returns.service import ReturnLine, ReturnService

router = APIRouter(tags=["purchases"])


# --- schemas -----------------------------------------------------------------


class SupplierUpsertRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    business_name: str | None = Field(default=None, max_length=200)
    tax_identifier: str | None = Field(default=None, max_length=32)


class PurchaseLineIn(StrictModel):
    product_id: uuid.UUID
    quantity: QuantityStr
    unit_cost: MoneyStr


class PurchaseCreateRequest(StrictModel):
    supplier_id: uuid.UUID
    items: list[PurchaseLineIn] = Field(min_length=1)
    supplier_document_number: str | None = Field(default=None, max_length=64)
    document_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ReturnLineIn(StrictModel):
    invoice_item_id: uuid.UUID
    quantity: QuantityStr
    #: `resalable` goes back into sellable stock; `damaged` becomes scrap.
    condition: str = "resalable"


class ReturnCreateRequest(StrictModel):
    invoice_id: uuid.UUID
    items: list[ReturnLineIn] = Field(min_length=1)
    reason: str = Field(min_length=3, max_length=500)
    restock: bool = True
    #: **Explicit, never inferred** (FR-RET-003).
    credit_handling: str = "allocate_to_invoice"


# --- suppliers ----------------------------------------------------------------


@router.get("/suppliers")
def list_suppliers(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.INVENTORY_RECEIVE))],
) -> dict[str, Any]:
    return {
        "items": [
            {"id": str(i), "name": n, "phone": p}
            for i, n, p in PurchaseService(session, _clock()).list_suppliers(ctx)
        ]
    }


@router.put("/suppliers/{supplier_id}")
def upsert_supplier(
    supplier_id: uuid.UUID,
    body: SupplierUpsertRequest,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_WRITE))],
) -> dict[str, str]:
    resolved = PurchaseService(session, clock).upsert_supplier(
        ctx,
        supplier_id,
        name=body.name,
        phone=body.phone,
        business_name=body.business_name,
        tax_identifier=body.tax_identifier,
    )
    return {"id": str(resolved)}


# --- purchases ----------------------------------------------------------------


@router.post("/purchases", status_code=status.HTTP_201_CREATED)
def create_purchase(
    body: PurchaseCreateRequest,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.INVENTORY_RECEIVE))],
) -> dict[str, str]:
    """Creates a `draft`. **No stock change** — recording a supplier's delivery note is
    not the same as the goods being on the shelf."""
    purchase_id = PurchaseService(session, clock).create(
        ctx,
        supplier_id=body.supplier_id,
        lines=[
            PurchaseLine(
                product_id=item.product_id, quantity=item.quantity, unit_cost=item.unit_cost
            )
            for item in body.items
        ],
        supplier_document_number=body.supplier_document_number,
        document_date=body.document_date,
        notes=body.notes,
    )
    return {"purchase_id": str(purchase_id), "status": "draft"}


@router.post("/purchases/{purchase_id}:confirm")
def confirm_purchase(
    purchase_id: uuid.UUID,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.INVENTORY_RECEIVE))],
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    """**The only path that receives stock** (FR-PUR-003). Idempotent."""
    movements = PurchaseService(session, clock).confirm(ctx, purchase_id)
    return {"purchase_id": str(purchase_id), "status": "confirmed", "movements": movements}


@router.post("/purchases/{purchase_id}:cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel_purchase(
    purchase_id: uuid.UUID,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.INVENTORY_RECEIVE))],
) -> None:
    """Only while `draft`. A confirmed receipt is immutable — correcting it is a
    supplier return or an adjustment, not an edit."""
    PurchaseService(session, clock).cancel(ctx, purchase_id)


@router.get("/purchases")
def list_purchases(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.INVENTORY_RECEIVE))],
    supplier_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    rows = PurchaseService(session, clock).list_purchases(ctx, supplier_id=supplier_id, limit=limit)
    return {
        "items": [
            {
                "id": str(p.id),
                "supplier_id": str(p.supplier_id),
                "supplier_name": p.supplier_name,
                "supplier_document_number": p.supplier_document_number,
                "document_date": p.document_date.isoformat() if p.document_date else None,
                "status": p.status,
                "subtotal": format(p.subtotal, "f"),
                "total": format(p.total, "f"),
                "line_count": p.line_count,
            }
            for p in rows
        ]
    }


# --- returns ------------------------------------------------------------------


@router.post("/returns", status_code=status.HTTP_201_CREATED)
def create_return(
    body: ReturnCreateRequest,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.RETURNS_CREATE))],
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    """Credits the **snapshot price** — what the customer paid, not today's price.

    Damaged goods, or an explicit `restock: false`, become **scrap** rather than
    sellable stock.
    """
    result = ReturnService(session, clock).create(
        ctx,
        invoice_id=body.invoice_id,
        lines=[
            ReturnLine(
                invoice_item_id=item.invoice_item_id,
                quantity=item.quantity,
                condition=item.condition,
            )
            for item in body.items
        ],
        reason=body.reason,
        restock=body.restock,
        credit_handling=body.credit_handling,
    )
    return {
        "return_id": str(result.return_id),
        "credit_note_id": str(result.credit_note_id) if result.credit_note_id else None,
        "total_amount": format(result.total_amount, "f"),
        "movements": result.movements,
    }


@router.get("/returns")
def list_returns(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[
        CurrentContext,
        Depends(require_any(Permission.RETURNS_CREATE, Permission.REPORTS_SALES_READ)),
    ],
    invoice_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    return {
        "items": ReturnService(session, clock).list_returns(ctx, invoice_id=invoice_id, limit=limit)
    }


def _clock() -> Any:
    from bm.domain.shared.clock import SystemClock

    return SystemClock()
