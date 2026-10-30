"""inventory balances and the append-only movement ledger

Revision ID: 0007
Revises: 0006
Created: 2026-10-30

`stock_movements` is granted `INSERT`/`SELECT` only, and the `REVOKE` ships **in
the same migration as the table** - so there is never a window in which NFR-DATA-006 is
untested, and a window is all a bug needs.

This is what makes T-3 (stock written down to hide shrinkage) structural: no code path,
no careless migration using the app role, and no cashier can rewrite stock history. A
correction is a new, reasoned, audited movement.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "bm_app"


def _app_role_exists() -> bool:
    """Local development sometimes runs everything as one role.

    The grants are skipped rather than failing the migration, and the privilege tests
    skip with a clear reason when the role is absent - so nobody sees a green
    immutability test that proved nothing.
    """
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
        ).first()
    )


# Every ENUM type was created in migration 0001, so each reference here carries
# `create_type=False`. Without it Alembic would emit a `CREATE TYPE` per column and
# the migration would fail on the second table that uses the same type.


def upgrade() -> None:
    op.create_table(
        "inventory_balances",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column(
            "quantity",
            sa.Numeric(precision=14, scale=3),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_movement_id", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "branch_id", "product_id", name="uq_inventory_balances__branch_product"
        ),
    )
    op.create_index(
        "ix_inventory_balances__low",
        "inventory_balances",
        ["branch_id", "product_id"],
        unique=False,
        postgresql_include=["quantity"],
    )
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("quantity_delta", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("balance_after", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column(
            "reason",
            postgresql.ENUM(
                "opening",
                "purchase_receipt",
                "sale",
                "sale_cancellation",
                "customer_return",
                "supplier_return",
                "adjustment_increase",
                "adjustment_decrease",
                "transfer_out",
                "transfer_in",
                "import_apply",
                "scrap",
                name="movement_reason",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column(
            "oversell_override", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("override_reason", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("counter_id", sa.UUID(), nullable=True),
        sa.Column("device_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "NOT oversell_override OR length(override_reason) >= 10",
            name="ck_stock_movements__override_reason",
        ),
        sa.CheckConstraint("quantity_delta <> 0", name="ck_stock_movements__nonzero"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["counter_id"], ["counters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stock_movements__product_time",
        "stock_movements",
        ["branch_id", "product_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_stock_movements__source", "stock_movements", ["source_type", "source_id"], unique=False
    )
    op.create_table(
        "stock_count_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "open", "applied", "cancelled", name="stock_count_status", create_type=False
            ),
            server_default=sa.text("'open'::stock_count_status"),
            nullable=False,
        ),
        sa.Column("counted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["counted_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "stock_count_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("stock_count_session_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("system_quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("counted_quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["stock_count_session_id"], ["stock_count_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stock_count_session_id", "product_id", name="uq_stock_count_items__pair"
        ),
    )
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("balances_checked", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("discrepancies_found", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )

    if _app_role_exists():
        op.execute("GRANT SELECT, INSERT ON stock_movements TO bm_app")
        op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON stock_movements FROM bm_app")


def downgrade() -> None:
    op.drop_table("reconciliation_runs")
    op.drop_table("stock_count_items")
    op.drop_table("stock_count_sessions")
    op.execute("DROP INDEX IF EXISTS ix_stock_movements__product_time")
    op.execute("DROP INDEX IF EXISTS ix_stock_movements__source")
    op.drop_table("stock_movements")
    op.execute("DROP INDEX IF EXISTS ix_inventory_balances__low")
    op.drop_table("inventory_balances")
