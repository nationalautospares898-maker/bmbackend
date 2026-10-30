"""M3 — authentication flows against a real database.

Every test here corresponds to a named behaviour in `SECURITY.md §2` or FR-AUTH-*. The
ones that matter most are rotation with reuse detection and lockout, because those are
the controls that make a copied cookie and a credential-stuffing run survivable.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import ROLE_PERMISSIONS, SystemRole
from tests.fixtures import TEST_PASSWORD, Tenant, UserFixture, create_user

pytestmark = pytest.mark.db

REFRESH_COOKIE = "bm_refresh"


@pytest.fixture
def cashier(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(migration_session, tenant, username="ravi", role=SystemRole.CASHIER)


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


def _login(client: TestClient, user: UserFixture, *, password: str | None = None) -> object:
    return client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": password or user.password,
            "device": {"id": str(user.device_id), "label": "Counter 1 PC"},
        },
    )


# --- login -------------------------------------------------------------------


def test_login_returns_a_session_and_sets_the_refresh_cookie(
    api_client: TestClient, cashier: UserFixture
) -> None:
    response = _login(api_client, cashier)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["user"]["full_name"] == "Ravi"
    assert body["role"] == "cashier"
    assert body["active_branch_id"]
    assert body["server_time"]

    # The refresh token is never in the body (NFR-SEC-003).
    assert "refresh_token" not in body
    assert REFRESH_COOKIE in response.cookies


def test_refresh_cookie_is_httponly_and_narrowly_scoped(
    api_client: TestClient, cashier: UserFixture
) -> None:
    """`SameSite=Lax` plus the narrow path is the CSRF control: every other endpoint
    uses a bearer header, so a cross-site post cannot act as the user."""
    response = _login(api_client, cashier)
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/api/v1/auth" in set_cookie
    # `Secure` is set outside local development, so the cookie never crosses plain HTTP.
    assert "secure" in set_cookie


def test_login_grants_exactly_the_role_s_permissions(
    api_client: TestClient, cashier: UserFixture
) -> None:
    """Permissions come from `role_permissions` rows, which migration 0005 seeds from
    the matrix in `PRD.md §3.2`."""
    body = _login(api_client, cashier).json()
    assert set(body["permissions"]) == {str(p) for p in ROLE_PERMISSIONS[SystemRole.CASHIER]}


def test_cashier_does_not_receive_owner_permissions(
    api_client: TestClient, cashier: UserFixture
) -> None:
    granted = set(_login(api_client, cashier).json()["permissions"])
    for denied in (
        "admin.users.manage",
        "admin.settings.manage",
        "credit.writeoff",
        "billing.sale.cancel",
        "inventory.oversell.override",
        "catalog.cost.read",
    ):
        assert denied not in granted


def test_auditor_holds_no_write_permission(
    api_client: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    """`PRD.md §3.2`: Auditor is read-only at the API level."""
    auditor = create_user(migration_session, tenant, username="auditor", role=SystemRole.AUDITOR)
    granted = set(_login(api_client, auditor).json()["permissions"])
    write_shaped = [
        p
        for p in granted
        if any(
            verb in p
            for verb in (
                ".write",
                ".manage",
                ".finalize",
                ".adjust",
                ".create",
                ".record",
                ".override",
                ".cancel",
                ".receive",
                ".takeover",
                ".writeoff",
                ".apply",
            )
        )
    ]
    assert write_shaped == [], f"Auditor must not hold write permissions: {write_shaped}"


def test_wrong_password_is_invalid_credentials(
    api_client: TestClient, cashier: UserFixture
) -> None:
    response = _login(api_client, cashier, password="not-the-password")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_unknown_username_is_indistinguishable_from_a_wrong_password(
    api_client: TestClient, cashier: UserFixture
) -> None:
    """A different code or status for an unknown user would be a user-enumeration
    oracle."""
    unknown = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": "nobody-here",
            "password": TEST_PASSWORD,
            "device": {"id": str(uuid.uuid4()), "label": "PC"},
        },
    )
    wrong = _login(api_client, cashier, password="wrong-password-value")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["code"] == wrong.json()["error"]["code"]
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


def test_inactive_user_cannot_log_in(
    api_client: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    user = create_user(migration_session, tenant, username="former-staff", is_active=False)
    response = _login(api_client, user)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "USER_INACTIVE"


def test_user_without_a_membership_cannot_obtain_scope(
    api_client: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    """Authenticating with no role is an administrative state, not a credential
    problem — but it must not yield a usable session."""
    user = create_user(migration_session, tenant, username="unassigned")
    migration_session.execute(text("DELETE FROM memberships WHERE user_id = :id"), {"id": user.id})
    migration_session.commit()
    response = _login(api_client, user)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "USER_INACTIVE"


def test_login_on_a_revoked_device_is_refused(
    api_client: TestClient, migration_session: Session, cashier: UserFixture
) -> None:
    migration_session.execute(
        text("UPDATE devices SET status = 'revoked', revoked_at = now() WHERE id = :id"),
        {"id": cashier.device_id},
    )
    migration_session.commit()
    response = _login(api_client, cashier)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DEVICE_REVOKED"


def test_logging_in_again_does_not_reactivate_a_revoked_device(
    api_client: TestClient, migration_session: Session, cashier: UserFixture
) -> None:
    """Reactivation must be an explicit administrative act, never a side effect of a
    login attempt — otherwise revoking a stolen machine achieves nothing."""
    migration_session.execute(
        text("UPDATE devices SET status = 'revoked', revoked_at = now() WHERE id = :id"),
        {"id": cashier.device_id},
    )
    migration_session.commit()

    _login(api_client, cashier)

    status_after = migration_session.execute(
        text("SELECT status FROM devices WHERE id = :id"), {"id": cashier.device_id}
    ).scalar_one()
    assert status_after == "revoked"


def test_login_registers_an_unknown_device_on_first_use(
    api_client: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    user = create_user(migration_session, tenant, username="newpc", register_device=False)
    response = _login(api_client, user)
    assert response.status_code == 200

    label = migration_session.execute(
        text("SELECT label FROM devices WHERE id = :id"), {"id": user.device_id}
    ).scalar_one()
    assert label == "Counter 1 PC"


def test_login_writes_an_audit_row(
    api_client: TestClient, migration_session: Session, cashier: UserFixture
) -> None:
    _login(api_client, cashier)
    actions = (
        migration_session.execute(
            text("SELECT action FROM audit_logs WHERE actor_user_id = :id"),
            {"id": cashier.id},
        )
        .scalars()
        .all()
    )
    assert "user.logged_in" in actions


def test_login_attempts_store_a_hashed_ip_never_a_raw_one(
    api_client: TestClient, migration_session: Session, cashier: UserFixture
) -> None:
    """`SECURITY.md §2.4`. Logs must be useful for rate limiting without retaining
    addresses."""
    _login(api_client, cashier)
    ip_hash = migration_session.execute(
        text(
            "SELECT ip_hash FROM login_attempts"
            " WHERE username_attempted = :u ORDER BY created_at DESC LIMIT 1"
        ),
        {"u": cashier.username},
    ).scalar_one()
    assert ip_hash is not None
    assert len(ip_hash) == 64  # sha256 hex
    assert "." not in ip_hash and ":" not in ip_hash


# --- lockout -----------------------------------------------------------------


def test_ten_failures_lock_the_account_with_a_countdown(
    api_client: TestClient, cashier: UserFixture
) -> None:
    """`SECURITY.md §2.4`: 10 failures / 5 min, then `423` with
    `retry_after_seconds` so the client can count down instead of guessing."""
    for _ in range(10):
        assert _login(api_client, cashier, password="wrong").status_code == 401

    locked = _login(api_client, cashier, password="wrong")
    assert locked.status_code == 423
    body = locked.json()
    assert body["error"]["code"] == "ACCOUNT_LOCKED"
    assert body["error"]["details"]["retry_after_seconds"] == 900


def test_lockout_applies_even_with_the_correct_password(
    api_client: TestClient, cashier: UserFixture
) -> None:
    """Otherwise an attacker who guesses on attempt 11 is rewarded for persisting."""
    for _ in range(10):
        _login(api_client, cashier, password="wrong")
    assert _login(api_client, cashier).status_code == 423


def test_a_successful_login_clears_the_failure_window(
    api_client: TestClient, cashier: UserFixture
) -> None:
    """Nine failures then a success is a forgotten password, not an attack: the next
    wrong attempt must not immediately lock the account."""
    for _ in range(9):
        _login(api_client, cashier, password="wrong")
    assert _login(api_client, cashier).status_code == 200

    assert _login(api_client, cashier, password="wrong").status_code == 401


# --- refresh rotation --------------------------------------------------------


def test_refresh_rotates_the_token_and_returns_a_new_session(
    api_client: TestClient, cashier: UserFixture
) -> None:
    first = _login(api_client, cashier)
    original = first.cookies[REFRESH_COOKIE]

    response = api_client.post(
        "/api/v1/auth/refresh", headers={"X-Device-Id": str(cashier.device_id)}
    )
    assert response.status_code == 200, response.text
    assert response.cookies[REFRESH_COOKIE] != original
    assert response.json()["access_token"]


def test_reusing_a_consumed_refresh_token_revokes_the_whole_family(
    api_client: TestClient, migration_session: Session, cashier: UserFixture
) -> None:
    """FR-AUTH-002. A consumed token presented again means the cookie was copied, so
    every token in the family goes — the attacker and the real user are both signed
    out, which is the safe outcome."""
    _login(api_client, cashier)
    stolen = api_client.cookies[REFRESH_COOKIE]

    rotated = api_client.post(
        "/api/v1/auth/refresh", headers={"X-Device-Id": str(cashier.device_id)}
    )
    assert rotated.status_code == 200

    # Present the old (now consumed) token again.
    api_client.cookies.set(REFRESH_COOKIE, stolen, path="/api/v1/auth")
    replayed = api_client.post(
        "/api/v1/auth/refresh", headers={"X-Device-Id": str(cashier.device_id)}
    )
    assert replayed.status_code == 401
    assert replayed.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"

    live = migration_session.execute(
        text(
            "SELECT count(*) FROM refresh_tokens"
            " WHERE user_id = :id AND revoked_at IS NULL AND consumed_at IS NULL"
        ),
        {"id": cashier.id},
    ).scalar_one()
    assert live == 0, "the whole family must be revoked, not just the replayed token"

    audited = (
        migration_session.execute(
            text("SELECT action FROM audit_logs WHERE action = 'refresh_token.reused'")
        )
        .scalars()
        .all()
    )
    assert audited, "reuse detection must be audited"


def test_refresh_requires_the_bound_device(api_client: TestClient, cashier: UserFixture) -> None:
    """The cookie is device-bound; a mismatch means it was moved to another machine."""
    _login(api_client, cashier)
    response = api_client.post("/api/v1/auth/refresh", headers={"X-Device-Id": str(uuid.uuid4())})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"


def test_refresh_without_a_cookie_is_refused(api_client: TestClient) -> None:
    response = api_client.post("/api/v1/auth/refresh", headers={"X-Device-Id": str(uuid.uuid4())})
    assert response.status_code == 401


def test_refresh_after_device_revocation_is_refused(
    api_client: TestClient, migration_session: Session, cashier: UserFixture
) -> None:
    """Revocation stops refresh immediately; the ≤15 minute access-token window is the
    documented and accepted bound (`SECURITY.md §15`)."""
    _login(api_client, cashier)
    migration_session.execute(
        text("UPDATE devices SET status = 'revoked', revoked_at = now() WHERE id = :id"),
        {"id": cashier.device_id},
    )
    migration_session.commit()

    response = api_client.post(
        "/api/v1/auth/refresh", headers={"X-Device-Id": str(cashier.device_id)}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DEVICE_REVOKED"


# --- logout ------------------------------------------------------------------


def test_logout_revokes_the_family_and_clears_the_cookie(
    api_client: TestClient, migration_session: Session, cashier: UserFixture
) -> None:
    _login(api_client, cashier)
    response = api_client.post("/api/v1/auth/logout")
    assert response.status_code == 204

    live = migration_session.execute(
        text("SELECT count(*) FROM refresh_tokens WHERE user_id = :id AND revoked_at IS NULL"),
        {"id": cashier.id},
    ).scalar_one()
    assert live == 0


def test_logout_without_a_session_is_not_an_error(api_client: TestClient) -> None:
    """The caller wanted to be signed out, and they are."""
    assert api_client.post("/api/v1/auth/logout").status_code == 204


# --- authenticated access ----------------------------------------------------


def _auth_headers(client: TestClient, user: UserFixture) -> dict[str, str]:
    token = _login(client, user).json()["access_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Device-Id": str(user.device_id),
    }


def test_me_returns_the_caller_s_own_context(api_client: TestClient, cashier: UserFixture) -> None:
    response = api_client.get("/api/v1/auth/me", headers=_auth_headers(api_client, cashier))
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == str(cashier.id)
    assert body["role"] == "cashier"
    # Never `TAX INVOICE` until the FR-TAX-003 gate passes.
    assert body["tax_mode"] == "estimate_only"


def test_missing_token_is_refused(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_garbage_token_is_refused(api_client: TestClient, cashier: UserFixture) -> None:
    response = api_client.get(
        "/api/v1/auth/me",
        headers={
            "Authorization": "Bearer not-a-jwt",
            "X-Device-Id": str(cashier.device_id),
        },
    )
    assert response.status_code == 401


def test_token_signed_with_another_key_is_refused(
    api_client: TestClient, cashier: UserFixture
) -> None:
    import jwt

    forged = jwt.encode(
        {
            "iss": "bm",
            "sub": str(cashier.id),
            "jti": str(uuid.uuid4()),
            "iat": 1,
            "exp": 9_999_999_999,
            "business_id": str(cashier.business_id),
            "permissions": ["admin.users.manage"],
        },
        "an-attacker-chosen-key",
        algorithm="HS256",
    )
    response = api_client.get(
        "/api/v1/auth/me",
        headers={
            "Authorization": f"Bearer {forged}",
            "X-Device-Id": str(cashier.device_id),
        },
    )
    assert response.status_code == 401


def test_revoked_device_blocks_every_authenticated_endpoint(
    api_client: TestClient, migration_session: Session, cashier: UserFixture
) -> None:
    """FR-OFFLINE-010: refused everywhere, including sync. Checked on every request
    rather than only at login."""
    headers = _auth_headers(api_client, cashier)
    migration_session.execute(
        text("UPDATE devices SET status = 'revoked', revoked_at = now() WHERE id = :id"),
        {"id": cashier.device_id},
    )
    migration_session.commit()

    response = api_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DEVICE_REVOKED"


def test_unregistered_device_header_is_refused(
    api_client: TestClient, cashier: UserFixture
) -> None:
    headers = _auth_headers(api_client, cashier)
    headers["X-Device-Id"] = str(uuid.uuid4())
    response = api_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DEVICE_NOT_REGISTERED"


def test_malformed_device_header_is_a_validation_error(
    api_client: TestClient, cashier: UserFixture
) -> None:
    headers = _auth_headers(api_client, cashier)
    headers["X-Device-Id"] = "not-a-uuid"
    response = api_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"]["X-Device-Id"]


# --- must_change_password ----------------------------------------------------


def test_must_change_password_blocks_every_permissioned_endpoint(
    api_client: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    """T-5. A seeded credential that is never rotated is the most likely breach path,
    so the API refuses real work until it is changed."""
    user = create_user(
        migration_session,
        tenant,
        username="newhire",
        role=SystemRole.OWNER,
        all_branches=True,
        must_change_password=True,
    )
    headers = _auth_headers(api_client, user)

    blocked = api_client.get("/api/v1/admin/users", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "PERMISSION_DENIED"


def test_must_change_password_still_allows_changing_it(
    api_client: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    user = create_user(migration_session, tenant, username="newhire2", must_change_password=True)
    headers = _auth_headers(api_client, user)

    changed = api_client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": user.password, "new_password": "a-better-passphrase"},
    )
    assert changed.status_code == 204, changed.text

    flag = migration_session.execute(
        text("SELECT must_change_password FROM users WHERE id = :id"), {"id": user.id}
    ).scalar_one()
    assert flag is False


def test_weak_new_password_is_refused_with_a_usable_message(
    api_client: TestClient, cashier: UserFixture
) -> None:
    headers = _auth_headers(api_client, cashier)
    response = api_client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": cashier.password, "new_password": "short"},
    )
    assert response.status_code == 422
    message = response.json()["error"]["details"]["fields"]["new_password"]
    assert "10 characters" in message


def test_blocklisted_password_is_refused(api_client: TestClient, cashier: UserFixture) -> None:
    headers = _auth_headers(api_client, cashier)
    response = api_client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": cashier.password, "new_password": "password1234"},
    )
    assert response.status_code == 422


def test_changing_password_with_the_wrong_current_one_is_refused(
    api_client: TestClient, cashier: UserFixture
) -> None:
    headers = _auth_headers(api_client, cashier)
    response = api_client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": "not-it", "new_password": "a-fine-new-passphrase"},
    )
    assert response.status_code == 401


# --- branch selection --------------------------------------------------------


def test_owner_can_select_a_branch_they_belong_to(
    api_client: TestClient, owner: UserFixture, tenant: Tenant
) -> None:
    headers = _auth_headers(api_client, owner)
    response = api_client.post(
        "/api/v1/auth/select-branch",
        headers=headers,
        json={"branch_id": str(tenant.branch_id)},
    )
    assert response.status_code == 200
    assert response.json()["active_branch_id"] == str(tenant.branch_id)
    assert response.json()["access_token"]


def test_selecting_a_branch_outside_the_membership_is_refused(
    api_client: TestClient, migration_session: Session, cashier: UserFixture, tenant: Tenant
) -> None:
    other_branch = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO branches (id, business_id, code, name)"
            " VALUES (:id, :business_id, 'SECOND', 'Second Shop')"
        ),
        {"id": other_branch, "business_id": tenant.business_id},
    )
    migration_session.commit()

    headers = _auth_headers(api_client, cashier)
    response = api_client.post(
        "/api/v1/auth/select-branch",
        headers=headers,
        json={"branch_id": str(other_branch)},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BRANCH_SCOPE_INVALID"


# --- permissions and isolation -----------------------------------------------


def test_cashier_cannot_reach_an_owner_only_endpoint(
    api_client: TestClient, cashier: UserFixture
) -> None:
    """UI-level hiding is not a control (NFR-SEC-004): the API refuses the call even
    when the UI would never offer it."""
    response = api_client.get("/api/v1/admin/users", headers=_auth_headers(api_client, cashier))
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "PERMISSION_DENIED"
    assert body["error"]["details"]["required_permission"] == "admin.users.manage"


def test_owner_can_list_users(api_client: TestClient, owner: UserFixture) -> None:
    response = api_client.get("/api/v1/admin/users", headers=_auth_headers(api_client, owner))
    assert response.status_code == 200
    assert any(u["username"] == "owner" for u in response.json())


def test_cross_tenant_user_lookup_returns_404_not_403(
    api_client: TestClient, migration_session: Session, owner: UserFixture
) -> None:
    """`§4.1`: a 403 would confirm the row exists."""
    from tests.fixtures import create_tenant

    other = create_tenant(migration_session, display_name="OTHER SHOP", branch_code="OTH")
    stranger = create_user(migration_session, other, username="stranger")

    response = api_client.put(
        f"/api/v1/admin/users/{stranger.id}",
        headers=_auth_headers(api_client, owner),
        json={"full_name": "Renamed By Outsider"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_user_list_never_crosses_businesses(
    api_client: TestClient, migration_session: Session, owner: UserFixture
) -> None:
    """AC-8. The list is the easiest place for a missing tenant predicate to show up."""
    from tests.fixtures import create_tenant

    other = create_tenant(migration_session, display_name="OTHER SHOP", branch_code="OTH")
    create_user(migration_session, other, username="stranger")

    usernames = {
        u["username"]
        for u in api_client.get(
            "/api/v1/admin/users", headers=_auth_headers(api_client, owner)
        ).json()
    }
    assert "stranger" not in usernames


def test_owner_cannot_deactivate_their_own_account(
    api_client: TestClient, owner: UserFixture
) -> None:
    """T-15. Break-glass exists for genuine lockouts, not for an accidental click."""
    response = api_client.put(
        f"/api/v1/admin/users/{owner.id}",
        headers=_auth_headers(api_client, owner),
        json={"is_active": False},
    )
    assert response.status_code == 422
    assert "own account" in response.json()["error"]["details"]["fields"]["is_active"]


# --- administrative reset ----------------------------------------------------


def test_admin_reset_forces_a_change_and_revokes_sessions(
    api_client: TestClient, migration_session: Session, owner: UserFixture, cashier: UserFixture
) -> None:
    """The owner knows the temporary password, so it is not yet a credential the staff
    member owns."""
    _login(api_client, cashier)  # give the cashier a live session to revoke

    response = api_client.post(
        f"/api/v1/admin/users/{cashier.id}:reset-password",
        headers=_auth_headers(api_client, owner),
        json={"new_password": "temporary-pass-01"},
    )
    assert response.status_code == 204, response.text

    row = migration_session.execute(
        text("SELECT must_change_password FROM users WHERE id = :id"), {"id": cashier.id}
    ).scalar_one()
    assert row is True

    live = migration_session.execute(
        text("SELECT count(*) FROM refresh_tokens WHERE user_id = :id AND revoked_at IS NULL"),
        {"id": cashier.id},
    ).scalar_one()
    assert live == 0


def test_cashier_cannot_reset_another_user_s_password(
    api_client: TestClient, cashier: UserFixture, owner: UserFixture
) -> None:
    response = api_client.post(
        f"/api/v1/admin/users/{owner.id}:reset-password",
        headers=_auth_headers(api_client, cashier),
        json={"new_password": "temporary-pass-01"},
    )
    assert response.status_code == 403


# --- device revocation -------------------------------------------------------


def test_owner_can_revoke_a_device_and_it_is_audited(
    api_client: TestClient, migration_session: Session, owner: UserFixture, cashier: UserFixture
) -> None:
    response = api_client.post(
        f"/api/v1/admin/devices/{cashier.device_id}:revoke",
        headers=_auth_headers(api_client, owner),
        json={"reason": "Counter PC was stolen from the shop"},
    )
    assert response.status_code == 204, response.text

    status_value = migration_session.execute(
        text("SELECT status FROM devices WHERE id = :id"), {"id": cashier.device_id}
    ).scalar_one()
    assert status_value == "revoked"

    audited = (
        migration_session.execute(
            text(
                "SELECT reason FROM audit_logs WHERE action = 'device.revoked' AND entity_id = :id"
            ),
            {"id": cashier.device_id},
        )
        .scalars()
        .all()
    )
    assert audited == ["Counter PC was stolen from the shop"]


def test_revoking_a_device_requires_a_reason(
    api_client: TestClient, owner: UserFixture, cashier: UserFixture
) -> None:
    response = api_client.post(
        f"/api/v1/admin/devices/{cashier.device_id}:revoke",
        headers=_auth_headers(api_client, owner),
        json={"reason": "x"},
    )
    assert response.status_code == 422


def test_revoking_an_unknown_device_is_404(api_client: TestClient, owner: UserFixture) -> None:
    response = api_client.post(
        f"/api/v1/admin/devices/{uuid.uuid4()}:revoke",
        headers=_auth_headers(api_client, owner),
        json={"reason": "Testing an unknown device id"},
    )
    assert response.status_code == 404


def test_revoking_twice_is_idempotent_not_an_error(
    api_client: TestClient, owner: UserFixture, cashier: UserFixture
) -> None:
    headers = _auth_headers(api_client, owner)
    body = {"reason": "Replaced the counter machine"}
    first = api_client.post(
        f"/api/v1/admin/devices/{cashier.device_id}:revoke", headers=headers, json=body
    )
    second = api_client.post(
        f"/api/v1/admin/devices/{cashier.device_id}:revoke", headers=headers, json=body
    )
    assert first.status_code == 204
    assert second.status_code == 204


# --- input validation --------------------------------------------------------


def test_unknown_request_field_is_rejected_not_ignored(
    api_client: TestClient, cashier: UserFixture
) -> None:
    """`extra="forbid"` (`SECURITY.md §4`). A silently dropped field is how a discount
    disappears without anyone noticing."""
    response = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": cashier.username,
            "password": cashier.password,
            "device": {"id": str(cashier.device_id), "label": "PC"},
            "grant_me_admin": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_validation_errors_are_reported_per_field(api_client: TestClient) -> None:
    """The client maps `details.fields` straight back onto the form."""
    response = api_client.post("/api/v1/auth/login", json={"username": "only-this"})
    assert response.status_code == 422
    fields = response.json()["error"]["details"]["fields"]
    assert "password" in fields


def test_error_responses_never_leak_internals(api_client: TestClient, cashier: UserFixture) -> None:
    """`§4`: `message` never contains SQL, stack traces, file paths or internal ids."""
    response = _login(api_client, cashier, password="wrong")
    message = response.json()["error"]["message"]
    for leak in ("Traceback", "SELECT", "psycopg", "sqlalchemy", "/src/", "argon2"):
        assert leak.lower() not in message.lower()
