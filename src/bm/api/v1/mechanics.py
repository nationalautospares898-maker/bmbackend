"""Mechanic reference list (M27).

The counter types a mechanic's name on a bill; this is what makes the *next* bill
suggest it. `invoices.mechanic_name` remains the snapshot of record, so nothing here can
change what a finalised bill says — see `infra/db/models/mechanics.py` for why that
separation is what makes a mutable mechanic list safe at all.

`POST /mechanics` is a **get-or-create**, not a create. Sending a name that already
exists returns the existing mechanic with `created: false` rather than a conflict,
because the billing screen cannot know and must not have to ask. That also makes the
endpoint idempotent without an `Idempotency-Key`: it moves neither money nor stock, so
invariant 9 does not apply to it.

Permission is `customer.manage` throughout — the permission the cashier already holds
for the equivalent contact data, rather than a new name invented here (`CLAUDE.md §6`,
`PRD.md §3.2`).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from bm.api.deps import CurrentContext, ReadSession, WriteSession, require
from bm.api.v1.schemas.common import StrictModel
from bm.api.v1.schemas.responses import MechanicListOut, MechanicOut
from bm.domain.auth.permissions import Permission
from bm.domain.mechanics.service import MechanicService, MechanicView

router = APIRouter(prefix="/mechanics", tags=["mechanics"])


class MechanicEnsureRequest(StrictModel):
    #: The only required field, matching the customer rule (FR-CUST-010): a name is
    #: enough, because anything more at a busy counter is what makes staff skip it.
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=32)


class MechanicRenameRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)


def _mechanic_dict(view: MechanicView) -> dict[str, Any]:
    return {
        "id": str(view.id),
        "name": view.name,
        "phone": view.phone,
        "is_active": view.is_active,
        "use_count": view.use_count,
        "last_used_at": view.last_used_at.isoformat() if view.last_used_at else None,
    }


@router.get("", responses={200: {"model": MechanicListOut}})
def list_mechanics(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CUSTOMER_MANAGE))],
    query: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    """Suggestions for the billing screen.

    **`query` is optional on purpose.** The field is focused before anything is typed,
    and the recently-used list is the most useful thing to show at that moment.
    """
    views = MechanicService(session).search(ctx, query=query, limit=limit)
    return {
        "items": [_mechanic_dict(v) for v in views],
        "page": {"next_cursor": None, "has_more": False, "limit": limit},
    }


@router.post("", status_code=status.HTTP_200_OK, responses={200: {"model": MechanicOut}})
def ensure_mechanic(
    body: MechanicEnsureRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CUSTOMER_MANAGE))],
) -> dict[str, Any]:
    """Get-or-create by normalised name, and mark it used.

    `200`, not `201`: the common outcome is "this mechanic already existed", and a
    status code that claims a creation every time would be a lie the client then has to
    ignore. `created` in the body carries the truth.
    """
    result = MechanicService(session).ensure(ctx, name=body.name, phone=body.phone)
    return {**_mechanic_dict(result.mechanic), "created": result.created}


@router.patch("/{mechanic_id}", responses={200: {"model": MechanicOut}})
def rename_mechanic(
    mechanic_id: uuid.UUID,
    body: MechanicRenameRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CUSTOMER_MANAGE))],
) -> dict[str, Any]:
    """Correct a spelling. Past bills keep their own snapshot and are unaffected."""
    view = MechanicService(session).rename(ctx, mechanic_id, name=body.name)
    return {**_mechanic_dict(view), "created": False}


@router.post("/{mechanic_id}:deactivate", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_mechanic(
    mechanic_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CUSTOMER_MANAGE))],
) -> None:
    """Drop from the suggestions. Deliberately not a delete."""
    MechanicService(session).deactivate(ctx, mechanic_id)
