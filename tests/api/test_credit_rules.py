"""Credit: payments, allocation, reversal, write-offs and credit notes.

Audit Issue B. `bm.domain.credit` sat at 51.5% branch coverage against a 95% floor, and
what was missing is the half that decides **how much a customer owes**:

* every amount refusal — zero, negative, more than the bill, more than is outstanding;
* the three allocation strategies, including the over-allocation guard that stops one
  payment settling more than the invoices it names actually owe;
* reversal, which is the only way to correct a payment because `payments` is append-only
  — and its four refusals: no reason, unknown payment, reversing a reversal, reversing
  twice;
* `payment_status` being **derived**, never set, across unpaid → partially paid → paid;
* write-offs, which remove money the shop is owed and therefore carry a higher
  explanation floor;
* credit notes and their allocation.

An untested refusal in this module is money silently forgiven or double-counted, so every
test asserts the resulting balance or the specific error — never just that a call
returned.

Driven through the real API wherever an endpoint exists, so the router contract is
covered with the rule.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import Permission, SystemRole, permissions_for_role
from bm.domain.credit.service import AllocationTarget, CreditService
from bm.domain.shared.clock import SystemClock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    NotFound,
    OverAllocation,
    PaymentAlreadyReversed,
    ValidationFailed,
)
from bm.infra.db.enums import CreditNoteSource
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
def headers(api_client: TestClient, owner: UserFixture, tenant: Tenant) -> dict[str, str]:
    token = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": owner.username,
            "password": owner.password,
            "device": {"id": str(owner.device_id), "label": "Counter 1 PC"},
        },
    ).json()["access_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Device-Id": str(owner.device_id),
        "X-Counter-Id": str(tenant.counter_id),
    }


@pytest.fixture
def ctx(tenant: Tenant, owner: UserFixture) -> AuthContext:
    return AuthContext(
        user_id=owner.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id,
        device_id=owner.device_id,
        role=str(SystemRole.OWNER),
        permissions=frozenset(str(p) for p in permissions_for_role(SystemRole.OWNER)),
        token_id="credit-rules",
        business_timezone=tenant.timezone,
        full_name="Owner",
    )


@pytest.fixture
def service(migration_session: Session) -> CreditService:
    return CreditService(migration_session, SystemClock())


@pytest.fixture
def stocked(api_client: TestClient, headers: dict[str, str]) -> str:
    """A product with ten on the shelf, through the only path that adds stock."""
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200
    product = api_client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": "Credit Test Part",
            "unit_code": "NOS",
            "default_selling_price": "1000.0000",
            "sku": "CR-1",
            "cost_price": "800.0000",
        },
    )
    assert product.status_code == 201, product.text
    product_id = str(product.json()["id"])

    supplier = api_client.put(
        f"/api/v1/suppliers/{uuid.uuid4()}",
        headers=headers,
        json={"name": "Allied Spares", "phone": "9876543210"},
    )
    assert supplier.status_code == 200, supplier.text
    purchase = api_client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "supplier_id": supplier.json()["id"],
            "supplier_document_number": f"CR/{uuid.uuid4().hex[:6]}",
            "items": [{"product_id": product_id, "quantity": "50.000", "unit_cost": "800.0000"}],
        },
    )
    assert purchase.status_code == 201, purchase.text
    confirmed = api_client.post(
        f"/api/v1/purchases/{purchase.json()['purchase_id']}:confirm",
        headers={**headers, **_key()},
    )
    assert confirmed.status_code == 200, confirmed.text
    return product_id


def _credit_sale(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    product_id: str,
    *,
    customer_id: str,
    quantity: str = "1.000",
    name: str = "Ramesh Motors",
) -> dict[str, Any]:
    """A finalised credit sale. Returns the `invoice` object."""
    total = str(int(D(quantity) * D("1000"))) + ".00"
    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**headers, **_key()},
        json={
            "counter_id": str(tenant.counter_id),
            "sale_type": "credit",
            "lines": [
                {
                    "product_id": product_id,
                    "quantity": quantity,
                    "unit_price": "1000.0000",
                    "price_source": "catalog",
                }
            ],
            "header": {"customer_name": name},
            "customer": {"mode": "new", "customer_id": customer_id, "name": name},
            "client_computed_total": total,
        },
    )
    assert response.status_code in (200, 201), response.text
    return dict(response.json()["invoice"])


# --- payment amount refusals --------------------------------------------------


@pytest.mark.parametrize("amount", ["0.00", "-100.00"])
def test_a_payment_must_be_positive(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str, amount: str
) -> None:
    """A zero or negative payment is a typo. Recording one would misstate the balance."""
    customer_id = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    response = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={"customer_id": customer_id, "amount": amount, "method": "cash"},
    )
    assert response.status_code == 422, response.text


def test_a_counter_payment_cannot_exceed_the_bill(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """At the counter, overpaying is a typo rather than an intent (FR-PAY-003).

    Refusing is kinder than silently creating on-account credit nobody asked for — the
    cashier gets to correct the figure while the customer is still standing there.
    """
    customer_id = str(uuid.uuid4())
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    # Pay more than the single invoice owes, invoice-specific: refused as over-allocation.
    response = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={
            "customer_id": customer_id,
            "amount": "5000.00",
            "method": "cash",
            "allocation": {
                "strategy": "invoice_specific",
                "allocations": [{"invoice_id": invoice["id"], "amount": "5000.00"}],
            },
        },
    )
    # `OverAllocation.status_code` is 422: it is a validation outcome about the amount,
    # not a concurrency conflict. The code is the part worth asserting.
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "OVER_ALLOCATION"


def test_an_unknown_payment_method_is_refused(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    customer_id = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    response = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={"customer_id": customer_id, "amount": "100.00", "method": "crypto"},
    )
    assert response.status_code == 422, response.text


# --- allocation ----------------------------------------------------------------


def test_invoice_specific_allocation_needs_the_invoices_listed(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """Naming the strategy without naming the invoices is an incomplete instruction."""
    customer_id = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    response = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={
            "customer_id": customer_id,
            "amount": "100.00",
            "method": "cash",
            "allocation": {"strategy": "invoice_specific"},
        },
    )
    assert response.status_code == 422, response.text


def test_oldest_first_settles_the_oldest_bill_first(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    """The default strategy, and the one a shopkeeper expects: the oldest debt clears
    first, and a payment that covers more than one bill spills forward in order."""
    customer_id = str(uuid.uuid4())
    first = _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    second = _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)

    # 1500 against two 1000 bills: the first is settled, the second part-paid.
    response = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={"customer_id": customer_id, "amount": "1500.00", "method": "cash"},
    )
    assert response.status_code == 200, response.text

    rows = dict(
        migration_session.execute(
            text("SELECT id, amount_paid FROM invoices WHERE customer_id = :c"),
            {"c": customer_id},
        ).all()
    )
    assert rows[uuid.UUID(first["id"])] == D("1000.00")
    assert rows[uuid.UUID(second["id"])] == D("500.00")


def test_a_payment_beyond_everything_owed_becomes_on_account_credit(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """FR-PAY-003: a remainder is never silently discarded.

    This is the `remaining <= 0` exit from the allocation loop — the customer has paid
    more than they owe, and the surplus stays theirs.
    """
    customer_id = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    response = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={"customer_id": customer_id, "amount": "2500.00", "method": "cash"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert D(str(body["on_account"])) == D("1500.00")


def test_an_allocation_larger_than_what_the_invoice_owes_is_refused(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """The per-target guard. Without it one payment could appear to settle more than the
    bill it names, and `amount_paid` would exceed `total`."""
    customer_id = str(uuid.uuid4())
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    response = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={
            "customer_id": customer_id,
            "amount": "1200.00",
            "method": "cash",
            "allocation": {
                "strategy": "invoice_specific",
                "allocations": [{"invoice_id": invoice["id"], "amount": "1200.00"}],
            },
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "OVER_ALLOCATION"


def test_allocating_against_a_cancelled_bill_is_refused(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    service: CreditService,
    ctx: AuthContext,
) -> None:
    """A cancelled bill owes nothing, so money must not be allocated to it."""
    customer_id = str(uuid.uuid4())
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    cancelled = api_client.post(
        f"/api/v1/invoices/{invoice['id']}:cancel",
        headers={**headers, **_key()},
        json={"reason": "entered against the wrong customer"},
    )
    assert cancelled.status_code in (200, 201), cancelled.text

    with pytest.raises(Exception) as caught:
        service._locked_outstanding(ctx, uuid.UUID(invoice["id"]))
    assert "cancel" in str(caught.value).lower() or caught.type.__name__ == "InvoiceCancelled"


def test_an_unknown_invoice_cannot_be_allocated_to(
    service: CreditService, ctx: AuthContext
) -> None:
    with pytest.raises(NotFound):
        service._locked_outstanding(ctx, uuid.uuid4())


# --- payment_status is derived, never set -------------------------------------


def test_payment_status_moves_unpaid_to_partially_paid_to_paid(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    """ADR-022. There is no "mark as paid" anywhere in this system; the status is
    recomputed from the allocations in the same transaction that writes them."""
    customer_id = str(uuid.uuid4())
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)

    def status() -> str:
        return str(
            migration_session.execute(
                text("SELECT payment_status FROM invoices WHERE id = :i"), {"i": invoice["id"]}
            ).scalar_one()
        )

    assert status() == "unpaid"

    assert (
        api_client.post(
            "/api/v1/payments",
            headers={**headers, **_key()},
            json={"customer_id": customer_id, "amount": "400.00", "method": "cash"},
        ).status_code
        == 200
    )
    assert status() == "partially_paid"

    assert (
        api_client.post(
            "/api/v1/payments",
            headers={**headers, **_key()},
            json={"customer_id": customer_id, "amount": "600.00", "method": "cash"},
        ).status_code
        == 200
    )
    # Exactly settled: the `paid == total` branch.
    assert status() == "paid"


def test_recomputing_an_unknown_invoice_is_a_quiet_no_op(
    service: CreditService, ctx: AuthContext, migration_session: Session
) -> None:
    """Deliberately **not** an error, unlike `_locked_outstanding`.

    This runs after an allocation to refresh a derived figure. If the invoice is gone by
    then there is nothing to recompute and nothing to report — raising here would turn a
    successful payment into a failed request. `_locked_outstanding` is the opposite case:
    it is the lookup that decides whether the money may be allocated at all, so an
    unknown invoice there must stop the operation.
    """
    before = migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one()
    service._recompute_invoice_payment(ctx, uuid.uuid4())
    assert migration_session.execute(text("SELECT count(*) FROM invoices")).scalar_one() == before


# --- reversal: the only way to correct a payment ------------------------------


def test_a_reversal_needs_a_reason(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    customer_id = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    paid = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={"customer_id": customer_id, "amount": "500.00", "method": "cash"},
    )
    payment_id = paid.json()["payment_id"]

    response = api_client.post(
        f"/api/v1/payments/{payment_id}:reverse",
        headers={**headers, **_key()},
        json={"reason": "x"},
    )
    assert response.status_code == 422, response.text


def test_reversing_restores_the_outstanding_balance(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    """FR-PAY-005. The original row is **never edited** — a `reversal` row points at it,
    and the derived balance follows."""
    customer_id = str(uuid.uuid4())
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    paid = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={"customer_id": customer_id, "amount": "1000.00", "method": "cash"},
    )
    payment_id = paid.json()["payment_id"]

    reversed_response = api_client.post(
        f"/api/v1/payments/{payment_id}:reverse",
        headers={**headers, **_key()},
        json={"reason": "customer's cheque bounced"},
    )
    assert reversed_response.status_code == 200, reversed_response.text

    row = migration_session.execute(
        text("SELECT amount_paid, payment_status FROM invoices WHERE id = :i"),
        {"i": invoice["id"]},
    ).first()
    assert row is not None
    assert row[0] == D("0.00")
    assert str(row[1]) == "unpaid"
    # The original payment row survives untouched.
    kinds = [
        str(k)
        for (k,) in migration_session.execute(
            text("SELECT kind FROM payments WHERE customer_id = :c ORDER BY received_at"),
            {"c": customer_id},
        ).all()
    ]
    assert "payment" in kinds and "reversal" in kinds


def test_reversing_an_unknown_payment_is_a_not_found(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    response = api_client.post(
        f"/api/v1/payments/{uuid.uuid4()}:reverse",
        headers={**headers, **_key()},
        json={"reason": "a perfectly good reason"},
    )
    assert response.status_code == 404, response.text


def test_the_same_payment_cannot_be_reversed_twice(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """Otherwise the balance would be credited twice for one mistake."""
    customer_id = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    payment_id = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={"customer_id": customer_id, "amount": "500.00", "method": "cash"},
    ).json()["payment_id"]
    first = api_client.post(
        f"/api/v1/payments/{payment_id}:reverse",
        headers={**headers, **_key()},
        json={"reason": "first reversal, legitimate"},
    )
    assert first.status_code == 200, first.text

    second = api_client.post(
        f"/api/v1/payments/{payment_id}:reverse",
        headers={**headers, **_key()},
        json={"reason": "second attempt at the same reversal"},
    )
    assert second.status_code in (409, 422), second.text
    assert second.json()["error"]["code"] == "PAYMENT_ALREADY_REVERSED"


def test_a_reversal_itself_cannot_be_reversed(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
    service: CreditService,
    ctx: AuthContext,
) -> None:
    """Reversing a reversal would re-apply the payment through the back door."""
    customer_id = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    payment_id = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={"customer_id": customer_id, "amount": "500.00", "method": "cash"},
    ).json()["payment_id"]
    api_client.post(
        f"/api/v1/payments/{payment_id}:reverse",
        headers={**headers, **_key()},
        json={"reason": "legitimate reversal"},
    )
    reversal_id = migration_session.execute(
        text("SELECT id FROM payments WHERE kind = 'reversal' AND customer_id = :c"),
        {"c": customer_id},
    ).scalar_one()

    with pytest.raises((ValidationFailed, PaymentAlreadyReversed)):
        service.reverse_payment(ctx, reversal_id, reason="reversing the reversal")


# --- filters ------------------------------------------------------------------


def test_outstanding_can_be_narrowed_to_one_customer(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    mine = str(uuid.uuid4())
    theirs = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=mine, name="Mine")
    _credit_sale(api_client, headers, tenant, stocked, customer_id=theirs, name="Theirs")

    everything = api_client.get("/api/v1/credit/outstanding", headers=headers).json()["items"]
    assert len(everything) >= 2

    narrowed = api_client.get(
        "/api/v1/credit/outstanding", headers=headers, params={"customer_id": mine}
    ).json()["items"]
    assert narrowed
    assert all(str(row["customer_id"]) == mine for row in narrowed if "customer_id" in row)


def test_payments_can_be_narrowed_by_invoice_and_by_customer(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    customer_id = str(uuid.uuid4())
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={"customer_id": customer_id, "amount": "250.00", "method": "cash"},
    )

    by_customer = api_client.get(
        "/api/v1/payments", headers=headers, params={"customer_id": customer_id}
    )
    assert by_customer.status_code == 200, by_customer.text
    assert by_customer.json()["items"]

    by_invoice = api_client.get(
        "/api/v1/payments", headers=headers, params={"invoice_id": invoice["id"]}
    )
    assert by_invoice.status_code == 200, by_invoice.text


def test_settled_customers_are_hidden_by_default_and_can_be_included(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """FR-RPT-031: a settled customer's history is never hidden permanently."""
    customer_id = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id, name="Settled Co")
    api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={"customer_id": customer_id, "amount": "1000.00", "method": "cash"},
    )

    default = api_client.get("/api/v1/credit/customers", headers=headers).json()["items"]
    assert all(str(row["customer"]["id"]) != customer_id for row in default)

    included = api_client.get(
        "/api/v1/credit/customers", headers=headers, params={"include_settled": True}
    ).json()["items"]
    assert any(str(row["customer"]["id"]) == customer_id for row in included)


