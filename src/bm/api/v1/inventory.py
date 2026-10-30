"""Inventory endpoints (M6).

**There is no endpoint that writes `inventory_balances` directly.** Every change is a
reasoned movement through `InventoryService.apply_movements`, which is the sole writer.

`GET /inventory/reconciliation/latest` is **read-only and never auto-fixes**
(FR-INV-006). Drift means there is a bug to find, and a self-healing balance table would
hide it.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from bm.api.deps import CurrentContext, IdempotencyKey, ReadSession, WriteSession, require
from bm.api.v1.schemas.common import QuantityStr, StrictModel
from bm.api.v1.schemas.responses import (
    AdjustmentOut,
    StockBalancesOut,
    StockMovementsOut,
)
from bm.domain.auth.permissions import Permission
from bm.domain.inventory.service import InventoryService

router = APIRouter(prefix="/inventory", tags=["inventory"])


class AdjustmentRequest(StrictModel):
    product_id: uuid.UUID
    #: Signed. The direction of the movement is derived from this, never from
    #: `reason_code`, so a mislabelled code cannot record an increase that did not
    #: happen.
    quantity_delta: QuantityStr
    reason_code: str = "adjustment_decrease"
    #: **Mandatory free text** on top of the code (FR-INV-007). "Stock adjusted by -4"
    #: with no explanation is exactly the audit gap T-3 describes.
    reason_text: str = Field(min_length=3, max_length=500)


@router.get("/balances", responses={200: {"model": StockBalancesOut}})
def list_balances(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
    low_stock_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> dict[str, Any]:
    rows = InventoryService(session).balances(ctx, low_stock_only=low_stock_only, limit=limit)
    return {
        "items": [
            {
                "product_id": str(product_id),
                "name": name,
                "quantity": format(quantity, "f"),
                "reorder_level": format(reorder, "f") if reorder is not None else None,
            }
            for product_id, name, quantity, reorder in rows
        ],
        "as_of": _now(),
    }


@router.get("/movements", responses={200: {"model": StockMovementsOut}})
def list_movements(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
    product_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> dict[str, Any]:
    """The append-only ledger (FR-INV-001)."""
    movements = InventoryService(session).movements(ctx, product_id=product_id, limit=limit)
    return {
        "items": [
            {
                "id": str(m.id),
                "product_id": str(m.product_id),
                "quantity_delta": format(m.quantity_delta, "f"),
                "balance_after": format(m.balance_after, "f"),
                "reason": str(m.reason),
                "source_type": m.source_type,
                "source_id": str(m.source_id),
                "oversell_override": m.oversell_override,
                "override_reason": m.override_reason,
                "created_at": m.created_at.isoformat(),
            }
            for m in movements
        ],
        "as_of": _now(),
    }


@router.post("/adjustments", responses={200: {"model": AdjustmentOut}})
def create_adjustment(
    body: AdjustmentRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.INVENTORY_ADJUST))],
    idempotency_key: IdempotencyKey,
) -> dict[str, str]:
    """A reasoned, audited correction. `Idempotency-Key` is required so a retried
    adjustment cannot be applied twice."""
    movement_id = InventoryService(session).adjust(
        ctx,
        product_id=body.product_id,
        quantity_delta=body.quantity_delta,
        reason_code=body.reason_code,
        reason_text=body.reason_text,
    )
    return {"movement_id": str(movement_id)}


@router.get("/reconciliation/latest")
def latest_reconciliation(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.AUDIT_READ))],
) -> dict[str, Any]:
    """Read-only. **Reports and never repairs** (FR-INV-006).

    Also carries T3-04's numbering check: per series, `max(sequence_number) + 1` must
    equal `next_number`. A mismatch indicates a lost update and is reported, not
    corrected.
    """
    report = InventoryService(session).reconcile(ctx)
    return {**report, "as_of": _now()}


@router.post("/reconciliation:run")
def run_reconciliation(
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.AUDIT_READ))],
) -> dict[str, Any]:
    """Runs the check now rather than waiting for the nightly job.

    Still read-only with respect to balances — it writes only its own report row.
    """
    service = InventoryService(session)
    report = service.reconcile(ctx)

    from sqlalchemy import text as sql_text

    session.execute(
        sql_text(
            "INSERT INTO reconciliation_runs"
            " (id, business_id, finished_at, balances_checked, discrepancies_found,"
            "  findings, severity)"
            " VALUES (gen_random_uuid(), :business_id, now(), :checked, :found,"
            "         CAST(:findings AS jsonb), :severity)"
        ),
        {
            "business_id": ctx.business_id,
            "checked": report["balances_checked"],
            "found": report["discrepancies_found"],
            "findings": _json(report),
            "severity": report["severity"],
        },
    )
    return {**report, "as_of": _now()}


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
