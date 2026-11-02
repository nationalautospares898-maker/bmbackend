"""Inventory endpoints (M6).

**There is no endpoint that writes `inventory_balances` directly.** Every change is a
reasoned movement through `InventoryService.apply_movements`, which is the sole writer.

`GET /inventory/reconciliation/latest` is **read-only and never auto-fixes**
(FR-INV-006). Drift means there is a bug to find, and a self-healing balance table would
hide it.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from bm.api.deps import CurrentContext, IdempotencyKey, ReadSession, WriteSession, require
from bm.api.v1.schemas.common import MoneyStr, QuantityStr, StrictModel
from bm.api.v1.schemas.responses import (
    AdjustmentOut,
    MovementReportOut,
    StockBalancesOut,
    StockEntriesOut,
    StockEntryMatchOut,
    StockMovementsOut,
)
from bm.domain.auth.permissions import Permission
from bm.domain.inventory.service import InventoryService
from bm.domain.stockentry.service import StockEntryInput, StockEntryService
from bm.domain.sync.idempotency import IdempotencyService

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


class StockEntryRow(StrictModel):
    """One row of the owner's four-column sheet: Item · Price · Min Qty · Stock Qty."""

    item: str = Field(min_length=1, max_length=200)
    price: MoneyStr
    #: This product's **own** minimum. Per item, never one figure for the whole catalog
    #: (FR-INV-008).
    min_qty: QuantityStr
    #: The quantity that **came in** — added to whatever is already on the shelf. It is
    #: not a new total, and there is no endpoint here that sets a balance.
    stock_qty: QuantityStr


class StockEntryRequest(StrictModel):
    entries: list[StockEntryRow] = Field(min_length=1, max_length=200)


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
                "product_id": str(row.product_id),
                "name": row.name,
                "quantity": format(row.quantity, "f"),
                "reorder_level": (
                    format(row.reorder_level, "f") if row.reorder_level is not None else None
                ),
                "sku": row.sku,
                "default_selling_price": format(row.default_selling_price, "f"),
            }
            for row in rows
        ],
        "as_of": _now(),
    }


@router.get("/movements", responses={200: {"model": StockMovementsOut}})
def list_movements(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
    product_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
    oldest_first: bool = False,
) -> dict[str, Any]:
    """The append-only ledger (FR-INV-001) — one product's complete history.

    Every movement is here, not only sales: the opening figure, each sheet arrival, each
    correction, each sale, and each reversal of a cancelled bill. `balance_after` is the
    stock the database actually held after that row, recorded when the movement was
    written — so the history is read from the ledger and never reconstructed backwards
    from today's total.

    `oldest_first=true` is what the History panel asks for, so the running balance reads
    downwards.
    """
    service = InventoryService(session)
    movements = service.movements(
        ctx, product_id=product_id, limit=limit, oldest_first=oldest_first
    )
    references = service.movement_references(ctx, movements)
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
                "reference": references.get(m.id),
            }
            for m in movements
        ],
        "as_of": _now(),
    }


@router.get("/movements/report", responses={200: {"model": MovementReportOut}})
def movement_report(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
    on_date: Annotated[date, Query(description="Business-local date, YYYY-MM-DD")],
    kind: Annotated[str, Query(pattern="^(added|sold)$")] = "added",
) -> dict[str, Any]:
    """What moved in or out of stock on one day.

    The per-product History answers "what happened to this part"; this answers "what
    happened today", from the same append-only ledger — so the two can never disagree and
    nothing is recomputed from the current total.

    `kind=added` is every movement that **increased** stock: the opening figure, a
    purchase receipt, the owner's stock sheet, a correction upwards, a customer return, an
    applied import, and the reversal of a cancelled bill. `kind=sold` is `reason = 'sale'`
    alone — what customers took away.

    Three exclusions are structural rather than filtered, which is what makes them
    reliable: a **draft** writes no movement, a **reprint** writes no movement, and a
    **credit payment** moves money rather than goods. None of them can appear here
    whatever this endpoint does.

    `stock_before` and `stock_after` are the ledger's own figures. The day is the
    business-local one (`(created_at AT TIME ZONE …)::date`), the same boundary Daily
    Bills uses, so the two screens agree on what "today" means.
    """
    report = InventoryService(session).movement_report(ctx, on_date=on_date, kind=kind)
    return {
        "on_date": report.on_date.isoformat(),
        "kind": report.kind,
        "items": [
            {
                "movement_id": str(line.movement_id),
                "product_id": str(line.product_id),
                "product_name": line.product_name,
                "sku": line.sku,
                "quantity": format(line.quantity, "f"),
                "stock_before": format(line.stock_before, "f"),
                "stock_after": format(line.stock_after, "f"),
                "reason": line.reason,
                "reference": line.reference,
                "is_credit": line.is_credit,
                "invoice_cancelled": line.invoice_cancelled,
                "created_at": line.created_at.isoformat(),
            }
            for line in report.lines
        ],
        "products": report.products,
        "total_quantity": format(report.total_quantity, "f"),
        "movements": report.movements,
        "invoices": report.invoices,
        "truncated": report.truncated,
        "as_of": _now(),
    }