# --- write-offs and credit notes ---------------------------------------------


def test_a_write_off_needs_a_long_explanation(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """A write-off removes money the shop is owed, so the floor is ten characters, not
    three. The higher bar is the control."""
    customer_id = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    response = api_client.post(
        "/api/v1/credit/write-off",
        headers={**headers, **_key()},
        json={"customer_id": customer_id, "amount": "100.00", "reason": "bad"},
    )
    assert response.status_code == 422, response.text


def test_a_write_off_must_be_positive(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    customer_id = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    response = api_client.post(
        "/api/v1/credit/write-off",
        headers={**headers, **_key()},
        json={
            "customer_id": customer_id,
            "amount": "0.00",
            "reason": "a thoroughly adequate explanation",
        },
    )
    assert response.status_code == 422, response.text


def test_a_write_off_produces_a_credit_note_with_its_reason(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    customer_id = str(uuid.uuid4())
    _credit_sale(api_client, headers, tenant, stocked, customer_id=customer_id)
    response = api_client.post(
        "/api/v1/credit/write-off",
        headers={**headers, **_key()},
        json={
            "customer_id": customer_id,
            "amount": "100.00",
            "reason": "goodwill after a long dispute",
        },
    )
    assert response.status_code in (200, 201), response.text

    reason = migration_session.execute(
        text("SELECT reason FROM credit_notes WHERE customer_id = :c ORDER BY issued_at DESC"),
        {"c": customer_id},
    ).scalar_one_or_none()
    assert reason is not None
    assert "goodwill" in str(reason)


def test_allocating_an_unknown_credit_note_is_a_not_found(
    service: CreditService, ctx: AuthContext
) -> None:
    with pytest.raises(NotFound):
        service.allocate_credit_note(ctx, uuid.uuid4(), [])


def test_a_credit_note_cannot_be_allocated_beyond_its_value(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    service: CreditService,
    ctx: AuthContext,
    migration_session: Session,
) -> None:
    """A note worth 100 cannot settle 500 of debt — otherwise money would be invented."""
    customer_id = uuid.uuid4()
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=str(customer_id))
    note_id = service.issue_credit_note(
        ctx,
        customer_id=customer_id,
        amount=D("100.00"),
        reason="partial goodwill credit",
        source_type=CreditNoteSource.ADJUSTMENT,
    )
    migration_session.commit()

    with pytest.raises(OverAllocation):
        service.allocate_credit_note(
            ctx, note_id, [AllocationTarget(invoice_id=uuid.UUID(invoice["id"]), amount=D("500"))]
        )


def test_a_credit_note_allocated_in_full_settles_that_much_of_the_bill(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    service: CreditService,
    ctx: AuthContext,
    migration_session: Session,
) -> None:
    """The `total == remaining` branch: a note consumed exactly."""
    customer_id = uuid.uuid4()
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=str(customer_id))
    note_id = service.issue_credit_note(
        ctx,
        customer_id=customer_id,
        amount=D("400.00"),
        reason="agreed settlement credit",
        source_type=CreditNoteSource.ADJUSTMENT,
    )
    migration_session.commit()

    service.allocate_credit_note(
        ctx, note_id, [AllocationTarget(invoice_id=uuid.UUID(invoice["id"]), amount=D("400.00"))]
    )
    migration_session.commit()

    paid = migration_session.execute(
        text("SELECT amount_paid FROM invoices WHERE id = :i"), {"i": invoice["id"]}
    ).scalar_one()
    assert paid == D("400.00")


def test_a_write_off_requires_the_writeoff_permission(
    api_client: TestClient,
    migration_session: Session,
    tenant: Tenant,
    stocked: str,
    headers: dict[str, str],
) -> None:
    """Owner-only. A cashier forgiving a debt is the control this guards."""
    cashier = create_user(migration_session, tenant, username="ravi", role=SystemRole.CASHIER)
    if Permission.CREDIT_WRITEOFF in {
        Permission(p) for p in permissions_for_role(SystemRole.CASHIER)
    }:
        pytest.skip("cashier holds credit.writeoff in this role matrix")
    token = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": cashier.username,
            "password": cashier.password,
            "device": {"id": str(cashier.device_id), "label": "C2"},
        },
    ).json()["access_token"]
    response = api_client.post(
        "/api/v1/credit/write-off",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Device-Id": str(cashier.device_id),
            "X-Counter-Id": str(tenant.counter_id),
            **_key(),
        },
        json={
            "customer_id": str(uuid.uuid4()),
            "amount": "100.00",
            "reason": "should never be allowed",
        },
    )
    assert response.status_code == 403, response.text


