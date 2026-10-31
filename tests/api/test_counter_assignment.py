"""C-2 / FR-ADMIN-003 — counter administration and the device→counter assignment.

Why this file exists
--------------------

A freshly enrolled counter PC could not bill. `register_device` sets `branch_id` and
deliberately not `counter_id`, `resolve_context` falls back to the registered device's
counter, and nothing in the system ever wrote that column — so the fallback had nothing
to fall back to and `/sync/bootstrap` returned `counter: null` for ever. The frontend was
fixed in the previous round to say so instead of spinning; this is the other half, the
part that lets an administrator actually fix it.

`API-SPECIFICATION.md §3.13` specifies `GET /admin/counters` and
`PUT /admin/counters/{id}` under `admin.settings.manage` and neither existed.
`PUT /admin/devices/{id}/counter` is new, and the reasoning for its path and permission
is in `bm.domain.auth.counters`.

What is asserted here, and why each one matters
-----------------------------------------------

* The **acceptance test** is not "the column changed" but "bootstrap now resolves a
  counter", because that is the thing that was broken. A test that only checked the
  column would have passed against a system that still could not bill.
* **Several devices may share one counter.** `OFFLINE-SYNCHRONIZATION.md` describes "the
  same counter on two devices (a tablet and the counter PC)" as a supported case, there
  is no unique constraint, and `tests/fixtures.py` registers every test device against
  the same counter. So this is asserted as *allowed* — if a future change rejects it as a
  duplicate, this file fails, which is the point.
* **Cross-tenant ids return 404, never 403** (`API-SPECIFICATION.md §4.1`), for the
  device in the path and for the counter in the body. A 403 would confirm the id exists
  in some other shop.
* A **revoked** device cannot be handed a counter. Revocation is the control for a stolen
  till (T-4); an assignment route that reactivated one would be a way back in.
* Only `admin.settings.manage` may do any of it. A **manager holds
  `admin.devices.manage`** and is still refused, which is the specific boundary worth
  pinning: the two permissions are one word apart and a manager can already revoke.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import ROLE_PERMISSIONS, Permission, SystemRole
from tests.fixtures import Tenant, UserFixture, create_tenant, create_user

pytestmark = pytest.mark.db


def _headers(client: TestClient, user: UserFixture) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": user.password,
            "device": {"id": str(user.device_id), "label": "Counter PC"},
        },
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Device-Id": str(user.device_id)}


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def headers(api_client: TestClient, owner: UserFixture) -> dict[str, str]:
    return _headers(api_client, owner)


def _unassigned_device(
    session: Session, tenant: Tenant, *, label: str = "New Counter PC"
) -> uuid.UUID:
    """A device registered to the branch but pointed at no counter.

    This is exactly what `register_device` produces on a brand-new browser profile, so
    the tests below start from the real broken state rather than a contrived one.
    """
    device_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO devices (id, business_id, branch_id, counter_id, label)"
            " VALUES (:id, :b, :br, NULL, :label)"
        ),
        {"id": device_id, "b": tenant.business_id, "br": tenant.branch_id, "label": label},
    )
    session.commit()
    return device_id


def _second_counter(session: Session, tenant: Tenant, *, code: str = "C2") -> uuid.UUID:
    counter_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO counters (id, business_id, branch_id, code, name)"
            " VALUES (:id, :b, :br, :code, :name)"
        ),
        {
            "id": counter_id,
            "b": tenant.business_id,
            "br": tenant.branch_id,
            "code": code,
            "name": f"Counter {code}",
        },
    )
    session.commit()
    return counter_id


def _audit_rows(session: Session, action: str) -> list[dict[str, Any]]:
    session.commit()
    rows = session.execute(
        text(
            "SELECT entity_type, entity_id, before_value AS before, after_value AS after"
            " FROM audit_logs WHERE action = :a ORDER BY created_at"
        ),
        {"a": action},
    ).mappings()
    return [dict(r) for r in rows]


# --- GET /admin/counters ------------------------------------------------------


def test_the_counter_list_shows_the_shops_counters(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    response = api_client.get("/api/v1/admin/counters", headers=headers)
    assert response.status_code == 200, response.text
    counters = response.json()
    assert [c["id"] for c in counters] == [str(tenant.counter_id)]
    assert counters[0]["code"] == "C1"
    assert counters[0]["max_open_drafts"] == 20
    assert counters[0]["is_active"] is True


def test_the_counter_list_names_the_computers_assigned_to_each_counter(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    owner: UserFixture,
    migration_session: Session,
) -> None:
    """The administrator has to see the current assignment before changing it; a list of
    counter codes alone would make reassignment guesswork."""
    device_id = _unassigned_device(migration_session, tenant, label="Till 2 PC")
    api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(tenant.counter_id)},
    )

    counters = api_client.get("/api/v1/admin/counters", headers=headers).json()
    labels = {d["label"] for d in counters[0]["devices"]}
    # The owner's own login device is registered against this counter by the fixture.
    assert "Till 2 PC" in labels


def test_the_counter_list_is_scoped_to_the_callers_business(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """AC-8."""
    other = create_tenant(
        migration_session, display_name="OTHER SHOP", branch_code="OTH", counter_code="C9"
    )
    counters = api_client.get("/api/v1/admin/counters", headers=headers).json()
    ids = {c["id"] for c in counters}
    assert str(other.counter_id) not in ids
    assert str(tenant.counter_id) in ids


def test_an_anonymous_caller_cannot_list_counters(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/admin/counters").status_code == 401


@pytest.mark.parametrize("role", [SystemRole.CASHIER, SystemRole.STOCK_KEEPER, SystemRole.MANAGER])
def test_only_settings_managers_may_list_counters(
    api_client: TestClient, migration_session: Session, tenant: Tenant, role: SystemRole
) -> None:
    """A manager is included deliberately: they hold `admin.devices.manage` and can
    revoke a till, but counter assignment is business configuration."""
    user = create_user(
        migration_session, tenant, username=f"list-{role}", role=role, all_branches=True
    )
    response = api_client.get("/api/v1/admin/counters", headers=_headers(api_client, user))
    assert response.status_code == 403
    assert response.json()["error"]["details"]["required_permission"] == "admin.settings.manage"


def test_a_manager_really_does_hold_the_device_permission_but_not_the_settings_one(
    migration_session: Session,
) -> None:
    """Guards the test above from passing for the wrong reason.

    If a manager held neither permission the 403 would prove nothing about the
    distinction this design rests on.
    """
    held = ROLE_PERMISSIONS[SystemRole.MANAGER]
    assert Permission.ADMIN_DEVICES_MANAGE in held
    assert Permission.ADMIN_SETTINGS_MANAGE not in held
    assert Permission.ADMIN_SETTINGS_MANAGE in ROLE_PERMISSIONS[SystemRole.OWNER]


# --- PUT /admin/counters/{id} — FR-ADMIN-003 ----------------------------------


def test_a_counter_can_be_renamed(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    response = api_client.put(
        f"/api/v1/admin/counters/{tenant.counter_id}",
        headers=headers,
        json={"name": "Front Counter"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Front Counter"


def test_draft_capacity_can_be_raised(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    response = api_client.put(
        f"/api/v1/admin/counters/{tenant.counter_id}",
        headers=headers,
        json={"max_open_drafts": 40},
    )
    assert response.status_code == 200, response.text
    assert response.json()["max_open_drafts"] == 40


def test_draft_capacity_below_twenty_is_refused(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    """C-5 is a floor, not a default. Refused at the request model, so the administrator
    is told which field is wrong rather than meeting a constraint violation."""
    response = api_client.put(
        f"/api/v1/admin/counters/{tenant.counter_id}",
        headers=headers,
        json={"max_open_drafts": 19},
    )
    assert response.status_code == 422, response.text


def test_the_database_floor_is_real_and_not_only_the_request_model(
    migration_session: Session, tenant: Tenant
) -> None:
    """`ck_counters__min_drafts` is the constraint that holds when a future caller does
    not go through this router. Asserted directly so the 422 above cannot be the only
    thing standing between C-5 and a broken counter."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        migration_session.execute(
            text("UPDATE counters SET max_open_drafts = 5 WHERE id = :id"),
            {"id": tenant.counter_id},
        )
        migration_session.commit()
    migration_session.rollback()


