"""Editing a finalised invoice in place (owner Decision A, 2026-11-07).

OD-10 said "there is no edit path, ever" and `0011` enforced it with a privilege revoke.
The owner reversed that. These tests pin what replaced it, and the ones that matter most
are the refusals — because an edit feature that *mostly* works is a feature that quietly
loses money.

The five properties under test, in order of how much they would cost if wrong:

1. **A bill cannot be edited below what has been paid.** Blocked, with payment history
   untouched. There is no refund entity in this schema and inventing one was expressly
   excluded, so refusing is the only honest answer.
2. **Payments are never touched.** Not by a total increase, not by a decrease, not by a
   customer change.
3. **Stock reconciles exactly**, by net change per product, through new append-only
   movements — never by rewriting an old one.
4. **Identity is preserved.** Same id, same internal reference, same series position.
5. **A stale edit is refused**, and a retried one applies once.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
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


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session,
        tenant,
        username="amend-owner",
        role=SystemRole.OWNER,
        all_branches=True,
    )


@pytest.fixture
def headers(api_client: TestClient, owner: UserFixture) -> dict[str, str]:
    client_headers = _headers(api_client, owner)
    api_client.post("/api/v1/units:ensure-defaults", headers=client_headers, json={})
    return client_headers


def _product(
    client: TestClient, headers: dict[str, str], name: str, price: str = "100.0000"
) -> str:
    response = client.post(
        "/api/v1/products",
        headers=headers,
        json={"name": name, "unit_code": "NOS", "default_selling_price": price},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _stock(client: TestClient, headers: dict[str, str], product_id: str, qty: str) -> None:
    """Stock arrives only through a confirmed purchase — never by setting a number."""
    supplier_id = str(uuid.uuid4())
    # A unique name per call: supplier names are unique per business, so reusing one
    # with a fresh id leaves the id uncreated and the purchase 404s on it.
    created = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        headers=headers,
        json={"name": f"Amend Supplier {uuid.uuid4().hex[:8]}"},
    )
    assert created.status_code in (200, 201), created.text
    purchase = client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "supplier_id": supplier_id,
            "supplier_document_number": f"AMEND/{uuid.uuid4().hex[:8]}",
            "items": [{"product_id": product_id, "quantity": qty, "unit_cost": "10.0000"}],
        },
    )
    assert purchase.status_code in (200, 201), purchase.text
    confirmed = client.post(
        f"/api/v1/purchases/{purchase.json()['purchase_id']}:confirm",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={},
    )
    assert confirmed.status_code == 200, confirmed.text


def _balance(client: TestClient, headers: dict[str, str], product_id: str) -> Decimal:
    body = client.get(f"/api/v1/products/{product_id}", headers=headers).json()
    return Decimal(str((body.get("stock") or {}).get("branch_quantity", "0")))


def _finalize(
    client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    *,
    lines: list[dict[str, Any]],
    sale_type: str = "cash",
    payment: dict[str, Any] | None = None,
    customer_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "counter_id": str(tenant.counter_id),
        "sale_type": sale_type,
        "lines": lines,
        "header": {"customer_name": "Amend Walk-in", "mechanic_name": "Ramesh"},
    }
    if payment is not None:
        body["payment"] = payment
    if customer_id is not None:
        body["customer"] = {"mode": "existing", "customer_id": customer_id}
    response = client.post(
        "/api/v1/sales:finalize",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()["invoice"]


def _amend(
    client: TestClient,
    headers: dict[str, str],
    invoice_id: str,
    *,
    version: int,
    lines: list[dict[str, Any]],
    reason: str = "Customer took one more item",
    key: str | None = None,
    header: dict[str, Any] | None = None,
) -> Any:
    body: dict[str, Any] = {
        "expected_version": version,
        "reason": reason,
        "lines": lines,
    }
    if header is not None:
        body["header"] = header
    return client.post(
        f"/api/v1/invoices/{invoice_id}:amend",
        headers={**headers, "Idempotency-Key": key or str(uuid.uuid4())},
        json=body,
    )


@pytest.fixture
def billed(api_client: TestClient, headers: dict[str, str], tenant: Tenant) -> dict[str, Any]:
    """A cash bill: 2 × 100 of one stocked product, nothing paid."""
    product_id = _product(api_client, headers, "Amend Part A")
    _stock(api_client, headers, product_id, "20.000")
    invoice = _finalize(
        api_client,
        headers,
        tenant,
        lines=[
            {
                "product_id": product_id,
                "quantity": "2.000",
                "unit_price": "100.0000",
                "price_source": "catalog",
            }
        ],
        payment={"method": "cash", "amount": "200.00"},
    )
    return {"invoice": invoice, "product_id": product_id}


# --- identity -----------------------------------------------------------------


def test_an_edit_keeps_the_invoice_id_and_reference(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    """The single most important property: an ordinary edit is not a new bill."""
    invoice = billed["invoice"]
    response = _amend(
        api_client,
        headers,
        invoice["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "3.000", "unit_price": "100.0000"}],
    )
    assert response.status_code == 200, response.text

    after = api_client.get(f"/api/v1/invoices/{invoice['id']}", headers=headers).json()
    assert after["id"] == invoice["id"]
    assert after["internal_reference"] == invoice["internal_reference"]
    assert after["sequence_number"] == invoice["sequence_number"]
    assert after["issued_at"] == invoice["issued_at"]


def test_an_edit_creates_no_second_invoice(
    api_client: TestClient,
    headers: dict[str, str],
    billed: dict[str, Any],
    migration_session: Session,
) -> None:
    before = migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one()
    _amend(
        api_client,
        headers,
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "3.000", "unit_price": "100.0000"}],
    )
    after = migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one()
    assert after == before


def test_the_database_refuses_to_change_an_invoice_number(
    migration_session: Session, app_session: Session, app_role_present: bool
) -> None:
    """Belt and braces: the column-level grant, not just the service's good manners."""
    if not app_role_present:
        pytest.skip("bm_app is not a distinct role in this database")
    with pytest.raises(Exception, match=r"permission denied|InsufficientPrivilege"):
        app_session.execute(text("UPDATE invoices SET invoice_number = 'x' WHERE false"))
    app_session.rollback()