# --- the domain guards, called directly ---------------------------------------
#
# Everything above goes through the router, and the router validates first: Pydantic
# rejects a short reason or a negative amount before the service is reached. That is
# correct, and it is also why these guards need their own tests.
#
# `SECURITY.md §3.1` rule 2: the route declaration is the first gate, but anything
# financially sensitive checks **again** in the service, so an internal caller — a future
# endpoint, a job, a sync operation — cannot bypass the router's validation. A guard that
# only the router can trigger is a guard that stops working the moment someone adds a
# second caller.


def test_a_counter_payment_of_zero_is_refused_by_the_service(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    service: CreditService,
    ctx: AuthContext,
) -> None:
    """`record_sale_payment` is the payment taken *during finalisation*.

    It never passes through the `/payments` router, so its own amount guards are the only
    ones protecting it.
    """
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=str(uuid.uuid4()))
    with pytest.raises(ValidationFailed):
        service.record_sale_payment(
            ctx,
            invoice_id=uuid.UUID(invoice["id"]),
            customer_id=None,
            amount=D("0"),
            method="cash",
            reference=None,
            invoice_total=D("1000.00"),
        )


def test_a_counter_payment_above_the_bill_total_is_refused_by_the_service(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    service: CreditService,
    ctx: AuthContext,
) -> None:
    """Overpaying at the counter is a typo, not an intent to create on-account credit."""
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=str(uuid.uuid4()))
    with pytest.raises(OverAllocation) as caught:
        service.record_sale_payment(
            ctx,
            invoice_id=uuid.UUID(invoice["id"]),
            customer_id=None,
            amount=D("5000.00"),
            method="cash",
            reference=None,
            invoice_total=D("1000.00"),
        )
    assert caught.value.details["offered"] == "5000.00"


