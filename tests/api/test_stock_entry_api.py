"""`POST /inventory/stock-entries` and `GET /inventory/stock-entries:resolve`.

The test that earns this file is
`test_the_same_key_twice_receives_the_delivery_once`. The route *requires* an
`Idempotency-Key`, and requiring one while ignoring it is worse than not asking: the
caller is told a retry is safe, a second press books the goods again, and because
`stock_movements` can never be deleted the only correction left is a third, opposite
movement. An earlier draft of this endpoint did exactly that — the header was declared
and validated, and nothing ever claimed it.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="sheetowner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def owner_headers(api_client: TestClient, owner: UserFixture, tenant: Tenant) -> dict[str, str]:
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


def _sheet(*rows: tuple[str, str, str, str]) -> dict[str, Any]:
    return {
        "entries": [
            {"item": item, "price": price, "min_qty": min_qty, "stock_qty": stock_qty}
            for item, price, min_qty, stock_qty in rows
        ]
    }


def _post(
    api_client: TestClient, headers: dict[str, str], body: dict[str, Any], key: str | None = None
) -> Any:
    sent = dict(headers)
    sent["Idempotency-Key"] = key or str(uuid.uuid4())
    return api_client.post("/api/v1/inventory/stock-entries", json=body, headers=sent)


def _quantity(session: Session, name: str) -> Any:
    return session.execute(
        text(
            "SELECT b.quantity FROM inventory_balances b"
            " JOIN products p ON p.id = b.product_id"
            " WHERE p.normalized_name = :n"
        ),
        {"n": name},
    ).scalar_one()


# --- the happy path -----------------------------------------------------------


def test_a_sheet_row_creates_the_product_with_its_price_and_minimum(
    api_client: TestClient, owner_headers: dict[str, str], migration_session: Session
) -> None:
    response = _post(api_client, owner_headers, _sheet(("Axel Packing", "30", "50", "200")))
    assert response.status_code == 200, response.text

    [item] = response.json()["items"]
    assert item["created"] is True
    assert item["quantity_before"] == "0"
    assert item["quantity_after"] == "200.000"
    assert item["reason"] == "opening"

    row = migration_session.execute(
        text(
            "SELECT default_selling_price, reorder_level FROM products"
            " WHERE normalized_name = 'axel packing'"
        )
    ).one()
    assert str(row[0]) == "30.0000"
    assert str(row[1]) == "50.000"


def test_adding_to_an_existing_product_accumulates_rather_than_replacing(
    api_client: TestClient, owner_headers: dict[str, str], migration_session: Session
) -> None:
    """20 on the shelf plus a typed 200 is 220 over the wire too, not 200."""
    _post(api_client, owner_headers, _sheet(("Axel Packing", "30", "50", "20")))
    response = _post(api_client, owner_headers, _sheet(("Axel Packing", "30", "50", "200")))

    [item] = response.json()["items"]
    assert item["created"] is False
    assert item["quantity_before"] == "20.000"
    assert item["quantity_after"] == "220.000"
    assert item["reason"] == "adjustment_increase"
    assert str(_quantity(migration_session, "axel packing")) == "220.000"


# --- the reason this file exists ----------------------------------------------


def test_the_same_key_twice_receives_the_delivery_once(
    api_client: TestClient, owner_headers: dict[str, str], migration_session: Session
) -> None:
    """A double-tapped Save must book one delivery and return the first outcome.

    Asserted on the stock, on the ledger row count and on the response body, because a
    key that is validated but never claimed satisfies none of the three while looking
    entirely healthy from the client's side.
    """
    key = str(uuid.uuid4())
    body = _sheet(("Axel Packing", "30", "50", "200"))

    first = _post(api_client, owner_headers, body, key=key)
    second = _post(api_client, owner_headers, body, key=key)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    # The replay is the stored response, verbatim.
    assert second.json() == first.json()
    # One delivery on the shelf, not two.
    assert str(_quantity(migration_session, "axel packing")) == "200.000"
    # One ledger row. `stock_movements` is append-only, so a second row could never be
    # tidied away afterwards.
    movements = migration_session.execute(
        text(
            "SELECT count(*) FROM stock_movements m"
            " JOIN products p ON p.id = m.product_id"
            " WHERE p.normalized_name = 'axel packing'"
        )
    ).scalar_one()
    assert movements == 1


def test_the_same_key_with_a_different_sheet_is_refused(
    api_client: TestClient, owner_headers: dict[str, str]
) -> None:
    """A changed body under a reused key is a client bug, never silently accepted."""
    key = str(uuid.uuid4())
    _post(api_client, owner_headers, _sheet(("Axel Packing", "30", "50", "200")), key=key)
    clash = _post(api_client, owner_headers, _sheet(("Axel Packing", "30", "50", "999")), key=key)

    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSE"


def test_a_missing_key_is_refused(api_client: TestClient, owner_headers: dict[str, str]) -> None:
    response = api_client.post(
        "/api/v1/inventory/stock-entries",
        json=_sheet(("Axel Packing", "30", "50", "200")),
        headers=owner_headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- refusals -----------------------------------------------------------------


def test_a_rejected_row_leaves_the_whole_sheet_unapplied(
    api_client: TestClient, owner_headers: dict[str, str], migration_session: Session
) -> None:
    response = _post(
        api_client,
        owner_headers,
        _sheet(("Good Row", "30", "5", "10"), ("Bad Row", "30", "5", "0")),
    )
    assert response.status_code == 422

    created = migration_session.execute(
        text("SELECT count(*) FROM products WHERE normalized_name IN ('good row', 'bad row')")
    ).scalar_one()
    assert created == 0


def test_an_unknown_field_is_refused(api_client: TestClient, owner_headers: dict[str, str]) -> None:
    """`extra="forbid"`: a client typo must surface, not silently drop a minimum."""
    response = _post(
        api_client,
        owner_headers,
        {
            "entries": [
                {
                    "item": "Axel Packing",
                    "price": "30",
                    "min_qty": "50",
                    "stock_qty": "200",
                    "minimum_qty": "50",  # the typo
                }
            ]
        },
    )
    assert response.status_code == 422


def test_a_json_number_is_refused_for_money(
    api_client: TestClient, owner_headers: dict[str, str]
) -> None:
    """Money and quantity cross the wire as strings (ADR-013)."""
    response = _post(
        api_client,
        owner_headers,
        {"entries": [{"item": "Axel Packing", "price": 30.5, "min_qty": "50", "stock_qty": "200"}]},
    )
    assert response.status_code == 422


# --- resolve ------------------------------------------------------------------


def test_resolve_reports_new_then_existing(
    api_client: TestClient, owner_headers: dict[str, str]
) -> None:
    """The screen's "will add" / "will update" badge, from the same matcher as the save."""
    before = api_client.get(
        "/api/v1/inventory/stock-entries:resolve",
        params={"item": "Axel Packing"},
        headers=owner_headers,
    )
    assert before.status_code == 200
    assert before.json()["kind"] == "new"

    _post(api_client, owner_headers, _sheet(("Axel Packing", "30", "50", "200")))

    after = api_client.get(
        "/api/v1/inventory/stock-entries:resolve",
        # Different spelling on purpose: the match is on the normalised name.
        params={"item": "  axel   packing  "},
        headers=owner_headers,
    )
    body = after.json()
    assert body["kind"] == "existing"
    assert body["current_quantity"] == "200.000"
    assert body["current_price"] == "30.0000"
    assert body["current_min_qty"] == "50.000"


