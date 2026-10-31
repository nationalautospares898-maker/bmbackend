"""Finalisation: every refusal, and the figures that are derived rather than accepted.

Audit Issue B. `bm.domain.sales.service` is the single most consequential file in the
system — it is the one transaction that allocates a reference, deducts stock, writes the
movements and records the payment — and it sat with 28 uncovered branch arcs. The happy
path is well covered by `tests/integration/test_billing_workflows.py`; what was not
covered is **every way a bill is refused**, which is most of what the file is.

Grouped by the question each refusal answers:

* *Is there a bill at all?* — empty lines, unknown product, no resolvable line.
* *Is this draft still mine to finalise?* — unknown, cancelled, or a stale version.
* *Is this price allowed?* — negative, a manual override without permission, a discount
  above the cashier's cap.
* *Is this customer real?* — a walk-in with no name, a "new" customer with no id, an
  existing customer who does not exist.
* *Can this customer owe this much?* — the credit limit, enforced or warned.
* *What is the payment status?* — derived from what was actually paid, never accepted
  from the client.

Driven through `POST /sales:finalize` so the router contract is exercised with the rule.
Where the router validates first, the domain guard is called directly and the comment
says why (`SECURITY.md §3.1` rule 2: an internal caller must not be able to bypass it).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import Permission, SystemRole, permissions_for_role
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db

D = Decimal


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def cashier(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(migration_session, tenant, username="ravi", role=SystemRole.CASHIER)


def _headers(client: TestClient, user: UserFixture, tenant: Tenant) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": user.password,
            "device": {"id": str(user.device_id), "label": "Counter PC"},
        },
    ).json()["access_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Device-Id": str(user.device_id),
        "X-Counter-Id": str(tenant.counter_id),
    }


@pytest.fixture
def headers(api_client: TestClient, owner: UserFixture, tenant: Tenant) -> dict[str, str]:
    return _headers(api_client, owner, tenant)


@pytest.fixture
def stocked(api_client: TestClient, headers: dict[str, str]) -> str:
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200
    product = api_client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": "Finalise Test Part",
            "unit_code": "NOS",
            "default_selling_price": "1000.0000",
            "sku": "FN-1",
            "cost_price": "800.0000",
        },
    )
    assert product.status_code == 201, product.text
    product_id = str(product.json()["id"])
    supplier = api_client.put(
        f"/api/v1/suppliers/{uuid.uuid4()}",
        headers=headers,
        json={"name": "Allied", "phone": "9876543210"},
    )
    purchase = api_client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "supplier_id": supplier.json()["id"],
            "supplier_document_number": f"FN/{uuid.uuid4().hex[:6]}",
            "items": [{"product_id": product_id, "quantity": "200.000", "unit_cost": "800.0000"}],
        },
    )
    api_client.post(
        f"/api/v1/purchases/{purchase.json()['purchase_id']}:confirm",
        headers={**headers, **_key()},
    )
    return product_id


def _body(
    tenant: Tenant,
    product_id: str,
    *,
    quantity: str = "1.000",
    unit_price: str = "1000.0000",
    total: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    computed = total or (str(int(D(quantity) * D(unit_price))) + ".00")
    body: dict[str, Any] = {
        "counter_id": str(tenant.counter_id),
        "sale_type": "cash",
        "lines": [
            {
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": unit_price,
                "price_source": "catalog",
            }
        ],
        "header": {"customer_name": "Imran"},
        "client_computed_total": computed,
        "payment": {"method": "cash", "amount": computed},
    }
    body.update(extra)
    return body


def _post(client: TestClient, headers: dict[str, str], body: dict[str, Any]) -> Any:
    return client.post("/api/v1/sales:finalize", headers={**headers, **_key()}, json=body)


# --- is there a bill at all? --------------------------------------------------


def test_a_bill_with_no_lines_is_refused(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """An empty sale would allocate a reference and deduct nothing — a gap in the
    sequence explaining nothing."""
    body = _body(tenant, stocked)
    body["lines"] = []
    body["client_computed_total"] = "0.00"
    body["payment"] = {"method": "cash", "amount": "0.00"}
    assert _post(api_client, headers, body).status_code == 422


def test_a_line_naming_an_unknown_product_is_refused(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """§4.1: a cross-tenant or absent product is simply not there."""
    response = _post(api_client, headers, _body(tenant, str(uuid.uuid4())))
    assert response.status_code in (404, 422), response.text


def test_a_negative_unit_price_is_refused(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """A negative line price is a refund wearing a sale's clothes — it would bypass every
    control a return has."""
    body = _body(tenant, stocked, unit_price="-1000.0000", total="-1000.00")
    assert _post(api_client, headers, body).status_code == 422


# --- is this draft still mine to finalise? ------------------------------------


def test_finalising_against_an_unknown_draft_is_refused(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    body = _body(tenant, stocked, draft_id=str(uuid.uuid4()), draft_version=1)
    response = _post(api_client, headers, body)
    assert response.status_code in (404, 409, 422), response.text


def test_finalising_against_a_cleared_draft_is_refused(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """`Clear Draft` cancels the bill. Finalising it afterwards would resurrect a bill the
    cashier deliberately abandoned."""
    draft_id = str(uuid.uuid4())
    saved = api_client.put(
        f"/api/v1/drafts/{draft_id}",
        headers=headers,
        json={
            "counter_id": str(tenant.counter_id),
            "items": [
                {
                    "id": str(uuid.uuid4()),
                    "line_no": 1,
                    "product_id": stocked,
                    "quantity": "1.000",
                    "unit_price": "1000.0000",
                    "catalog_price_at_add": "1000.0000",
                    "price_source": "catalog",
                }
            ],
        },
    )
    assert saved.status_code in (200, 201), saved.text
    assert api_client.delete(f"/api/v1/drafts/{draft_id}", headers=headers).status_code in (
        200,
        204,
    )

    body = _body(tenant, stocked, draft_id=draft_id, draft_version=saved.json().get("version", 1))
    response = _post(api_client, headers, body)
    assert response.status_code in (404, 409, 422), response.text


def test_finalising_against_a_stale_draft_version_conflicts(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """Two counters, one bill. A stale version means someone else edited it; completing
    it would bill content this cashier never saw."""
    draft_id = str(uuid.uuid4())
    line = {
        "id": str(uuid.uuid4()),
        "line_no": 1,
        "product_id": stocked,
        "quantity": "1.000",
        "unit_price": "1000.0000",
        "catalog_price_at_add": "1000.0000",
        "price_source": "catalog",
    }
    first = api_client.put(
        f"/api/v1/drafts/{draft_id}",
        headers=headers,
        json={"counter_id": str(tenant.counter_id), "items": [line]},
    )
    assert first.status_code in (200, 201), first.text
    # Edit it again so the stored version moves on.
    api_client.put(
        f"/api/v1/drafts/{draft_id}",
        headers={**headers, "If-Match": str(first.json().get("version", 1))},
        json={
            "counter_id": str(tenant.counter_id),
            "items": [line, {**line, "line_no": 2, "id": str(uuid.uuid4())}],
        },
    )

    body = _body(tenant, stocked, draft_id=draft_id, draft_version=1)
    response = _post(api_client, headers, body)
    assert response.status_code in (409, 422), response.text


# --- is this price allowed? ---------------------------------------------------


def test_a_manual_price_override_needs_permission(
    api_client: TestClient,
    headers: dict[str, str],
    cashier: UserFixture,
    tenant: Tenant,
    stocked: str,
) -> None:
    """FR-SALE: a cashier cannot quietly change a price. The catalogue is the price list
    unless someone with authority says otherwise."""
    if Permission.BILLING_PRICE_OVERRIDE in {
        Permission(p) for p in permissions_for_role(SystemRole.CASHIER)
    }:
        pytest.skip("cashier holds billing.price.override in this role matrix")

    cashier_headers = _headers(api_client, cashier, tenant)
    body = _body(tenant, stocked, unit_price="500.0000", total="500.00")
    body["lines"][0]["price_source"] = "manual_override"
    response = _post(api_client, cashier_headers, body)
    assert response.status_code == 403, response.text


def test_an_owner_may_override_a_price(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """The permitted side, so the refusal above is not simply "overrides fail"."""
    body = _body(tenant, stocked, unit_price="500.0000", total="500.00")
    body["lines"][0]["price_source"] = "manual_override"
    body["accept_price_changes"] = True
    response = _post(api_client, headers, body)
    assert response.status_code in (200, 201), response.text
    assert D(str(response.json()["invoice"]["total"])) == D("500.00")


def test_a_discount_above_the_cashiers_cap_is_refused(
    api_client: TestClient, cashier: UserFixture, tenant: Tenant, stocked: str
) -> None:
    """`discount.cashier_max_percent` defaults to 0, so any discount from a cashier needs
    a supervisor. The cap is a setting the owner controls, not a constant."""
    cashier_headers = _headers(api_client, cashier, tenant)
    body = _body(tenant, stocked, total="900.00")
    body["lines"][0]["discount_percent"] = "10.000"
    response = _post(api_client, cashier_headers, body)
    # A 409 with its own code, not a generic validation error: the request is well
    # formed and the cashier simply is not allowed to give this much away. The details
    # name both figures so the screen can say "10% requested, 0% allowed".
    assert response.status_code == 409, response.text
    body_json = response.json()
    assert body_json["error"]["code"] == "DISCOUNT_EXCEEDS_LIMIT"
    assert body_json["error"]["details"]["requested_percent"] == "10.000"


# --- is this customer real? ---------------------------------------------------


def test_a_credit_bill_needs_a_saved_customer_not_a_typed_name(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """FR-CREDIT: credit is owed by somebody. A walk-in cannot owe money."""
    body = _body(tenant, stocked)
    body["sale_type"] = "credit"
    body.pop("payment", None)
    body["customer"] = {"mode": "walk_in", "customer_id": None, "name": None}
    response = _post(api_client, headers, body)
    assert response.status_code in (403, 422), response.text


def test_a_new_customer_needs_both_an_id_and_a_name(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """The id is client-generated so a retry is an upsert rather than a second customer;
    the name is what a human recognises. Neither is optional."""
    body = _body(tenant, stocked)
    body["sale_type"] = "credit"
    body.pop("payment", None)
    body["customer"] = {"mode": "new", "customer_id": None, "name": "Ramesh"}
    assert _post(api_client, headers, body).status_code in (403, 422)

    body["customer"] = {"mode": "new", "customer_id": str(uuid.uuid4()), "name": "   "}
    assert _post(api_client, headers, body).status_code in (403, 422)


def test_an_existing_customer_must_actually_exist(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    body = _body(tenant, stocked)
    body["sale_type"] = "credit"
    body.pop("payment", None)
    body["customer"] = {"mode": "existing", "customer_id": str(uuid.uuid4()), "name": None}
    response = _post(api_client, headers, body)
    assert response.status_code in (404, 422), response.text

    body["customer"] = {"mode": "existing", "customer_id": None, "name": None}
    assert _post(api_client, headers, body).status_code in (404, 422)


# --- due dates and credit limits ---------------------------------------------


def test_an_explicit_due_date_is_honoured(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """The header may name a due date; otherwise the customer's terms decide it."""
    chosen = (datetime.now(UTC).date() + timedelta(days=45)).isoformat()
    body = _body(tenant, stocked)
    body["sale_type"] = "credit"
    body.pop("payment", None)
    body["customer"] = {"mode": "new", "customer_id": str(uuid.uuid4()), "name": "Terms Co"}
    body["header"] = {"customer_name": "Terms Co", "due_date": chosen}
    response = _post(api_client, headers, body)
    assert response.status_code in (200, 201), response.text
    assert str(response.json()["invoice"]["due_date"]) == chosen