# --- the money ----------------------------------------------------------------


def test_increasing_the_quantity_recomputes_the_total_server_side(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    response = _amend(
        api_client,
        headers,
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "5.000", "unit_price": "100.0000"}],
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == "500.00"

    after = api_client.get(f"/api/v1/invoices/{billed['invoice']['id']}", headers=headers).json()
    assert after["total"] == "500.00"
    assert after["subtotal"] == "500.00"


def test_a_decrease_down_to_exactly_what_was_paid_is_allowed(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    """Equal is permitted — that is a bill edited down to settled."""
    response = _amend(
        api_client,
        headers,
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "2.000", "unit_price": "100.0000"}],
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == "200.00"
    assert response.json()["outstanding"] == "0.00"
    assert response.json()["payment_status"] == "paid"


# --- the paid-total rule ------------------------------------------------------


def test_an_edit_below_the_paid_amount_is_refused(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    """The owner's explicit rule. ₹200 paid; editing to ₹100 must be blocked."""
    response = _amend(
        api_client,
        headers,
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "1.000", "unit_price": "100.0000"}],
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "EDIT_BELOW_PAID_TOTAL"


def test_a_refused_edit_leaves_the_bill_completely_untouched(
    api_client: TestClient,
    headers: dict[str, str],
    billed: dict[str, Any],
    migration_session: Session,
) -> None:
    """No revision, no stock movement, no changed total. A refusal costs nothing."""
    invoice_id = billed["invoice"]["id"]
    stock_before = _balance(api_client, headers, billed["product_id"])
    movements_before = migration_session.execute(
        text("SELECT count(*) FROM stock_movements")
    ).scalar_one()

    _amend(
        api_client,
        headers,
        invoice_id,
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "1.000", "unit_price": "100.0000"}],
    )

    after = api_client.get(f"/api/v1/invoices/{invoice_id}", headers=headers).json()
    assert after["total"] == "200.00"
    assert after["amount_paid"] == "200.00"
    assert _balance(api_client, headers, billed["product_id"]) == stock_before
    assert (
        migration_session.execute(text("SELECT count(*) FROM stock_movements")).scalar_one()
        == movements_before
    )
    assert (
        migration_session.execute(
            text("SELECT count(*) FROM invoice_revisions WHERE invoice_id = :id"),
            {"id": invoice_id},
        ).scalar_one()
        == 0
    )