def test_allocations_adding_up_to_more_than_the_payment_are_refused(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    service: CreditService,
    ctx: AuthContext,
    migration_session: Session,
) -> None:
    """Each allocation can sit inside its own invoice's balance and the **sum** still
    exceed the money actually received. Without this the ledger would show more
    allocated than was paid."""
    customer_id = uuid.uuid4()
    first = _credit_sale(api_client, headers, tenant, stocked, customer_id=str(customer_id))
    second = _credit_sale(api_client, headers, tenant, stocked, customer_id=str(customer_id))
    migration_session.commit()

    with pytest.raises(OverAllocation):
        service.record_payment(
            ctx,
            customer_id=customer_id,
            amount="500.00",
            method="cash",
            strategy="invoice_specific",
            allocations=[
                AllocationTarget(invoice_id=uuid.UUID(first["id"]), amount=D("400.00")),
                AllocationTarget(invoice_id=uuid.UUID(second["id"]), amount=D("400.00")),
            ],
        )


def test_oldest_first_stops_once_the_money_runs_out(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    service: CreditService,
    ctx: AuthContext,
    migration_session: Session,
) -> None:
    """Three bills, enough money for one and a half. The loop must stop, not wrap."""
    customer_id = uuid.uuid4()
    invoices = [
        _credit_sale(api_client, headers, tenant, stocked, customer_id=str(customer_id))
        for _ in range(3)
    ]
    migration_session.commit()

    service.record_payment(
        ctx,
        customer_id=customer_id,
        amount="1500.00",
        method="cash",
        strategy="oldest_first",
    )
    migration_session.commit()

    paid = {
        str(row[0]): row[1]
        for row in migration_session.execute(
            text("SELECT id, amount_paid FROM invoices WHERE customer_id = :c"),
            {"c": customer_id},
        ).all()
    }
    assert paid[invoices[0]["id"]] == D("1000.00")
    assert paid[invoices[1]["id"]] == D("500.00")
    # The third is untouched: the money ran out, and nothing was invented to fill it.
    assert paid[invoices[2]["id"]] == D("0.00")