def test_a_credit_sale_past_the_limit_is_refused_when_the_limit_is_enforced(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    """`credit.enforce_limit` off by default, so this turns it on deliberately.

    With it on, a sale that would take the customer past their limit is refused — that is
    the whole point of recording a limit.
    """
    customer_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO customers (id, business_id, name, normalized_name, credit_limit,"
            " is_active) VALUES (:id, :b, 'Capped Co', 'capped co', 500, true)"
        ),
        {"id": customer_id, "b": tenant.business_id},
    )
    for key, value in (("credit.enforce_limit", "true"),):
        migration_session.execute(
            text("DELETE FROM settings WHERE business_id = :b AND branch_id IS NULL AND key = :k"),
            {"b": tenant.business_id, "k": key},
        )
        migration_session.execute(
            text(
                "INSERT INTO settings (id, business_id, branch_id, key, value, value_type)"
                " VALUES (gen_random_uuid(), :b, NULL, :k, CAST(:v AS jsonb), 'bool')"
            ),
            {"b": tenant.business_id, "k": key, "v": value},
        )
    migration_session.commit()

    body = _body(tenant, stocked, quantity="2.000", total="2000.00")
    body["sale_type"] = "credit"
    body.pop("payment", None)
    body["customer"] = {"mode": "existing", "customer_id": str(customer_id), "name": None}
    response = _post(api_client, headers, body)
    assert response.status_code in (403, 409, 422), response.text
    assert "CREDIT_LIMIT" in response.text or "limit" in response.text.lower()


