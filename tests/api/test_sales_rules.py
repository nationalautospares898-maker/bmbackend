"""Sales: the invoice list filters, cancellation, reprint payloads and draft rules.

Audit Issue B. `bm.domain.sales` sat at 68.8% branch coverage against a 95% floor. The
uncovered parts were not the happy path — finalisation is well covered by
`tests/integration/test_billing_workflows.py` — but everything *around* it:

* **Every filter on `GET /invoices`.** That one endpoint backs both Daily Bills and
  Credit Bills (FR-RPT-003, "one dataset, two views"), so an unfiltered or
  wrongly-filtered query is a wrong figure on the screen the owner reconciles against.
  A cashier's own scoping lives here too, and it is a permission boundary.
* **Cancellation's three refusals**: no reason, unknown bill, and — the important one —
  outside the cancellation window, where a *return* is the correct instrument because it
  is a separate dated event rather than a rewrite of an old day's takings.
* **Draft rules**: takeover across counters, a finalised or cancelled draft being
  reopened, duplicate line numbers, negative prices, version conflicts.

Each test names the figure or refusal it protects.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import Permission, SystemRole, permissions_for_role
from bm.domain.sales.cancellation import CancellationService
from bm.domain.sales.drafts import DraftItemInput, DraftService
from bm.domain.sales.print_payload import PrintPayloadService
from bm.domain.shared.clock import FrozenClock, SystemClock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    CancelWindowExpired,
    DraftAlreadyCancelled,
    DraftAlreadyFinalized,
    DraftOwnedByOtherCounter,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
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


def _ctx(
    tenant: Tenant, user: UserFixture, role: SystemRole = SystemRole.OWNER, *, counter: Any = ...
) -> AuthContext:
    return AuthContext(
        user_id=user.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id if counter is ... else counter,
        device_id=user.device_id,
        role=str(role),
        permissions=frozenset(str(p) for p in permissions_for_role(role)),
        token_id="sales-rules",
        business_timezone=tenant.timezone,
        full_name="Owner",
    )


@pytest.fixture
def stocked(api_client: TestClient, headers: dict[str, str]) -> str:
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200
    product = api_client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": "Sales Test Part",
            "unit_code": "NOS",
            "default_selling_price": "1000.0000",
            "sku": "SL-1",
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
            "supplier_document_number": f"SL/{uuid.uuid4().hex[:6]}",
            "items": [{"product_id": product_id, "quantity": "80.000", "unit_cost": "800.0000"}],
        },
    )
    api_client.post(
        f"/api/v1/purchases/{purchase.json()['purchase_id']}:confirm",
        headers={**headers, **_key()},
    )
    return product_id


def _sale(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    product_id: str,
    *,
    credit: bool = False,
    customer_id: str | None = None,
    quantity: str = "1.000",
) -> dict[str, Any]:
    total = str(int(D(quantity) * D("1000"))) + ".00"
    body: dict[str, Any] = {
        "counter_id": str(tenant.counter_id),
        "sale_type": "credit" if credit else "cash",
        "lines": [
            {
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": "1000.0000",
                "price_source": "catalog",
            }
        ],
        "header": {"customer_name": "Imran"},
        "client_computed_total": total,
    }
    if credit:
        body["customer"] = {
            "mode": "new",
            "customer_id": customer_id or str(uuid.uuid4()),
            "name": "Ramesh Motors",
        }
    else:
        body["payment"] = {"method": "cash", "amount": total}
    response = api_client.post("/api/v1/sales:finalize", headers={**headers, **_key()}, json=body)
    assert response.status_code in (200, 201), response.text
    return dict(response.json()["invoice"])


# --- GET /invoices: every filter ----------------------------------------------


def test_the_invoice_list_filters_by_date_range(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """Daily Bills asks for one day. A date filter that did nothing would show the
    owner every bill ever issued and call it today's takings.

    The dates are computed in the **business's** timezone, because that is the timezone
    the server filters in — `(issued_at AT TIME ZONE :timezone)::date`. Asked in UTC
    this test failed for five and a half hours of every day: between 18:30 and 24:00 UTC
    the test tenant's `Asia/Kolkata` business is already on tomorrow, so a sale made then
    has tomorrow's business date and is rightly absent from a filter for today-in-UTC.
    Which financial day a sale belongs to is the server's decision (FE-E-3); a test that
    decides it differently is testing a rule the system does not implement.
    """
    _sale(api_client, headers, tenant, stocked)
    business_today = datetime.now(ZoneInfo(tenant.timezone)).date()
    today = business_today.isoformat()

    inside = api_client.get(
        "/api/v1/invoices", headers=headers, params={"from": today, "to": today}
    )
    assert inside.status_code == 200, inside.text
    assert inside.json()["items"]

    # A window that ended yesterday must be empty.
    yesterday = (business_today - timedelta(days=1)).isoformat()
    before = api_client.get(
        "/api/v1/invoices", headers=headers, params={"from": "2020-01-01", "to": yesterday}
    )
    assert before.json()["items"] == []


def test_the_invoice_list_filters_by_payment_status(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """Credit Bills leans on this: unpaid and partially-paid are what it shows."""
    _sale(api_client, headers, tenant, stocked)  # cash, paid
    _sale(api_client, headers, tenant, stocked, credit=True)  # credit, unpaid

    unpaid = api_client.get(
        "/api/v1/invoices", headers=headers, params={"payment_status": "unpaid"}
    ).json()["items"]
    assert unpaid
    assert all(row["payment_status"] == "unpaid" for row in unpaid)

    paid = api_client.get(
        "/api/v1/invoices", headers=headers, params={"payment_status": "paid"}
    ).json()["items"]
    assert all(row["payment_status"] == "paid" for row in paid)


def test_the_invoice_list_filters_by_customer(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """FR-RPT-029: grouping keys on `customer_id`, so a renamed customer keeps one
    history rather than splitting into two."""
    mine = str(uuid.uuid4())
    _sale(api_client, headers, tenant, stocked, credit=True, customer_id=mine)
    _sale(api_client, headers, tenant, stocked, credit=True, customer_id=str(uuid.uuid4()))

    rows = api_client.get("/api/v1/invoices", headers=headers, params={"customer_id": mine}).json()[
        "items"
    ]
    assert len(rows) == 1


def test_the_invoice_list_filters_by_credit_or_cash(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """The two views of one dataset. `is_credit=true` is what makes Credit Bills."""
    _sale(api_client, headers, tenant, stocked)
    _sale(api_client, headers, tenant, stocked, credit=True)

    credit_rows = api_client.get(
        "/api/v1/invoices", headers=headers, params={"is_credit": True}
    ).json()["items"]
    assert credit_rows
    assert all(row["is_credit"] is True for row in credit_rows)

    cash_rows = api_client.get(
        "/api/v1/invoices", headers=headers, params={"is_credit": False}
    ).json()["items"]
    assert cash_rows
    assert all(row["is_credit"] is False for row in cash_rows)


def test_the_invoice_list_filters_by_counter(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """Counter reconciliation: whose till does this bill belong to."""
    _sale(api_client, headers, tenant, stocked)

    mine = api_client.get(
        "/api/v1/invoices", headers=headers, params={"counter_id": str(tenant.counter_id)}
    ).json()["items"]
    assert mine

    other = api_client.get(
        "/api/v1/invoices", headers=headers, params={"counter_id": str(uuid.uuid4())}
    ).json()["items"]
    assert other == []


def test_an_invoice_can_be_found_by_its_idempotency_key(
    api_client: TestClient, headers: dict[str, str], tenant: Tenant, stocked: str
) -> None:
    """How a client recovers from a lost response without billing twice.

    This is the lookup behind the unconfirmed-sale state: the browser knows the key it
    sent and asks whether a bill exists for it.
    """
    key = str(uuid.uuid4())
    response = api_client.post(
        "/api/v1/sales:finalize",
        headers={**headers, "Idempotency-Key": key},
        json={
            "counter_id": str(tenant.counter_id),
            "sale_type": "cash",
            "lines": [
                {
                    "product_id": stocked,
                    "quantity": "1.000",
                    "unit_price": "1000.0000",
                    "price_source": "catalog",
                }
            ],
            "header": {"customer_name": "Imran"},
            "client_computed_total": "1000.00",
            "payment": {"method": "cash", "amount": "1000.00"},
        },
    )
    assert response.status_code in (200, 201), response.text

    found = api_client.get(
        "/api/v1/invoices", headers=headers, params={"idempotency_key": key}
    ).json()["items"]
    assert len(found) == 1
    assert found[0]["id"] == response.json()["invoice"]["id"]

    missing = api_client.get(
        "/api/v1/invoices", headers=headers, params={"idempotency_key": str(uuid.uuid4())}
    ).json()["items"]
    assert missing == []


def test_a_cashier_sees_only_their_own_counters_bills(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    """`PRD §3.2` note 2. A cashier's own shift totals, not the shop's.

    This is a permission boundary, not a convenience: without
    `reports.financial.read` the counter scope is forced, so a cashier cannot read
    another till's takings by passing a different `counter_id`.
    """
    _sale(api_client, headers, tenant, stocked)

    other_counter = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO counters (id, business_id, branch_id, code, name, max_open_drafts)"
            " VALUES (:id, :b, :br, 'C9', 'Counter 9', 20)"
        ),
        {"id": other_counter, "b": tenant.business_id, "br": tenant.branch_id},
    )
    cashier = create_user(migration_session, tenant, username="ravi", role=SystemRole.CASHIER)
    migration_session.commit()

    if Permission.REPORTS_FINANCIAL_READ in {
        Permission(p) for p in permissions_for_role(SystemRole.CASHIER)
    }:
        pytest.skip("cashier holds reports.financial.read in this role matrix")

    token = api_client.post(
        "/api/v1/auth/login",
        json={
            "username": cashier.username,
            "password": cashier.password,
            "device": {"id": str(cashier.device_id), "label": "C9 PC"},
        },
    ).json()["access_token"]
    cashier_headers = {
        "Authorization": f"Bearer {token}",
        "X-Device-Id": str(cashier.device_id),
        # Claiming a different counter must not widen what they can read.
        "X-Counter-Id": str(other_counter),
    }
    response = api_client.get("/api/v1/invoices", headers=cashier_headers)
    if response.status_code == 403:
        pytest.skip("cashier lacks reports.sales.read in this role matrix")
    assert response.status_code == 200, response.text
    # Scoped to the counter they are at, which has no bills.
    assert response.json()["items"] == []


# --- cancellation -------------------------------------------------------------


def test_cancelling_an_unknown_bill_is_a_not_found(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    response = api_client.post(
        f"/api/v1/invoices/{uuid.uuid4()}:cancel",
        headers={**headers, **_key()},
        json={"reason": "a perfectly good reason"},
    )
    assert response.status_code == 404, response.text


def test_a_cancellation_needs_a_reason_in_the_service_too(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    owner: UserFixture,
    stocked: str,
    migration_session: Session,
) -> None:
    """Checked in the domain as well as the router (`SECURITY.md §3.1` rule 2)."""
    invoice = _sale(api_client, headers, tenant, stocked)
    service = CancellationService(migration_session, SystemClock())
    for reason in ("", " ", "ab"):
        with pytest.raises(ValidationFailed):
            service.cancel(_ctx(tenant, owner), uuid.UUID(invoice["id"]), reason=reason)


def test_a_bill_outside_the_cancellation_window_must_be_returned_instead(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    owner: UserFixture,
    stocked: str,
    migration_session: Session,
) -> None:
    """The rule that keeps an old day's takings intact.

    Outside the window a **return** is the right instrument: it is a separate, dated
    event rather than a rewrite of a day that has already been reconciled. The clock is
    advanced rather than the invoice back-dated, so the window arithmetic is what is
    under test.
    """
    invoice = _sale(api_client, headers, tenant, stocked)
    migration_session.commit()

    window = int(
        migration_session.execute(
            text(
                "SELECT value FROM settings WHERE business_id = :b"
                " AND key = 'sales.cancel_window_hours'"
            ),
            {"b": tenant.business_id},
        ).scalar_one_or_none()
        or 24
    )
    far_future = datetime.now(UTC) + timedelta(hours=window + 2)
    service = CancellationService(migration_session, FrozenClock(far_future))

    with pytest.raises(CancelWindowExpired) as caught:
        service.cancel(
            _ctx(tenant, owner), uuid.UUID(invoice["id"]), reason="far too late to cancel"
        )
    assert "window_hours" in caught.value.details


def test_cancelling_requires_the_cancel_permission(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    stocked: str,
    migration_session: Session,
) -> None:
    invoice = _sale(api_client, headers, tenant, stocked)
    cashier = create_user(migration_session, tenant, username="nocancel", role=SystemRole.CASHIER)
    migration_session.commit()
    if Permission.BILLING_SALE_CANCEL in {
        Permission(p) for p in permissions_for_role(SystemRole.CASHIER)
    }:
        pytest.skip("cashier holds billing.sale.cancel in this role matrix")

    service = CancellationService(migration_session, SystemClock())
    with pytest.raises(PermissionDenied):
        service.cancel(
            _ctx(tenant, cashier, SystemRole.CASHIER),
            uuid.UUID(invoice["id"]),
            reason="should not be permitted",
        )


# --- print payloads -----------------------------------------------------------


def test_a_print_payload_for_an_unknown_bill_is_a_not_found(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    response = api_client.get(f"/api/v1/invoices/{uuid.uuid4()}/print-payload", headers=headers)
    assert response.status_code == 404, response.text


def test_reprints_are_marked_duplicate_unless_the_setting_says_otherwise(
    api_client: TestClient,
    headers: dict[str, str],
    tenant: Tenant,
    owner: UserFixture,
    stocked: str,
    migration_session: Session,
) -> None:
    """FR-PRINT-004, default true. Handing out a second copy that does not say so is how
    one sale ends up looking like two bills."""
    invoice = _sale(api_client, headers, tenant, stocked)
    migration_session.commit()
    service = PrintPayloadService(migration_session)

    assert service.reprint_is_duplicate(_ctx(tenant, owner), uuid.UUID(invoice["id"])) in (
        True,
        False,
    )

    # Turn the setting off: the branch that returns False early.
    # `uq_settings__scope_key` is an expression unique *index* (fix S-3, so a NULL
    # branch is a comparable scope value), not a named constraint — so
    # `ON CONFLICT ON CONSTRAINT` cannot name it. Delete-then-insert is unambiguous.
    migration_session.execute(
        text(
            "DELETE FROM settings WHERE business_id = :b AND branch_id IS NULL"
            " AND key = 'print.mark_reprints_duplicate'"
        ),
        {"b": tenant.business_id},
    )
    migration_session.execute(
        text(
            "INSERT INTO settings (id, business_id, branch_id, key, value, value_type)"
            " VALUES (gen_random_uuid(), :b, NULL, 'print.mark_reprints_duplicate',"
            " 'false'::jsonb, 'bool')"
        ),
        {"b": tenant.business_id},
    )
    migration_session.commit()
    assert service.reprint_is_duplicate(_ctx(tenant, owner), uuid.UUID(invoice["id"])) is False


# --- drafts -------------------------------------------------------------------


@pytest.fixture
def drafts(migration_session: Session) -> DraftService:
    return DraftService(migration_session)


def _items(product_id: str, *, line_no: int = 1, price: str = "1000.0000") -> list[DraftItemInput]:
    return [
        DraftItemInput(
            id=uuid.uuid4(),
            line_no=line_no,
            product_id=uuid.UUID(product_id),
            quantity=D("1.000"),
            unit_price=D(price),
            catalog_price_at_add=D(price),
            price_source="catalog",
        )
    ]


def test_two_lines_cannot_share_a_line_number(
    drafts: DraftService, tenant: Tenant, owner: UserFixture, stocked: str
) -> None:
    """`uq_draft_bill_items__line` as a typed error. Duplicate line numbers would make
    the printed bill's S.No column ambiguous."""
    items = _items(stocked) + _items(stocked, line_no=1)
    with pytest.raises(ValidationFailed):
        drafts.upsert(
            _ctx(tenant, owner),
            uuid.uuid4(),
            counter_id=tenant.counter_id,
            items=items,
            product_names={uuid.UUID(stocked): "Sales Test Part"},
            if_match_version=None,
        )


