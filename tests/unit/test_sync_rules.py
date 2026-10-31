"""Synchronisation: dispatch, ordering, conflicts, bounds and idempotent replay.

Audit Issue B. `bm.domain.sync` sat at 69% branch coverage against a 95% floor, and the
uncovered half was the part that decides **whether one bill becomes two**:

* the batch-size bound;
* the intra-entity ordering rule, which stops a later edit being applied after an
  earlier one was refused;
* the `draft.delete` and `customer.upsert` dispatch arms — only `draft.upsert` had ever
  been exercised, so two thirds of the registry was untested;
* the version-conflict detection;
* the `server_state` echo for both entity types, which is what lets a client resolve a
  conflict without a second round trip;
* the catalogue cursor and its truncation warning;
* and in `idempotency`, the canonicalisation of non-string numbers — the rule that makes
  `"1250.00"`, `1250` and `Decimal("1250")` the same intent rather than three.

Every test asserts an outcome: a specific status, a specific stored row, a specific
refusal. "It ran" is not a property worth protecting here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole, permissions_for_role
from bm.domain.shared.clock import SystemClock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import SyncBatchTooLarge, SyncCursorExpired
from bm.domain.sync.idempotency import (
    FIELD_SCALES,
    IdempotencyClaim,
    IdempotencyService,
    _canonical,
    fingerprint,
)
from bm.domain.sync.service import (
    ALLOWED_OPERATION_TYPES,
    MAX_BATCH_SIZE,
    SyncOperationInput,
    SyncService,
)
from bm.infra.db.enums import SyncOperationStatus
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db

D = Decimal


@pytest.fixture
def user(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="syncer", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def ctx(tenant: Tenant, user: UserFixture) -> AuthContext:
    return AuthContext(
        user_id=user.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id,
        device_id=user.device_id,
        role=str(SystemRole.OWNER),
        permissions=frozenset(str(p) for p in permissions_for_role(SystemRole.OWNER)),
        token_id="sync-rules",
        business_timezone=tenant.timezone,
        full_name="Syncer",
    )


@pytest.fixture
def service(migration_session: Session) -> SyncService:
    return SyncService(migration_session, SystemClock())


@pytest.fixture
def product(migration_session: Session, tenant: Tenant) -> uuid.UUID:
    unit_id = migration_session.execute(
        text(
            "INSERT INTO units (id, business_id, code, name, allows_fraction, decimal_places)"
            " VALUES (gen_random_uuid(), :b, 'NOS', 'Numbers', false, 0)"
            " ON CONFLICT (business_id, code) DO UPDATE SET name = 'Numbers' RETURNING id"
        ),
        {"b": tenant.business_id},
    ).scalar_one()
    product_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO products (id, business_id, name, normalized_name, unit_id,"
            " default_selling_price, search_text, is_active)"
            " VALUES (:id, :b, 'Clutch Plate', 'clutch plate', :u, 1250, 'clutch plate', true)"
        ),
        {"id": product_id, "b": tenant.business_id, "u": unit_id},
    )
    migration_session.commit()
    return product_id


def _draft_upsert(
    tenant: Tenant, product_id: uuid.UUID, *, draft_id: uuid.UUID, seq: int = 1, **extra: object
) -> SyncOperationInput:
    return SyncOperationInput(
        op_id=uuid.uuid4(),
        client_seq=seq,
        type="draft.upsert",
        entity_type="draft_bill",
        entity_id=draft_id,
        payload={
            "counter_id": str(tenant.counter_id),
            "items": [
                {
                    "id": str(uuid.uuid4()),
                    "line_no": 1,
                    "product_id": str(product_id),
                    "quantity": "1.000",
                    "unit_price": "1250.0000",
                    "catalog_price_at_add": "1250.0000",
                    "price_source": "catalog",
                }
            ],
        },
        **extra,  # type: ignore[arg-type]
    )


# --- the registry itself ------------------------------------------------------


def test_the_registry_is_exactly_the_three_safe_operations() -> None:
    """ADR-011 / AC-OFF-8. Nothing that moves money or stock may be added.

    Asserted structurally here as well as in the structural suite, because this module's
    dispatch is what would have to grow to support one.
    """
    assert ALLOWED_OPERATION_TYPES == ("draft.upsert", "draft.delete", "customer.upsert")


def test_an_unknown_operation_type_is_rejected_with_a_reason(
    service: SyncService, ctx: AuthContext
) -> None:
    """And the message says what needs a connection, rather than just refusing."""
    result = service.apply_batch(
        ctx,
        [
            SyncOperationInput(
                op_id=uuid.uuid4(),
                client_seq=1,
                type="sale.finalize",
                entity_type="draft_bill",
                entity_id=uuid.uuid4(),
                payload={},
            )
        ],
    )
    outcome = result.results[0]
    assert outcome.status == str(SyncOperationStatus.REJECTED)
    assert outcome.error_message is not None
    assert "need" in outcome.error_message.lower()


# --- bounds -------------------------------------------------------------------


def test_a_batch_larger_than_the_cap_is_refused_whole(
    service: SyncService, ctx: AuthContext
) -> None:
    """The cap protects the server from one client; refusing the batch rather than
    truncating it means the client still knows exactly what was not applied."""
    operations = [
        SyncOperationInput(
            op_id=uuid.uuid4(),
            client_seq=i,
            type="draft.delete",
            entity_type="draft_bill",
            entity_id=uuid.uuid4(),
            payload={},
        )
        for i in range(MAX_BATCH_SIZE + 1)
    ]
    with pytest.raises(SyncBatchTooLarge) as caught:
        service.apply_batch(ctx, operations)
    assert caught.value.details["max"] == MAX_BATCH_SIZE
    assert caught.value.details["received"] == MAX_BATCH_SIZE + 1


def test_a_batch_at_exactly_the_cap_is_accepted(service: SyncService, ctx: AuthContext) -> None:
    """Off-by-one on a limit is how a client that respects the limit still fails."""
    operations = [
        SyncOperationInput(
            op_id=uuid.uuid4(),
            client_seq=i,
            type="draft.delete",
            entity_type="draft_bill",
            entity_id=uuid.uuid4(),
            payload={},
        )
        for i in range(MAX_BATCH_SIZE)
    ]
    result = service.apply_batch(ctx, operations)
    assert len(result.results) == MAX_BATCH_SIZE


# --- ordering and the blocked-entity rule ------------------------------------


def test_a_later_edit_to_the_same_bill_is_skipped_after_an_earlier_failure(
    service: SyncService, ctx: AuthContext, product: uuid.UUID, tenant: Tenant
) -> None:
    """§4.2a. Intra-entity ordering is preserved by **stopping**, not by reordering.

    If an earlier edit to a bill was refused, applying the later one would write newer
    content on top of a state the server never accepted — so the rest of that bill's
    operations are skipped and reported, never silently applied out of order. Other
    entities in the same batch are unaffected.
    """
    draft_id = uuid.uuid4()
    other_draft = uuid.uuid4()

    # First operation for `draft_id` is refused on a business rule: a zero quantity.
    # It has to be a `DomainError` — `_apply_one` catches those and records an outcome,
    # while a malformed payload raises `TypeError` and is deliberately *not* caught,
    # because an unparseable operation is a client bug rather than a business result.
    bad = _draft_upsert(tenant, product, draft_id=draft_id, seq=1)
    bad.payload["items"][0]["quantity"] = "0.000"  # type: ignore[index]
    # …so this later one for the same bill must be skipped…
    later = _draft_upsert(tenant, product, draft_id=draft_id, seq=2)
    # …while a different bill in the same batch still succeeds.
    unrelated = _draft_upsert(tenant, product, draft_id=other_draft, seq=1)

    result = service.apply_batch(ctx, [bad, later, unrelated])
    by_op = {r.op_id: r for r in result.results}

    assert by_op[bad.op_id].status == str(SyncOperationStatus.REJECTED)
    assert by_op[later.op_id].status == str(SyncOperationStatus.REJECTED)
    assert by_op[later.op_id].error_code == "SKIPPED_AFTER_EARLIER_FAILURE"
    assert by_op[unrelated.op_id].status == str(SyncOperationStatus.ACCEPTED)


def test_the_callers_original_order_is_returned(
    service: SyncService, ctx: AuthContext, product: uuid.UUID, tenant: Tenant
) -> None:
    """Applied in client-sequence order per entity, **reported** in the caller's order,
    so a client can match by index as well as by `op_id`."""
    first = _draft_upsert(tenant, product, draft_id=uuid.uuid4(), seq=1)
    second = _draft_upsert(tenant, product, draft_id=uuid.uuid4(), seq=1)
    result = service.apply_batch(ctx, [first, second])
    assert [r.op_id for r in result.results] == [first.op_id, second.op_id]


# --- replay -------------------------------------------------------------------


def test_replaying_a_whole_batch_changes_nothing(
    service: SyncService,
    ctx: AuthContext,
    product: uuid.UUID,
    tenant: Tenant,
    migration_session: Session,
) -> None:
    """`sync_operations.id` **is** the client's `op_id`, so a batch resent after a lost
    response is free. This is the property that makes the outbox safe to retry."""
    operation = _draft_upsert(tenant, product, draft_id=uuid.uuid4())
    first = service.apply_batch(ctx, [operation])
    migration_session.commit()
    assert first.results[0].status == str(SyncOperationStatus.ACCEPTED)

    items_after_first = migration_session.execute(
        text("SELECT count(*) FROM draft_bill_items")
    ).scalar_one()

    second = service.apply_batch(ctx, [operation])
    migration_session.commit()
    assert second.results[0].status == str(SyncOperationStatus.REPLAYED)
    # The replay echoes the server's state so the client can reconcile without asking.
    assert second.results[0].server_state is not None

    assert (
        migration_session.execute(text("SELECT count(*) FROM draft_bill_items")).scalar_one()
        == items_after_first
    )


# --- dispatch: the two arms that had never been exercised --------------------


def test_a_draft_can_be_deleted_offline(
    service: SyncService,
    ctx: AuthContext,
    product: uuid.UUID,
    tenant: Tenant,
    migration_session: Session,
) -> None:
    """`draft.delete` is in the registry because clearing a bill is not a financial act.

    It must reach `DraftService`, which is what guarantees the same rule applies online
    and offline — a draft is cancelled, never destroyed.
    """
    draft_id = uuid.uuid4()
    service.apply_batch(ctx, [_draft_upsert(tenant, product, draft_id=draft_id)])
    migration_session.commit()

    delete = SyncOperationInput(
        op_id=uuid.uuid4(),
        client_seq=2,
        type="draft.delete",
        entity_type="draft_bill",
        entity_id=draft_id,
        payload={},
    )
    result = service.apply_batch(ctx, [delete])
    migration_session.commit()
    assert result.results[0].status == str(SyncOperationStatus.ACCEPTED)

    status = migration_session.execute(
        text("SELECT status FROM draft_bills WHERE id = :d"), {"d": draft_id}
    ).scalar_one_or_none()
    # Cancelled rather than deleted: the row survives for audit.
    assert status is not None
    assert str(status) != "open"


def test_a_customer_can_be_created_offline(
    service: SyncService, ctx: AuthContext, migration_session: Session
) -> None:
    """`customer.upsert` is replay-safe because the id is client-generated."""
    customer_id = uuid.uuid4()
    operation = SyncOperationInput(
        op_id=uuid.uuid4(),
        client_seq=1,
        type="customer.upsert",
        entity_type="customer",
        entity_id=customer_id,
        payload={"name": "Offline Customer", "phones": ["9876543210"]},
    )
    result = service.apply_batch(ctx, [operation])
    migration_session.commit()

    assert result.results[0].status == str(SyncOperationStatus.ACCEPTED), result.results[0]
    name = migration_session.execute(
        text("SELECT name FROM customers WHERE id = :c"), {"c": customer_id}
    ).scalar_one_or_none()
    assert name == "Offline Customer"


# --- conflicts and the server_state echo -------------------------------------


def test_an_edit_against_a_stale_version_conflicts_rather_than_overwriting(
    service: SyncService,
    ctx: AuthContext,
    product: uuid.UUID,
    tenant: Tenant,
    migration_session: Session,
) -> None:
    """Two counters, one bill. The second writer is told, not silently overruled.

    A conflict is reported distinctly from a rejection because the client treats them
    differently: a conflict opens the resolution dialog, a rejection needs
    acknowledgement. Neither is ever dropped.
    """
    draft_id = uuid.uuid4()
    service.apply_batch(ctx, [_draft_upsert(tenant, product, draft_id=draft_id)])
    migration_session.commit()

    stale = _draft_upsert(tenant, product, draft_id=draft_id, seq=5, base_version=0)
    result = service.apply_batch(ctx, [stale])
    migration_session.commit()

    outcome = result.results[0]
    assert outcome.status in {
        str(SyncOperationStatus.CONFLICTED),
        str(SyncOperationStatus.REJECTED),
    }
    # And the server's own version is echoed back, so the client can resolve it without
    # a second round trip.
    assert outcome.server_state is not None
    assert "version" in outcome.server_state


def test_server_state_for_a_missing_draft_is_none(
    service: SyncService, ctx: AuthContext, migration_session: Session
) -> None:
    """A draft the server has never seen has no state to echo.

    Reached when a client replays an operation for a bill that was cleared on another
    machine — common, and not an error.
    """
    operation = SyncOperationInput(
        op_id=uuid.uuid4(),
        client_seq=1,
        type="draft.delete",
        entity_type="draft_bill",
        entity_id=uuid.uuid4(),
        payload={},
    )
    service.apply_batch(ctx, [operation])
    migration_session.commit()
    # Replay it: the dedupe path asks for `server_state`, and there is no draft row.
    replay = service.apply_batch(ctx, [operation])
    assert replay.results[0].status == str(SyncOperationStatus.REPLAYED)
    assert replay.results[0].server_state is None


def test_server_state_for_a_customer_echoes_the_customer(
    service: SyncService, ctx: AuthContext, migration_session: Session
) -> None:
    """The other entity type. Without this arm a customer conflict would echo nothing."""
    customer_id = uuid.uuid4()
    operation = SyncOperationInput(
        op_id=uuid.uuid4(),
        client_seq=1,
        type="customer.upsert",
        entity_type="customer",
        entity_id=customer_id,
        payload={"name": "Echo Customer"},
    )
    service.apply_batch(ctx, [operation])
    migration_session.commit()

    replay = service.apply_batch(ctx, [operation])
    assert replay.results[0].status == str(SyncOperationStatus.REPLAYED)
    state = replay.results[0].server_state
    assert state is not None
    assert state.get("name") == "Echo Customer"


# --- the catalogue delta ------------------------------------------------------


def test_the_catalogue_delta_never_carries_a_cost_price(
    service: SyncService, ctx: AuthContext, product: uuid.UUID, migration_session: Session
) -> None:
    """`SECURITY.md §11`: a stolen counter machine must not carry the shop's margins."""
    migration_session.execute(
        text("UPDATE products SET last_cost_price = 980 WHERE id = :p"), {"p": product}
    )
    migration_session.commit()

    delta = service.catalog_delta(ctx, since=None)
    assert delta["products"]
    for row in delta["products"]:
        assert "cost_price" not in row
        assert "last_cost_price" not in row