@router.get("/stock-entries:resolve", responses={200: {"model": StockEntryMatchOut}})
def resolve_stock_entry(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
    item: Annotated[str, Query(min_length=1, max_length=200)],
) -> dict[str, Any]:
    """What would saving this item name do — add a new product, or top up an existing one?

    Read-only, and answered by the **same** matcher the save uses, so the screen's
    "will update existing stock" can never promise something the save then does
    differently (FR-INV-008, the owner's stock sheet).
    """
    match = StockEntryService(session).resolve(ctx, item)
    return {
        "kind": match.kind,
        "product_id": str(match.product_id) if match.product_id else None,
        "product_name": match.product_name,
        "current_quantity": (
            format(match.current_quantity, "f") if match.current_quantity is not None else None
        ),
        "current_price": (
            format(match.current_price, "f") if match.current_price is not None else None
        ),
        "current_min_qty": (
            format(match.current_min_qty, "f") if match.current_min_qty is not None else None
        ),
        "candidates": [
            {"product_id": str(candidate_id), "name": name}
            for candidate_id, name in match.candidates
        ],
    }


@router.post("/stock-entries", responses={200: {"model": StockEntriesOut}})
def create_stock_entries(
    body: StockEntryRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_WRITE))],
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    """Save a sheet of stock arrivals. **Adds to stock; never replaces it.**

    The route declares `catalog.write` because a row may create a product;
    `StockEntryService` additionally re-asserts `inventory.adjust`, so a caller who may
    edit the catalog but not move stock is refused there rather than here.

    The key is **claimed**, not merely required. Requiring the header and then ignoring it
    is worse than not asking for it: the caller is told the retry is safe while a second
    press books the delivery a second time, and because `stock_movements` can never be
    deleted the only correction left is a third, opposite movement. `claim` runs before
    any stock is locked and `store_response` commits in the same transaction as the
    movements, so a replay returns the first outcome verbatim (`§5.2.1`, `§5.2.8`).
    """
    idempotency = IdempotencyService(session)
    claim = idempotency.claim(
        business_id=ctx.business_id,
        endpoint="POST /inventory/stock-entries",
        key=idempotency_key,
        body=body.model_dump(mode="json"),
        user_id=ctx.user_id,
        device_id=ctx.device_id,
    )
    if claim.replayed:
        # The same sheet, already received. Hand back what it did rather than doing it
        # again.
        return cast("dict[str, Any]", claim.stored_body)

    results = StockEntryService(session).record(
        ctx,
        [
            StockEntryInput(
                item=row.item,
                price=row.price,
                min_qty=row.min_qty,
                stock_qty=row.stock_qty,
            )
            for row in body.entries
        ],
    )
    payload = {
        "items": [
            {
                "item": r.item,
                "product_id": str(r.product_id),
                "created": r.created,
                "quantity_added": format(r.quantity_added, "f"),
                "quantity_before": format(r.quantity_before, "f"),
                "quantity_after": format(r.quantity_after, "f"),
                "movement_id": str(r.movement_id),
                "reason": r.reason,
            }
            for r in results
        ],
        "as_of": _now(),
    }
    idempotency.store_response(claim, status_code=200, body=payload)
    return payload


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
