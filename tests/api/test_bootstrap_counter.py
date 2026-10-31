"""Which counter `GET /sync/bootstrap` reports, and why it matters so much.

The defect this file pins
------------------------

This lookup was **not scoped to the caller's business**. It read

    SELECT code, name, max_open_drafts FROM counters WHERE id = :id

and `ctx.counter_id` is taken from the caller-supplied `X-Counter-Id` header without
`AuthService.resolve_context` checking that the id belongs to the caller's business. So
an authenticated user of one shop could send another shop's counter id and read that
counter's **code and name** straight back. Two fields is a narrow leak, and it is still a
cross-tenant disclosure of the kind `SECURITY.md §3.2` and AC-8 exist to prevent — and
the kind that grows the next time a field is added to this payload.

How the counter is resolved, which is worth stating because it is not obvious
----------------------------------------------------------------------------

`resolve_context` already takes the first of: the `X-Counter-Id` header, the token's own
claim, and **the registered device's `counter_id`**. So a provisioned counter PC needs no
header — the device knows which till it is. That chain is pre-existing and is left
alone; the tests below pin it so it cannot be removed by accident, because the counter
screen depends on it completely.

Why that matters more than it looks
-----------------------------------

`BillingCounter` renders `Busy("Opening your bills")` until its draft store is
initialised, and `AppShell` only initialises it once a counter is known. A device with no
counter therefore sat on that spinner **for ever** — no error, no retry, nothing to act
on. The full-stack end-to-end suite found it on 31 October 2026; it is the first suite in
this project that talks to a real server, and the stubbed suites all stub this response
with a counter present, so none of them could have.

What is deliberately *not* fixed here
-------------------------------------

A device registered by `POST /auth/login` has `counter_id = NULL` — `register_device`
sets `branch_id` and deliberately not `counter_id` — and there is **no endpoint to assign
one**. `GET /admin/counters` and `PUT /admin/counters/<id>` appear in
`API-SPECIFICATION.md` and are not implemented, and C-2 "counter selection"
(`TRACEABILITY.md`) has neither an endpoint nor a screen. So an *unprovisioned* device
still cannot bill, by design gap rather than by defect. That is an unimplemented
requirement and is reported as one; the honest behaviour for it — saying so plainly
instead of spinning — is asserted by
`test_a_device_with_no_counter_gets_null_rather_than_a_guess` here and by the
"not assigned to a counter" panel in `AppShell`.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from tests.fixtures import Tenant, UserFixture, create_tenant, create_user

pytestmark = pytest.mark.db


def _login(client: TestClient, user: UserFixture) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": user.password,
            "device": {"id": str(user.device_id), "label": "Counter 1 PC"},
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


def _bootstrap(
    client: TestClient, token: str, *, device_id: uuid.UUID | None, counter_id: uuid.UUID | None
) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if device_id is not None:
        headers["X-Device-Id"] = str(device_id)
    if counter_id is not None:
        headers["X-Counter-Id"] = str(counter_id)
    response = client.get("/api/v1/sync/bootstrap", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_a_device_registered_to_a_counter_gets_that_counter_without_the_header(
    api_client: TestClient, owner: UserFixture, tenant: Tenant
) -> None:
    """No `X-Counter-Id`, and a counter still comes back.

    `create_user` registers the device against the tenant's counter, which is what a
    provisioned counter PC looks like. This is the property the billing screen depends on
    absolutely — without a counter it never mounts — so it is pinned here rather than
    left implicit inside `resolve_context`.
    """
    token = _login(api_client, owner)
    body = _bootstrap(api_client, token, device_id=owner.device_id, counter_id=None)

    assert body["counter"] is not None, (
        "bootstrap returned no counter for a device that is registered to one; "
        "the client has no other way to learn it, so billing can never start"
    )
    assert body["counter"]["id"] == str(tenant.counter_id)
    assert body["counter"]["code"] == "C1"
    # `max_open_drafts` is what the client enforces its tab limit from (C-5 floor of 20).
    assert body["counter"]["max_open_drafts"] >= 20


def test_an_explicit_counter_header_still_wins(
    api_client: TestClient, owner: UserFixture, tenant: Tenant, migration_session: Session
) -> None:
    """The pre-existing behaviour, unchanged.

    A machine that already knows its till keeps saying so, and a supervisor can address
    a specific counter deliberately. The device fallback is a fallback, not a override.
    """
    other = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO counters (id, business_id, branch_id, code, name, max_open_drafts)"
            " VALUES (:id, :b, :br, 'C2', 'Counter 2', 20)"
        ),
        {"id": other, "b": tenant.business_id, "br": tenant.branch_id},
    )
    migration_session.commit()

    token = _login(api_client, owner)
    body = _bootstrap(api_client, token, device_id=owner.device_id, counter_id=other)

    assert body["counter"]["id"] == str(other)
    assert body["counter"]["code"] == "C2"


def test_a_device_with_no_counter_gets_null_rather_than_a_guess(
    api_client: TestClient, owner: UserFixture, migration_session: Session
) -> None:
    """The unprovisioned counter PC, asserted rather than papered over.

    `POST /auth/login` enrols a device with a `branch_id` and deliberately **no**
    `counter_id`, and nothing assigns one afterwards: `GET /admin/counters` and
    `PUT /admin/counters/<id>` are specified but unimplemented, and C-2 "counter
    selection" has neither an endpoint nor a screen. So this is the state every
    brand-new counter PC is actually in.

    The server must **not** invent a counter here. Picking "the branch's only counter"
    would be a business decision nobody made, and in a two-till shop it would silently
    attribute one cashier's takings to the other's till. `null` is the honest answer, and
    the client is responsible for saying so — which it now does, instead of spinning.
    """
    migration_session.execute(
        text("UPDATE devices SET counter_id = NULL WHERE id = :d"), {"d": owner.device_id}
    )
    migration_session.commit()

    token = _login(api_client, owner)
    body = _bootstrap(api_client, token, device_id=owner.device_id, counter_id=None)

    assert body["counter"] is None
    # Everything else still arrives, so the client can explain the problem rather than
    # failing to start at all.
    assert body["money_policy"]["version"] == 1
    assert "catalog.read" in body["permissions"]


def test_another_tenants_counter_cannot_be_named_in_the_header(
    api_client: TestClient, owner: UserFixture, migration_session: Session
) -> None:
    """§4.1. The header is caller-supplied, so it is scoped, not trusted."""
    other_tenant = create_tenant(
        migration_session, display_name="OTHER SHOP", branch_code="OTH", counter_code="X1"
    )
    token = _login(api_client, owner)

    body = _bootstrap(
        api_client, token, device_id=owner.device_id, counter_id=other_tenant.counter_id
    )
    # Absent, not another business's counter — and not a 403, which would confirm it
    # exists.
    assert body["counter"] is None


def test_a_device_pointing_at_another_tenants_counter_is_also_refused(
    api_client: TestClient, owner: UserFixture, migration_session: Session
) -> None:
    """The same scoping on the fallback path.

    A cross-tenant `devices.counter_id` should not be reachable either. It cannot occur
    through the API, but the fallback must be scoped on its own merits rather than on
    the assumption that the column is always clean.
    """
    other_tenant = create_tenant(
        migration_session, display_name="THIRD SHOP", branch_code="THR", counter_code="Z1"
    )
    migration_session.execute(
        text("UPDATE devices SET counter_id = :c WHERE id = :d"),
        {"c": other_tenant.counter_id, "d": owner.device_id},
    )
    migration_session.commit()

    token = _login(api_client, owner)
    body = _bootstrap(api_client, token, device_id=owner.device_id, counter_id=None)
    assert body["counter"] is None


def test_the_token_claim_carries_the_device_when_the_header_is_absent(
    api_client: TestClient, owner: UserFixture, tenant: Tenant
) -> None:
    """`resolve_context` falls back to the token's own device claim.

    So omitting `X-Device-Id` does not lose the counter either. Worth pinning: it is the
    reason a reload mid-shift does not drop a cashier back to a spinner.
    """
    token = _login(api_client, owner)
    response = api_client.get(
        "/api/v1/sync/bootstrap", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counter"] is not None
    assert body["counter"]["id"] == str(tenant.counter_id)
    assert body["money_policy"]["currency"] == "INR"
