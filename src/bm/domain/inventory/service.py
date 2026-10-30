"""Inventory service — the **sole writer** of stock (M6).

Everything that changes stock goes through `apply_movements`. Nothing else may
`UPDATE inventory_balances`, and a test proves it by patching this one method and running
the entire API suite: if any other path writes the balance table, the patch does not
catch it and the test fails.

Why a single writer rather than careful call sites: NFR-DATA-001 (`balance = Σ
movements`) cannot be expressed as a row constraint. The only way to keep it true is for
every balance change to be inseparable from its ledger row, in the same transaction, in
one function.

Lock order is **ascending `product_id`**, always (ADR-008). Two counters selling the
same two products in opposite orders is the textbook deadlock, and a fixed order is what
makes it structurally impossible rather than rare.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    InsufficientStock,
    OversellNotPermitted,
    OversellReasonRequired,
    ValidationFailed,
)
from bm.domain.shared.money import ZERO, parse_decimal
from bm.infra.db.enums import MovementReason
from bm.infra.db.models.inventory import InventoryBalance, StockMovement

#: Reasons that increase stock. Everything else decreases it. The sign is derived from
#: the reason, never supplied by a caller — a caller that could choose the sign could
#: turn a sale into a receipt.
_INCREASING: frozenset[MovementReason] = frozenset(
    {
        MovementReason.OPENING,
        MovementReason.PURCHASE_RECEIPT,
        MovementReason.SALE_CANCELLATION,
        MovementReason.CUSTOMER_RETURN,
        MovementReason.ADJUSTMENT_INCREASE,
        MovementReason.TRANSFER_IN,
        MovementReason.IMPORT_APPLY,
    }
)

MIN_OVERRIDE_REASON_LENGTH = 10
MIN_ADJUSTMENT_REASON_LENGTH = 3


@dataclass(frozen=True, slots=True)
class MovementRequest:
    """One intended stock change. `quantity` is always **positive**; the direction comes
    from `reason`."""

    product_id: uuid.UUID
    quantity: Decimal
    reason: MovementReason
    source_type: str
    source_id: uuid.UUID
    unit_cost: Decimal | None = None
    oversell_override: bool = False
    override_reason: str | None = None

    @property
    def signed_delta(self) -> Decimal:
        return self.quantity if self.reason in _INCREASING else -self.quantity


@dataclass(frozen=True, slots=True)
class Shortfall:
    product_id: uuid.UUID
    requested: Decimal
    available: Decimal


@dataclass(frozen=True, slots=True)
class StockCheck:
    """The result of locking and checking. `shortfalls` is empty when every line can be
    satisfied from recorded stock."""

    locked_quantities: dict[uuid.UUID, Decimal]
    shortfalls: tuple[Shortfall, ...]

    @property
    def has_shortfall(self) -> bool:
        return bool(self.shortfalls)


class InventoryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- locking and checking -------------------------------------------------

    def lock_and_check(
        self,
        *,
        business_id: uuid.UUID,
        branch_id: uuid.UUID,
        required: dict[uuid.UUID, Decimal],
    ) -> StockCheck:
        """Lock the balance rows for these products and report any shortfall.

        Rows are created on demand with `ON CONFLICT DO NOTHING` so a product that has
        never moved can still be locked — otherwise the first sale of a new product
        would race with itself.

        **Ordered by `product_id` ascending.** This is the first step of the fixed lock
        order; deviating here is what creates deadlocks (ADR-008).
        """
        if not required:
            return StockCheck(locked_quantities={}, shortfalls=())

        product_ids = sorted(required)

        for product_id in product_ids:
            self._session.execute(
                text(
                    "INSERT INTO inventory_balances"
                    " (id, business_id, branch_id, product_id, quantity)"
                    " VALUES (gen_random_uuid(), :business_id, :branch_id, :product_id, 0)"
                    " ON CONFLICT (branch_id, product_id) DO NOTHING"
                ),
                {
                    "business_id": business_id,
                    "branch_id": branch_id,
                    "product_id": product_id,
                },
            )

        rows = self._session.execute(
            select(InventoryBalance.product_id, InventoryBalance.quantity)
            .where(
                InventoryBalance.branch_id == branch_id,
                InventoryBalance.product_id.in_(product_ids),
            )
            .order_by(InventoryBalance.product_id)
            .with_for_update()
        ).all()

        locked = {row[0]: row[1] for row in rows}
        shortfalls = tuple(
            Shortfall(
                product_id=product_id,
                requested=required[product_id],
                available=locked.get(product_id, ZERO),
            )
            for product_id in product_ids
            if locked.get(product_id, ZERO) < required[product_id]
        )
        return StockCheck(locked_quantities=locked, shortfalls=shortfalls)

    @staticmethod
    def assert_oversell_authorized(
        ctx: AuthContext,
        check: StockCheck,
        *,
        override_reason: str | None,
        names: dict[uuid.UUID, str] | None = None,
    ) -> None:
        """Refuse a shortfall unless the caller is authorised **and** gave a reason.

        C-3: overselling is prevented by default; the override exists, is explicit, is
        permissioned, and is audited with a reason that is persisted on both the invoice
        and every movement row it creates.
        """
        if not check.has_shortfall:
            return

        display = names or {}
        details = {
            "lines": [
                {
                    "product_id": str(s.product_id),
                    "name": display.get(s.product_id, ""),
                    "requested": format(s.requested, "f"),
                    "available": format(s.available, "f"),
                }
                for s in check.shortfalls
            ]
        }

        if override_reason is None:
            raise InsufficientStock(
                f"Not enough stock for {len(check.shortfalls)} item"
                f"{'s' if len(check.shortfalls) != 1 else ''}.",
                details=details,
            )
        if not ctx.has(Permission.INVENTORY_OVERSELL_OVERRIDE):
            raise OversellNotPermitted()
        if len(override_reason.strip()) < MIN_OVERRIDE_REASON_LENGTH:
            raise OversellReasonRequired(
                f"Give a reason of at least {MIN_OVERRIDE_REASON_LENGTH} characters."
            )

    # --- the single write path ------------------------------------------------

    def apply_movements(
        self,
        ctx: AuthContext,
        *,
        branch_id: uuid.UUID,
        movements: list[MovementRequest],
    ) -> list[uuid.UUID]:
        """Insert ledger rows and update balances, in one transaction.

        This is the **only** code path that writes `inventory_balances` or
        `stock_movements`. It does not commit — it participates in the caller's
        transaction, so a sale that rolls back takes its stock change with it.

        `balance_after` is read back from the `UPDATE ... RETURNING`, not computed in
        Python, so the ledger records what the database actually holds even under
        concurrency.
        """
        if not movements:
            return []

        # Ascending product_id, consistent with lock_and_check.
        ordered = sorted(movements, key=lambda m: m.product_id)
        created: list[uuid.UUID] = []

        for movement in ordered:
            if movement.quantity <= ZERO:
                raise ValidationFailed(
                    {"quantity": "A stock movement quantity must be greater than zero."}
                )
            if movement.oversell_override and (
                movement.override_reason is None
                or len(movement.override_reason.strip()) < MIN_OVERRIDE_REASON_LENGTH
            ):
                # Mirrors ck_stock_movements__override_reason so the failure is a typed
                # validation error rather than a constraint violation.
                raise OversellReasonRequired()

            self._session.execute(
                text(
                    "INSERT INTO inventory_balances"
                    " (id, business_id, branch_id, product_id, quantity)"
                    " VALUES (gen_random_uuid(), :business_id, :branch_id, :product_id, 0)"
                    " ON CONFLICT (branch_id, product_id) DO NOTHING"
                ),
                {
                    "business_id": ctx.business_id,
                    "branch_id": branch_id,
                    "product_id": movement.product_id,
                },
            )

            balance_after = self._session.execute(
                update(InventoryBalance)
                .where(
                    InventoryBalance.branch_id == branch_id,
                    InventoryBalance.product_id == movement.product_id,
                )
                .values(quantity=InventoryBalance.quantity + movement.signed_delta)
                .returning(InventoryBalance.quantity)
            ).scalar_one()

            movement_id = self._session.execute(
                insert(StockMovement)
                .values(
                    id=uuid.uuid4(),
                    business_id=ctx.business_id,
                    branch_id=branch_id,
                    product_id=movement.product_id,
                    quantity_delta=movement.signed_delta,
                    balance_after=balance_after,
                    reason=str(movement.reason),
                    source_type=movement.source_type,
                    source_id=movement.source_id,
                    unit_cost=movement.unit_cost,
                    oversell_override=movement.oversell_override,
                    override_reason=movement.override_reason,
                    created_by_user_id=ctx.user_id,
                    counter_id=ctx.counter_id,
                    device_id=ctx.device_id,
                )
                .returning(StockMovement.id)
            ).scalar_one()

            self._session.execute(
                update(InventoryBalance)
                .where(
                    InventoryBalance.branch_id == branch_id,
                    InventoryBalance.product_id == movement.product_id,
                )
                .values(last_movement_id=movement_id)
            )
            created.append(movement_id)

            if movement.oversell_override:
                audit.record_for(
                    self._session,
                    ctx,
                    # ⚠️ The audit-action spelling differs from the permission and the
                    # column names on purpose — `DATA-MODEL.md §3.14`.
                    action=AuditAction.INVENTORY_OVERSELL_OVERRIDE,
                    entity_type="stock_movement",
                    entity_id=movement_id,
                    reason=movement.override_reason,
                    after={
                        "product_id": str(movement.product_id),
                        "delta": format(movement.signed_delta, "f"),
                        "balance_after": format(balance_after, "f"),
                    },
                )

        return created

    # --- reads ----------------------------------------------------------------

    def balance_for(self, *, branch_id: uuid.UUID, product_id: uuid.UUID) -> Decimal:
        value = self._session.execute(
            select(InventoryBalance.quantity).where(
                InventoryBalance.branch_id == branch_id,
                InventoryBalance.product_id == product_id,
            )
        ).scalar_one_or_none()
        return value if value is not None else ZERO

    def balances(
        self,
        ctx: AuthContext,
        *,
        product_ids: list[uuid.UUID] | None = None,
        low_stock_only: bool = False,
        limit: int = 200,
    ) -> list[tuple[uuid.UUID, str, Decimal, Decimal | None]]:
        """`(product_id, name, quantity, reorder_level)` for the caller's branch."""
        ctx.require(Permission.CATALOG_READ)
        from bm.infra.db.models.catalog import Product

        query = (
            select(
                InventoryBalance.product_id,
                Product.name,
                InventoryBalance.quantity,
                Product.reorder_level,
            )
            .join(Product, Product.id == InventoryBalance.product_id)
            .where(
                InventoryBalance.business_id == ctx.business_id,
                InventoryBalance.branch_id == ctx.requires_branch,
            )
            .order_by(Product.name)
            .limit(min(limit, 200))
        )
        if product_ids:
            query = query.where(InventoryBalance.product_id.in_(product_ids))
        if low_stock_only:
            query = query.where(
                Product.reorder_level.is_not(None),
                InventoryBalance.quantity <= Product.reorder_level,
            )
        return [(r[0], r[1], r[2], r[3]) for r in self._session.execute(query).all()]

    def movements(
        self,
        ctx: AuthContext,
        *,
        product_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[StockMovement]:
        """The product ledger (FR-INV-001)."""
        ctx.require(Permission.CATALOG_READ)
        query = (
            select(StockMovement)
            .where(StockMovement.business_id == ctx.business_id)
            .order_by(StockMovement.created_at.desc())
            .limit(min(limit, 200))
        )
        if ctx.branch_id is not None:
            query = query.where(StockMovement.branch_id == ctx.branch_id)
        if product_id is not None:
            query = query.where(StockMovement.product_id == product_id)
        return list(self._session.execute(query).scalars().all())

    # --- adjustments ----------------------------------------------------------

    def adjust(
        self,
        ctx: AuthContext,
        *,
        product_id: uuid.UUID,
        quantity_delta: object,
        reason_code: str,
        reason_text: str,
    ) -> uuid.UUID:
        """A reasoned, audited correction (FR-INV-007).

        The reason is **mandatory free text** on top of a reason code, because "stock
        adjusted by -4" with no explanation is exactly the audit gap T-3 describes.
        """
        ctx.require(Permission.INVENTORY_ADJUST)

        delta = parse_decimal(quantity_delta, field_name="quantity_delta")
        if delta == ZERO:
            raise ValidationFailed({"quantity_delta": "An adjustment cannot be zero."})
        if len(reason_text.strip()) < MIN_ADJUSTMENT_REASON_LENGTH:
            raise ValidationFailed(
                {"reason_text": "Say briefly why the stock figure is being corrected."}
            )

        # The direction comes from the sign of the delta, not from the caller's reason
        # code — so a code of `adjustment_increase` with a negative delta cannot record
        # an increase that did not happen.
        reason = (
            MovementReason.ADJUSTMENT_INCREASE
            if delta > ZERO
            else MovementReason.ADJUSTMENT_DECREASE
        )
        if reason_code and reason_code not in {
            str(MovementReason.ADJUSTMENT_INCREASE),
            str(MovementReason.ADJUSTMENT_DECREASE),
            str(MovementReason.SCRAP),
            str(MovementReason.OPENING),
        }:
            raise ValidationFailed(
                {
                    "reason_code": (
                        f"`{reason_code}` is not an adjustment reason. "
                        "A sale, purchase or return is recorded through its own endpoint."
                    )
                }
            )
        if reason_code == str(MovementReason.SCRAP):
            reason = MovementReason.SCRAP
        elif reason_code == str(MovementReason.OPENING):
            reason = MovementReason.OPENING

        branch_id = ctx.requires_branch
        adjustment_id = uuid.uuid4()
        movement_ids = self.apply_movements(
            ctx,
            branch_id=branch_id,
            movements=[
                MovementRequest(
                    product_id=product_id,
                    quantity=abs(delta),
                    reason=reason,
                    source_type="adjustment",
                    source_id=adjustment_id,
                )
            ],
        )
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.INVENTORY_ADJUSTED,
            entity_type="product",
            entity_id=product_id,
            reason=reason_text.strip(),
            after={
                "delta": format(delta, "f"),
                "reason_code": reason_code,
                "movement_id": str(movement_ids[0]),
            },
        )
        return movement_ids[0]

    # --- reconciliation -------------------------------------------------------

    def reconcile(self, ctx: AuthContext) -> dict[str, object]:
        """Compare every balance with the sum of its movements.

        **Reports and never repairs** (FR-INV-006). Drift means there is a bug to find,
        and a self-healing balance table would hide it — which is why this returns a
        report and raises an alert rather than issuing a correcting movement.

        Also performs T3-04's check: for each series, `max(sequence_number) + 1` must
        equal `next_number` for committed invoices. A mismatch indicates a lost update.
        """
        ctx.require(Permission.AUDIT_READ)

        ledger_sum = (
            select(
                StockMovement.branch_id.label("branch_id"),
                StockMovement.product_id.label("product_id"),
                func.sum(StockMovement.quantity_delta).label("ledger_total"),
            )
            .where(StockMovement.business_id == ctx.business_id)
            .group_by(StockMovement.branch_id, StockMovement.product_id)
            .subquery()
        )

        rows = self._session.execute(
            select(
                InventoryBalance.branch_id,
                InventoryBalance.product_id,
                InventoryBalance.quantity,
                func.coalesce(ledger_sum.c.ledger_total, 0),
            )
            .outerjoin(
                ledger_sum,
                (ledger_sum.c.branch_id == InventoryBalance.branch_id)
                & (ledger_sum.c.product_id == InventoryBalance.product_id),
            )
            .where(InventoryBalance.business_id == ctx.business_id)
        ).all()

        discrepancies = [
            {
                "branch_id": str(r[0]),
                "product_id": str(r[1]),
                "balance": format(r[2], "f"),
                "ledger_total": format(r[3], "f"),
                "difference": format(r[2] - r[3], "f"),
            }
            for r in rows
            if r[2] != r[3]
        ]

        numbering = self._session.execute(
            text(
                """
                SELECT s.id::text AS series_id,
                       s.next_number,
                       coalesce(max(i.sequence_number), 0) AS max_sequence
                  FROM invoice_series s
                  LEFT JOIN invoices i ON i.invoice_series_id = s.id
                 WHERE s.business_id = :business_id
                 GROUP BY s.id, s.next_number
                """
            ),
            {"business_id": ctx.business_id},
        ).all()
        numbering_issues = [
            {
                "series_id": r.series_id,
                "next_number": r.next_number,
                "max_sequence": r.max_sequence,
                "expected_next": r.max_sequence + 1,
            }
            for r in numbering
            if r.max_sequence + 1 != r.next_number
        ]

        severity = "critical" if discrepancies or numbering_issues else "info"
        return {
            "balances_checked": len(rows),
            "discrepancies_found": len(discrepancies),
            "discrepancies": discrepancies,
            "numbering_issues": numbering_issues,
            "severity": severity,
            # Stated explicitly so no caller mistakes this for a repair tool.
            "action_taken": "none — discrepancies are reported, never corrected",
        }
