"""Physical stock count (Feature A.6).

`stock_count_sessions`/`stock_count_items` (migration 0008) existed with no service or
route ever built against them. This is that service.

## The workflow, and why it is not "overwrite the shelf figure"

1. **Start** a session for the caller's branch. Only one open session per branch at a
   time — two concurrent counts of the same shelf would double-apply or disagree, and
   refusing a second `start` is cheaper than reconciling two counts afterwards.
2. **Record** a counted quantity per product, as many times as needed, in any order.
   Nothing is written to the ledger yet — counting is observation, not mutation.
3. **Review** shows every product with a balance in the branch: the counted quantity
   (null if not yet counted), the *current* system quantity read fresh, and the
   discrepancy. "Current", not "what the system said when you counted it a minute
   ago" — a sale that happened mid-count must be visible before anyone finalises
   against it.
4. **Finalize** claims the session (`status: open -> applied`, guarded so a retried or
   double-submitted finalize is a no-op, matching every other idempotent transition in
   this codebase) and, for every *counted* item, re-reads the balance one more time —
   inside the same transaction `InventoryService.apply_movements` locks it under — and
   writes exactly one adjustment movement for whatever the discrepancy still is at that
   instant. Uncounted products are left alone entirely: a stock count is a statement
   about what was actually counted, not an inference about what was not.

Movements use the existing `adjustment_increase`/`adjustment_decrease` reasons with
`source_type='stock_count'` — the same pattern that already tells a correction made
through the Stock sheet apart from one made through the Adjust dialog, reused here
rather than adding a new reason that would need a migration.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import Permission
from bm.domain.inventory.service import InventoryService, MovementRequest
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import DomainError, ErrorCode, NotFound, ValidationFailed
from bm.infra.db.enums import MovementReason
from bm.infra.db.rows import rows_affected

ZERO = Decimal("0")


class StockCountAlreadyOpen(DomainError):
    code = ErrorCode.STOCK_COUNT_ALREADY_OPEN
    status_code = 409
    message = "A stock count is already open for this branch. Finish or cancel it first."


@dataclass(frozen=True, slots=True)
class StockCountItemView:
    product_id: uuid.UUID
    name: str
    sku: str | None
    system_quantity: Decimal
    counted_quantity: Decimal | None

    @property
    def discrepancy(self) -> Decimal | None:
        if self.counted_quantity is None:
            return None
        return self.counted_quantity - self.system_quantity


@dataclass(frozen=True, slots=True)
class StockCountSessionView:
    id: uuid.UUID
    status: str
    notes: str | None
    created_at: str
    applied_at: str | None


class StockCountService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _own_session(self, ctx: AuthContext, session_id: uuid.UUID) -> StockCountSessionView:
        row = self._session.execute(
            text(
                "SELECT id, status, notes, created_at, applied_at FROM stock_count_sessions"
                " WHERE id = :id AND business_id = :business_id"
            ),
            {"id": session_id, "business_id": ctx.business_id},
        ).first()
        if row is None:
            raise NotFound("That stock count does not exist.")
        return StockCountSessionView(
            id=row.id,
            status=str(row.status),
            notes=row.notes,
            created_at=row.created_at.isoformat(),
            applied_at=row.applied_at.isoformat() if row.applied_at else None,
        )

    def get_session(self, ctx: AuthContext, session_id: uuid.UUID) -> StockCountSessionView:
        ctx.require(Permission.INVENTORY_ADJUST)
        return self._own_session(ctx, session_id)

    def list_sessions(self, ctx: AuthContext, *, limit: int = 20) -> list[StockCountSessionView]:
        ctx.require(Permission.INVENTORY_ADJUST)
        rows = self._session.execute(
            text(
                "SELECT id, status, notes, created_at, applied_at FROM stock_count_sessions"
                " WHERE business_id = :business_id ORDER BY created_at DESC LIMIT :limit"
            ),
            {"business_id": ctx.business_id, "limit": limit},
        ).all()
        return [
            StockCountSessionView(
                id=r.id,
                status=str(r.status),
                notes=r.notes,
                created_at=r.created_at.isoformat(),
                applied_at=r.applied_at.isoformat() if r.applied_at else None,
            )
            for r in rows
        ]

    def start(self, ctx: AuthContext, *, notes: str | None) -> uuid.UUID:
        ctx.require(Permission.INVENTORY_ADJUST)
        branch_id = ctx.requires_branch
        existing = self._session.execute(
            text(
                "SELECT 1 FROM stock_count_sessions"
                " WHERE business_id = :business_id AND branch_id = :branch_id AND status = 'open'"
            ),
            {"business_id": ctx.business_id, "branch_id": branch_id},
        ).first()
        if existing is not None:
            raise StockCountAlreadyOpen()

        session_id = uuid.uuid4()
        self._session.execute(
            text(
                """
                INSERT INTO stock_count_sessions
                    (id, business_id, branch_id, counted_by_user_id, notes)
                VALUES (:id, :business_id, :branch_id, :user_id, :notes)
                """
            ),
            {
                "id": session_id,
                "business_id": ctx.business_id,
                "branch_id": branch_id,
                "user_id": ctx.user_id,
                "notes": notes,
            },
        )
        return session_id

    def record_count(
        self,
        ctx: AuthContext,
        session_id: uuid.UUID,
        *,
        product_id: uuid.UUID,
        counted_quantity: Decimal,
    ) -> None:
        ctx.require(Permission.INVENTORY_ADJUST)
        if counted_quantity < ZERO:
            raise ValidationFailed({"counted_quantity": "Cannot be negative."})
        branch_id = ctx.requires_branch
        status = self._session.execute(
            text(
                "SELECT status FROM stock_count_sessions"
                " WHERE id = :id AND business_id = :business_id AND branch_id = :branch_id"
            ),
            {"id": session_id, "business_id": ctx.business_id, "branch_id": branch_id},
        ).scalar_one_or_none()
        if status is None:
            raise NotFound("That stock count does not exist.")
        if status != "open":
            raise ValidationFailed({"session": "This stock count is no longer open."})

        system_quantity = InventoryService(self._session).balance_for(
            branch_id=branch_id, product_id=product_id
        )
        self._session.execute(
            text(
                """
                INSERT INTO stock_count_items
                    (id, stock_count_session_id, business_id, product_id,
                     system_quantity, counted_quantity)
                VALUES (:id, :session_id, :business_id, :product_id, :system_qty, :counted_qty)
                ON CONFLICT (stock_count_session_id, product_id)
                DO UPDATE SET system_quantity = :system_qty, counted_quantity = :counted_qty
                """
            ),
            {
                "id": uuid.uuid4(),
                "session_id": session_id,
                "business_id": ctx.business_id,
                "product_id": product_id,
                "system_qty": system_quantity,
                "counted_qty": counted_quantity,
            },
        )

    def review(self, ctx: AuthContext, session_id: uuid.UUID) -> list[StockCountItemView]:
        """Every product with a balance in this branch — counted or not — with the
        **current** system quantity, read fresh rather than from whatever was stored
        when (if) it was counted."""
        ctx.require(Permission.INVENTORY_ADJUST)
        branch_id = ctx.requires_branch
        self._own_session(ctx, session_id)  # 404s on a cross-tenant id

        rows = self._session.execute(
            text(
                """
                SELECT p.id AS product_id, p.name, p.sku, ib.quantity AS system_quantity,
                       sci.counted_quantity
                  FROM inventory_balances ib
                  JOIN products p ON p.id = ib.product_id
                  LEFT JOIN stock_count_items sci
                         ON sci.stock_count_session_id = :session_id
                        AND sci.product_id = ib.product_id
                 WHERE ib.business_id = :business_id AND ib.branch_id = :branch_id
                 ORDER BY p.name
                """
            ),
            {"session_id": session_id, "business_id": ctx.business_id, "branch_id": branch_id},
        ).all()
        return [
            StockCountItemView(
                product_id=r.product_id,
                name=r.name,
                sku=r.sku,
                system_quantity=r.system_quantity,
                counted_quantity=r.counted_quantity,
            )
            for r in rows
        ]

    def cancel(self, ctx: AuthContext, session_id: uuid.UUID) -> None:
        ctx.require(Permission.INVENTORY_ADJUST)
        result = self._session.execute(
            text(
                "UPDATE stock_count_sessions SET status = 'cancelled', updated_at = now()"
                " WHERE id = :id AND business_id = :business_id AND status = 'open'"
            ),
            {"id": session_id, "business_id": ctx.business_id},
        )
        if not rows_affected(result):
            raise ValidationFailed({"session": "This stock count is not open."})

    def finalize(self, ctx: AuthContext, session_id: uuid.UUID) -> dict[str, int]:
        """Apply every counted item's discrepancy as exactly one adjustment movement
        each, against the balance **at this instant** — not whatever was snapshotted
        while counting."""
        ctx.require(Permission.INVENTORY_ADJUST)
        branch_id = ctx.requires_branch

        # Claim first: a session already `applied` cannot be applied a second time,
        # the same guard every other idempotent transition in this codebase uses.
        claimed = self._session.execute(
            text(
                "UPDATE stock_count_sessions SET status = 'applied', applied_at = now(),"
                " updated_at = now()"
                " WHERE id = :id AND business_id = :business_id AND branch_id = :branch_id"
                " AND status = 'open'"
            ),
            {"id": session_id, "business_id": ctx.business_id, "branch_id": branch_id},
        )
        if not rows_affected(claimed):
            raise ValidationFailed({"session": "This stock count is not open."})

        counted = self._session.execute(
            text(
                "SELECT product_id, counted_quantity FROM stock_count_items"
                " WHERE stock_count_session_id = :id AND business_id = :business_id"
            ),
            {"id": session_id, "business_id": ctx.business_id},
        ).all()

        inventory = InventoryService(self._session)
        movements: list[MovementRequest] = []
        for row in counted:
            live_system = inventory.balance_for(branch_id=branch_id, product_id=row.product_id)
            delta = row.counted_quantity - live_system
            if delta == ZERO:
                continue
            movements.append(
                MovementRequest(
                    product_id=row.product_id,
                    quantity=abs(delta),
                    reason=(
                        MovementReason.ADJUSTMENT_INCREASE
                        if delta > ZERO
                        else MovementReason.ADJUSTMENT_DECREASE
                    ),
                    source_type="stock_count",
                    source_id=session_id,
                )
            )
        inventory.apply_movements(ctx, branch_id=branch_id, movements=movements)
        return {"movements": len(movements), "counted": len(counted)}