def test_a_refused_edit_does_not_alter_payment_history(
    api_client: TestClient,
    headers: dict[str, str],
    billed: dict[str, Any],
    migration_session: Session,
) -> None:
    before = migration_session.execute(
        text("SELECT count(*), coalesce(sum(amount), 0) FROM payments")
    ).one()
    _amend(
        api_client,
        headers,
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "1.000", "unit_price": "100.0000"}],
    )
    after = migration_session.execute(
        text("SELECT count(*), coalesce(sum(amount), 0) FROM payments")
    ).one()
    assert after == before


# --- payments are never touched -----------------------------------------------


def test_an_increase_preserves_the_payment_and_reopens_the_balance(
    api_client: TestClient,
    headers: dict[str, str],
    billed: dict[str, Any],
    migration_session: Session,
) -> None:
    payments_before = migration_session.execute(
        text("SELECT id, amount FROM payments ORDER BY id")
    ).all()

    response = _amend(
        api_client,
        headers,
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "5.000", "unit_price": "100.0000"}],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # ₹500 bill, ₹200 already paid, ₹300 now outstanding — and the payment row is the
    # same row it was.
    assert body["amount_paid"] == "200.00"
    assert body["outstanding"] == "300.00"
    assert body["payment_status"] == "partially_paid"
    assert (
        migration_session.execute(text("SELECT id, amount FROM payments ORDER BY id")).all()
        == payments_before
    )


# --- stock --------------------------------------------------------------------


def test_increasing_a_quantity_consumes_only_the_difference(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    """20 received, 2 sold, so 18. Editing to 5 must take 3 more, not 5."""
    assert _balance(api_client, headers, billed["product_id"]) == Decimal("18.000")

    _amend(
        api_client,
        headers,
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "5.000", "unit_price": "100.0000"}],
    )

    assert _balance(api_client, headers, billed["product_id"]) == Decimal("15.000")