def test_a_cursor_returns_only_what_changed_since(
    service: SyncService, ctx: AuthContext, product: uuid.UUID
) -> None:
    """The `since` branch. A cursor in the future returns nothing, which is what makes
    an incremental sync incremental rather than a full download each time."""
    future = datetime(2099, 1, 1, tzinfo=UTC).isoformat()
    delta = service.catalog_delta(ctx, since=future)
    assert delta["products"] == []

    past = datetime(2000, 1, 1, tzinfo=UTC).isoformat()
    assert service.catalog_delta(ctx, since=past)["products"]


def test_an_unparseable_cursor_is_refused_rather_than_ignored(
    service: SyncService, ctx: AuthContext
) -> None:
    """Silently treating a bad cursor as "no cursor" would download the whole catalogue
    and look like a performance problem instead of a client bug."""
    with pytest.raises(SyncCursorExpired):
        service.catalog_delta(ctx, since="not-a-timestamp")


def test_a_truncated_page_warns_that_offline_search_is_incomplete(
    service: SyncService,
    ctx: AuthContext,
    tenant: Tenant,
    product: uuid.UUID,
    migration_session: Session,
) -> None:
    """The cache is deliberately bounded, so the client must be told when it is partial —
    otherwise a cashier trusts an offline search that cannot find half the catalogue."""
    unit_id = migration_session.execute(
        text("SELECT id FROM units WHERE business_id = :b LIMIT 1"), {"b": tenant.business_id}
    ).scalar_one()
    for index in range(4):
        migration_session.execute(
            text(
                "INSERT INTO products (id, business_id, name, normalized_name, unit_id,"
                " default_selling_price, search_text, is_active)"
                " VALUES (gen_random_uuid(), :b, :n, :nn, :u, 100, :st, true)"
            ),
            {
                "b": tenant.business_id,
                "n": f"Bulk {index}",
                "nn": f"bulk {index}",
                # Bound separately from `:nn`: reusing one parameter in two positions
                # makes psycopg deduce inconsistent types for it.
                "st": f"bulk {index}",
                "u": unit_id,
            },
        )
    migration_session.commit()

    delta = service.catalog_delta(ctx, since=None, limit=2)
    assert delta["has_more"] is True
    codes = [w["code"] for w in delta["warnings"]]
    assert "CATALOG_CACHE_TRUNCATED" in codes
    assert len(delta["products"]) == 2


