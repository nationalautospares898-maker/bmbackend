"""Offline synchronisation — server side (M14).

The registry is the whole design. It contains **exactly three** operation types:

    draft.upsert · draft.delete · customer.upsert

**No operation type finalises a sale, records a payment, confirms a purchase, creates a
return or applies an import** (FR-OFFLINE-002, ADR-011). Adding one requires revisiting
ADR-011 **and owner approval**, and `test_sync_registry_contains_no_financial_operation`
(AC-OFF-8) asserts the absence structurally rather than trusting review.

`Credit Bill` is refused offline exactly like `Print Bill`: since ADR-033 it finalises a
sale, reduces stock and allocates a reference. "Credit" is about *who owes money*, never
about *whether the sale happened*.

This module implements **no business logic**. It deduplicates on `op_id`, orders
operations per entity by `client_seq`, and dispatches each to the owning module's normal
service method — so there is exactly one code path for "create draft", online or offline.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from bm.domain.shared.clock import Clock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    DomainError,
    ErrorCode,
    SyncBatchTooLarge,
    SyncCursorExpired,
)
from bm.infra.db.enums import SyncOperationStatus
from bm.infra.db.models.infra import SyncOperation

#: The complete registry. Every entry is replay-safe and may be created offline.
#:
#: ⚠️ Never add a type that moves money or stock. The structural test reads this exact
#: tuple, so an addition fails CI with a message naming ADR-011.
ALLOWED_OPERATION_TYPES: tuple[str, ...] = (
    "draft.upsert",
    "draft.delete",
    "customer.upsert",
)

#: Substrings that indicate a financial operation. Checked by the structural test
#: against `ALLOWED_OPERATION_TYPES` so a plausible-looking addition is caught.
FORBIDDEN_OPERATION_FRAGMENTS: tuple[str, ...] = (
    "sale",
    "finalize",
    "finalise",
    "payment",
    "purchase",
    "return",
    "import",
    "invoice",
    "adjustment",
)

#: Batch cap (`API-SPECIFICATION.md §3.12`).
MAX_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True)
class SyncOperationInput:
    op_id: uuid.UUID
    client_seq: int
    type: str
    entity_type: str
    entity_id: uuid.UUID
    payload: dict[str, Any]
    base_version: int | None = None
    client_created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SyncOperationOutcome:
    op_id: uuid.UUID
    status: str
    server_state: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class SyncBatchResult:
    results: list[SyncOperationOutcome]
    server_time: str


class SyncService:
    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    def apply_batch(
        self, ctx: AuthContext, operations: list[SyncOperationInput]
    ) -> SyncBatchResult:
        """Apply a bounded batch, returning a per-operation outcome.

        The endpoint returns `200` whenever the batch itself was well-formed; individual
        outcomes are in the body (`§3.12`). A bad operation never blocks the rest of the
        batch — **except** that operations for the *same* entity stop at the first
        failure, to preserve intra-entity ordering.
        """
        if len(operations) > MAX_BATCH_SIZE:
            raise SyncBatchTooLarge(details={"max": MAX_BATCH_SIZE, "received": len(operations)})

        # Ordered by the client's sequence per entity. Server time decides *when* it was
        # received (FR-OFFLINE-007), but the client's own sequence decides the order its
        # edits are applied in — a client clock a year out changes nothing.
        ordered = sorted(operations, key=lambda op: (str(op.entity_id), op.client_seq))

        results: list[SyncOperationOutcome] = []
        blocked_entities: set[str] = set()

        for operation in ordered:
            entity_key = str(operation.entity_id)

            if entity_key in blocked_entities:
                # An earlier operation for this entity was not accepted, so this one is
                # skipped rather than applied out of order (`§4.2a`).
                results.append(
                    SyncOperationOutcome(
                        op_id=operation.op_id,
                        status=str(SyncOperationStatus.REJECTED),
                        error_code=str(ErrorCode.SKIPPED_AFTER_EARLIER_FAILURE),
                        error_message=(
                            "Skipped because an earlier change to the same bill needs attention."
                        ),
                    )
                )
                continue

            outcome = self._apply_one(ctx, operation)
            results.append(outcome)
            if outcome.status in {
                str(SyncOperationStatus.CONFLICTED),
                str(SyncOperationStatus.REJECTED),
            }:
                blocked_entities.add(entity_key)

        # Restore the caller's original order so the client can match by index as well
        # as by `op_id`.
        by_id = {r.op_id: r for r in results}
        return SyncBatchResult(
            results=[by_id[op.op_id] for op in operations if op.op_id in by_id],
            server_time=self._clock.now().isoformat(),
        )

    def _apply_one(self, ctx: AuthContext, operation: SyncOperationInput) -> SyncOperationOutcome:
        if operation.type not in ALLOWED_OPERATION_TYPES:
            return SyncOperationOutcome(
                op_id=operation.op_id,
                status=str(SyncOperationStatus.REJECTED),
                error_code=str(ErrorCode.VALIDATION_ERROR),
                error_message=(
                    f"`{operation.type}` is not a synchronisable operation. Completing a "
                    "sale, recording a payment, confirming a purchase, creating a return "
                    "or applying an import all need a connection."
                ),
            )

        # Replay is free: `sync_operations.id` **is** the client's `op_id`, so a whole
        # batch resent after a lost response changes nothing.
        existing = self._session.execute(
            select(SyncOperation.status).where(SyncOperation.id == operation.op_id)
        ).scalar_one_or_none()
        if existing is not None:
            return SyncOperationOutcome(
                op_id=operation.op_id,
                status=str(SyncOperationStatus.REPLAYED),
                server_state=self._server_state(ctx, operation),
            )

        try:
            with self._session.begin_nested():
                self._dispatch(ctx, operation)
                status = SyncOperationStatus.ACCEPTED
                error_code = None
                error_message = None
        except DomainError as exc:
            # A conflict is distinguished from a rejection because the client treats
            # them differently: a conflict opens the resolution dialog, a rejection
            # needs acknowledgement. Neither is ever dropped silently.
            conflicted = exc.code in {
                ErrorCode.DRAFT_VERSION_CONFLICT,
                ErrorCode.CORRUPT_VERSION,
            }
            status = SyncOperationStatus.CONFLICTED if conflicted else SyncOperationStatus.REJECTED
            error_code = str(exc.code)
            error_message = exc.message

        self._record(ctx, operation, status=status, error_code=error_code)
        return SyncOperationOutcome(
            op_id=operation.op_id,
            status=str(status),
            server_state=self._server_state(ctx, operation),
            error_code=error_code,
            error_message=error_message,
        )

    def _dispatch(self, ctx: AuthContext, operation: SyncOperationInput) -> None:
        """Route to the owning module's **normal** service method.

        There is exactly one code path for each of these operations, online or offline.
        A separate offline path would be a second place for the rules to drift.
        """
        payload = operation.payload

        if operation.type == "draft.upsert":
            from bm.domain.sales.drafts import DraftItemInput, DraftService
            from bm.domain.shared.errors import CorruptVersion
            from bm.domain.shared.money import parse_decimal

            service = DraftService(self._session)

            current = self._session.execute(
                text(
                    "SELECT version FROM draft_bills WHERE id = :id AND business_id = :business_id"
                ),
                {"id": operation.entity_id, "business_id": ctx.business_id},
            ).scalar_one_or_none()
            if (
                operation.base_version is not None
                and current is not None
                and operation.base_version > current
            ):
                # Impossible under normal operation: the client claims a version the
                # server never issued (`OFFLINE-SYNCHRONIZATION.md §5.2`).
                raise CorruptVersion()

            items = [
                DraftItemInput(
                    id=uuid.UUID(str(item["id"])),
                    line_no=int(item["line_no"]),
                    product_id=uuid.UUID(str(item["product_id"])),
                    quantity=parse_decimal(item["quantity"], field_name="quantity"),
                    unit_price=parse_decimal(item["unit_price"], field_name="unit_price"),
                    catalog_price_at_add=parse_decimal(
                        item.get("catalog_price_at_add", item["unit_price"]),
                        field_name="catalog_price_at_add",
                    ),
                    price_source=str(item.get("price_source", "catalog")),
                    discount_percent=parse_decimal(
                        item.get("discount_percent", "0"), field_name="discount_percent"
                    ),
                    discount_amount=parse_decimal(
                        item.get("discount_amount", "0"), field_name="discount_amount"
                    ),
                )
                for item in payload.get("items", [])
            ]
            names = {item.product_id: self._product_name(ctx, item.product_id) for item in items}
            service.upsert(
                ctx,
                operation.entity_id,
                counter_id=uuid.UUID(str(payload["counter_id"])),
                items=items,
                product_names=names,
                if_match_version=operation.base_version,
                label=payload.get("label"),
                customer_id=(
                    uuid.UUID(str(payload["customer_id"])) if payload.get("customer_id") else None
                ),
                customer_name=payload.get("customer_name"),
                customer_business_name=payload.get("customer_business_name"),
                vehicle_number=payload.get("vehicle_number"),
                mechanic_name=payload.get("mechanic_name"),
                phone=payload.get("phone"),
                notes=payload.get("notes"),
                is_credit_intent=bool(payload.get("is_credit_intent", False)),
            )
            return

        if operation.type == "draft.delete":
            from bm.domain.sales.drafts import DraftService

            DraftService(self._session).clear(ctx, operation.entity_id)
            return

        if operation.type == "customer.upsert":
            from bm.domain.customers.service import CustomerService

            CustomerService(self._session).upsert(
                ctx,
                operation.entity_id,
                name=str(payload["name"]),
                business_name=payload.get("business_name"),
                phone=payload.get("phone"),
                address_lines=payload.get("address_lines"),
                default_mechanic_name=payload.get("default_mechanic_name"),
                vehicle_number=payload.get("vehicle_number"),
            )
            return

        raise AssertionError(f"unreachable operation type {operation.type}")  # pragma: no cover

    def _product_name(self, ctx: AuthContext, product_id: uuid.UUID) -> str:
        return (
            self._session.execute(
                text("SELECT name FROM products WHERE id = :id AND business_id = :business_id"),
                {"id": product_id, "business_id": ctx.business_id},
            ).scalar_one_or_none()
            or ""
        )

    def _record(
        self,
        ctx: AuthContext,
        operation: SyncOperationInput,
        *,
        status: SyncOperationStatus,
        error_code: str | None,
    ) -> None:
        self._session.execute(
            insert(SyncOperation).values(
                id=operation.op_id,
                business_id=ctx.business_id,
                device_id=ctx.device_id,
                user_id=ctx.user_id,
                operation_type=operation.type,
                entity_type=operation.entity_type,
                entity_id=operation.entity_id,
                client_seq=operation.client_seq,
                base_version=operation.base_version,
                payload=operation.payload,
                status=status,
                error_code=error_code,
                client_created_at=operation.client_created_at,
                server_received_at=self._clock.now(),
            )
        )

    def _server_state(
        self, ctx: AuthContext, operation: SyncOperationInput
    ) -> dict[str, Any] | None:
        """The server's view, returned with every outcome so the client can show both
        versions in a conflict without a second round trip."""
        if operation.entity_type == "draft_bill":
            row = self._session.execute(
                text(
                    "SELECT id, version, status, preview_total FROM draft_bills"
                    " WHERE id = :id AND business_id = :business_id"
                ),
                {"id": operation.entity_id, "business_id": ctx.business_id},
            ).first()
            if row is None:
                return None
            return {
                "id": str(row.id),
                "version": row.version,
                "status": row.status,
                "preview_total": (
                    format(row.preview_total, "f") if row.preview_total is not None else None
                ),
            }
        if operation.entity_type == "customer":
            row = self._session.execute(
                text(
                    "SELECT id, name FROM customers WHERE id = :id AND business_id = :business_id"
                ),
                {"id": operation.entity_id, "business_id": ctx.business_id},
            ).first()
            return {"id": str(row.id), "name": row.name} if row else None
        return None

    # --- catalog delta --------------------------------------------------------

    def catalog_delta(
        self, ctx: AuthContext, *, since: str | None, limit: int = 1000
    ) -> dict[str, Any]:
        """A bounded delta for the offline cache (FR-OFFLINE-008).

        **`cost_price` is never included, regardless of permission.** A stolen counter
        machine must not carry the shop's margins, and the cache is deliberately bounded
        for the same reason (`SECURITY.md §11`).
        """
        from bm.domain.settings.defaults import SettingKey
        from bm.domain.settings.service import SettingsService

        max_rows = int(
            SettingsService(self._session).get(
                ctx.business_id, ctx.branch_id, SettingKey.SYNC_CATALOG_CACHE_MAX_ROWS
            )
        )
        effective = min(limit, max_rows)

        cursor_time: datetime | None = None
        if since:
            try:
                cursor_time = datetime.fromisoformat(since)
            except ValueError as exc:
                raise SyncCursorExpired(
                    "That offline catalog marker is unreadable. Rebuilding."
                ) from exc

        rows = self._session.execute(
            text(
                """
                SELECT p.id, p.name, p.sku, p.barcode, u.code AS unit_code,
                       u.allows_fraction, p.default_selling_price, p.is_active,
                       p.updated_at,
                       coalesce(b.quantity, 0) AS branch_quantity
                  FROM products p
                  JOIN units u ON u.id = p.unit_id
                  LEFT JOIN inventory_balances b
                         ON b.product_id = p.id AND b.branch_id = :branch_id
                 WHERE p.business_id = :business_id
                   AND (CAST(:since AS timestamptz) IS NULL OR p.updated_at > CAST(:since AS timestamptz))
                 ORDER BY p.updated_at, p.id
                 LIMIT :limit
                """
            ),
            {
                "business_id": ctx.business_id,
                "branch_id": ctx.branch_id,
                "since": cursor_time,
                "limit": effective + 1,
            },
        ).all()

        truncated = len(rows) > effective
        page = rows[:effective]
        as_of = self._clock.now().isoformat()

        warnings: list[dict[str, Any]] = []
        if truncated:
            warnings.append(
                {
                    "code": "CATALOG_CACHE_TRUNCATED",
                    "message": "Offline search will be incomplete until the next sync.",
                    "details": {"max_rows": effective},
                }
            )

        return {
            "products": [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "sku": r.sku,
                    "barcode": r.barcode,
                    "unit": {"code": r.unit_code, "allows_fraction": r.allows_fraction},
                    "default_selling_price": format(r.default_selling_price, "f"),
                    "is_active": r.is_active,
                    "stock": {
                        "branch_quantity": format(r.branch_quantity, "f"),
                        # Every cached figure carries its `as_of` time, so the client
                        # can show how stale it is rather than implying it is live.
                        "as_of": as_of,
                    },
                }
                for r in page
            ],
            "next_cursor": page[-1].updated_at.isoformat() if page else since,
            "has_more": truncated,
            "reset_required": False,
            "cache_policy": {"max_rows": effective},
            "as_of": as_of,
            "warnings": warnings,
        }

    def _resolve_counter(self, ctx: AuthContext) -> dict[str, Any] | None:
        """The counter this request is working at, **scoped to the caller's business**.

        `ctx.counter_id` is already resolved for us by `AuthService.resolve_context`,
        which takes the first of `X-Counter-Id`, the token's claim, and the registered
        device's own `counter_id`. So a provisioned counter PC needs no header: the
        device knows which till it is.

        The tenant scope is the fix here. `X-Counter-Id` is **caller-supplied** and
        `resolve_context` does not check that it belongs to the caller's business, while
        this lookup used to be `WHERE id = :id` with no filter at all. An authenticated
        user of one shop could therefore send another shop's counter id and read back
        that counter's **code and name** — a cross-tenant disclosure of exactly the kind
        `SECURITY.md §3.2` and AC-8 exist to prevent. Narrow, because only two fields
        leak, but a leak is a leak, and it is the sort that grows the next time a field
        is added to this payload.

        Scoped, a foreign or unknown id is simply absent (§4.1) — never a 403, which
        would confirm that it exists.
        """
        if ctx.counter_id is None:
            return None
        row = self._session.execute(
            text(
                "SELECT code, name, max_open_drafts FROM counters"
                " WHERE id = :id AND business_id = :business_id"
            ),
            {"id": ctx.counter_id, "business_id": ctx.business_id},
        ).first()
        if row is None:
            return None
        return {
            "id": str(ctx.counter_id),
            "code": row.code,
            "name": row.name,
            "max_open_drafts": row.max_open_drafts,
        }

    def bootstrap(self, ctx: AuthContext) -> dict[str, Any]:
        """One call after login (`API-SPECIFICATION.md §3.12`).

        Publishes the **money policy** so the client never hardcodes a rounding rule
        (`../bmfrontend/CLAUDE.md §5` rule 15), plus the client-visible settings, the
        print profile and the date order.
        """
        from bm.domain.settings.defaults import SPECS
        from bm.domain.settings.service import SettingsService

        settings_service = SettingsService(self._session)
        resolved = settings_service.resolve_all(ctx.business_id, ctx.branch_id)
        policy = settings_service.money_policy(ctx.business_id, ctx.branch_id)

        client_settings = {
            str(key): (str(value) if hasattr(value, "quantize") else value)
            for key, value in resolved.items()
            if SPECS[key].client_visible
        }

        counter = self._resolve_counter(ctx)

        return {
            "business_id": str(ctx.business_id),
            "branch_id": str(ctx.branch_id) if ctx.branch_id else None,
            "counter": counter,
            "permissions": sorted(ctx.permissions),
            "money_policy": policy.as_payload(),
            "settings": client_settings,
            "sync": {
                "allowed_operation_types": list(ALLOWED_OPERATION_TYPES),
                # Stated explicitly so the client can show the reason rather than
                # inventing one.
                "offline_finalisation": "refused",
                "max_batch_size": MAX_BATCH_SIZE,
            },
            "server_time": self._clock.now().isoformat(),
            "catalog_cursor": None,
        }