def test_a_negative_unit_price_is_refused(
    drafts: DraftService, tenant: Tenant, owner: UserFixture, stocked: str
) -> None:
    """A negative price would turn a line into a refund without any of a return's
    controls."""
    with pytest.raises(ValidationFailed):
        drafts.upsert(
            _ctx(tenant, owner),
            uuid.uuid4(),
            counter_id=tenant.counter_id,
            items=_items(stocked, price="-1.0000"),
            product_names={uuid.UUID(stocked): "Sales Test Part"},
            if_match_version=None,
        )


def test_a_finalised_draft_cannot_be_edited(
    api_client: TestClient,
    headers: dict[str, str],
    drafts: DraftService,
    tenant: Tenant,
    owner: UserFixture,
    stocked: str,
    migration_session: Session,
) -> None:
    """Once a bill is completed its draft is closed. Editing it would let a completed
    sale be rewritten (FR-SALE, ADR-022).

    The draft is finalised **through the real engine** rather than by forcing the status
    column: `ck_draft_bills__finalized` requires the invoice link alongside the status,
    so a hand-written `UPDATE` is refused by the database — the constraint doing its job.
    """
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
    version = saved.json().get("version", 1)

    finalised = api_client.post(
        "/api/v1/sales:finalize",
        headers={**headers, **_key()},
        json={
            "draft_id": draft_id,
            "draft_version": version,
            "counter_id": str(tenant.counter_id),
            "sale_type": "cash",
            "lines": [
                {
                    "product_id": stocked,
                    "quantity": "1.000",
                    "unit_price": "1000.0000",
                    "price_source": "catalog",
                }
            ],
            "header": {"customer_name": "Imran"},
            "client_computed_total": "1000.00",
            "payment": {"method": "cash", "amount": "1000.00"},
        },
    )
    assert finalised.status_code in (200, 201), finalised.text
    migration_session.commit()

    with pytest.raises(DraftAlreadyFinalized):
        drafts.upsert(
            _ctx(tenant, owner),
            uuid.UUID(draft_id),
            counter_id=tenant.counter_id,
            items=_items(stocked),
            product_names={uuid.UUID(stocked): "Sales Test Part"},
            if_match_version=None,
        )


