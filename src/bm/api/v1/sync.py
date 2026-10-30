"""Synchronisation endpoints (M14).

**There is no sync operation type for finalising a sale, recording a payment, confirming
a purchase, creating a return or applying an import.** This is an intentional, enforced
boundary (FR-OFFLINE-002, ADR-011), asserted structurally by
`tests/structural/test_sync_registry.py` (AC-OFF-8).

`POST /sync/operations` returns `200` whenever the batch itself was well-formed;
per-operation outcomes are in the body. A revoked device is refused here too
(FR-OFFLINE-010).
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
    ReadSession,
    WriteSession,
    authenticated_only,
    require,
)
from bm.api.v1.schemas.common import StrictModel
from bm.api.v1.schemas.responses import (
    BootstrapOut,
    CatalogDeltaOut,
    OperationTypesOut,
    SyncBatchOut,
)
from bm.domain.auth.permissions import Permission
from bm.domain.sync.service import (
    ALLOWED_OPERATION_TYPES,
    MAX_BATCH_SIZE,
    SyncOperationInput,
    SyncService,
)

router = APIRouter(prefix="/sync", tags=["sync"])


class SyncOperationIn(StrictModel):
    op_id: uuid.UUID
    client_seq: int = Field(ge=0)
    #: Must be one of `draft.upsert`, `draft.delete`, `customer.upsert`. Anything else
    #: is rejected with an explanation naming what needs a connection.
    type: str
    entity_type: str
    entity_id: uuid.UUID
    payload: dict[str, Any] = Field(default_factory=dict)
    base_version: int | None = None
    #: Diagnostics only; the server's receipt time is authoritative (FR-OFFLINE-007).
    client_created_at: datetime | None = None


class SyncBatchRequest(StrictModel):
    device_id: uuid.UUID
    operations: list[SyncOperationIn] = Field(max_length=MAX_BATCH_SIZE)


@router.post("/operations", responses={200: {"model": SyncBatchOut}})
def apply_operations(
    body: SyncBatchRequest,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.BILLING_DRAFT_MANAGE))],
) -> dict[str, Any]:
    """Apply a bounded batch of safe offline operations.

    A bad operation never blocks the rest of the batch, except that operations for the
    **same entity** stop at the first failure to preserve ordering — a later edit
    applied before an unresolved earlier one would silently reorder the user's work.
    """
    result = SyncService(session, clock).apply_batch(
        ctx,
        [
            SyncOperationInput(
                op_id=op.op_id,
                client_seq=op.client_seq,
                type=op.type,
                entity_type=op.entity_type,
                entity_id=op.entity_id,
                payload=op.payload,
                base_version=op.base_version,
                client_created_at=op.client_created_at,
            )
            for op in body.operations
        ],
    )
    return {
        "results": [
            {
                "op_id": str(outcome.op_id),
                "status": outcome.status,
                "server_state": outcome.server_state,
                **(
                    {
                        "error": {
                            "code": outcome.error_code,
                            "message": outcome.error_message,
                        }
                    }
                    if outcome.error_code
                    else {}
                ),
            }
            for outcome in result.results
        ],
        "server_time": result.server_time,
    }


@router.get("/catalog", responses={200: {"model": CatalogDeltaOut}})
def catalog_delta(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.CATALOG_READ))],
    since: str | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> dict[str, Any]:
    """A bounded delta for the offline cache.

    **`cost_price` is never included, regardless of permission** — a stolen counter
    machine must not carry the shop's margins (`SECURITY.md §11`).
    """
    return SyncService(session, clock).catalog_delta(ctx, since=since, limit=limit)


@router.get("/bootstrap", responses={200: {"model": BootstrapOut}})
def bootstrap(
    session: ReadSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
) -> dict[str, Any]:
    """One call after login.

    Publishes the **money policy** so the client never hardcodes a rounding rule, the
    client-visible settings, the print profile and the date order — and states plainly
    that offline finalisation is refused, so the client can show the reason rather than
    inventing one.
    """
    return SyncService(session, clock).bootstrap(ctx)


@router.get("/operation-types", responses={200: {"model": OperationTypesOut}})
def operation_types(
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
) -> dict[str, Any]:
    """The registry, so a client can verify it rather than assume it."""
    return {
        "allowed": list(ALLOWED_OPERATION_TYPES),
        "offline_finalisation": "refused",
        "reason": (
            "Completing a sale - cash or credit - reduces stock and allocates a "
            "reference, so it requires a connection (ADR-011, ADR-033)."
        ),
    }
