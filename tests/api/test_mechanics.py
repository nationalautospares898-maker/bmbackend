"""`/mechanics` — the reusable list the billing header suggests from (M27).

The owner's complaint: the same twenty mechanic names were retyped on every bill, and
misspelled often enough that the same person appeared several ways. These tests pin the
four properties that make the fix actually work at a counter, as opposed to merely
existing:

1. **Typing an existing name is not an error.** `POST /mechanics` is a get-or-create, so
   the billing screen can call it without first asking whether the name is known — which
   it cannot know, and must not have to.
2. **Case and whitespace do not create duplicates.** This is the specific defect the
   owner reported, so it is asserted directly rather than inferred from the unique index.
3. **A past bill is never rewritten.** `invoices.mechanic_name` is a snapshot and there
   is no foreign key to `mechanics`, so renaming a mechanic changes the next suggestion
   and nothing about a finalised bill. This is the test that would fail if somebody
   "normalised" the schema by adding that foreign key.
4. **An empty query is a real request.** The field is focused before anything is typed,
   and the recently-used list is what should appear then.
"""

from __future__ import annotations

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


def _ensure(client: TestClient, headers: dict[str, str], name: str, **extra: Any) -> dict:
    response = client.post("/api/v1/mechanics", headers=headers, json={"name": name, **extra})
    assert response.status_code == 200, response.text
    return response.json()


# --- get-or-create ------------------------------------------------------------


