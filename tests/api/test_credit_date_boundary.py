"""Credit ageing and overdue reckoning use the **business's** calendar.

The defect
----------

`invoices.due_date` is a `DATE`, produced by `business_date(now, timezone) + terms` —
a business-local calendar date, and `SalesService` says so in a comment citing T3-02.
Every ageing and overdue query then compared it against `self._clock.now().date()`,
which is **UTC**. Two different calendars either side of the same subtraction.

For `Asia/Kolkata` (UTC+5:30) they disagree for the first 5½ hours of every business day,
and the consequences are financial:

* an invoice due **today** read as **one day overdue** between 00:00 and 05:30 local,
  because `:today` was still yesterday — `due_date < :today` was false while
  `(:today - due_date) BETWEEN 1 AND 30` had already become true;
* every ageing bucket shifted with it, moving balances across the 30/60/90 boundaries a
  day early;
* `GET /reports/credit-outstanding`'s `overdue` total disagreed with the ageing buckets
  computed from the very same rows — two views, reconciling only after 05:30.

This was **not** a new financial policy to decide. T3-02's acceptance criterion
(`PHASE-2-IMPLEMENTATION-PLAN.md`) is that "the financial-year boundary **and report
day-boundaries** are evaluated in business-local time rather than UTC", and
FR-CREDIT-005 / FR-RPT-002 are report day-boundaries. The fix makes both sides of an
existing comparison use the same calendar.

How these tests are built
-------------------------

The clock is **frozen** at an instant where the UTC date and the business date are known
to differ, so the assertions hold at any wall-clock hour — a timezone bug pinned by a
wall-clock-dependent test is not pinned at all. `due_date` is then set to exact offsets
from the business date, so each bucket boundary is hit on the nose rather than
approximately.

Authentication happens before the clock is frozen: PyJWT validates `exp`/`nbf` against
the real wall clock, not against the injected `Clock`, so a token minted at a frozen
instant is rejected as immature or expired before it reaches the code under test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from bm.domain.shared.clock import FrozenClock, business_date
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db

#: 20:00 UTC is 01:30 the **next day** in Asia/Kolkata. Comfortably inside the
#: 18:30–24:00 UTC window where the two calendars disagree, rather than on its edge.
FROZEN_UTC = datetime(2026, 10, 30, 20, 0, 0, tzinfo=UTC)
UTC_DAY = date(2026, 10, 30)
BUSINESS_DAY = date(2026, 10, 31)


def test_the_frozen_instant_really_does_straddle_two_calendar_days() -> None:
    """A guard on the guard: if this stops being true every assertion below is vacuous."""
    assert FROZEN_UTC.date() == UTC_DAY
    assert business_date(FROZEN_UTC, "Asia/Kolkata") == BUSINESS_DAY
    assert (BUSINESS_DAY - UTC_DAY).days == 1


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def api(api_settings: Any, app_engine: Any, schema_at_head: None) -> Any:
    """A client whose clock can be frozen *after* login. See the module docstring."""
    from bm.infra.db import session as session_module
    from bm.main import create_app

    session_module.dispose_engine()
    session_module.init_engine(api_settings)
    app = create_app(api_settings)
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.clear()
    session_module.dispose_engine()


def freeze(client: TestClient) -> None:
    from bm.api.deps import clock_dep

    client.app.dependency_overrides[clock_dep] = lambda: FrozenClock(FROZEN_UTC)  # type: ignore[union-attr]


def headers_for(client: TestClient, user: UserFixture, tenant: Tenant) -> dict[str, str]:
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


def _credit_invoice(
    session: Session, tenant: Tenant, owner: UserFixture, *, due: date, total: str = "1000.00"
) -> uuid.UUID:
    """One unpaid credit invoice with an exact `due_date`.

    Inserted directly, which is the "deliberately constructed state" exception in
    `CLAUDE.md §10`: the point is to place `due_date` on an exact bucket boundary, and
    finalising a sale through the engine would compute a `due_date` from the settings
    instead of letting the test choose one.
    """
    customer_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO customers (id, business_id, name, normalized_name, is_active)"
            " VALUES (:id, :b, :n, :nn, true)"
        ),
        {"id": customer_id, "b": tenant.business_id, "n": f"Cust {due}", "nn": f"cust {due}"},
    )
    series_id = session.execute(
        text(
            "INSERT INTO invoice_series"
            " (id, business_id, branch_id, series_code, financial_year, prefix_template)"
            " VALUES (:id, :b, :br, 'A', '2026-27', 'A/')"
            " ON CONFLICT (business_id, branch_id, series_code, financial_year)"
            " DO UPDATE SET prefix_template = 'A/' RETURNING id"
        ),
        {"id": uuid.uuid4(), "b": tenant.business_id, "br": tenant.branch_id},
    ).scalar_one()
    invoice_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO invoices (id, business_id, branch_id, counter_id, customer_id,"
            " invoice_series_id, sequence_number, invoice_number, document_kind, issued_at,"
            " due_date, is_credit, subtotal, discount_total, tax_total, total, amount_paid,"
            " payment_status, status, created_by_user_id, money_policy_version)"
            " VALUES (:id, :b, :br, :c, :cust, :series,"
            "   (SELECT coalesce(max(sequence_number), 0) + 1 FROM invoices WHERE business_id = :b),"
            "   :ref, 'estimate', :issued, :due, true, :total, 0, 0, :total, 0,"
            "   'unpaid', 'issued', :u, 1)"
        ),
        {
            "id": invoice_id,
            "b": tenant.business_id,
            "br": tenant.branch_id,
            "c": tenant.counter_id,
            "cust": customer_id,
            "series": series_id,
            "ref": f"A/{uuid.uuid4().hex[:8]}",
            "issued": FROZEN_UTC - timedelta(days=60),
            "due": due,
            "total": total,
            "u": owner.id,
        },
    )
    session.commit()
    return invoice_id


# --- the headline case --------------------------------------------------------


def test_an_invoice_due_today_is_not_overdue(
    api: TestClient, owner: UserFixture, tenant: Tenant, migration_session: Session
) -> None:
    """**The defect, in one assertion.**

    Due on the business's today. At 01:30 local the UTC date is still yesterday, so the
    old code computed `today - due_date = -1`… and reported it in the 1–30 bucket via the
    `overdue` filter. A customer was chased for money that was not yet late.
    """
    _credit_invoice(migration_session, tenant, owner, due=BUSINESS_DAY)
    headers = headers_for(api, owner, tenant)
    freeze(api)

    report = api.get("/api/v1/reports/credit-outstanding", headers=headers)
    assert report.status_code == 200, report.text
    body = report.json()
    assert float(body["outstanding"]) == 1000.00
    # Compared numerically: `coalesce(sum(...) FILTER (...), 0)` renders an empty total
    # as `"0"` rather than `"0.00"`, so a string comparison would be asserting the
    # formatter, not the arithmetic. (That inconsistency is noted in the report; it is
    # not changed here, because the field is part of a published contract.)
    assert float(body["overdue"]) == 0.0, (
        "an invoice due today was reported overdue; `today` is not the business's today"
    )


def test_an_invoice_due_on_the_utc_day_is_already_overdue(
    api: TestClient, owner: UserFixture, tenant: Tenant, migration_session: Session
) -> None:
    """The other side of the boundary, so the test above cannot pass by coincidence.

    The UTC date is one day **behind** the business date here, so an invoice due then is
    genuinely one day late in the business's calendar.
    """
    _credit_invoice(migration_session, tenant, owner, due=UTC_DAY)
    headers = headers_for(api, owner, tenant)
    freeze(api)

    body = api.get("/api/v1/reports/credit-outstanding", headers=headers).json()
    assert float(body["overdue"]) == 1000.00


def test_an_invoice_due_tomorrow_is_not_overdue(
    api: TestClient, owner: UserFixture, tenant: Tenant, migration_session: Session
) -> None:
    _credit_invoice(migration_session, tenant, owner, due=BUSINESS_DAY + timedelta(days=1))
    headers = headers_for(api, owner, tenant)
    freeze(api)

    body = api.get("/api/v1/reports/credit-outstanding", headers=headers).json()
    assert float(body["overdue"]) == 0.0


# --- ageing bucket boundaries -------------------------------------------------


@pytest.mark.parametrize(
    ("days_past_due", "bucket"),
    [
        # `current` is "not yet due", so due today belongs here.
        (0, "current"),
        (1, "d1_30"),
        (30, "d1_30"),
        (31, "d31_60"),
        (60, "d31_60"),
        (61, "d61_90"),
        (90, "d61_90"),
        (91, "d90_plus"),
    ],
)
def test_each_ageing_bucket_boundary_is_measured_from_the_business_day(
    api: TestClient,
    owner: UserFixture,
    tenant: Tenant,
    migration_session: Session,
    days_past_due: int,
    bucket: str,
) -> None:
    """The exact boundaries FR-CREDIT-005 names: current, 1–30, 31–60, 61–90, 90+.

    Measured from the **business** day. With the UTC day each of these landed one bucket
    early for 5½ hours out of every 24 — and a balance crossing the 90+ line is the one
    an owner acts on.
    """
    _credit_invoice(
        migration_session, tenant, owner, due=BUSINESS_DAY - timedelta(days=days_past_due)
    )
    headers = headers_for(api, owner, tenant)
    freeze(api)

    response = api.get("/api/v1/credit/ageing-summary", headers=headers)
    assert response.status_code == 200, response.text
    buckets = response.json()["buckets"]

    assert buckets[bucket] == "1000.00", (
        f"{days_past_due} day(s) past the business due date belongs in {bucket}, got {buckets}"
    )
    # And nowhere else: a balance counted twice overstates what the shop is owed.
    for name, amount in buckets.items():
        if name != bucket and not name.startswith("_"):
            assert amount in ("0.00", "0"), f"{name} should be empty, got {amount}"


# --- the two views must agree -------------------------------------------------


def test_the_overdue_total_and_the_ageing_buckets_describe_the_same_money(
    api: TestClient, owner: UserFixture, tenant: Tenant, migration_session: Session
) -> None:
    """FR-RPT-003 across the boundary.

    `credit-outstanding` computes `overdue` in `ReportService`; the buckets come from
    `CreditService`. They read the same rows, so they must agree — and before the fix
    they did not, because only one of them was being told what day it was.
    """
    # One not yet due, one a day late, one deep in 90+.
    _credit_invoice(migration_session, tenant, owner, due=BUSINESS_DAY, total="100.00")
    _credit_invoice(
        migration_session, tenant, owner, due=BUSINESS_DAY - timedelta(days=1), total="200.00"
    )
    _credit_invoice(
        migration_session, tenant, owner, due=BUSINESS_DAY - timedelta(days=120), total="400.00"
    )
    headers = headers_for(api, owner, tenant)
    freeze(api)

    report = api.get("/api/v1/reports/credit-outstanding", headers=headers).json()
    buckets = api.get("/api/v1/credit/ageing-summary", headers=headers).json()["buckets"]

    assert float(report["outstanding"]) == 700.00
    # Only the two past their due date are overdue.
    assert float(report["overdue"]) == 600.00
    assert float(buckets["current"]) == 100.00
    assert float(buckets["d1_30"]) == 200.00
    assert float(buckets["d90_plus"]) == 400.00

    overdue_from_buckets = sum(
        float(buckets[name]) for name in ("d1_30", "d31_60", "d61_90", "d90_plus")
    )
    assert overdue_from_buckets == float(report["overdue"]), (
        "the overdue total and the ageing buckets disagree about the same rows"
    )


def test_days_overdue_per_invoice_is_measured_from_the_business_day(
    api: TestClient, owner: UserFixture, tenant: Tenant, migration_session: Session
) -> None:
    """The per-invoice figure a cashier reads on the customer screen."""
    _credit_invoice(migration_session, tenant, owner, due=BUSINESS_DAY - timedelta(days=5))
    headers = headers_for(api, owner, tenant)
    freeze(api)

    response = api.get("/api/v1/credit/outstanding", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["days_overdue"] == 5, (
        "days_overdue is counted from the UTC day, so it reads one day high overnight"
    )


# --- month and year ends ------------------------------------------------------


@pytest.mark.parametrize(
    ("frozen", "expected_business_day"),
    [
        # Month end: 31 Oct 20:00 UTC is 1 Nov in Kolkata.
        (datetime(2026, 10, 31, 20, 0, tzinfo=UTC), date(2026, 11, 1)),
        # Year end.
        (datetime(2026, 12, 31, 20, 0, tzinfo=UTC), date(2027, 1, 1)),
        # Indian financial-year end (1 April start, so this crosses the FY too).
        (datetime(2027, 3, 31, 20, 0, tzinfo=UTC), date(2027, 4, 1)),
    ],
)
def test_month_and_year_ends_roll_on_the_business_day(
    frozen: datetime, expected_business_day: date
) -> None:
    """Asia/Kolkata has no daylight saving, so the offset is a constant +5:30.

    These are the rollovers where a one-day error is most visible in a report an
    accountant reads: the last day of a month, a year, and the Indian financial year.
    """
    assert business_date(frozen, "Asia/Kolkata") == expected_business_day
    assert frozen.date() != expected_business_day


def test_a_dst_observing_timezone_is_handled_by_the_tz_database() -> None:
    """Not a supported business timezone today, but the helper must not assume a fixed
    offset — `businesses.timezone` is free text and an owner could configure one.

    01:30 on the US spring-forward day: the UTC instant is still the previous day.
    """
    # 2027-03-14 is the US DST transition. 05:30 UTC is 01:30 EST (-05:00).
    moment = datetime(2027, 3, 14, 5, 30, tzinfo=UTC)
    assert business_date(moment, "America/New_York") == date(2027, 3, 14)
    # And an hour later it is 02:30 EDT — the hour that does not exist locally is the
    # tz database's problem, not this code's.
    later = datetime(2027, 3, 14, 7, 30, tzinfo=UTC)
    assert business_date(later, "America/New_York") == date(2027, 3, 14)
