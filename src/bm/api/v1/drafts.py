"""Draft bill endpoints (M8).

`PUT /drafts/{id}` is an **upsert with a client-generated id** (FR-DRAFT-001): create
when absent, update with `If-Match: <version>`. A blind write to an existing draft is
`428 PRECONDITION_REQUIRED` — the server will not overwrite a bill somebody else may be
editing.

`DELETE /drafts/{id}` is `Clear Draft`. It marks the draft `cancelled`, keeps the row,
and **never** touches a finalised sale.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import Field
from sqlalchemy import text

from bm.api.deps import CurrentContext, ReadSession, WriteSession, require
from bm.api.v1.schemas.common import MoneyStr, PercentStr, QuantityStr, StrictModel
from bm.api.v1.schemas.responses import DraftListOut, DraftOut
from bm.domain.auth.permissions import Permission
from bm.domain.sales.drafts import DraftItemInput, DraftService, DraftView
from bm.domain.shared.errors import ValidationFailed
from bm.domain.shared.money import ZERO

router = APIRouter(prefix="/drafts", tags=["drafts"])


class DraftItemIn(StrictModel):
    #: Client-generated, so an offline-created line keeps its identity through sync.
    id: uuid.UUID
    line_no: int = Field(ge=1, le=999)
    product_id: uuid.UUID
    quantity: QuantityStr
    unit_price: MoneyStr
    catalog_price_at_add: MoneyStr | None = None
    price_source: str = "catalog"
    discount_percent: PercentStr | None = None
    discount_amount: MoneyStr | None = None
    notes: str | None = Field(default=None, max_length=500)


class DraftUpsertRequest(StrictModel):
    counter_id: uuid.UUID
    items: list[DraftItemIn] = Field(default_factory=list)
    label: str | None = Field(default=None, max_length=200)
    customer_id: uuid.UUID | None = None
    customer_name: str | None = Field(default=None, max_length=200)
    customer_business_name: str | None = Field(default=None, max_length=200)
    vehicle_number: str | None = Field(default=None, max_length=32)
    mechanic_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    is_credit_intent: bool = False
    #: Diagnostics only; server time is authoritative (FR-OFFLINE-007).
    client_created_at: datetime | None = None
    client_updated_at: datetime | None = None


def _draft_dict(view: DraftView) -> dict[str, Any]:
    return {
        "id": str(view.id),
        "counter_id": str(view.counter_id),
        "label": view.label,
        "status": view.status,
        "version": view.version,
        "customer_id": str(view.customer_id) if view.customer_id else None,
        "customer_name": view.customer_name,
        "customer_business_name": view.customer_business_name,
        "vehicle_number": view.vehicle_number,
        "mechanic_name": view.mechanic_name,
        "phone": view.phone,
        "notes": view.notes,
        "is_credit_intent": view.is_credit_intent,
        "items": [
            {
                "id": str(i.id),
                "line_no": i.line_no,
                "product_id": str(i.product_id),
                "product_name": i.product_name,
                "quantity": format(i.quantity, "f"),
                "unit_price": format(i.unit_price, "f"),
                "catalog_price_at_add": format(i.catalog_price_at_add, "f"),
                "price_source": i.price_source,
                "discount_percent": format(i.discount_percent, "f"),
                "discount_amount": format(i.discount_amount, "f"),
                "line_total_preview": format(i.line_total_preview, "f"),
            }
            for i in view.items
        ],
        # Clearly labelled advisory (FR-DRAFT-011). The server recomputes everything at
        # finalisation; the client shows this as "preview — confirmed on completion".
        "preview_totals": {
            "advisory": True,
            "total": format(view.preview_total, "f"),
        },
        "invoice_id": str(view.invoice_id) if view.invoice_id else None,
        "updated_at": view.updated_at.isoformat(),
    }


@router.get("", responses={200: {"model": DraftListOut}})
def list_drafts(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.BILLING_DRAFT_MANAGE))],
    counter_id: uuid.UUID | None = None,
    draft_status: Annotated[str, Query(alias="status")] = "open",
) -> dict[str, Any]:
    if draft_status != "open":
        raise ValidationFailed({"status": "Only `open` drafts are listed."})
    views = DraftService(session).list_open(ctx, counter_id=counter_id)
    return {"items": [_draft_dict(v) for v in views]}


@router.get("/{draft_id}", responses={200: {"model": DraftOut}})
def get_draft(
    draft_id: uuid.UUID,
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.BILLING_DRAFT_MANAGE))],
) -> dict[str, Any]:
    return _draft_dict(DraftService(session).get(ctx, draft_id))


@router.put("/{draft_id}", responses={200: {"model": DraftOut}})
def upsert_draft(
    draft_id: uuid.UUID,
    body: DraftUpsertRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.BILLING_DRAFT_MANAGE))],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> dict[str, Any]:
    """Saving a draft **never** finalises a sale and **never** deducts stock
    (FR-DRAFT-005)."""
    version: int | None = None
    if if_match is not None:
        stripped = if_match.strip().strip('"')
        if not stripped.isdigit():
            raise ValidationFailed({"If-Match": "Must be the draft's version number."})
        version = int(stripped)

    # Product names are snapshotted onto the line so a reopened tab reads correctly even
    # if the catalog changed. Resolved here because the draft service must not reach
    # into the catalog's tables.
    names: dict[uuid.UUID, str] = {}
    for item in body.items:
        name = session.execute(
            text("SELECT name FROM products WHERE id = :id AND business_id = :business_id"),
            {"id": item.product_id, "business_id": ctx.business_id},
        ).scalar_one_or_none()
        if name is None:
            raise ValidationFailed(
                {f"items.{item.line_no}.product_id": "That product does not exist."}
            )
        names[item.product_id] = name

    view = DraftService(session).upsert(
        ctx,
        draft_id,
        counter_id=body.counter_id,
        items=[
            DraftItemInput(
                id=item.id,
                line_no=item.line_no,
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                catalog_price_at_add=item.catalog_price_at_add or item.unit_price,
                price_source=item.price_source,
                discount_percent=item.discount_percent or ZERO,
                discount_amount=item.discount_amount or ZERO,
                notes=item.notes,
            )
            for item in body.items
        ],
        product_names=names,
        if_match_version=version,
        label=body.label,
        customer_id=body.customer_id,
        customer_name=body.customer_name,
        customer_business_name=body.customer_business_name,
        vehicle_number=body.vehicle_number,
        mechanic_name=body.mechanic_name,
        phone=body.phone,
        notes=body.notes,
        is_credit_intent=body.is_credit_intent,
        client_created_at=body.client_created_at,
    )
    return _draft_dict(view)


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def clear_draft(
    draft_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.BILLING_DRAFT_MANAGE))],
) -> None:
    """`Clear Draft`. Marks `cancelled`, audited, **never** hard-deletes and never
    removes a finalised sale."""
    DraftService(session).clear(ctx, draft_id)


@router.post("/{draft_id}:takeover", responses={200: {"model": DraftOut}})
def takeover_draft(
    draft_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.BILLING_DRAFT_TAKEOVER))],
) -> dict[str, Any]:
    return _draft_dict(DraftService(session).takeover(ctx, draft_id))
