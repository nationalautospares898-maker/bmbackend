"""The owner's single computer bills without anybody setting up a counter.

The owner runs the shop from one PC and asked for the counter feature to leave the
workflow. It could not simply be hidden from the interface: `draft_bills.counter_id` and
`invoices.counter_id` are `NOT NULL`, so a machine with no counter could authenticate
and then be unable to save a bill — which is the dead end these tests exist to close.

The counter therefore stays as internal bookkeeping (history readable, reports intact, no
migration) and the **requirement to configure it** is what was removed: a device binds
itself on first sign-in. What follows checks that it binds, that billing then works
end to end, and — just as important — that the automatic binding cannot trample a
decision a person made on purpose.
"""

from __future__ import annotations

import uuid

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
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


def _login(client: TestClient, user: UserFixture, *, device_id: uuid.UUID) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": user.password,
            "device": {"id": str(device_id), "label": "Shop PC"},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _counter_of(session: Session, device_id: uuid.UUID) -> uuid.UUID | None:
    return session.execute(
        text("SELECT counter_id FROM devices WHERE id = :id"), {"id": device_id}
    ).scalar_one()


def test_a_brand_new_computer_can_bill_without_being_set_up(
    api_client: TestClient, migration_session: Session, tenant: Tenant, owner: UserFixture
) -> None:
    """The headline: a device nobody has configured signs in and is ready to bill.

    This is the whole requirement in one test. Before the change, `bootstrap.counter` was
    `null` for a device like this and the billing screen had nothing to open bills
    against.
    """
    fresh_device = uuid.uuid4()

    session = _login(api_client, owner, device_id=fresh_device)
    token = session["access_token"]

    bootstrap = api_client.get(
        "/api/v1/sync/bootstrap", headers={"Authorization": f"Bearer {token}"}
    )
    assert bootstrap.status_code == 200, bootstrap.text
    counter = bootstrap.json()["counter"]

    assert counter is not None, "a freshly installed computer must be ready to bill"
    assert uuid.UUID(counter["id"]) == tenant.counter_id
    # And it was persisted, not merely reported for this one request.
    assert _counter_of(migration_session, fresh_device) == tenant.counter_id


def test_the_automatic_binding_is_recorded_in_the_audit_trail(
    api_client: TestClient, migration_session: Session, tenant: Tenant, owner: UserFixture
) -> None:
    """Removing the human step must not remove the record of what was decided.

    The trail still answers "whose takings does this machine's next sale land in", and
    says plainly that the server decided rather than a person.
    """
    fresh_device = uuid.uuid4()
    _login(api_client, owner, device_id=fresh_device)

    row = migration_session.execute(
        text(
            "SELECT counter_id, reason, before_value, after_value FROM audit_logs"
            " WHERE action = 'device.counter_assigned' AND entity_id = :device"
        ),
        {"device": fresh_device},
    ).one()

    assert row.counter_id == tenant.counter_id
    assert row.reason == "assigned automatically on first sign-in"
    assert row.before_value == {"counter_id": None}
    assert row.after_value == {"counter_id": str(tenant.counter_id)}


def test_a_computer_already_pointed_at_a_counter_is_left_alone(
    api_client: TestClient, migration_session: Session, tenant: Tenant, owner: UserFixture
) -> None:
    """An assignment somebody made on purpose outranks the automatic one.

    A second counter exists and this device is deliberately pointed at it. Signing in
    must not quietly move the machine back to the default — that would redirect a till's
    takings on every login, and it is the reason the `UPDATE` carries a
    `counter_id IS NULL` guard rather than writing unconditionally.
    """
    second_counter = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO counters (id, business_id, branch_id, code, name)"
            " VALUES (:id, :business_id, :branch_id, 'C2', 'Counter C2')"
        ),
        {"id": second_counter, "business_id": tenant.business_id, "branch_id": tenant.branch_id},
    )
    migration_session.execute(
        text("UPDATE devices SET counter_id = :counter WHERE id = :id"),
        {"counter": second_counter, "id": owner.device_id},
    )
    migration_session.commit()

    _login(api_client, owner, device_id=owner.device_id)

    assert _counter_of(migration_session, owner.device_id) == second_counter


def test_an_existing_computer_whose_counter_was_cleared_recovers_on_sign_in(
    api_client: TestClient, migration_session: Session, tenant: Tenant, owner: UserFixture
) -> None:
    """The repair path for a machine enrolled before this change.

    `bm`'s own `Probe` device is in exactly this state — registered, active, and carrying
    `counter_id IS NULL` — so without this the owner's existing install would still need
    the screen that was removed.
    """
    migration_session.execute(
        text("UPDATE devices SET counter_id = NULL WHERE id = :id"), {"id": owner.device_id}
    )
    migration_session.commit()
    assert _counter_of(migration_session, owner.device_id) is None

    _login(api_client, owner, device_id=owner.device_id)

    assert _counter_of(migration_session, owner.device_id) == tenant.counter_id


def test_a_closed_counter_is_never_chosen(
    api_client: TestClient, migration_session: Session, tenant: Tenant, owner: UserFixture
) -> None:
    """A deactivated counter cannot accept a sale, so it is not an eligible default.

    With the shop's only counter closed there is genuinely no answer, and the server says
    so rather than binding to a till that would refuse the first bill.
    """
    migration_session.execute(
        text("UPDATE devices SET counter_id = NULL WHERE id = :id"), {"id": owner.device_id}
    )
    migration_session.execute(
        text("UPDATE counters SET is_active = false WHERE id = :id"), {"id": tenant.counter_id}
    )
    migration_session.commit()

    session = _login(api_client, owner, device_id=owner.device_id)
    bootstrap = api_client.get(
        "/api/v1/sync/bootstrap",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )

    assert bootstrap.json()["counter"] is None
    assert _counter_of(migration_session, owner.device_id) is None


def test_a_revoked_computer_cannot_bind_its_way_back_into_billing(
    api_client: TestClient, migration_session: Session, tenant: Tenant, owner: UserFixture
) -> None:
    """Revocation is the control for a stolen till (T-4) and this must not weaken it.

    Login is refused outright, and the binding does not happen either — so a machine
    that is refused everywhere does not quietly acquire a counter on the way out.
    """
    migration_session.execute(
        text(
            "UPDATE devices SET counter_id = NULL, status = 'revoked', revoked_at = now()"
            " WHERE id = :id"
        ),
        {"id": owner.device_id},
    )
    migration_session.commit()

    response = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": owner.username,
            "password": owner.password,
            "device": {"id": str(owner.device_id), "label": "Shop PC"},
        },
    )

    assert response.status_code == 403
    assert _counter_of(migration_session, owner.device_id) is None
