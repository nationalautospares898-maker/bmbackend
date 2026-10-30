"""customers, phones, vehicles and the derived credit cache

Revision ID: 0008
Revises: 0007
Created: 2026-10-30

`uq_customer_phones__business_phone` is the duplicate **detector** for
FR-CUST-004. It surfaces candidates; it never merges. Automatic merging is forbidden -
the system cannot know that two people sharing a phone number are the same customer.

`ix_customers__name_trgm` supports fuzzy name search for the counter's customer picker.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
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


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("business_name", sa.String(length=200), nullable=True),
        sa.Column(
            "address_lines",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("default_mechanic_name", sa.String(length=200), nullable=True),
        sa.Column("credit_terms_days", sa.Integer(), nullable=True),
        sa.Column("credit_limit", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
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
            "credit_limit IS NULL OR credit_limit >= 0", name="ck_customers__credit_limit"
        ),
        sa.CheckConstraint(
            "credit_terms_days IS NULL OR credit_terms_days >= 0", name="ck_customers__credit_terms"
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_customers__name_present"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "customer_phones",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("normalized_phone", sa.String(length=32), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id", "normalized_phone", name="uq_customer_phones__business_phone"
        ),
    )
    op.create_index(
        "ix_customer_phones__customer", "customer_phones", ["customer_id"], unique=False
    )
    op.create_table(
        "customer_vehicles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_number", sa.String(length=32), nullable=False),
        sa.Column("normalized_vehicle_number", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_customer_vehicles__normalized",
        "customer_vehicles",
        ["business_id", "normalized_vehicle_number"],
        unique=False,
    )
    op.create_table(
        "customer_credit_balances",
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column(
            "outstanding_total",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "on_account_credit",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("oldest_due_date", sa.Date(), nullable=True),
        sa.Column(
            "recomputed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("customer_id"),
    )


def downgrade() -> None:
    op.drop_table("customer_credit_balances")
    op.execute("DROP INDEX IF EXISTS ix_customer_vehicles__normalized")
    op.drop_table("customer_vehicles")
    op.execute("DROP INDEX IF EXISTS ix_customer_phones__customer")
    op.drop_table("customer_phones")
    op.drop_table("customers")