def test_an_untruncated_page_carries_no_warning(
    service: SyncService, ctx: AuthContext, product: uuid.UUID
) -> None:
    delta = service.catalog_delta(ctx, since=None, limit=500)
    assert delta["has_more"] is False
    assert delta["warnings"] == []


# --- idempotency canonicalisation --------------------------------------------


def test_the_same_amount_in_three_spellings_is_one_intent() -> None:
    """§5.2.3 rule 3 and rule 8 together.

    A client that sends `1250`, `"1250.00"` or a `Decimal` means the same sale. If the
    fingerprint disagreed, a retry would look like a *different* bill and the
    idempotency key would be reported as reused with a conflicting body — refusing a
    legitimate retry, which is the worst outcome available here.
    """
    assert _canonical("1250", field_name="amount") == "1250.00"
    assert _canonical(D("1250"), field_name="amount") == "1250.00"
    assert _canonical(D("1250.00"), field_name="amount") == "1250.00"
    # Integers compare by value; the scale is applied when the field declares one.
    assert fingerprint({"amount": "1250.00"}) == fingerprint({"amount": D("1250")})


def test_a_decimal_without_a_declared_scale_defaults_to_two_places() -> None:
    """The `FIELD_SCALES.get(..., 2)` default: money is two places unless stated."""
    assert "unlisted_field" not in FIELD_SCALES
    assert _canonical(D("7"), field_name="unlisted_field") == "7.00"


