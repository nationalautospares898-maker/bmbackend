"""Chitti orders (Feature B).

No specific permission beyond being signed in — see `domain/orders/service.py`'s
module docstring for why.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from bm.api.deps import CurrentContext, ReadSession, WriteSession, authenticated_only
from bm.api.v1.schemas.responses import (
    CreatedIdOut,
    LifecycleStatusOut,
    OrderListOut,
    UpdatedOut,
)
from bm.domain.orders import service as orders
from bm.domain.shared.errors import NotFound

router = APIRouter(prefix="/orders", tags=["orders"])


def _out(o: orders.OrderView) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "customer_name": o.customer_name,
        "phone": o.phone,
        "body": o.body,
        "status": o.status,
        "created_at": o.created_at,
        "updated_at": o.updated_at,
        "completed_at": o.completed_at,
        "discarded_at": o.discarded_at,
        "remind_at": o.remind_at,
    }


@router.get("", responses={200: {"model": OrderListOut}})
def list_orders(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    items = orders.list_orders(ctx, session, status=status, limit=limit)
    return {"items": [_out(o) for o in items]}


class OrderIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    customer_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)


@router.post("", status_code=201, responses={201: {"model": CreatedIdOut}})
def create_order(
    body: OrderIn,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
) -> dict[str, Any]:
    order_id = orders.create_order(
        ctx, session, body=body.body, customer_name=body.customer_name, phone=body.phone
    )
    return {"id": str(order_id)}


class OrderUpdateIn(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    customer_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)


@router.put("/{order_id}", responses={200: {"model": UpdatedOut}})
def update_order(
    order_id: uuid.UUID,
    body: OrderUpdateIn,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
) -> dict[str, Any]:
    if not orders.update_order(
        ctx,
        session,
        order_id,
        body=body.body,
        customer_name=body.customer_name,
        phone=body.phone,
    ):
        raise NotFound("That order does not exist, or is no longer pending.")
    return {"updated": True}


class OrderReminderIn(BaseModel):
    #: `null` clears the reminder. A timezone-aware instant — the owner picks a wall-clock
    #: time in their own browser, which already carries its own offset in ISO 8601.
    remind_at: datetime | None = None


@router.put("/{order_id}/reminder", responses={200: {"model": UpdatedOut}})
def set_order_reminder(
    order_id: uuid.UUID,
    body: OrderReminderIn,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
) -> dict[str, Any]:
    """Set or clear the owner's one-time reminder for one order (owner request).

    Fires **ahead of** the flat ten-minute nudge once due, and is consumed (cleared)
    the instant it fires — see `domain/orders/service.py::nudge_pending`.
    """
    if not orders.set_reminder(ctx, session, order_id, remind_at=body.remind_at):
        raise NotFound("That order does not exist, or is no longer pending.")
    return {"updated": True}


@router.post("/{order_id}:complete", responses={200: {"model": LifecycleStatusOut}})
def complete_order(
    order_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
) -> dict[str, Any]:
    if not orders.complete_order(ctx, session, order_id):
        raise NotFound("That order does not exist, or is no longer pending.")
    return {"status": "completed"}


@router.post("/{order_id}:discard", responses={200: {"model": LifecycleStatusOut}})
def discard_order(
    order_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
) -> dict[str, Any]:
    if not orders.discard_order(ctx, session, order_id):
        raise NotFound("That order does not exist, or is no longer pending.")
    return {"status": "discarded"}