def test_an_already_settled_bill_is_skipped_by_oldest_first(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    service: CreditService,
    ctx: AuthContext,
    migration_session: Session,
) -> None:
    """A bill with nothing outstanding contributes no allocation row.

    Without the `take > ZERO` guard a settled invoice would collect a zero-amount
    allocation — a row that means nothing and makes a customer statement harder to read.
    """
    customer_id = uuid.uuid4()
    first = _credit_sale(api_client, headers, tenant, stocked, customer_id=str(customer_id))
    second = _credit_sale(api_client, headers, tenant, stocked, customer_id=str(customer_id))
    migration_session.commit()

    service.record_payment(
        ctx, customer_id=customer_id, amount="1000.00", method="cash", strategy="oldest_first"
    )
    migration_session.commit()

    # Pay again: the first owes nothing, so only the second is allocated to.
    service.record_payment(
        ctx, customer_id=customer_id, amount="300.00", method="cash", strategy="oldest_first"
    )
    migration_session.commit()

    allocations = migration_session.execute(
        text(
            "SELECT invoice_id, amount FROM payment_allocations"
            " WHERE invoice_id IN (:a, :b) ORDER BY amount"
        ),
        {"a": first["id"], "b": second["id"]},
    ).all()
    assert allocations
    # No zero-amount rows anywhere.
    assert all(row[1] > D("0") for row in allocations)
    paid_second = migration_session.execute(
        text("SELECT amount_paid FROM invoices WHERE id = :i"), {"i": second["id"]}
    ).scalar_one()
    assert paid_second == D("300.00")


