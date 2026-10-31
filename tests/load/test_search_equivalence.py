"""Proof that optimising the search query did not change its answers.

Why a separate suite
--------------------

The 30 October 2026 audit measured product-search p95 at 347 ms against the 150 ms of
NFR-PERF-001. The cause was not the ranking and not the indexes: `EXPLAIN (ANALYZE,
BUFFERS)` showed the five detail joins (`products`, `units`, `brands`, `categories`,
`inventory_balances`) running once per *candidate* row — `loops=10000` for `'clutch pl'`,
95 249 buffer hits — and the `LIMIT` discarding all but 500 of them afterwards.

The fix moves the ranking and the `LIMIT` ahead of those joins, and bounds each ranking
branch. Both are meant to be answer-preserving, and "meant to be" is not evidence. So
this suite holds the optimised query against a **brute-force reference implementation**
of the documented order — no per-branch bound, detail joined before the limit, ordered
exactly as `ARCHITECTURE.md §8` states — and requires them to agree row for row, on the
real 100 000-product catalogue where the broad query shapes actually have tens of
thousands of candidates.

It also pins the structural property that the timing depends on: the detail joins must
not run more than `SEARCH_ROW_CAP` times. A timing assertion alone would pass today and
quietly regress the moment someone moved the `LIMIT` back.

Marked `slow`, like the rest of `tests/load`. Run deliberately:

    python scripts/seed_demo.py --products 100000 --reset
    pytest tests/load -m slow -q -s
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from bm.infra.db.repositories.catalog import (
    MIN_FUZZY_QUERY_LENGTH,
    SEARCH_ROW_CAP,
    SEARCH_SQL,
)
from bm.infra.db.sql_patterns import escape_like

pytestmark = [pytest.mark.db, pytest.mark.slow]

SEED_BUSINESS_NAME = "SEED DATA — NOT A REAL BUSINESS"

#: The documented ranking tiers, written out the obvious way: every matching row from
#: every tier, no per-branch bound. This is deliberately the slow shape — it is the
#: thing being checked against, so it must be the one that is easy to read and hard to
#: get wrong.
_REFERENCE_CANDIDATES = """
        SELECT id AS product_id, 100 AS score, 'exact_sku' AS match_kind
          FROM products
         WHERE business_id = :business_id AND normalized_sku = :exact
        UNION ALL
        SELECT id, 80, 'sku_prefix'
          FROM products
         WHERE business_id = :business_id AND normalized_sku LIKE :prefix_sku
        UNION ALL
        SELECT id, 60, 'name_prefix'
          FROM products
         WHERE business_id = :business_id AND normalized_name LIKE :prefix_name
        UNION ALL
        SELECT a.product_id, 50, 'alias_prefix'
          FROM product_aliases a
         WHERE a.business_id = :business_id AND a.normalized_alias LIKE :prefix_sku
        UNION ALL
        SELECT id, (similarity(search_text, :raw) * 40)::int, 'fuzzy'
          FROM products
         WHERE :fuzzy_enabled
           AND business_id = :business_id
           AND (search_text LIKE :contains OR search_text % :raw)
