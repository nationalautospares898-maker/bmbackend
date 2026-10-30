"""Create the search indexes migration 0006 described but never created.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-30

Migration `0006`'s own docstring says:

> Search indexes are the point of this migration. `ix_products__sku_prefix` and
> `ix_products__name_prefix` use `text_pattern_ops` so a prefix match can use the index
> at all; `ix_products__search_trgm` is a GIN trigram index for the fuzzy/contains tier.

None of the three were ever created. `pg_indexes` on `products` carried only the primary
key and the two partial unique constraints, so **every product search was a sequential
scan** — correct, and fast enough on a seeded test catalogue, which is exactly why no
test caught it.

That matters against NFR-PERF-001 (product search p95 ≤ 150 ms at 100 000 products with
50 concurrent searches). A seq scan over 100 000 rows per keystroke, with the search
debounced at 120 ms per counter and five counters typing, does not meet it.

Two notes on the specifics:

* `text_pattern_ops` is required, not an optimisation. With the database's default
  collation a plain btree index cannot serve `LIKE 'abc%'` at all; the planner ignores
  it. This is the single most common reason a prefix search is slow despite "having an
  index".
* The trigram index is on `search_text` because that is the column the contains/fuzzy
  tier actually filters on (`LIKE :contains OR search_text % :raw`), and a second one
  goes on `normalized_name`, which the **import matcher** filters with `%` (FR-IMP-006).
  Indexing the column a query names, rather than the one that seems natural, is the
  whole job here.

`products.business_id` leads the prefix indexes so the scan is confined to one tenant
before the pattern is considered.
"""

from __future__ import annotations

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None

#: (name, table, the index definition after `USING`).
#:
#: Written as raw DDL rather than `op.create_index` because `text_pattern_ops` and
#: `gin_trgm_ops` are operator classes, and expressing an operator class through
#: Alembic's `postgresql_ops` for a *partial* index is less legible than the DDL it
#: generates.
_INDEXES: tuple[tuple[str, str, str], ...] = (
    (
        "ix_products__sku_prefix",
        "products",
        "btree (business_id, normalized_sku text_pattern_ops) WHERE normalized_sku IS NOT NULL",
    ),
    (
        "ix_products__name_prefix",
        "products",
        "btree (business_id, normalized_name text_pattern_ops)",
    ),
    (
        "ix_products__search_trgm",
        "products",
        "gin (search_text gin_trgm_ops)",
    ),
    (
        "ix_products__name_trgm",
        "products",
        "gin (normalized_name gin_trgm_ops)",
    ),
    (
        "ix_product_aliases__prefix",
        "product_aliases",
        "btree (business_id, normalized_alias text_pattern_ops)",
    ),
)


def upgrade() -> None:
    for name, table, definition in _INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING {definition}")
    # The planner needs statistics on the newly indexed expressions to choose them.
    # Cheap on an empty database and correct on a populated one.
    op.execute("ANALYZE products")
    op.execute("ANALYZE product_aliases")


def downgrade() -> None:
    for name, _table, _definition in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