def test_updating_an_unknown_counter_is_a_not_found(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    response = api_client.put(
        f"/api/v1/admin/counters/{uuid.uuid4()}", headers=headers, json={"name": "Nope"}
    )
    assert response.status_code == 404


def test_another_shops_counter_is_a_not_found_rather_than_a_forbidden(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    """A 403 here would confirm the id exists somewhere (`API-SPECIFICATION.md §4.1`)."""
    other = create_tenant(
        migration_session, display_name="OTHER SHOP", branch_code="OTH", counter_code="C9"
    )
    response = api_client.put(
        f"/api/v1/admin/counters/{other.counter_id}", headers=headers, json={"name": "Theirs"}
    )
    assert response.status_code == 404

    # And it really was not touched.
    name = migration_session.execute(
        text("SELECT name FROM counters WHERE id = :id"), {"id": other.counter_id}
    ).scalar()
    assert name != "Theirs"


def test_a_counter_change_is_audited_with_before_and_after(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    api_client.put(
        f"/api/v1/admin/counters/{tenant.counter_id}",
        headers=headers,
        json={"name": "Audited Counter", "max_open_drafts": 25},
    )
    rows = _audit_rows(migration_session, "counter.updated")
    assert len(rows) == 1
    assert rows[0]["entity_type"] == "counter"
    assert rows[0]["entity_id"] == tenant.counter_id
    assert rows[0]["before"]["max_open_drafts"] == 20
    assert rows[0]["after"]["max_open_drafts"] == 25
    assert rows[0]["after"]["name"] == "Audited Counter"


def test_a_change_that_changes_nothing_writes_no_audit_row(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """An audit trail that records non-events is harder to read when it matters."""
    current = api_client.get("/api/v1/admin/counters", headers=headers).json()[0]
    response = api_client.put(
        f"/api/v1/admin/counters/{tenant.counter_id}",
        headers=headers,
        json={"name": current["name"], "max_open_drafts": current["max_open_drafts"]},
    )
    assert response.status_code == 200, response.text
    assert _audit_rows(migration_session, "counter.updated") == []


def test_a_counter_with_computers_still_on_it_cannot_be_closed(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """Closing it would leave those tills signed in, assigned, and unable to bill — the
    same dead end C-2 is about, reached from the other side."""
    device_id = _unassigned_device(migration_session, tenant, label="Still Here PC")
    api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(tenant.counter_id)},
    )

    response = api_client.put(
        f"/api/v1/admin/counters/{tenant.counter_id}", headers=headers, json={"is_active": False}
    )
    assert response.status_code == 422, response.text
    assert "Still Here PC" in response.text


def test_an_empty_counter_can_be_closed(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """The counterpart to the test above — the refusal must be about the devices, not a
    blanket ban on closing a counter."""
    spare = _second_counter(migration_session, tenant, code="C7")
    response = api_client.put(
        f"/api/v1/admin/counters/{spare}", headers=headers, json={"is_active": False}
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False


@pytest.mark.parametrize("role", [SystemRole.CASHIER, SystemRole.MANAGER])
def test_only_settings_managers_may_change_a_counter(
    api_client: TestClient, migration_session: Session, tenant: Tenant, role: SystemRole
) -> None:
    user = create_user(
        migration_session, tenant, username=f"upd-{role}", role=role, all_branches=True
    )
    response = api_client.put(
        f"/api/v1/admin/counters/{tenant.counter_id}",
        headers=_headers(api_client, user),
        json={"name": "Nope"},
    )
    assert response.status_code == 403


# --- PUT /admin/devices/{id}/counter — the assignment -------------------------


def test_assigning_a_counter_sets_it_on_the_device(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    device_id = _unassigned_device(migration_session, tenant)
    response = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(tenant.counter_id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["counter_id"] == str(tenant.counter_id)


def test_after_assignment_bootstrap_resolves_the_counter_and_billing_can_start(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    migration_session: Session,
) -> None:
    """**The acceptance test for C-2.**

    Not "the column changed" but "the client is now told which counter it is", because
    `counter: null` from this endpoint is the exact value that left the billing screen
    with nothing to mount. Asserted before and after, so a system that was already
    returning a counter could not make this pass.
    """
    staff = create_user(
        migration_session,
        tenant,
        username="till-user",
        role=SystemRole.OWNER,
        all_branches=True,
        register_device=False,
    )
    device_id = _unassigned_device(migration_session, tenant, label="Till PC")

    login = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": staff.username,
            "password": staff.password,
            "device": {"id": str(device_id), "label": "Till PC"},
        },
    )
    assert login.status_code == 200, login.text
    till_headers = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Device-Id": str(device_id),
    }

    before = api_client.get("/api/v1/sync/bootstrap", headers=till_headers)
    assert before.status_code == 200, before.text
    assert before.json()["counter"] is None, "the broken state this requirement is about"

    assert (
        api_client.put(
            f"/api/v1/admin/devices/{device_id}/counter",
            headers=headers,
            json={"counter_id": str(tenant.counter_id)},
        ).status_code
        == 200
    )

    after = api_client.get("/api/v1/sync/bootstrap", headers=till_headers)
    assert after.status_code == 200, after.text
    counter = after.json()["counter"]
    assert counter is not None, "bootstrap still cannot name the counter"
    assert counter["id"] == str(tenant.counter_id)
    assert counter["code"] == "C1"


def test_an_assignment_can_be_changed_to_another_counter(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    device_id = _unassigned_device(migration_session, tenant)
    second = _second_counter(migration_session, tenant)

    api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(tenant.counter_id)},
    )
    response = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(second)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["counter_id"] == str(second)


def test_a_computer_can_be_taken_off_every_counter(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """Retiring a machine from billing without revoking it. Revocation refuses the device
    everywhere (T-4) and is a much heavier statement."""
    device_id = _unassigned_device(migration_session, tenant)
    api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(tenant.counter_id)},
    )
    response = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter", headers=headers, json={"counter_id": None}
    )
    assert response.status_code == 200, response.text
    assert response.json()["counter_id"] is None
    assert response.json()["status"] == "active", "unassigning must not revoke"


def test_assigning_the_counter_a_computer_already_has_is_not_an_error(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """A double click or a retried request, not a conflict. And no second audit row,
    because nothing changed."""
    device_id = _unassigned_device(migration_session, tenant)
    body = {"counter_id": str(tenant.counter_id)}
    first = api_client.put(f"/api/v1/admin/devices/{device_id}/counter", headers=headers, json=body)
    second = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter", headers=headers, json=body
    )
    assert first.status_code == 200
    assert second.status_code == 200, second.text
    assert second.json()["counter_id"] == str(tenant.counter_id)
    assert len(_audit_rows(migration_session, "device.counter_assigned")) == 1


def test_two_computers_may_share_one_counter(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """**Allowed, deliberately.**

    `OFFLINE-SYNCHRONIZATION.md` calls "the same counter on two devices (a tablet and the
    counter PC)" the real case its conflict rules exist for, `DECISIONS.md` says the same
    counter may edit one draft from two devices, there is no unique constraint on
    `devices.counter_id`, and `tests/fixtures.py` points every test device at one
    counter. If someone later "fixes" this as a duplicate assignment, this test fails —
    which is cheaper than discovering it when the shop's tablet stops working.
    """
    first_device = _unassigned_device(migration_session, tenant, label="Counter PC")
    second_device = _unassigned_device(migration_session, tenant, label="Counter Tablet")
    body = {"counter_id": str(tenant.counter_id)}

    assert (
        api_client.put(
            f"/api/v1/admin/devices/{first_device}/counter", headers=headers, json=body
        ).status_code
        == 200
    )
    response = api_client.put(
        f"/api/v1/admin/devices/{second_device}/counter", headers=headers, json=body
    )
    assert response.status_code == 200, response.text

    counters = api_client.get("/api/v1/admin/counters", headers=headers).json()
    labels = {d["label"] for d in counters[0]["devices"]}
    assert {"Counter PC", "Counter Tablet"} <= labels


def test_assigning_an_unknown_computer_is_a_not_found(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    response = api_client.put(
        f"/api/v1/admin/devices/{uuid.uuid4()}/counter",
        headers=headers,
        json={"counter_id": str(tenant.counter_id)},
    )
    assert response.status_code == 404


def test_another_shops_computer_is_a_not_found(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    other = create_tenant(
        migration_session, display_name="OTHER SHOP", branch_code="OTH", counter_code="C9"
    )
    other_user = create_user(
        migration_session, other, username="other-owner", role=SystemRole.OWNER, all_branches=True
    )
    response = api_client.put(
        f"/api/v1/admin/devices/{other_user.device_id}/counter",
        headers=headers,
        json={"counter_id": str(tenant.counter_id)},
    )
    assert response.status_code == 404

    # Their device was not quietly repointed at our counter.
    counter_id = migration_session.execute(
        text("SELECT counter_id FROM devices WHERE id = :id"), {"id": other_user.device_id}
    ).scalar()
    assert counter_id != tenant.counter_id


def test_assigning_an_unknown_counter_is_a_not_found(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    device_id = _unassigned_device(migration_session, tenant)
    response = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


def test_another_shops_counter_cannot_be_assigned_and_is_not_confirmed_to_exist(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """The cross-tenant case for a body field rather than a path parameter. Same rule:
    404, so the response does not confirm the counter exists in another shop."""
    other = create_tenant(
        migration_session, display_name="OTHER SHOP", branch_code="OTH", counter_code="C9"
    )
    device_id = _unassigned_device(migration_session, tenant)
    response = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(other.counter_id)},
    )
    assert response.status_code == 404

    counter_id = migration_session.execute(
        text("SELECT counter_id FROM devices WHERE id = :id"), {"id": device_id}
    ).scalar()
    assert counter_id is None, "a cross-tenant counter must not be written"


def test_a_revoked_computer_cannot_be_given_a_counter(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """Revocation is the control for a stolen till (T-4). An assignment route that
    reactivated one would be a way back into billing without re-authorisation."""
    device_id = _unassigned_device(migration_session, tenant, label="Stolen PC")
    migration_session.execute(
        text(
            "UPDATE devices SET status = 'revoked', revoked_at = now(),"
            " revoke_reason = 'stolen from the counter' WHERE id = :id"
        ),
        {"id": device_id},
    )
    migration_session.commit()

    response = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(tenant.counter_id)},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "DEVICE_REVOKED"

    counter_id = migration_session.execute(
        text("SELECT counter_id FROM devices WHERE id = :id"), {"id": device_id}
    ).scalar()
    assert counter_id is None


def test_a_closed_counter_cannot_be_assigned(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    closed = _second_counter(migration_session, tenant, code="C8")
    api_client.put(f"/api/v1/admin/counters/{closed}", headers=headers, json={"is_active": False})
    device_id = _unassigned_device(migration_session, tenant)

    response = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(closed)},
    )
    assert response.status_code == 422, response.text


def test_a_counter_in_another_branch_cannot_be_assigned(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """Branch decides which stock a sale draws down and which series numbers the bill, so
    a device billing as a counter in another branch would move stock in one place and
    number the invoice in another."""
    other_branch = uuid.uuid4()
    other_counter = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO branches (id, business_id, code, name)"
            " VALUES (:id, :b, 'BR2', 'Second Branch')"
        ),
        {"id": other_branch, "b": tenant.business_id},
    )
    migration_session.execute(
        text(
            "INSERT INTO counters (id, business_id, branch_id, code, name)"
            " VALUES (:id, :b, :br, 'B2C1', 'Branch 2 Counter')"
        ),
        {"id": other_counter, "b": tenant.business_id, "br": other_branch},
    )
    migration_session.commit()

    device_id = _unassigned_device(migration_session, tenant)
    response = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(other_counter)},
    )
    assert response.status_code == 422, response.text
    assert "branch" in response.text.lower()