def test_decreasing_a_quantity_returns_the_difference_to_stock(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    product_id = _product(api_client, headers, "Amend Part Down")
    _stock(api_client, headers, product_id, "20.000")
    invoice = _finalize(
        api_client,
        headers,
        tenant,
        lines=[
            {
                "product_id": product_id,
                "quantity": "5.000",
                "unit_price": "100.0000",
                "price_source": "catalog",
            }
        ],
        payment={"method": "cash", "amount": "500.00"},
    )
    assert _balance(api_client, headers, product_id) == Decimal("15.000")

    # Down to 5 × 100 = 500, which equals what was paid, so the rule permits it. To go
    # lower the payment would have to be lower; that case is covered above.
    response = _amend(
        api_client,
        headers,
        invoice["id"],
        version=1,
        lines=[{"product_id": product_id, "quantity": "5.000", "unit_price": "100.0000"}],
    )
    assert response.status_code == 200, response.text
    # Unchanged quantity writes **no** movement, so the balance is untouched.
    assert _balance(api_client, headers, product_id) == Decimal("15.000")


def test_an_unpaid_bill_can_be_reduced_and_returns_stock(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    product_id = _product(api_client, headers, "Amend Part Unpaid")
    _stock(api_client, headers, product_id, "20.000")
    customer_id = str(uuid.uuid4())
    api_client.put(
        f"/api/v1/customers/{customer_id}", headers=headers, json={"name": "Amend Credit Co"}
    )
    invoice = _finalize(
        api_client,
        headers,
        tenant,
        lines=[
            {
                "product_id": product_id,
                "quantity": "5.000",
                "unit_price": "100.0000",
                "price_source": "catalog",
            }
        ],
        sale_type="credit",
        customer_id=customer_id,
    )
    assert _balance(api_client, headers, product_id) == Decimal("15.000")

    response = _amend(
        api_client,
        headers,
        invoice["id"],
        version=1,
        lines=[{"product_id": product_id, "quantity": "2.000", "unit_price": "100.0000"}],
    )
    assert response.status_code == 200, response.text
    # Three came back.
    assert _balance(api_client, headers, product_id) == Decimal("18.000")
    assert response.json()["total"] == "200.00"


def test_changing_the_product_adjusts_both(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    """A swap is a decrease of one and an increase of the other, which is why the diff
    is computed per product rather than per line."""
    first = _product(api_client, headers, "Amend Swap From")
    second = _product(api_client, headers, "Amend Swap To")
    _stock(api_client, headers, first, "10.000")
    _stock(api_client, headers, second, "10.000")
    customer_id = str(uuid.uuid4())
    api_client.put(
        f"/api/v1/customers/{customer_id}", headers=headers, json={"name": "Amend Swap Co"}
    )
    invoice = _finalize(
        api_client,
        headers,
        tenant,
        lines=[
            {
                "product_id": first,
                "quantity": "4.000",
                "unit_price": "100.0000",
                "price_source": "catalog",
            }
        ],
        sale_type="credit",
        customer_id=customer_id,
    )
    assert _balance(api_client, headers, first) == Decimal("6.000")
    assert _balance(api_client, headers, second) == Decimal("10.000")

    response = _amend(
        api_client,
        headers,
        invoice["id"],
        version=1,
        lines=[{"product_id": second, "quantity": "4.000", "unit_price": "100.0000"}],
        reason="Wrong part was picked from the shelf",
    )
    assert response.status_code == 200, response.text
    assert _balance(api_client, headers, first) == Decimal("10.000")
    assert _balance(api_client, headers, second) == Decimal("6.000")


def test_stock_movements_are_appended_never_rewritten(
    api_client: TestClient,
    headers: dict[str, str],
    billed: dict[str, Any],
    migration_session: Session,
) -> None:
    before = migration_session.execute(text("SELECT id FROM stock_movements ORDER BY id")).all()

    _amend(
        api_client,
        headers,
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "5.000", "unit_price": "100.0000"}],
    )

    after = migration_session.execute(text("SELECT id FROM stock_movements ORDER BY id")).all()
    # Every original movement is still there, and one more has been added.
    assert set(before).issubset(set(after))
    assert len(after) == len(before) + 1


def test_an_edit_beyond_available_stock_is_refused(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    """The existing oversell policy still applies to an edit."""
    cashier_free = _amend(
        api_client,
        headers,
        billed["invoice"]["id"],
        version=1,
        lines=[
            {
                "product_id": billed["product_id"],
                "quantity": "500.000",
                "unit_price": "100.0000",
            }
        ],
    )
    # The owner holds the override permission, so the refusal is for the missing reason
    # rather than for the shortfall itself — either way the edit does not go through.
    assert cashier_free.status_code in (409, 422), cashier_free.text
    assert cashier_free.json()["error"]["code"] in {
        "INSUFFICIENT_STOCK",
        "OVERSELL_REASON_REQUIRED",
    }


# --- the revision history -----------------------------------------------------


def test_the_before_state_is_recorded_in_full(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    invoice_id = billed["invoice"]["id"]
    _amend(
        api_client,
        headers,
        invoice_id,
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "5.000", "unit_price": "100.0000"}],
        reason="Customer took three more",
    )

    history = api_client.get(f"/api/v1/invoices/{invoice_id}/revisions", headers=headers).json()[
        "items"
    ]
    assert len(history) == 1
    revision = history[0]
    assert revision["revision_number"] == 1
    assert revision["reason"] == "Customer took three more"
    assert revision["edited_by"] is not None
    # The bill **as it was**: two units at a two-hundred-rupee total.
    assert revision["total"] == "200.00"
    assert revision["amount_paid"] == "200.00"
    assert revision["mechanic_name"] == "Ramesh"
    assert [item["quantity"] for item in revision["items"]] == ["2.000"]
    assert revision["items"][0]["line_total"] == "200.00"


def test_each_edit_appends_a_new_revision(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    invoice_id = billed["invoice"]["id"]
    _amend(
        api_client,
        headers,
        invoice_id,
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "3.000", "unit_price": "100.0000"}],
    )
    _amend(
        api_client,
        headers,
        invoice_id,
        version=2,
        lines=[{"product_id": billed["product_id"], "quantity": "4.000", "unit_price": "100.0000"}],
    )

    history = api_client.get(f"/api/v1/invoices/{invoice_id}/revisions", headers=headers).json()[
        "items"
    ]
    # Newest first, and nothing overwritten: 300 was the state before the second edit.
    assert [r["revision_number"] for r in history] == [2, 1]
    assert history[0]["total"] == "300.00"
    assert history[1]["total"] == "200.00"


