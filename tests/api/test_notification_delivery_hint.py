"""`next_check_after_seconds` — the hint that replaced the 30-second poll.

The client no longer owns a cadence; it sleeps for exactly as long as the server says the
next *scheduled* notification is away (`notificationsPoller.ts`). That makes this field
load-bearing: too large and a 5 PM summary sits undiscovered, too small and the request
volume the owner complained about comes straight back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from bm.domain.reminders import service as reminders
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db

FLOOR = reminders._HINT_FLOOR_SECONDS
CEILING = reminders._HINT_CEILING_SECONDS


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


# --- the endpoint contract ------------------------------------------------------


def test_the_notification_list_always_carries_a_usable_hint(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """The client has no fallback cadence of its own beyond a clamp, so the field must
    always be present and always be sane."""
    body = api_client.get("/api/v1/notifications", headers=headers).json()

    assert "next_check_after_seconds" in body
    hint = body["next_check_after_seconds"]
    assert isinstance(hint, int)
    assert FLOOR <= hint <= CEILING


def test_a_business_with_no_schedules_is_told_to_wait_the_maximum(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """Nothing is due, ever, so there is nothing to come back for sooner. This is the
    case the old code spent 120 requests an hour on."""
    body = api_client.get("/api/v1/notifications", headers=headers).json()
    assert body["next_check_after_seconds"] == CEILING


# --- the arithmetic -------------------------------------------------------------


def _schedule(
    session: Session,
    tenant: Tenant,
    *,
    kind: str,
    fire_time: str | None,
    interval_hours: int | None,
    last_fired_at: datetime | None,
) -> None:
    session.execute(
        text(
            "INSERT INTO reminder_schedules"
            " (id, business_id, kind, label, fire_time, interval_hours, last_fired_at)"
            " VALUES (:id, :b, :kind, 'Test reminder', :fire_time, :interval, :last)"
        ),
        {
            "id": uuid.uuid4(),
            "b": tenant.business_id,
            "kind": kind,
            "fire_time": fire_time,
            "interval": interval_hours,
            "last": last_fired_at,
        },
    )
    session.commit()


def test_an_hourly_reminder_just_fired_is_most_of_an_hour_away(
    migration_session: Session, tenant: Tenant
) -> None:
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    _schedule(
        migration_session,
        tenant,
        kind="bill_reminder",
        fire_time=None,
        interval_hours=1,
        last_fired_at=now - timedelta(minutes=5),
    )

    hint = reminders.next_due_in_seconds(migration_session, tenant.business_id, "Asia/Kolkata", now)

    # 55 minutes away, so the ceiling is what the client is told — it will check again
    # well before the reminder exists, which is the cost of bounding a stale hint.
    assert hint == CEILING


def test_an_hourly_reminder_due_in_two_minutes_is_reported_precisely(
    migration_session: Session, tenant: Tenant
) -> None:
    """The case the ceiling must not swallow: something is imminent, so the client is
    told to come back in about two minutes rather than up to fifteen."""
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    _schedule(
        migration_session,
        tenant,
        kind="bill_reminder",
        fire_time=None,
        interval_hours=1,
        last_fired_at=now - timedelta(minutes=58),
    )

    hint = reminders.next_due_in_seconds(migration_session, tenant.business_id, "Asia/Kolkata", now)

    assert 110 <= hint <= 130, hint


def test_an_overdue_reminder_reports_the_ceiling_not_the_floor(
    migration_session: Session, tenant: Tenant
) -> None:
    """A schedule that returned "nothing to report" keeps its slot and stays overdue
    **indefinitely** — `_fire_one` deliberately does not advance `last_fired_at`, so a
    shop with no overdue credit bill never leaves this state.

    This assertion used to be `== FLOOR`, and that was the defect: it told every
    signed-in tab to come back in 60 seconds, permanently, to be told "still nothing".
    Measured against the real stack it produced one request a minute per tab forever
    (five in a five-minute idle session). Overdue is not the same as imminent: the
    scheduler re-checks this row every 60 s, so anything reportable has already been
    reported, and a new notification needs the underlying data to change first.

    The imminent cases are unaffected and are asserted either side of this one.
    """
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    _schedule(
        migration_session,
        tenant,
        kind="bill_reminder",
        fire_time=None,
        interval_hours=1,
        last_fired_at=now - timedelta(hours=9),
    )

    hint = reminders.next_due_in_seconds(migration_session, tenant.business_id, "Asia/Kolkata", now)

    assert hint == CEILING


def test_a_never_fired_interval_reminder_reports_the_ceiling(
    migration_session: Session, tenant: Tenant
) -> None:
    """`last_fired_at IS NULL` is the state a freshly seeded shop sits in forever while
    it has no overdue credit bill — this is the exact row that was pinning production's
    hint to 60 seconds. Same reasoning as the overdue test above."""
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    _schedule(
        migration_session,
        tenant,
        kind="bill_reminder",
        fire_time=None,
        interval_hours=1,
        last_fired_at=None,
    )

    assert (
        reminders.next_due_in_seconds(migration_session, tenant.business_id, "Asia/Kolkata", now)
        == CEILING
    )


def test_an_idle_shop_is_never_told_to_come_back_at_the_floor(
    migration_session: Session, tenant: Tenant
) -> None:
    """The regression this whole change exists to prevent, asserted as the property
    rather than as one arrangement of rows.

    Both built-in reminders in the state a real idle shop holds them in — enabled, never
    fired, nothing outstanding — plus the evening half of the daily summary's day, which
    was a second instance of the same pin (nothing low at 17:00 meant 60-second requests
    from 17:00 until midnight).

    If any future change reintroduces floor-pinning for a shop with nothing to report,
    this fails, and the symptom it describes is a request loop in production rather than
    a number in a test.
    """
    # 13:30 UTC is 19:00 Asia/Kolkata — past the 17:00 summary, which has not fired.
    now = datetime(2026, 11, 22, 13, 30, tzinfo=UTC)
    _schedule(
        migration_session,
        tenant,
        kind="bill_reminder",
        fire_time=None,
        interval_hours=1,
        last_fired_at=None,
    )
    _schedule(
        migration_session,
        tenant,
        kind="stock_reminder",
        fire_time="17:00",
        interval_hours=None,
        last_fired_at=None,
    )

    hint = reminders.next_due_in_seconds(migration_session, tenant.business_id, "Asia/Kolkata", now)

    assert hint == CEILING, (
        f"an idle shop was told to re-check in {hint}s. At the 60-second floor that is "
        "1,440 requests per tab per day, forever, every one of them answered 'nothing'."
    )


def test_the_five_pm_summary_is_reported_precisely_when_it_is_minutes_away(
    migration_session: Session, tenant: Tenant
) -> None:
    """The scenario that decides whether this design works at all: a cashier who has not
    touched the browser since four o'clock must still see the 5 PM summary. At 16:58
    business-local the hint has to be ~2 minutes, not the ceiling.

    11:28 UTC is 16:58 Asia/Kolkata (UTC+5:30).
    """
    now = datetime(2026, 11, 22, 11, 28, tzinfo=UTC)
    _schedule(
        migration_session,
        tenant,
        kind="stock_reminder",
        fire_time="17:00",
        interval_hours=None,
        last_fired_at=None,
    )

    hint = reminders.next_due_in_seconds(migration_session, tenant.business_id, "Asia/Kolkata", now)

    assert 110 <= hint <= 130, hint


def test_a_daily_summary_already_fired_today_is_clamped_to_the_ceiling(
    migration_session: Session, tenant: Tenant
) -> None:
    """Tomorrow's 17:00 is ~23 hours away. The client is still told 15 minutes, because
    the owner could enable another reminder in the meantime."""
    now = datetime(2026, 11, 22, 12, 30, tzinfo=UTC)  # 18:00 IST
    _schedule(
        migration_session,
        tenant,
        kind="stock_reminder",
        fire_time="17:00",
        interval_hours=None,
        last_fired_at=now - timedelta(minutes=30),
    )

    assert (
        reminders.next_due_in_seconds(migration_session, tenant.business_id, "Asia/Kolkata", now)
        == CEILING
    )


def test_a_disabled_reminder_is_not_counted(migration_session: Session, tenant: Tenant) -> None:
    """The reason the hint comes from the server at all: the owner can switch a reminder
    off, and a client with a hardcoded hourly cadence would carry on regardless."""
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    _schedule(
        migration_session,
        tenant,
        kind="bill_reminder",
        fire_time=None,
        interval_hours=1,
        last_fired_at=now - timedelta(minutes=58),
    )
    migration_session.execute(
        text("UPDATE reminder_schedules SET enabled = false WHERE business_id = :b"),
        {"b": tenant.business_id},
    )
    migration_session.commit()

    assert (
        reminders.next_due_in_seconds(migration_session, tenant.business_id, "Asia/Kolkata", now)
        == CEILING
    )


def test_the_soonest_of_several_schedules_wins(migration_session: Session, tenant: Tenant) -> None:
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    _schedule(
        migration_session,
        tenant,
        kind="bill_reminder",
        fire_time=None,
        interval_hours=1,
        last_fired_at=now - timedelta(minutes=10),
    )
    # A 3-hour custom reminder last fired 2h58m ago, so it is 2 minutes away — sooner
    # than the hourly one's remaining 50 minutes.
    _schedule(
        migration_session,
        tenant,
        kind="custom",
        fire_time=None,
        interval_hours=3,
        last_fired_at=now - timedelta(hours=2, minutes=58),
    )

    hint = reminders.next_due_in_seconds(migration_session, tenant.business_id, "Asia/Kolkata", now)

    assert 110 <= hint <= 130, hint


def test_a_pending_order_due_a_nudge_shortens_the_hint(
    api_client: TestClient, headers: dict[str, str], migration_session: Session, tenant: Tenant
) -> None:
    """Pending-order nudges are the third scheduled producer, so they belong in the
    hint too — otherwise the hourly nudge would be up to 15 minutes late."""
    created = api_client.post(
        "/api/v1/orders",
        headers=headers,
        json={"body": "2x brake pad for Suresh"},
    )
    assert created.status_code == 201, created.text
    now = datetime.now(UTC)
    migration_session.execute(
        text("UPDATE orders SET last_reminder_at = :last, remind_at = NULL WHERE business_id = :b"),
        {"last": now - timedelta(minutes=58), "b": tenant.business_id},
    )
    migration_session.commit()

    hint = reminders.next_due_in_seconds(migration_session, tenant.business_id, "Asia/Kolkata", now)

    assert 60 <= hint <= 190, hint
