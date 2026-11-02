"""Opening the application without a password — and everything that still holds.

The owner runs the shop from one computer and asked for the application to open straight
into billing rather than onto a sign-in form. `POST /auth/unattended` is the whole of
that change on the server.

These tests are mostly about what did **not** change, because that is where the risk
lives. A reader should be able to check, from this file alone, that:

* the endpoint is **refused unless the server was configured to allow it** — so no
  deployment acquires password-free access by upgrading;
* the session it issues is an **ordinary** one: same shape, same permissions, same
  rotating refresh cookie, same device registration, same counter binding;
* every authorisation check **after** the session still applies — a request with no token
  is still refused, and the token's permissions are still the account's real ones;
* a **revoked device is still refused**, which is the only way to take access away from a
  machine that has left the shop;
* the audit trail still names the account, and says plainly that no password was given.

The one thing deliberately given up is stated in `unattended_session`'s docstring and in
the acceptance report: anyone who can reach this endpoint gets a session. That is a
network-boundary decision, not a code one.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.config import Settings
from bm.domain.auth.permissions import SystemRole
from bm.main import create_app
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db

UNATTENDED = "/api/v1/auth/unattended"


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def server(api_settings: Settings, api_client: TestClient) -> Any:
    """Build a client whose server has extra settings, on the already-wired engine.

    `api_client` is depended on rather than used directly: it is what initialises the
    engine and holds the schema at head for the duration of the test. Constructing an app
    without it gives "Session factory is not initialised", which is how this fixture
    earned its docstring.
    """

    def build(**overrides: Any) -> TestClient:
        settings = Settings(**{**api_settings.model_dump(), **overrides})
        return TestClient(
            create_app(settings), base_url="https://testserver", raise_server_exceptions=False
        )

    return build


@pytest.fixture
def enabled(server: Any, owner: UserFixture) -> TestClient:
    """A server configured the way the shop's own machine will be."""
    return server(unattended_access=True, unattended_username=owner.username)


def _ask(client: TestClient, device_id: uuid.UUID | None = None) -> Any:
    return client.post(
        UNATTENDED,
        json={"device": {"id": str(device_id or uuid.uuid4()), "label": "Shop PC"}},
    )


# --- it is off unless somebody turns it on -----------------------------------


def test_a_server_that_was_not_configured_for_it_refuses(
    api_client: TestClient, owner: UserFixture
) -> None:
    """The default, and the reason this cannot arrive by accident.

    `api_client` is the ordinary test server — the same settings every other suite uses,
    which is to say the shipped defaults. It must refuse.
    """
    response = _ask(api_client)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


def test_enabling_it_without_naming_an_account_is_refused_at_startup(
    api_settings: Settings,
) -> None:
    """A half-configured server does not boot.

    Refusing here is kinder than booting and failing every request: the session belongs to
    a real account, and there is no sensible default for whose it is.
    """
    with pytest.raises(ValueError, match="BM_UNATTENDED_USERNAME"):
        Settings(
            **{
                **api_settings.model_dump(),
                "unattended_access": True,
                "unattended_username": None,
            }
        )


# --- what it issues ----------------------------------------------------------


def test_it_returns_an_ordinary_session(enabled: TestClient, owner: UserFixture) -> None:
    """Identical in shape to a login, because it **is** a login minus the password."""
    response = _ask(enabled)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["user"]["id"] == str(owner.id)
    assert body["role"] == "owner"
    # The account's real permissions — not a widened set, and not an empty one.
    assert "billing.sale.finalize" in body["permissions"]
    assert "catalog.read" in body["permissions"]
    # The refresh token is a cookie, never a field in the body (NFR-SEC-003).
    assert "refresh_token" not in body
    assert "bm_refresh" in response.cookies


def test_the_session_it_issues_actually_works(enabled: TestClient) -> None:
    """End to end: ask for a session, then use it on a guarded endpoint."""
    token = _ask(enabled).json()["access_token"]

    bootstrap = enabled.get("/api/v1/sync/bootstrap", headers={"Authorization": f"Bearer {token}"})

    assert bootstrap.status_code == 200, bootstrap.text
    # The machine is ready to bill, which is the point of opening straight into billing.
    assert bootstrap.json()["counter"] is not None


def test_the_refresh_cookie_it_sets_can_be_rotated(enabled: TestClient, owner: UserFixture) -> None:
    """Rotation is unchanged, so a reload recovers a session the same way it always did."""
    device_id = uuid.uuid4()
    first = _ask(enabled, device_id)
    assert first.status_code == 200

    refreshed = enabled.post("/api/v1/auth/refresh", headers={"X-Device-Id": str(device_id)})

    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["user"]["id"] == str(owner.id)


# --- everything downstream still applies -------------------------------------


