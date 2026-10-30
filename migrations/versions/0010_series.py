"""invoice series

Revision ID: 0010
Revises: 0009
Created: 2026-10-30

One row per (business, branch, series, financial year). `next_number` is locked
`FOR UPDATE` inside the finalisation transaction, so a rolled-back sale consumes no
number (NFR-DATA-005).

The deferred foreign key from `counters.invoice_series_id` is added here, now that the
target table exists. Per-counter series remain the documented escalation path and must
not be used without re-checking OD-25 first.

WARNING: the reference this produces is **internal only**. OD-3 is re-answered - no
customer-visible invoice numbering. It is retained for audit, reconciliation, stable
ordering and the OD-25 contingency.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
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
        "invoice_series",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("series_code", sa.String(length=32), nullable=False),
        sa.Column("financial_year", sa.String(length=16), nullable=False),
        sa.Column("prefix_template", sa.String(length=64), nullable=False),
        sa.Column("next_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "document_kind",
            postgresql.ENUM("estimate", "tax_invoice", name="document_kind", create_type=False),
            server_default=sa.text("'estimate'::document_kind"),
            nullable=False,
        ),
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
        sa.CheckConstraint("next_number >= 1", name="ck_invoice_series__next_number"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id",
            "branch_id",
            "series_code",
            "financial_year",
            name="uq_invoice_series__scope",
        ),
    )

    if _app_role_exists():
        op.execute(
            "ALTER TABLE counters ADD CONSTRAINT fk_counters__invoice_series FOREIGN KEY (invoice_series_id) REFERENCES invoice_series (id) ON DELETE SET NULL"
        )


def downgrade() -> None:
    op.execute("ALTER TABLE counters DROP CONSTRAINT IF EXISTS fk_counters__invoice_series")
    op.drop_table("invoice_series")