"""

#: The reference. `p.id` last, so the order is total and the expected answer is unique —
#: the optimised query must reproduce it exactly, not merely agree on the scores.
REFERENCE_SQL = text(
    f"""
    WITH candidates AS ({_REFERENCE_CANDIDATES}),
    best AS (
        SELECT product_id,
               max(score) AS score,
               (ARRAY_AGG(match_kind ORDER BY score DESC))[1] AS match_kind
          FROM candidates
         GROUP BY product_id
    )
    SELECT p.id, best.score AS score, best.match_kind AS match_kind,
           p.sales_rank, p.normalized_name
      FROM best
      JOIN products p ON p.id = best.product_id
      JOIN units u ON u.id = p.unit_id
      LEFT JOIN brands b ON b.id = p.brand_id
      LEFT JOIN categories c ON c.id = p.category_id
      LEFT JOIN inventory_balances ib
             ON ib.product_id = p.id AND ib.branch_id = :branch_id
     WHERE (:include_inactive OR p.is_active)
     ORDER BY best.score DESC, p.sales_rank DESC, p.normalized_name, p.id
     LIMIT :row_cap
    """
)

#: The query shapes from the load suite, plus the ones that stress the parts most likely
#: to diverge: single characters (prefix tiers only, fuzzy gated off), multi-word name
#: prefixes, a long exact part code, and a query that matches nothing.
EQUIVALENCE_QUERIES = (
    # the documented load-test shapes
    "TT",
    "TT-10",
    "tt1000",
    "oil",
    "Oil Filter",
    "bolero",
    "clutch pl",
    "HF-12",
    "bearing",
    "6204",
    # single characters — the broadest prefix queries FR-CAT-003 permits
    "t",
    "o",
    "b",
    "a",
    "m",
    "6",
    # multi-word and partial-word name prefixes
    "tie",
    "tie rod",
    "tie rod end bolero",
    "gasket head alto",
    "brake shoe set bullet",
    "wheel cyl",
    "fan belt",
    "shock",
    "air",
    # part-code shapes
    "SK-",
    "sk-105",
    "TT-1000-A",
    "mb",
    "rd-5",
    "204",
    # and one that matches nothing at all
    "zzzzzzzznothing",
)


def _url() -> str:
    url = os.environ.get("BM_MIGRATION_DATABASE_URL")
    if not url:
        from pathlib import Path

        env = Path(__file__).resolve().parents[2] / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("BM_MIGRATION_DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
    if not url:
        pytest.skip("no database URL configured")
    return url


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_url())
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def seeded(engine: Engine) -> dict[str, object]:
    with Session(engine) as session:
        row = session.execute(
            text(
                "SELECT b.id AS business_id, br.id AS branch_id"
                "  FROM businesses b JOIN branches br ON br.business_id = b.id"
                " WHERE b.display_name = :name LIMIT 1"
            ),
            {"name": SEED_BUSINESS_NAME},
        ).first()
        if row is None:
            pytest.skip(
                "no seeded dataset; run: python scripts/seed_demo.py --products 100000 --reset"
            )
        count = session.execute(
            text("SELECT count(*) FROM products WHERE business_id = :b"),
            {"b": row.business_id},
        ).scalar_one()
    print(f"\n  equivalence catalogue: {count} products")
    return {"business_id": row.business_id, "branch_id": row.branch_id, "count": count}


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _normalize_sku(value: str) -> str | None:
    cleaned = re.sub(r"[^a-z0-9]+", "", value.lower())
    return cleaned or None


def _params(seeded: dict[str, object], query: str, *, include_inactive: bool) -> dict[str, object]:
    """The same binding the repository builds, so the comparison is of the SQL only."""
    normalized_query = _normalize_name(query)
    normalized_sku_query = _normalize_sku(query) or normalized_query
    return {
        "business_id": seeded["business_id"],
        "branch_id": seeded["branch_id"],
        "exact": normalized_sku_query,
        "prefix_sku": f"{escape_like(normalized_sku_query)}%",
        "prefix_name": f"{escape_like(normalized_query)}%",
        "contains": f"%{escape_like(normalized_query)}%",
        "raw": normalized_query,
        "include_inactive": include_inactive,
        "row_cap": SEARCH_ROW_CAP,
        "fuzzy_enabled": len(normalized_query) >= MIN_FUZZY_QUERY_LENGTH,
    }


def _answer(rows: list) -> list[tuple[uuid.UUID, int, str]]:
    return [(r.id, r.score, r.match_kind) for r in rows]


@pytest.mark.parametrize("query", EQUIVALENCE_QUERIES)
@pytest.mark.parametrize("include_inactive", [False, True], ids=["active_only", "with_inactive"])
def test_optimised_query_matches_the_brute_force_reference(
    engine: Engine, seeded: dict[str, object], query: str, include_inactive: bool
) -> None:
    """Row for row: same ids, same scores, same `match_kind`s, same order."""
    params = _params(seeded, query, include_inactive=include_inactive)
    with Session(engine) as session:
        reference = _answer(session.execute(REFERENCE_SQL, params).all())
        optimised = _answer(session.execute(SEARCH_SQL, params).all())

    assert len(optimised) == len(reference), (
        f"{query!r}: optimised returned {len(optimised)} rows, reference {len(reference)}"
    )
    for position, (expected, actual) in enumerate(zip(reference, optimised, strict=True)):
        assert actual == expected, (
            f"{query!r}: first divergence at position {position}\n"
            f"  reference: {expected}\n"
            f"  optimised: {actual}"
        )


@pytest.mark.parametrize("query", ["a", "oil", "Oil Filter", "clutch pl", "bolero", "TT"])
def test_the_answer_is_identical_on_repeated_calls(
    engine: Engine, seeded: dict[str, object], query: str
) -> None:
    """`(score, sales_rank, normalized_name)` is not a total order on a real catalogue —
    the seeded 100 000 products resolve the `'air'` tier into tie groups averaging five
    rows — so without `id` as the final discriminator the per-branch `LIMIT` could
    return a different five of a thirteen-row tie on each call. A cashier would see the
    list reorder between keystrokes."""
    params = _params(seeded, query, include_inactive=False)
    with Session(engine) as session:
        answers = {tuple(_answer(session.execute(SEARCH_SQL, params).all())) for _ in range(5)}
    assert len(answers) == 1, f"{query!r} returned {len(answers)} different orders in 5 calls"


@pytest.mark.parametrize("query", ["oil", "Oil Filter", "clutch pl", "bolero", "a", "TT"])
def test_the_detail_joins_run_at_most_row_cap_times(
    engine: Engine, seeded: dict[str, object], query: str
) -> None:
    """The structural guard for NFR-PERF-001.

    This is the assertion that would have caught the audit's finding. Before the fix,
    `units_pkey` showed `loops=10000` for a broad query — one lookup per candidate —
    because the `LIMIT` sat above the joins. A timing test alone would not have failed
    until the catalogue grew.
    """
    params = _params(seeded, query, include_inactive=False)
    with Session(engine) as session:
        plan = "\n".join(
            str(r[0])
            for r in session.execute(
                text("EXPLAIN (ANALYZE, TIMING OFF) " + str(SEARCH_SQL)), params
            ).all()
        )

    # Every `loops=` recorded against a primary-key lookup of a detail table.
    offenders = [
        (table, int(loops))
        for table, loops in re.findall(
            r"Index (?:Only )?Scan using (units_pkey|brands_pkey|categories_pkey|products_pkey)"
            r"[^\n]*loops=(\d+)",
            plan,
        )
        if int(loops) > SEARCH_ROW_CAP
    ]
    assert not offenders, (
        f"{query!r}: detail joins ran more than SEARCH_ROW_CAP={SEARCH_ROW_CAP} times "
        f"({offenders}) — the ranking LIMIT is no longer ahead of the joins.\n{plan}"
    )


def test_the_fuzzy_tier_is_absent_below_three_characters(
    engine: Engine, seeded: dict[str, object]
) -> None:
    """A trigram is three characters, so the GIN index cannot serve a shorter pattern.
    The gate is what keeps a one-character query off a sequential scan."""
    with Session(engine) as session:
        for query in ("a", "t", "tt"):
            rows = session.execute(SEARCH_SQL, _params(seeded, query, include_inactive=False)).all()
            assert all(r.match_kind != "fuzzy" for r in rows), (
                f"{query!r} produced a fuzzy match below MIN_FUZZY_QUERY_LENGTH"
            )
        # Three characters re-enables it, so the gate is a gate and not a removal.
        rows = session.execute(SEARCH_SQL, _params(seeded, "bolero", include_inactive=False)).all()
        assert any(r.match_kind == "fuzzy" for r in rows)