def test_a_request_with_no_token_is_still_refused(enabled: TestClient) -> None:
    """The endpoint hands out sessions; it does not make the API public.

    This is the assertion that matters most. Enabling password-free *entry* must not
    make the rest of the application unauthenticated.
    """
    assert enabled.get("/api/v1/sync/bootstrap").status_code == 401
    assert enabled.get("/api/v1/inventory/balances").status_code == 401
    assert enabled.get("/api/v1/customers").status_code == 401


def test_a_garbage_token_is_still_refused(enabled: TestClient) -> None:
    response = enabled.get(
        "/api/v1/sync/bootstrap", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_the_token_carries_the_accounts_real_permissions(
    server: Any, enabled: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    """A cashier configured for unattended access gets a cashier's session, not an owner's.

    The account decides what the session can do, exactly as it does for a password login —
    so an operator who wants a restricted kiosk can have one by naming a restricted
    account.
    """
    cashier = create_user(migration_session, tenant, username="till", role=SystemRole.CASHIER)
    migration_session.commit()
    client = server(unattended_access=True, unattended_username=cashier.username)

    body = _ask(client).json()

    assert body["role"] == "cashier"
    # A cashier cannot change business settings, and this session cannot either.
    assert "admin.settings.manage" not in body["permissions"]


# --- the controls that still take access away --------------------------------


def test_a_revoked_device_is_still_refused(
    enabled: TestClient, migration_session: Session, owner: UserFixture
) -> None:
    """The control that matters once a machine has left the shop.

    Without a password, revoking the device is the **only** way to stop a particular
    browser getting back in. So it is checked here exactly as it is on a login.
    """
    device_id = uuid.uuid4()
    assert _ask(enabled, device_id).status_code == 200

    migration_session.execute(
        text("UPDATE devices SET status = 'revoked', revoked_at = now() WHERE id = :id"),
        {"id": device_id},
    )
    migration_session.commit()

    refused = _ask(enabled, device_id)

    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "DEVICE_REVOKED"


def test_an_inactive_account_cannot_open_a_session(
    server: Any, migration_session: Session, tenant: Tenant
) -> None:
    disabled = create_user(migration_session, tenant, username="retired", role=SystemRole.OWNER)
    migration_session.execute(
        text("UPDATE users SET is_active = false WHERE id = :id"), {"id": disabled.id}
    )
    migration_session.commit()
    client = server(unattended_access=True, unattended_username=disabled.username)

    response = _ask(client)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "USER_INACTIVE"


def test_an_account_that_does_not_exist_is_refused(server: Any, owner: UserFixture) -> None:
    client = server(unattended_access=True, unattended_username="nobody-at-all")
    assert _ask(client).status_code == 403


# --- the trail ---------------------------------------------------------------


def test_the_audit_row_names_the_account_and_says_no_password_was_given(
    enabled: TestClient, migration_session: Session, owner: UserFixture
) -> None:
    """ "Who did this" has the same answer it always had, and the trail does not pretend
    somebody typed a password."""
    assert _ask(enabled).status_code == 200

    row = migration_session.execute(
        text(
            "SELECT actor_user_id, reason, after_value FROM audit_logs"
            " WHERE action = 'user.logged_in' ORDER BY created_at DESC LIMIT 1"
        )
    ).one()

    assert row.actor_user_id == owner.id
    assert row.reason == "unattended access — no password was presented"
    assert row.after_value["role"] == "owner"
    # The token itself is never audited (FR-AUDIT-004) — only a correlation id.
    assert "access_token" not in str(row.after_value)


def test_an_ordinary_login_still_records_no_reason(
    api_client: TestClient, migration_session: Session, owner: UserFixture
) -> None:
    """The existing audit rows are unchanged, so the new reason is a signal rather than
    noise."""
    response = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": owner.username,
            "password": owner.password,
            "device": {"id": str(owner.device_id), "label": "Shop PC"},
        },
    )
    assert response.status_code == 200, response.text

    reason = migration_session.execute(
        text(
            "SELECT reason FROM audit_logs WHERE action = 'user.logged_in'"
            " ORDER BY created_at DESC LIMIT 1"
        )
    ).scalar_one()

    assert reason is None


def test_signing_in_with_a_password_still_works_when_unattended_is_enabled(
    enabled: TestClient, owner: UserFixture
) -> None:
    """The form is not removed, only bypassed. An operator can still sign in as somebody
    else — which is how a second person uses the machine without changing the config."""
    response = enabled.post(
        "/api/v1/auth/login",
        json={
            "username": owner.username,
            "password": owner.password,
            "device": {"id": str(owner.device_id), "label": "Shop PC"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == str(owner.id)


def test_a_wrong_password_is_still_wrong_when_unattended_is_enabled(
    enabled: TestClient, owner: UserFixture
) -> None:
    """Enabling the bypass must not turn the password check into a formality."""
    response = enabled.post(
        "/api/v1/auth/login",
        json={
            "username": owner.username,
            "password": "not-the-password",
            "device": {"id": str(owner.device_id), "label": "Shop PC"},
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