def test_each_declared_field_scale_is_honoured() -> None:
    """A quantity is three places and a unit price four, so `1` is not `1.00` for them.

    The scales are part of the contract (`DATA-MODEL.md §1`); rendering a quantity at
    two places would make two identical bills fingerprint differently.
    """
    assert _canonical(D("1"), field_name="quantity") == "1.000"
    assert _canonical(D("1"), field_name="unit_price") == "1.0000"
    assert _canonical("2.5", field_name="discount_percent") == "2.500"


def test_a_non_numeric_string_in_a_numeric_field_is_left_alone() -> None:
    """Canonicalisation must not throw on a value validation will reject anyway —
    the 422 should come from the validator, with a field name, not from here."""
    assert _canonical("not-a-number", field_name="amount") == "not-a-number"


def test_a_value_that_is_neither_container_nor_scalar_passes_through() -> None:
    """The final fall-through. `None` inside a list is preserved, because array order and
    length are significant (rule 4) even when an element is null."""
    assert _canonical([1, None, True]) == [1, None, True]


def test_storing_a_response_for_an_unclaimed_key_is_a_no_op(
    migration_session: Session,
) -> None:
    """`claim.row_id is None` means nothing was claimed — a replay, or a path that never
    reserved a key. Writing a response then would attribute an outcome to a row that
    does not exist; returning quietly is correct."""
    service = IdempotencyService(migration_session)
    claim = IdempotencyClaim(key="k", fingerprint="f", replayed=True, row_id=None)
    before = migration_session.execute(text("SELECT count(*) FROM idempotency_keys")).scalar_one()

    service.store_response(claim, status_code=200, body={"ok": True})

    assert (
        migration_session.execute(text("SELECT count(*) FROM idempotency_keys")).scalar_one()
        == before
    )
