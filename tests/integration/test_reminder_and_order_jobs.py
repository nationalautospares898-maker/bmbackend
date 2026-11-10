"""Low-stock scanning, reminder firing and order nudges — Feature A/B's own jobs
(2026-11-10), against a real database, following `test_jobs.py`'s pattern exactly.

Each is tested for the thing it is supposed to do, then run a second time to prove it
is idempotent — at-least-once delivery is the queue's contract here too, and the
in-process ticker in `worker.py` makes at-least-once more likely, not less: a slow
tick and the next one racing is the normal case, not an edge case.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from bm.domain.jobs import handlers
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db


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


@pytest.fixture
def credit_sale_product(api_client: TestClient, headers: dict[str, str]) -> str:
    """A product with ten on the shelf, through the only path that adds stock —
    matching `test_credit_rules.py`'s `stocked` fixture exactly."""
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200
    product = api_client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": "Reminder Test Part",
            "unit_code": "NOS",
            "default_selling_price": "1000.0000",
            "sku": "RM-1",
            "cost_price": "800.0000",
        },
    )
    assert product.status_code == 201, product.text
    product_id = str(product.json()["id"])

    supplier = api_client.put(
        f"/api/v1/suppliers/{uuid.uuid4()}",
        headers=headers,
        json={"name": "Reminder Test Supplier", "phone": "9876543210"},
    )
    assert supplier.status_code == 200, supplier.text
    purchase = api_client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "supplier_id": supplier.json()["id"],
            "supplier_document_number": f"RM/{uuid.uuid4().hex[:6]}",
            "items": [{"product_id": product_id, "quantity": "10.000", "unit_cost": "800.0000"}],
        },
    )
    assert purchase.status_code == 201, purchase.text
    confirmed = api_client.post(
        f"/api/v1/purchases/{purchase.json()['purchase_id']}:confirm",
        headers={**headers, **_key()},
    )
    assert confirmed.status_code == 200, confirmed.text
    return product_id


def _credit_sale(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product_id: str
) -> dict[str, Any]:
    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**headers, **_key()},
        json={
            "counter_id": str(tenant.counter_id),
            "sale_type": "credit",
            "lines": [
                {
                    "product_id": product_id,
                    "quantity": "1.000",
                    "unit_price": "1000.0000",
                    "price_source": "catalog",
                }
            ],
            "header": {"customer_name": "Reminder Test Customer"},
            "customer": {
                "mode": "new",
                "customer_id": str(uuid.uuid4()),
                "name": "Reminder Test Customer",
            },
            "client_computed_total": "1000.00",
        },
    )
    assert response.status_code in (200, 201), response.text
    return dict(response.json()["invoice"])


@pytest.fixture
def engine_bound(api_settings: object) -> None:
    from bm.infra.db import session as session_module

    session_module.dispose_engine()
    session_module.init_engine(api_settings)  # type: ignore[arg-type]
    yield
    session_module.dispose_engine()


def _product(session: Session, tenant: Tenant, *, reorder_level: str | None) -> uuid.UUID:
    unit_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO units (id, business_id, code, name, allows_fraction, decimal_places)"
            " VALUES (:id, :business_id, 'NOS', 'Numbers', false, 0)"
        ),
        {"id": unit_id, "business_id": tenant.business_id},
    )
    product_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO products"
            " (id, business_id, name, normalized_name, unit_id, default_selling_price,"
            "  search_text, reorder_level)"
            " VALUES (:id, :business_id, 'Tie Rod End', 'tie rod end', :unit_id, 420,"
            "  'tie rod', :reorder_level)"
        ),
        {
            "id": product_id,
            "business_id": tenant.business_id,
            "unit_id": unit_id,
            "reorder_level": reorder_level,
        },
    )
    return product_id


def _balance(session: Session, tenant: Tenant, product_id: uuid.UUID, quantity: str) -> None:
    session.execute(
        text(
            "INSERT INTO inventory_balances"
            " (id, business_id, branch_id, product_id, quantity)"
            " VALUES (:id, :business_id, :branch_id, :product_id, :quantity)"
        ),
        {
            "id": uuid.uuid4(),
            "business_id": tenant.business_id,
            "branch_id": tenant.branch_id,
            "product_id": product_id,
            "quantity": quantity,
        },
    )


