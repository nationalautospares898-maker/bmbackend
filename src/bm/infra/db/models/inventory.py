"""Inventory tables (migration 0008).

`inventory_balances` and `stock_movements`.

Two properties carry the whole inventory design:

* `inventory_balances` is **one row per product per branch**, and it is the row locked
  `FOR UPDATE` during finalisation. One row keeps the lock set small and orderable,
  which is what makes the fixed lock order (ascending `product_id`) able to prevent
  deadlocks at all.
* `stock_movements` is **append-only at the privilege level** — the application role is
  granted `INSERT`/`SELECT` only. That makes NFR-DATA-006 structural rather than
  aspirational: no bug, no careless migration using the app role, and no cashier can
  rewrite stock history.

NFR-DATA-001 (`balance = Σ movements`) cannot be a single-row `CHECK`, so it is held by
a single-writer service plus a nightly reconciliation job that **alerts rather than
repairs** — an auto-healing balance would hide the bug that caused the drift.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bm.infra.db.base import (
    QUANTITY,
    SHORT_TEXT,
    UNIT_PRICE,
    Base,
    created_at_column,
    updated_at_column,
)
from bm.infra.db.enums import MOVEMENT_REASON, STOCK_COUNT_STATUS


class InventoryBalance(Base):
    __tablename__ = "inventory_balances"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    #: May be negative **only** via an audited oversell override (NFR-DATA-007).
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False, server_default=text("0"))
    #: A cheap staleness check during reconciliation.
    last_movement_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint("branch_id", "product_id", name="uq_inventory_balances__branch_product"),
    )


class StockMovement(Base):
    """Append-only ledger. Every balance change has a row here in the same transaction."""

    __tablename__ = "stock_movements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    #: Signed. The sign is derived from `reason`, never supplied by a caller.
    quantity_delta: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    #: Written in the same transaction; makes the ledger readable without replaying it.
    balance_after: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    reason: Mapped[str] = mapped_column(MOVEMENT_REASON, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The document that caused it.
    source_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(UNIT_PRICE, nullable=True)

    #: ⚠️ `stock_movements.oversell_override` is the **database-column** spelling of the
    #: oversell concept. The permission is `inventory.oversell.override`, the audit
    #: action is `inventory.oversell_override`, and the settings key is
    #: `inventory.allow_oversell_override`. `DATA-MODEL.md §3.14` is authoritative.
    oversell_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    override_reason: Mapped[str | None] = mapped_column(SHORT_TEXT, nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    counter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counters.id", ondelete="RESTRICT"), nullable=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("quantity_delta <> 0", name="ck_stock_movements__nonzero"),
        CheckConstraint(
            "NOT oversell_override OR length(override_reason) >= 10",
            name="ck_stock_movements__override_reason",
        ),
    )


class StockCountSession(Base):
    """P1. Applying a session creates adjustment movements in one transaction."""

    __tablename__ = "stock_count_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        STOCK_COUNT_STATUS, nullable=False, server_default=text("'open'::stock_count_status")
    )
    counted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(SHORT_TEXT, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class StockCountItem(Base):
    __tablename__ = "stock_count_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    stock_count_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stock_count_sessions.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    system_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    counted_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)

    __table_args__ = (
        UniqueConstraint("stock_count_session_id", "product_id", name="uq_stock_count_items__pair"),
    )


class ReconciliationRun(Base):
    """The discrepancy report from the nightly job (FR-INV-006).

    It **reports and never repairs**: drift means there is a bug to find, and a
    self-healing balance table would hide it.
    """

    __tablename__ = "reconciliation_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime] = created_at_column()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    balances_checked: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    discrepancies_found: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    #: Per-discrepancy detail, including the numbering check from T3-04.
    findings: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)


# Indexes declared outside the classes so the model and the migration agree on their
# names in one place; the invariant tests assert these names exist.
Index(
    "ix_inventory_balances__low",
    InventoryBalance.branch_id,
    InventoryBalance.product_id,
    postgresql_include=["quantity"],
)
Index(
    "ix_stock_movements__product_time",
    StockMovement.branch_id,
    StockMovement.product_id,
    StockMovement.created_at.desc(),
)
Index("ix_stock_movements__source", StockMovement.source_type, StockMovement.source_id)
#: The `0017` child-table tenancy index. See the note in `catalog.py`.
Index(
    "ix_stock_count_items__business",
    StockCountItem.business_id,
    StockCountItem.stock_count_session_id,
)
