"""`catalog.create` — the narrow create-only catalogue permission (owner Decision B).

The owner approved letting a cashier add a part that is missing at the billing counter,
and **did not** approve giving them the catalogue generally. So this is its own
permission rather than a widening of `catalog.write`, and the tests that matter are the
negative ones: a cashier who can create must still be refused an edit and a withdrawal.

`POST /products` accepts **either** permission. That is not laziness — a custom role
granted only the broad `catalog.write` would otherwise lose the ability to create when
this landed, which would be a silent regression for a deployment nobody has looked at.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import ROLE_PERMISSIONS, Permission, SystemRole
from tests.fixtures import Tenant, UserFixture, create_user

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


def _create(
    client: TestClient, headers: dict[str, str], name: str = "Cashier Added Part"
) -> object:
    return client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": name,
            "unit_code": "NOS",
            "default_selling_price": "450.0000",
            "reorder_level": "10",
        },
    )


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="perm-owner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def units_ready(api_client: TestClient, owner: UserFixture) -> dict[str, str]:
    """Units are reference data a product create resolves against."""
    headers = _headers(api_client, owner)
    assert api_client.post(
        "/api/v1/units:ensure-defaults", headers=headers, json={}
    ).status_code in (
        200,
        201,
    )
    return headers


# --- the role matrix ----------------------------------------------------------


def test_the_cashier_role_holds_create_but_not_write() -> None:
    """The whole point of Decision B, asserted on the matrix itself."""
    cashier = ROLE_PERMISSIONS[SystemRole.CASHIER]
    assert Permission.CATALOG_CREATE in cashier
    assert Permission.CATALOG_WRITE not in cashier


@pytest.mark.parametrize("role", [SystemRole.OWNER, SystemRole.MANAGER, SystemRole.STOCK_KEEPER])
def test_roles_that_could_already_create_still_can(role: SystemRole) -> None:
    """No existing role loses an ability it had before this permission existed."""
    assert Permission.CATALOG_CREATE in ROLE_PERMISSIONS[role]


@pytest.mark.parametrize("role", [SystemRole.ACCOUNTANT, SystemRole.AUDITOR])
def test_read_only_roles_gain_nothing(role: SystemRole) -> None:
    permissions = ROLE_PERMISSIONS[role]
    assert Permission.CATALOG_CREATE not in permissions
    assert Permission.CATALOG_WRITE not in permissions


# --- the cashier at the counter -----------------------------------------------


def test_a_cashier_can_create_a_missing_part(
    api_client: TestClient,
    migration_session: Session,
    tenant: Tenant,
    units_ready: dict[str, str],
) -> None:
    cashier = create_user(
        migration_session, tenant, username="perm-cashier", role=SystemRole.CASHIER
    )
    response = _create(api_client, _headers(api_client, cashier))
    assert response.status_code == 201, response.text


def test_a_cashier_still_cannot_edit_a_product(
    api_client: TestClient,
    migration_session: Session,
    tenant: Tenant,
    units_ready: dict[str, str],
) -> None:
    """The line Decision B draws: create, not edit."""
    created = _create(api_client, units_ready, name="Owner Added Part").json()
    cashier = create_user(
        migration_session, tenant, username="perm-cashier-2", role=SystemRole.CASHIER
    )

    response = api_client.patch(
        f"/api/v1/products/{created['id']}",
        headers=_headers(api_client, cashier),
        json={"default_selling_price": "1.0000"},
    )
    assert response.status_code == 403, response.text


def test_a_cashier_still_cannot_withdraw_a_product(
    api_client: TestClient,
    migration_session: Session,
    tenant: Tenant,
    units_ready: dict[str, str],
) -> None:
    created = _create(api_client, units_ready, name="Owner Added Part 2").json()
    cashier = create_user(
        migration_session, tenant, username="perm-cashier-3", role=SystemRole.CASHIER
    )

    response = api_client.post(
        f"/api/v1/products/{created['id']}:deactivate",
        headers=_headers(api_client, cashier),
    )
    assert response.status_code == 403, response.text


def test_a_role_with_neither_permission_cannot_create(
    api_client: TestClient,
    migration_session: Session,
    tenant: Tenant,
    units_ready: dict[str, str],
) -> None:
    accountant = create_user(
        migration_session, tenant, username="perm-accountant", role=SystemRole.ACCOUNTANT
    )
    response = _create(api_client, _headers(api_client, accountant))
    assert response.status_code == 403, response.text


def test_a_custom_role_with_only_catalog_write_can_still_create(
    api_client: TestClient,
    migration_session: Session,
    tenant: Tenant,
    units_ready: dict[str, str],
) -> None:
    """The regression this guards against.

    Had `POST /products` been narrowed to `catalog.create` alone, a deployment with a
    custom role holding only `catalog.write` would have silently lost the ability to add
    a product — a permission taken away by an upgrade nobody asked for.
    """
    role_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO roles (id, business_id, code, name, is_system)"
            " VALUES (:id, :business_id, 'catalogue_only', 'Catalogue only', false)"
        ),
        {"id": role_id, "business_id": tenant.business_id},
    )
    for permission in (Permission.CATALOG_READ, Permission.CATALOG_WRITE):
        migration_session.execute(
            text(
                "INSERT INTO role_permissions (role_id, permission_code) VALUES (:role_id, :code)"
            ),
            {"role_id": role_id, "code": str(permission)},
        )
    migration_session.commit()

    user = create_user(migration_session, tenant, username="perm-custom", role_id=role_id)
    response = _create(api_client, _headers(api_client, user), name="Custom Role Part")
    assert response.status_code == 201, response.text


def test_creating_a_product_is_audited(
    api_client: TestClient,
    migration_session: Session,
    tenant: Tenant,
    units_ready: dict[str, str],
) -> None:
    cashier = create_user(
        migration_session, tenant, username="perm-cashier-4", role=SystemRole.CASHIER
    )
    created = _create(api_client, _headers(api_client, cashier), name="Audited Part").json()

    action = migration_session.execute(
        text("SELECT action FROM audit_logs WHERE entity_type = 'product'   AND entity_id = :id"),
        {"id": created["id"]},
    ).scalar_one()
    assert action == "product.created"