def test_a_new_name_is_created_and_reported_as_created(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    body = _ensure(api_client, headers, "Ramesh")
    assert body["created"] is True
    assert body["name"] == "Ramesh"
    assert body["is_active"] is True


def test_sending_the_same_name_again_returns_the_same_mechanic_and_does_not_error(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """The billing screen cannot know whether a name is already on the list.

    If this returned a `409` the screen would have to ask first, and a cashier who typed
    a known name would see an error for doing nothing wrong.
    """
    first = _ensure(api_client, headers, "Ramesh")
    second = _ensure(api_client, headers, "Ramesh")

    assert second["created"] is False
    assert second["id"] == first["id"]


@pytest.mark.parametrize(
    "variant",
    ["ramesh", "RAMESH", "  Ramesh  ", "Ramesh   ", "rAmEsH"],
)
def test_case_and_whitespace_do_not_create_a_second_mechanic(
    api_client: TestClient, headers: dict[str, str], variant: str
) -> None:
    """The duplicate problem the owner reported, asserted directly."""
    original = _ensure(api_client, headers, "Ramesh")
    again = _ensure(api_client, headers, variant)

    assert again["id"] == original["id"]
    assert again["created"] is False
    # The first spelling is kept as the display label; a later variant does not
    # overwrite it, because there is no reason to prefer the newer capitalisation.
    assert again["name"] == "Ramesh"


def test_internal_whitespace_is_collapsed_rather_than_kept(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    one = _ensure(api_client, headers, "Ramesh Kumar")
    two = _ensure(api_client, headers, "Ramesh    Kumar")
    assert two["id"] == one["id"]


@pytest.mark.parametrize("blank", ["", "   ", "\t", "..."])
def test_a_name_that_normalises_away_to_nothing_is_refused(
    api_client: TestClient, headers: dict[str, str], blank: str
) -> None:
    """Stored, it would be a row nobody could ever search for."""
    response = api_client.post("/api/v1/mechanics", headers=headers, json={"name": blank})
    assert response.status_code == 422, response.text


def test_two_concurrent_creates_of_one_name_converge_on_a_single_row(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    """The upsert, not the prior `SELECT`, is what makes this safe.

    Sequential requests here rather than real threads — the point under test is that the
    second request takes the `ON CONFLICT` branch and succeeds, which a plain `INSERT`
    guarded by a `SELECT` would not do under a race.
    """
    _ensure(api_client, headers, "Suresh")
    _ensure(api_client, headers, "Suresh")
    _ensure(api_client, headers, "suresh")

    count = migration_session.execute(
        text("SELECT count(*) FROM mechanics WHERE normalized_name = 'suresh'")
    ).scalar_one()
    assert count == 1


# --- suggestions --------------------------------------------------------------


def test_an_empty_query_returns_the_recently_used_list(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """The field is focused before anything is typed; that is a real request."""
    _ensure(api_client, headers, "Ramesh")
    _ensure(api_client, headers, "Suresh")

    response = api_client.get("/api/v1/mechanics", headers=headers)
    assert response.status_code == 200, response.text
    names = [item["name"] for item in response.json()["items"]]
    assert sorted(names) == ["Ramesh", "Suresh"]


def test_most_recently_used_comes_first(api_client: TestClient, headers: dict[str, str]) -> None:
    _ensure(api_client, headers, "Ramesh")
    _ensure(api_client, headers, "Suresh")
    # Used again, so it should lead the list.
    _ensure(api_client, headers, "Ramesh")

    items = api_client.get("/api/v1/mechanics", headers=headers).json()["items"]
    assert items[0]["name"] == "Ramesh"


def test_a_prefix_matches(api_client: TestClient, headers: dict[str, str]) -> None:
    _ensure(api_client, headers, "Ramesh")
    _ensure(api_client, headers, "Mahesh")

    items = api_client.get("/api/v1/mechanics", headers=headers, params={"query": "ram"}).json()[
        "items"
    ]
    assert [item["name"] for item in items] == ["Ramesh"]


def test_a_prefix_match_outranks_a_fuzzy_one(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """Typing "ram" means Ramesh before Sriram, which a trigram score alone does not
    know."""
    _ensure(api_client, headers, "Sriram")
    _ensure(api_client, headers, "Ramesh")

    items = api_client.get("/api/v1/mechanics", headers=headers, params={"query": "ram"}).json()[
        "items"
    ]
    assert items[0]["name"] == "Ramesh"


def test_a_deactivated_mechanic_is_not_suggested(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    created = _ensure(api_client, headers, "Ramesh")
    response = api_client.post(f"/api/v1/mechanics/{created['id']}:deactivate", headers=headers)
    assert response.status_code == 204, response.text

    items = api_client.get("/api/v1/mechanics", headers=headers).json()["items"]
    assert items == []


def test_deactivation_is_not_a_delete(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    created = _ensure(api_client, headers, "Ramesh")
    api_client.post(f"/api/v1/mechanics/{created['id']}:deactivate", headers=headers)

    still_there = migration_session.execute(
        text("SELECT is_active FROM mechanics WHERE id = :id"), {"id": created["id"]}
    ).scalar_one()
    assert still_there is False


def test_using_a_deactivated_name_again_brings_it_back(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """A mechanic who comes back should not need an admin screen to reappear."""
    created = _ensure(api_client, headers, "Ramesh")
    api_client.post(f"/api/v1/mechanics/{created['id']}:deactivate", headers=headers)

    revived = _ensure(api_client, headers, "Ramesh")
    assert revived["id"] == created["id"]
    assert revived["is_active"] is True


# --- renaming, and what it must not touch -------------------------------------


def test_a_rename_corrects_the_suggestion(api_client: TestClient, headers: dict[str, str]) -> None:
    created = _ensure(api_client, headers, "Rmaesh")
    response = api_client.patch(
        f"/api/v1/mechanics/{created['id']}", headers=headers, json={"name": "Ramesh"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Ramesh"

    items = api_client.get("/api/v1/mechanics", headers=headers).json()["items"]
    assert [item["name"] for item in items] == ["Ramesh"]


def test_a_rename_onto_an_existing_name_is_refused_rather_than_merged(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """Two mechanics are not provably one person because a rename made them collide —
    the same stance customers take (FR-CUST-004)."""
    _ensure(api_client, headers, "Ramesh")
    other = _ensure(api_client, headers, "Suresh")

    response = api_client.patch(
        f"/api/v1/mechanics/{other['id']}", headers=headers, json={"name": "Ramesh"}
    )
    assert response.status_code == 422, response.text


def test_the_mechanics_table_has_no_foreign_key_from_invoices(
    migration_session: Session,
) -> None:
    """The structural guarantee behind "a past bill is never rewritten".

    `invoices.mechanic_name` is a snapshot. If somebody adds a foreign key to
    `mechanics` — the obvious "normalisation" — then renaming a mechanic silently
    rewrites what every past bill says, and the immutability of a finalised invoice
    (OD-10, invariant 1) is broken by a reference-data edit. This test is the tripwire.
    """
    referencing = migration_session.execute(
        text(
            """
            SELECT tc.table_name, kcu.column_name
              FROM information_schema.table_constraints tc
              JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name
              JOIN information_schema.key_column_usage kcu
                ON kcu.constraint_name = tc.constraint_name
             WHERE tc.constraint_type = 'FOREIGN KEY'
               AND ccu.table_name = 'mechanics'
            """
        )
    ).all()
    assert referencing == [], (
        "nothing may reference `mechanics`: the bill's `mechanic_name` snapshot is the "
        "historical record, and a foreign key would let a rename rewrite past bills"
    )


def test_invoices_still_store_the_mechanic_as_a_text_snapshot(
    migration_session: Session,
) -> None:
    """The snapshot column is what preserves the mechanic on a historical invoice.

    The column is named `mechanic_name_snapshot`, and the suffix is the whole point:
    it records what the bill said, not who the mechanic record currently is.
    """
    column = migration_session.execute(
        text(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name = 'invoices' AND column_name = 'mechanic_name_snapshot'"
        )
    ).scalar_one()
    assert column == "character varying"


# --- tenancy and permissions --------------------------------------------------


def test_another_tenants_mechanic_is_a_404_not_a_403(
    api_client: TestClient,
    headers: dict[str, str],
    migration_session: Session,
) -> None:
    """Never leak existence across a tenant boundary (`CLAUDE.md §7`)."""
    other_tenant = create_tenant(migration_session)
    other_owner = create_user(
        migration_session,
        other_tenant,
        username="other-owner",
        role=SystemRole.OWNER,
        all_branches=True,
    )
    other_headers = _headers(api_client, other_owner)
    theirs = _ensure(api_client, other_headers, "Ramesh")

    response = api_client.patch(
        f"/api/v1/mechanics/{theirs['id']}", headers=headers, json={"name": "Changed"}
    )
    assert response.status_code == 404, response.text


def test_the_same_name_in_two_tenants_is_two_mechanics(
    api_client: TestClient,
    headers: dict[str, str],
    migration_session: Session,
) -> None:
    """Uniqueness is per business, not global."""
    mine = _ensure(api_client, headers, "Ramesh")

    other_tenant = create_tenant(migration_session)
    other_owner = create_user(
        migration_session,
        other_tenant,
        username="other-owner-2",
        role=SystemRole.OWNER,
        all_branches=True,
    )
    theirs = _ensure(api_client, _headers(api_client, other_owner), "Ramesh")

    assert theirs["id"] != mine["id"]


def test_a_cashier_may_maintain_the_list(
    api_client: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    """The permission is `customer.manage`, which the cashier already holds.

    Deliberately not a new permission name: the list is maintained at the counter while
    a bill is being written, so a cashier who could not add one would be back to
    retyping — and inventing a permission would need `PRD.md §3.2` changed first
    (`CLAUDE.md §6`).
    """
    cashier = create_user(migration_session, tenant, username="cashier", role=SystemRole.CASHIER)
    cashier_headers = _headers(api_client, cashier)

    created = _ensure(api_client, cashier_headers, "Ramesh")
    assert created["created"] is True
    assert api_client.get("/api/v1/mechanics", headers=cashier_headers).status_code == 200


def test_a_role_without_customer_manage_is_refused(
    api_client: TestClient, migration_session: Session, tenant: Tenant
) -> None:
    stock_keeper = create_user(
        migration_session, tenant, username="stock", role=SystemRole.STOCK_KEEPER
    )
    sk_headers = _headers(api_client, stock_keeper)

    assert api_client.get("/api/v1/mechanics", headers=sk_headers).status_code == 403
    assert (
        api_client.post(
            "/api/v1/mechanics", headers=sk_headers, json={"name": "Ramesh"}
        ).status_code
        == 403
    )


def test_mechanics_moves_neither_money_nor_stock(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    """Invariant 10b's neighbourhood: reference data has no financial effect.

    Asserted because the endpoint needs no `Idempotency-Key`, and the justification for
    that is precisely that nothing financial happens here.
    """
    before_movements = migration_session.execute(
        text("SELECT count(*) FROM stock_movements")
    ).scalar_one()
    before_payments = migration_session.execute(text("SELECT count(*) FROM payments")).scalar_one()

    _ensure(api_client, headers, "Ramesh")

    assert (
        migration_session.execute(text("SELECT count(*) FROM stock_movements")).scalar_one()
        == before_movements
    )
    assert (
        migration_session.execute(text("SELECT count(*) FROM payments")).scalar_one()
        == before_payments
    )


def test_creating_a_mechanic_is_audited(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    created = _ensure(api_client, headers, "Ramesh")

    action = migration_session.execute(
        text("SELECT action FROM audit_logs WHERE entity_type = 'mechanic'   AND entity_id = :id"),
        {"id": created["id"]},
    ).scalar_one()
    assert action == "mechanic.created"


def test_reusing_an_existing_mechanic_is_not_audited_as_a_creation(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    """Otherwise every bill would add an audit row claiming a mechanic was created."""
    created = _ensure(api_client, headers, "Ramesh")
    _ensure(api_client, headers, "Ramesh")
    _ensure(api_client, headers, "Ramesh")

    rows = migration_session.execute(
        text(
            "SELECT count(*) FROM audit_logs WHERE entity_type = 'mechanic'"
            "   AND entity_id = :id AND action = 'mechanic.created'"
        ),
        {"id": created["id"]},
    ).scalar_one()
    assert rows == 1