# --- low_stock.scan --------------------------------------------------------------


def test_a_product_below_its_reorder_level_raises_exactly_one_alert(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    product_id = _product(migration_session, tenant, reorder_level="10")
    _balance(migration_session, tenant, product_id, "3")
    migration_session.commit()

    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})
    migration_session.commit()

    rows = migration_session.execute(
        text(
            "SELECT kind, severity, status FROM notifications"
            " WHERE business_id = :b AND entity_id = :p"
        ),
        {"b": tenant.business_id, "p": product_id},
    ).all()
    assert len(rows) == 1
    assert str(rows[0].kind) == "low_stock"
    assert str(rows[0].status) == "active"

    # Running again must not duplicate the still-open alert.
    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})
    migration_session.commit()
    again = migration_session.execute(
        text("SELECT count(*) FROM notifications WHERE business_id = :b AND entity_id = :p"),
        {"b": tenant.business_id, "p": product_id},
    ).scalar_one()
    assert again == 1


def test_restocking_resolves_the_alert_and_a_later_dip_raises_a_fresh_one(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    product_id = _product(migration_session, tenant, reorder_level="10")
    _balance(migration_session, tenant, product_id, "3")
    migration_session.commit()

    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})
    migration_session.commit()

    # Restocked above the reorder level.
    migration_session.execute(
        text("UPDATE inventory_balances SET quantity = 50 WHERE product_id = :p"),
        {"p": product_id},
    )
    migration_session.commit()
    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})
    migration_session.commit()

    resolved = migration_session.execute(
        text(
            "SELECT status FROM notifications WHERE business_id = :b AND entity_id = :p"
            " ORDER BY created_at DESC LIMIT 1"
        ),
        {"b": tenant.business_id, "p": product_id},
    ).scalar_one()
    assert str(resolved) == "resolved"

    # Dips again — a fresh alert, because the slot was freed by the resolve.
    migration_session.execute(
        text("UPDATE inventory_balances SET quantity = 2 WHERE product_id = :p"),
        {"p": product_id},
    )
    migration_session.commit()
    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})
    migration_session.commit()

    active_count = migration_session.execute(
        text(
            "SELECT count(*) FROM notifications"
            " WHERE business_id = :b AND entity_id = :p AND status = 'active'"
        ),
        {"b": tenant.business_id, "p": product_id},
    ).scalar_one()
    assert active_count == 1


