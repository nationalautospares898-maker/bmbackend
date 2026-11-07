"""A reusable list of mechanics, so the counter stops retyping the same twenty names.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-07

The billing screen already captures a mechanic: `invoices.mechanic_name` and
`draft_bills.mechanic_name` are free-text snapshots and have been since 0011 and 0012.
What did not exist was anything to *suggest* from, so the same name was retyped — and
misspelled — on every bill.

## Why a table, and not `SELECT DISTINCT mechanic_name FROM invoices`

The distinct-query version is tempting because the data is already there. It is wrong
for three separate reasons, and all three bite in normal shop use:

* It reads a **financial** table on every keystroke of an autocomplete. `invoices` is the
  hottest table in the system and the one whose row locks finalisation depends on
  (ADR-008); putting a `LIKE` scan on it behind a text box is how billing starts timing
  out at month end.
* **A typo would be permanent.** Invoices are immutable (OD-10, invariant 1), so
  "Rmaesh" entered once would be offered as a suggestion for ever with no way to correct
  it that does not involve rewriting a finalised bill. A reference list can be fixed;
  a snapshot cannot, and must not be.
* It would suggest **nothing until the first sale is finalised**. A mechanic typed on a
  draft, or on a bill that was cleared, would never become a suggestion — which is
  precisely the case the owner asked for ("newly entered mechanic names must be saved and
  reused on subsequent bills").

## What this deliberately does *not* do

There is **no foreign key from `invoices` to `mechanics`**, and `invoices.mechanic_name`
is untouched. The snapshot on the bill stays the authoritative historical record, exactly
as `product_name_snapshot` does for a renamed product (FR-SALE-012). So renaming a
mechanic, merging a duplicate or deactivating someone who left **cannot** rewrite what a
past bill says. The table is a convenience list for the next bill, not a claim about
previous ones.

`uq_mechanics__business_name` is on the **normalized** name, which is what makes
`  ramesh `, `Ramesh` and `RAMESH` one mechanic rather than three — the duplicate problem
the owner specifically called out. The display `name` keeps whatever capitalisation was
first entered.

Grants are the schema default (`scripts/init-roles.sql`): `SELECT, INSERT, UPDATE,
DELETE`. That is correct and intentional here — a mechanic list is reference data, not a
financial record, so it is the one thing in this area that *should* be editable. No
`REVOKE` belongs in this migration, and adding one by reflex would make the correct-a-typo
case impossible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mechanics",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        #: As typed, for display. Capitalisation is preserved.
        sa.Column("name", sa.String(length=200), nullable=False),
        #: `normalize_name` — lower-cased, accent-stripped, whitespace-collapsed. The
        #: uniqueness key, so case and spacing cannot create a duplicate.
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        #: Ordering signal for the suggestion list: most recently used first beats
        #: alphabetical when twenty names exist and three of them are used daily.
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("use_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_mechanics__name_present"),
        sa.CheckConstraint(
            "length(btrim(normalized_name)) > 0", name="ck_mechanics__normalized_present"
        ),
        sa.CheckConstraint("use_count >= 0", name="ck_mechanics__use_count"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "normalized_name", name="uq_mechanics__business_name"),
    )

    # The prefix tier of the search, matching how `products` and `customers` are indexed
    # (0018). `text_pattern_ops` is what makes `LIKE 'ram%'` an index scan under the
    # non-C collation a real deployment may have.
    op.execute(
        "CREATE INDEX ix_mechanics__name_prefix ON mechanics"
        " USING btree (business_id, normalized_name text_pattern_ops)"
    )
    # The fuzzy tier, for a name that is misremembered rather than mistyped at the front.
    # `pg_trgm` is already installed by 0018.
    op.execute(
        "CREATE INDEX ix_mechanics__name_trgm ON mechanics USING gin (normalized_name gin_trgm_ops)"
    )
    # "Most recently used first" needs an index or it is a sort of the whole list on
    # every empty-query open, which is the common case: the box is focused before
    # anything is typed.
    op.execute(
        "CREATE INDEX ix_mechanics__recent ON mechanics"
        " (business_id, last_used_at DESC NULLS LAST, use_count DESC)"
    )


def downgrade() -> None:
    op.drop_table("mechanics")
