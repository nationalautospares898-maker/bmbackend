"""suppliers and purchases

Revision ID: 0009
Revises: 0008
Created: 2026-10-30

`uq_purchases__supplier_doc` blocks double-receiving the same supplier invoice
(FR-PUR-006). The partial index means a purchase with no document number is still
allowed, which is the common case for a cash purchase.

Creating a purchase changes no stock. Only the explicit `draft -> confirmed` transition
does (FR-PUR-003).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
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
        "suppliers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("business_name", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column(
            "address_lines",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("tax_identifier", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "normalized_name", name="uq_suppliers__business_name"),
    )
    op.create_table(
        "purchases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("supplier_document_number", sa.String(length=64), nullable=True),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "draft", "confirmed", "cancelled", name="purchase_status", create_type=False
            ),
            server_default=sa.text("'draft'::purchase_status"),
            nullable=False,
        ),
        sa.Column(
            "subtotal",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "tax_total",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total", sa.Numeric(precision=14, scale=2), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("import_job_id", sa.UUID(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
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
            "(status = 'confirmed') = (confirmed_at IS NOT NULL)", name="ck_purchases__confirmed"
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_purchases__supplier_time",
        "purchases",
        ["supplier_id", sa.literal_column("received_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_purchases__supplier_doc",
        "purchases",
        ["business_id", "supplier_id", "supplier_document_number"],
        unique=True,
        postgresql_where=sa.text("supplier_document_number IS NOT NULL"),
    )
    op.create_table(
        "purchase_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("purchase_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("product_name_snapshot", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_purchase_items__quantity"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_purchase_items__cost"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["purchase_id"], ["purchases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("purchase_id", "line_no", name="uq_purchase_items__line"),
    )


def downgrade() -> None:
    op.drop_table("purchase_items")
    op.execute("DROP INDEX IF EXISTS ix_purchases__supplier_time")
    op.execute("DROP INDEX IF EXISTS uq_purchases__supplier_doc")
    op.drop_table("purchases")
    op.drop_table("suppliers")
