"""The low-stock threshold, asserted across **every** surface that implements it.

Owner decision, 2026-11-22: an item is low when `current_quantity <= minimum_quantity`.
A part with 10 on the shelf and a minimum of 10 **is** low.

## Why this file exists rather than one assertion per module's own test

The rule was implemented six times and two of those disagreed. The listing surfaces
compared `<` while the notification producers and the post-sale warning compared `<=`,
so a part sold down to exactly its minimum raised a bell alert that the Stock screen
refused to list — and the next reconciliation then resolved the alert, so the notice
appeared and vanished with nothing having changed.

Per-module tests could not catch that: each one was internally consistent. What was
missing was a test that asks *the same question of every surface at once* and requires
the same answer. That is this file. The sixth implementation is client-side and is
covered by `bmfrontend/src/features/stock/lowStock.test.ts`.

Everything below goes through real HTTP endpoints, because the surfaces under test are
endpoints — a unit test of the comparison would prove the operator works, not that the
Stock screen and the bell agree.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db

#: The owner's three cases: below, **at**, above.
BOUNDARY_CASES = [
    ("9.000", True),
    ("10.000", True),
    ("11.000", False),
]
MINIMUM = "10.000"


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    user = create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )
    migration_session.commit()
    return user


@pytest.fixture
def headers(api_client: TestClient, owner: UserFixture, tenant: Tenant) -> dict[str, str]:
    token = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": owner.username,
            "password": owner.password,
            "device": {"id": str(owner.device_id), "label": "Counter 1 PC"},
        },
    ).json()["access_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Device-Id": str(owner.device_id),
        "X-Counter-Id": str(tenant.counter_id),
    }


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _stocked_product(
    api_client: TestClient, headers: dict[str, str], *, name: str, quantity: str
) -> str:
    """A product with `MINIMUM` as its reorder level and `quantity` on the shelf.

    Stock arrives through a confirmed purchase receipt rather than the stock sheet,
    because the sheet restates `min_qty` on every row and these tests are about the
    minimum staying put.
    """
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200
    created = api_client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": name,
            "unit_code": "NOS",
            "default_selling_price": "100.0000",
            "sku": f"TH-{uuid.uuid4().hex[:8]}",
            "reorder_level": MINIMUM,
        },
    )
    assert created.status_code == 201, created.text
    product_id = str(created.json()["id"])

    supplier = api_client.put(
        f"/api/v1/suppliers/{uuid.uuid4()}",
        headers=headers,
        json={"name": "Threshold Supplier", "phone": "9876543210"},
    )
    assert supplier.status_code == 200, supplier.text
    purchase = api_client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "supplier_id": supplier.json()["id"],
            "supplier_document_number": f"TH/{uuid.uuid4().hex[:6]}",
            "items": [{"product_id": product_id, "quantity": quantity, "unit_cost": "80.0000"}],
        },
    )
    assert purchase.status_code == 201, purchase.text
    confirmed = api_client.post(
        f"/api/v1/purchases/{purchase.json()['purchase_id']}:confirm",
        headers={**headers, **_key()},
    )
    assert confirmed.status_code == 200, confirmed.text
    return product_id


# --- surface 1: the Stock screen's "Low stock only" filter ----------------------


@pytest.mark.parametrize("quantity,expected_low", BOUNDARY_CASES)
def test_the_stock_screen_filter_is_at_or_below(
    api_client: TestClient, headers: dict[str, str], quantity: str, expected_low: bool
) -> None:
    """`GET /inventory/balances?low_stock_only=true` — what the tick box calls."""
    product_id = _stocked_product(api_client, headers, name="Filter Part", quantity=quantity)

    listed = api_client.get(
        "/api/v1/inventory/balances",
        params={"low_stock_only": True},
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    ids = {row["product_id"] for row in listed.json()["items"]}

    assert (product_id in ids) is expected_low, f"{quantity} against a minimum of {MINIMUM}"


# --- surface 2: the product's own badge -----------------------------------------


@pytest.mark.parametrize("quantity,expected_low", BOUNDARY_CASES)
def test_the_product_badge_is_at_or_below(
    api_client: TestClient, headers: dict[str, str], quantity: str, expected_low: bool
) -> None:
    """`is_low_stock` on the product payload — the badge the Stock screen renders."""
    product_id = _stocked_product(api_client, headers, name="Badge Part", quantity=quantity)

    found = api_client.get(
        "/api/v1/products/search", params={"query": "Badge Part"}, headers=headers
    )
    assert found.status_code == 200, found.text
    match = next(item for item in found.json()["items"] if item["id"] == product_id)

    assert match["is_low_stock"] is expected_low, f"{quantity} against a minimum of {MINIMUM}"


# --- surface 3: the stock report -------------------------------------------------


@pytest.mark.parametrize("quantity,expected_low", BOUNDARY_CASES)
def test_the_stock_report_is_at_or_below(
    api_client: TestClient, headers: dict[str, str], quantity: str, expected_low: bool
) -> None:
    """`GET /reports/stock-position?low_stock_only=true`."""
    _stocked_product(api_client, headers, name="Report Part", quantity=quantity)

    report = api_client.get(
        "/api/v1/reports/stock-position",
        params={"low_stock_only": True},
        headers=headers,
    )
    assert report.status_code == 200, report.text
    names = {row["name"] for row in report.json()["items"]}

    assert ("Report Part" in names) is expected_low, f"{quantity} vs minimum {MINIMUM}"


# --- surface 4: the notification producer ----------------------------------------


def _low_stock_notices(
    api_client: TestClient, headers: dict[str, str], product_id: str
) -> list[dict[str, Any]]:
    body = api_client.get("/api/v1/notifications", headers=headers).json()
    return [
        n
        for n in body["notifications"]
        if n["kind"] == "low_stock" and n["entity_id"] == product_id and n["status"] == "active"
    ]


@pytest.mark.parametrize("quantity,expected_low", BOUNDARY_CASES)
def test_the_notification_producer_is_at_or_below(
    api_client: TestClient, headers: dict[str, str], quantity: str, expected_low: bool
) -> None:
    """The event-driven hook in `apply_movements`, reached through a real receipt."""
    product_id = _stocked_product(api_client, headers, name="Notified Part", quantity=quantity)

    notices = _low_stock_notices(api_client, headers, product_id)

    assert (len(notices) == 1) is expected_low, (
        f"{quantity} against a minimum of {MINIMUM}: {notices}"
    )


@pytest.mark.parametrize("quantity,expected_low", BOUNDARY_CASES)
def test_the_daily_reconciliation_is_at_or_below(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    quantity: str,
    expected_low: bool,
) -> None:
    """The safety-net sweep must reach the same verdict as the event path, or it would
    undo the event path's work on every run — which is exactly the defect that made this
    file necessary."""
    from bm.domain.jobs import handlers

    product_id = _stocked_product(api_client, headers, name="Reconciled Part", quantity=quantity)

    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})

    notices = _low_stock_notices(api_client, headers, product_id)
    assert (len(notices) == 1) is expected_low, (
        f"{quantity} against a minimum of {MINIMUM}: {notices}"
    )


# --- the surfaces agreeing with each other ---------------------------------------


def test_every_surface_agrees_about_a_part_exactly_at_its_minimum(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    """The regression that this whole file is for.

    Before 2026-11-22, this part had a notification and no Stock-screen row. Asserting
    the three surfaces separately would not have caught it; asserting that they *agree*
    is what does.
    """
    product_id = _stocked_product(api_client, headers, name="Boundary Part", quantity=MINIMUM)

    in_filter = product_id in {
        row["product_id"]
        for row in api_client.get(
            "/api/v1/inventory/balances", params={"low_stock_only": True}, headers=headers
        ).json()["items"]
    }
    badge = next(
        item
        for item in api_client.get(
            "/api/v1/products/search", params={"query": "Boundary Part"}, headers=headers
        ).json()["items"]
        if item["id"] == product_id
    )["is_low_stock"]
    in_report = "Boundary Part" in {
        row["name"]
        for row in api_client.get(
            "/api/v1/reports/stock-position",
            params={"low_stock_only": True},
            headers=headers,
        ).json()["items"]
    }
    notified = len(_low_stock_notices(api_client, headers, product_id)) == 1

    verdicts = {
        "stock screen filter": in_filter,
        "product badge": badge,
        "stock report": in_report,
        "notification": notified,
    }
    assert all(verdicts.values()), f"the surfaces disagree at the boundary: {verdicts}"


def test_the_notification_body_does_not_claim_below_when_the_part_is_at_its_minimum(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """A part with 10 left and a minimum of 10 must not be described as "below the
    minimum of 10" while the screen beside it reads 10. Small wrongness like that is how
    people stop trusting the figure."""
    product_id = _stocked_product(api_client, headers, name="Wording Part", quantity=MINIMUM)

    notice = _low_stock_notices(api_client, headers, product_id)[0]

    assert "below the minimum" not in (notice["body"] or "")
    assert "down to the minimum" in (notice["body"] or ""), notice["body"]


# --- the lifecycle, at the new boundary ------------------------------------------


def _sell(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product_id: str, qty: str
) -> None:
    total = f"{int(float(qty) * 100)}.00"
    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**headers, **_key()},
        json={
            "counter_id": str(tenant.counter_id),
            "sale_type": "cash",
            "lines": [
                {
                    "product_id": product_id,
                    "quantity": qty,
                    "unit_price": "100.0000",
                    "price_source": "catalog",
                }
            ],
            "client_computed_total": total,
            "payment": {"method": "cash", "amount": total},
        },
    )
    assert response.status_code == 201, response.text


def _receive(api_client: TestClient, headers: dict[str, str], product_id: str, qty: str) -> None:
    supplier = api_client.put(
        f"/api/v1/suppliers/{uuid.uuid4()}",
        headers=headers,
        json={"name": "Threshold Supplier", "phone": "9876543210"},
    )
    purchase = api_client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "supplier_id": supplier.json()["id"],
            "supplier_document_number": f"TH/{uuid.uuid4().hex[:6]}",
            "items": [{"product_id": product_id, "quantity": qty, "unit_cost": "80.0000"}],
        },
    )
    confirmed = api_client.post(
        f"/api/v1/purchases/{purchase.json()['purchase_id']}:confirm",
        headers={**headers, **_key()},
    )
    assert confirmed.status_code == 200, confirmed.text


def test_replenishment_past_the_minimum_resolves_the_alert(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    """Resolution has to clear the boundary, not merely reach it: at 10/10 the part is
    still low under the new rule, so the alert must stay until 11."""
    product_id = _stocked_product(api_client, headers, name="Resolve Part", quantity="20.000")
    _sell(api_client, headers, tenant, product_id, "11.000")  # 9 left — low
    assert len(_low_stock_notices(api_client, headers, product_id)) == 1

    _receive(api_client, headers, product_id, "1.000")  # 10 — still AT the minimum
    assert len(_low_stock_notices(api_client, headers, product_id)) == 1, (
        "10 against a minimum of 10 is low, so the alert must not resolve here"
    )

    _receive(api_client, headers, product_id, "1.000")  # 11 — clear
    assert _low_stock_notices(api_client, headers, product_id) == []


def test_a_later_transition_into_low_stock_creates_a_new_alert(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    product_id = _stocked_product(api_client, headers, name="Recur Part", quantity="20.000")
    _sell(api_client, headers, tenant, product_id, "10.000")  # 10 — low
    first = _low_stock_notices(api_client, headers, product_id)
    assert len(first) == 1

    _receive(api_client, headers, product_id, "20.000")  # 30 — resolved
    assert _low_stock_notices(api_client, headers, product_id) == []

    _sell(api_client, headers, tenant, product_id, "20.000")  # 10 again — low again
    second = _low_stock_notices(api_client, headers, product_id)

    assert len(second) == 1
    assert second[0]["id"] != first[0]["id"], "a new condition reused the old notification"


def test_repeated_updates_while_still_low_do_not_duplicate(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    """Including the boundary itself: moving 11 → 10 → 9 → 10 keeps one alert."""
    from bm.domain.jobs import handlers

    product_id = _stocked_product(api_client, headers, name="Steady Part", quantity="20.000")
    _sell(api_client, headers, tenant, product_id, "10.000")  # 10 — low
    original = _low_stock_notices(api_client, headers, product_id)
    assert len(original) == 1

    _sell(api_client, headers, tenant, product_id, "1.000")  # 9
    _receive(api_client, headers, product_id, "1.000")  # 10
    _sell(api_client, headers, tenant, product_id, "2.000")  # 8
    # And the daily reconciliation on top, which must also leave it alone.
    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})

    final = _low_stock_notices(api_client, headers, product_id)
    assert len(final) == 1, final
    assert final[0]["id"] == original[0]["id"], "the alert was churned rather than kept"
