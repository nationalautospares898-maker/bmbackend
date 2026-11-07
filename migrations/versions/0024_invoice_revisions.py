"""Controlled, audited, in-place editing of a finalised invoice.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-07

The owner reversed OD-10 on 2026-11-07 (Decision A): a finalised invoice may be edited
in place, keeping its identity, by an authorised user, with an immutable record of every
previous state. This migration is what makes that possible, and it is deliberately the
narrowest change that does.

## What immutability now means

It used to mean "the row never changes" — `0011` revoked `UPDATE` and `DELETE` on
`invoices` and `invoice_items` outright. That is no longer the rule the owner wants, so
the guarantee is re-expressed rather than abandoned:

> **Every state an invoice has ever been in is permanently recorded.**

`invoice_revisions` and `invoice_revision_items` hold the complete before-state of each
edit — header, customer, mechanic, notes, every line with its quantities and prices,
the totals, the payment state, who edited it, when, and why. Those two tables are
`SELECT, INSERT` only; `UPDATE`, `DELETE` and `TRUNCATE` are revoked from the application
role, so the history cannot be rewritten even by a bug. `invoices` and `invoice_items`
become the **current** projection of that history.

This is the trade the owner accepted, and it is worth naming plainly: the protection
against a staff member quietly altering a sale moves from "the database refuses" to "the
database keeps the evidence". The evidence is immutable; the current figures are not.

## What is still refused

* **`invoices` keeps no `DELETE` grant.** An invoice cannot be removed, only cancelled —
  the existing mechanism (ADR-015), which Decision C preserves.
* **`payments`, `payment_allocations`, `stock_movements` and `audit_logs` are untouched
  by this migration.** Editing a bill never edits a payment and never rewrites the stock
  ledger; a quantity change writes a *further* movement.
* **`invoice_revisions` cannot be altered or removed.**

`invoice_items` does gain `DELETE`, because removing a line from the current line set is
how a removed line stops being on the bill. Nothing is lost: that line's full state is in
`invoice_revision_items` before it goes, and a test asserts the snapshot exists.

## `invoices.version`

Optimistic concurrency. An amend sends the version it read; the service compares under a
row lock and refuses a stale edit rather than overwriting whatever the other counter did.
Defaults to `1` for every existing row, so a bill issued before this migration is
editable immediately.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from bm.domain.auth.permissions import (
    PERMISSION_DESCRIPTIONS,
    ROLE_NAMES,
    ROLE_PERMISSIONS,
    Permission,
    SystemRole,
)

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "bm_app"

#: Exactly the columns an amend may write on `invoices`, and nothing else. Notably
#: absent: `invoice_number`, `sequence_number`, `invoice_series_id`, `issued_at`,
#: `business_id`, `branch_id`, `counter_id`, `idempotency_key` — an edit must not be able
#: to change a bill's identity, its place in the series, or when it was issued.
_INVOICE_EDITABLE_COLUMNS = (
    # Cancellation and the derived caches, as granted by 0011.
    "status",
    "cancelled_at",
    "cancelled_by_user_id",
    "cancellation_reason",
    "amount_paid",
    "payment_status",
    "updated_at",
    # Added by this migration, for the amend path.
    "customer_id",
    "customer_name_snapshot",
    "customer_business_name_snapshot",
    "vehicle_number_snapshot",
    "mechanic_name_snapshot",
    "phone_snapshot",
    "notes",
    "due_date",
    "subtotal",
    "discount_total",
    "tax_total",
    "rounding_adjustment",
    "total",
    "cost_total",
    "oversell_override",
    "oversell_reason",
    "version",
)


def _app_role_exists() -> bool:
    """Local development sometimes runs everything as one role.

    The grants are skipped rather than failing the migration, exactly as 0008 and 0011
    do, and the privilege tests skip with a clear reason when the role is absent — so
    nobody sees a green immutability test that proved nothing.
    """
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
        ).first()
    )


def _synchronise_system_roles() -> None:
    """Re-run `0005`'s permission and system-role synchronisation.

    Copied rather than imported: a migration importing another migration module couples
    two revisions that must each stand alone at whatever point in the chain they run,
    and Alembic gives no guarantee about the other being importable. The function is
    small and idempotent, which is what makes duplicating it the cheaper risk.
    """
    bind = op.get_bind()
    role_namespace = uuid.UUID("6f1c2d4e-0000-4000-8000-000000000000")

    for permission in Permission:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (code, description) VALUES (:code, :description)"
                " ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description"
            ),
            {"code": str(permission), "description": PERMISSION_DESCRIPTIONS[permission]},
        )

    for role in SystemRole:
        role_id = uuid.uuid5(role_namespace, f"system-role:{role!s}")
        bind.execute(
            sa.text(
                "INSERT INTO roles (id, business_id, code, name, is_system)"
                " VALUES (:id, NULL, :code, :name, true)"
                " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name"
            ),
            {"id": role_id, "code": str(role), "name": ROLE_NAMES[role]},
        )
        granted = sorted({str(p) for p in ROLE_PERMISSIONS[role]})
        bind.execute(
            sa.text(
                "DELETE FROM role_permissions"
                " WHERE role_id = :role_id AND NOT (permission_code = ANY(:granted))"
            ),
            {"role_id": role_id, "granted": granted},
        )
        for code in granted:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_code)"
                    " VALUES (:role_id, :code) ON CONFLICT DO NOTHING"
                ),
                {"role_id": role_id, "code": code},
            )


def upgrade() -> None:
    # --- the permission that gates editing -------------------------------------
    #
    # `billing.sale.edit`. Re-synchronised rather than hand-inserted, for the reason
    # 0023 documents: the owner role's grants are `frozenset(Permission)` computed in
    # Python, so a hand-written INSERT would reach the manager and silently miss them.
    _synchronise_system_roles()

    # --- optimistic concurrency ------------------------------------------------
    op.add_column(
        "invoices",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint("ck_invoices__version", "invoices", "version >= 1")

    # --- the immutable history -------------------------------------------------
    op.create_table(
        "invoice_revisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        #: 1 for the first edit's before-state, 2 for the second, and so on. Unique per
        #: invoice, so the history cannot develop a gap or a duplicate.
        sa.Column("revision_number", sa.Integer(), nullable=False),
        #: Mandatory, and length-checked: "edit" is not a reason.
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("edited_by_user_id", sa.UUID(), nullable=True),
        sa.Column("device_id", sa.UUID(), nullable=True),
        # --- the before-state of the header ---
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("customer_name_snapshot", sa.String(length=200), nullable=True),
        sa.Column("customer_business_name_snapshot", sa.String(length=200), nullable=True),
        sa.Column("vehicle_number_snapshot", sa.String(length=32), nullable=True),
        sa.Column("mechanic_name_snapshot", sa.String(length=200), nullable=True),
        sa.Column("phone_snapshot", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("subtotal", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("discount_total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("tax_total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("rounding_adjustment", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("total", sa.Numeric(precision=14, scale=2), nullable=False),
        #: The payment state **as it was**. Payments themselves are never touched; this
        #: records what had been paid at the moment of the edit, which is what makes the
        #: paid-total rule auditable after the fact.
        sa.Column("amount_paid", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("payment_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("revision_number >= 1", name="ck_invoice_revisions__number"),
        sa.CheckConstraint(
            "length(btrim(reason)) >= 5", name="ck_invoice_revisions__reason_present"
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["edited_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invoice_id", "revision_number", name="uq_invoice_revisions__invoice_number"
        ),
    )
    op.create_index(
        "ix_invoice_revisions__invoice",
        "invoice_revisions",
        ["invoice_id", "revision_number"],
    )

    op.create_table(
        "invoice_revision_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("revision_id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        #: The original line's id, so a reader can follow one line across revisions.
        sa.Column("invoice_item_id", sa.UUID(), nullable=True),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("product_name_snapshot", sa.String(length=200), nullable=False),
        sa.Column("sku_snapshot", sa.String(length=64), nullable=True),
        sa.Column("unit_code_snapshot", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("discount_percent", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column("discount_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("tax_rate_snapshot", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column("tax_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("returned_quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["revision_id"], ["invoice_revisions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_invoice_revision_items__revision", "invoice_revision_items", ["revision_id"]
    )

    if not _app_role_exists():
        return

    # --- privileges ------------------------------------------------------------
    #
    # The history is append-only, enforced below the application. This is the control
    # that replaces 0011's blanket revoke: the current figures may change, the record of
    # what they were may not.
    for table in ("invoice_revisions", "invoice_revision_items"):
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {APP_ROLE}")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")

    # `invoices` gains UPDATE on the amend columns and **keeps no DELETE**. Re-granted
    # as one statement listing every permitted column, because a column-level GRANT
    # replaces rather than adds to the previous one.
    op.execute(f"REVOKE UPDATE ON invoices FROM {APP_ROLE}")
    op.execute(f"GRANT UPDATE ({', '.join(_INVOICE_EDITABLE_COLUMNS)}) ON invoices TO {APP_ROLE}")

    # `invoice_items` becomes the editable current line set. Its every prior state is in
    # `invoice_revision_items` first — asserted by a test, not merely intended.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON invoice_items TO {APP_ROLE}")
    # Still no bulk wipe.
    op.execute(f"REVOKE TRUNCATE ON invoice_items FROM {APP_ROLE}")


def downgrade() -> None:
    bind = op.get_bind()
    code = str(Permission.BILLING_SALE_EDIT)
    bind.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_code = :code"), {"code": code}
    )
    bind.execute(sa.text("DELETE FROM permissions WHERE code = :code"), {"code": code})

    if _app_role_exists():
        # Back to 0011's posture exactly.
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON invoice_items FROM {APP_ROLE}")
        op.execute(f"GRANT SELECT, INSERT ON invoice_items TO {APP_ROLE}")
        op.execute(f"GRANT UPDATE (returned_quantity) ON invoice_items TO {APP_ROLE}")
        op.execute(f"REVOKE UPDATE ON invoices FROM {APP_ROLE}")
        op.execute(
            "GRANT UPDATE (status, cancelled_at, cancelled_by_user_id,"
            f" cancellation_reason, amount_paid, payment_status, updated_at) ON invoices TO {APP_ROLE}"
        )

    op.drop_index("ix_invoice_revision_items__revision", table_name="invoice_revision_items")
    op.drop_table("invoice_revision_items")
    op.drop_index("ix_invoice_revisions__invoice", table_name="invoice_revisions")
    op.drop_table("invoice_revisions")
    op.drop_constraint("ck_invoices__version", "invoices", type_="check")
    op.drop_column("invoices", "version")
