"""Catalog data access, including the ranked search query.

The search query is the one place in this system where a hand-written SQL expression is
clearer than the ORM equivalent, because the ranking is a tiered `CASE` and the whole
point is to express the tiers in one readable place. Every parameter is bound; the
normalised query text is a *value*, never interpolated (`SECURITY.md §4.1`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from bm.infra.db.models.catalog import Brand, Category, Product, ProductAlias, Unit
from bm.infra.db.models.inventory import InventoryBalance
from bm.infra.db.rows import rows_affected
from bm.infra.db.sql_patterns import escape_like


@dataclass(frozen=True, slots=True)
class UnitRow:
    id: uuid.UUID
    code: str
    name: str
    allows_fraction: bool
    decimal_places: int


@dataclass(frozen=True, slots=True)
class ProductRow:
    id: uuid.UUID
    name: str
    sku: str | None
    barcode: str | None
    brand_name: str | None
    category_name: str | None
    unit_code: str
    unit_allows_fraction: bool
    unit_decimal_places: int
    default_selling_price: Decimal
    last_cost_price: Decimal | None
    reorder_level: Decimal | None
    hsn_code: str | None
    tax_group_id: uuid.UUID | None
    is_active: bool
    branch_quantity: Decimal | None = None
    match_kind: str | None = None
    match_score: int | None = None


#: Tiered ranking from `ARCHITECTURE.md §8`. Exact SKU beats a SKU prefix, which beats a
#: name prefix, which beats an alias, which beats a trigram similarity — because that is
#: the order in which a counter hand's intent becomes less certain.
_RANK_SQL = """
    CASE
        WHEN p.normalized_sku = :exact            THEN 100
        WHEN p.normalized_sku LIKE :prefix_sku    THEN 80
        WHEN p.normalized_name LIKE :prefix_name  THEN 60
        WHEN a.normalized_alias LIKE :prefix_sku  THEN 50
        ELSE (similarity(p.search_text, :raw) * 40)::int
    END
"""

_MATCH_KIND_SQL = """
    CASE
        WHEN p.normalized_sku = :exact            THEN 'exact_sku'
        WHEN p.normalized_sku LIKE :prefix_sku    THEN 'sku_prefix'
        WHEN p.normalized_name LIKE :prefix_name  THEN 'name_prefix'
        WHEN a.normalized_alias LIKE :prefix_sku  THEN 'alias_prefix'
        ELSE 'fuzzy'
    END