def test_an_assignment_is_audited_with_before_and_after(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """ "Whose takings does this machine's next sale land in" has to be answerable later
    (FR-AUDIT-001)."""
    device_id = _unassigned_device(migration_session, tenant)
    api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={"counter_id": str(tenant.counter_id)},
    )
    rows = _audit_rows(migration_session, "device.counter_assigned")
    assert len(rows) == 1
    assert rows[0]["entity_type"] == "device"
    assert rows[0]["entity_id"] == device_id
    assert rows[0]["before"]["counter_id"] is None
    assert rows[0]["after"]["counter_id"] == str(tenant.counter_id)


def test_an_anonymous_caller_cannot_assign_a_counter(
    api_client: TestClient, tenant: Tenant, migration_session: Session
) -> None:
    device_id = _unassigned_device(migration_session, tenant)
    response = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        json={"counter_id": str(tenant.counter_id)},
    )
    assert response.status_code == 401


@pytest.mark.parametrize("role", [SystemRole.CASHIER, SystemRole.MANAGER, SystemRole.STOCK_KEEPER])
def test_only_settings_managers_may_assign_a_counter(
    api_client: TestClient, migration_session: Session, tenant: Tenant, role: SystemRole
) -> None:
    """NFR-SEC-004 — enforced at the backend, not by hiding the screen."""
    user = create_user(
        migration_session, tenant, username=f"assign-{role}", role=role, all_branches=True
    )
    device_id = _unassigned_device(migration_session, tenant)
    response = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=_headers(api_client, user),
        json={"counter_id": str(tenant.counter_id)},
    )
    assert response.status_code == 403

    counter_id = migration_session.execute(
        text("SELECT counter_id FROM devices WHERE id = :id"), {"id": device_id}
    ).scalar()
    assert counter_id is None, "a refused request must not have written anything"


def test_the_assignment_route_cannot_change_anything_but_the_counter(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, migration_session: Session
) -> None:
    """The body is a sub-resource with one field. A general device update would let a
    counter assignment also rewrite the label, branch or revocation state, and
    `StrictModel` is what makes the attempt a 422 rather than a silent no-op."""
    device_id = _unassigned_device(migration_session, tenant, label="Original Label")
    response = api_client.put(
        f"/api/v1/admin/devices/{device_id}/counter",
        headers=headers,
        json={
            "counter_id": str(tenant.counter_id),
            "label": "Renamed",
            "status": "revoked",
            "branch_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422, response.text

    row = (
        migration_session.execute(
            text("SELECT label, status, counter_id FROM devices WHERE id = :id"), {"id": device_id}
        )
        .mappings()
        .one()
    )
    assert row["label"] == "Original Label"
    assert row["status"] == "active"
    assert row["counter_id"] is None
