"""sales returns

Revision ID: 0014
Revises: 0013
Created: 2026-10-30

`ck_invoice_items__returned` (migration 0011) is what makes NFR-DATA-008
enforceable: cumulative returns cannot exceed the quantity sold, and the constraint is
exercised directly by a test rather than trusted.

A return at `unit_price_snapshot` credits what the customer actually paid, not today's
price.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
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
        "sales_returns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("return_number", sa.String(length=64), nullable=False),
        sa.Column(
            "returned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("restock", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("credit_note_id", sa.UUID(), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.CheckConstraint("total_amount > 0", name="ck_sales_returns__amount"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["credit_note_id"], ["credit_notes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "return_number", name="uq_sales_returns__number"),
    )
    op.create_index("ix_sales_returns__invoice", "sales_returns", ["invoice_id"], unique=False)
    op.create_table(
        "sales_return_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("sales_return_id", sa.UUID(), nullable=False),
        sa.Column("invoice_item_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit_price_snapshot", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("line_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "condition",
            postgresql.ENUM("resalable", "damaged", name="return_condition", create_type=False),
            nullable=False,
        ),
        sa.CheckConstraint("quantity > 0", name="ck_sales_return_items__quantity"),
        sa.ForeignKeyConstraint(["invoice_item_id"], ["invoice_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sales_return_id"], ["sales_returns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sales_return_id", "invoice_item_id", name="uq_sales_return_items__pair"
        ),
    )


def downgrade() -> None:
    op.drop_table("sales_return_items")
    op.execute("DROP INDEX IF EXISTS ix_sales_returns__invoice")
    op.drop_table("sales_returns")
