"""The declared response shapes must match what the API actually returns.

`schemas/responses.py` is published in `openapi.json` so `bmfrontend` can **generate**
its response types instead of hand-writing field names (ADR-029). That only helps if the
declaration is true, and nothing in FastAPI enforces it: the handlers return dicts and
the models are attached as documentation, never as `response_model=` filters, because
several responses must **omit** a key rather than null it (FR-CAT-004).

So the enforcement is here. Every model is validated against a **real** response from
the running app with `extra="forbid"`, which means:

* a renamed or removed key fails this suite rather than appearing as a blank column at
  the counter;
* an added key fails until it is documented, so the client can be told about it;
* the omit-don't-null rule is asserted directly — `cost_price` must be **absent**
  without permission, not `null`.

This is deliberately an integration test against PostgreSQL. A hand-built dict would
only prove the model matches a fixture someone wrote at the same time as the model.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from bm.api.v1.schemas.responses import (
    BootstrapOut,
    CatalogDeltaOut,
    CreditCustomersOut,
    DailySalesOut,
    DraftListOut,
    DraftOut,
    ErrorOut,
    FinalizeOut,
    InvoiceListOut,
    InvoiceOut,
    OperationTypesOut,
    PaymentOut,
    PrintPayloadOut,
    ProductOut,
    ProductSearchOut,
    SyncBatchOut,
)
from bm.domain.auth.permissions import SystemRole
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db


# --- helpers ------------------------------------------------------------------


def _headers(client: TestClient, user: UserFixture, tenant: Tenant) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": user.password,
            "device": {"id": str(user.device_id), "label": "Counter 1 PC"},
        },
    ).json()["access_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Device-Id": str(user.device_id),
        "X-Counter-Id": str(tenant.counter_id),
    }


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def check(model: type[BaseModel], body: Any) -> BaseModel:
    """Validate a real response body against its declared shape.

    `extra="forbid"` on every model in `responses.py` is what gives this teeth: the
    failure message names the undocumented key.
    """
    return model.model_validate(body)


# --- fixtures -----------------------------------------------------------------


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def cashier(migration_session: Session, tenant: Tenant) -> UserFixture:
    """A cashier has no `catalog.cost.read`, which is how the omission rule is tested."""
    return create_user(migration_session, tenant, username="ravi", role=SystemRole.CASHIER)


@pytest.fixture
def headers(api_client: TestClient, owner: UserFixture, tenant: Tenant) -> dict[str, str]:
    return _headers(api_client, owner, tenant)


@pytest.fixture
def cashier_headers(api_client: TestClient, cashier: UserFixture, tenant: Tenant) -> dict[str, str]:
    return _headers(api_client, cashier, tenant)


@pytest.fixture
def product(api_client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200
    response = api_client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": "Clutch Plate - Mahindra Bolero",
            "unit_code": "NOS",
            "default_selling_price": "2400.0000",
            "sku": "MB-CP-01",
            "cost_price": "1900.0000",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def stocked(api_client: TestClient, headers: dict[str, str], product: dict[str, Any]) -> str:
    supplier_id = str(uuid.uuid4())
    api_client.put(
        f"/api/v1/suppliers/{supplier_id}", headers=headers, json={"name": "Bolero Spares"}
    )
    created = api_client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "supplier_id": supplier_id,
            "supplier_document_number": "BS/2026/12",
            "items": [
                {"product_id": product["id"], "quantity": "20.000", "unit_cost": "1900.0000"}
            ],
        },
    )
    assert created.status_code == 201, created.text
    purchase_id = created.json()["purchase_id"]
    confirmed = api_client.post(
        f"/api/v1/purchases/{purchase_id}:confirm", headers={**headers, **_key()}
    )
    assert confirmed.status_code == 200, confirmed.text
    return str(product["id"])


@pytest.fixture
def customer(api_client: TestClient, headers: dict[str, str]) -> str:
    customer_id = str(uuid.uuid4())
    response = api_client.put(
        f"/api/v1/customers/{customer_id}",
        headers=headers,
        json={"name": "Shankar Transport", "phone": "9000011111"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["customer"]["id"])


def _finalize(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    product_id: str,
    *,
    credit_customer: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "counter_id": str(tenant.counter_id),
        "sale_type": "credit" if credit_customer else "cash",
        "lines": [{"product_id": product_id, "quantity": "1.000", "unit_price": "2400.0000"}],
    }
    if credit_customer:
        body["customer"] = {"mode": "existing", "customer_id": credit_customer}
    else:
        body["payment"] = {"method": "cash", "amount": "2400.00"}
    response = api_client.post("/api/v1/sales:finalize", headers={**headers, **_key()}, json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


# =============================================================================
# catalog
# =============================================================================


def test_the_product_search_response_matches_its_declared_shape(
    api_client: TestClient, headers: dict[str, str], product: dict[str, Any]
) -> None:
    response = api_client.get(
        "/api/v1/products/search", headers=headers, params={"query": "clutch"}
    )
    assert response.status_code == 200, response.text
    parsed = check(ProductSearchOut, response.json())
    assert isinstance(parsed, ProductSearchOut)
    assert parsed.items, "the fixture product must be found, or this test proves nothing"
    # A search hit always carries its ranking tier, which the client uses to decide
    # whether to auto-select the first result.
    assert parsed.items[0].match is not None


def test_the_product_detail_response_matches_its_declared_shape(
    api_client: TestClient, headers: dict[str, str], product: dict[str, Any]
) -> None:
    response = api_client.get(f"/api/v1/products/{product['id']}", headers=headers)
    assert response.status_code == 200, response.text
    check(ProductOut, response.json())


def test_cost_price_is_absent_from_the_body_not_null_without_permission(
    api_client: TestClient, cashier_headers: dict[str, str], product: dict[str, Any]
) -> None:
    """FR-CAT-004, asserted on the wire rather than on the model.

    The declared field is optional, so validation alone would accept `null`. A nulled
    key invites a client to render a placeholder where a margin would be, so the key
    itself must be gone.
    """
    response = api_client.get(f"/api/v1/products/{product['id']}", headers=cashier_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "cost_price" not in body
    check(ProductOut, body)


def test_cost_price_is_present_and_declared_with_permission(
    api_client: TestClient, headers: dict[str, str], product: dict[str, Any]
) -> None:
    response = api_client.get(f"/api/v1/products/{product['id']}", headers=headers)
    body = response.json()
    assert "cost_price" in body
    parsed = check(ProductOut, body)
    assert parsed.cost_price is not None


# =============================================================================
# drafts
# =============================================================================


def test_the_draft_responses_match_their_declared_shapes(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, product: dict[str, Any]
) -> None:
    draft_id = str(uuid.uuid4())
    created = api_client.put(
        f"/api/v1/drafts/{draft_id}",
        headers=headers,
        json={
            "counter_id": str(tenant.counter_id),
            "label": "Counter 1 — bill 1",
            "items": [
                {
                    # Client-generated: an offline draft already has its final ids, so
                    # syncing it is an upsert rather than a create (FR-DRAFT-001).
                    "id": str(uuid.uuid4()),
                    "line_no": 1,
                    "product_id": product["id"],
                    "quantity": "2.000",
                    "unit_price": "2400.0000",
                    "catalog_price_at_add": "2400.0000",
                }
            ],
        },
    )
    assert created.status_code == 200, created.text
    parsed = check(DraftOut, created.json())
    assert isinstance(parsed, DraftOut)
    # The preview is always labelled advisory; the server recomputes at finalisation.
    assert parsed.preview_totals.advisory is True

    listed = api_client.get("/api/v1/drafts", headers=headers)
    assert listed.status_code == 200, listed.text
    check(DraftListOut, listed.json())


# =============================================================================
# sales, invoices and the print payload
# =============================================================================


def test_the_finalize_response_matches_its_declared_shape(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """The single most important shape in the contract: it drives the confirmation card
    **and** the printed bill."""
    parsed = check(FinalizeOut, _finalize(api_client, headers, tenant, stocked))
    assert isinstance(parsed, FinalizeOut)
    # Documented and asserted together: there is no customer-visible number (OD-3).
    assert "invoice_number" not in parsed.print_payload.model_dump()


def test_the_invoice_responses_match_their_declared_shapes(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    finalized = _finalize(api_client, headers, tenant, stocked)
    invoice_id = finalized["invoice"]["id"]

    detail = api_client.get(f"/api/v1/invoices/{invoice_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    check(InvoiceOut, detail.json())

    listed = api_client.get("/api/v1/invoices", headers=headers)
    assert listed.status_code == 200, listed.text
    parsed = check(InvoiceListOut, listed.json())
    assert isinstance(parsed, InvoiceListOut)
    assert parsed.items


def test_the_print_payload_response_matches_its_declared_shape(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    finalized = _finalize(api_client, headers, tenant, stocked)
    response = api_client.get(
        f"/api/v1/invoices/{finalized['invoice']['id']}/print-payload", headers=headers
    )
    assert response.status_code == 200, response.text
    parsed = check(PrintPayloadOut, response.json())
    assert isinstance(parsed, PrintPayloadOut)
    # `null`, not `"0.00"` — the client must render no tax row at all (ADR-020).
    assert parsed.totals.tax is None
    assert parsed.columns == ["sl_no", "item", "quantity", "price", "total"]


# =============================================================================
# credit
# =============================================================================


def test_the_payment_and_credit_customer_responses_match_their_declared_shapes(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    customer: str,
) -> None:
    finalized = _finalize(api_client, headers, tenant, stocked, credit_customer=customer)
    invoice_id = finalized["invoice"]["id"]

    paid = api_client.post(
        "/api/v1/payments",
        headers={**headers, **_key()},
        json={
            "customer_id": customer,
            "method": "cash",
            "amount": "1000.00",
            "allocation": {
                "strategy": "invoice_specific",
                "allocations": [{"invoice_id": invoice_id, "amount": "1000.00"}],
            },
        },
    )
    assert paid.status_code == 200, paid.text
    check(PaymentOut, paid.json())

    customers = api_client.get("/api/v1/credit/customers", headers=headers)
    assert customers.status_code == 200, customers.text
    parsed = check(CreditCustomersOut, customers.json())
    assert isinstance(parsed, CreditCustomersOut)
    assert parsed.items


# =============================================================================
# sync and reports
# =============================================================================


def test_the_bootstrap_response_matches_its_declared_shape(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """Bootstrap is how the client avoids hardcoding a business rule, so its shape is
    the one the client cannot afford to guess."""
    response = api_client.get("/api/v1/sync/bootstrap", headers=headers)
    assert response.status_code == 200, response.text
    parsed = check(BootstrapOut, response.json())
    assert isinstance(parsed, BootstrapOut)
    assert parsed.sync.offline_finalisation == "refused"
    assert parsed.money_policy.require_integral_amounts is True


def test_the_catalog_delta_response_matches_its_declared_shape(
    api_client: TestClient, headers: dict[str, str], product: dict[str, Any]
) -> None:
    response = api_client.get("/api/v1/sync/catalog", headers=headers)
    assert response.status_code == 200, response.text
    parsed = check(CatalogDeltaOut, response.json())
    assert isinstance(parsed, CatalogDeltaOut)
    # The offline cache never carries a cost price, whatever the caller's permissions.
    for cached in parsed.products:
        assert "cost_price" not in cached.model_dump()


def test_the_sync_batch_response_matches_its_declared_shape(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    response = api_client.post(
        "/api/v1/sync/operations",
        headers=headers,
        json={
            "device_id": headers["X-Device-Id"],
            "operations": [
                {
                    "op_id": str(uuid.uuid4()),
                    "client_seq": 1,
                    "type": "draft.upsert",
                    "entity_type": "draft_bill",
                    "entity_id": str(uuid.uuid4()),
                    "base_version": None,
                    "client_created_at": "2026-10-30T10:00:00+05:30",
                    "payload": {
                        "counter_id": str(tenant.counter_id),
                        "label": "Offline bill",
                        "items": [],
                    },
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    check(SyncBatchOut, response.json())


def test_the_operation_types_response_matches_its_declared_shape(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    response = api_client.get("/api/v1/sync/operation-types", headers=headers)
    assert response.status_code == 200, response.text
    parsed = check(OperationTypesOut, response.json())
    assert isinstance(parsed, OperationTypesOut)
    assert "sale.finalize" not in parsed.allowed


def test_the_report_envelope_matches_its_declared_shape(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    _finalize(api_client, headers, tenant, stocked)
    response = api_client.get("/api/v1/reports/daily-sales", headers=headers)
    assert response.status_code == 200, response.text
    parsed = check(DailySalesOut, response.json())
    assert isinstance(parsed, DailySalesOut)
    # `collected` is cash actually taken and is **not** `net`: a credit bill adds to
    # `net` and nothing to the till (FR-RPT-002).
    assert parsed.collected != parsed.net or parsed.credit_sales == 0
    # Reports state what they leave out rather than applying it silently.
    assert parsed.excluded


# =============================================================================
# the error envelope
# =============================================================================


def test_every_error_carries_the_declared_envelope(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """One shape for every failure (§4), so the client has exactly one error path."""
    response = api_client.get(f"/api/v1/invoices/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404, response.text
    parsed = check(ErrorOut, response.json())
    assert isinstance(parsed, ErrorOut)
    assert parsed.error.code == "NOT_FOUND"
    assert parsed.error.request_id


def test_a_validation_failure_carries_the_declared_envelope(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant
) -> None:
    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**headers, **_key()},
        json={"counter_id": str(tenant.counter_id), "sale_type": "cash", "lines": []},
    )
    assert response.status_code == 422, response.text
    parsed = check(ErrorOut, response.json())
    assert isinstance(parsed, ErrorOut)
    assert parsed.error.code == "VALIDATION_ERROR"


def test_a_missing_idempotency_key_is_a_declared_validation_error(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """Never a silent pass: an unkeyed retry is how a duplicate invoice is created."""
    response = api_client.post(
        "/api/v1/sales:finalize",
        headers=headers,
        json={
            "counter_id": str(tenant.counter_id),
            "sale_type": "cash",
            "lines": [{"product_id": stocked, "quantity": "1.000", "unit_price": "2400.0000"}],
            "payment": {"method": "cash", "amount": "2400.00"},
        },
    )
    assert response.status_code == 422, response.text
    parsed = check(ErrorOut, response.json())
    assert isinstance(parsed, ErrorOut)
    assert "Idempotency-Key" in parsed.error.details.get("fields", {})
