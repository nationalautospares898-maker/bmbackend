"""`GET /customers` — the customer directory, and why it had to exist.

Found in a real browser, not by this suite: a customer created through
`Add a customer` did not appear in the Customers list, and the only customer visible was
one that happened to owe money.

The cause was that there was **no way to list customers at all**. `GET /customers/search`
requires a term, and the only other read-many is `GET /credit/customers`, which
inner-joins credit invoices — it answers "who owes us money" and is, by the
specification's own words, "the top level of the Credit Bills view". The Customers screen
was reading from it. So a customer with no credit invoice was invisible there, could not
be found by typing their name either, and `include_settled` did not help because it only
relaxes the `outstanding > 0` filter — it does not reach past the join.

The tests below pin the directory's own guarantees, and the last one pins the
`/credit/customers` behaviour that made this confusing, so nobody "fixes" that endpoint
into a customer list and reintroduces the muddle.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
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


def _create(client: TestClient, headers: dict[str, str], name: str, **extra: Any) -> str:
    """Create through the real endpoint, with a client-generated id (FR-CUST-003)."""
    customer_id = str(uuid.uuid4())
    response = client.put(
        f"/api/v1/customers/{customer_id}",
        headers=headers,
        json={"name": name, **extra},
    )
    assert response.status_code == 200, response.text
    return customer_id


def test_a_customer_created_through_the_api_appears_in_the_directory(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """The reported symptom, as a test. This is the whole point of the endpoint."""
    customer_id = _create(api_client, headers, "Sukith", phone="9306432399")

    response = api_client.get("/api/v1/customers", headers=headers)
    assert response.status_code == 200, response.text
    ids = [item["id"] for item in response.json()["items"]]
    assert customer_id in ids


def test_the_newest_customer_is_first(api_client: TestClient, headers: dict[str, str]) -> None:
    """Newest first, because the one just added is the one being looked for."""
    _create(api_client, headers, "First Customer")
    second = _create(api_client, headers, "Second Customer")

    items = api_client.get("/api/v1/customers", headers=headers).json()["items"]
    assert items[0]["id"] == second


def test_a_customer_with_no_credit_history_is_still_listed(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """The exact gap. A customer who has never been sold anything on credit has no
    credit invoice, and that is the only reason they used to be invisible."""
    customer_id = _create(api_client, headers, "Never Bought Anything")
    items = api_client.get("/api/v1/customers", headers=headers).json()["items"]
    listed = next(item for item in items if item["id"] == customer_id)
    # Listed, which is the point. The credit block reads zero rather than being absent:
    # `_view` computes outstanding from the invoices and a customer with none sums to
    # zero. "Owes nothing" and "has never been billed on credit" look the same here,
    # which is fine for a directory — `/credit/customers` is where that distinction
    # matters, and it excludes them.
    assert listed["credit"]["outstanding"] == "0.00"
    assert listed["credit"]["oldest_due_date"] is None


def test_the_directory_carries_the_fields_the_screen_shows(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    customer_id = _create(
        api_client,
        headers,
        "Full Details Co",
        phone="9876500011",
        business_name="Full Details Transport",
        credit_limit="50000.00",
        credit_terms_days=30,
        vehicle_number="KA01AB1234",
    )
    items = api_client.get("/api/v1/customers", headers=headers).json()["items"]
    row = next(item for item in items if item["id"] == customer_id)
    assert row["name"] == "Full Details Co"
    assert row["primary_phone"] == "9876500011"
    assert row["business_name"] == "Full Details Transport"
    assert row["credit_limit"] == "50000.00"
    assert row["credit_terms_days"] == 30
    assert "KA01AB1234" in row["vehicles"]


def test_a_deactivated_customer_is_not_listed(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """Deactivation is the only delete (FR-CUST-011); listing them would surface records
    the shop has deliberately retired. They remain reachable by id."""
    customer_id = _create(api_client, headers, "Retired Customer")
    assert (
        api_client.post(f"/api/v1/customers/{customer_id}:deactivate", headers=headers).status_code
        == 204
    )

    items = api_client.get("/api/v1/customers", headers=headers).json()["items"]
    assert customer_id not in [item["id"] for item in items]
    assert api_client.get(f"/api/v1/customers/{customer_id}", headers=headers).status_code == 200


def test_the_directory_is_scoped_to_the_callers_business(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    """AC-8."""
    other = create_tenant(
        migration_session, display_name="OTHER SHOP", branch_code="OTH", counter_code="C9"
    )
    foreign_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO customers (id, business_id, name, normalized_name, is_active)"
            " VALUES (:id, :b, 'Their Customer', 'their customer', true)"
        ),
        {"id": foreign_id, "b": other.business_id},
    )
    migration_session.commit()

    items = api_client.get("/api/v1/customers", headers=headers).json()["items"]
    assert str(foreign_id) not in [item["id"] for item in items]


def test_paging_does_not_repeat_or_skip_a_customer(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """Keyset, not offset: a directory that grows while someone pages through it must
    not shift rows across page boundaries."""
    created = [_create(api_client, headers, f"Paged Customer {n}") for n in range(5)]

    first = api_client.get("/api/v1/customers?limit=2", headers=headers).json()
    assert first["page"]["has_more"] is True
    cursor = first["page"]["next_cursor"]
    assert cursor
    # Opaque and URL-safe by contract: digits only, so pasting it into a query string
    # cannot corrupt it. An ISO cursor carried a `+`, which decodes as a space.
    assert cursor.isdigit(), f"cursor must need no escaping, got {cursor!r}"

    second = api_client.get(f"/api/v1/customers?limit=2&cursor={cursor}", headers=headers).json()

    seen = [item["id"] for item in first["items"]] + [item["id"] for item in second["items"]]
    assert len(seen) == len(set(seen)), "a customer appeared on two pages"
    assert all(cid in created for cid in seen)


def test_the_last_page_reports_no_more(api_client: TestClient, headers: dict[str, str]) -> None:
    _create(api_client, headers, "Only Customer")
    page = api_client.get("/api/v1/customers?limit=50", headers=headers).json()
    assert page["page"]["has_more"] is False
    assert page["page"]["next_cursor"] is None


def test_an_unusable_cursor_is_refused_rather_than_ignored(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """Silently ignoring it would quietly return page one again, which looks like the
    end of the list."""
    response = api_client.get("/api/v1/customers?cursor=not-a-date", headers=headers)
    assert response.status_code == 422, response.text


def test_an_anonymous_caller_cannot_list_customers(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/customers").status_code == 401


@pytest.mark.parametrize("role", [SystemRole.STOCK_KEEPER])
def test_a_role_with_neither_permission_is_refused(
    api_client: TestClient, migration_session: Session, tenant: Tenant, role: SystemRole
) -> None:
    """`customer.manage` or `credit.read`, matching `search` — a stock keeper holds
    neither."""
    user = create_user(
        migration_session, tenant, username=f"dir-{role}", role=role, all_branches=True
    )
    response = api_client.get("/api/v1/customers", headers=_headers(api_client, user))
    assert response.status_code == 403


def test_credit_customers_still_lists_only_customers_with_credit_history(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """The behaviour that caused the confusion, pinned deliberately.

    `/credit/customers` is the Credit Bills view and must stay that way: it inner-joins
    credit invoices on purpose. If someone later widens it into a customer list, the two
    screens blur back together and "who owes us money" stops being answerable. The
    directory above is the place for a list.
    """
    customer_id = _create(api_client, headers, "No Credit Yet")

    # Visible in the directory...
    directory = api_client.get("/api/v1/customers", headers=headers).json()["items"]
    assert customer_id in [item["id"] for item in directory]

    # ...and absent from the credit view, with or without `include_settled`.
    for include in ("false", "true"):
        credit = api_client.get(
            f"/api/v1/credit/customers?include_settled={include}", headers=headers
        ).json()
        listed = [item["customer"]["id"] for item in credit["items"]]
        assert customer_id not in listed, (
            f"include_settled={include} should not reach past the join"
        )