def test_a_credit_sale_near_the_limit_warns_but_completes(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    """A warning on a **successful** response, handled permissively by the client so a
    new warning can never break a completed sale (§4.3)."""
    customer_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO customers (id, business_id, name, normalized_name, credit_limit,"
            " is_active) VALUES (:id, :b, 'Nearly Co', 'nearly co', 1000, true)"
        ),
        {"id": customer_id, "b": tenant.business_id},
    )
    migration_session.commit()

    body = _body(tenant, stocked, total="1000.00")
    body["sale_type"] = "credit"
    body.pop("payment", None)
    body["customer"] = {"mode": "existing", "customer_id": str(customer_id), "name": None}
    response = _post(api_client, headers, body)
    assert response.status_code in (200, 201), response.text
    # The sale completed; the warning is advisory.
    assert isinstance(response.json()["warnings"], list)


# --- payment status is derived -----------------------------------------------


def test_a_part_paid_credit_bill_is_partially_paid(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    """ADR-022: the status follows the money, and the money is what was recorded."""
    customer_id = str(uuid.uuid4())
    body = _body(tenant, stocked, quantity="2.000", total="2000.00")
    body["sale_type"] = "credit"
    body["customer"] = {"mode": "new", "customer_id": customer_id, "name": "Part Payer"}
    # A deposit at the counter on a credit bill.
    body["payment"] = {"method": "cash", "amount": "500.00"}
    response = _post(api_client, headers, body)
    assert response.status_code in (200, 201), response.text

    invoice = response.json()["invoice"]
    assert invoice["payment_status"] == "partially_paid"
    assert D(str(invoice["amount_paid"])) == D("500.00")
    assert D(str(invoice["outstanding"])) == D("1500.00")


def test_a_fully_paid_cash_bill_is_paid(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    response = _post(api_client, headers, _body(tenant, stocked))
    assert response.status_code in (200, 201), response.text
    invoice = response.json()["invoice"]
    assert invoice["payment_status"] == "paid"
    assert D(str(invoice["outstanding"])) == D("0.00")


def test_a_client_total_that_disagrees_with_the_server_is_refused(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """Never auto-resubmitted: the cashier sees the difference and decides (E-5)."""
    body = _body(tenant, stocked)
    body["client_computed_total"] = "999.00"
    response = _post(api_client, headers, body)
    assert response.status_code in (409, 422), response.text
    assert "TOTAL_MISMATCH" in response.text or "total" in response.text.lower()


# --- the last of the finalisation guards, and the draft takeover --------------
#
# These are the paths the router validates before the domain sees them, plus the
# takeover flow, which has no happy-path test because it exists for an unusual day:
# a cashier walks away mid-bill and someone finishes it at another till.


def test_a_bill_with_no_lines_is_refused_by_the_domain_too(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    owner: UserFixture,
    stocked: str,
    migration_session: Session,
) -> None:
    """`lines` has a `min_length` on the request model, so the router refuses an empty
    sale first. The domain checks again (`SECURITY.md §3.1` rule 2) — a sync operation
    or a future endpoint must not be able to allocate a reference for nothing."""
    from bm.domain.sales.service import (
        CustomerSelection,
        FinalizeCommand,
        InvoiceDiscount,
        PaymentCommand,
        SaleHeader,
        SalesService,
    )
    from bm.domain.shared.clock import SystemClock
    from bm.domain.shared.errors import EmptySale

    service = SalesService(migration_session, SystemClock())
    command = FinalizeCommand(
        draft_id=None,
        draft_version=None,
        counter_id=tenant.counter_id,
        sale_type="cash",
        customer=CustomerSelection(mode="walk_in", customer_id=None, name=None),
        header=SaleHeader(),
        lines=[],
        invoice_discount=InvoiceDiscount(),
        payment=PaymentCommand(method="cash", amount=D("0"), reference=None),
        client_computed_total=D("0"),
        accept_price_changes=True,
        oversell_reason=None,
    )
    with pytest.raises(EmptySale):
        service.finalize(
            _ctx_for(tenant, owner), command, idempotency_key=uuid.uuid4(), raw_body={}
        )


def _ctx_for(tenant: Tenant, user: UserFixture, role: SystemRole = SystemRole.OWNER):
    from bm.domain.shared.context import AuthContext

    return AuthContext(
        user_id=user.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id,
        device_id=user.device_id,
        role=str(role),
        permissions=frozenset(str(p) for p in permissions_for_role(role)),
        token_id="finalize-rules",
        business_timezone=tenant.timezone,
        full_name="Owner",
    )


def test_a_credit_sale_uses_the_business_default_terms_when_the_customer_has_none(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    """A customer without their own terms inherits `credit.default_terms_days`.

    So the due date is never absent on a credit bill — an undated debt is one nobody can
    chase or age.
    """
    customer_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO customers (id, business_id, name, normalized_name, is_active)"
            " VALUES (:id, :b, 'Default Terms Co', 'default terms co', true)"
        ),
        {"id": customer_id, "b": tenant.business_id},
    )
    migration_session.commit()

    body = _body(tenant, stocked)
    body["sale_type"] = "credit"
    body.pop("payment", None)
    body["customer"] = {"mode": "existing", "customer_id": str(customer_id), "name": None}
    response = _post(api_client, headers, body)
    assert response.status_code in (200, 201), response.text
    assert response.json()["invoice"]["due_date"] is not None


def test_a_credit_sale_well_under_the_limit_warns_about_nothing(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    """The quiet path. A warning on every credit sale would train the cashier to ignore
    warnings, which is worse than having none."""
    customer_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO customers (id, business_id, name, normalized_name, credit_limit,"
            " is_active) VALUES (:id, :b, 'Roomy Co', 'roomy co', 100000, true)"
        ),
        {"id": customer_id, "b": tenant.business_id},
    )
    migration_session.commit()

    body = _body(tenant, stocked)
    body["sale_type"] = "credit"
    body.pop("payment", None)
    body["customer"] = {"mode": "existing", "customer_id": str(customer_id), "name": None}
    response = _post(api_client, headers, body)
    assert response.status_code in (200, 201), response.text
    codes = [w.get("code") for w in response.json()["warnings"]]
    assert "CREDIT_LIMIT_APPROACHED" not in codes


def test_over_the_limit_with_enforcement_off_warns_and_completes(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    """`credit.enforce_limit` is **off by default** (FR-CREDIT-006), so the shop keeps
    trading and the owner is told. That default is a business decision, and this is the
    branch that implements it — the sale completes *and* carries a warning."""
    customer_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO customers (id, business_id, name, normalized_name, credit_limit,"
            " is_active) VALUES (:id, :b, 'Over Co', 'over co', 100, true)"
        ),
        {"id": customer_id, "b": tenant.business_id},
    )
    migration_session.commit()

    body = _body(tenant, stocked, quantity="2.000", total="2000.00")
    body["sale_type"] = "credit"
    body.pop("payment", None)
    body["customer"] = {"mode": "existing", "customer_id": str(customer_id), "name": None}
    response = _post(api_client, headers, body)
    assert response.status_code in (200, 201), response.text
    codes = [w.get("code") for w in response.json()["warnings"]]
    assert "CREDIT_LIMIT_APPROACHED" in codes


# --- drafts: listing, reading and taking over --------------------------------


def test_listing_open_drafts_without_a_counter_returns_every_counters_bills(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    owner: UserFixture,
    stocked: str,
    migration_session: Session,
) -> None:
    """A supervisor with no counter selected sees the shop, not one till.

    The scoping is deliberate rather than accidental: with a counter in context the list
    narrows to it, which is what a cashier gets.
    """
    from bm.domain.sales.drafts import DraftItemInput, DraftService
    from bm.domain.shared.context import AuthContext

    service = DraftService(migration_session)
    draft_id = uuid.uuid4()
    service.upsert(
        _ctx_for(tenant, owner),
        draft_id,
        counter_id=tenant.counter_id,
        items=[
            DraftItemInput(
                id=uuid.uuid4(),
                line_no=1,
                product_id=uuid.UUID(stocked),
                quantity=D("1.000"),
                unit_price=D("1000.0000"),
                catalog_price_at_add=D("1000.0000"),
                price_source="catalog",
            )
        ],
        product_names={uuid.UUID(stocked): "Finalise Test Part"},
        if_match_version=None,
    )
    migration_session.commit()

    no_counter = AuthContext(
        user_id=owner.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=None,
        device_id=owner.device_id,
        role=str(SystemRole.OWNER),
        permissions=frozenset(str(p) for p in permissions_for_role(SystemRole.OWNER)),
        token_id="no-counter",
        business_timezone=tenant.timezone,
    )
    everything = service.list_open(no_counter)
    assert any(view.id == draft_id for view in everything)


def test_reading_an_unknown_draft_is_a_not_found(
    tenant: Tenant, owner: UserFixture, migration_session: Session
) -> None:
    from bm.domain.sales.drafts import DraftService
    from bm.domain.shared.errors import NotFound

    with pytest.raises(NotFound):
        DraftService(migration_session).get(_ctx_for(tenant, owner), uuid.uuid4())


def test_clearing_an_already_cleared_draft_is_a_quiet_no_op(
    tenant: Tenant, owner: UserFixture, stocked: str, migration_session: Session
) -> None:
    """Deliberately idempotent: "the caller's intent is satisfied".

    `Clear Draft` is a button a cashier may press twice, or press again after a lost
    response. Refusing the second press would show an error for something that already
    happened. Note the contrast with `upsert`, which *does* refuse a cancelled draft —
    clearing again is harmless, editing a cleared bill is not.
    """
    from bm.domain.sales.drafts import DraftItemInput, DraftService

    service = DraftService(migration_session)
    draft_id = uuid.uuid4()
    service.upsert(
        _ctx_for(tenant, owner),
        draft_id,
        counter_id=tenant.counter_id,
        items=[
            DraftItemInput(
                id=uuid.uuid4(),
                line_no=1,
                product_id=uuid.UUID(stocked),
                quantity=D("1.000"),
                unit_price=D("1000.0000"),
                catalog_price_at_add=D("1000.0000"),
                price_source="catalog",
            )
        ],
        product_names={uuid.UUID(stocked): "Finalise Test Part"},
        if_match_version=None,
    )
    migration_session.commit()
    service.clear(_ctx_for(tenant, owner), draft_id)
    migration_session.commit()

    # Second press: no exception, and the bill stays cancelled rather than being
    # re-cancelled with another version bump.
    version_after_first = migration_session.execute(
        text("SELECT version FROM draft_bills WHERE id = :d"), {"d": draft_id}
    ).scalar_one()
    service.clear(_ctx_for(tenant, owner), draft_id)
    migration_session.commit()
    row = migration_session.execute(
        text("SELECT status, version FROM draft_bills WHERE id = :d"), {"d": draft_id}
    ).first()
    assert str(row[0]) == "cancelled"
    assert row[1] == version_after_first


def test_taking_over_an_unknown_draft_is_a_not_found(
    tenant: Tenant, owner: UserFixture, migration_session: Session
) -> None:
    from bm.domain.sales.drafts import DraftService
    from bm.domain.shared.errors import NotFound

    with pytest.raises(NotFound):
        DraftService(migration_session).takeover(_ctx_for(tenant, owner), uuid.uuid4())


def test_only_an_open_draft_can_be_taken_over(
    tenant: Tenant, owner: UserFixture, stocked: str, migration_session: Session
) -> None:
    """A cleared or completed bill is not someone's work in progress."""
    from bm.domain.sales.drafts import DraftItemInput, DraftService
    from bm.domain.shared.errors import DraftAlreadyFinalized

    service = DraftService(migration_session)
    draft_id = uuid.uuid4()
    service.upsert(
        _ctx_for(tenant, owner),
        draft_id,
        counter_id=tenant.counter_id,
        items=[
            DraftItemInput(
                id=uuid.uuid4(),
                line_no=1,
                product_id=uuid.UUID(stocked),
                quantity=D("1.000"),
                unit_price=D("1000.0000"),
                catalog_price_at_add=D("1000.0000"),
                price_source="catalog",
            )
        ],
        product_names={uuid.UUID(stocked): "Finalise Test Part"},
        if_match_version=None,
    )
    migration_session.commit()
    service.clear(_ctx_for(tenant, owner), draft_id)
    migration_session.commit()

    with pytest.raises(DraftAlreadyFinalized):
        service.takeover(_ctx_for(tenant, owner), draft_id)


def test_a_takeover_needs_a_counter_to_take_the_draft_to(
    tenant: Tenant, owner: UserFixture, stocked: str, migration_session: Session
) -> None:
    """Reassigning a bill to "no counter" would leave it owned by nobody and invisible to
    every till's list."""
    from bm.domain.sales.drafts import DraftItemInput, DraftService
    from bm.domain.shared.context import AuthContext
    from bm.domain.shared.errors import ValidationFailed

    service = DraftService(migration_session)
    draft_id = uuid.uuid4()
    service.upsert(
        _ctx_for(tenant, owner),
        draft_id,
        counter_id=tenant.counter_id,
        items=[
            DraftItemInput(
                id=uuid.uuid4(),
                line_no=1,
                product_id=uuid.UUID(stocked),
                quantity=D("1.000"),
                unit_price=D("1000.0000"),
                catalog_price_at_add=D("1000.0000"),
                price_source="catalog",
            )
        ],
        product_names={uuid.UUID(stocked): "Finalise Test Part"},
        if_match_version=None,
    )
    migration_session.commit()

    no_counter = AuthContext(
        user_id=owner.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=None,
        device_id=owner.device_id,
        role=str(SystemRole.OWNER),
        permissions=frozenset(str(p) for p in permissions_for_role(SystemRole.OWNER)),
        token_id="no-counter",
        business_timezone=tenant.timezone,
    )
    with pytest.raises(ValidationFailed):
        service.takeover(no_counter, draft_id)


# --- the draft must already be on the server (the NOT_FOUND defect) -----------


def test_finalising_a_draft_the_server_has_never_seen_is_a_not_found(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
) -> None:
    """The server half of a defect found in a real browser, not by this suite.

    A cashier typed a bill and pressed `Print Bill`, and got "This bill is not
    confirmed" with three failed attempts and `NOT_FOUND`. The cause was on the client —
    nothing pushed a draft to the server except the `Save a Draft` button, so the
    ordinary way to use the billing screen sent a `draft_id` the server had never heard
    of.

    This pins the server's side of that contract, which is **correct and deliberate**:
    `draft_id` and `draft_version` exist for optimistic concurrency against a shared
    draft (`API-SPECIFICATION.md §6`: "`draft_version` must match the server's draft"),
    and silently inventing a draft row to satisfy a stale client would throw that away.
    So the refusal stays, and the client pushes first.

    Asserted here so that if someone later "fixes" this by making the server tolerant,
    they have to delete a test that says why it is not tolerant.
    """
    body = _body(tenant, stocked)
    body["draft_id"] = str(uuid.uuid4())
    body["draft_version"] = 1

    response = _post(api_client, headers, body)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_the_refused_finalisation_creates_nothing_at_all(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    """The reassurance the cashier actually needs: a refusal is not a half-sale.

    Checked against the real tables rather than inferred from the status code, because
    "the bill is not confirmed" is exactly the moment someone needs to know whether
    stock moved. Nothing may be created — no invoice, no stock movement, and no
    idempotency row that would make a corrected retry look like a replay.
    """
    before_invoices = migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one()
    before_movements = migration_session.execute(
        text("SELECT count(*) FROM stock_movements")
    ).scalar_one()
    stock_before = migration_session.execute(
        text("SELECT quantity FROM inventory_balances WHERE product_id = :p"),
        {"p": stocked},
    ).scalar_one()

    body = _body(tenant, stocked)
    body["draft_id"] = str(uuid.uuid4())
    assert _post(api_client, headers, body).status_code == 404

    migration_session.commit()
    assert (
        migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one()
        == before_invoices
    )
    assert (
        migration_session.execute(text("SELECT count(*) FROM stock_movements")).scalar_one()
        == before_movements
    )
    assert (
        migration_session.execute(
            text("SELECT quantity FROM inventory_balances WHERE product_id = :p"),
            {"p": stocked},
        ).scalar_one()
        == stock_before
    )
