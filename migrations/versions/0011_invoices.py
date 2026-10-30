"""invoices, invoice items and the print log

Revision ID: 0011
Revises: 0010
Created: 2026-10-30

The column-level grants here are the control that makes T-1 structural - a
cashier editing a past invoice to pocket cash.

`DELETE` is revoked outright. `UPDATE` is granted **only** on the enumerated
cancellation fields, the derived payment cache and `updated_at`; on `invoice_items`,
only `returned_quantity`. Everything else is impossible to change, including by a future
bug or a careless migration running as the app role.

`ck_invoices__total` makes NFR-DATA-002 a database guarantee rather than a test, and
T3-03's `money_policy_version` is `NOT NULL` from the very first invoice - a bill issued
without recording the rule that produced it could never be explained afterwards.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
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
        "invoices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("counter_id", sa.UUID(), nullable=False),
        sa.Column("invoice_series_id", sa.UUID(), nullable=False),
        sa.Column("invoice_number", sa.String(length=64), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column(
            "document_kind",
            postgresql.ENUM("estimate", "tax_invoice", name="document_kind", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM("issued", "cancelled", name="invoice_status", create_type=False),
            server_default=sa.text("'issued'::invoice_status"),
            nullable=False,
        ),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("customer_name_snapshot", sa.String(length=200), nullable=True),
        sa.Column("customer_business_name_snapshot", sa.String(length=200), nullable=True),
        sa.Column("vehicle_number_snapshot", sa.String(length=32), nullable=True),
        sa.Column("mechanic_name_snapshot", sa.String(length=200), nullable=True),
        sa.Column("phone_snapshot", sa.String(length=32), nullable=True),
        sa.Column("is_credit", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("subtotal", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "discount_total",
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
            "rounding_adjustment",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "amount_paid",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "payment_status",
            postgresql.ENUM(
                "unpaid",
                "partially_paid",
                "paid",
                "overpaid",
                "cancelled",
                name="payment_status",
                create_type=False,
            ),
            server_default=sa.text("'unpaid'::payment_status"),
            nullable=False,
        ),
        sa.Column("cost_total", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "oversell_override", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("oversell_reason", sa.String(length=500), nullable=True),
        sa.Column(
            "price_changes_accepted", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("tax_config_version_id", sa.UUID(), nullable=True),
        sa.Column("money_policy_version", sa.Integer(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.UUID(), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=500), nullable=True),
        sa.Column("draft_bill_id", sa.UUID(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
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
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)", name="ck_invoices__cancel_fields"
        ),
        sa.CheckConstraint(
            "NOT is_credit OR customer_id IS NOT NULL", name="ck_invoices__credit_customer"
        ),
        sa.CheckConstraint(
            "NOT oversell_override OR length(oversell_reason) >= 10",
            name="ck_invoices__oversell_reason",
        ),
        sa.CheckConstraint("amount_paid >= 0", name="ck_invoices__amount_paid"),
        sa.CheckConstraint(
            "total = subtotal - discount_total + tax_total + rounding_adjustment",
            name="ck_invoices__total",
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["cancelled_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["counter_id"], ["counters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_series_id"], ["invoice_series.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tax_config_version_id"], ["tax_config_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "invoice_number", name="uq_invoices__business_number"),
        sa.UniqueConstraint(
            "invoice_series_id", "sequence_number", name="uq_invoices__series_sequence"
        ),
    )
    op.create_index(
        "ix_invoices__branch_issued",
        "invoices",
        ["branch_id", sa.literal_column("issued_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_invoices__customer_outstanding",
        "invoices",
        ["business_id", "customer_id", sa.literal_column("issued_at DESC")],
        unique=False,
        postgresql_where=sa.text("is_credit AND payment_status IN ('unpaid','partially_paid')"),
    )
    op.create_index(
        "ix_invoices__customer_status",
        "invoices",
        ["customer_id", "payment_status"],
        unique=False,
        postgresql_where=sa.text("is_credit"),
    )
    op.create_index(
        "ix_invoices__idempotency_key", "invoices", ["business_id", "idempotency_key"], unique=False
    )
    op.create_index(
        "ix_invoices__outstanding",
        "invoices",
        ["business_id", "due_date"],
        unique=False,
        postgresql_where=sa.text("payment_status IN ('unpaid','partially_paid')"),
    )
    op.create_table(
        "invoice_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("product_name_snapshot", sa.String(length=200), nullable=False),
        sa.Column("sku_snapshot", sa.String(length=64), nullable=True),
        sa.Column("unit_code_snapshot", sa.String(length=16), nullable=False),
        sa.Column("hsn_snapshot", sa.String(length=16), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=14, scale=4), nullable=False),
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
        sa.Column("taxable_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "tax_rate_snapshot",
            sa.Numeric(precision=6, scale=3),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "tax_amount",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("line_total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("unit_cost_snapshot", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column(
            "returned_quantity",
            sa.Numeric(precision=14, scale=3),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "price_source",
            postgresql.ENUM("catalog", "manual_override", name="price_source", create_type=False),
            nullable=False,
        ),
        sa.Column("catalog_price_at_sale", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_invoice_items__quantity"),
        sa.CheckConstraint(
            "returned_quantity >= 0 AND returned_quantity <= quantity",
            name="ck_invoice_items__returned",
        ),
        sa.CheckConstraint("unit_price >= 0", name="ck_invoice_items__price"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id", "line_no", name="uq_invoice_items__line"),
    )
    op.create_index(
        "ix_invoice_items__product", "invoice_items", ["product_id", "invoice_id"], unique=False
    )
    op.create_table(
        "print_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("printed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("device_id", sa.UUID(), nullable=True),
        sa.Column("copy_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_duplicate", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "printed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["printed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_print_log__invoice",
        "print_log",
        ["invoice_id", sa.literal_column("printed_at DESC")],
        unique=False,
    )

    if _app_role_exists():
        op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON invoices FROM bm_app")
        op.execute("GRANT SELECT, INSERT ON invoices TO bm_app")
        op.execute(
            "GRANT UPDATE (status, cancelled_at, cancelled_by_user_id, cancellation_reason, amount_paid, payment_status, updated_at) ON invoices TO bm_app"
        )
        op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON invoice_items FROM bm_app")
        op.execute("GRANT SELECT, INSERT ON invoice_items TO bm_app")
        op.execute("GRANT UPDATE (returned_quantity) ON invoice_items TO bm_app")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_print_log__invoice")
    op.drop_table("print_log")
    op.execute("DROP INDEX IF EXISTS ix_invoice_items__product")
    op.drop_table("invoice_items")
    op.execute("DROP INDEX IF EXISTS ix_invoices__branch_issued")
    op.execute("DROP INDEX IF EXISTS ix_invoices__customer_outstanding")
    op.execute("DROP INDEX IF EXISTS ix_invoices__customer_status")
    op.execute("DROP INDEX IF EXISTS ix_invoices__idempotency_key")
    op.execute("DROP INDEX IF EXISTS ix_invoices__outstanding")
    op.drop_table("invoices")