"""

#: Ranked search, as a union of **one indexable branch per tier**, ranked and truncated
#: **before** the row detail is joined on.
#:
#: Why it is shaped this way rather than as one `WHERE … OR … OR …`
#: ----------------------------------------------------------------
#:
#: The obvious version — a single scan with the five tier predicates OR'd together — is
#: what this was, and it could not use an index at all. Two reasons, both worth knowing:
#:
#: 1. One disjunct (`a.normalized_alias LIKE …`) lives on a `LEFT JOIN`ed table. A
#:    predicate spanning the join cannot be evaluated before the join, so PostgreSQL has
#:    to materialise the joined relation and filter afterwards — and once the alias
#:    disjunct is in the filter, the whole disjunction goes with it.
#: 2. A bitmap-OR over five different indexes is only chosen when each branch is
#:    selective. `search_text LIKE '%tt%'` is not, so the planner correctly preferred one
#:    sequential scan to five index scans plus a bitmap merge.
#:
#: Measured on the 100 000-product seeded catalogue: `Parallel Seq Scan on products`,
#: 100 000 rows scanned, 90 000 `Rows Removed by Filter`, **p95 411 ms against the 150 ms
#: budget of NFR-PERF-001**. The indexes from migration `0018` existed and were
#: unreachable.
#:
#: As a union, each branch is a separate query the planner can satisfy from one index:
#: equality and prefix branches from the btree `text_pattern_ops` indexes, the fuzzy
#: branch from the GIN trigram index.
#:
#: Why the ranking and the `LIMIT` come *before* the five detail joins
#: ------------------------------------------------------------------
#:
#: Making the tiers indexable fixed the scan but left a second, larger cost in place, and
#: the 30 October 2026 audit measured it: **single-thread p95 347 ms against the same
#: 150 ms budget**. The union was cheap; what was expensive was what happened to its
#: output.
#:
#: A broad but perfectly legitimate query matches a large fraction of the catalogue —
#: `normalized_name LIKE 'oil filter%'` matches 10 000 of the seeded 100 000, and the
#: trigram branch matches another 10 000. The old query joined `products`, `units`,
#: `brands`, `categories` and `inventory_balances` onto **every one of those candidate
#: rows**, and only then sorted and applied `LIMIT`. Measured with
#: `EXPLAIN (ANALYZE, BUFFERS)` for `'clutch pl'`:
#:
#: * candidate branches: 20 000 rows, 36 ms, 5 248 buffers — the indexes doing their job;
#: * `products_pkey` index scan **loops=10000**, 40 000 buffers;
#: * `units_pkey` **loops=10000**, 20 000 buffers; `brands_pkey` and `categories_pkey`
#:   likewise; `inventory_balances` **loops=10000**, 30 001 buffers;
#: * `Limit → Sort (top-N heapsort)` then discarded all but 500 rows.
#:
#: 95 249 buffer hits and 108 ms to return 500 rows, of which roughly 90 000 hits were
#: spent decorating rows that the `LIMIT` immediately threw away.
#:
#: So the ordering key is computed where it is cheap and the detail is fetched where it is
#: needed. `sales_rank` and `normalized_name` are carried up out of the candidate branches
#: (the branches are already reading those heap rows, so selecting two more columns is
#: free), `best` ranks and truncates on them, and the five joins then run for at most
#: `SEARCH_ROW_CAP` rows instead of for every match.
#:
#: Two further bounds, both of which preserve the result exactly:
#:
#: * **Each branch is itself `LIMIT :row_cap`**, ordered by the same tie-break the final
#:   ordering uses. This is exact, not an approximation: a tier's score is fixed, so if a
#:   product is outside its own tier's best `row_cap` by `(sales_rank DESC,
#:   normalized_name, id)`, then `row_cap` products from that same tier — each with a
#:   final score at least as high, because `max()` can only raise it — already precede
#:   it, and it therefore cannot be in the overall best `row_cap` either. The fuzzy
#:   branch orders by its own varying score first, and the same argument applies.
#: * **`is_active` is filtered inside each branch** rather than after the union. With the
#:   per-branch `LIMIT` in place this is required for correctness, not an optimisation: an
#:   inactive row must not consume a slot that an active row would otherwise have taken.
#:   The resulting set is identical to filtering afterwards.
#:
#: Why `id` is the last ordering column
#: ------------------------------------
#:
#: `(score, sales_rank, normalized_name)` is **not** a total order, and on a real
#: catalogue it is nowhere near one: a shop stocks the same part for eight makes under
#: forty name stems, so the 100 000-product catalogue resolves the `'air'` name-prefix
#: tier's 10 000 rows into only 1 983 distinct ordering keys — tie groups averaging five
#: rows and reaching thirteen. Without a final discriminator the order *within* a tie
#: group was whatever the plan happened to emit, so the comment in `search()` promising a
#: list "stable between keystrokes" was not true: a plan flip or a `VACUUM` could reorder
#: it, and the per-branch `LIMIT` above would be free to choose a different five of a
#: thirteen-row tie each time.
#:
#: `id` is appended to every ordering clause here, and to the service's own sort, so the
#: order is total and the answer is deterministic. This does not change the documented
#: ranking — score, then sales frequency, then name, exactly as `ARCHITECTURE.md §8`
#: states — it only fixes which of two genuinely indistinguishable rows comes first, and
#: it is what makes the per-branch `LIMIT` provably exact rather than merely usually
#: right.
#:
#: The scores and `match_kind` values are unchanged from `_RANK_SQL` / `_MATCH_KIND_SQL`
#: above, which still document the tiers. Two suites hold that claim up:
#:
#: * `tests/api/test_catalog_search.py` asserts the tier ordering, the alias collapse,
#:   the `include_inactive` rule, tenant isolation and the limit clamp directly. It is
#:   new — the audit found the ranking had **no** test coverage, which is why a claim
#:   that used to live here, that an "M5 catalog suite" proved the behaviour, was false.
#: * `tests/load/test_search_equivalence.py` asserts, on the 100 000-product catalogue,
#:   that this query returns the same ids, scores and `match_kind`s **in the same order**
#:   as the pre-optimisation query it replaced, for every documented query shape.
SEARCH_SQL = text(
    """
    WITH candidates AS (
        -- exact part code (100)
        (SELECT id AS product_id, 100 AS score, 'exact_sku' AS match_kind,
                sales_rank, normalized_name
           FROM products
          WHERE business_id = :business_id AND normalized_sku = :exact
            AND (:include_inactive OR is_active)
          ORDER BY sales_rank DESC, normalized_name, id
          LIMIT :row_cap)
        UNION ALL
        -- part-code prefix (80)
        (SELECT id, 80, 'sku_prefix', sales_rank, normalized_name
           FROM products
          WHERE business_id = :business_id AND normalized_sku LIKE :prefix_sku
            AND (:include_inactive OR is_active)
          ORDER BY sales_rank DESC, normalized_name, id
          LIMIT :row_cap)
        UNION ALL
        -- name prefix (60)
        (SELECT id, 60, 'name_prefix', sales_rank, normalized_name
           FROM products
          WHERE business_id = :business_id AND normalized_name LIKE :prefix_name
            AND (:include_inactive OR is_active)
          ORDER BY sales_rank DESC, normalized_name, id
          LIMIT :row_cap)
        UNION ALL
        -- alias prefix (50). This branch joins `products` because the tie-break and the
        -- `is_active` filter live there; `product_aliases` is a small table and the join
        -- is on its primary key.
        (SELECT a.product_id, 50, 'alias_prefix', p.sales_rank, p.normalized_name
           FROM product_aliases a
           JOIN products p ON p.id = a.product_id
          WHERE a.business_id = :business_id AND a.normalized_alias LIKE :prefix_sku
            AND (:include_inactive OR p.is_active)
          ORDER BY p.sales_rank DESC, p.normalized_name, p.id
          LIMIT :row_cap)
        UNION ALL
        -- contains / trigram (similarity * 40, so always below the tiers above).
        --
        -- Gated on `:fuzzy_enabled`, which the repository sets only for queries of
        -- `MIN_FUZZY_QUERY_LENGTH` characters or more. Two reasons, and they agree:
        --
        -- * **It cannot be indexed below three characters.** A trigram is three
        --   characters, so `'%tt%'` contains no complete trigram and the GIN index is
        --   unusable however well it is built. Measured: a sequential scan of 159 ms
        --   out of a 252 ms query.
        -- * **It is not useful below three characters either.** On the seeded
        --   catalogue, `search_text LIKE '%tt%'` matched 10 000 of 100 000 products.
        --   A cashier typing two characters wants the part codes that *start* that
        --   way, which the prefix tiers already return from the first character
        --   (FR-CAT-003) — not every product that happens to contain the letters.
        (SELECT id, (similarity(search_text, :raw) * 40)::int, 'fuzzy',
                sales_rank, normalized_name
           FROM products
          WHERE :fuzzy_enabled
            AND business_id = :business_id
            AND (search_text LIKE :contains OR search_text % :raw)
            AND (:include_inactive OR is_active)
          ORDER BY (similarity(search_text, :raw) * 40)::int DESC,
                   sales_rank DESC, normalized_name, id
          LIMIT :row_cap)
    ),
    best AS (
        SELECT product_id,
               max(score) AS score,
               -- The kind belonging to the winning score. `DISTINCT ON` cannot be used
               -- here because the aggregate is what selects the row.
               (ARRAY_AGG(match_kind ORDER BY score DESC))[1] AS match_kind,
               -- Both are functionally dependent on `product_id`, so the aggregate
               -- returns that product's own value; they are aggregated only because
               -- `GROUP BY product_id` requires it. Carrying them here is what lets the
               -- ranking happen before the detail joins.
               max(sales_rank) AS sales_rank,
               min(normalized_name) AS normalized_name
          FROM candidates
         GROUP BY product_id
         ORDER BY 2 DESC, 4 DESC, 5, 1
         LIMIT :row_cap
    )
    SELECT p.id, p.name, p.sku, p.barcode, p.default_selling_price, p.last_cost_price,
           p.reorder_level, p.hsn_code, p.tax_group_id, p.is_active,
           b.name AS brand_name, c.name AS category_name,
           u.code AS unit_code, u.allows_fraction, u.decimal_places,
           ib.quantity AS branch_quantity,
           best.score AS score,
           best.match_kind AS match_kind,
           best.sales_rank, best.normalized_name
      FROM best
      JOIN products p ON p.id = best.product_id
      JOIN units u ON u.id = p.unit_id
      LEFT JOIN brands b ON b.id = p.brand_id
      LEFT JOIN categories c ON c.id = p.category_id
      LEFT JOIN inventory_balances ib
             ON ib.product_id = p.id AND ib.branch_id = :branch_id
     ORDER BY best.score DESC, best.sales_rank DESC, best.normalized_name, p.id
    """
)

#: Hard cap on candidate rows fetched before the service applies its own `limit`.
#:
#: The service sorts and truncates in Python (see `search`), so it needs more rows than
#: it returns — but not unboundedly more. A one-character query against a large
#: catalogue can match tens of thousands of rows on the fuzzy tier, and fetching all of
#: them to return 20 is the other way to miss the latency budget. 500 is far above any
#: page size and far below "the whole catalogue".
SEARCH_ROW_CAP = 500

#: Below this many characters the fuzzy tier is skipped — see the comment on that branch.
#:
#: Three because that is a trigram's length; this is `pg_trgm`'s constraint, not a tuning
#: choice. Searching from the **first** character still works (FR-CAT-003): the exact,
#: part-code-prefix, name-prefix and alias tiers all fire at one character, and those are
#: the tiers that carry a cashier's intent when they have typed one letter.
MIN_FUZZY_QUERY_LENGTH = 3


class CatalogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- units ---------------------------------------------------------------

    def list_units(self, business_id: uuid.UUID) -> list[UnitRow]:
        rows = (
            self._session.execute(
                select(Unit).where(Unit.business_id == business_id).order_by(Unit.code)
            )
            .scalars()
            .all()
        )
        return [
            UnitRow(
                id=r.id,
                code=r.code,
                name=r.name,
                allows_fraction=r.allows_fraction,
                decimal_places=r.decimal_places,
            )
            for r in rows
        ]

    def get_unit(self, unit_id: uuid.UUID, business_id: uuid.UUID) -> UnitRow | None:
        row = self._session.execute(
            select(Unit).where(Unit.id == unit_id, Unit.business_id == business_id)
        ).scalar_one_or_none()
        if row is None:
            return None
        return UnitRow(
            id=row.id,
            code=row.code,
            name=row.name,
            allows_fraction=row.allows_fraction,
            decimal_places=row.decimal_places,
        )

    def unit_for_product(self, product_id: uuid.UUID) -> UnitRow | None:
        row = self._session.execute(
            select(Unit).join(Product, Product.unit_id == Unit.id).where(Product.id == product_id)
        ).scalar_one_or_none()
        if row is None:
            return None
        return UnitRow(
            id=row.id,
            code=row.code,
            name=row.name,
            allows_fraction=row.allows_fraction,
            decimal_places=row.decimal_places,
        )

    def ensure_unit(
        self,
        business_id: uuid.UUID,
        *,
        code: str,
        name: str,
        allows_fraction: bool,
        decimal_places: int,
    ) -> uuid.UUID:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        statement = (
            pg_insert(Unit)
            .values(
                id=uuid.uuid4(),
                business_id=business_id,
                code=code,
                name=name,
                allows_fraction=allows_fraction,
                decimal_places=decimal_places,
            )
            .on_conflict_do_update(
                index_elements=[Unit.business_id, Unit.code], set_={"name": name}
            )
            .returning(Unit.id)
        )
        return self._session.execute(statement).scalar_one()

    # --- brands and categories ------------------------------------------------

    def ensure_brand(self, business_id: uuid.UUID, *, name: str, normalized: str) -> uuid.UUID:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        statement = (
            pg_insert(Brand)
            .values(
                id=uuid.uuid4(),
                business_id=business_id,
                name=name,
                normalized_name=normalized,
            )
            .on_conflict_do_update(
                index_elements=[Brand.business_id, Brand.normalized_name], set_={"name": name}
            )
            .returning(Brand.id)
        )
        return self._session.execute(statement).scalar_one()

    def ensure_category(self, business_id: uuid.UUID, *, name: str, normalized: str) -> uuid.UUID:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        statement = (
            pg_insert(Category)
            .values(
                id=uuid.uuid4(),
                business_id=business_id,
                name=name,
                normalized_name=normalized,
            )
            .on_conflict_do_update(
                index_elements=[Category.business_id, Category.normalized_name],
                set_={"name": name},
            )
            .returning(Category.id)
        )
        return self._session.execute(statement).scalar_one()

    def list_brands(self, business_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
        rows = self._session.execute(
            select(Brand.id, Brand.name)
            .where(Brand.business_id == business_id, Brand.is_active.is_(True))
            .order_by(Brand.name)
        ).all()
        return [(r[0], r[1]) for r in rows]

    def list_categories(self, business_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
        rows = self._session.execute(
            select(Category.id, Category.name)
            .where(Category.business_id == business_id, Category.is_active.is_(True))
            .order_by(Category.name)
        ).all()
        return [(r[0], r[1]) for r in rows]

    # --- products -------------------------------------------------------------

    def insert_product(self, values: dict[str, Any]) -> uuid.UUID:
        from sqlalchemy import insert

        return self._session.execute(
            insert(Product).values(**values).returning(Product.id)
        ).scalar_one()

    def update_product(
        self, product_id: uuid.UUID, business_id: uuid.UUID, values: dict[str, Any]
    ) -> bool:
        if not values:
            return False
        return bool(
            rows_affected(
                self._session.execute(
                    update(Product)
                    .where(Product.id == product_id, Product.business_id == business_id)
                    .values(**values)
                )
            )
        )

    def get_product(
        self, product_id: uuid.UUID, business_id: uuid.UUID, *, branch_id: uuid.UUID | None = None
    ) -> ProductRow | None:
        row = self._session.execute(
            select(
                Product,
                Brand.name,
                Category.name,
                Unit.code,
                Unit.allows_fraction,
                Unit.decimal_places,
                InventoryBalance.quantity,
            )
            .join(Unit, Unit.id == Product.unit_id)
            .outerjoin(Brand, Brand.id == Product.brand_id)
            .outerjoin(Category, Category.id == Product.category_id)
            .outerjoin(
                InventoryBalance,
                (InventoryBalance.product_id == Product.id)
                & (InventoryBalance.branch_id == branch_id),
            )
            .where(Product.id == product_id, Product.business_id == business_id)
        ).first()
        if row is None:
            return None
        product, brand, category, unit_code, allows_fraction, places, quantity = row
        return ProductRow(
            id=product.id,
            name=product.name,
            sku=product.sku,
            barcode=product.barcode,
            brand_name=brand,
            category_name=category,
            unit_code=unit_code,
            unit_allows_fraction=allows_fraction,
            unit_decimal_places=places,
            default_selling_price=product.default_selling_price,
            last_cost_price=product.last_cost_price,
            reorder_level=product.reorder_level,
            hsn_code=product.hsn_code,
            tax_group_id=product.tax_group_id,
            is_active=product.is_active,
            branch_quantity=quantity,
        )

    def find_by_normalized_sku(
        self, business_id: uuid.UUID, normalized_sku: str
    ) -> uuid.UUID | None:
        return self._session.execute(
            select(Product.id).where(
                Product.business_id == business_id, Product.normalized_sku == normalized_sku
            )
        ).scalar_one_or_none()

    def find_by_barcode(self, business_id: uuid.UUID, barcode: str) -> uuid.UUID | None:
        return self._session.execute(
            select(Product.id).where(Product.business_id == business_id, Product.barcode == barcode)
        ).scalar_one_or_none()

    def search(
        self,
        *,
        business_id: uuid.UUID,
        branch_id: uuid.UUID | None,
        normalized_query: str,
        normalized_sku_query: str,
        limit: int,
        include_inactive: bool,
    ) -> list[ProductRow]:
        """Ranked search. One query, no N+1.

        `GROUP BY product_id` in `best` collapses the tiers so a product matching on
        five aliases appears once, at its best score.
        """
        rows = self._session.execute(
            SEARCH_SQL,
            {
                "business_id": business_id,
                "branch_id": branch_id,
                "exact": normalized_sku_query,
                "prefix_sku": f"{escape_like(normalized_sku_query)}%",
                "prefix_name": f"{escape_like(normalized_query)}%",
                "contains": f"%{escape_like(normalized_query)}%",
                "raw": normalized_query,
                "include_inactive": include_inactive,
                "row_cap": SEARCH_ROW_CAP,
                "fuzzy_enabled": len(normalized_query) >= MIN_FUZZY_QUERY_LENGTH,
            },
        ).all()

        results = [
            ProductRow(
                id=r.id,
                name=r.name,
                sku=r.sku,
                barcode=r.barcode,
                brand_name=r.brand_name,
                category_name=r.category_name,
                unit_code=r.unit_code,
                unit_allows_fraction=r.allows_fraction,
                unit_decimal_places=r.decimal_places,
                default_selling_price=r.default_selling_price,
                last_cost_price=r.last_cost_price,
                reorder_level=r.reorder_level,
                hsn_code=r.hsn_code,
                tax_group_id=r.tax_group_id,
                is_active=r.is_active,
                branch_quantity=r.branch_quantity,
                match_kind=r.match_kind,
                match_score=r.score,
            )
            for r in rows
        ]
        # The SQL already returns the rows in this order; re-applying the key here is
        # what makes the ordering a property of *this* function rather than of the
        # query's shape, so a future change to the query cannot silently reorder the
        # list a cashier sees. Ties break on sales frequency, then name, then id — the
        # id last so the order is *total*, which is what actually makes the list stable
        # between keystrokes. See the note on `SEARCH_SQL`: without it, a tie group of
        # five or more rows was ordered by whatever the plan emitted.
        by_rank = {r.id: (r.sales_rank, r.normalized_name) for r in rows}
        results.sort(
            key=lambda p: (
                -(p.match_score or 0),
                -by_rank[p.id][0],
                by_rank[p.id][1],
                p.id,
            )
        )
        return results[:limit]

    def deactivate_product(self, product_id: uuid.UUID, business_id: uuid.UUID) -> bool:
        return bool(
            rows_affected(
                self._session.execute(
                    update(Product)
                    .where(Product.id == product_id, Product.business_id == business_id)
                    .values(is_active=False)
                )
            )
        )

    # --- aliases --------------------------------------------------------------

    def add_alias(
        self,
        *,
        business_id: uuid.UUID,
        product_id: uuid.UUID,
        alias: str,
        normalized_alias: str,
        alias_type: str,
    ) -> uuid.UUID:
        from sqlalchemy import insert

        return self._session.execute(
            insert(ProductAlias)
            .values(
                id=uuid.uuid4(),
                business_id=business_id,
                product_id=product_id,
                alias=alias,
                normalized_alias=normalized_alias,
                alias_type=alias_type,
            )
            .returning(ProductAlias.id)
        ).scalar_one()

    def delete_alias(self, alias_id: uuid.UUID, business_id: uuid.UUID) -> uuid.UUID | None:
        from sqlalchemy import delete

        return self._session.execute(
            delete(ProductAlias)
            .where(ProductAlias.id == alias_id, ProductAlias.business_id == business_id)
            .returning(ProductAlias.product_id)
        ).scalar_one_or_none()

    def aliases_for(self, product_id: uuid.UUID) -> list[tuple[uuid.UUID, str, str]]:
        rows = self._session.execute(
            select(ProductAlias.id, ProductAlias.alias, ProductAlias.alias_type)
            .where(ProductAlias.product_id == product_id)
            .order_by(ProductAlias.alias)
        ).all()
        return [(r[0], r[1], r[2]) for r in rows]

    def alias_texts_for(self, product_id: uuid.UUID) -> list[str]:
        return list(
            self._session.execute(
                select(ProductAlias.alias).where(ProductAlias.product_id == product_id)
            ).scalars()
        )

    def refresh_search_text(self, product_id: uuid.UUID, search_text: str) -> None:
        """Fix **S-8**.

        `search_text` is application-maintained, so an alias insert or delete has to
        rewrite the parent product's haystack. Without this, alias searches silently
        stop working for every product whose aliases changed after creation — and
        nothing fails loudly enough to notice.
        """
        self._session.execute(
            update(Product).where(Product.id == product_id).values(search_text=search_text)
        )

    def product_name_and_brand(self, product_id: uuid.UUID) -> tuple[str, str | None, str | None]:
        row = self._session.execute(
            select(Product.name, Product.sku, Brand.name)
            .outerjoin(Brand, Brand.id == Product.brand_id)
            .where(Product.id == product_id)
        ).first()
        if row is None:
            return ("", None, None)
        return (row[0], row[1], row[2])

    def count_products(self, business_id: uuid.UUID) -> int:
        return int(
            self._session.execute(
                select(func.count()).select_from(Product).where(Product.business_id == business_id)
            ).scalar_one()
        )