def test_a_product_with_no_reorder_level_is_never_flagged(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    product_id = _product(migration_session, tenant, reorder_level=None)
    _balance(migration_session, tenant, product_id, "0")
    migration_session.commit()

    handlers.handle_low_stock_scan({"business_id": str(tenant.business_id)})
    migration_session.commit()

    count = migration_session.execute(
        text("SELECT count(*) FROM notifications WHERE entity_id = :p"), {"p": product_id}
    ).scalar_one()
    assert count == 0


# --- orders.nudge ------------------------------------------------------------------


def test_a_fresh_pending_order_is_nudged_immediately_and_not_twice_within_ten_minutes(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    from bm.domain.auth.permissions import permissions_for_role
    from bm.domain.orders import service as orders
    from bm.domain.shared.context import AuthContext

    ctx = AuthContext(
        user_id=owner.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id,
        device_id=owner.device_id,
        role="owner",
        permissions=frozenset(str(p) for p in permissions_for_role(SystemRole.OWNER)),
        token_id="t",
    )
    order_id = orders.create_order(
        ctx, migration_session, body="2x brake pad, Suresh", customer_name="Suresh", phone=None
    )
    migration_session.commit()

    handlers.handle_orders_nudge({"business_id": str(tenant.business_id)})
    migration_session.commit()
    first = migration_session.execute(
        text(
            "SELECT count(*) FROM notifications"
            " WHERE business_id = :b AND entity_id = :o AND kind = 'order_reminder'"
        ),
        {"b": tenant.business_id, "o": order_id},
    ).scalar_one()
    assert first == 1

    # Immediately again: no duplicate, because the reminder is still open.
    handlers.handle_orders_nudge({"business_id": str(tenant.business_id)})
    migration_session.commit()
    still_one = migration_session.execute(
        text(
            "SELECT count(*) FROM notifications"
            " WHERE business_id = :b AND entity_id = :o AND kind = 'order_reminder'"
        ),
        {"b": tenant.business_id, "o": order_id},
    ).scalar_one()
    assert still_one == 1


def test_completing_an_order_stops_its_reminders(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    from bm.domain.auth.permissions import permissions_for_role
    from bm.domain.orders import service as orders
    from bm.domain.shared.context import AuthContext

    ctx = AuthContext(
        user_id=owner.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id,
        device_id=owner.device_id,
        role="owner",
        permissions=frozenset(str(p) for p in permissions_for_role(SystemRole.OWNER)),
        token_id="t",
    )
    order_id = orders.create_order(
        ctx, migration_session, body="1x wiper blade", customer_name=None, phone=None
    )
    migration_session.commit()

    handlers.handle_orders_nudge({"business_id": str(tenant.business_id)})
    migration_session.commit()

    orders.complete_order(ctx, migration_session, order_id)
    migration_session.commit()

    handlers.handle_orders_nudge({"business_id": str(tenant.business_id)})
    migration_session.commit()

    open_reminders = migration_session.execute(
        text(
            "SELECT count(*) FROM notifications"
            " WHERE entity_id = :o AND kind = 'order_reminder' AND status = 'active'"
        ),
        {"o": order_id},
    ).scalar_one()
    assert open_reminders == 0


# --- the per-order custom reminder (owner request, 2026-11-11) ----------------


def test_a_future_reminder_suppresses_the_flat_cadence(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    """ "remind me about this one tomorrow at 3pm" means quiet *until* then — not quiet
    plus the usual nudging in the meantime."""
    from bm.domain.auth.permissions import permissions_for_role
    from bm.domain.orders import service as orders
    from bm.domain.shared.context import AuthContext

    ctx = AuthContext(
        user_id=owner.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id,
        device_id=owner.device_id,
        role="owner",
        permissions=frozenset(str(p) for p in permissions_for_role(SystemRole.OWNER)),
        token_id="t",
    )
    order_id = orders.create_order(
        ctx, migration_session, body="2x brake pad", customer_name=None, phone=None
    )
    # The flat cadence would otherwise have fired ages ago.
    migration_session.execute(
        text("UPDATE orders SET last_reminder_at = :t WHERE id = :id"),
        {"t": datetime.now(UTC) - timedelta(hours=1), "id": order_id},
    )
    orders.set_reminder(
        ctx, migration_session, order_id, remind_at=datetime.now(UTC) + timedelta(hours=1)
    )
    migration_session.commit()

    handlers.handle_orders_nudge({"business_id": str(tenant.business_id)})
    migration_session.commit()

    open_reminders = migration_session.execute(
        text(
            "SELECT count(*) FROM notifications"
            " WHERE entity_id = :o AND kind = 'order_reminder' AND status = 'active'"
        ),
        {"o": order_id},
    ).scalar_one()
    assert open_reminders == 0


def test_a_due_custom_reminder_fires_ahead_of_the_flat_cadence_and_is_consumed(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    """A due `remind_at` fires even though the order was nudged moments ago (the flat
    cadence's ten minutes has not elapsed), and is cleared back to `NULL` once it does —
    a one-time request must not keep re-firing every ten minutes afterwards."""
    from bm.domain.auth.permissions import permissions_for_role
    from bm.domain.orders import service as orders
    from bm.domain.shared.context import AuthContext

    ctx = AuthContext(
        user_id=owner.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id,
        device_id=owner.device_id,
        role="owner",
        permissions=frozenset(str(p) for p in permissions_for_role(SystemRole.OWNER)),
        token_id="t",
    )
    order_id = orders.create_order(
        ctx, migration_session, body="2x brake pad", customer_name=None, phone=None
    )
    # Just nudged a moment ago: the flat cadence alone would not fire again yet.
    migration_session.execute(
        text("UPDATE orders SET last_reminder_at = :t WHERE id = :id"),
        {"t": datetime.now(UTC), "id": order_id},
    )
    orders.set_reminder(
        ctx, migration_session, order_id, remind_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    migration_session.commit()

    handlers.handle_orders_nudge({"business_id": str(tenant.business_id)})
    migration_session.commit()

    open_reminders = migration_session.execute(
        text(
            "SELECT count(*) FROM notifications"
            " WHERE entity_id = :o AND kind = 'order_reminder' AND status = 'active'"
        ),
        {"o": order_id},
    ).scalar_one()
    assert open_reminders == 1

    remind_at = migration_session.execute(
        text("SELECT remind_at FROM orders WHERE id = :id"), {"id": order_id}
    ).scalar_one()
    assert remind_at is None


# --- reminders.check_schedules -----------------------------------------------------


def test_the_bill_reminder_fires_only_when_a_credit_bill_is_outstanding(
    api_client: TestClient,
    headers: dict[str, str],
    migration_session: Session,
    tenant: Tenant,
    owner: UserFixture,
    engine_bound: None,
    credit_sale_product: str,
) -> None:
    from bm.domain.reminders import service as reminders

    reminders.ensure_defaults(migration_session, tenant.business_id)
    migration_session.commit()

    # Force the bill reminder's fire_time to the past so "due" does not depend on the
    # wall clock at test time.
    migration_session.execute(
        text(
            "UPDATE reminder_schedules SET fire_time = '00:00'"
            " WHERE business_id = :b AND kind = 'bill_reminder'"
        ),
        {"b": tenant.business_id},
    )
    migration_session.commit()

    handlers.handle_reminders_check_schedules({"business_id": str(tenant.business_id)})
    migration_session.commit()
    none_yet = migration_session.execute(
        text("SELECT count(*) FROM notifications WHERE kind = 'bill_reminder'")
    ).scalar_one()
    assert none_yet == 0, "no outstanding credit bill exists yet"

    _credit_sale(api_client, headers, tenant, credit_sale_product)
    migration_session.commit()

    handlers.handle_reminders_check_schedules({"business_id": str(tenant.business_id)})
    migration_session.commit()
    fired = migration_session.execute(
        text(
            "SELECT title, body FROM notifications"
            " WHERE business_id = :b AND kind = 'bill_reminder' AND status = 'active'"
        ),
        {"b": tenant.business_id},
    ).one()
    assert "1 credit bill" in fired.title
    assert "1000" in fired.body

    # The next tick (fire_time already passed, same business-day) must not duplicate.
    handlers.handle_reminders_check_schedules({"business_id": str(tenant.business_id)})
    migration_session.commit()
    count = migration_session.execute(
        text(
            "SELECT count(*) FROM notifications WHERE business_id = :b AND kind = 'bill_reminder'"
        ),
        {"b": tenant.business_id},
    ).scalar_one()
    assert count == 1


def test_a_custom_interval_reminder_fires_once_the_interval_elapses(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    from bm.domain.auth.permissions import permissions_for_role
    from bm.domain.reminders import service as reminders
    from bm.domain.shared.context import AuthContext

    ctx = AuthContext(
        user_id=owner.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id,
        device_id=owner.device_id,
        role="owner",
        permissions=frozenset(str(p) for p in permissions_for_role(SystemRole.OWNER)),
        token_id="t",
    )
    schedule_id = reminders.create_schedule(
        ctx, migration_session, label="Call the tyre supplier", fire_time=None, interval_hours=4
    )
    migration_session.commit()

    now = datetime.now(UTC)
    fired_first = reminders.check_and_fire(
        migration_session, tenant.business_id, "Asia/Kolkata", now
    )
    migration_session.commit()
    assert fired_first == 1

    # Immediately again: not due yet.
    fired_again = reminders.check_and_fire(
        migration_session, tenant.business_id, "Asia/Kolkata", now + timedelta(minutes=1)
    )
    migration_session.commit()
    assert fired_again == 0

    # Once the interval has elapsed, it fires again, superseding the open one rather
    # than piling up a second.
    fired_later = reminders.check_and_fire(
        migration_session, tenant.business_id, "Asia/Kolkata", now + timedelta(hours=5)
    )
    migration_session.commit()
    assert fired_later == 1

    open_count = migration_session.execute(
        text("SELECT count(*) FROM notifications WHERE entity_id = :s AND status = 'active'"),
        {"s": schedule_id},
    ).scalar_one()
    assert open_count == 1
