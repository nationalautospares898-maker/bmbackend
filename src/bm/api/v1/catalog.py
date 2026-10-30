"""Catalog endpoints (M5).

`GET /products/search` is **the autocomplete endpoint** and has a minimum query length
of **1** (FR-CAT-003) — searching from the first character is what makes keyboard-only
billing possible.

`cost_price` is **omitted** from every response without `catalog.cost.read`, not nulled
(FR-CAT-004). `exclude_none` would still emit the key for an explicit null, so the field
is dropped from the dict instead.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from bm.api.deps import CurrentContext, ReadSession, WriteSession, require
from bm.api.v1.schemas.common import ResponseModel, StrictModel
from bm.api.v1.schemas.responses import ProductOut, ProductSearchOut
from bm.domain.auth.permissions import Permission
from bm.domain.catalog.service import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    CatalogService,
    ProductView,
)

router = APIRouter(tags=["catalog"])


# --- schemas -----------------------------------------------------------------


# `ProductOut`, `UnitOut`, `StockOut` and `MatchOut` moved to
# `schemas/responses.py` when the response shapes began being published in the OpenAPI
# document: `catalog.py` and `sync.py` both describe a product, and two copies of one
# shape is how the offline cache and the online search drift apart.


class ProductCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    unit_code: str = Field(min_length=1, max_length=16)
    default_selling_price: str
    sku: str | None = Field(default=None, max_length=64)
    barcode: str | None = Field(default=None, max_length=64)
    brand: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)
    cost_price: str | None = None
    reorder_level: str | None = None
    hsn_code: str | None = Field(default=None, max_length=16)
    #: Client-generatable so a create is idempotent on retry.
    id: uuid.UUID | None = None


class ProductUpdateRequest(StrictModel):
    name: str | None = Field(default=None, max_length=200)
    sku: str | None = Field(default=None, max_length=64)
    barcode: str | None = Field(default=None, max_length=64)
    default_selling_price: str | None = None
    cost_price: str | None = None
    reorder_level: str | None = None
    hsn_code: str | None = Field(default=None, max_length=16)
    brand: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)


class AliasCreateRequest(StrictModel):
    alias: str = Field(min_length=1, max_length=128)
    alias_type: str = "alternate_name"


class NamedOut(ResponseModel):
    id: uuid.UUID
    name: str


# --- serialisation ------------------------------------------------------------


def _product_dict(view: ProductView, *, as_of: str, may_see_cost: bool) -> dict[str, Any]:
    """Build the response dict, **dropping** `cost_price` when not permitted.

    Omission rather than null is the requirement: a nulled field invites a client to
    render a placeholder where a margin should be, and the whole point of FR-CAT-004 is
    that the value can never leak through a careless render.
    """
    payload: dict[str, Any] = {
        "id": view.id,
        "name": view.name,
        "sku": view.sku,
        "barcode": view.barcode,
        "brand": view.brand,
        "category": view.category,
        "unit": {"code": view.unit_code, "allows_fraction": view.unit_allows_fraction},
        "default_selling_price": view.default_selling_price,
        "stock": (
            {"branch_quantity": view.branch_quantity, "as_of": as_of}
            if view.branch_quantity is not None
            else None
        ),
        "is_active": view.is_active,
        "is_low_stock": view.is_low_stock,
        "hsn_code": view.hsn_code,
    }
    if view.match_kind is not None:
        payload["match"] = {"kind": view.match_kind, "score": view.match_score or 0}
    if may_see_cost and view.cost_price is not None:
        payload["cost_price"] = view.cost_price
    return payload


def _as_of() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# --- routes ------------------------------------------------------------------


@router.get("/products/search", responses={200: {"model": ProductSearchOut}})
def search_products(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
    query: Annotated[str, Query(min_length=1, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=MAX_SEARCH_LIMIT)] = DEFAULT_SEARCH_LIMIT,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """Minimum query length **1**. Results are ranked exact SKU → SKU prefix → name
    prefix → alias → trigram, with sales frequency then name as the tie-break."""
    views = CatalogService(session).search(
        ctx, query=query, limit=limit, include_inactive=include_inactive
    )
    as_of = _as_of()
    may_see_cost = ctx.has(Permission.CATALOG_COST_READ)
    return {
        "items": [_product_dict(v, as_of=as_of, may_see_cost=may_see_cost) for v in views],
        "page": {"next_cursor": None, "has_more": False, "limit": limit},
    }


@router.get("/products/lookup-barcode", responses={200: {"model": ProductOut}})
def lookup_barcode(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
    code: Annotated[str, Query(min_length=1, max_length=64)],
) -> dict[str, Any]:
    view = CatalogService(session).lookup_barcode(ctx, code)
    return _product_dict(view, as_of=_as_of(), may_see_cost=ctx.has(Permission.CATALOG_COST_READ))


@router.get("/products/{product_id}", responses={200: {"model": ProductOut}})
def get_product(
    product_id: uuid.UUID,
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
) -> dict[str, Any]:
    view = CatalogService(session).get(ctx, product_id)
    return _product_dict(view, as_of=_as_of(), may_see_cost=ctx.has(Permission.CATALOG_COST_READ))


@router.post(
    "/products",
    status_code=status.HTTP_201_CREATED,
    responses={201: {"model": ProductOut}},
)
def create_product(
    body: ProductCreateRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_WRITE))],
) -> dict[str, Any]:
    view = CatalogService(session).create_product(
        ctx,
        name=body.name,
        unit_code=body.unit_code,
        default_selling_price=body.default_selling_price,
        sku=body.sku,
        barcode=body.barcode,
        brand=body.brand,
        category=body.category,
        cost_price=body.cost_price,
        reorder_level=body.reorder_level,
        hsn_code=body.hsn_code,
        product_id=body.id,
    )
    return _product_dict(view, as_of=_as_of(), may_see_cost=ctx.has(Permission.CATALOG_COST_READ))


@router.patch("/products/{product_id}", responses={200: {"model": ProductOut}})
def update_product(
    product_id: uuid.UUID,
    body: ProductUpdateRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_WRITE))],
) -> dict[str, Any]:
    """A price change is audited with before/after values (FR-AUDIT-003, T-2)."""
    view = CatalogService(session).update_product(
        ctx,
        product_id,
        name=body.name,
        sku=body.sku,
        barcode=body.barcode,
        default_selling_price=body.default_selling_price,
        cost_price=body.cost_price,
        reorder_level=body.reorder_level,
        hsn_code=body.hsn_code,
        brand=body.brand,
        category=body.category,
    )
    return _product_dict(view, as_of=_as_of(), may_see_cost=ctx.has(Permission.CATALOG_COST_READ))


@router.post("/products/{product_id}:deactivate", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_product(
    product_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_WRITE))],
) -> None:
    """Never deletes (FR-CAT-009)."""
    CatalogService(session).deactivate(ctx, product_id)


@router.post("/products/{product_id}/aliases", status_code=status.HTTP_201_CREATED)
def add_alias(
    product_id: uuid.UUID,
    body: AliasCreateRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_WRITE))],
) -> dict[str, str]:
    """Adding an alias refreshes the parent's `search_text` (fix S-8), otherwise the
    alias is stored but unsearchable."""
    alias_id = CatalogService(session).add_alias(
        ctx, product_id, alias=body.alias, alias_type=body.alias_type
    )
    return {"id": str(alias_id)}


@router.delete("/products/{product_id}/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alias(
    product_id: uuid.UUID,
    alias_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_WRITE))],
) -> None:
    CatalogService(session).delete_alias(ctx, alias_id)


@router.get("/products/{product_id}/aliases")
def list_aliases(
    product_id: uuid.UUID,
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
) -> list[dict[str, str]]:
    return [
        {"id": str(alias_id), "alias": alias, "alias_type": alias_type}
        for alias_id, alias, alias_type in CatalogService(session).aliases_for(ctx, product_id)
    ]


@router.get("/units")
def list_units(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
) -> list[dict[str, Any]]:
    return [
        {
            "id": str(u.id),
            "code": u.code,
            "name": u.name,
            "allows_fraction": u.allows_fraction,
            "decimal_places": u.decimal_places,
        }
        for u in CatalogService(session).list_units(ctx)
    ]


@router.post("/units:ensure-defaults")
def ensure_default_units(
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_WRITE))],
) -> list[dict[str, Any]]:
    """Seed the default units for a business created after migration 0016 ran.

    Units are reference data a spare-parts shop cannot operate without. A tax rate, a
    product or a business identity is **never** seeded.
    """
    return [
        {"code": u.code, "name": u.name, "allows_fraction": u.allows_fraction}
        for u in CatalogService(session).ensure_default_units(ctx)
    ]


@router.get("/brands", response_model=list[NamedOut])
def list_brands(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
) -> list[NamedOut]:
    return [NamedOut(id=i, name=n) for i, n in CatalogService(session).list_brands(ctx)]


@router.get("/categories", response_model=list[NamedOut])
def list_categories(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
) -> list[NamedOut]:
    return [NamedOut(id=i, name=n) for i, n in CatalogService(session).list_categories(ctx)]
