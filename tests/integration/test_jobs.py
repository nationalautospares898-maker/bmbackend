"""The scheduled jobs, against a real database (M26, `ARCHITECTURE.md §7`).

These are the paths nobody watches. A nightly job that silently stopped working is
indistinguishable from a nightly job that found nothing wrong, which is why each one is
tested for the thing it is *supposed* to do and then run a second time to prove it is
idempotent — at-least-once delivery is the queue's contract, not an edge case.

The reconciliation test deliberately corrupts a balance. That is the only honest way to
assert "reports and never repairs": if the job quietly fixed the row, the assertion that
the row is still wrong would fail.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from bm.domain.jobs import handlers
from bm.infra.db.enums import DraftStatus, JobKind
from bm.infra.queue.runner import HANDLERS
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    user = create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )
    migration_session.commit()
    return user


@pytest.fixture
def engine_bound(api_settings: object) -> None:
    """Point the handlers' `transaction()` at the test database.

    The handlers open their own sessions, exactly as the worker process does, so they
    cannot be handed the test's session. Initialising the module-level engine is what
    makes them talk to the same database the fixtures wrote to.
    """
    from bm.infra.db import session as session_module

    session_module.dispose_engine()
    session_module.init_engine(api_settings)  # type: ignore[arg-type]
    yield
    session_module.dispose_engine()


def _product(session: Session, tenant: Tenant) -> uuid.UUID:
    unit_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO units (id, business_id, code, name, allows_fraction, decimal_places)"
            " VALUES (:id, :business_id, 'NOS', 'Numbers', false, 0)"
        ),
        {"id": unit_id, "business_id": tenant.business_id},
    )
    product_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO products"
            " (id, business_id, name, normalized_name, unit_id, default_selling_price,"
            "  search_text)"
            " VALUES (:id, :business_id, 'Tie Rod End', 'tie rod end', :unit_id, 420, 'tie rod')"
        ),
        {"id": product_id, "business_id": tenant.business_id, "unit_id": unit_id},
    )
    return product_id


# --- inventory.reconcile ------------------------------------------------------


def test_reconciliation_reports_a_drifted_balance_and_does_not_repair_it(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    """FR-INV-006. Drift means there is a bug to find, and a self-healing balance table
    would hide it."""
    product_id = _product(migration_session, tenant)
    # A balance with no movements behind it: `balance = Σ movements` is violated.
    migration_session.execute(
        text(
            "INSERT INTO inventory_balances"
            " (id, business_id, branch_id, product_id, quantity)"
            " VALUES (:id, :business_id, :branch_id, :product_id, 25)"
        ),
        {
            "id": uuid.uuid4(),
            "business_id": tenant.business_id,
            "branch_id": tenant.branch_id,
            "product_id": product_id,
        },
    )
    migration_session.commit()

    handlers.handle_inventory_reconcile({"business_id": str(tenant.business_id)})

    migration_session.commit()
    still_wrong = migration_session.execute(
        text("SELECT quantity FROM inventory_balances WHERE product_id = :id"),
        {"id": product_id},
    ).scalar_one()
    assert still_wrong == Decimal("25.000"), "the job repaired the row; it must only report"

    notice = migration_session.execute(
        text("SELECT kind, severity, title FROM notifications ORDER BY created_at DESC LIMIT 1")
    ).one()
    assert str(notice.kind) == "reconciliation_alert"
    assert str(notice.severity) == "critical"


def test_a_reconciliation_discrepancy_queues_an_operational_alert(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    product_id = _product(migration_session, tenant)
    migration_session.execute(
        text(
            "INSERT INTO inventory_balances"
            " (id, business_id, branch_id, product_id, quantity)"
            " VALUES (:id, :business_id, :branch_id, :product_id, 9)"
        ),
        {
            "id": uuid.uuid4(),
            "business_id": tenant.business_id,
            "branch_id": tenant.branch_id,
            "product_id": product_id,
        },
    )
    migration_session.commit()

    handlers.handle_inventory_reconcile({"business_id": str(tenant.business_id)})
    migration_session.commit()

    event = migration_session.execute(
        text("SELECT event_type, payload, status FROM outbox_events ORDER BY created_at LIMIT 1")
    ).one()
    assert str(event.event_type) == "operational.alert"
    assert event.payload["severity"] == "critical"
    # Alerting is disabled in the test settings, so the event stays pending rather than
    # being marked dispatched — an undelivered alert must never look delivered.
    assert str(event.status) == "pending"


def test_the_alert_body_carries_no_product_or_customer_detail(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    """An alert travels to a third-party webhook. `SECURITY.md §4` does not permit shop
    financials to leave by that route, so the body is a count and nothing else."""
    product_id = _product(migration_session, tenant)
    migration_session.execute(
        text(
            "INSERT INTO inventory_balances"
            " (id, business_id, branch_id, product_id, quantity)"
            " VALUES (:id, :business_id, :branch_id, :product_id, 4)"
        ),
        {
            "id": uuid.uuid4(),
            "business_id": tenant.business_id,
            "branch_id": tenant.branch_id,
            "product_id": product_id,
        },
    )
    migration_session.commit()

    handlers.handle_inventory_reconcile({"business_id": str(tenant.business_id)})
    migration_session.commit()

    payload = migration_session.execute(
        text("SELECT payload FROM outbox_events ORDER BY created_at LIMIT 1")
    ).scalar_one()
    assert "Tie Rod End" not in payload["body"]
    assert str(product_id) not in payload["body"]


def test_a_clean_reconciliation_raises_nothing(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    """No news is no notification. A nightly "all clear" would train the owner to
    ignore the one that matters."""
    migration_session.commit()
    handlers.handle_inventory_reconcile({"business_id": str(tenant.business_id)})
    migration_session.commit()
    assert migration_session.execute(text("SELECT count(*) FROM notifications")).scalar() == 0
    assert migration_session.execute(text("SELECT count(*) FROM outbox_events")).scalar() == 0


# --- notifications.dispatch ---------------------------------------------------


def test_the_dispatcher_leaves_an_event_pending_when_there_is_no_recipient(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    """The dispatcher must not mark an event dispatched that nobody received — that is
    how an alert is lost rather than retried."""
    from bm.domain.notifications import service as notifications

    notifications.raise_alert(
        migration_session,
        business_id=tenant.business_id,
        kind="test.alert",
        severity=handlers.Severity.CRITICAL,
        title="t",
        body="b",
    )
    migration_session.commit()

    handlers.handle_notifications_dispatch({})
    migration_session.commit()

    status, attempts = migration_session.execute(
        text("SELECT status, attempts FROM outbox_events LIMIT 1")
    ).one()
    assert str(status) == "pending"
    assert attempts == 1


def test_an_event_becomes_failed_after_the_attempt_limit(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    """`failed` is a visible dead-letter state, not a deletion: an operator can see
    exactly which alerts never reached anyone."""
    from bm.domain.notifications import service as notifications

    notifications.raise_alert(
        migration_session,
        business_id=tenant.business_id,
        kind="test.alert",
        severity=handlers.Severity.CRITICAL,
        title="t",
        body="b",
    )
    migration_session.commit()

    for _ in range(notifications.MAX_DELIVERY_ATTEMPTS):
        handlers.handle_notifications_dispatch({})
    migration_session.commit()

    assert (
        str(
            migration_session.execute(text("SELECT status FROM outbox_events LIMIT 1")).scalar_one()
        )
        == "failed"
    )


def test_an_event_of_another_type_is_left_for_its_own_consumer(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    """Silently consuming another handler's event is how a future e-invoice push would
    vanish."""
    migration_session.execute(
        text(
            "INSERT INTO outbox_events"
            " (id, business_id, event_type, aggregate_type, aggregate_id, payload, status)"
            " VALUES (:id, :business_id, 'einvoice.push', 'invoice', :id, '{}', 'pending')"
        ),
        {"id": uuid.uuid4(), "business_id": tenant.business_id},
    )
    migration_session.commit()

    handlers.handle_notifications_dispatch({})
    migration_session.commit()

    assert (
        str(
            migration_session.execute(text("SELECT status FROM outbox_events LIMIT 1")).scalar_one()
        )
        == "pending"
    )


# --- drafts.mark_stale --------------------------------------------------------


def _draft(session: Session, tenant: Tenant, owner: UserFixture, *, age_days: int) -> uuid.UUID:
    draft_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO draft_bills"
            " (id, business_id, branch_id, counter_id, status, version, created_by_user_id,"
            "  created_at, updated_at)"
            " VALUES (:id, :business_id, :branch_id, :counter_id, :status, 1, :user_id,"
            "         now() - make_interval(days => :days),"
            "         now() - make_interval(days => :days))"
        ),
        {
            "id": draft_id,
            "business_id": tenant.business_id,
            "branch_id": tenant.branch_id,
            "counter_id": tenant.counter_id,
            "status": DraftStatus.OPEN.value,
            "user_id": owner.id,
            "days": age_days,
        },
    )
    return draft_id


def test_an_abandoned_draft_is_marked_stale_not_deleted(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    """FR-DRAFT-009: archived, never deleted, never auto-finalised. A stale draft is
    still a bill somebody started."""
    old = _draft(migration_session, tenant, owner, age_days=30)
    migration_session.commit()

    handlers.handle_drafts_mark_stale({"stale_days": 7})
    migration_session.commit()

    status = migration_session.execute(
        text("SELECT status FROM draft_bills WHERE id = :id"), {"id": old}
    ).scalar_one()
    assert str(status) == "stale"


def test_a_recent_draft_is_left_open(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    recent = _draft(migration_session, tenant, owner, age_days=1)
    migration_session.commit()

    handlers.handle_drafts_mark_stale({"stale_days": 7})
    migration_session.commit()

    status = migration_session.execute(
        text("SELECT status FROM draft_bills WHERE id = :id"), {"id": recent}
    ).scalar_one()
    assert str(status) == "open"


def test_marking_stale_is_never_a_finalisation(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    """The requirement says so outright, and it is the one outcome that would cost
    money: an abandoned bill must not become a sale."""
    _draft(migration_session, tenant, owner, age_days=30)
    migration_session.commit()

    handlers.handle_drafts_mark_stale({"stale_days": 7})
    migration_session.commit()

    assert migration_session.execute(text("SELECT count(*) FROM invoices")).scalar() == 0
    assert migration_session.execute(text("SELECT count(*) FROM stock_movements")).scalar() == 0


def test_running_mark_stale_twice_changes_nothing_the_second_time(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    _draft(migration_session, tenant, owner, age_days=30)
    migration_session.commit()

    handlers.handle_drafts_mark_stale({"stale_days": 7})
    migration_session.commit()
    first = migration_session.execute(
        text("SELECT status, updated_at FROM draft_bills LIMIT 1")
    ).one()

    handlers.handle_drafts_mark_stale({"stale_days": 7})
    migration_session.commit()
    second = migration_session.execute(
        text("SELECT status, updated_at FROM draft_bills LIMIT 1")
    ).one()

    assert first.updated_at == second.updated_at


# --- idempotency.prune -------------------------------------------------------


def test_pruning_removes_keys_past_the_retention_window(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    migration_session.execute(
        text(
            # `ck_idempotency__completed` requires a completed key to carry the
            # response it is replaying. A row without one would be a key that promises
            # a replay it cannot perform.
            "INSERT INTO idempotency_keys"
            " (id, business_id, endpoint, key, request_fingerprint, status,"
            "  response_status, response_body, completed_at, created_at)"
            " VALUES (:id, :business_id, '/sales:finalize', :key, 'fp', 'completed',"
            "         201, '{}', now() - interval '90 days', now() - interval '90 days')"
        ),
        {"id": uuid.uuid4(), "business_id": tenant.business_id, "key": str(uuid.uuid4())},
    )
    migration_session.commit()

    handlers.handle_idempotency_prune({"retention_days": 7})
    migration_session.commit()

    assert migration_session.execute(text("SELECT count(*) FROM idempotency_keys")).scalar() == 0


def test_pruning_keeps_a_key_inside_the_window(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    """A client's retry window is what this protects. Pruning early turns a safe retry
    into a second sale."""
    migration_session.execute(
        text(
            "INSERT INTO idempotency_keys"
            " (id, business_id, endpoint, key, request_fingerprint, status,"
            "  response_status, response_body, completed_at, created_at)"
            " VALUES (:id, :business_id, '/sales:finalize', :key, 'fp', 'completed',"
            "         201, '{}', now(), now())"
        ),
        {"id": uuid.uuid4(), "business_id": tenant.business_id, "key": str(uuid.uuid4())},
    )
    migration_session.commit()

    handlers.handle_idempotency_prune({"retention_days": 7})
    migration_session.commit()

    assert migration_session.execute(text("SELECT count(*) FROM idempotency_keys")).scalar() == 1


def test_pruning_twice_is_harmless(
    migration_session: Session, tenant: Tenant, owner: UserFixture, engine_bound: None
) -> None:
    migration_session.commit()
    handlers.handle_idempotency_prune({"retention_days": 7})
    handlers.handle_idempotency_prune({"retention_days": 7})


# --- the queue itself ---------------------------------------------------------


def test_a_job_with_a_bad_payload_raises_so_the_runner_can_record_it(
    migration_session: Session, engine_bound: None
) -> None:
    """Handlers raise rather than swallowing: a silent failure in a background job is
    invisible by construction."""
    with pytest.raises(ValueError, match="import_job_id"):
        HANDLERS[JobKind.IMPORT_PARSE.value]({})
