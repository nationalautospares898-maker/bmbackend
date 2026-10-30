"""Add business_id to the five child tables that were missing it.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-30

NFR-DATA-010 requires every business-scoped row to carry `business_id NOT NULL`.
`invoice_items`, `payment_allocations` and `credit_note_allocations` already did;
`draft_bill_items`, `purchase_items`, `sales_return_items`, `stock_count_items` and
`import_rows` did not, and derived their tenancy from a parent row instead.

That is one join away from correct, which is the problem. The column exists so a query
that forgets the parent join cannot return another business's rows — defence in depth
against a tenancy bug rather than a normalisation preference. `test_schema_shape.py`
asserts it, and the exemption list there is explicit so a new table cannot quietly join
it.

Backfilled from the parent before the NOT NULL, so this applies cleanly to a populated
database.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None

#: (child table, parent table, the child's FK to that parent).
_CHILDREN: tuple[tuple[str, str, str], ...] = (
    ("draft_bill_items", "draft_bills", "draft_bill_id"),
    ("purchase_items", "purchases", "purchase_id"),
    ("sales_return_items", "sales_returns", "sales_return_id"),
    ("stock_count_items", "stock_count_sessions", "stock_count_session_id"),
    ("import_rows", "import_jobs", "import_job_id"),
)


def upgrade() -> None:
    for child, parent, fk in _CHILDREN:
        op.add_column(child, sa.Column("business_id", sa.Uuid(), nullable=True))
        op.execute(
            f"UPDATE {child} AS c"
            f"   SET business_id = p.business_id"
            f"  FROM {parent} AS p"
            f" WHERE p.id = c.{fk}"
        )
        op.alter_column(child, "business_id", nullable=False)
        op.create_foreign_key(
            f"fk_{child}__business",
            child,
            "businesses",
            ["business_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        # The column exists to make a tenant-scoped scan possible without the parent
        # join, so it is indexed with the parent key it will be filtered alongside.
        op.create_index(f"ix_{child}__business", child, ["business_id", fk])


def downgrade() -> None:
    for child, _parent, _fk in reversed(_CHILDREN):
        op.drop_index(f"ix_{child}__business", table_name=child)
        op.drop_constraint(f"fk_{child}__business", child, type_="foreignkey")
        op.drop_column(child, "business_id")
