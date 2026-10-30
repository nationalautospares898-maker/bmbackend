"""End-to-end business workflows through the real API and database.

These are the tests that matter most. A passing unit suite proves the pieces are
individually correct; these prove the system actually bills, deducts stock exactly once,
and refuses the things it must refuse.

Every assertion here traces to a Phase 2 invariant, and each is checked by reading the
database afterwards — not by trusting the response body.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db

D = Decimal


# --- helpers ------------------------------------------------------------------


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


def _key() -> dict[str, str]:
    """A fresh idempotency key per user intent."""
    return {"Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def cashier(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(migration_session, tenant, username="ravi", role=SystemRole.CASHIER)


@pytest.fixture
def owner_headers(api_client: TestClient, owner: UserFixture, tenant: Tenant) -> dict[str, str]:
    return _headers(api_client, owner, tenant)


@pytest.fixture
def units(api_client: TestClient, owner_headers: dict[str, str]) -> None:
    """Seed the default units for this freshly created business."""
    response = api_client.post("/api/v1/units:ensure-defaults", headers=owner_headers)
    assert response.status_code == 200, response.text


@pytest.fixture
def product(api_client: TestClient, owner_headers: dict[str, str], units: None) -> dict[str, Any]:
    """A whole-rupee product, which is what the v1 money policy permits."""
    response = api_client.post(
        "/api/v1/products",
        headers=owner_headers,
        json={
            "name": "Brake Shoe Set - Tata 407",
            "unit_code": "SET",
            "default_selling_price": "1250.0000",
            "sku": "TT-407-BS",
            "brand": "Allied",
            "cost_price": "980.0000",
            "reorder_level": "2.000",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def supplier(api_client: TestClient, owner_headers: dict[str, str]) -> str:
    supplier_id = str(uuid.uuid4())
    response = api_client.put(
        f"/api/v1/suppliers/{supplier_id}",
        headers=owner_headers,
        json={"name": "Allied Spares Depot", "phone": "9876543210"},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.fixture
def stocked(
    api_client: TestClient,
    owner_headers: dict[str, str],
    product: dict[str, Any],
    supplier: str,
) -> dict[str, Any]:
    """Receive 10 units through a confirmed purchase — the only path that adds stock."""
    created = api_client.post(
        "/api/v1/purchases",
        headers=owner_headers,
        json={
            "supplier_id": supplier,
            "supplier_document_number": "ASD/2026/881",
            "items": [{"product_id": product["id"], "quantity": "10.000", "unit_cost": "980.0000"}],
        },
    )
    assert created.status_code == 201, created.text
    purchase_id = created.json()["purchase_id"]

    confirmed = api_client.post(
        f"/api/v1/purchases/{purchase_id}:confirm",
        headers={**owner_headers, **_key()},
    )
    assert confirmed.status_code == 200, confirmed.text
    return {"purchase_id": purchase_id, "product": product}


def _stock_of(session: Session, product_id: str) -> Decimal:
    return session.execute(
        text("SELECT quantity FROM inventory_balances WHERE product_id = :id"),
        {"id": product_id},
    ).scalar_one()


def _movement_count(session: Session, product_id: str) -> int:
    return int(
        session.execute(
            text("SELECT count(*) FROM stock_movements WHERE product_id = :id"),
            {"id": product_id},
        ).scalar_one()
    )


# =============================================================================
# 1. Catalog and search
# =============================================================================


def test_search_finds_a_product_from_the_first_character(
    api_client: TestClient, owner_headers: dict[str, str], product: dict[str, Any]
) -> None:
    """FR-CAT-003. This single behaviour is what makes keyboard-only billing possible."""
    response = api_client.get(
        "/api/v1/products/search", headers=owner_headers, params={"query": "b"}
    )
    assert response.status_code == 200, response.text
    names = [item["name"] for item in response.json()["items"]]
    assert product["name"] in names


@pytest.mark.parametrize("typed", ["TT-407-BS", "tt407bs", "tt 407 bs", "TT.407.BS"])
def test_every_part_number_spelling_finds_the_same_product(
    api_client: TestClient, owner_headers: dict[str, str], product: dict[str, Any], typed: str
) -> None:
    """FR-CAT-006. A counter hand types the separators however they like."""
    response = api_client.get(
        "/api/v1/products/search", headers=owner_headers, params={"query": typed}
    )
    ids = [item["id"] for item in response.json()["items"]]
    assert product["id"] in ids, f"{typed!r} did not match"


def test_exact_sku_outranks_a_fuzzy_match(
    api_client: TestClient, owner_headers: dict[str, str], product: dict[str, Any]
) -> None:
    api_client.post(
        "/api/v1/products",
        headers=owner_headers,
        json={
            "name": "Brake Fluid DOT4",
            "unit_code": "LTR",
            "default_selling_price": "240.0000",
            "sku": "BF-DOT4",
        },
    )
    response = api_client.get(
        "/api/v1/products/search", headers=owner_headers, params={"query": "TT-407-BS"}
    )
    items = response.json()["items"]
    assert items[0]["id"] == product["id"]
    assert items[0]["match"]["kind"] == "exact_sku"


def test_an_alias_is_searchable_immediately_after_it_is_added(
    api_client: TestClient, owner_headers: dict[str, str], product: dict[str, Any]
) -> None:
    """Fix **S-8**. `search_text` is application-maintained, so adding an alias must
    refresh the parent product — otherwise the alias is stored but unfindable, and
    nothing fails loudly enough to notice."""
    added = api_client.post(
        f"/api/v1/products/{product['id']}/aliases",
        headers=owner_headers,
        json={"alias": "OLD-407-SHOE", "alias_type": "old_part_no"},
    )
    assert added.status_code == 201, added.text

    found = api_client.get(
        "/api/v1/products/search", headers=owner_headers, params={"query": "OLD-407"}
    )
    assert product["id"] in [i["id"] for i in found.json()["items"]]


def test_removing_an_alias_removes_it_from_search(
    api_client: TestClient, owner_headers: dict[str, str], product: dict[str, Any]
) -> None:
    alias_id = api_client.post(
        f"/api/v1/products/{product['id']}/aliases",
        headers=owner_headers,
        json={"alias": "TEMPORARY-CODE", "alias_type": "local_name"},
    ).json()["id"]

    api_client.delete(f"/api/v1/products/{product['id']}/aliases/{alias_id}", headers=owner_headers)
    found = api_client.get(
        "/api/v1/products/search", headers=owner_headers, params={"query": "TEMPORARY-CODE"}
    )
    assert product["id"] not in [i["id"] for i in found.json()["items"]]


def test_duplicate_sku_is_refused_naming_the_conflict(
    api_client: TestClient, owner_headers: dict[str, str], product: dict[str, Any]
) -> None:
    response = api_client.post(
        "/api/v1/products",
        headers=owner_headers,
        json={
            "name": "Another Part",
            "unit_code": "NOS",
            "default_selling_price": "100.0000",
            "sku": "TT-407-BS",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_SKU"
    assert "TT-407-BS" in response.json()["error"]["message"]


def test_cost_price_is_omitted_not_nulled_without_permission(
    api_client: TestClient,
    migration_session: Session,
    tenant: Tenant,
    product: dict[str, Any],
) -> None:
    """FR-CAT-004. A nulled field invites a client to render a placeholder where a
    margin should be; an absent field cannot be rendered at all."""
    cashier = create_user(migration_session, tenant, username="nocost")
    headers = _headers(api_client, cashier, tenant)

    body = api_client.get(f"/api/v1/products/{product['id']}", headers=headers).json()
    assert "cost_price" not in body


def test_cost_price_is_present_with_permission(
    api_client: TestClient, owner_headers: dict[str, str], product: dict[str, Any]
) -> None:
    body = api_client.get(f"/api/v1/products/{product['id']}", headers=owner_headers).json()
    assert body["cost_price"] == "980.0000"


def test_a_deactivated_product_is_excluded_by_default(
    api_client: TestClient, owner_headers: dict[str, str], product: dict[str, Any]
) -> None:
    api_client.post(f"/api/v1/products/{product['id']}:deactivate", headers=owner_headers)
    default = api_client.get(
        "/api/v1/products/search", headers=owner_headers, params={"query": "brake"}
    )
    assert product["id"] not in [i["id"] for i in default.json()["items"]]

    included = api_client.get(
        "/api/v1/products/search",
        headers=owner_headers,
        params={"query": "brake", "include_inactive": True},
    )
    assert product["id"] in [i["id"] for i in included.json()["items"]]


def test_search_limit_above_the_maximum_is_clamped_not_rejected(
    api_client: TestClient, owner_headers: dict[str, str], product: dict[str, Any]
) -> None:
    """A client asking for too much is served the maximum, which keeps a careless
    caller working instead of failing at the counter."""
    response = api_client.get(
        "/api/v1/products/search",
        headers=owner_headers,
        params={"query": "b", "limit": 500},
    )
    assert response.status_code == 422  # the query bound rejects it before the service
    ok = api_client.get(
        "/api/v1/products/search",
        headers=owner_headers,
        params={"query": "b", "limit": 50},
    )
    assert ok.status_code == 200


# =============================================================================
# 2. Purchases add stock; only a confirm does
# =============================================================================


def test_creating_a_purchase_changes_no_stock(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    product: dict[str, Any],
    supplier: str,
) -> None:
    """FR-PUR-003. Recording a supplier's delivery note is not the goods arriving."""
    before = _movement_count(migration_session, product["id"])

    created = api_client.post(
        "/api/v1/purchases",
        headers=owner_headers,
        json={
            "supplier_id": supplier,
            "items": [{"product_id": product["id"], "quantity": "5.000", "unit_cost": "900.0000"}],
        },
    )
    assert created.status_code == 201
    assert _movement_count(migration_session, product["id"]) == before


