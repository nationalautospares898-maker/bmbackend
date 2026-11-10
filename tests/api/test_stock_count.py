"""Physical stock count (Feature A.6), against the real API and a real database.

The load-bearing guarantees:

* **only one open count per branch** — a second `start` while one is open is refused,
  not silently allowed to race the first;
* **nothing is written to the ledger until finalize** — recording a count, however many
  times, never moves a balance;
* **finalize uses the balance at that instant, not whatever was true while counting** —
  a stock movement that happens between counting and finalizing is respected, not
  overwritten;
* **uncounted products are untouched** — finalize only ever acts on what was actually
  counted;
* **finalize cannot run twice** — a retried or double-submitted finalize is a no-op on
  the ledger, not a second adjustment.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


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


@pytest.fixture
def stocked(api_client: TestClient, headers: dict[str, str]) -> str:
    """A product with 80 on the shelf, through the only path that adds stock."""
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200
    product = api_client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": "Stock Count Test Part",
            "unit_code": "NOS",
            "default_selling_price": "1000.0000",
            "sku": "SC-1",
            "cost_price": "800.0000",
        },
    )
    assert product.status_code == 201, product.text
    product_id = str(product.json()["id"])

    supplier = api_client.put(
        f"/api/v1/suppliers/{uuid.uuid4()}",
        headers=headers,
        json={"name": "Stock Count Supplier", "phone": "9876543210"},
    )
    purchase = api_client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "supplier_id": supplier.json()["id"],
            "supplier_document_number": f"SC/{uuid.uuid4().hex[:6]}",
            "items": [{"product_id": product_id, "quantity": "80.000", "unit_cost": "800.0000"}],
        },
    )
    assert purchase.status_code == 201, purchase.text
    confirmed = api_client.post(
        f"/api/v1/purchases/{purchase.json()['purchase_id']}:confirm",
        headers={**headers, **_key()},
    )
    assert confirmed.status_code == 200, confirmed.text
    return product_id


def _balance(api_client: TestClient, headers: dict[str, str], product_id: str) -> Decimal:
    balances = api_client.get("/api/v1/inventory/balances", headers=headers).json()
    row = next(r for r in balances["items"] if r["product_id"] == product_id)
    return Decimal(row["quantity"])


def test_only_one_open_stock_count_per_branch(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    first = api_client.post("/api/v1/inventory/stock-counts", headers=headers, json={})
    assert first.status_code == 201, first.text

    second = api_client.post("/api/v1/inventory/stock-counts", headers=headers, json={})
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "STOCK_COUNT_ALREADY_OPEN"


def test_review_shows_the_live_system_quantity_before_anything_is_counted(
    api_client: TestClient, headers: dict[str, str], stocked: str
) -> None:
    started = api_client.post("/api/v1/inventory/stock-counts", headers=headers, json={})
    session_id = started.json()["id"]

    review = api_client.get(f"/api/v1/inventory/stock-counts/{session_id}", headers=headers)
    assert review.status_code == 200, review.text
    row = next(i for i in review.json()["items"] if i["product_id"] == stocked)
    assert row["system_quantity"] == "80.000"
    assert row["counted_quantity"] is None
    assert row["discrepancy"] is None


def test_recording_a_count_writes_nothing_to_the_ledger(
    api_client: TestClient, headers: dict[str, str], stocked: str
) -> None:
    session_id = api_client.post("/api/v1/inventory/stock-counts", headers=headers, json={}).json()[
        "id"
    ]

    recorded = api_client.put(
        f"/api/v1/inventory/stock-counts/{session_id}/items/{stocked}",
        headers=headers,
        json={"counted_quantity": "75.000"},
    )
    assert recorded.status_code == 200, recorded.text

    assert _balance(api_client, headers, stocked) == Decimal("80.000")

    review = api_client.get(f"/api/v1/inventory/stock-counts/{session_id}", headers=headers)
    row = next(i for i in review.json()["items"] if i["product_id"] == stocked)
    assert row["counted_quantity"] == "75.000"
    assert row["discrepancy"] == "-5.000"


def test_finalize_applies_exactly_one_adjustment_for_the_discrepancy(
    api_client: TestClient, headers: dict[str, str], stocked: str
) -> None:
    session_id = api_client.post("/api/v1/inventory/stock-counts", headers=headers, json={}).json()[
        "id"
    ]
    api_client.put(
        f"/api/v1/inventory/stock-counts/{session_id}/items/{stocked}",
        headers=headers,
        json={"counted_quantity": "75.000"},
    )

    finalized = api_client.post(
        f"/api/v1/inventory/stock-counts/{session_id}:finalize", headers=headers
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json() == {"movements": 1, "counted": 1}

    assert _balance(api_client, headers, stocked) == Decimal("75.000")

    movements = api_client.get(
        "/api/v1/inventory/movements", headers=headers, params={"product_id": stocked}
    ).json()["items"]
    stock_count_moves = [m for m in movements if m["source_type"] == "stock_count"]
    assert len(stock_count_moves) == 1
    assert stock_count_moves[0]["reason"] == "adjustment_decrease"


def test_finalize_cannot_run_twice(
    api_client: TestClient, headers: dict[str, str], stocked: str
) -> None:
    session_id = api_client.post("/api/v1/inventory/stock-counts", headers=headers, json={}).json()[
        "id"
    ]
    api_client.put(
        f"/api/v1/inventory/stock-counts/{session_id}/items/{stocked}",
        headers=headers,
        json={"counted_quantity": "75.000"},
    )
    first = api_client.post(
        f"/api/v1/inventory/stock-counts/{session_id}:finalize", headers=headers
    )
    assert first.status_code == 200

    second = api_client.post(
        f"/api/v1/inventory/stock-counts/{session_id}:finalize", headers=headers
    )
    assert second.status_code == 422, second.text

    assert _balance(api_client, headers, stocked) == Decimal("75.000")


def test_finalize_uses_the_balance_at_that_instant_not_the_stale_snapshot(
    api_client: TestClient, headers: dict[str, str], stocked: str
) -> None:
    """The owner counts 75 while the system says 80. Before anyone finalises, a sale
    happens and the system balance moves to 70 on its own. Finalizing must bring the
    shelf to 75 (what was actually counted) by adjusting from the *current* 70, i.e.
    +5 — not by blindly re-applying "counted minus the original snapshot of 80", which
    would silently also reverse the sale's own movement."""
    session_id = api_client.post("/api/v1/inventory/stock-counts", headers=headers, json={}).json()[
        "id"
    ]
    api_client.put(
        f"/api/v1/inventory/stock-counts/{session_id}/items/{stocked}",
        headers=headers,
        json={"counted_quantity": "75.000"},
    )

    # An unrelated movement after counting, before finalize.
    concurrent = api_client.post(
        "/api/v1/inventory/adjustments",
        headers={**headers, **_key()},
        json={
            "product_id": stocked,
            "quantity_delta": "-10",
            "reason_code": "adjustment_decrease",
            "reason_text": "Simulated concurrent sale during the stock count window",
        },
    )
    assert concurrent.status_code == 200, concurrent.text
    assert _balance(api_client, headers, stocked) == Decimal("70.000")

    finalized = api_client.post(
        f"/api/v1/inventory/stock-counts/{session_id}:finalize", headers=headers
    )
    assert finalized.status_code == 200, finalized.text

    # 70 (current) -> 75 (counted) is +5, landing exactly on what was physically
    # counted — the concurrent sale's effect is preserved, not undone.
    assert _balance(api_client, headers, stocked) == Decimal("75.000")