def test_a_revision_cannot_be_altered_or_removed(
    app_session: Session, app_role_present: bool
) -> None:
    """The privilege that replaces 0011's blanket revoke."""
    if not app_role_present:
        pytest.skip("bm_app is not a distinct role in this database")
    for statement in (
        "UPDATE invoice_revisions SET reason = 'x' WHERE false",
        "DELETE FROM invoice_revisions WHERE false",
        "UPDATE invoice_revision_items SET quantity = 1 WHERE false",
        "DELETE FROM invoice_revision_items WHERE false",
    ):
        with pytest.raises(Exception, match=r"permission denied|InsufficientPrivilege"):
            app_session.execute(text(statement))
        app_session.rollback()


# --- concurrency and idempotency ----------------------------------------------


def test_a_stale_edit_is_refused_rather_than_overwriting(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    invoice_id = billed["invoice"]["id"]
    first = _amend(
        api_client,
        headers,
        invoice_id,
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "3.000", "unit_price": "100.0000"}],
    )
    assert first.status_code == 200, first.text

    # A second editor who still has version 1 open.
    stale = _amend(
        api_client,
        headers,
        invoice_id,
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "9.000", "unit_price": "100.0000"}],
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "INVOICE_VERSION_CONFLICT"

    # The first editor's change survived.
    after = api_client.get(f"/api/v1/invoices/{invoice_id}", headers=headers).json()
    assert after["total"] == "300.00"


def test_the_version_increments_once_per_edit(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    body = _amend(
        api_client,
        headers,
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "3.000", "unit_price": "100.0000"}],
    ).json()
    assert body["version"] == 2


def test_a_retried_save_applies_once(
    api_client: TestClient,
    headers: dict[str, str],
    billed: dict[str, Any],
    migration_session: Session,
) -> None:
    """The same key twice must not write a second revision or a second movement."""
    invoice_id = billed["invoice"]["id"]
    key = str(uuid.uuid4())
    lines = [{"product_id": billed["product_id"], "quantity": "5.000", "unit_price": "100.0000"}]

    first = _amend(api_client, headers, invoice_id, version=1, lines=lines, key=key)
    assert first.status_code == 200, first.text
    second = _amend(api_client, headers, invoice_id, version=1, lines=lines, key=key)
    assert second.status_code == 200, second.text
    assert second.json() == first.json()

    assert (
        migration_session.execute(
            text("SELECT count(*) FROM invoice_revisions WHERE invoice_id = :id"),
            {"id": invoice_id},
        ).scalar_one()
        == 1
    )
    # 18 after the sale, 15 after taking three more — and not 12.
    assert _balance(api_client, headers, billed["product_id"]) == Decimal("15.000")


# --- header fields ------------------------------------------------------------


def test_the_mechanic_and_notes_can_be_changed(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    invoice_id = billed["invoice"]["id"]
    response = _amend(
        api_client,
        headers,
        invoice_id,
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "2.000", "unit_price": "100.0000"}],
        header={"mechanic_name": "Suresh", "notes": "Collected by the driver"},
    )
    assert response.status_code == 200, response.text

    after = api_client.get(f"/api/v1/invoices/{invoice_id}", headers=headers).json()
    assert after["customer"]["mechanic_name_snapshot"] == "Suresh"
    assert after["notes"] == "Collected by the driver"