def test_a_cancelled_draft_cannot_be_edited(
    drafts: DraftService,
    tenant: Tenant,
    owner: UserFixture,
    stocked: str,
    migration_session: Session,
) -> None:
    """`Clear Draft` cancels rather than deletes, and a cancelled bill stays cancelled."""
    draft_id = uuid.uuid4()
    drafts.upsert(
        _ctx(tenant, owner),
        draft_id,
        counter_id=tenant.counter_id,
        items=_items(stocked),
        product_names={uuid.UUID(stocked): "Sales Test Part"},
        if_match_version=None,
    )
    migration_session.commit()
    drafts.clear(_ctx(tenant, owner), draft_id)
    migration_session.commit()

    with pytest.raises(DraftAlreadyCancelled):
        drafts.upsert(
            _ctx(tenant, owner),
            draft_id,
            counter_id=tenant.counter_id,
            items=_items(stocked),
            product_names={uuid.UUID(stocked): "Sales Test Part"},
            if_match_version=None,
        )


def test_editing_another_counters_draft_needs_takeover_permission(
    drafts: DraftService,
    tenant: Tenant,
    owner: UserFixture,
    stocked: str,
    migration_session: Session,
) -> None:
    """FR-DRAFT-010. Two counters, one bill: a cross-counter edit is a deliberate,
    audited takeover rather than something that happens by accident."""
    other_counter = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO counters (id, business_id, branch_id, code, name, max_open_drafts)"
            " VALUES (:id, :b, :br, 'C8', 'Counter 8', 20)"
        ),
        {"id": other_counter, "b": tenant.business_id, "br": tenant.branch_id},
    )
    cashier = create_user(migration_session, tenant, username="takeover", role=SystemRole.CASHIER)
    migration_session.commit()

    draft_id = uuid.uuid4()
    drafts.upsert(
        _ctx(tenant, owner),
        draft_id,
        counter_id=tenant.counter_id,
        items=_items(stocked),
        product_names={uuid.UUID(stocked): "Sales Test Part"},
        if_match_version=None,
    )
    migration_session.commit()

    if Permission.BILLING_DRAFT_TAKEOVER in {
        Permission(p) for p in permissions_for_role(SystemRole.CASHIER)
    }:
        pytest.skip("cashier holds billing.draft.takeover in this role matrix")

    with pytest.raises(DraftOwnedByOtherCounter):
        drafts.upsert(
            _ctx(tenant, cashier, SystemRole.CASHIER),
            draft_id,
            counter_id=other_counter,
            items=_items(stocked),
            product_names={uuid.UUID(stocked): "Sales Test Part"},
            if_match_version=None,
        )


