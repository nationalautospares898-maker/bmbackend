"""The day's stock movement report — what came in, what went out, and the figures.

This is a reporting feature, so the tests are mostly about **accuracy**. Three properties
matter more than anything on screen:

1. `stock_before` and `stock_after` come from the ledger, not from today's total. The
   identity `before + added == after` (and `before - sold == after`) is asserted on every
   row, including the awkward case of one product moving several times in a day.
2. Movements that should not appear **cannot** appear: a draft, a reprint and a credit
   payment all write no `stock_movements` row at all, which is a structural guarantee
   rather than a filter somebody has to maintain.
3. A sale deducts once and a receipt adds once, so the report never double-counts.

The day is the **business-local** one. There is a test below at 23:59 and 00:01 local,
because truncating UTC would put an evening sale on the wrong day for every shop east of
Greenwich — which is every shop this application has.
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
from tests.fixtures import Tenant, UserFixture, create_tenant, create_user

pytestmark = pytest.mark.db

REPORT = "/api/v1/inventory/movements/report"


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def headers(api_client: TestClient, owner: UserFixture, tenant: Tenant) -> dict[str, str]:
    response = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": owner.username,
            "password": owner.password,
            "device": {"id": str(owner.device_id), "label": "Shop PC"},
        },
    )
    assert response.status_code == 200, response.text
    return {
        "Authorization": f"Bearer {response.json()['access_token']}",
        "X-Counter-Id": str(tenant.counter_id),
    }


@pytest.fixture
def units(api_client: TestClient, headers: dict[str, str]) -> None:
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200


@pytest.fixture
def product(api_client: TestClient, headers: dict[str, str], units: None) -> dict[str, Any]:
    response = api_client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": "Brake Shoe Set - Tata 407",
            "unit_code": "SET",
            "default_selling_price": "1250.0000",
            "sku": "TT-407-BS",
            "reorder_level": "2.000",
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


@pytest.fixture
def supplier(api_client: TestClient, headers: dict[str, str]) -> str:
    supplier_id = str(uuid.uuid4())
    response = api_client.put(
        f"/api/v1/suppliers/{supplier_id}",
        headers=headers,
        json={"name": "Allied Spares Depot"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _receive(
    api_client: TestClient,
    headers: dict[str, str],
    supplier_id: str,
    product_id: str,
    quantity: str,
    document: str = "ASD/2026/881",
) -> str:
    """Stock in through a confirmed purchase — the sanctioned receiving path."""
    created = api_client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "supplier_id": supplier_id,
            "supplier_document_number": document,
            "items": [{"product_id": product_id, "quantity": quantity, "unit_cost": "980.0000"}],
        },
    )
    assert created.status_code == 201, created.text
    purchase_id = str(created.json()["purchase_id"])
    confirmed = api_client.post(
        f"/api/v1/purchases/{purchase_id}:confirm", headers={**headers, **_key()}
    )
    assert confirmed.status_code == 200, confirmed.text
    return purchase_id


def _sell(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    product_id: str,
    quantity: str = "2.000",
    *,
    credit: bool = False,
    customer_id: str | None = None,
) -> dict[str, Any]:
    total = str(int(Decimal(quantity) * Decimal("1250"))) + ".00"
    body: dict[str, Any] = {
        "counter_id": str(tenant.counter_id),
        "sale_type": "credit" if credit else "cash",
        "lines": [
            {
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": "1250.0000",
                "price_source": "catalog",
            }
        ],
        "header": {"customer_name": "Imran", "vehicle_number": "KA 01 AB 1234"},
        "client_computed_total": total,
    }
    if credit:
        body["customer"] = {"mode": "existing", "customer_id": customer_id}
        # No `payment` at all. Nothing was handed over, and the server refuses a zero
        # amount rather than recording a payment that did not happen.
    else:
        body["payment"] = {"method": "cash", "amount": total}

    response = api_client.post("/api/v1/sales:finalize", headers={**headers, **_key()}, json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _today(api_client: TestClient, headers: dict[str, str]) -> str:
    """The business's own date, from the server — never the test runner's clock."""
    response = api_client.get("/api/v1/sync/bootstrap", headers=headers)
    assert response.status_code == 200, response.text
    return str(response.json()["business_date"])


def _report(
    api_client: TestClient, headers: dict[str, str], *, on_date: str, kind: str
) -> dict[str, Any]:
    response = api_client.get(REPORT, headers=headers, params={"on_date": on_date, "kind": kind})
    assert response.status_code == 200, response.text
    return dict(response.json())


# --- stock added --------------------------------------------------------------


def test_a_purchase_receipt_appears_with_the_supplier_document(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    supplier: str,
) -> None:
    """The headline for stock in: the right quantity, the right before and after, and a
    reference the owner can go and look up."""
    _receive(api_client, headers, supplier, product["id"], "10.000")

    report = _report(api_client, headers, on_date=_today(api_client, headers), kind="added")

    assert report["movements"] == 1
    assert report["products"] == 1
    assert Decimal(report["total_quantity"]) == Decimal("10.000")
    # `invoices` is a sold-report figure and must not leak into this one.
    assert report["invoices"] == 0

    line = report["items"][0]
    assert line["product_name"] == "Brake Shoe Set - Tata 407"
    assert line["sku"] == "TT-407-BS"
    assert Decimal(line["quantity"]) == Decimal("10.000")
    # Nothing was on the shelf before, ten after. Both from the ledger.
    assert Decimal(line["stock_before"]) == Decimal("0.000")
    assert Decimal(line["stock_after"]) == Decimal("10.000")
    assert line["reason"] == "purchase_receipt"
    assert line["reference"] == "ASD/2026/881"
    # Not a sale, so there is no cash/credit answer to give.
    assert line["is_credit"] is None


def test_the_owners_stock_sheet_and_a_correction_both_count_as_added(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
) -> None:
    """`added` is "everything that increased stock", not a hand-maintained list of
    reasons — so the sheet the owner actually uses is in it, and so is a correction."""
    sheet = api_client.post(
        "/api/v1/inventory/stock-entries",
        headers={**headers, **_key()},
        json={
            "entries": [
                {
                    "item": "Brake Shoe Set - Tata 407",
                    "price": "1250.0000",
                    "min_qty": "2.000",
                    "stock_qty": "4.000",
                }
            ]
        },
    )
    assert sheet.status_code == 200, sheet.text

    adjust = api_client.post(
        "/api/v1/inventory/adjustments",
        headers={**headers, **_key()},
        json={
            # Signed, and the direction is derived from the sign rather than from the
            # code — the contract `AdjustmentRequest` documents.
            "product_id": product["id"],
            "quantity_delta": "3.000",
            "reason_code": "adjustment_increase",
            "reason_text": "Found a box behind the counter",
        },
    )
    assert adjust.status_code in {200, 201}, adjust.text

    report = _report(api_client, headers, on_date=_today(api_client, headers), kind="added")
    reasons = [line["reason"] for line in report["items"]]

    assert "adjustment_increase" in reasons
    # The sheet books an arrival; on a product that already existed that is a correction
    # upwards, and on a new one an opening figure. Either way it increased stock.
    assert any(reason in {"opening", "adjustment_increase"} for reason in reasons)
    assert report["movements"] == len(report["items"]) >= 2


def test_several_arrivals_of_one_product_stay_separate_and_chain_correctly(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    supplier: str,
) -> None:
    """The case a naive report gets wrong: three receipts of the same part in one day.

    They must not be merged into one row — the owner needs to see each delivery — and the
    running balance must chain, each row starting where the previous one finished.
    """
    for index, quantity in enumerate(["5.000", "3.000", "2.000"]):
        _receive(api_client, headers, supplier, product["id"], quantity, document=f"DOC/{index}")

    report = _report(api_client, headers, on_date=_today(api_client, headers), kind="added")

    assert report["movements"] == 3
    # Three rows, but one product — the summary counts products, not rows.
    assert report["products"] == 1
    assert Decimal(report["total_quantity"]) == Decimal("10.000")

    befores = [Decimal(line["stock_before"]) for line in report["items"]]
    afters = [Decimal(line["stock_after"]) for line in report["items"]]
    assert befores == [Decimal("0.000"), Decimal("5.000"), Decimal("8.000")]
    assert afters == [Decimal("5.000"), Decimal("8.000"), Decimal("10.000")]
    # Each delivery keeps its own document rather than collapsing into one.
    assert [line["reference"] for line in report["items"]] == ["DOC/0", "DOC/1", "DOC/2"]


def test_every_added_row_satisfies_before_plus_quantity_equals_after(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    supplier: str,
) -> None:
    """The arithmetic the whole report rests on, asserted directly."""
    _receive(api_client, headers, supplier, product["id"], "7.000")

    report = _report(api_client, headers, on_date=_today(api_client, headers), kind="added")

    for line in report["items"]:
        assert Decimal(line["stock_before"]) + Decimal(line["quantity"]) == Decimal(
            line["stock_after"]
        )


# --- stock sold ---------------------------------------------------------------


def test_a_cash_sale_appears_once_with_its_invoice_number(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    product: dict[str, Any],
    supplier: str,
) -> None:
    _receive(api_client, headers, supplier, product["id"], "10.000")
    sale = _sell(api_client, headers, tenant, product["id"], "2.000")

    report = _report(api_client, headers, on_date=_today(api_client, headers), kind="sold")

    assert report["movements"] == 1, "a sale must deduct stock exactly once"
    assert report["products"] == 1
    assert report["invoices"] == 1
    assert Decimal(report["total_quantity"]) == Decimal("2.000")

    line = report["items"][0]
    assert Decimal(line["quantity"]) == Decimal("2.000")
    # Ten on the shelf, two sold, eight left — read from the ledger, not recomputed.
    assert Decimal(line["stock_before"]) == Decimal("10.000")
    assert Decimal(line["stock_after"]) == Decimal("8.000")
    assert line["reason"] == "sale"
    assert line["reference"] == sale["invoice"]["internal_reference"]
    assert line["is_credit"] is False
    assert line["invoice_cancelled"] is False


def test_a_credit_sale_is_marked_as_credit(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    product: dict[str, Any],
    supplier: str,
) -> None:
    _receive(api_client, headers, supplier, product["id"], "10.000")
    customer_id = str(uuid.uuid4())
    created = api_client.put(
        f"/api/v1/customers/{customer_id}",
        headers={**headers, **_key()},
        json={"name": "Rakesh Motors"},
    )
    assert created.status_code == 200, created.text

    _sell(
        api_client,
        headers,
        tenant,
        product["id"],
        "3.000",
        credit=True,
        customer_id=str(created.json()["customer"]["id"]),
    )

    report = _report(api_client, headers, on_date=_today(api_client, headers), kind="sold")

    assert report["movements"] == 1
    assert report["items"][0]["is_credit"] is True


def test_recording_a_payment_against_a_credit_bill_moves_no_stock(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    product: dict[str, Any],
    supplier: str,
) -> None:
    """Money changing hands is not goods changing hands.

    Structural rather than filtered: a payment writes no `stock_movements` row, so the
    count is unchanged by it whatever this endpoint does.
    """
    _receive(api_client, headers, supplier, product["id"], "10.000")
    customer_id = str(uuid.uuid4())
    created = api_client.put(
        f"/api/v1/customers/{customer_id}",
        headers={**headers, **_key()},
        json={"name": "Rakesh Motors"},
    )
    sale = _sell(
        api_client,
        headers,
        tenant,
        product["id"],
        "2.000",
        credit=True,
        customer_id=str(created.json()["customer"]["id"]),
    )
    today = _today(api_client, headers)
    before = _report(api_client, headers, on_date=today, kind="sold")

    payment = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={
            "customer_id": str(created.json()["customer"]["id"]),
            "amount": "2500.00",
            "method": "cash",
            "allocation": {
                "strategy": "invoice_specific",
                "allocations": [{"invoice_id": sale["invoice"]["id"], "amount": "2500.00"}],
            },
        },
    )
    assert payment.status_code in {200, 201}, payment.text

    after = _report(api_client, headers, on_date=today, kind="sold")
    assert after["movements"] == before["movements"]
    assert Decimal(after["total_quantity"]) == Decimal(before["total_quantity"])


def test_reprinting_a_bill_deducts_nothing_further(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    product: dict[str, Any],
    supplier: str,
) -> None:
    _receive(api_client, headers, supplier, product["id"], "10.000")
    sale = _sell(api_client, headers, tenant, product["id"], "2.000")
    today = _today(api_client, headers)

    reprint = api_client.post(
        f"/api/v1/invoices/{sale['invoice']['id']}/print-events",
        headers=headers,
        json={"is_duplicate": True},
    )
    assert reprint.status_code in {200, 201, 204}, reprint.text

    report = _report(api_client, headers, on_date=today, kind="sold")
    assert report["movements"] == 1
    assert Decimal(report["total_quantity"]) == Decimal("2.000")


def test_a_saved_draft_is_not_a_sale(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    product: dict[str, Any],
    supplier: str,
) -> None:
    """Drafts never reserve or deduct stock, so the report cannot show one."""
    _receive(api_client, headers, supplier, product["id"], "10.000")
    draft_id = str(uuid.uuid4())
    saved = api_client.put(
        f"/api/v1/drafts/{draft_id}",
        headers={**headers, **_key()},
        json={
            "counter_id": str(tenant.counter_id),
            "items": [
                {
                    "id": str(uuid.uuid4()),
                    "line_no": 1,
                    "product_id": product["id"],
                    "quantity": "4.000",
                    "unit_price": "1250.0000",
                    "price_source": "catalog",
                }
            ],
        },
    )
    assert saved.status_code in {200, 201}, saved.text

    report = _report(api_client, headers, on_date=_today(api_client, headers), kind="sold")
    assert report["movements"] == 0
    assert report["items"] == []


def test_each_sold_row_satisfies_before_minus_quantity_equals_after(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    product: dict[str, Any],
    supplier: str,
) -> None:
    """Three sales of one part in a day, each starting where the last finished."""
    _receive(api_client, headers, supplier, product["id"], "20.000")
    for quantity in ["2.000", "3.000", "1.000"]:
        _sell(api_client, headers, tenant, product["id"], quantity)

    report = _report(api_client, headers, on_date=_today(api_client, headers), kind="sold")

    assert report["movements"] == 3
    assert report["products"] == 1
    assert report["invoices"] == 3
    assert Decimal(report["total_quantity"]) == Decimal("6.000")

    for line in report["items"]:
        assert Decimal(line["stock_before"]) - Decimal(line["quantity"]) == Decimal(
            line["stock_after"]
        )
    assert [Decimal(line["stock_after"]) for line in report["items"]] == [
        Decimal("18.000"),
        Decimal("15.000"),
        Decimal("14.000"),
    ]


def test_a_sale_does_not_appear_on_the_added_report(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    product: dict[str, Any],
    supplier: str,
) -> None:
    _receive(api_client, headers, supplier, product["id"], "10.000")
    _sell(api_client, headers, tenant, product["id"], "2.000")

    added = _report(api_client, headers, on_date=_today(api_client, headers), kind="added")
    assert all(line["reason"] != "sale" for line in added["items"])
    # Only the receipt increased stock.
    assert Decimal(added["total_quantity"]) == Decimal("10.000")


# --- the day itself -----------------------------------------------------------


def test_a_day_with_nothing_on_it_reports_nothing_rather_than_failing(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    for kind in ("added", "sold"):
        report = _report(api_client, headers, on_date="2019-01-01", kind=kind)
        assert report["items"] == []
        assert report["products"] == 0
        assert report["movements"] == 0
        assert report["invoices"] == 0
        assert Decimal(report["total_quantity"]) == Decimal("0")
        assert report["truncated"] is False


def test_the_day_boundary_is_the_shops_own_evening_not_utcs(
    api_client: TestClient,
    headers: dict[str, str],
    migration_session: Session,
    product: dict[str, Any],
    supplier: str,
) -> None:
    """The test that fails if anyone truncates UTC.

    The tenant is `Asia/Kolkata` (UTC+5:30). A movement at **18:29 UTC** is 23:59 that
    evening in the shop, and one at **18:31 UTC** is 00:01 the next morning. Truncating
    UTC would put both on the same day and move the evening's takings into the wrong
    report — for every shop east of Greenwich, which is every shop this serves.
    """
    _receive(api_client, headers, supplier, product["id"], "5.000")

    movement_id = migration_session.execute(
        text("SELECT id FROM stock_movements WHERE product_id = :p LIMIT 1"),
        {"p": product["id"]},
    ).scalar_one()

    for stamp, expected_day, other_day in (
        ("2026-03-15 18:29:00+00", "2026-03-15", "2026-03-16"),
        ("2026-03-15 18:31:00+00", "2026-03-16", "2026-03-15"),
    ):
        migration_session.execute(
            text("UPDATE stock_movements SET created_at = :t WHERE id = :id"),
            {"t": stamp, "id": movement_id},
        )
        migration_session.commit()

        on_day = _report(api_client, headers, on_date=expected_day, kind="added")
        off_day = _report(api_client, headers, on_date=other_day, kind="added")

        assert on_day["movements"] == 1, f"{stamp} should fall on {expected_day}"
        assert off_day["movements"] == 0, f"{stamp} must not fall on {other_day}"


def test_rows_are_ordered_oldest_first_and_identically_on_every_request(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    supplier: str,
) -> None:
    """Deterministic ordering: a report the owner refreshes must not reshuffle."""
    for index in range(4):
        _receive(api_client, headers, supplier, product["id"], "1.000", document=f"D/{index}")

    today = _today(api_client, headers)
    first = _report(api_client, headers, on_date=today, kind="added")
    second = _report(api_client, headers, on_date=today, kind="added")

    assert [line["movement_id"] for line in first["items"]] == [
        line["movement_id"] for line in second["items"]
    ]
    stamps = [line["created_at"] for line in first["items"]]
    assert stamps == sorted(stamps)
    assert [line["reference"] for line in first["items"]] == ["D/0", "D/1", "D/2", "D/3"]


# --- scope and access ---------------------------------------------------------


def test_another_shops_movements_are_not_in_this_shops_report(
    api_client: TestClient,
    headers: dict[str, str],
    migration_session: Session,
    product: dict[str, Any],
    supplier: str,
) -> None:
    """Tenant isolation, asserted on the report rather than assumed from the query."""
    _receive(api_client, headers, supplier, product["id"], "6.000")

    other = create_tenant(migration_session, display_name="SOMEBODY ELSE")
    other_user = create_user(
        migration_session, other, username="other-owner", role=SystemRole.OWNER, all_branches=True
    )
    migration_session.commit()

    login = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": other_user.username,
            "password": other_user.password,
            "device": {"id": str(other_user.device_id), "label": "Their PC"},
        },
    )
    assert login.status_code == 200, login.text
    their_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    theirs = _report(api_client, their_headers, on_date=_today(api_client, headers), kind="added")
    assert theirs["items"] == []
    assert theirs["movements"] == 0

    # And ours is unaffected by theirs existing.
    ours = _report(api_client, headers, on_date=_today(api_client, headers), kind="added")
    assert ours["movements"] == 1


def test_an_unauthenticated_request_is_refused(api_client: TestClient) -> None:
    response = api_client.get(REPORT, params={"on_date": "2026-01-01", "kind": "added"})
    assert response.status_code == 401


def test_a_kind_that_is_neither_added_nor_sold_is_rejected(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """The parameter is constrained at the edge, so the service never sees a third kind
    and no SQL is built from an unexpected value."""
    response = api_client.get(
        REPORT, headers=headers, params={"on_date": "2026-01-01", "kind": "everything"}
    )
    assert response.status_code == 422


def test_a_missing_date_is_rejected_rather_than_guessed(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    assert api_client.get(REPORT, headers=headers, params={"kind": "added"}).status_code == 422
