"""The report day boundary is the **business's** day, not UTC's.

Why this file exists
--------------------

`bm/api/v1/reports.py` has always promised this in its own module docstring — "Day
boundaries use **business-local time** (T3-02), so a sale at 23:59:59 and one at
00:00:00 land on different days" — and the report SQL has always kept the promise,
grouping on `(issued_at AT TIME ZONE :timezone)::date`.

The *default* date did not. Every endpoint passed `clock.now().date()`, and `SystemClock`
is UTC by design, so for `Asia/Kolkata` the default and the grouping disagreed for the
first five and a half hours of every business day. Asked for "today" at 01:30 local, the
report defaulted to **yesterday's** business date and came back empty — while the bills
were sitting in the database, correctly stamped.

It surfaced as two integration tests in `test_billing_workflows.py` failing at
2026-10-31 00:22 IST that had passed earlier the same day. A wall-clock-dependent
failure is the worst kind to leave pinned by a wall-clock-dependent test, so the tests
here **freeze the clock** at an instant where the UTC date and the business date are
known to differ. They fail on the old behaviour at any hour, and pass on the new one at
any hour.

`business_date()` already existed for this, with a docstring describing this exact
problem. It simply was not called at the one place that defaulted a date.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from bm.domain.shared.clock import FrozenClock, business_date
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db

#: 20:00 UTC is 01:30 the **next day** in Asia/Kolkata (UTC+5:30).
#:
#: Chosen so the two calendars genuinely disagree: the UTC date is 30 October and the
#: business date is 31 October. Any instant in the 18:30–24:00 UTC window would do; this
#: one is comfortably inside it rather than on its edge.
LATE_EVENING_UTC = datetime(2026, 10, 30, 20, 0, 0, tzinfo=UTC)
UTC_DAY = date(2026, 10, 30)
BUSINESS_DAY = date(2026, 10, 31)


def test_the_fixture_instant_really_does_straddle_two_calendar_days() -> None:
    """A guard on the guard. If this ever stops being true — a timezone database change,
    an edited constant — every assertion below would pass for the wrong reason."""
    assert LATE_EVENING_UTC.date() == UTC_DAY
    assert business_date(LATE_EVENING_UTC, "Asia/Kolkata") == BUSINESS_DAY
    assert UTC_DAY != BUSINESS_DAY


# --- the unit the defect lived in --------------------------------------------


@pytest.mark.parametrize(
    ("timezone", "expected"),
    [
        # Ahead of UTC: the business is already on the next day.
        ("Asia/Kolkata", date(2026, 10, 31)),
        ("Asia/Tokyo", date(2026, 10, 31)),
        # At UTC: the two agree, so the old code was accidentally right here — which is
        # exactly why a UTC-only test would never have caught this.
        ("UTC", date(2026, 10, 30)),
        # Behind UTC: the business is still on the previous day.
        ("America/New_York", date(2026, 10, 30)),
        ("Pacific/Honolulu", date(2026, 10, 30)),
    ],
)
def test_today_is_resolved_in_the_business_timezone(timezone: str, expected: date) -> None:
    """`_today` is the helper every report endpoint defaults through."""
    from bm.api.v1.reports import _today

    class _Ctx:
        business_timezone = timezone

    clock = FrozenClock(LATE_EVENING_UTC)
    assert _today(clock, _Ctx()) == expected  # type: ignore[arg-type]


def test_today_is_not_the_utc_date_for_an_india_business() -> None:
    """Stated as its own assertion because it *is* the defect, in one line."""
    from bm.api.v1.reports import _today

    class _Ctx:
        business_timezone = "Asia/Kolkata"

    clock = FrozenClock(LATE_EVENING_UTC)
    assert _today(clock, _Ctx()) != clock.now().date()  # type: ignore[arg-type]


# --- end to end, through the real engine and the real report -----------------


@pytest.fixture
def frozen_api_client(api_settings: Any, app_engine: Any, schema_at_head: None) -> Any:
    """An API client whose clock can be frozen in the straddling window.

    The clock is **not** frozen at construction, and `freeze_clock` below is what
    installs it. Authentication has to happen under the real clock first: the access
    token's `exp` and `nbf` are validated by PyJWT against the real wall clock, not
    against the injected `Clock`, so a token minted an hour in the future is rejected as
    immature and one minted a day in the past as expired. Either way the request never
    reaches the report code the test is about.

    Everything that matters is still deterministic. Once frozen, the same instant stamps
    `invoices.issued_at` and defaults the report date, because both come from the
    injected clock — which is the property under test.
    """
    from bm.infra.db import session as session_module
    from bm.main import create_app

    session_module.dispose_engine()
    session_module.init_engine(api_settings)
    app = create_app(api_settings)
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.clear()
    session_module.dispose_engine()


def freeze_clock(client: TestClient) -> None:
    """Pin the application clock to the straddling instant, after login."""
    from bm.api.deps import clock_dep

    client.app.dependency_overrides[clock_dep] = lambda: FrozenClock(  # type: ignore[union-attr]
        LATE_EVENING_UTC
    )


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


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


def _sell_one(client: TestClient, headers: dict[str, str], tenant: Tenant) -> None:
    """A whole sale through the real engine: units, product, stock, finalisation."""
    assert client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200

    product = client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": "Boundary Test Part",
            "unit_code": "NOS",
            "default_selling_price": "1250.0000",
            "sku": "BD-1",
            "cost_price": "980.0000",
        },
    )
    assert product.status_code == 201, product.text
    product_id = product.json()["id"]

    # A supplier is an upsert on a client-generated id, so a retry is not a second
    # supplier.
    supplier = client.put(
        f"/api/v1/suppliers/{uuid.uuid4()}",
        headers=headers,
        json={"name": "Allied Spares Depot", "phone": "9876543210"},
    )
    assert supplier.status_code == 200, supplier.text
    supplier_id = supplier.json()["id"]

    # Stock arrives only through a confirmed purchase.
    purchase = client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "supplier_id": supplier_id,
            "supplier_document_number": "BD/2026/1",
            "items": [{"product_id": product_id, "quantity": "10.000", "unit_cost": "980.0000"}],
        },
    )
    assert purchase.status_code == 201, purchase.text
    confirmed = client.post(
        f"/api/v1/purchases/{purchase.json()['purchase_id']}:confirm",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert confirmed.status_code == 200, confirmed.text

    sale = client.post(
        "/api/v1/sales:finalize",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={
            "counter_id": str(tenant.counter_id),
            "sale_type": "cash",
            "lines": [
                {
                    "product_id": product_id,
                    "quantity": "1.000",
                    "unit_price": "1250.0000",
                    "price_source": "catalog",
                }
            ],
            "header": {"customer_name": "Imran"},
            "client_computed_total": "1250.00",
            # A cash bill must settle in full at the counter.
            "payment": {"method": "cash", "amount": "1250.00"},
        },
    )
    assert sale.status_code in (200, 201), sale.text


def test_daily_sales_defaults_to_the_business_day_not_the_utc_day(
    frozen_api_client: TestClient, owner: UserFixture, tenant: Tenant
) -> None:
    """The regression. A bill rung up at 01:30 local must appear in "today's" report.

    With the UTC default this returned `bill_count: 0` — the owner cashing up after
    midnight saw an empty day while the money was in the drawer.
    """
    headers = _headers(frozen_api_client, owner, tenant)
    assert tenant.timezone == "Asia/Kolkata"
    freeze_clock(frozen_api_client)
    _sell_one(frozen_api_client, headers, tenant)

    report = frozen_api_client.get("/api/v1/reports/daily-sales", headers=headers)
    assert report.status_code == 200, report.text
    body = report.json()

    assert body["bill_count"] == 1, (
        "the default report day is not the business day; a sale made at "
        f"01:30 local is being looked for on {UTC_DAY} instead of {BUSINESS_DAY}"
    )
    assert body["net"] == "1250.00"
    # And the filters echo the business day, so the response says which day it means.
    assert body["filters"]["from"] == BUSINESS_DAY.isoformat()
    assert body["filters"]["to"] == BUSINESS_DAY.isoformat()


def test_asking_for_the_utc_day_explicitly_finds_nothing(
    frozen_api_client: TestClient, owner: UserFixture, tenant: Tenant
) -> None:
    """The other half of the proof.

    If the previous test passed because the two dates happened to coincide, this one
    would fail: the UTC day must be genuinely empty.
    """
    headers = _headers(frozen_api_client, owner, tenant)
    freeze_clock(frozen_api_client)
    _sell_one(frozen_api_client, headers, tenant)

    report = frozen_api_client.get(
        "/api/v1/reports/daily-sales",
        headers=headers,
        params={"date": UTC_DAY.isoformat()},
    )
    assert report.status_code == 200, report.text
    assert report.json()["bill_count"] == 0


def test_the_two_report_views_reconcile_at_the_boundary(
    frozen_api_client: TestClient, owner: UserFixture, tenant: Tenant
) -> None:
    """FR-RPT-003 across the boundary.

    `daily-sales` and `credit-outstanding` must describe the same money whatever the
    hour — the pair that broke first when the default day was wrong.
    """
    headers = _headers(frozen_api_client, owner, tenant)
    freeze_clock(frozen_api_client)
    _sell_one(frozen_api_client, headers, tenant)

    check = frozen_api_client.get("/api/v1/reports/reconciliation-check", headers=headers)
    assert check.status_code == 200, check.text
    assert check.json()["reconciles"] is True, check.json()


def test_counter_reconciliation_also_defaults_to_the_business_day(
    frozen_api_client: TestClient, owner: UserFixture, tenant: Tenant
) -> None:
    """The same default lived in four endpoints; this is the second of them.

    `counter-reconciliation` is what a cashier's shift is checked against, so an empty
    one at 01:30 reads as "you took no money".
    """
    headers = _headers(frozen_api_client, owner, tenant)
    freeze_clock(frozen_api_client)
    _sell_one(frozen_api_client, headers, tenant)

    report = frozen_api_client.get("/api/v1/reports/counter-reconciliation", headers=headers)
    assert report.status_code == 200, report.text
    assert report.json()["filters"]["date"] == BUSINESS_DAY.isoformat()


def test_item_sales_also_defaults_to_the_business_day(
    frozen_api_client: TestClient, owner: UserFixture, tenant: Tenant
) -> None:
    """And the third."""
    headers = _headers(frozen_api_client, owner, tenant)
    freeze_clock(frozen_api_client)
    _sell_one(frozen_api_client, headers, tenant)

    report = frozen_api_client.get("/api/v1/reports/item-sales", headers=headers)
    assert report.status_code == 200, report.text
    assert report.json()["filters"]["from"] == BUSINESS_DAY.isoformat()


def test_the_csv_export_defaults_to_the_business_day(
    frozen_api_client: TestClient, owner: UserFixture, tenant: Tenant
) -> None:
    """And the fourth. An export that silently covers the wrong day is worse than one
    that fails, because it ends up in an accountant's spreadsheet."""
    headers = _headers(frozen_api_client, owner, tenant)
    freeze_clock(frozen_api_client)
    _sell_one(frozen_api_client, headers, tenant)

    export = frozen_api_client.get(
        "/api/v1/reports/daily-sales/export", headers=headers, params={"format": "csv"}
    )
    assert export.status_code == 200, export.text
    assert BUSINESS_DAY.isoformat() in export.text
