"""Customer endpoints (M7).

`PUT /customers/{id}` is an upsert with a **client-generated id**, so creating a customer
offline is idempotent and the client's id becomes the server's id with no remapping
(FR-CUST-003).

**Only `name` is required** (FR-CUST-010). `POST /customers/check-duplicates` surfaces
likely matches; the system **never** auto-merges (FR-CUST-004).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from bm.api.deps import CurrentContext, ReadSession, WriteSession, require, require_any
from bm.api.v1.schemas.common import MoneyStr, StrictModel
from bm.domain.auth.permissions import Permission
from bm.domain.customers.service import CustomerService, CustomerView

router = APIRouter(prefix="/customers", tags=["customers"])


class CustomerUpsertRequest(StrictModel):
    #: The **only** required field.
    name: str = Field(min_length=1, max_length=200)
    business_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    address_lines: list[str] | None = None
    default_mechanic_name: str | None = Field(default=None, max_length=200)
    vehicle_number: str | None = Field(default=None, max_length=32)
    credit_terms_days: int | None = Field(default=None, ge=0, le=365)
    credit_limit: MoneyStr | None = None


class DuplicateCheckRequest(StrictModel):
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)


def _customer_dict(view: CustomerView, *, include_credit: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(view.id),
        "name": view.name,
        "business_name": view.business_name,
        "primary_phone": view.primary_phone,
        "phones": list(view.phones),
        "vehicles": list(view.vehicles),
        "address_lines": list(view.address_lines),
        "default_mechanic_name": view.default_mechanic_name,
        "credit_terms_days": view.credit_terms_days,
        "credit_limit": format(view.credit_limit, "f") if view.credit_limit is not None else None,
        "is_active": view.is_active,
    }
    # The outstanding summary appears only with `credit.read` (FR-CUST-012).
    if include_credit and view.outstanding is not None:
        payload["credit"] = {
            "outstanding": format(view.outstanding, "f"),
            "oldest_due_date": (view.oldest_due_date.isoformat() if view.oldest_due_date else None),
        }
    return payload


@router.get("/search")
def search_customers(
    session: ReadSession,
    ctx: Annotated[
        CurrentContext,
        Depends(require_any(Permission.CUSTOMER_MANAGE, Permission.CREDIT_READ)),
    ],
    query: Annotated[str, Query(min_length=1, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    """Matches name, phone, business name and vehicle."""
    views = CustomerService(session).search(ctx, query=query, limit=limit)
    include_credit = ctx.has(Permission.CREDIT_READ)
    return {
        "items": [_customer_dict(v, include_credit=include_credit) for v in views],
        "page": {"next_cursor": None, "has_more": False, "limit": limit},
    }


@router.get("/{customer_id}")
def get_customer(
    customer_id: uuid.UUID,
    session: ReadSession,
    ctx: Annotated[
        CurrentContext,
        Depends(require_any(Permission.CUSTOMER_MANAGE, Permission.CREDIT_READ)),
    ],
) -> dict[str, Any]:
    view = CustomerService(session).get(ctx, customer_id)
    return _customer_dict(view, include_credit=ctx.has(Permission.CREDIT_READ))


@router.put("/{customer_id}")
def upsert_customer(
    customer_id: uuid.UUID,
    body: CustomerUpsertRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CUSTOMER_MANAGE))],
) -> dict[str, Any]:
    """Needs no `Idempotency-Key` — the client-generated id makes it naturally
    idempotent (`API-SPECIFICATION.md §5`)."""
    result = CustomerService(session).upsert(
        ctx,
        customer_id,
        name=body.name,
        business_name=body.business_name,
        phone=body.phone,
        address_lines=body.address_lines,
        default_mechanic_name=body.default_mechanic_name,
        vehicle_number=body.vehicle_number,
        credit_terms_days=body.credit_terms_days,
        credit_limit=body.credit_limit,
    )
    payload = _customer_dict(result.customer, include_credit=ctx.has(Permission.CREDIT_READ))
    # A duplicate suspicion rides along with the **successful** response; nothing is
    # merged and the save is not blocked (§4.3).
    return {"customer": payload, "created": result.created, "warnings": result.warnings}


@router.post("/check-duplicates")
def check_duplicates(
    body: DuplicateCheckRequest,
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CUSTOMER_MANAGE))],
) -> dict[str, Any]:
    """Surfaces candidates for a person to choose between. **Never merges.**"""
    candidates = CustomerService(session).check_duplicates(ctx, name=body.name, phone=body.phone)
    return {
        "candidates": [
            {
                "id": str(c.id),
                "name": c.name,
                "primary_phone": c.primary_phone,
                "matched_on": c.matched_on,
            }
            for c in candidates
        ],
        "auto_merged": False,
    }


@router.get("/{customer_id}/ledger")
def customer_ledger(
    customer_id: uuid.UUID,
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CREDIT_READ))],
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> dict[str, Any]:
    """Invoices, payments and credit notes with a running balance."""
    return {
        "entries": CustomerService(session).ledger(ctx, customer_id, limit=limit),
        "as_of": _now(),
    }


@router.post("/{customer_id}:deactivate", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_customer(
    customer_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CUSTOMER_MANAGE))],
) -> None:
    """Soft deactivate. A referenced customer is never hard-deleted."""
    CustomerService(session).deactivate(ctx, customer_id)


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