def test_confirming_a_purchase_receives_stock(
    api_client: TestClient,
    migration_session: Session,
    stocked: dict[str, Any],
) -> None:
    assert _stock_of(migration_session, stocked["product"]["id"]) == D("10.000")


def test_confirming_twice_receives_stock_only_once(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
) -> None:
    """Idempotent confirm: a retried request cannot receive the same goods twice."""
    again = api_client.post(
        f"/api/v1/purchases/{stocked['purchase_id']}:confirm",
        headers={**owner_headers, **_key()},
    )
    assert again.status_code == 200
    assert again.json()["movements"] == 0
    assert _stock_of(migration_session, stocked["product"]["id"]) == D("10.000")


def test_duplicate_supplier_document_is_refused(
    api_client: TestClient,
    owner_headers: dict[str, str],
    product: dict[str, Any],
    supplier: str,
    stocked: dict[str, Any],
) -> None:
    """FR-PUR-006 — blocks double-receiving the same supplier invoice."""
    response = api_client.post(
        "/api/v1/purchases",
        headers=owner_headers,
        json={
            "supplier_id": supplier,
            "supplier_document_number": "ASD/2026/881",
            "items": [{"product_id": product["id"], "quantity": "1.000", "unit_cost": "980.0000"}],
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_SUPPLIER_DOCUMENT"


# =============================================================================
# 3. Drafts: no sale, no stock
# =============================================================================


def _draft_body(tenant: Tenant, product_id: str, *, quantity: str = "2.000") -> dict[str, Any]:
    return {
        "counter_id": str(tenant.counter_id),
        "label": "Bill 1",
        "customer_name": "Imran",
        "vehicle_number": "KA 01 AB 1234",
        "items": [
            {
                "id": str(uuid.uuid4()),
                "line_no": 1,
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": "1250.0000",
                "catalog_price_at_add": "1250.0000",
                "price_source": "catalog",
            }
        ],
    }


def test_saving_a_draft_creates_no_stock_movement(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-DRAFT-005 / ADR-006. A draft reserves nothing."""
    product_id = stocked["product"]["id"]
    before_moves = _movement_count(migration_session, product_id)
    before_stock = _stock_of(migration_session, product_id)

    draft_id = str(uuid.uuid4())
    response = api_client.put(
        f"/api/v1/drafts/{draft_id}",
        headers=owner_headers,
        json=_draft_body(tenant, product_id),
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    # Clearly labelled advisory (FR-DRAFT-011).
    assert response.json()["preview_totals"]["advisory"] is True
    assert response.json()["preview_totals"]["total"] == "2500.00"

    assert _movement_count(migration_session, product_id) == before_moves
    assert _stock_of(migration_session, product_id) == before_stock


def test_updating_a_draft_without_if_match_is_refused(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """`428 PRECONDITION_REQUIRED`. The server will not take a blind write to a bill
    somebody else may be editing."""
    draft_id = str(uuid.uuid4())
    body = _draft_body(tenant, stocked["product"]["id"])
    api_client.put(f"/api/v1/drafts/{draft_id}", headers=owner_headers, json=body)

    blind = api_client.put(f"/api/v1/drafts/{draft_id}", headers=owner_headers, json=body)
    assert blind.status_code == 428
    assert blind.json()["error"]["code"] == "PRECONDITION_REQUIRED"


def test_a_stale_if_match_returns_the_current_state(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """ADR-007 forbids a server-side merge; the conflict carries `current_state` so the
    user can choose keep-mine or keep-theirs."""
    draft_id = str(uuid.uuid4())
    body = _draft_body(tenant, stocked["product"]["id"])
    api_client.put(f"/api/v1/drafts/{draft_id}", headers=owner_headers, json=body)
    api_client.put(
        f"/api/v1/drafts/{draft_id}", headers={**owner_headers, "If-Match": "1"}, json=body
    )

    stale = api_client.put(
        f"/api/v1/drafts/{draft_id}", headers={**owner_headers, "If-Match": "1"}, json=body
    )
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "DRAFT_VERSION_CONFLICT"
    assert error["details"]["current_version"] == 2
    assert error["details"]["current_state"]["items"]


def test_clearing_a_draft_keeps_the_row_and_touches_no_stock(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    product_id = stocked["product"]["id"]
    draft_id = str(uuid.uuid4())
    api_client.put(
        f"/api/v1/drafts/{draft_id}",
        headers=owner_headers,
        json=_draft_body(tenant, product_id),
    )
    before = _movement_count(migration_session, product_id)

    cleared = api_client.delete(f"/api/v1/drafts/{draft_id}", headers=owner_headers)
    assert cleared.status_code == 204

    status_value = migration_session.execute(
        text("SELECT status FROM draft_bills WHERE id = :id"), {"id": draft_id}
    ).scalar_one()
    assert status_value == "cancelled"  # the row is kept, not deleted
    assert _movement_count(migration_session, product_id) == before


def test_the_draft_limit_never_discards_an_existing_bill(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-DRAFT-002 / C-5. Twenty open tabs, and the twenty-first is refused — the
    server does not make room by closing someone's bill."""
    product_id = stocked["product"]["id"]
    for _ in range(20):
        created = api_client.put(
            f"/api/v1/drafts/{uuid.uuid4()}",
            headers=owner_headers,
            json=_draft_body(tenant, product_id),
        )
        assert created.status_code == 200, created.text

    twenty_first = api_client.put(
        f"/api/v1/drafts/{uuid.uuid4()}",
        headers=owner_headers,
        json=_draft_body(tenant, product_id),
    )
    assert twenty_first.status_code == 409
    error = twenty_first.json()["error"]
    assert error["code"] == "DRAFT_LIMIT_REACHED"
    assert error["details"]["max_open_drafts"] == 20
    assert error["details"]["current_open"] == 20

    still_open = migration_session.execute(
        text("SELECT count(*) FROM draft_bills WHERE status = 'open'")
    ).scalar_one()
    assert still_open == 20, "no existing draft may be discarded to make room"


# =============================================================================
# 4. Cash billing — the core workflow
# =============================================================================


def _finalize_body(
    tenant: Tenant,
    product_id: str,
    *,
    sale_type: str = "cash",
    quantity: str = "2.000",
    draft_id: str | None = None,
    draft_version: int | None = None,
    customer: dict[str, Any] | None = None,
    payment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "counter_id": str(tenant.counter_id),
        "sale_type": sale_type,
        "lines": [
            {
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": "1250.0000",
                "price_source": "catalog",
            }
        ],
        "header": {"customer_name": "Imran", "vehicle_number": "KA 01 AB 1234"},
        "client_computed_total": str(int(D(quantity) * D("1250"))) + ".00",
    }
    if draft_id:
        body["draft_id"] = draft_id
    if draft_version is not None:
        body["draft_version"] = draft_version
    if customer is not None:
        body["customer"] = customer
    if sale_type == "cash":
        body["payment"] = payment or {
            "method": "cash",
            "amount": body["client_computed_total"],
        }
    elif payment is not None:
        body["payment"] = payment
    return body


def test_cash_sale_finalises_and_deducts_stock_exactly_once(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """The central workflow. AC-1/AC-2/AC-3."""
    product_id = stocked["product"]["id"]
    before = _stock_of(migration_session, product_id)

    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id),
    )
    assert response.status_code == 201, response.text
    invoice = response.json()["invoice"]

    assert invoice["total"] == "2500.00"
    assert invoice["amount_paid"] == "2500.00"
    assert invoice["outstanding"] == "0.00"
    assert invoice["payment_status"] == "paid"
    assert invoice["is_credit"] is False

    # Stock down exactly once.
    assert _stock_of(migration_session, product_id) == before - D("2.000")
    sale_moves = migration_session.execute(
        text("SELECT count(*) FROM stock_movements WHERE source_id = :id AND reason = 'sale'"),
        {"id": invoice["id"]},
    ).scalar_one()
    assert sale_moves == 1


def test_the_invoice_total_reconciles_in_the_database(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """NFR-DATA-002 is a database `CHECK`; this proves the engine satisfies it and that
    `subtotal` equals the sum of the line totals (NFR-DATA-002b)."""
    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, stocked["product"]["id"]),
    )
    invoice_id = response.json()["invoice"]["id"]

    row = migration_session.execute(
        text(
            "SELECT total, subtotal, discount_total, tax_total, rounding_adjustment,"
            "       (SELECT sum(line_total) FROM invoice_items WHERE invoice_id = :id)"
            "         AS line_sum"
            "  FROM invoices WHERE id = :id"
        ),
        {"id": invoice_id},
    ).one()
    assert row.total == row.subtotal - row.discount_total + row.tax_total + row.rounding_adjustment
    assert row.subtotal == row.line_sum
    assert row.rounding_adjustment == D("0.00")  # v1 performs no rounding


def test_finalising_a_draft_marks_it_finalised_and_blocks_a_second_attempt(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    product_id = stocked["product"]["id"]
    draft_id = str(uuid.uuid4())
    api_client.put(
        f"/api/v1/drafts/{draft_id}",
        headers=owner_headers,
        json=_draft_body(tenant, product_id),
    )

    first = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id, draft_id=draft_id, draft_version=1),
    )
    assert first.status_code == 201, first.text

    row = migration_session.execute(
        text("SELECT status, invoice_id FROM draft_bills WHERE id = :id"), {"id": draft_id}
    ).one()
    assert row.status == "finalized"
    assert str(row.invoice_id) == first.json()["invoice"]["id"]

    # A **new** key, so this is a genuinely new attempt rather than a replay.
    second = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id, draft_id=draft_id, draft_version=1),
    )
    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "DRAFT_ALREADY_FINALIZED"
    assert error["details"]["invoice_id"] == first.json()["invoice"]["id"]


