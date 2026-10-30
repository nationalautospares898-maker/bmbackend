"""draft bills

Revision ID: 0012
Revises: 0011
Created: 2026-10-30

A draft is never a sale: it reserves no stock (ADR-006) and holds no number.

`uq_draft_bills__invoice` is what makes double-finalisation impossible at the database
level, and `ck_draft_bills__finalized` stops the two halves of that state from
disagreeing.

The foreign key to `invoices` is why this follows 0011 rather than preceding it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
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
        "draft_bills",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("counter_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "open", "finalized", "cancelled", "stale", name="draft_status", create_type=False
            ),
            server_default=sa.text("'open'::draft_status"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("customer_name", sa.String(length=200), nullable=True),
        sa.Column("customer_business_name", sa.String(length=200), nullable=True),
        sa.Column("vehicle_number", sa.String(length=32), nullable=True),
        sa.Column("mechanic_name", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column(
            "is_credit_intent", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("preview_total", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("invoice_id", sa.UUID(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("last_edited_by_user_id", sa.UUID(), nullable=True),
        sa.Column("device_id", sa.UUID(), nullable=True),
        sa.Column("client_created_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "(status = 'finalized') = (invoice_id IS NOT NULL)", name="ck_draft_bills__finalized"
        ),
        sa.CheckConstraint("version >= 1", name="ck_draft_bills__version"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["counter_id"], ["counters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["last_edited_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_draft_bills__open",
        "draft_bills",
        ["counter_id", "status"],
        unique=False,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "uq_draft_bills__invoice",
        "draft_bills",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text("invoice_id IS NOT NULL"),
    )
    op.create_table(
        "draft_bill_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("draft_bill_id", sa.UUID(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("product_name_snapshot", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("catalog_price_at_add", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column(
            "price_source",
            postgresql.ENUM("catalog", "manual_override", name="price_source", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "discount_percent",
            sa.Numeric(precision=6, scale=3),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "discount_amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("line_total_preview", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_draft_bill_items__quantity"),
        sa.CheckConstraint("unit_price >= 0", name="ck_draft_bill_items__price"),
        sa.ForeignKeyConstraint(["draft_bill_id"], ["draft_bills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_bill_id", "line_no", name="uq_draft_bill_items__line"),
    )


def downgrade() -> None:
    op.drop_table("draft_bill_items")
    op.execute("DROP INDEX IF EXISTS ix_draft_bills__open")
    op.execute("DROP INDEX IF EXISTS uq_draft_bills__invoice")
    op.drop_table("draft_bills")
