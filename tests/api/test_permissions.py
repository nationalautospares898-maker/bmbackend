"""M3 acceptance — permission and tenant-isolation matrices, **generated** from the
route table.

Why generated rather than hand-written: a hand-written list covers the endpoints someone
remembered. The risk is the endpoint added next month whose author forgot the guard, and
a per-route loop over the live route table covers that automatically
(`SECURITY.md §14`, M3's risk note).

Two matrices:

* **Permissions** — every permissioned route, called by a role that lacks its
  permission, must be refused. UI-level hiding is not a control (NFR-SEC-004).
* **Isolation (AC-8)** — no cross-business read or write succeeds on any endpoint, and a
  cross-tenant id returns `404`, never `403`, so existence is never leaked.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from bm.api.deps import ANY_AUTHENTICATED, PERMISSION_MARKER
from bm.domain.auth.permissions import ROLE_PERMISSIONS, SystemRole
from tests.fixtures import Tenant, UserFixture, create_tenant, create_user

pytestmark = pytest.mark.db


def _route_permissions(app: FastAPI) -> list[tuple[str, str, str]]:
    """`(method, path, permission)` for every permissioned route."""
    found: list[tuple[str, str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for dependency in route.dependant.dependencies:
            call = dependency.call
            if call is None or not hasattr(call, PERMISSION_MARKER):
                continue
            declared = str(getattr(call, PERMISSION_MARKER))
            if declared == ANY_AUTHENTICATED:
                continue
            for method in sorted(route.methods or ()):
                if method in {"HEAD", "OPTIONS"}:
                    continue
                found.append((method, route.path, declared))
    return found


def _role_lacking(permission: str) -> SystemRole | None:
    """A system role that does **not** hold the permission, for the negative test."""
    needed = set(permission.split("|"))
    for role in SystemRole:
        held = {str(p) for p in ROLE_PERMISSIONS[role]}
        if not (needed & held):
            return role
    return None


def _concrete_path(path: str) -> str:
    """Substitute a random UUID for every path parameter.

    The id does not have to exist: the permission guard is a dependency and runs before
    the handler, so a refusal must happen regardless of whether the target is real. If
    an endpoint ever returned 404 here instead of 403, that would mean the guard ran too
    late — which is itself the bug worth catching.
    """
    result = path
    while "{" in result:
        start = result.index("{")
        end = result.index("}", start)
        result = result[:start] + str(uuid.uuid4()) + result[end + 1 :]
    return result


def _headers(client: TestClient, user: UserFixture) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": user.password,
            "device": {"id": str(user.device_id), "label": "PC"},
        },
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Device-Id": str(user.device_id)}


@pytest.fixture
def app(api_client: TestClient) -> FastAPI:
    return api_client.app  # type: ignore[return-value]


def test_the_matrix_is_not_empty(app: FastAPI) -> None:
    """A guard against the matrix silently covering nothing — which is how a generated
    test turns into a no-op after a refactor."""
    assert len(_route_permissions(app)) >= 5


def test_every_permissioned_route_refuses_a_role_without_it(
    api_client: TestClient, migration_session: Session, tenant: Tenant, app: FastAPI
) -> None:
    """NFR-SEC-004. Generated per route, so a new endpoint is covered automatically."""
    created: dict[SystemRole, UserFixture] = {}
    failures: list[str] = []

    for method, path, permission in _route_permissions(app):
        role = _role_lacking(permission)
        if role is None:
            continue  # every role holds it; nothing to assert negatively

        if role not in created:
            created[role] = create_user(
                migration_session,
                tenant,
                username=f"neg-{role}",
                role=role,
                all_branches=True,
            )
        user = created[role]

        response = api_client.request(
            method,
            _concrete_path(path),
            headers=_headers(api_client, user),
            json={} if method in {"POST", "PUT", "PATCH"} else None,
        )
        if response.status_code != 403:
            failures.append(
                f"{method} {path} as {role} (lacks {permission}) -> {response.status_code}"
            )

    assert failures == [], "Routes that did not refuse an unauthorised role:\n  " + "\n  ".join(
        failures
    )


def test_permission_denied_names_the_missing_permission(
    api_client: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    """The client explains the refusal and offers a supervisor login, so it needs the
    permission name rather than a bare 403."""
    cashier = create_user(migration_session, tenant, username="neg-cashier-detail")
    response = api_client.get("/api/v1/admin/users", headers=_headers(api_client, cashier))
    assert response.status_code == 403
    assert response.json()["error"]["details"]["required_permission"] == "admin.users.manage"


# --- AC-8: tenant isolation ---------------------------------------------------


def test_no_authenticated_route_leaks_another_business_s_data(
    api_client: TestClient, migration_session: Session, tenant: Tenant, app: FastAPI
) -> None:
    """AC-8. Every permissioned `GET` is called by an owner of business A while
    business B holds data; nothing belonging to B may appear.

    The check is on B's identifiers rather than on a count, because a missing tenant
    predicate shows up as a foreign id in the payload, not as an error.
    """
    owner_a = create_user(
        migration_session, tenant, username="owner-a", role=SystemRole.OWNER, all_branches=True
    )
    tenant_b = create_tenant(
        migration_session, display_name="OTHER SHOP", branch_code="OTH", counter_code="C9"
    )
    user_b = create_user(
        migration_session, tenant_b, username="owner-b", role=SystemRole.OWNER, all_branches=True
    )

    foreign_ids = {
        str(tenant_b.business_id),
        str(tenant_b.branch_id),
        str(tenant_b.counter_id),
        str(user_b.id),
        str(user_b.device_id),
    }

    headers = _headers(api_client, owner_a)
    leaks: list[str] = []

    for method, path, _permission in _route_permissions(app):
        if method != "GET" or "{" in path:
            continue
        response = api_client.get(path, headers=headers)
        if response.status_code != 200:
            continue
        body = response.text
        for foreign in foreign_ids:
            if foreign in body:
                leaks.append(f"GET {path} leaked {foreign}")

    assert leaks == [], "\n".join(leaks)


def test_cross_tenant_write_returns_404_not_403(
    api_client: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    """`§4.1`: a 403 would confirm the row exists in another tenant."""
    owner_a = create_user(
        migration_session,
        tenant,
        username="owner-a2",
        role=SystemRole.OWNER,
        all_branches=True,
    )
    tenant_b = create_tenant(
        migration_session, display_name="OTHER SHOP 2", branch_code="OT2", counter_code="C8"
    )
    user_b = create_user(migration_session, tenant_b, username="victim")

    headers = _headers(api_client, owner_a)

    renamed = api_client.put(
        f"/api/v1/admin/users/{user_b.id}", headers=headers, json={"full_name": "Hijacked"}
    )
    assert renamed.status_code == 404
    assert renamed.json()["error"]["code"] == "NOT_FOUND"

    revoked = api_client.post(
        f"/api/v1/admin/devices/{user_b.device_id}:revoke",
        headers=headers,
        json={"reason": "Attempting a cross-tenant revocation"},
    )
    assert revoked.status_code == 404

    # And the victim's data is untouched.
    from sqlalchemy import text

    name = migration_session.execute(
        text("SELECT full_name FROM users WHERE id = :id"), {"id": user_b.id}
    ).scalar_one()
    assert name == "Victim"


def test_a_token_from_one_business_cannot_act_in_another(
    api_client: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    """Scope comes from the token, never the request body (`§1.3`), so there is no
    field to tamper with — this asserts the absence is real."""
    tenant_b = create_tenant(
        migration_session, display_name="OTHER SHOP 3", branch_code="OT3", counter_code="C7"
    )
    owner_b = create_user(
        migration_session, tenant_b, username="owner-b3", role=SystemRole.OWNER, all_branches=True
    )

    usernames = {
        u["username"]
        for u in api_client.get("/api/v1/admin/users", headers=_headers(api_client, owner_b)).json()
    }
    assert usernames == {"owner-b3"}