# --- the balance row the Stock screen renders ---------------------------------


def test_balances_carry_the_price_and_the_minimum(
    api_client: TestClient, owner_headers: dict[str, str]
) -> None:
    _post(api_client, owner_headers, _sheet(("Axel Packing", "30", "50", "200")))

    response = api_client.get("/api/v1/inventory/balances", headers=owner_headers)
    assert response.status_code == 200
    row = next(r for r in response.json()["items"] if r["name"] == "Axel Packing")
    assert row["default_selling_price"] == "30.0000"
    assert row["reorder_level"] == "50.000"
    assert row["quantity"] == "200.000"
    # Created from a sheet row, which has no part-number column.
    assert row["sku"] is None


def test_low_stock_only_is_strictly_below_the_minimum(
    api_client: TestClient, owner_headers: dict[str, str]
) -> None:
    """200 against a minimum of 50 is not low; 40 is. The boundary case is 50 itself."""
    _post(api_client, owner_headers, _sheet(("Plenty", "30", "50", "200")))
    _post(api_client, owner_headers, _sheet(("Short", "30", "50", "40")))
    _post(api_client, owner_headers, _sheet(("Exactly At Minimum", "30", "50", "50")))

    response = api_client.get(
        "/api/v1/inventory/balances", params={"low_stock_only": True}, headers=owner_headers
    )
    names = {row["name"] for row in response.json()["items"]}
    assert "Short" in names
    assert "Plenty" not in names
    # At the minimum is *at* it, not below it.
    assert "Exactly At Minimum" not in names


# --- the history the Stock screen shows ---------------------------------------


def test_movements_read_oldest_first_with_a_running_balance(
    api_client: TestClient, owner_headers: dict[str, str]
) -> None:
    _post(api_client, owner_headers, _sheet(("Axel Packing", "30", "50", "100")))
    _post(api_client, owner_headers, _sheet(("Axel Packing", "30", "50", "200")))

    product_id = next(
        row["product_id"]
        for row in api_client.get("/api/v1/inventory/balances", headers=owner_headers).json()[
            "items"
        ]
        if row["name"] == "Axel Packing"
    )
    response = api_client.get(
        "/api/v1/inventory/movements",
        params={"product_id": product_id, "oldest_first": True},
        headers=owner_headers,
    )
    items = response.json()["items"]
    assert [(i["reason"], i["quantity_delta"], i["balance_after"]) for i in items] == [
        ("opening", "100.000", "100.000"),
        ("adjustment_increase", "200.000", "300.000"),
    ]
    # A sheet arrival has no document to look up, and says so rather than inventing one.
    assert all(i["reference"] is None for i in items)
    assert all(i["source_type"] == "stock_entry" for i in items)