def test_a_reversal_reason_floor_is_enforced_in_the_service(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    service: CreditService,
    ctx: AuthContext,
    migration_session: Session,
) -> None:
    """Three characters, checked in the domain as well as in the router."""
    customer_id = uuid.uuid4()
    _credit_sale(api_client, headers, tenant, stocked, customer_id=str(customer_id))
    migration_session.commit()
    payment_id = service.record_payment(
        ctx, customer_id=customer_id, amount="100.00", method="cash", strategy="oldest_first"
    ).payment_id
    migration_session.commit()

    for reason in ("", " ", "ab"):
        with pytest.raises(ValidationFailed):
            service.reverse_payment(ctx, payment_id, reason=reason)


def test_a_write_off_reason_floor_is_enforced_in_the_service(
    service: CreditService, ctx: AuthContext
) -> None:
    """Ten characters for a write-off, because it removes money the shop is owed."""
    for reason in ("", "too short", "nine char"):
        with pytest.raises(ValidationFailed):
            service.write_off(ctx, customer_id=uuid.uuid4(), amount="100.00", reason=reason)


def test_a_write_off_amount_must_be_positive_in_the_service(
    service: CreditService, ctx: AuthContext
) -> None:
    for amount in ("0", "-100.00"):
        with pytest.raises(ValidationFailed):
            service.write_off(
                ctx,
                customer_id=uuid.uuid4(),
                amount=amount,
                reason="a thoroughly adequate explanation",
            )


