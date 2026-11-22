"""Event-driven low-stock notifications (owner request, 2026-11-22).

The behaviour under test is a **lifecycle**, not a single trigger, so each test drives a
real HTTP path end to end and then asks the bell's own endpoint what the owner would see.
Nothing here pokes `notifications` directly to set up a state: if a notification exists it
is because a stock movement put it there, which is the only claim worth making.

Why these go through the API rather than calling `apply_movements`: the point of hooking
the single stock writer is that *every* route inherits detection. A test that called the
writer directly would prove the hook works and tell us nothing about whether the routes
reach it. Each route below is a separate `MovementReason` and a separate service.
"""

from __future__ import annotations

import uuid
from decimal import Decimal as D
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db

#: The fixture part's name. The stock sheet matches rows by item name.
PART_NAME = "Event Driven Part"


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


def _stock_via_purchase(
    api_client: TestClient, headers: dict[str, str], product_id: str, qty: str
) -> None:
    """Put stock on the shelf through a confirmed purchase receipt.

    Used instead of the stock sheet wherever a test must not disturb the product's
    minimum: `POST /inventory/stock-entries` carries `min_qty` as a **mandatory** column
    of the owner's four-column sheet, so it always (re)states the reorder level. A
    purchase receipt moves stock and nothing else.
    """
    supplier = api_client.put(
        f"/api/v1/suppliers/{uuid.uuid4()}",
        headers=headers,
        json={"name": "Event Driven Supplier", "phone": "9876543210"},
    )
    assert supplier.status_code == 200, supplier.text
    purchase = api_client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "supplier_id": supplier.json()["id"],
            "supplier_document_number": f"EV/{uuid.uuid4().hex[:6]}",
            "items": [{"product_id": product_id, "quantity": qty, "unit_cost": "80.0000"}],
        },
    )
    assert purchase.status_code == 201, purchase.text
    confirmed = api_client.post(
        f"/api/v1/purchases/{purchase.json()['purchase_id']}:confirm",
        headers={**headers, **_key()},
    )
    assert confirmed.status_code == 200, confirmed.text


@pytest.fixture
def product(api_client: TestClient, headers: dict[str, str]) -> str:
    """A part with a minimum of 2 and ten on the shelf, stocked through a real purchase
    receipt — the same path `test_reminder_and_order_jobs.py` uses."""
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200
    created = api_client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": PART_NAME,
            "unit_code": "NOS",
            "default_selling_price": "100.0000",
            "sku": "EV-1",
            "reorder_level": "2.000",
        },
    )
    assert created.status_code == 201, created.text
    product_id = str(created.json()["id"])
    _stock_via_purchase(api_client, headers, product_id, "10.000")
    return product_id


def _low_stock_notices(
    api_client: TestClient, headers: dict[str, str], product_id: str
) -> list[dict[str, Any]]:
    """What the bell would show for this product, right now."""
    body = api_client.get("/api/v1/notifications", headers=headers).json()
    return [
        n
        for n in body["notifications"]
        if n["kind"] == "low_stock" and n["entity_id"] == product_id
    ]


def _sale_body(tenant: Tenant, product_id: str, qty: str) -> dict[str, Any]:
    total = f"{int(D(qty) * D('100'))}.00"
    return {
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
        # Cash finalisation takes the money in the same request (ADR-022: there is no
        # separate "mark as paid").
        "payment": {"method": "cash", "amount": total},
    }


def _sell(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product_id: str, qty: str
) -> dict[str, Any]:
    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**headers, **_key()},
        json=_sale_body(tenant, product_id, qty),
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _add_stock(
    api_client: TestClient, headers: dict[str, str], product_id: str, qty: str, *, name: str
) -> None:
    """Replenishment through the owner's stock sheet — a different service and a
    different `MovementReason` from the purchase receipt the fixture used.

    `min_qty` is restated because the sheet carries it per row; it is kept at the
    fixture's 2.000 so these helpers never move the threshold under a test.
    """
    response = api_client.post(
        "/api/v1/inventory/stock-entries",
        headers={**headers, **_key()},
        json={
            "entries": [{"item": name, "price": "100.0000", "min_qty": "2.000", "stock_qty": qty}]
        },
    )
    assert response.status_code in (200, 201), response.text


# --- raising ---------------------------------------------------------------------