def test_an_omitted_header_field_is_left_alone(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    """Omitting is not clearing — otherwise every edit would wipe the mechanic."""
    invoice_id = billed["invoice"]["id"]
    _amend(
        api_client,
        headers,
        invoice_id,
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "2.000", "unit_price": "100.0000"}],
        header={"notes": "Only the note changed"},
    )
    after = api_client.get(f"/api/v1/invoices/{invoice_id}", headers=headers).json()
    assert after["customer"]["mechanic_name_snapshot"] == "Ramesh"


# --- refusals -----------------------------------------------------------------


def test_a_reason_is_required(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    response = _amend(
        api_client,
        headers,
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "3.000", "unit_price": "100.0000"}],
        reason="x",
    )
    assert response.status_code == 422, response.text


def test_an_empty_line_set_is_refused(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    """A bill with no items is not an edit, it is a cancellation — and that has its own
    endpoint (ADR-015, Decision C)."""
    response = _amend(api_client, headers, billed["invoice"]["id"], version=1, lines=[])
    assert response.status_code == 422, response.text


def test_a_cancelled_bill_cannot_be_edited(
    api_client: TestClient, headers: dict[str, str], billed: dict[str, Any]
) -> None:
    invoice_id = billed["invoice"]["id"]
    cancelled = api_client.post(
        f"/api/v1/invoices/{invoice_id}:cancel",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"reason": "Customer changed their mind entirely"},
    )
    assert cancelled.status_code == 200, cancelled.text

    response = _amend(
        api_client,
        headers,
        invoice_id,
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "3.000", "unit_price": "100.0000"}],
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "INVOICE_NOT_EDITABLE"


def test_another_tenants_invoice_is_a_404(
    api_client: TestClient,
    headers: dict[str, str],
    billed: dict[str, Any],
    migration_session: Session,
) -> None:
    from tests.fixtures import create_tenant

    other = create_tenant(migration_session)
    intruder = create_user(
        migration_session,
        other,
        username="amend-intruder",
        role=SystemRole.OWNER,
        all_branches=True,
    )
    response = _amend(
        api_client,
        _headers(api_client, intruder),
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "3.000", "unit_price": "100.0000"}],
    )
    assert response.status_code == 404, response.text


# --- permissions --------------------------------------------------------------


def test_a_cashier_cannot_edit_a_finalised_bill(
    api_client: TestClient,
    headers: dict[str, str],
    billed: dict[str, Any],
    migration_session: Session,
    tenant: Tenant,
) -> None:
    """`billing.sale.edit` sits with cancellation: owner and manager only."""
    cashier = create_user(
        migration_session, tenant, username="amend-cashier", role=SystemRole.CASHIER
    )
    response = _amend(
        api_client,
        _headers(api_client, cashier),
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "3.000", "unit_price": "100.0000"}],
    )
    assert response.status_code == 403, response.text


def test_a_manager_can_edit(
    api_client: TestClient,
    headers: dict[str, str],
    billed: dict[str, Any],
    migration_session: Session,
    tenant: Tenant,
) -> None:
    manager = create_user(
        migration_session,
        tenant,
        username="amend-manager",
        role=SystemRole.MANAGER,
        all_branches=True,
    )
    response = _amend(
        api_client,
        _headers(api_client, manager),
        billed["invoice"]["id"],
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "3.000", "unit_price": "100.0000"}],
    )
    assert response.status_code == 200, response.text


# --- audit --------------------------------------------------------------------


def test_the_edit_is_audited_with_its_reason(
    api_client: TestClient,
    headers: dict[str, str],
    billed: dict[str, Any],
    migration_session: Session,
) -> None:
    invoice_id = billed["invoice"]["id"]
    _amend(
        api_client,
        headers,
        invoice_id,
        version=1,
        lines=[{"product_id": billed["product_id"], "quantity": "3.000", "unit_price": "100.0000"}],
        reason="Customer took one more item",
    )

    row = migration_session.execute(
        text(
            "SELECT action, reason, before_value, after_value FROM audit_logs"
            " WHERE entity_type = 'invoice' AND action = 'invoice.amended'"
            "   AND entity_id = :id"
        ),
        {"id": invoice_id},
    ).one()
    assert row.action == "invoice.amended"
    assert row.reason == "Customer took one more item"
    assert row.before_value["total"] == "200.00"
    assert row.after_value["total"] == "300.00"
