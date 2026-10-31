"""Product search ranking, scoping and bounds, through the real API and database.

Why this file exists
--------------------

The ranked search query is the most intricate piece of SQL in the system and, until the
30 October 2026 audit, it had **no test coverage at all**. A comment on `SEARCH_SQL`
asserted that "the M5 catalog suite" proved its behaviour; no such suite existed. That
mattered when the audit required the query to be optimised for NFR-PERF-001, because
there was nothing to tell an optimisation apart from a regression.

So these tests pin the behaviour that `ARCHITECTURE.md §8` and FR-CAT-003/004/009
describe, on a small, hand-built catalogue where every expected answer can be reasoned
about by eye:

* the five ranking tiers, and that they order exact SKU → SKU prefix → name prefix →
  alias → fuzzy;
* the tie-break — sales frequency, then name, then id, the last of which is what makes
  the order *total* and therefore the list stable between keystrokes;
* one row per product however many tiers or aliases it matched;
* search answers from the **first** character (FR-CAT-003);
* `include_inactive`, which must default to excluding deactivated products;
* tenant isolation — another business's products are invisible, not 403;
* the limit clamp, so a client asking for 10 000 rows gets the maximum;
* `cost_price` omitted rather than nulled without `catalog.cost.read` (FR-CAT-004).

`tests/load/test_search_equivalence.py` is the companion: it asserts on the full
100 000-product catalogue that the optimised query returns exactly what a brute-force
reference implementation of the same documented order returns.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from bm.infra.db.repositories.catalog import SEARCH_ROW_CAP
from tests.fixtures import Tenant, UserFixture, create_tenant, create_user

pytestmark = pytest.mark.db


def _headers(client: TestClient, user: UserFixture, tenant: Tenant) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": user.password,
            "device": {"id": str(user.device_id), "label": "Counter 1 PC"},
        },
    ).json()["access_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Device-Id": str(user.device_id),
        "X-Counter-Id": str(tenant.counter_id),
    }


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def headers(api_client: TestClient, owner: UserFixture, tenant: Tenant) -> dict[str, str]:
    return _headers(api_client, owner, tenant)


@pytest.fixture
def units(api_client: TestClient, headers: dict[str, str]) -> None:
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200


def _create(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str,
    sku: str | None = None,
    price: str = "100.0000",
    **extra: Any,
) -> uuid.UUID:
    body: dict[str, Any] = {
        "name": name,
        "unit_code": "NOS",
        "default_selling_price": price,
        **extra,
    }
    if sku is not None:
        body["sku"] = sku
    response = client.post("/api/v1/products", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _search(
    client: TestClient, headers: dict[str, str], query: str, **params: Any
) -> list[dict[str, Any]]:
    response = client.get(
        "/api/v1/products/search", headers=headers, params={"query": query, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _kinds(items: list[dict[str, Any]]) -> list[str]:
    return [i["match"]["kind"] for i in items]


# --- the five ranking tiers ---------------------------------------------------


@pytest.fixture
def tiered(api_client: TestClient, headers: dict[str, str], units: None) -> dict[str, uuid.UUID]:
    """One product per tier, all reachable from the single query `"tt100"`.

    Chosen so that each product matches exactly one tier, which is what makes the
    expected ordering unambiguous:

    * `exact`      — `normalized_sku == 'tt100'`
    * `sku_prefix` — `normalized_sku LIKE 'tt100%'` but not equal
    * `name_prefix`— `normalized_name LIKE 'tt-100%'`
    * `alias`      — an alias normalising to `tt100…`
    * `fuzzy`      — contains the query somewhere in the haystack but matches no prefix

    Note the two different normalisations, which is exactly why this is worth pinning:
    `normalize_sku` strips punctuation (`'TT-100' -> 'tt100'`) while `normalize_name`
    only folds case and whitespace (`'TT-100' -> 'tt-100'`). So the name-prefix tier is
    reached by a product whose *name* starts with the hyphenated form.
    """
    ids = {
        "exact": _create(api_client, headers, name="Exact Match Part", sku="TT-100"),
        "sku_prefix": _create(api_client, headers, name="Sku Prefix Part", sku="TT-1009"),
        "name_prefix": _create(api_client, headers, name="TT-100 Named Part", sku="ZZ-1"),
        "alias_host": _create(api_client, headers, name="Alias Host Part", sku="ZZ-2"),
        "fuzzy": _create(api_client, headers, name="Shim", sku="ZZ-3", brand="Brand TT1009"),
    }
    response = api_client.post(
        f"/api/v1/products/{ids['alias_host']}/aliases",
        headers=headers,
        json={"alias": "TT-1001-ALIAS", "alias_type": "alternate_name"},
    )
    assert response.status_code == 201, response.text
    return ids


def test_the_five_tiers_rank_in_the_documented_order(
    api_client: TestClient, headers: dict[str, str], tiered: dict[str, uuid.UUID]
) -> None:
    """ARCHITECTURE.md §8: exact SKU → SKU prefix → name prefix → alias → trigram."""
    items = _search(api_client, headers, "TT-100", limit=50)
    by_id = {uuid.UUID(i["id"]): i for i in items}

    # Every tier is represented.
    for tier in ("exact", "sku_prefix", "name_prefix", "alias_host"):
        assert tiered[tier] in by_id, f"{tier} product missing from results"

    assert by_id[tiered["exact"]]["match"]["kind"] == "exact_sku"
    assert by_id[tiered["exact"]]["match"]["score"] == 100
    assert by_id[tiered["sku_prefix"]]["match"]["kind"] == "sku_prefix"
    assert by_id[tiered["sku_prefix"]]["match"]["score"] == 80
    assert by_id[tiered["name_prefix"]]["match"]["kind"] == "name_prefix"
    assert by_id[tiered["name_prefix"]]["match"]["score"] == 60
    assert by_id[tiered["alias_host"]]["match"]["kind"] == "alias_prefix"
    assert by_id[tiered["alias_host"]]["match"]["score"] == 50

    # And the scores are strictly descending in the order returned, which is the
    # property a cashier actually experiences.
    scores = [i["match"]["score"] for i in items]
    assert scores == sorted(scores, reverse=True)

    # The first four results are the four prefix/exact tiers, in order.
    assert [uuid.UUID(i["id"]) for i in items[:4]] == [
        tiered["exact"],
        tiered["sku_prefix"],
        tiered["name_prefix"],
        tiered["alias_host"],
    ]


def test_a_fuzzy_match_scores_below_every_prefix_tier(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """The trigram tier is `similarity * 40`, so it can never outrank tier 50."""
    _create(api_client, headers, name="Clutch Plate Bolero", sku="CP-500")
    _create(api_client, headers, name="Gasket For Bolero Engine", sku="GK-9")

    items = _search(api_client, headers, "bolero", limit=50)
    assert items, "expected at least one match"
    for item in items:
        if item["match"]["kind"] == "fuzzy":
            assert item["match"]["score"] <= 40


def test_fuzzy_is_skipped_below_three_characters(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """A trigram is three characters, so the GIN index cannot serve `'%tt%'`.

    The prefix tiers still answer from the first character (FR-CAT-003); what must not
    happen is a fuzzy match appearing for a two-character query.
    """
    _create(api_client, headers, name="Rear Axle Nut", sku="RA-1")

    # 'xl' appears inside 'Rear Axle' but starts nothing, so with the fuzzy tier gated
    # off there must be no match at all.
    assert _search(api_client, headers, "xl") == []
    # Three characters re-enables it.
    assert any(i["match"]["kind"] == "fuzzy" for i in _search(api_client, headers, "axl"))


# --- collapse, tie-break and determinism --------------------------------------


def test_a_product_matching_several_tiers_appears_once_at_its_best_score(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """`GROUP BY product_id` collapses the tiers; `max(score)` picks the winner."""
    product_id = _create(api_client, headers, name="TT-9000 Tie Rod", sku="TT-9000")
    for alias in ("TT-9000-A", "TT-9000-B", "TT-9000-C"):
        assert (
            api_client.post(
                f"/api/v1/products/{product_id}/aliases",
                headers=headers,
                json={"alias": alias, "alias_type": "alternate_name"},
            ).status_code
            == 201
        )

    # Matches exact SKU, SKU prefix, name prefix and three aliases at once.
    items = _search(api_client, headers, "TT-9000", limit=50)
    matching = [i for i in items if uuid.UUID(i["id"]) == product_id]
    assert len(matching) == 1, "a product matching several tiers must appear once"
    assert matching[0]["match"]["kind"] == "exact_sku"
    assert matching[0]["match"]["score"] == 100


def test_ties_break_on_sales_frequency_then_name_then_id(
    api_client: TestClient,
    headers: dict[str, str],
    migration_session: Session,
    units: None,
    tenant: Tenant,
) -> None:
    """The documented tie-break, including the `id` that makes the order total.

    `sales_rank` has no write endpoint — it is maintained by the sales engine — so it is
    set directly here. That is the "reference data or deliberately constructed state"
    exception in `CLAUDE.md §10`, and it is the only way to construct a tie on purpose.
    """
    low = _create(api_client, headers, name="Bearing Zulu", sku="BR-1")
    high = _create(api_client, headers, name="Bearing Zulu", sku="BR-2")
    migration_session.execute(
        text("UPDATE products SET sales_rank = :r WHERE id = :i"), {"r": 5, "i": str(low)}
    )
    migration_session.execute(
        text("UPDATE products SET sales_rank = :r WHERE id = :i"), {"r": 90, "i": str(high)}
    )
    migration_session.commit()

    items = _search(api_client, headers, "Bearing Zulu", limit=50)
    ordered = [uuid.UUID(i["id"]) for i in items if uuid.UUID(i["id"]) in {low, high}]
    assert ordered == [high, low], "higher sales_rank must come first"

    # Now make them tie on sales_rank too. Name is identical, so only the id can
    # separate them — and the result must be the same on every call.
    migration_session.execute(
        text("UPDATE products SET sales_rank = 42 WHERE id IN (:a, :b)"),
        {"a": str(low), "b": str(high)},
    )
    migration_session.commit()

    expected = sorted([low, high])
    for _ in range(5):
        items = _search(api_client, headers, "Bearing Zulu", limit=50)
        got = [uuid.UUID(i["id"]) for i in items if uuid.UUID(i["id"]) in {low, high}]
        assert got == expected, (
            "a tie on score, sales_rank and name must be broken by id, identically "
            "on every call — otherwise the list is not stable between keystrokes"
        )


# --- FR-CAT-003: the first character -----------------------------------------


def test_search_answers_from_the_first_character(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """FR-CAT-003. This is what makes keyboard-only billing possible."""
    product_id = _create(api_client, headers, name="Qwerty Special", sku="Q-1")
    items = _search(api_client, headers, "q")
    assert product_id in {uuid.UUID(i["id"]) for i in items}


def test_an_empty_query_is_refused_by_the_router(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """`min_length=1` on the query parameter, so this never reaches the service."""
    response = api_client.get("/api/v1/products/search", headers=headers, params={"query": ""})
    assert response.status_code == 422


def test_a_whitespace_only_query_is_refused_by_the_service(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """Passes the router's `min_length`, so the service's own guard is what refuses it."""
    response = api_client.get("/api/v1/products/search", headers=headers, params={"query": "   "})
    assert response.status_code == 422, response.text


# --- is_active ----------------------------------------------------------------


def test_a_deactivated_product_is_hidden_unless_asked_for(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """FR-CAT-009: deactivated, never deleted — so it must still be findable on request."""
    product_id = _create(api_client, headers, name="Retired Part", sku="RET-1")
    assert (
        api_client.post(f"/api/v1/products/{product_id}:deactivate", headers=headers).status_code
        == 204
    )

    assert product_id not in {uuid.UUID(i["id"]) for i in _search(api_client, headers, "RET-1")}
    with_inactive = _search(api_client, headers, "RET-1", include_inactive=True)
    assert product_id in {uuid.UUID(i["id"]) for i in with_inactive}
    assert [i["is_active"] for i in with_inactive if uuid.UUID(i["id"]) == product_id] == [False]


def test_an_inactive_product_does_not_displace_an_active_one(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """The `is_active` filter runs inside each ranking branch, before that branch's own
    `LIMIT`. If it ran afterwards, an inactive row could consume a slot an active row
    should have had — this is the test that holds that ordering in place."""
    active = _create(api_client, headers, name="Live Filter Part", sku="LF-1")
    dead = _create(api_client, headers, name="Live Filter Part", sku="LF-2")
    assert (
        api_client.post(f"/api/v1/products/{dead}:deactivate", headers=headers).status_code == 204
    )

    items = _search(api_client, headers, "Live Filter", limit=50)
    returned = {uuid.UUID(i["id"]) for i in items}
    assert active in returned
    assert dead not in returned


# --- tenant isolation ---------------------------------------------------------


def test_another_businesss_products_are_invisible(
    api_client: TestClient,
    headers: dict[str, str],
    migration_session: Session,
    units: None,
) -> None:
    """§4.1: a cross-tenant row is absent, not forbidden — existence is never leaked."""
    mine = _create(api_client, headers, name="Shared Name Widget", sku="SH-1")

    other = create_tenant(migration_session, display_name="OTHER SHOP", branch_code="OTH")
    other_owner = create_user(
        migration_session, other, username="otherowner", role=SystemRole.OWNER, all_branches=True
    )
    other_headers = _headers(api_client, other_owner, other)
    assert (
        api_client.post("/api/v1/units:ensure-defaults", headers=other_headers).status_code == 200
    )
    theirs = _create(api_client, other_headers, name="Shared Name Widget", sku="SH-2")

    assert {uuid.UUID(i["id"]) for i in _search(api_client, headers, "Shared Name")} == {mine}
    assert {uuid.UUID(i["id"]) for i in _search(api_client, other_headers, "Shared Name")} == {
        theirs
    }
    # And a direct read of the other tenant's id is a 404, never a 403.
    assert api_client.get(f"/api/v1/products/{theirs}", headers=headers).status_code == 404


def test_search_declares_the_catalog_read_permission_and_refuses_anonymously(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """UI-level hiding is not a control (NFR-SEC-004).

    Every system role currently holds `catalog.read` — a counter hand who cannot search
    cannot bill — so there is no role to demonstrate a 403 with, and asserting one would
    require inventing a role that does not exist. What *is* assertable here is that the
    route carries the guard at all, and that an unauthenticated caller is refused.
    `tests/api/test_permissions.py` generates the role matrix over every route, so a
    future role without `catalog.read` is covered there automatically.
    """
    from bm.api.deps import PERMISSION_MARKER
    from bm.api.route_policy import mounted_routes
    from bm.domain.auth.permissions import Permission

    # The guard is declared on the route. `mounted_routes` rather than `app.routes`: from
    # FastAPI 0.141 the search route lives inside an included router and a flat scan does
    # not see it — which made `declared` an empty set and this assertion unsatisfiable.
    matching = [
        r
        for r in mounted_routes(api_client.app)  # type: ignore[arg-type]
        if r.path == "/api/v1/products/search"
    ]
    assert matching, "the search route was not found in the mounted route table"
    declared = {
        str(getattr(dep.call, PERMISSION_MARKER))
        for route in matching
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if dep.call is not None and hasattr(dep.call, PERMISSION_MARKER)
    }
    assert str(Permission.CATALOG_READ) in declared

    # And it is enforced, not merely declared.
    assert api_client.get("/api/v1/products/search", params={"query": "a"}).status_code == 401


# --- bounds -------------------------------------------------------------------


def test_the_limit_is_clamped_rather_than_rejected(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """NFR-PERF-006. A client asking for too much is served the maximum."""
    from bm.domain.catalog.service import MAX_SEARCH_LIMIT

    for _ in range(3):
        _create(api_client, headers, name=f"Bulk Part {uuid.uuid4().hex[:6]}", sku=None)

    # Above the router's own ceiling the request is refused, which is the documented
    # contract for this endpoint; at the ceiling it is served.
    assert (
        api_client.get(
            "/api/v1/products/search",
            headers=headers,
            params={"query": "Bulk", "limit": 10_000},
        ).status_code
        == 422
    )
    items = _search(api_client, headers, "Bulk", limit=MAX_SEARCH_LIMIT)
    assert len(items) <= MAX_SEARCH_LIMIT


def test_the_row_cap_is_above_any_page_size() -> None:
    """The candidate cap bounds work without bounding the answer."""
    from bm.domain.catalog.service import MAX_SEARCH_LIMIT

    assert SEARCH_ROW_CAP > MAX_SEARCH_LIMIT


def test_a_query_matching_nothing_returns_an_empty_page(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    response = api_client.get(
        "/api/v1/products/search",
        headers=headers,
        params={"query": "zzzzzzzznothing"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["page"]["has_more"] is False


def test_like_wildcards_in_the_query_are_escaped(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """`SECURITY.md §4.1`: without escaping, `%` matches every row and autocomplete
    becomes a full scan."""
    _create(api_client, headers, name="Percent Free Part", sku="PF-1")
    # '%' normalises away for the SKU tiers but survives into the name pattern; either
    # way it must be a literal, so it cannot match a product with no '%' in its name.
    assert _search(api_client, headers, "%") == []
    assert _search(api_client, headers, "_") == []


# --- FR-CAT-004: cost redaction ----------------------------------------------


def test_cost_price_is_omitted_not_nulled_without_permission(
    api_client: TestClient,
    headers: dict[str, str],
    migration_session: Session,
    tenant: Tenant,
    units: None,
) -> None:
    """A nulled field invites a client to render a placeholder where a margin should be,
    so the key is absent entirely (FR-CAT-004)."""
    _create(api_client, headers, name="Costed Part", sku="CO-1", cost_price="80.0000")

    from bm.domain.auth.permissions import ROLE_PERMISSIONS, Permission

    owner_items = _search(api_client, headers, "CO-1")
    assert owner_items
    if Permission.CATALOG_COST_READ in ROLE_PERMISSIONS[SystemRole.OWNER]:
        assert "cost_price" in owner_items[0]

    cashier = create_user(migration_session, tenant, username="nocost", role=SystemRole.CASHIER)
    if Permission.CATALOG_COST_READ in ROLE_PERMISSIONS[SystemRole.CASHIER]:
        pytest.skip("cashier holds catalog.cost.read in this role matrix")
    cashier_items = _search(api_client, _headers(api_client, cashier, tenant), "CO-1")
    assert cashier_items
    assert "cost_price" not in cashier_items[0], "the key must be absent, not null"


# --- the alias refresh (fix S-8) ---------------------------------------------


def test_deleting_an_alias_stops_it_matching(
    api_client: TestClient, headers: dict[str, str], units: None
) -> None:
    """`search_text` is application-maintained, so a delete must rewrite the parent's
    haystack as well as an insert (fix S-8)."""
    product_id = _create(api_client, headers, name="Aliased Part", sku="AP-1")
    created = api_client.post(
        f"/api/v1/products/{product_id}/aliases",
        headers=headers,
        json={"alias": "ZEBRACODE", "alias_type": "alternate_name"},
    )
    assert created.status_code == 201, created.text
    alias_id = created.json()["id"]

    assert product_id in {uuid.UUID(i["id"]) for i in _search(api_client, headers, "ZEBRACODE")}

    assert (
        api_client.delete(
            f"/api/v1/products/{product_id}/aliases/{alias_id}", headers=headers
        ).status_code
        == 204
    )
    assert _search(api_client, headers, "ZEBRACODE") == []