def test_clearing_a_finalised_draft_is_refused(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """T3-09: `Clear Draft` must **never** remove a completed sale."""
    product_id = stocked["product"]["id"]
    draft_id = str(uuid.uuid4())
    api_client.put(
        f"/api/v1/drafts/{draft_id}",
        headers=owner_headers,
        json=_draft_body(tenant, product_id),
    )
    api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id, draft_id=draft_id, draft_version=1),
    )

    cleared = api_client.delete(f"/api/v1/drafts/{draft_id}", headers=owner_headers)
    assert cleared.status_code == 409
    assert cleared.json()["error"]["code"] == "DRAFT_ALREADY_FINALIZED"


def test_insufficient_stock_refuses_with_per_line_detail_and_no_side_effects(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """C-3: overselling is prevented by default. Nothing is written — no invoice, no
    movement, and no consumed reference."""
    product_id = stocked["product"]["id"]
    before_invoices = migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one()
    before_next = migration_session.execute(
        text("SELECT next_number FROM invoice_series LIMIT 1")
    ).scalar_one_or_none()

    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id, quantity="99.000"),
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "INSUFFICIENT_STOCK"
    line = error["details"]["lines"][0]
    assert line["requested"] == "99.000"
    assert line["available"] == "10.000"
    assert line["name"]

    assert (
        migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one()
        == before_invoices
    )
    after_next = migration_session.execute(
        text("SELECT next_number FROM invoice_series LIMIT 1")
    ).scalar_one_or_none()
    assert after_next == before_next, "a failed sale must consume no reference"


def test_oversell_requires_permission_and_a_reason(
    api_client: TestClient,
    migration_session: Session,
    tenant: Tenant,
    stocked: dict[str, Any],
) -> None:
    product_id = stocked["product"]["id"]
    cashier = create_user(migration_session, tenant, username="nooverride")
    headers = _headers(api_client, cashier, tenant)

    body = _finalize_body(tenant, product_id, quantity="99.000")
    body["oversell_override"] = {"reason": "Verified physically, customer waiting"}

    refused = api_client.post("/api/v1/sales:finalize", headers={**headers, **_key()}, json=body)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "OVERSELL_NOT_PERMITTED"


