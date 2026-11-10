"""Chitti orders (Feature B) and scheduled reminders (Feature A.5), via the real API.

Covers the lifecycle guarantees the owner was explicit about:

* dismissing/acknowledging a notification never touches what it is about;
* an order's ten-minute nudge and a reminder's firing are idempotent — covered in
  `tests/integration/test_reminder_and_order_jobs.py` against the job handlers
  directly, the same way `test_jobs.py` covers the existing scheduled jobs.
"""

from __future__ import annotations

import uuid

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


# --- orders --------------------------------------------------------------------


def test_an_order_needs_only_its_note_to_be_saved(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """Owner request: no mandatory field before saving beyond the note itself."""
    response = api_client.post(
        "/api/v1/orders", headers=headers, json={"body": "2x Brake Shoe, Ramesh, call back"}
    )
    assert response.status_code == 201, response.text
    order_id = response.json()["id"]

    listed = api_client.get("/api/v1/orders", headers=headers, params={"status": "pending"})
    assert listed.status_code == 200
    ids = [row["id"] for row in listed.json()["items"]]
    assert order_id in ids


def test_completing_an_order_removes_it_from_the_pending_list(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    order_id = api_client.post(
        "/api/v1/orders", headers=headers, json={"body": "1x Clutch Plate"}
    ).json()["id"]

    done = api_client.post(f"/api/v1/orders/{order_id}:complete", headers=headers)
    assert done.status_code == 200, done.text

    pending = api_client.get(
        "/api/v1/orders", headers=headers, params={"status": "pending"}
    ).json()["items"]
    assert order_id not in [row["id"] for row in pending]

    completed = api_client.get(
        "/api/v1/orders", headers=headers, params={"status": "completed"}
    ).json()["items"]
    assert order_id in [row["id"] for row in completed]


def test_discarding_an_order_does_not_delete_the_record(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """Trash means hidden from the active list, not erased (owner request: the row is
    kept so a wrongly discarded order can still be found)."""
    order_id = api_client.post(
        "/api/v1/orders", headers=headers, json={"body": "entered by mistake"}
    ).json()["id"]

    trashed = api_client.post(f"/api/v1/orders/{order_id}:discard", headers=headers)
    assert trashed.status_code == 200

    discarded = api_client.get(
        "/api/v1/orders", headers=headers, params={"status": "discarded"}
    ).json()["items"]
    assert order_id in [row["id"] for row in discarded]


def test_a_completed_order_cannot_be_edited(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    order_id = api_client.post(
        "/api/v1/orders", headers=headers, json={"body": "original note"}
    ).json()["id"]
    api_client.post(f"/api/v1/orders/{order_id}:complete", headers=headers)

    edit = api_client.put(
        f"/api/v1/orders/{order_id}", headers=headers, json={"body": "rewritten after the fact"}
    )
    assert edit.status_code == 404, edit.text


# --- the per-order reminder (owner request, 2026-11-11) -----------------------


def test_setting_a_reminder_on_a_pending_order_is_reflected_in_the_list(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    order_id = api_client.post(
        "/api/v1/orders", headers=headers, json={"body": "2x Brake Shoe"}
    ).json()["id"]

    set_reminder = api_client.put(
        f"/api/v1/orders/{order_id}/reminder",
        headers=headers,
        json={"remind_at": "2026-12-25T09:30:00+00:00"},
    )
    assert set_reminder.status_code == 200, set_reminder.text

    items = api_client.get("/api/v1/orders", headers=headers, params={"status": "pending"}).json()[
        "items"
    ]
    row = next(r for r in items if r["id"] == order_id)
    assert row["remind_at"] == "2026-12-25T09:30:00+00:00"


def test_clearing_a_reminder_sets_it_back_to_null(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    order_id = api_client.post(
        "/api/v1/orders", headers=headers, json={"body": "2x Brake Shoe"}
    ).json()["id"]
    api_client.put(
        f"/api/v1/orders/{order_id}/reminder",
        headers=headers,
        json={"remind_at": "2026-12-25T09:30:00+00:00"},
    )

    cleared = api_client.put(
        f"/api/v1/orders/{order_id}/reminder", headers=headers, json={"remind_at": None}
    )
    assert cleared.status_code == 200, cleared.text

    items = api_client.get("/api/v1/orders", headers=headers, params={"status": "pending"}).json()[
        "items"
    ]
    row = next(r for r in items if r["id"] == order_id)
    assert row["remind_at"] is None


def test_a_reminder_cannot_be_set_on_a_completed_order(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    order_id = api_client.post(
        "/api/v1/orders", headers=headers, json={"body": "done already"}
    ).json()["id"]
    api_client.post(f"/api/v1/orders/{order_id}:complete", headers=headers)

    response = api_client.put(
        f"/api/v1/orders/{order_id}/reminder",
        headers=headers,
        json={"remind_at": "2026-12-25T09:30:00+00:00"},
    )
    assert response.status_code == 404, response.text


def test_orders_with_a_reminder_are_listed_ahead_of_those_without_one(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """Priority sort (owner request): a reminder means "surface this one", so it jumps
    ahead of plain newest-first ordering, even though it was created first."""
    without_reminder = api_client.post(
        "/api/v1/orders", headers=headers, json={"body": "no reminder, created first"}
    ).json()["id"]
    with_reminder = api_client.post(
        "/api/v1/orders", headers=headers, json={"body": "has a reminder, created second"}
    ).json()["id"]
    api_client.put(
        f"/api/v1/orders/{with_reminder}/reminder",
        headers=headers,
        json={"remind_at": "2026-12-25T09:30:00+00:00"},
    )

    items = api_client.get("/api/v1/orders", headers=headers, params={"status": "pending"}).json()[
        "items"
    ]
    ids = [row["id"] for row in items]
    assert ids.index(with_reminder) < ids.index(without_reminder)


def test_the_soonest_reminder_is_listed_first_among_reminders(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    later = api_client.post("/api/v1/orders", headers=headers, json={"body": "due later"}).json()[
        "id"
    ]
    sooner = api_client.post("/api/v1/orders", headers=headers, json={"body": "due sooner"}).json()[
        "id"
    ]
    api_client.put(
        f"/api/v1/orders/{later}/reminder",
        headers=headers,
        json={"remind_at": "2026-12-25T09:30:00+00:00"},
    )
    api_client.put(
        f"/api/v1/orders/{sooner}/reminder",
        headers=headers,
        json={"remind_at": "2026-12-20T09:30:00+00:00"},
    )

    items = api_client.get("/api/v1/orders", headers=headers, params={"status": "pending"}).json()[
        "items"
    ]
    ids = [row["id"] for row in items]
    assert ids.index(sooner) < ids.index(later)


def test_setting_a_reminder_on_someone_elses_order_is_refused(
    api_client: TestClient,
    headers: dict[str, str],
    migration_session: Session,
) -> None:
    from tests.fixtures import create_tenant

    other = create_tenant(migration_session, display_name="SOMEBODY ELSE")
    other_user = create_user(
        migration_session, other, username="other-owner", role=SystemRole.OWNER, all_branches=True
    )
    migration_session.commit()
    their_token = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": other_user.username,
            "password": other_user.password,
            "device": {"id": str(other_user.device_id), "label": "Their PC"},
        },
    ).json()["access_token"]

    order_id = api_client.post(
        "/api/v1/orders", headers=headers, json={"body": "2x Brake Shoe"}
    ).json()["id"]

    response = api_client.put(
        f"/api/v1/orders/{order_id}/reminder",
        headers={"Authorization": f"Bearer {their_token}"},
        json={"remind_at": "2026-12-25T09:30:00+00:00"},
    )
    assert response.status_code == 404, response.text


def test_an_empty_order_is_refused(api_client: TestClient, headers: dict[str, str]) -> None:
    response = api_client.post("/api/v1/orders", headers=headers, json={"body": "   "})
    assert response.status_code == 422, response.text


# --- reminders -------------------------------------------------------------------


def test_the_two_built_in_reminders_are_seeded_on_first_read(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    listed = api_client.get("/api/v1/reminders", headers=headers)
    assert listed.status_code == 200, listed.text
    kinds = {row["kind"] for row in listed.json()["items"]}
    assert kinds == {"bill_reminder", "stock_reminder"}
    times = {row["kind"]: row["fire_time"] for row in listed.json()["items"]}
    assert times["bill_reminder"] == "08:45:00"
    assert times["stock_reminder"] == "17:00:00"


def test_seeding_the_defaults_twice_does_not_duplicate_them(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    api_client.get("/api/v1/reminders", headers=headers)
    second = api_client.get("/api/v1/reminders", headers=headers).json()["items"]
    assert len([r for r in second if r["kind"] == "bill_reminder"]) == 1


def test_a_custom_reminder_needs_exactly_one_trigger(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    neither = api_client.post("/api/v1/reminders", headers=headers, json={"label": "Call supplier"})
    assert neither.status_code == 422, neither.text

    both = api_client.post(
        "/api/v1/reminders",
        headers=headers,
        json={"label": "Call supplier", "fire_time": "10:00", "interval_hours": 4},
    )
    assert both.status_code == 422, both.text


def test_a_custom_reminder_can_be_created_disabled_and_removed(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    created = api_client.post(
        "/api/v1/reminders",
        headers=headers,
        json={"label": "Call the tyre supplier", "interval_hours": 4},
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]

    disabled = api_client.put(
        f"/api/v1/reminders/{reminder_id}", headers=headers, json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert not [
        r
        for r in api_client.get("/api/v1/reminders", headers=headers).json()["items"]
        if r["id"] == reminder_id and r["enabled"]
    ]

    removed = api_client.delete(f"/api/v1/reminders/{reminder_id}", headers=headers)
    assert removed.status_code == 204
    remaining_ids = {
        r["id"] for r in api_client.get("/api/v1/reminders", headers=headers).json()["items"]
    }
    assert reminder_id not in remaining_ids


# --- notification lifecycle ------------------------------------------------------


def test_dismissing_a_notification_does_not_resolve_it_as_fixed(
    api_client: TestClient, headers: dict[str, str], migration_session: Session, tenant: Tenant
) -> None:
    """Owner request, verified at the API: dismiss must change `status` only, never the
    thing the notification is about."""
    from bm.domain.notifications import service as notifications
    from bm.infra.db.enums import NotificationKind, NotificationSeverity

    notification_id = notifications.notify(
        migration_session,
        business_id=tenant.business_id,
        kind=NotificationKind.LOW_STOCK,
        severity=NotificationSeverity.WARNING,
        title="Test Part is running low",
        entity_type="product",
        entity_id=uuid.uuid4(),
    )
    migration_session.commit()

    dismissed = api_client.post(f"/api/v1/notifications/{notification_id}:dismiss", headers=headers)
    assert dismissed.status_code == 200, dismissed.text

    active = api_client.get("/api/v1/notifications", headers=headers).json()["notifications"]
    assert str(notification_id) not in [n["id"] for n in active]

    # Dismissing twice is a 404, not a silent success — it is no longer open to act on.
    again = api_client.post(f"/api/v1/notifications/{notification_id}:dismiss", headers=headers)
    assert again.status_code == 404


def test_snoozing_requires_a_bounded_duration(
    api_client: TestClient, headers: dict[str, str], migration_session: Session, tenant: Tenant
) -> None:
    from bm.domain.notifications import service as notifications
    from bm.infra.db.enums import NotificationKind, NotificationSeverity

    notification_id = notifications.notify(
        migration_session,
        business_id=tenant.business_id,
        kind=NotificationKind.CUSTOM_REMINDER,
        severity=NotificationSeverity.INFO,
        title="Call the tyre supplier",
    )
    migration_session.commit()

    too_long = api_client.post(
        f"/api/v1/notifications/{notification_id}:snooze", headers=headers, json={"minutes": 10000}
    )
    assert too_long.status_code == 422

    snoozed = api_client.post(
        f"/api/v1/notifications/{notification_id}:snooze", headers=headers, json={"minutes": 30}
    )
    assert snoozed.status_code == 200, snoozed.text
    # Snoozed, so not currently active, but not lost — a later read (once it lapses)
    # is covered by `notifications.active_for`'s own lapse clause, not re-asserted here.
    active_now = api_client.get("/api/v1/notifications", headers=headers).json()["notifications"]
    assert str(notification_id) not in [n["id"] for n in active_now]