def test_an_uncounted_product_is_untouched_by_finalize(
    api_client: TestClient, headers: dict[str, str], stocked: str
) -> None:
    session_id = api_client.post("/api/v1/inventory/stock-counts", headers=headers, json={}).json()[
        "id"
    ]
    # Deliberately never counted.
    finalized = api_client.post(
        f"/api/v1/inventory/stock-counts/{session_id}:finalize", headers=headers
    )
    assert finalized.status_code == 200
    assert finalized.json() == {"movements": 0, "counted": 0}
    assert _balance(api_client, headers, stocked) == Decimal("80.000")


def test_cancelling_an_open_count_applies_nothing_and_frees_the_branch(
    api_client: TestClient, headers: dict[str, str], stocked: str
) -> None:
    session_id = api_client.post("/api/v1/inventory/stock-counts", headers=headers, json={}).json()[
        "id"
    ]
    api_client.put(
        f"/api/v1/inventory/stock-counts/{session_id}/items/{stocked}",
        headers=headers,
        json={"counted_quantity": "1.000"},
    )

    cancelled = api_client.post(
        f"/api/v1/inventory/stock-counts/{session_id}:cancel", headers=headers
    )
    assert cancelled.status_code == 200, cancelled.text
    assert _balance(api_client, headers, stocked) == Decimal("80.000"), (
        "cancelling must never apply the counted figures"
    )

    # The branch is free for a new count.
    reopened = api_client.post("/api/v1/inventory/stock-counts", headers=headers, json={})
    assert reopened.status_code == 201, reopened.text

    # And the cancelled one cannot be finalised after the fact.
    stale_finalize = api_client.post(
        f"/api/v1/inventory/stock-counts/{session_id}:finalize", headers=headers
    )
    assert stale_finalize.status_code == 422