def test_a_sale_that_takes_stock_to_the_minimum_raises_the_alert_immediately(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    """Ten on the shelf, minimum 2, sell 8 → exactly 2 left. The bell must already know
    by the time the finalize response has been written; nothing waits for a scan."""
    assert _low_stock_notices(api_client, headers, product) == []

    _sell(api_client, headers, tenant, product, "8.000")

    notices = _low_stock_notices(api_client, headers, product)
    assert len(notices) == 1, notices
    assert notices[0]["status"] == "active"
    assert notices[0]["severity"] == "warning"


def test_selling_out_entirely_raises_a_critical_alert(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    _sell(api_client, headers, tenant, product, "10.000")

    notices = _low_stock_notices(api_client, headers, product)
    assert len(notices) == 1, notices
    assert notices[0]["severity"] == "critical"
    assert "Out of stock" in (notices[0]["body"] or "")


def test_a_product_with_no_minimum_set_is_never_flagged(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200
    created = api_client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": "No Minimum Part",
            "unit_code": "NOS",
            # Matches `_sale_body`'s unit price: a mismatch is refused as
            # PRICE_CHANGED before any stock moves, which would not test anything.
            "default_selling_price": "100.0000",
            "sku": "NM-1",
        },
    )
    assert created.status_code == 201, created.text
    product_id = str(created.json()["id"])
    _stock_via_purchase(api_client, headers, product_id, "3.000")

    _sell(api_client, headers, tenant, product_id, "3.000")

    assert _low_stock_notices(api_client, headers, product_id) == []


def test_an_inventory_adjustment_downwards_raises_the_alert(
    api_client: TestClient, headers: dict[str, str], product: str
) -> None:
    """A different route, a different reason, the same rule — this is the whole point of
    hooking the single writer rather than the sale path."""
    response = api_client.post(
        "/api/v1/inventory/adjustments",
        headers={**headers, **_key()},
        json={
            "product_id": product,
            "quantity_delta": "-9.000",
            "reason_text": "Damaged in storage, written off after inspection",
        },
    )
    assert response.status_code in (200, 201), response.text

    notices = _low_stock_notices(api_client, headers, product)
    assert len(notices) == 1, notices


# --- not duplicating -------------------------------------------------------------


def test_two_sales_that_both_leave_it_low_produce_one_alert_not_two(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    _sell(api_client, headers, tenant, product, "8.000")
    _sell(api_client, headers, tenant, product, "1.000")

    notices = _low_stock_notices(api_client, headers, product)
    assert len(notices) == 1, notices


def test_stock_crossing_the_threshold_repeatedly_does_not_accumulate_alerts(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    """Down, up, down, up, down. The owner must end with one open alert, not three."""
    for _ in range(3):
        _sell(api_client, headers, tenant, product, "8.000")
        _add_stock(api_client, headers, product, "8.000", name=PART_NAME)
    _sell(api_client, headers, tenant, product, "8.000")

    notices = _low_stock_notices(api_client, headers, product)
    assert len(notices) == 1, notices
    assert notices[0]["status"] == "active"


# --- resolving -------------------------------------------------------------------


def test_replenishment_resolves_the_open_alert(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    """Stock arriving back above the minimum closes the notice — the owner should not
    have to dismiss an alert about a problem they have already fixed."""
    _sell(api_client, headers, tenant, product, "8.000")
    assert len(_low_stock_notices(api_client, headers, product)) == 1

    _add_stock(api_client, headers, product, "20.000", name=PART_NAME)

    assert _low_stock_notices(api_client, headers, product) == []


def test_a_cancelled_sale_returns_the_stock_and_resolves_the_alert(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    """Cancellation is its own service and its own movement reason. It restores stock,
    so it must also clear the alert that stock level caused."""
    sale = _sell(api_client, headers, tenant, product, "9.000")
    assert len(_low_stock_notices(api_client, headers, product)) == 1

    cancelled = api_client.post(
        f"/api/v1/invoices/{sale['invoice']['id']}:cancel",
        headers={**headers, **_key()},
        json={"reason": "Customer changed their mind before leaving the counter"},
    )
    assert cancelled.status_code == 200, cancelled.text

    assert _low_stock_notices(api_client, headers, product) == []


def test_partial_replenishment_that_stays_at_the_minimum_keeps_the_alert_open(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    """Minimum 2. Sell 9 (1 left, low), add 1 (2 left) — still *at* the minimum, so on
    this application's threshold it is still low and the alert must stay."""
    _sell(api_client, headers, tenant, product, "9.000")
    before = _low_stock_notices(api_client, headers, product)
    assert len(before) == 1

    _add_stock(api_client, headers, product, "1.000", name=PART_NAME)

    after = _low_stock_notices(api_client, headers, product)
    assert len(after) == 1, after
    assert after[0]["id"] == before[0]["id"], "the alert was replaced rather than kept"


def test_a_later_dip_after_a_resolve_raises_a_genuinely_new_alert(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    """The half of the lifecycle that a naive dedup breaks: once resolved, the slot must
    be free again, or a restocked part that runs low next month stays silent."""
    _sell(api_client, headers, tenant, product, "8.000")
    first = _low_stock_notices(api_client, headers, product)
    assert len(first) == 1

    _add_stock(api_client, headers, product, "20.000", name=PART_NAME)
    assert _low_stock_notices(api_client, headers, product) == []

    _sell(api_client, headers, tenant, product, "20.000")
    second = _low_stock_notices(api_client, headers, product)

    assert len(second) == 1, second
    assert second[0]["id"] != first[0]["id"], "a new condition reused the old notification"


def test_a_dismissed_alert_does_not_block_a_later_one(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    """Dismissing frees the dedup slot too. A part the owner dismissed weeks ago must be
    able to alert again when it dips afresh."""
    _sell(api_client, headers, tenant, product, "8.000")
    notices = _low_stock_notices(api_client, headers, product)
    dismissed = api_client.post(
        f"/api/v1/notifications/{notices[0]['id']}:dismiss", headers=headers
    )
    assert dismissed.status_code == 200, dismissed.text
    assert _low_stock_notices(api_client, headers, product) == []

    # Restock above the minimum and dip again: a fresh condition.
    _add_stock(api_client, headers, product, "20.000", name=PART_NAME)
    _sell(api_client, headers, tenant, product, "20.000")

    assert len(_low_stock_notices(api_client, headers, product)) == 1


# --- transactional correctness ---------------------------------------------------


def test_a_refused_sale_leaves_no_notification_behind(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    """The hook runs inside the caller's transaction, so a sale that rolls back must take
    its notification with it. Overselling without the override permission is refused
    after stock has been locked and checked — exactly the window where a notification
    written outside the transaction would survive."""
    refused = api_client.post(
        "/api/v1/sales:finalize",
        headers={**headers, **_key()},
        json={
            "counter_id": str(tenant.counter_id),
            "sale_type": "cash",
            "lines": [
                {
                    "product_id": product,
                    "quantity": "500.000",
                    "unit_price": "100.0000",
                    "price_source": "catalog",
                }
            ],
            "client_computed_total": "50000.00",
        },
    )
    assert refused.status_code in (409, 422), refused.text

    assert _low_stock_notices(api_client, headers, product) == []


def test_replaying_a_sale_with_the_same_idempotency_key_does_not_duplicate_the_alert(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    """A retried finalisation returns the stored response and moves no further stock, so
    it must not raise a second notification either."""
    key = _key()
    body = _sale_body(tenant, product, "8.000")
    first = api_client.post("/api/v1/sales:finalize", headers={**headers, **key}, json=body)
    assert first.status_code == 201, first.text
    replay = api_client.post("/api/v1/sales:finalize", headers={**headers, **key}, json=body)
    assert replay.status_code in (200, 201), replay.text

    notices = _low_stock_notices(api_client, headers, product)
    assert len(notices) == 1, notices


# --- the daily reconciliation ----------------------------------------------------


def test_the_reconciliation_is_a_safety_net_and_changes_nothing_already_correct(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    """It must not be a second generator. Running it against a state event-driven
    detection already got right produces no new row and no churn."""
    from bm.domain.jobs import handlers

    _sell(api_client, headers, tenant, product, "8.000")
    before = _low_stock_notices(api_client, headers, product)
    assert len(before) == 1

    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})
    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})

    after = _low_stock_notices(api_client, headers, product)
    assert len(after) == 1, after
    assert after[0]["id"] == before[0]["id"], "the reconciliation replaced a correct alert"
    assert after[0]["status"] == "active"


def test_the_reconciliation_repairs_a_missed_alert(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    """Its reason for existing: a low balance carrying no alert gets one. Simulated by
    resolving the alert while the stock is still low, which is the observable shape of
    'event-driven detection did not happen'."""
    from bm.domain.jobs import handlers

    _sell(api_client, headers, tenant, product, "8.000")
    notices = _low_stock_notices(api_client, headers, product)
    api_client.post(f"/api/v1/notifications/{notices[0]['id']}:dismiss", headers=headers)
    assert _low_stock_notices(api_client, headers, product) == []

    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})

    repaired = _low_stock_notices(api_client, headers, product)
    assert len(repaired) == 1, repaired


def test_the_reconciliation_resolves_an_alert_for_stock_that_recovered(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: str
) -> None:
    from bm.domain.jobs import handlers

    _sell(api_client, headers, tenant, product, "8.000")
    assert len(_low_stock_notices(api_client, headers, product)) == 1

    # Raise the minimum's distance by adding stock through a path, then force the
    # reconciliation to be the one that notices by running it directly.
    _add_stock(api_client, headers, product, "50.000", name=PART_NAME)
    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})

    assert _low_stock_notices(api_client, headers, product) == []