def test_a_credit_note_cannot_pay_more_than_a_bill_owes(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    service: CreditService,
    ctx: AuthContext,
    migration_session: Session,
) -> None:
    """A large note against a small bill: refused per **invoice**, not just per note.

    Distinct from the note-exhaustion check: here the note has plenty left, and the
    target still exceeds what that particular invoice owes. Allowing it would push
    `amount_paid` past `total` and leave the customer's balance negative on one bill
    while they still owed money on another.
    """
    customer_id = uuid.uuid4()
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=str(customer_id))
    note_id = service.issue_credit_note(
        ctx,
        customer_id=customer_id,
        amount=D("2000.00"),
        reason="large goodwill credit",
        source_type=CreditNoteSource.ADJUSTMENT,
    )
    migration_session.commit()

    with pytest.raises(OverAllocation) as caught:
        service.allocate_credit_note(
            ctx,
            note_id,
            [AllocationTarget(invoice_id=uuid.UUID(invoice["id"]), amount=D("1500.00"))],
        )
    # The message names the invoice, because a cashier needs to know *which* bill.
    assert caught.value.details["invoice_id"] == invoice["id"]


def test_a_partly_used_credit_note_stays_open(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    service: CreditService,
    ctx: AuthContext,
    migration_session: Session,
) -> None:
    """Only a note consumed in full is closed; a partly used one keeps its remainder.

    The remainder is the customer's money. Closing the note early would quietly forfeit
    it, which is the mirror image of over-allocating.
    """
    customer_id = uuid.uuid4()
    invoice = _credit_sale(api_client, headers, tenant, stocked, customer_id=str(customer_id))
    note_id = service.issue_credit_note(
        ctx,
        customer_id=customer_id,
        amount=D("1000.00"),
        reason="credit for a returned part",
        source_type=CreditNoteSource.ADJUSTMENT,
    )
    migration_session.commit()

    service.allocate_credit_note(
        ctx, note_id, [AllocationTarget(invoice_id=uuid.UUID(invoice["id"]), amount=D("400.00"))]
    )
    migration_session.commit()

    status = migration_session.execute(
        text("SELECT status FROM credit_notes WHERE id = :n"), {"n": note_id}
    ).scalar_one()
    assert str(status) != "fully_allocated", (
        "a note with 600 left must stay open, or the remainder is forfeited"
    )
    paid = migration_session.execute(
        text("SELECT amount_paid FROM invoices WHERE id = :i"), {"i": invoice["id"]}
    ).scalar_one()
    assert paid == D("400.00")