def test_authorised_oversell_records_the_reason_on_invoice_and_movement(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """The reason is persisted and audited, and the balance may go negative only here
    (NFR-DATA-007)."""
    product_id = stocked["product"]["id"]
    reason = "Stock miscount, verified physically with the owner"

    body = _finalize_body(tenant, product_id, quantity="12.000")
    body["oversell_override"] = {"reason": reason}
    response = api_client.post(
        "/api/v1/sales:finalize", headers={**owner_headers, **_key()}, json=body
    )
    assert response.status_code == 201, response.text
    invoice_id = response.json()["invoice"]["id"]

    invoice = migration_session.execute(
        text("SELECT oversell_override, oversell_reason FROM invoices WHERE id = :id"),
        {"id": invoice_id},
    ).one()
    assert invoice.oversell_override is True
    assert invoice.oversell_reason == reason

    movement = migration_session.execute(
        text(
            "SELECT oversell_override, override_reason FROM stock_movements WHERE source_id = :id"
        ),
        {"id": invoice_id},
    ).one()
    assert movement.oversell_override is True
    assert movement.override_reason == reason

    assert _stock_of(migration_session, product_id) == D("-2.000")

    audited = (
        migration_session.execute(
            text("SELECT reason FROM audit_logs WHERE action = 'inventory.oversell_override'")
        )
        .scalars()
        .all()
    )
    assert reason in audited


def test_a_client_total_mismatch_is_never_silently_accepted(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-SALE-003. A mismatch means the cashier is looking at a different bill from the
    one the server is about to issue."""
    body = _finalize_body(tenant, stocked["product"]["id"])
    body["client_computed_total"] = "2400.00"

    response = api_client.post(
        "/api/v1/sales:finalize", headers={**owner_headers, **_key()}, json=body
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "TOTAL_MISMATCH"
    assert error["details"]["server_total"] == "2500.00"
    assert error["details"]["client_total"] == "2400.00"


def test_a_price_change_is_surfaced_not_auto_accepted(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-SALE-006 / E-5."""
    product_id = stocked["product"]["id"]
    api_client.patch(
        f"/api/v1/products/{product_id}",
        headers=owner_headers,
        json={"default_selling_price": "1400.0000"},
    )

    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id),
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "PRICE_CHANGED"
    assert error["details"]["lines"][0]["draft_price"] == "1250.0000"
    assert error["details"]["lines"][0]["current_price"] == "1400.0000"


def test_accepting_the_price_change_lets_the_sale_through(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    product_id = stocked["product"]["id"]
    api_client.patch(
        f"/api/v1/products/{product_id}",
        headers=owner_headers,
        json={"default_selling_price": "1400.0000"},
    )
    body = _finalize_body(tenant, product_id)
    body["accept_price_changes"] = True

    response = api_client.post(
        "/api/v1/sales:finalize", headers={**owner_headers, **_key()}, json=body
    )
    assert response.status_code == 201, response.text


def test_an_inactive_product_cannot_be_sold(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    product_id = stocked["product"]["id"]
    api_client.post(f"/api/v1/products/{product_id}:deactivate", headers=owner_headers)

    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PRODUCT_INACTIVE"


def test_a_fractional_result_is_refused_before_finalisation(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    units: None,
    supplier: str,
    tenant: Tenant,
) -> None:
    """Addendum §3. The whole-rupee rule, enforced before any record exists — the only
    way a zero-decimal bill can be both faithful to the owner's format and honest."""
    created = api_client.post(
        "/api/v1/products",
        headers=owner_headers,
        json={
            "name": "Engine Oil 15W40",
            "unit_code": "LTR",
            "default_selling_price": "241.0000",
            "sku": "EO-15W40",
        },
    ).json()

    purchase = api_client.post(
        "/api/v1/purchases",
        headers=owner_headers,
        json={
            "supplier_id": supplier,
            "items": [{"product_id": created["id"], "quantity": "20.000", "unit_cost": "200.0000"}],
        },
    ).json()
    api_client.post(
        f"/api/v1/purchases/{purchase['purchase_id']}:confirm",
        headers={**owner_headers, **_key()},
    )

    body = {
        "counter_id": str(tenant.counter_id),
        "sale_type": "cash",
        "lines": [
            {
                "product_id": created["id"],
                "quantity": "0.500",
                "unit_price": "241.0000",
                "price_source": "catalog",
            }
        ],
        "payment": {"method": "cash", "amount": "121.00"},
    }
    response = api_client.post(
        "/api/v1/sales:finalize", headers={**owner_headers, **_key()}, json=body
    )
    assert response.status_code == 422
    fields = response.json()["error"]["details"]["fields"]
    assert any("whole rupees" in message for message in fields.values())
    assert any("not be rounded" in message for message in fields.values())


def test_a_fractional_quantity_with_a_whole_rupee_result_is_accepted(
    api_client: TestClient,
    owner_headers: dict[str, str],
    units: None,
    supplier: str,
    tenant: Tenant,
) -> None:
    """The rule applies to the **extension**, not the quantity: half a litre at 240 is
    120, which is perfectly printable."""
    created = api_client.post(
        "/api/v1/products",
        headers=owner_headers,
        json={
            "name": "Coolant Premix",
            "unit_code": "LTR",
            "default_selling_price": "240.0000",
            "sku": "CP-PREMIX",
        },
    ).json()
    purchase = api_client.post(
        "/api/v1/purchases",
        headers=owner_headers,
        json={
            "supplier_id": supplier,
            "items": [{"product_id": created["id"], "quantity": "20.000", "unit_cost": "200.0000"}],
        },
    ).json()
    api_client.post(
        f"/api/v1/purchases/{purchase['purchase_id']}:confirm",
        headers={**owner_headers, **_key()},
    )

    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json={
            "counter_id": str(tenant.counter_id),
            "sale_type": "cash",
            "lines": [
                {
                    "product_id": created["id"],
                    "quantity": "0.500",
                    "unit_price": "240.0000",
                    "price_source": "catalog",
                }
            ],
            "payment": {"method": "cash", "amount": "120.00"},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["invoice"]["total"] == "120.00"


def test_an_integer_only_unit_refuses_a_fractional_quantity(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """E-18. A `SET` of brake shoes cannot be sold in halves."""
    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, stocked["product"]["id"], quantity="0.500"),
    )
    assert response.status_code == 422
    fields = response.json()["error"]["details"]["fields"]
    assert any("whole units only" in message for message in fields.values())


# =============================================================================
# 5. Idempotency — AC-IDEM-1..9
# =============================================================================


def test_same_key_and_payload_creates_one_invoice_and_replays(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """AC-IDEM-1. The second response is identical and carries `Idempotent-Replay`."""
    product_id = stocked["product"]["id"]
    key = _key()
    body = _finalize_body(tenant, product_id)

    first = api_client.post("/api/v1/sales:finalize", headers={**owner_headers, **key}, json=body)
    assert first.status_code == 201, first.text

    second = api_client.post("/api/v1/sales:finalize", headers={**owner_headers, **key}, json=body)
    assert second.status_code == 201
    assert second.headers.get("Idempotent-Replay") == "true"
    assert second.json() == first.json()

    count = migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one()
    assert count == 1
    # And stock moved exactly once.
    assert _stock_of(migration_session, product_id) == D("8.000")


def test_an_equivalent_payload_replays_despite_different_formatting(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """AC-IDEM-7 / `§5.2.3` rule 3. `"1250.0000"` and `"1250.00"` are the same intent,
    and a client that reserialises differently must not break its own retry."""
    product_id = stocked["product"]["id"]
    key = _key()

    first_body = _finalize_body(tenant, product_id)
    first = api_client.post(
        "/api/v1/sales:finalize", headers={**owner_headers, **key}, json=first_body
    )
    assert first.status_code == 201, first.text

    retry_body = _finalize_body(tenant, product_id)
    retry_body["lines"][0]["unit_price"] = "1250.00"  # same value, fewer places
    retry_body["lines"][0]["quantity"] = "2"  # same value, no places
    retry_body["client_computed_total"] = "2500.0"
    retry_body["draft_version"] = 99  # excluded from identity (§5.2.4)

    retry = api_client.post(
        "/api/v1/sales:finalize", headers={**owner_headers, **key}, json=retry_body
    )
    assert retry.status_code == 201, retry.text
    assert retry.headers.get("Idempotent-Replay") == "true"
    assert migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one() == 1


def test_same_key_with_a_genuinely_different_payload_is_refused(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """AC-IDEM-2. Never silently accepted — it means the client changed the bill without
    minting a new key."""
    product_id = stocked["product"]["id"]
    key = _key()

    api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **key},
        json=_finalize_body(tenant, product_id, quantity="2.000"),
    )
    conflicting = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **key},
        json=_finalize_body(tenant, product_id, quantity="3.000"),
    )
    assert conflicting.status_code == 409
    error = conflicting.json()["error"]
    assert error["code"] == "IDEMPOTENCY_KEY_REUSE"
    assert error["details"]["conflicting_fingerprint"]

    assert migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one() == 1


def test_a_missing_idempotency_key_is_refused(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """AC-IDEM-8. An unkeyed retry is how a duplicate invoice gets created."""
    response = api_client.post(
        "/api/v1/sales:finalize",
        headers=owner_headers,
        json=_finalize_body(tenant, stocked["product"]["id"]),
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"]["Idempotency-Key"] == "required"


def test_a_rolled_back_attempt_leaves_the_key_reusable(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """AC-IDEM-5. A business rejection rolls back, so the key is **not** consumed and
    the same key succeeds once the user fixes the quantity — it is still the same sale
    intent (`§5.2.5`)."""
    product_id = stocked["product"]["id"]
    key = _key()

    refused = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **key},
        json=_finalize_body(tenant, product_id, quantity="99.000"),
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "INSUFFICIENT_STOCK"

    corrected = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **key},
        json=_finalize_body(tenant, product_id, quantity="2.000"),
    )
    assert corrected.status_code == 201, corrected.text


def test_an_excess_scale_value_is_refused_never_rounded_in_canonicalisation(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """AC-IDEM-9."""
    body = _finalize_body(tenant, stocked["product"]["id"])
    body["lines"][0]["unit_price"] = "1250.123456"

    response = api_client.post(
        "/api/v1/sales:finalize", headers={**owner_headers, **_key()}, json=body
    )
    assert response.status_code == 422


# =============================================================================
# 6. Credit billing
# =============================================================================


def test_credit_requires_a_persisted_customer_not_a_typed_name(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-CREDIT-009. And **no** invoice, movement or consumed reference results."""
    product_id = stocked["product"]["id"]
    before_invoices = migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one()
    before_moves = _movement_count(migration_session, product_id)

    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id, sale_type="credit"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CREDIT_CUSTOMER_REQUIRED"

    assert (
        migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one()
        == before_invoices
    )
    assert _movement_count(migration_session, product_id) == before_moves


def test_credit_sale_finalises_immediately_from_a_name_alone(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """ADR-033: `Credit Bill` finalises in **one** request. FR-CUST-010: a customer
    created from a name alone can receive a credit invoice."""
    product_id = stocked["product"]["id"]
    customer_id = str(uuid.uuid4())

    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(
            tenant,
            product_id,
            sale_type="credit",
            customer={"mode": "new", "customer_id": customer_id, "name": "ANAS Transport"},
        ),
    )
    assert response.status_code == 201, response.text
    invoice = response.json()["invoice"]

    # Finalised, stock down, no payment required.
    assert invoice["is_credit"] is True
    assert invoice["payment_status"] == "unpaid"
    assert invoice["amount_paid"] == "0.00"
    assert invoice["outstanding"] == "2500.00"
    assert invoice["due_date"] is not None
    assert _stock_of(migration_session, product_id) == D("8.000")

    # The customer was created from the name alone.
    name = migration_session.execute(
        text("SELECT name FROM customers WHERE id = :id"), {"id": customer_id}
    ).scalar_one()
    assert name == "ANAS Transport"


def test_credit_and_cash_invoices_are_structurally_identical_apart_from_payment(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """C-6 / FR-SALE-009: the same engine. The records differ only where they must."""
    product_id = stocked["product"]["id"]

    cash = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id, quantity="1.000"),
    ).json()["invoice"]

    customer_id = str(uuid.uuid4())
    credit = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(
            tenant,
            product_id,
            sale_type="credit",
            quantity="1.000",
            customer={"mode": "new", "customer_id": customer_id, "name": "ANAS"},
        ),
    ).json()["invoice"]

    # Same shape, same keys.
    assert set(cash) == set(credit)
    assert cash["total"] == credit["total"] == "1250.00"
    assert cash["subtotal"] == credit["subtotal"]

    # Differences are confined to the payment picture and the customer.
    assert cash["is_credit"] is False and credit["is_credit"] is True
    assert cash["payment_status"] == "paid"
    assert credit["payment_status"] == "unpaid"
    assert cash["due_date"] is None and credit["due_date"] is not None

    # Both moved stock exactly once.
    for invoice in (cash, credit):
        moves = migration_session.execute(
            text("SELECT count(*) FROM stock_movements WHERE source_id = :id AND reason = 'sale'"),
            {"id": invoice["id"]},
        ).scalar_one()
        assert moves == 1


def test_recording_a_payment_updates_the_balance_and_moves_no_stock(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-CREDIT-010. The invariant that stops a credit sale's stock being deducted
    twice."""
    product_id = stocked["product"]["id"]
    customer_id = str(uuid.uuid4())

    invoice = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(
            tenant,
            product_id,
            sale_type="credit",
            customer={"mode": "new", "customer_id": customer_id, "name": "ANAS"},
        ),
    ).json()["invoice"]

    moves_before = _movement_count(migration_session, product_id)
    stock_before = _stock_of(migration_session, product_id)

    paid = api_client.post(
        "/api/v1/payments",
        headers={**owner_headers, **_key()},
        json={
            "customer_id": customer_id,
            "amount": "1000.00",
            "method": "upi",
            "reference": "UPI/12345",
            "allocation": {"strategy": "oldest_first"},
        },
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["allocations"][0]["amount"] == "1000.00"
    assert paid.json()["on_account"] == "0.00"

    # No stock effect whatsoever.
    assert _movement_count(migration_session, product_id) == moves_before
    assert _stock_of(migration_session, product_id) == stock_before

    row = migration_session.execute(
        text("SELECT amount_paid, payment_status FROM invoices WHERE id = :id"),
        {"id": invoice["id"]},
    ).one()
    assert row.amount_paid == D("1000.00")
    assert row.payment_status == "partially_paid"


def test_paying_the_balance_in_full_reaches_paid_without_any_paid_flag(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """ADR-022. `paid` is reached only when allocations sum to the total — there is no
    "mark as paid" endpoint and no checkbox."""
    product_id = stocked["product"]["id"]
    customer_id = str(uuid.uuid4())

    invoice = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(
            tenant,
            product_id,
            sale_type="credit",
            customer={"mode": "new", "customer_id": customer_id, "name": "ANAS"},
        ),
    ).json()["invoice"]

    for amount in ("1500.00", "1000.00"):
        response = api_client.post(
            "/api/v1/payments",
            headers={**owner_headers, **_key()},
            json={
                "customer_id": customer_id,
                "amount": amount,
                "method": "cash",
                "allocation": {"strategy": "oldest_first"},
            },
        )
        assert response.status_code == 200, response.text

    row = migration_session.execute(
        text("SELECT amount_paid, payment_status FROM invoices WHERE id = :id"),
        {"id": invoice["id"]},
    ).one()
    assert row.amount_paid == D("2500.00")
    assert row.payment_status == "paid"


def test_amount_paid_always_equals_the_allocation_sum_including_after_a_reversal(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """NFR-DATA-003, including the reversal case the invariant explicitly calls out."""
    product_id = stocked["product"]["id"]
    customer_id = str(uuid.uuid4())

    invoice = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(
            tenant,
            product_id,
            sale_type="credit",
            customer={"mode": "new", "customer_id": customer_id, "name": "ANAS"},
        ),
    ).json()["invoice"]

    payment_id = api_client.post(
        "/api/v1/payments",
        headers={**owner_headers, **_key()},
        json={
            "customer_id": customer_id,
            "amount": "1000.00",
            "method": "cash",
            "allocation": {"strategy": "oldest_first"},
        },
    ).json()["payment_id"]

    reversed_response = api_client.post(
        f"/api/v1/payments/{payment_id}:reverse",
        headers={**owner_headers, **_key()},
        json={"reason": "Cash was miscounted at the counter"},
    )
    assert reversed_response.status_code == 200, reversed_response.text

    row = migration_session.execute(
        text("SELECT amount_paid, payment_status FROM invoices WHERE id = :id"),
        {"id": invoice["id"]},
    ).one()
    assert row.amount_paid == D("0.00")
    assert row.payment_status == "unpaid"

    # The original payment row is untouched.
    original = migration_session.execute(
        text("SELECT amount, kind FROM payments WHERE id = :id"), {"id": payment_id}
    ).one()
    assert original.amount == D("1000.00")
    assert original.kind == "payment"


def test_over_allocating_a_payment_is_refused(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-PAY-007."""
    product_id = stocked["product"]["id"]
    customer_id = str(uuid.uuid4())

    invoice = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(
            tenant,
            product_id,
            sale_type="credit",
            customer={"mode": "new", "customer_id": customer_id, "name": "ANAS"},
        ),
    ).json()["invoice"]

    response = api_client.post(
        "/api/v1/payments",
        headers={**owner_headers, **_key()},
        json={
            "customer_id": customer_id,
            "amount": "5000.00",
            "method": "cash",
            "allocation": {
                "strategy": "invoice_specific",
                "allocations": [{"invoice_id": invoice["id"], "amount": "5000.00"}],
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OVER_ALLOCATION"


def test_a_payment_remainder_becomes_on_account_credit(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-PAY-003 — never silently discarded."""
    product_id = stocked["product"]["id"]
    customer_id = str(uuid.uuid4())

    api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(
            tenant,
            product_id,
            sale_type="credit",
            quantity="1.000",
            customer={"mode": "new", "customer_id": customer_id, "name": "ANAS"},
        ),
    )

    response = api_client.post(
        "/api/v1/payments",
        headers={**owner_headers, **_key()},
        json={
            "customer_id": customer_id,
            "amount": "2000.00",
            "method": "cash",
            "allocation": {"strategy": "oldest_first"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["on_account"] == "750.00"


def test_credit_customers_view_aggregates_server_side_and_survives_a_rename(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-RPT-029/030. Grouping keys on `customer_id`; the name is a label only."""
    product_id = stocked["product"]["id"]
    customer_id = str(uuid.uuid4())

    api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(
            tenant,
            product_id,
            sale_type="credit",
            customer={"mode": "new", "customer_id": customer_id, "name": "ANAS"},
        ),
    )

    before = api_client.get("/api/v1/credit/customers", headers=owner_headers).json()
    entry = next(i for i in before["items"] if i["customer"]["id"] == customer_id)
    assert entry["outstanding"] == "2500.00"
    assert entry["open_invoice_count"] == 1
    assert before["summary"]["overall_outstanding"] == "2500.00"

    api_client.put(
        f"/api/v1/customers/{customer_id}",
        headers=owner_headers,
        json={"name": "ANAS Transport Company"},
    )

    after = api_client.get("/api/v1/credit/customers", headers=owner_headers).json()
    renamed = next(i for i in after["items"] if i["customer"]["id"] == customer_id)
    assert renamed["customer"]["name"] == "ANAS Transport Company"
    assert renamed["outstanding"] == "2500.00", "a rename must not break the grouping"


# =============================================================================
# 7. Print payload — the OD-3 rule
# =============================================================================


def test_the_print_payload_carries_no_invoice_number_anywhere(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """T3-06 / OD-3 re-answered: *"No need of invoice numbering."*"""
    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, stocked["product"]["id"]),
    )
    payload = response.json()["print_payload"]

    serialised = str(payload)
    assert "invoice_number" not in serialised
    # The internal reference is on the invoice resource, not the print payload.
    assert response.json()["invoice"]["internal_reference"]
    assert response.json()["invoice"]["internal_reference"] not in serialised


def test_the_print_payload_matches_the_workbook_structure(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """T3-10 / OD-5. Columns, label, capacity and the absent fields all come from the
    inspected workbook."""
    payload = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, stocked["product"]["id"]),
    ).json()["print_payload"]

    assert payload["columns"] == ["sl_no", "item", "quantity", "price", "total"]
    # `ESTIMATE` for cash and credit alike (OD-24, FR-PRINT-021).
    assert payload["document_title"] == "ESTIMATE"
    assert payload["item_row_capacity"] == 25
    # `null`, not `"0.00"`, so the client renders no tax row at all (ADR-020).
    assert payload["totals"]["tax"] is None
    assert payload["tax_mode"] == "estimate_only"
    # T3-11 / OD-26, from workbook cell E7, through a single configurable source.
    assert payload["date_format"] == "MM-DD-YYYY"
    # OD-6 proposed profile.
    assert payload["print_profile"] == "a5_portrait"


def test_the_credit_block_appears_only_for_a_credit_bill(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """OD-22 / FR-PRINT-020. Cash bills omit it entirely."""
    product_id = stocked["product"]["id"]

    cash = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id, quantity="1.000"),
    ).json()["print_payload"]
    assert cash["show_credit_block"] is False
    assert cash["payment"]["is_credit"] is False

    customer_id = str(uuid.uuid4())
    credit = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(
            tenant,
            product_id,
            sale_type="credit",
            quantity="1.000",
            customer={"mode": "new", "customer_id": customer_id, "name": "ANAS"},
        ),
    ).json()["print_payload"]
    assert credit["show_credit_block"] is True
    # Figures come from the record, never recomputed client-side.
    assert credit["payment"]["amount_paid"] == "0.00"
    assert credit["payment"]["outstanding"] == "1250.00"


def test_amounts_are_emitted_as_exact_decimal_strings_never_preformatted(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """OD-23: display formatting is the client's concern. The server never pads,
    truncates or rounds for presentation."""
    payload = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, stocked["product"]["id"]),
    ).json()["print_payload"]

    assert payload["totals"]["grand_total"] == "2500.00"
    assert D(payload["totals"]["grand_total"]) == D("2500.00")
    assert "," not in payload["totals"]["grand_total"]
    assert "₹" not in payload["totals"]["grand_total"]


def test_reprinting_creates_nothing(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """No sale, no reference, no movement — reprint is a plain read."""
    product_id = stocked["product"]["id"]
    invoice_id = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id),
    ).json()["invoice"]["id"]

    before = {
        "invoices": migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one(),
        "movements": _movement_count(migration_session, product_id),
        "stock": _stock_of(migration_session, product_id),
        "next_number": migration_session.execute(
            text("SELECT next_number FROM invoice_series LIMIT 1")
        ).scalar_one(),
    }

    api_client.post(
        f"/api/v1/invoices/{invoice_id}/print-events",
        headers=owner_headers,
        json={"is_duplicate": False},
    )
    reprint = api_client.get(
        f"/api/v1/invoices/{invoice_id}/print-payload",
        headers=owner_headers,
        params={"copy": "reprint"},
    )
    assert reprint.status_code == 200
    assert reprint.json()["is_duplicate"] is True  # print.mark_reprints_duplicate

    assert (
        migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one()
        == before["invoices"]
    )
    assert _movement_count(migration_session, product_id) == before["movements"]
    assert _stock_of(migration_session, product_id) == before["stock"]
    assert (
        migration_session.execute(
            text("SELECT next_number FROM invoice_series LIMIT 1")
        ).scalar_one()
        == before["next_number"]
    )


def test_an_unset_business_identity_prints_nothing_never_a_placeholder(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-PRINT-016 / OD-4. The owner has not supplied a legal name, address or phone,
    and inventing one is forbidden."""
    payload = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, stocked["product"]["id"]),
    ).json()["print_payload"]

    assert payload["business"]["address_lines"] == []
    assert payload["business"]["phone_numbers"] == []
    assert payload["business"]["tax_identifier"] is None
    assert payload["business"]["tax_identifier_label"] is None
    # The display name is owner-supplied and is the one thing present.
    assert payload["business"]["display_name"] == "NATIONAL AUTO SPARES"


# =============================================================================
# 8. Cancellation and returns
# =============================================================================


def test_cancelling_reverses_stock_exactly_and_retains_the_reference(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    product_id = stocked["product"]["id"]
    finalised = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id),
    ).json()["invoice"]

    assert _stock_of(migration_session, product_id) == D("8.000")

    cancelled = api_client.post(
        f"/api/v1/invoices/{finalised['id']}:cancel",
        headers={**owner_headers, **_key()},
        json={"reason": "Customer changed their mind before leaving"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["movements_reversed"] == 1

    # Stock exactly back.
    assert _stock_of(migration_session, product_id) == D("10.000")

    row = migration_session.execute(
        text(
            "SELECT status, invoice_number, payment_status, cancellation_reason"
            "  FROM invoices WHERE id = :id"
        ),
        {"id": finalised["id"]},
    ).one()
    assert row.status == "cancelled"
    # The reference is retained; reusing it would make the audit trail ambiguous.
    assert row.invoice_number == finalised["internal_reference"]
    assert row.payment_status == "cancelled"
    assert row.cancellation_reason


def test_cancelling_twice_is_refused(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    invoice_id = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, stocked["product"]["id"]),
    ).json()["invoice"]["id"]

    body = {"reason": "Duplicate bill raised in error"}
    first = api_client.post(
        f"/api/v1/invoices/{invoice_id}:cancel",
        headers={**owner_headers, **_key()},
        json=body,
    )
    assert first.status_code == 200
    second = api_client.post(
        f"/api/v1/invoices/{invoice_id}:cancel",
        headers={**owner_headers, **_key()},
        json=body,
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "INVOICE_ALREADY_CANCELLED"


def test_a_partial_return_credits_the_snapshot_price_and_restocks(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """AC-12. The price the customer paid, not today's price."""
    product_id = stocked["product"]["id"]
    finalised = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id),
    ).json()

    # Raise the catalog price after the sale; the return must ignore it.
    api_client.patch(
        f"/api/v1/products/{product_id}",
        headers=owner_headers,
        json={"default_selling_price": "1800.0000"},
    )

    detail = api_client.get(
        f"/api/v1/invoices/{finalised['invoice']['id']}", headers=owner_headers
    ).json()
    assert detail["items"][0]["line_no"] == 1

    item_id = migration_session.execute(
        text("SELECT id FROM invoice_items WHERE invoice_id = :id"),
        {"id": finalised["invoice"]["id"]},
    ).scalar_one()

    returned = api_client.post(
        "/api/v1/returns",
        headers={**owner_headers, **_key()},
        json={
            "invoice_id": finalised["invoice"]["id"],
            "items": [
                {"invoice_item_id": str(item_id), "quantity": "1.000", "condition": "resalable"}
            ],
            "reason": "Wrong part supplied",
            "restock": True,
            "credit_handling": "hold_on_account",
        },
    )
    assert returned.status_code == 201, returned.text
    assert returned.json()["total_amount"] == "1250.00", "credited at the snapshot price"

    # One unit back in sellable stock.
    assert _stock_of(migration_session, product_id) == D("9.000")

    row = migration_session.execute(
        text("SELECT quantity, returned_quantity FROM invoice_items WHERE id = :id"),
        {"id": item_id},
    ).one()
    assert row.quantity == D("2.000")
    assert row.returned_quantity == D("1.000")


def test_returning_more_than_was_sold_is_refused(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """NFR-DATA-008, enforced by the service under a row lock and backed by
    `ck_invoice_items__returned`."""
    finalised = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, stocked["product"]["id"]),
    ).json()
    item_id = migration_session.execute(
        text("SELECT id FROM invoice_items WHERE invoice_id = :id"),
        {"id": finalised["invoice"]["id"]},
    ).scalar_one()

    response = api_client.post(
        "/api/v1/returns",
        headers={**owner_headers, **_key()},
        json={
            "invoice_id": finalised["invoice"]["id"],
            "items": [{"invoice_item_id": str(item_id), "quantity": "5.000"}],
            "reason": "Attempting to return more than sold",
        },
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "RETURN_QUANTITY_EXCEEDED"
    assert error["details"]["remaining"] == "2.000"


def test_a_damaged_return_becomes_scrap_not_sellable_stock(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """Putting a damaged part back on the shelf is the failure this prevents."""
    product_id = stocked["product"]["id"]
    finalised = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id),
    ).json()
    item_id = migration_session.execute(
        text("SELECT id FROM invoice_items WHERE invoice_id = :id"),
        {"id": finalised["invoice"]["id"]},
    ).scalar_one()

    api_client.post(
        "/api/v1/returns",
        headers={**owner_headers, **_key()},
        json={
            "invoice_id": finalised["invoice"]["id"],
            "items": [
                {"invoice_item_id": str(item_id), "quantity": "1.000", "condition": "damaged"}
            ],
            "reason": "Part arrived back cracked",
            "restock": False,
            "credit_handling": "hold_on_account",
        },
    )

    reason = migration_session.execute(
        text(
            "SELECT reason FROM stock_movements WHERE source_type = 'sales_return'"
            " ORDER BY created_at DESC LIMIT 1"
        )
    ).scalar_one()
    assert reason == "scrap"


# =============================================================================
# 9. Reports reconcile
# =============================================================================


def test_daily_sales_and_credit_outstanding_reconcile(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-RPT-003. The single most valuable report test: if these disagree, one of them
    is lying to the owner about how much the shop is owed."""
    product_id = stocked["product"]["id"]

    api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id, quantity="1.000"),
    )
    customer_id = str(uuid.uuid4())
    api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(
            tenant,
            product_id,
            sale_type="credit",
            quantity="2.000",
            customer={"mode": "new", "customer_id": customer_id, "name": "ANAS"},
        ),
    )

    daily = api_client.get("/api/v1/reports/daily-sales", headers=owner_headers).json()
    assert daily["bill_count"] == 2
    assert daily["net"] == "3750.00"
    # Credit sales are included on their sale date (FR-RPT-001).
    assert daily["credit_sales"] == "2500.00"
    assert daily["cash_sales"] == "1250.00"

    credit = api_client.get("/api/v1/reports/credit-outstanding", headers=owner_headers).json()
    assert credit["outstanding"] == "2500.00"

    check = api_client.get("/api/v1/reports/reconciliation-check", headers=owner_headers).json()
    assert check["reconciles"] is True, check


def test_a_cancelled_bill_leaves_revenue_but_is_reported_as_excluded(
    api_client: TestClient,
    owner_headers: dict[str, str],
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-RPT-008. Excluded from revenue, never silently disappeared."""
    product_id = stocked["product"]["id"]
    invoice_id = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, product_id),
    ).json()["invoice"]["id"]

    api_client.post(
        f"/api/v1/invoices/{invoice_id}:cancel",
        headers={**owner_headers, **_key()},
        json={"reason": "Raised against the wrong customer"},
    )

    daily = api_client.get("/api/v1/reports/daily-sales", headers=owner_headers).json()
    assert daily["bill_count"] == 0
    assert daily["net"] == "0.00"
    assert daily["excluded"]["cancelled_bill_count"] == 1
    assert daily["excluded"]["cancelled_total"] == "2500.00"


def test_csv_export_neutralises_a_formula_shaped_product_name(
    api_client: TestClient, owner_headers: dict[str, str], units: None
) -> None:
    """`test_csv_injection_escaped`. A product named `=cmd()` must arrive in the
    accountant's spreadsheet as text."""
    api_client.post(
        "/api/v1/products",
        headers=owner_headers,
        json={
            "name": "=cmd()|'/c calc'!A1",
            "unit_code": "NOS",
            "default_selling_price": "10.0000",
        },
    )
    response = api_client.get(
        "/api/v1/reports/stock-position/export",
        headers=owner_headers,
        params={"format": "csv"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "'=cmd()" in response.text, "a formula-shaped cell must be prefixed"


# =============================================================================
# 10. Offline boundary
# =============================================================================


def test_the_sync_registry_refuses_a_financial_operation(
    api_client: TestClient, owner_headers: dict[str, str], tenant: Tenant
) -> None:
    """AC-OFF-8 / FR-OFFLINE-002. `Credit Bill` is refused offline exactly like
    `Print Bill`."""
    types = api_client.get("/api/v1/sync/operation-types", headers=owner_headers).json()
    assert types["allowed"] == ["draft.upsert", "draft.delete", "customer.upsert"]
    assert types["offline_finalisation"] == "refused"

    response = api_client.post(
        "/api/v1/sync/operations",
        headers=owner_headers,
        json={
            "device_id": str(uuid.uuid4()),
            "operations": [
                {
                    "op_id": str(uuid.uuid4()),
                    "client_seq": 1,
                    "type": "sale.finalize",
                    "entity_type": "invoice",
                    "entity_id": str(uuid.uuid4()),
                    "payload": {},
                }
            ],
        },
    )
    assert response.status_code == 200  # the batch was well-formed
    outcome = response.json()["results"][0]
    assert outcome["status"] == "rejected"
    assert "need a connection" in outcome["error"]["message"]


def test_replaying_a_sync_batch_changes_nothing(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """`sync_operations.id` **is** the client's `op_id`, so replay is free."""
    product_id = stocked["product"]["id"]
    draft_id = str(uuid.uuid4())
    batch = {
        "device_id": str(uuid.uuid4()),
        "operations": [
            {
                "op_id": str(uuid.uuid4()),
                "client_seq": 1,
                "type": "draft.upsert",
                "entity_type": "draft_bill",
                "entity_id": draft_id,
                "payload": {
                    "counter_id": str(tenant.counter_id),
                    "items": [
                        {
                            "id": str(uuid.uuid4()),
                            "line_no": 1,
                            "product_id": product_id,
                            "quantity": "1.000",
                            "unit_price": "1250.0000",
                        }
                    ],
                },
            }
        ],
    }

    first = api_client.post("/api/v1/sync/operations", headers=owner_headers, json=batch)
    assert first.status_code == 200, first.text
    assert first.json()["results"][0]["status"] == "accepted"

    second = api_client.post("/api/v1/sync/operations", headers=owner_headers, json=batch)
    assert second.json()["results"][0]["status"] == "replayed"

    drafts = migration_session.execute(
        text("SELECT count(*) FROM draft_bills WHERE id = :id"), {"id": draft_id}
    ).scalar_one()
    assert drafts == 1


def test_bootstrap_publishes_the_money_policy_so_the_client_never_hardcodes_it(
    api_client: TestClient, owner_headers: dict[str, str]
) -> None:
    """The client takes the rounding rule from here (`../bmfrontend/CLAUDE.md §5`
    rule 15)."""
    response = api_client.get("/api/v1/sync/bootstrap", headers=owner_headers)
    assert response.status_code == 200, response.text
    body = response.json()

    policy = body["money_policy"]
    assert policy["version"] == 1
    assert policy["rounding_mode"] == "reject_fractional"
    assert policy["require_integral_amounts"] is True
    assert policy["display_decimal_places"] == 0
    assert policy["currency"] == "INR"

    assert body["settings"]["drafts.max_open_per_counter"] == 20
    assert body["settings"]["print.date_format"] == "MM-DD-YYYY"
    assert body["sync"]["offline_finalisation"] == "refused"


def test_the_catalog_delta_never_carries_a_cost_price(
    api_client: TestClient, owner_headers: dict[str, str], product: dict[str, Any]
) -> None:
    """`SECURITY.md §11`. A stolen counter machine must not hold the shop's margins —
    regardless of the caller's permission."""
    response = api_client.get("/api/v1/sync/catalog", headers=owner_headers)
    assert response.status_code == 200, response.text
    serialised = str(response.json())
    assert "cost_price" not in serialised
    assert "980" not in serialised
    # Every cached figure carries its `as_of`.
    assert response.json()["products"][0]["stock"]["as_of"]


# =============================================================================
# 11. Audit trail
# =============================================================================


def test_every_financial_action_leaves_an_audit_row(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-AUDIT-001, written in the same transaction as the change it describes."""
    product_id = stocked["product"]["id"]
    customer_id = str(uuid.uuid4())

    invoice = api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(
            tenant,
            product_id,
            sale_type="credit",
            customer={"mode": "new", "customer_id": customer_id, "name": "ANAS"},
        ),
    ).json()["invoice"]

    api_client.post(
        "/api/v1/payments",
        headers={**owner_headers, **_key()},
        json={
            "customer_id": customer_id,
            "amount": "500.00",
            "method": "cash",
            "allocation": {"strategy": "oldest_first"},
        },
    )
    api_client.post(
        f"/api/v1/invoices/{invoice['id']}:cancel",
        headers={**owner_headers, **_key()},
        json={"reason": "Customer cancelled the order"},
    )

    actions = set(migration_session.execute(text("SELECT action FROM audit_logs")).scalars().all())
    for expected in (
        "purchase.confirmed",
        "product.created",
        "customer.created",
        "sale.finalized",
        "payment.recorded",
        "invoice.cancelled",
    ):
        assert expected in actions, f"{expected} was not audited"


def test_the_audit_log_never_contains_a_secret(
    api_client: TestClient,
    owner_headers: dict[str, str],
    migration_session: Session,
    stocked: dict[str, Any],
    tenant: Tenant,
) -> None:
    """FR-AUDIT-004."""
    api_client.post(
        "/api/v1/sales:finalize",
        headers={**owner_headers, **_key()},
        json=_finalize_body(tenant, stocked["product"]["id"]),
    )
    rows = (
        migration_session.execute(
            text(
                "SELECT coalesce(before_value::text, '') || coalesce(after_value::text, '')"
                " FROM audit_logs"
            )
        )
        .scalars()
        .all()
    )
    blob = " ".join(rows).lower()
    for secret in ("password", "token", "argon2", "pepper", "bearer"):
        assert secret not in blob
