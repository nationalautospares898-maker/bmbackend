"""tenancy: businesses, branches, counters, settings

Revision ID: 0002
Revises: 0001
Created: 2026-10-30

Includes T3-02 (`businesses.timezone`) and fix S-3 (the expression unique index on the
settings scope, so a NULL `branch_id` is a comparable scope value).

**Nothing is seeded here.** No business, no tax rate, no product, no demo data — ever
(`CLAUDE.md §7`). The owner's business identity is theirs to supply.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NIL_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.create_table(
        "businesses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Nullable until the owner supplies it (OD-4). A blank value blocks go-live,
        # not startup, so staff can log in and fill it in.
        sa.Column("legal_name", sa.String(200), nullable=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column(
            "address_lines",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "phone_numbers",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("email", sa.String(200), nullable=True),
        # GSTIN when confirmed. Null while tax.mode = estimate_only. Never invented.
        sa.Column("tax_identifier", sa.String(32), nullable=True),
        sa.Column("tax_identifier_type", sa.String(32), nullable=True),
        sa.Column("state_code", sa.String(8), nullable=True),
        sa.Column(
            "financial_year_start_month",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("4"),
        ),
        # T3-02: the financial-year and report day boundaries are evaluated in
        # business-local time. Without this they would silently use UTC and a late
        # 31 March sale would land in the wrong financial year.
        sa.Column(
            "timezone",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'Asia/Kolkata'"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "financial_year_start_month BETWEEN 1 AND 12",
            name="ck_businesses__fy_start_month",
        ),
    )

    op.create_table(
        "branches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "address_lines",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], name="fk_branches__business", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("business_id", "code", name="uq_branches__business_code"),
    )

    op.create_table(
        "counters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("max_open_drafts", sa.Integer(), nullable=False, server_default=sa.text("20")),
        # FK added in 0010, when invoice_series exists. Per-counter series must not be
        # used without re-checking OD-25 first.
        sa.Column("invoice_series_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], name="fk_counters__business", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"], ["branches.id"], name="fk_counters__branch", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("branch_id", "code", name="uq_counters__branch_code"),
        # C-5 is a floor, not a default.
        sa.CheckConstraint("max_open_drafts >= 20", name="ck_counters__min_drafts"),
    )

    op.create_table(
        "settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("value_type", sa.String(32), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], name="fk_settings__business", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"], ["branches.id"], name="fk_settings__branch", ondelete="RESTRICT"
        ),
    )

    # S-3. A plain UNIQUE (business_id, branch_id, key) would permit two business-wide
    # rows for the same key, because NULL is not equal to NULL in SQL.
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_settings__scope_key
            ON settings (business_id, coalesce(branch_id, '{NIL_UUID}'::uuid), key)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_settings__scope_key")
    op.drop_table("settings")
    op.drop_table("counters")
    op.drop_table("branches")
    op.drop_table("businesses")
