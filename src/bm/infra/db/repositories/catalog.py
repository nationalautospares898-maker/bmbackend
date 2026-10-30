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

#: Ranked search, as a union of **one indexable branch per tier**.
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
#: branch from the GIN trigram index. The row detail is then fetched **once**, for the
#: few hundred candidate ids, instead of for every product in the catalogue.
#:
#: The scores and `match_kind` values are unchanged from `_RANK_SQL` / `_MATCH_KIND_SQL`
#: above, which still document the tiers; `GREATEST`-by-id picks the best tier a product
#: matched, exactly as `DISTINCT ON … ORDER BY score DESC` did. The M5 catalog suite is
#: what proves the behaviour is identical — it was written against the old query and
#: passes unchanged against this one.
SEARCH_SQL = text(
    """
    WITH candidates AS (
        -- exact part code (100)
        SELECT id AS product_id, 100 AS score, 'exact_sku' AS match_kind
          FROM products
         WHERE business_id = :business_id AND normalized_sku = :exact
        UNION ALL
        -- part-code prefix (80)
        SELECT id, 80, 'sku_prefix'
          FROM products
         WHERE business_id = :business_id AND normalized_sku LIKE :prefix_sku
        UNION ALL
        -- name prefix (60)
        SELECT id, 60, 'name_prefix'
          FROM products
         WHERE business_id = :business_id AND normalized_name LIKE :prefix_name
        UNION ALL
        -- alias prefix (50)
        SELECT a.product_id, 50, 'alias_prefix'
          FROM product_aliases a
         WHERE a.business_id = :business_id AND a.normalized_alias LIKE :prefix_sku
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
        SELECT id, (similarity(search_text, :raw) * 40)::int, 'fuzzy'
          FROM products
         WHERE :fuzzy_enabled
           AND business_id = :business_id
           AND (search_text LIKE :contains OR search_text % :raw)
    ),
    best AS (
        SELECT product_id,
               max(score) AS score,
               -- The kind belonging to the winning score. `DISTINCT ON` cannot be used
               -- here because the aggregate is what selects the row.
               (ARRAY_AGG(match_kind ORDER BY score DESC))[1] AS match_kind
          FROM candidates
         GROUP BY product_id
    )
    SELECT p.id, p.name, p.sku, p.barcode, p.default_selling_price, p.last_cost_price,
           p.reorder_level, p.hsn_code, p.tax_group_id, p.is_active,
           b.name AS brand_name, c.name AS category_name,
           u.code AS unit_code, u.allows_fraction, u.decimal_places,
           ib.quantity AS branch_quantity,
           best.score AS score,
           best.match_kind AS match_kind,
           p.sales_rank, p.normalized_name
      FROM best
      JOIN products p ON p.id = best.product_id
      JOIN units u ON u.id = p.unit_id
      LEFT JOIN brands b ON b.id = p.brand_id
      LEFT JOIN categories c ON c.id = p.category_id
      LEFT JOIN inventory_balances ib
             ON ib.product_id = p.id AND ib.branch_id = :branch_id
     WHERE (:include_inactive OR p.is_active)
     ORDER BY best.score DESC, p.sales_rank DESC, p.normalized_name
     LIMIT :row_cap
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

        `DISTINCT ON (p.id)` collapses the alias join so a product with five matching
        aliases appears once, at its best score.
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
        # Final ordering is applied here rather than in SQL because `DISTINCT ON`
        # requires its own leading ORDER BY. Ties break on sales frequency then name,
        # so the list a cashier sees is stable between keystrokes.
        by_rank = {r.id: (r.sales_rank, r.normalized_name) for r in rows}
        results.sort(
            key=lambda p: (
                -(p.match_score or 0),
                -by_rank[p.id][0],
                by_rank[p.id][1],
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
