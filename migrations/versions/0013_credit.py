"""payments, allocations and credit notes

Revision ID: 0013
Revises: 0012
Created: 2026-10-30

`payments` is append-only at the privilege level. A mistake is corrected by a
`reversal` row pointing at the original (FR-PAY-005); the original is never edited, so
the history of what was collected and when cannot be rewritten.

**Recording a payment never moves stock** (FR-CREDIT-010). These four tables are the
only ones a payment touches.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
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
        "payments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("counter_id", sa.UUID(), nullable=True),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column(
            "direction",
            postgresql.ENUM("received", "refunded", name="payment_direction", create_type=False),
            server_default=sa.text("'received'::payment_direction"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            postgresql.ENUM("payment", "reversal", name="payment_kind", create_type=False),
            server_default=sa.text("'payment'::payment_kind"),
            nullable=False,
        ),
        sa.Column("reverses_payment_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "method",
            postgresql.ENUM(
                "cash",
                "upi",
                "card",
                "bank_transfer",
                "cheque",
                "adjustment",
                name="payment_method",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("reference", sa.String(length=64), nullable=True),
        sa.Column(
            "allocation_strategy",
            postgresql.ENUM(
                "manual",
                "oldest_first",
                "invoice_specific",
                name="allocation_strategy",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("received_by_user_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'reversal') = (reverses_payment_id IS NOT NULL)", name="ck_payments__reversal"
        ),
        sa.CheckConstraint("amount > 0", name="ck_payments__amount_positive"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["counter_id"], ["counters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["received_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reverses_payment_id"], ["payments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payments__branch_day", "payments", ["branch_id", "received_at"], unique=False
    )
    op.create_index(
        "ix_payments__customer_time",
        "payments",
        ["customer_id", sa.literal_column("received_at DESC")],
        unique=False,
    )
    op.create_table(
        "payment_allocations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_payment_allocations__amount"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", "invoice_id", name="uq_payment_allocations__pair"),
    )
    op.create_index(
        "ix_payment_allocations__invoice", "payment_allocations", ["invoice_id"], unique=False
    )
    op.create_table(
        "credit_notes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("note_number", sa.String(length=64), nullable=False),
        sa.Column(
            "source_type",
            postgresql.ENUM(
                "sales_return",
                "cancellation",
                "write_off",
                "adjustment",
                name="credit_note_source",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("original_invoice_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "open",
                "fully_allocated",
                "refunded",
                "cancelled",
                name="credit_note_status",
                create_type=False,
            ),
            server_default=sa.text("'open'::credit_note_status"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_credit_notes__amount"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["original_invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "note_number", name="uq_credit_notes__number"),
    )
    op.create_index(
        "ix_credit_notes__customer",
        "credit_notes",
        ["customer_id", sa.literal_column("issued_at DESC")],
        unique=False,
    )
    op.create_table(
        "credit_note_allocations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("credit_note_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_credit_note_allocations__amount"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["credit_note_id"], ["credit_notes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "credit_note_id", "invoice_id", name="uq_credit_note_allocations__pair"
        ),
    )

    if _app_role_exists():
        op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON payments FROM bm_app")
        op.execute("GRANT SELECT, INSERT ON payments TO bm_app")
        op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON payment_allocations FROM bm_app")
        op.execute("GRANT SELECT, INSERT ON payment_allocations TO bm_app")


def downgrade() -> None:
    op.drop_table("credit_note_allocations")
    op.execute("DROP INDEX IF EXISTS ix_credit_notes__customer")
    op.drop_table("credit_notes")
    op.execute("DROP INDEX IF EXISTS ix_payment_allocations__invoice")
    op.drop_table("payment_allocations")
    op.execute("DROP INDEX IF EXISTS ix_payments__branch_day")
    op.execute("DROP INDEX IF EXISTS ix_payments__customer_time")
    op.drop_table("payments")