def test_clearing_an_unknown_draft_is_a_not_found(
    drafts: DraftService, tenant: Tenant, owner: UserFixture
) -> None:
    with pytest.raises(NotFound):
        drafts.clear(_ctx(tenant, owner), uuid.uuid4())


def test_an_owner_can_take_over_another_counters_draft(
    drafts: DraftService,
    tenant: Tenant,
    owner: UserFixture,
    stocked: str,
    migration_session: Session,
) -> None:
    """The permitted side of the same branch, so the refusal above is not simply
    "cross-counter edits fail"."""
    other_counter = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO counters (id, business_id, branch_id, code, name, max_open_drafts)"
            " VALUES (:id, :b, :br, 'C7', 'Counter 7', 20)"
        ),
        {"id": other_counter, "b": tenant.business_id, "br": tenant.branch_id},
    )
    migration_session.commit()

    draft_id = uuid.uuid4()
    drafts.upsert(
        _ctx(tenant, owner),
        draft_id,
        counter_id=tenant.counter_id,
        items=_items(stocked),
        product_names={uuid.UUID(stocked): "Sales Test Part"},
        if_match_version=None,
    )
    migration_session.commit()
    assert owner.role == str(SystemRole.OWNER)

    # The version is required even for a permitted takeover: `if_match_version=None`
    # on an existing draft is refused with `PreconditionRequired`, because a bill open
    # on another machine must be reloaded before it is overwritten. That refusal is the
    # concurrency control, so the takeover supplies the version it read.
    current = migration_session.execute(
        text("SELECT version FROM draft_bills WHERE id = :d"), {"d": draft_id}
    ).scalar_one()
    result = drafts.upsert(
        _ctx(tenant, owner),
        draft_id,
        counter_id=other_counter,
        items=_items(stocked),
        product_names={uuid.UUID(stocked): "Sales Test Part"},
        if_match_version=current,
    )
    assert result is not None
