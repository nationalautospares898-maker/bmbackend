"""The import workflow end to end (M23, FR-IMP-001…010).

Against the real API and a real PostgreSQL database, because what matters about an
import is what ends up in the tables, and a mocked session would prove only that the
code calls itself.

The invariants these tests exist to hold, in order of how much damage breaking them
would do:

1. **A price list never moves stock** (FR-IMP-002). Asserted by counting
   `stock_movements` before and after an apply, not by reading the code.
2. **Nothing is created or received from a guess** (FR-IMP-007). A row the matcher
   cannot resolve blocks the apply until a human rules on it, and the `409` names how
   many rows are waiting.
3. **`applied` is terminal** (FR-IMP-005). A second apply is refused even with a fresh
   idempotency key, because the job's own state is the guard.
4. **A failing row rolls the whole import back** (FR-IMP-008), so nobody is left
   guessing which half landed.
5. **Every row's outcome is recorded with its reason** (FR-IMP-009), including the
   skipped ones — which are the whole point of the report.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

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
            "device": {"id": str(user.device_id), "label": "Back office PC"},
        },
    ).json()["access_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Device-Id": str(user.device_id),
        "X-Counter-Id": str(tenant.counter_id),
    }


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def cashier(migration_session: Session, tenant: Tenant) -> UserFixture:
    """A cashier has no `imports.manage`, which is how the authorisation rule is tested."""
    return create_user(migration_session, tenant, username="ravi", role=SystemRole.CASHIER)


@pytest.fixture
def headers(api_client: TestClient, owner: UserFixture, tenant: Tenant) -> dict[str, str]:
    return _headers(api_client, owner, tenant)


@pytest.fixture
def product(api_client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    assert api_client.post("/api/v1/units:ensure-defaults", headers=headers).status_code == 200
    response = api_client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": "Tie Rod End Bolero",
            "unit_code": "NOS",
            "default_selling_price": "420.0000",
            "sku": "TT-407-BS",
            "cost_price": "300.0000",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def supplier_id(api_client: TestClient, headers: dict[str, str]) -> str:
    new_id = str(uuid.uuid4())
    response = api_client.put(
        f"/api/v1/suppliers/{new_id}",
        headers=headers,
        json={"name": "Sri Balaji Auto Parts", "phone": "9876500001"},
    )
    assert response.status_code in (200, 201), response.text
    return new_id


def _upload(
    client: TestClient,
    headers: dict[str, str],
    content: bytes,
    *,
    filename: str = "prices.csv",
    intent: str = "price_list",
    supplier_id: str | None = None,
    allow_duplicate: bool | None = None,
    duplicate_reason: str | None = None,
) -> Any:
    data: dict[str, Any] = {"intent": intent}
    if supplier_id:
        data["supplier_id"] = supplier_id
    if allow_duplicate is not None:
        data["allow_duplicate"] = str(allow_duplicate).lower()
    if duplicate_reason is not None:
        data["duplicate_reason"] = duplicate_reason
    return client.post(
        "/api/v1/imports",
        headers=headers,
        files={"file": (filename, io.BytesIO(content), "text/csv")},
        data=data,
    )


def _run_parse(migration_session: Session) -> None:
    """Run the queued `import.parse` job the way the worker would.

    Goes through `claim_and_run_one` rather than calling the service directly, so the
    registration in `domain/jobs/handlers.py`, the `FOR UPDATE SKIP LOCKED` claim and
    the handler's own idempotency guard are all exercised — the parse path in
    production is the queue, and testing a different path would test a different
    system.
    """
    import bm.domain.jobs.handlers  # noqa: F401  - registers the handlers
    from bm.infra.queue.runner import claim_and_run_one

    migration_session.commit()
    assert claim_and_run_one("test-worker") is True


def _movement_count(session: Session) -> int:
    session.commit()
    return int(session.execute(text("SELECT count(*) FROM stock_movements")).scalar() or 0)


PRICE_LIST = b"Part No,Description,MRP,Rate\nTT-407-BS,Tie Rod End Bolero,480.00,340.00\n"


# --- upload: validation (FR-IMP-001/002/003) ----------------------------------


def test_a_csv_price_list_is_accepted_and_queued(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    response = _upload(api_client, headers, PRICE_LIST)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "uploaded"
    assert body["duplicate_of_job_id"] is None


def test_an_unaccepted_file_type_is_refused_with_415(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    response = _upload(api_client, headers, b"MZ\x90\x00", filename="payload.exe")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_an_executable_renamed_csv_is_refused_on_its_content(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """The extension says `.csv`; the bytes say otherwise. Both are checked."""
    response = _upload(api_client, headers, b"MZ\x90\x00\x03\x00", filename="prices.csv")
    assert response.status_code == 415


def test_an_empty_file_is_refused(api_client: TestClient, headers: dict[str, str]) -> None:
    response = _upload(api_client, headers, b"")
    assert response.status_code == 422


def test_a_supplier_bill_import_requires_a_supplier(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """FR-IMP-002: the intent cannot be inferred and a receipt needs its source."""
    response = _upload(api_client, headers, PRICE_LIST, intent="purchase_receipt")
    assert response.status_code == 422
    assert "supplier_id" in response.json()["error"]["details"]["fields"]


def test_an_unknown_intent_is_refused(api_client: TestClient, headers: dict[str, str]) -> None:
    response = _upload(api_client, headers, PRICE_LIST, intent="guess_for_me")
    assert response.status_code == 422


def test_the_original_filename_is_kept_as_metadata_only(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    """FR-IMP-003. A traversal attempt is stored as a display string and never reaches
    the filesystem, because the stored key is generated."""
    hostile = "../../../etc/passwd.csv"
    response = _upload(api_client, headers, PRICE_LIST, filename=hostile)
    assert response.status_code == 202
    migration_session.commit()
    row = migration_session.execute(
        text("SELECT original_filename, stored_object_key FROM import_jobs")
    ).one()
    assert ".." not in row.stored_object_key
    assert "etc" not in row.stored_object_key
    assert row.stored_object_key.endswith(".csv")


def test_a_cashier_cannot_upload_an_import(
    api_client: TestClient, cashier: UserFixture, tenant: Tenant
) -> None:
    response = _upload(api_client, _headers(api_client, cashier, tenant), PRICE_LIST)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


# --- duplicate detection (FR-IMP-004 / E-9) -----------------------------------


def test_the_same_bytes_twice_returns_the_existing_job_not_a_second_one(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    first = _upload(api_client, headers, PRICE_LIST)
    assert first.status_code == 202
    second = _upload(api_client, headers, PRICE_LIST)
    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "DUPLICATE_FILE"
    assert error["details"]["existing_job"]["id"] == first.json()["job_id"]


def test_an_override_without_a_reason_is_refused(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """FR-IMP-004 requires an explicit flag *and* a reason. A flag alone would make the
    duplicate check a speed bump."""
    _upload(api_client, headers, PRICE_LIST)
    response = _upload(api_client, headers, PRICE_LIST, allow_duplicate=True)
    assert response.status_code == 422
    assert "duplicate_override_reason" in response.json()["error"]["details"]["fields"]


def test_an_override_with_a_reason_is_accepted_and_audited(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    first = _upload(api_client, headers, PRICE_LIST)
    second = _upload(
        api_client,
        headers,
        PRICE_LIST,
        allow_duplicate=True,
        duplicate_reason="Supplier resent the same list after a correction.",
    )
    assert second.status_code == 202, second.text
    assert second.json()["duplicate_of_job_id"] == first.json()["job_id"]

    migration_session.commit()
    actions = [
        r[0]
        for r in migration_session.execute(
            text("SELECT action FROM audit_logs WHERE action LIKE 'import.%'")
        ).all()
    ]
    assert "import.duplicate_overridden" in actions


# --- the state machine (FR-IMP-005) -------------------------------------------


def test_parsing_moves_the_job_to_parsed_when_every_row_resolved(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)

    body = api_client.get(f"/api/v1/imports/{job_id}", headers=headers).json()
    assert body["status"] == "parsed"
    assert body["row_count"] == 1
    assert body["rows_pending_decision"] == 0


def test_a_row_the_matcher_cannot_resolve_sends_the_job_to_needs_review(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    """No product exists, so the one row is a `new_product` decision."""
    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)

    body = api_client.get(f"/api/v1/imports/{job_id}", headers=headers).json()
    assert body["status"] == "needs_review"
    assert body["rows_pending_decision"] == 1


def test_an_unreadable_file_ends_in_failed_with_a_reason(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    job_id = _upload(api_client, headers, b"no,recognisable,columns\n1,2,3\n").json()["job_id"]
    _run_parse(migration_session)

    body = api_client.get(f"/api/v1/imports/{job_id}", headers=headers).json()
    assert body["status"] == "failed"
    assert body["error_summary"] is not None
    assert "column headings" in body["error_summary"]


def test_a_second_delivery_of_the_parse_job_is_a_no_op(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    """At-least-once delivery is the queue's contract, so the handler must be
    idempotent. A second run must not double the rows."""
    from bm.domain.imports.service import ImportService
    from bm.domain.shared.clock import SystemClock

    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)
    before = api_client.get(f"/api/v1/imports/{job_id}", headers=headers).json()

    from bm.config import get_settings

    service = ImportService.from_settings(migration_session, SystemClock(), get_settings())
    service.parse_job(uuid.UUID(job_id))
    migration_session.commit()

    after = api_client.get(f"/api/v1/imports/{job_id}", headers=headers).json()
    assert after == before
    assert (
        int(migration_session.execute(text("SELECT count(*) FROM import_rows")).scalar() or 0) == 1
    )


def test_a_job_can_be_cancelled_before_it_is_applied(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)
    response = api_client.post(f"/api/v1/imports/{job_id}:cancel", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_a_cancelled_job_cannot_then_be_applied(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)
    api_client.post(f"/api/v1/imports/{job_id}:cancel", headers=headers)
    response = api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    assert response.status_code == 422


# --- matching (FR-IMP-006) ----------------------------------------------------


def test_an_exact_part_code_matches_the_product(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)

    rows = api_client.get(f"/api/v1/imports/{job_id}/rows", headers=headers).json()["rows"]
    assert rows[0]["match_status"] == "exact_sku"
    assert rows[0]["matched_product_id"] == product["id"]
    assert rows[0]["matched_product_name"] == "Tie Rod End Bolero"
    assert rows[0]["needs_decision"] is False


def test_a_part_code_written_differently_still_matches(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    """`tt407bs` and `TT-407-BS` are one part code (FR-CAT-006). A supplier who writes
    it without separators must not create a duplicate product."""
    job_id = _upload(
        api_client, headers, b"Part No,Description,MRP\ntt 407 bs,Tie Rod,480.00\n"
    ).json()["job_id"]
    _run_parse(migration_session)

    rows = api_client.get(f"/api/v1/imports/{job_id}/rows", headers=headers).json()["rows"]
    assert rows[0]["match_status"] == "exact_sku"
    assert rows[0]["matched_product_id"] == product["id"]


def test_the_same_part_twice_in_one_file_is_a_duplicate_on_the_second_row(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    """The *first* occurrence stays a normal row, so a list with one accidental repeat
    still imports its unique lines once the repeat is ruled on."""
    content = (
        b"Part No,Description,MRP\nTT-407-BS,Tie Rod End,480.00\nTT-407-BS,Tie Rod End,500.00\n"
    )
    job_id = _upload(api_client, headers, content).json()["job_id"]
    _run_parse(migration_session)

    rows = api_client.get(f"/api/v1/imports/{job_id}/rows", headers=headers).json()["rows"]
    assert rows[0]["match_status"] == "exact_sku"
    assert rows[1]["match_status"] == "duplicate_in_file"
    assert "row 1" in rows[1]["apply_error"]


def test_an_unreadable_row_is_kept_as_invalid_with_its_raw_values(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    """Never dropped. The raw cells are shown beside the failure so a reviewer can see
    what the software was looking at."""
    content = b"Part No,Description,MRP\nTT-407-BS,Tie Rod,about 500\n"
    job_id = _upload(api_client, headers, content).json()["job_id"]
    _run_parse(migration_session)

    rows = api_client.get(f"/api/v1/imports/{job_id}/rows", headers=headers).json()["rows"]
    assert rows[0]["match_status"] == "invalid"
    assert rows[0]["raw_values"]["MRP"] == "about 500"
    assert rows[0]["needs_decision"] is True


def test_rows_can_be_filtered_by_match_status_for_the_review_queue(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    content = (
        b"Part No,Description,MRP\nTT-407-BS,Tie Rod End,480.00\nZZ-999,Unknown Thing,100.00\n"
    )
    job_id = _upload(api_client, headers, content).json()["job_id"]
    _run_parse(migration_session)

    response = api_client.get(
        f"/api/v1/imports/{job_id}/rows?match_status=new_product", headers=headers
    )
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["parsed_sku"] == "ZZ-999"


def test_an_unknown_match_status_filter_is_refused(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)
    response = api_client.get(
        f"/api/v1/imports/{job_id}/rows?match_status=probably_fine", headers=headers
    )
    assert response.status_code == 422


# --- human decisions (FR-IMP-007) ---------------------------------------------


def test_an_apply_is_refused_while_a_row_still_needs_a_decision(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    """The requirement, stated as a test: nothing auto-creates a product from a guess."""
    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)

    response = api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "IMPORT_ROWS_PENDING_DECISION"
    assert error["details"]["rows_pending"] == 1


def test_skipping_a_row_lets_the_import_proceed_without_it(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)
    row_id = api_client.get(f"/api/v1/imports/{job_id}/rows", headers=headers).json()["rows"][0][
        "id"
    ]

    decided = api_client.patch(
        f"/api/v1/imports/{job_id}/rows/{row_id}",
        headers=headers,
        json={"decision": "skip"},
    )
    assert decided.status_code == 200
    assert decided.json()["row"]["decision"] == "skip"

    applied = api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    assert applied.status_code == 200, applied.text
    assert applied.json()["rows_applied"] == 0
    assert applied.json()["rows_skipped"] == 1


def test_creating_a_new_product_from_a_row_requires_that_explicit_decision(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    content = b"Part No,Description,MRP,Rate\nZZ-999,Brake Shoe Set,900.00,700.00\n"
    job_id = _upload(api_client, headers, content).json()["job_id"]
    _run_parse(migration_session)
    row_id = api_client.get(f"/api/v1/imports/{job_id}/rows", headers=headers).json()["rows"][0][
        "id"
    ]

    api_client.patch(
        f"/api/v1/imports/{job_id}/rows/{row_id}",
        headers=headers,
        json={"decision": "create_new"},
    )
    applied = api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    assert applied.status_code == 200, applied.text
    assert applied.json()["rows_applied"] == 1

    found = api_client.get("/api/v1/products/search?query=ZZ-999", headers=headers).json()
    assert found["items"][0]["name"] == "Brake Shoe Set"
    assert found["items"][0]["default_selling_price"] == "900.0000"


def test_an_invalid_row_cannot_be_turned_into_a_new_product(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    """There is nothing to create from a row that could not be read. Offering it would
    create a product with a missing price."""
    content = b"Part No,Description,MRP\nZZ-888,Mystery Part,about 500\n"
    job_id = _upload(api_client, headers, content).json()["job_id"]
    _run_parse(migration_session)
    row_id = api_client.get(f"/api/v1/imports/{job_id}/rows", headers=headers).json()["rows"][0][
        "id"
    ]

    response = api_client.patch(
        f"/api/v1/imports/{job_id}/rows/{row_id}",
        headers=headers,
        json={"decision": "create_new"},
    )
    assert response.status_code == 422
    assert "could not be read" in str(response.json()["error"]["details"])


def test_accepting_a_match_only_permits_a_candidate_the_matcher_offered(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    """Otherwise the review screen is an unvalidated product picker, and a mistyped id
    silently reprices a part nobody mentioned."""
    content = b"Part No,Description,MRP\nZZ-777,Unrelated Thing,100.00\n"
    job_id = _upload(api_client, headers, content).json()["job_id"]
    _run_parse(migration_session)
    row_id = api_client.get(f"/api/v1/imports/{job_id}/rows", headers=headers).json()["rows"][0][
        "id"
    ]

    response = api_client.patch(
        f"/api/v1/imports/{job_id}/rows/{row_id}",
        headers=headers,
        json={"decision": "accept_match", "matched_product_id": product["id"]},
    )
    assert response.status_code == 422
    assert "candidates" in str(response.json()["error"]["details"])


def test_a_bulk_decision_covers_every_pending_row_in_one_status(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    """Reviewing 400 identical rows one at a time is data entry, not review."""
    content = b"Part No,Description,MRP\n" + b"".join(
        f"NEW-{i},Part {i},{100 + i}.00\n".encode() for i in range(5)
    )
    job_id = _upload(api_client, headers, content).json()["job_id"]
    _run_parse(migration_session)

    response = api_client.post(
        f"/api/v1/imports/{job_id}/rows:decide-all",
        headers=headers,
        json={"match_status": "new_product", "decision": "skip"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows_pending_decision"] == 0


def test_invalid_rows_can_only_be_skipped_in_bulk_never_created(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    content = b"Part No,Description,MRP\nZZ-1,Thing,about 10\n"
    job_id = _upload(api_client, headers, content).json()["job_id"]
    _run_parse(migration_session)

    response = api_client.post(
        f"/api/v1/imports/{job_id}/rows:decide-all",
        headers=headers,
        json={"match_status": "invalid", "decision": "create_new"},
    )
    assert response.status_code == 422


# --- apply: a price list never moves stock (FR-IMP-002) -----------------------


def test_applying_a_price_list_updates_the_price_and_moves_no_stock(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    """The single most important assertion in this file."""
    before = _movement_count(migration_session)

    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)
    applied = api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    assert applied.status_code == 200, applied.text
    assert applied.json()["rows_applied"] == 1
    assert applied.json()["created_purchase_id"] is None

    assert _movement_count(migration_session) == before

    updated = api_client.get(f"/api/v1/products/{product['id']}", headers=headers).json()
    assert updated["default_selling_price"] == "480.0000"


def test_applying_a_price_list_with_only_a_cost_column_still_moves_no_stock(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    before = _movement_count(migration_session)
    content = b"Part No,Description,Rate\nTT-407-BS,Tie Rod End,310.00\n"
    job_id = _upload(api_client, headers, content).json()["job_id"]
    _run_parse(migration_session)
    api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    assert _movement_count(migration_session) == before


# --- apply: a supplier bill receives stock through the purchase path ----------


def test_applying_a_supplier_bill_receives_stock_through_a_confirmed_purchase(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    supplier_id: str,
    migration_session: Session,
) -> None:
    """FR-PUR-003: confirming a purchase is the *only* path that receives stock, so the
    import builds one rather than writing movements itself."""
    content = b"Part No,Description,Qty,Rate\nTT-407-BS,Tie Rod End,12,305.00\n"
    job_id = _upload(
        api_client,
        headers,
        content,
        intent="purchase_receipt",
        supplier_id=supplier_id,
    ).json()["job_id"]
    _run_parse(migration_session)

    applied = api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["rows_applied"] == 1
    assert body["created_purchase_id"] is not None

    migration_session.commit()
    reason, quantity = migration_session.execute(
        text(
            "SELECT reason, quantity_delta FROM stock_movements"
            " WHERE source_type = 'purchase' ORDER BY created_at DESC LIMIT 1"
        )
    ).one()
    assert str(reason) == "purchase_receipt"
    assert quantity == 12

    purchase = migration_session.execute(
        text("SELECT status FROM purchases WHERE id = :id"),
        {"id": body["created_purchase_id"]},
    ).scalar_one()
    assert str(purchase) == "confirmed"


def test_a_skipped_row_in_a_supplier_bill_receives_nothing_for_that_row(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    supplier_id: str,
    migration_session: Session,
) -> None:
    content = (
        b"Part No,Description,Qty,Rate\n"
        b"TT-407-BS,Tie Rod End,12,305.00\n"
        b"ZZ-404,Not Carried,5,100.00\n"
    )
    job_id = _upload(
        api_client, headers, content, intent="purchase_receipt", supplier_id=supplier_id
    ).json()["job_id"]
    _run_parse(migration_session)
    api_client.post(
        f"/api/v1/imports/{job_id}/rows:decide-all",
        headers=headers,
        json={"match_status": "new_product", "decision": "skip"},
    )
    applied = api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    assert applied.json()["rows_applied"] == 1
    assert applied.json()["rows_skipped"] == 1

    migration_session.commit()
    received = migration_session.execute(
        text("SELECT count(*) FROM stock_movements WHERE source_type = 'purchase'")
    ).scalar()
    assert received == 1


# --- apply: terminal and atomic (FR-IMP-005, FR-IMP-008) ----------------------


def test_an_applied_job_cannot_be_applied_again_even_with_a_fresh_key(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    """`applied` is terminal. The job's own state is the guard, which is stronger than
    the idempotency key — it holds for a client that generates a new one."""
    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)
    assert (
        api_client.post(
            f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()}
        ).status_code
        == 200
    )

    second = api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IMPORT_ALREADY_APPLIED"


def test_reapplying_a_supplier_bill_cannot_receive_the_goods_twice(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    supplier_id: str,
    migration_session: Session,
) -> None:
    """The assertion that matters about terminality: stock, not status."""
    content = b"Part No,Description,Qty,Rate\nTT-407-BS,Tie Rod End,7,305.00\n"
    job_id = _upload(
        api_client, headers, content, intent="purchase_receipt", supplier_id=supplier_id
    ).json()["job_id"]
    _run_parse(migration_session)
    api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    after_first = _movement_count(migration_session)

    api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    assert _movement_count(migration_session) == after_first


def test_a_decision_cannot_be_recorded_on_an_applied_job(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    """It would be a lie: the row's outcome is already recorded."""
    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)
    row_id = api_client.get(f"/api/v1/imports/{job_id}/rows", headers=headers).json()["rows"][0][
        "id"
    ]
    api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})

    response = api_client.patch(
        f"/api/v1/imports/{job_id}/rows/{row_id}",
        headers=headers,
        json={"decision": "skip"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IMPORT_ALREADY_APPLIED"


def test_an_applied_job_cannot_be_cancelled(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    _run_parse(migration_session)
    api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    response = api_client.post(f"/api/v1/imports/{job_id}:cancel", headers=headers)
    assert response.status_code == 409


def test_a_failing_row_rolls_the_whole_import_back(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    supplier_id: str,
    migration_session: Session,
) -> None:
    """FR-IMP-008: one transaction. A half-applied supplier bill is worse than a
    rejected one, because nobody can tell which half landed.

    The failure is induced the way a real one would arrive — a part code colliding with
    an existing product's, so `create_new` raises `DUPLICATE_SKU` partway through — and
    the assertion is that the *earlier* row's price change is gone too.
    """
    content = (
        b"Part No,Description,MRP\nTT-407-BS,Tie Rod End,999.00\nZZ-NEW,Brand New Part,100.00\n"
    )
    job_id = _upload(api_client, headers, content).json()["job_id"]
    _run_parse(migration_session)
    rows = api_client.get(f"/api/v1/imports/{job_id}/rows", headers=headers).json()["rows"]
    new_row = next(r for r in rows if r["parsed_sku"] == "ZZ-NEW")
    api_client.patch(
        f"/api/v1/imports/{job_id}/rows/{new_row['id']}",
        headers=headers,
        json={"decision": "create_new"},
    )

    # Create a product that will collide with `ZZ-NEW` before the apply runs.
    assert (
        api_client.post(
            "/api/v1/products",
            headers=headers,
            json={
                "name": "Something Else",
                "unit_code": "NOS",
                "default_selling_price": "50.0000",
                "sku": "ZZ-NEW",
            },
        ).status_code
        == 201
    )

    response = api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_SKU"

    # The first row's price change must be gone with it.
    unchanged = api_client.get(f"/api/v1/products/{product['id']}", headers=headers).json()
    assert unchanged["default_selling_price"] == "420.0000"

    # And the job is still reviewable rather than stuck in `applying`.
    assert (
        api_client.get(f"/api/v1/imports/{job_id}", headers=headers).json()["status"]
        == "needs_review"
    )


# --- the report (FR-IMP-009) --------------------------------------------------


def test_the_report_names_every_row_including_the_skipped_ones_and_why(
    api_client: TestClient,
    headers: dict[str, str],
    product: dict[str, Any],
    migration_session: Session,
) -> None:
    """ "47 applied" without "and these 3 were skipped because…" hides exactly what the
    reader needs."""
    content = b"Part No,Description,MRP\nTT-407-BS,Tie Rod End,480.00\nZZ-404,Not Carried,100.00\n"
    job_id = _upload(api_client, headers, content).json()["job_id"]
    _run_parse(migration_session)
    api_client.post(
        f"/api/v1/imports/{job_id}/rows:decide-all",
        headers=headers,
        json={"match_status": "new_product", "decision": "skip"},
    )
    api_client.post(f"/api/v1/imports/{job_id}:apply", headers={**headers, **_key()})

    report = api_client.get(f"/api/v1/imports/{job_id}/report", headers=headers).json()
    assert report["job"]["rows_applied"] == 1
    assert report["job"]["rows_skipped"] == 1
    skipped = next(r for r in report["rows"] if r["apply_result"] == "skipped")
    assert skipped["parsed_sku"] == "ZZ-404"
    assert "Skipped by the person reviewing" in skipped["apply_error"]


def test_import_history_lists_jobs_newest_first(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    """Newest first, **guaranteed** — not merely likely.

    This began as a flake: it passed alone and failed in the full suite, because the
    list was ordered by `created_at DESC, id DESC` and neither half can carry the
    promise. `created_at` comes from `now()` at the operating system's clock resolution,
    so two uploads under load tie; `id` is a `uuid4`, so the tiebreak is random.
    Migration `0020` added a monotonic `ordinal`, and this asserts on that.
    """
    _upload(api_client, headers, PRICE_LIST, filename="first.csv")
    _upload(api_client, headers, b"Part No,Rate\nX-2,10\n", filename="second.csv")

    # Force the exact tie that produced the flake, so this fails if the ordering ever
    # goes back to depending on the timestamp.
    migration_session.execute(text("UPDATE import_jobs SET created_at = '2026-10-30T10:00:00+00'"))
    migration_session.commit()

    jobs = api_client.get("/api/v1/imports", headers=headers).json()["jobs"]
    assert [j["original_filename"] for j in jobs] == ["second.csv", "first.csv"]
    # Strictly descending, which is what the cursor relies on.
    assert [j["ordinal"] for j in jobs] == sorted((j["ordinal"] for j in jobs), reverse=True)


def test_the_history_cursor_neither_skips_nor_repeats_a_job(
    api_client: TestClient, headers: dict[str, str], migration_session: Session
) -> None:
    """`?before=<ordinal>` over a unique column, so paging is exact.

    The previous cursor was `?before=<created_at>` over a **non-unique** column, which
    either drops a row or serves it twice depending on which side of the boundary a tie
    falls. Every `created_at` here is made identical — the worst case for the old form,
    and no case at all for the new one.
    """
    for index in range(5):
        assert (
            _upload(
                api_client,
                headers,
                f"Part No,Rate\nX-{index},{10 + index}\n".encode(),
                filename=f"file-{index}.csv",
            ).status_code
            == 202
        )
    migration_session.execute(text("UPDATE import_jobs SET created_at = '2026-10-30T10:00:00+00'"))
    migration_session.commit()

    seen: list[str] = []
    cursor: int | None = None
    for _ in range(6):
        query = "/api/v1/imports?limit=2" + (f"&before={cursor}" if cursor else "")
        page = api_client.get(query, headers=headers).json()["jobs"]
        if not page:
            break
        seen.extend(job["original_filename"] for job in page)
        cursor = page[-1]["ordinal"]

    assert len(seen) == len(set(seen)), f"the cursor repeated a job: {seen}"
    assert sorted(seen) == [f"file-{i}.csv" for i in range(5)], f"the cursor skipped a job: {seen}"


# --- tenancy ------------------------------------------------------------------


def test_another_business_cannot_read_this_businesses_import(
    api_client: TestClient,
    headers: dict[str, str],
    migration_session: Session,
) -> None:
    """`NotFound`, not `PermissionDenied` — a 403 would confirm the job exists."""
    from tests.fixtures import create_tenant

    job_id = _upload(api_client, headers, PRICE_LIST).json()["job_id"]
    other = create_tenant(migration_session, display_name="OTHER SHOP", branch_code="OTH")
    outsider = create_user(
        migration_session, other, username="outsider", role=SystemRole.OWNER, all_branches=True
    )
    migration_session.commit()

    response = api_client.get(
        f"/api/v1/imports/{job_id}", headers=_headers(api_client, outsider, other)
    )
    assert response.status_code == 404
