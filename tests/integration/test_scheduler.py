"""The in-process scheduler (`infra/queue/scheduler.py`).

Drives it directly rather than through the app, because the behaviour under test is the
loop's own: who is allowed to enqueue, what happens when two processes try, whether a
restart duplicates work, and whether a tick that fails leaves the thing recoverable.

`tests/conftest.py` pins `scheduler_enabled=False` for the API fixture, so nothing here
is racing a scheduler that started by accident.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.infra.queue import scheduler as sched

pytestmark = pytest.mark.usefixtures("clean_db")


@pytest.fixture
def engine_bound(api_settings: object) -> None:
    """The scheduler reaches the database through the process-wide engine."""
    from bm.config import Settings
    from bm.infra.db.session import dispose_engine, init_engine

    assert isinstance(api_settings, Settings)
    dispose_engine()
    init_engine(api_settings)


@pytest.fixture
def handlers_registered() -> None:
    """`enqueue_due` skips any kind with no registered handler, so the registry has to
    be populated exactly as `bm/main.py`'s lifespan populates it."""
    import bm.domain.jobs.handlers  # noqa: F401


def _queued(session: Session, kind: str) -> int:
    return int(
        session.execute(
            text("SELECT count(*) FROM jobs WHERE kind = :kind"), {"kind": kind}
        ).scalar_one()
    )


# --- the schedule itself --------------------------------------------------------


def test_the_three_recurring_kinds_are_scheduled_at_their_required_cadences() -> None:
    """The owner's required timings, asserted as a fact about the code rather than left
    to a comment. The audit found nothing testing `_SCHEDULE_INTERVALS_SECONDS` at all,
    so a cadence could be changed silently."""
    assert sched.SCHEDULES["reminders.check_schedules"] == 60
    assert sched.SCHEDULES["orders.nudge"] == 60
    # The full-inventory sweep is a daily reconciliation now, not the detector.
    assert sched.SCHEDULES["low_stock.scan"] == 86_400


def test_the_pending_order_nudge_cadence_is_hourly_not_ten_minutes() -> None:
    """Owner request, 2026-11-22. The job is *checked* every 60 s (so a one-time
    `remind_at` is punctual); an unattended order *re-surfaces* hourly."""
    from bm.domain.orders.service import NUDGE_INTERVAL_MINUTES

    assert NUDGE_INTERVAL_MINUTES == 60


def test_the_built_in_reminders_keep_their_required_timings() -> None:
    from datetime import time as dt_time

    from bm.domain.reminders.service import _BUILTIN_DEFAULTS

    by_kind = {kind: (fire_time, interval) for kind, _, fire_time, interval in _BUILTIN_DEFAULTS}
    # Hourly credit reminder.
    assert by_kind["bill_reminder"] == (None, 1)
    # The 5 PM stock summary, retained.
    assert by_kind["stock_reminder"] == (dt_time(17, 0), None)


# --- enqueueing -----------------------------------------------------------------


def test_each_due_kind_is_enqueued_once(
    migration_session: Session, engine_bound: None, handlers_registered: None
) -> None:
    last: dict[str, float] = {}
    enqueued = sched.enqueue_due(last, time.monotonic())

    assert set(enqueued) == set(sched.SCHEDULES)
    for kind in sched.SCHEDULES:
        assert _queued(migration_session, kind) == 1, kind


def test_a_kind_is_not_re_enqueued_before_its_interval_elapses(
    migration_session: Session, engine_bound: None, handlers_registered: None
) -> None:
    now = time.monotonic()
    last: dict[str, float] = {}
    sched.enqueue_due(last, now)
    # Half a minute later: nothing is due again, not even the 60-second kinds.
    again = sched.enqueue_due(last, now + 30.0)

    assert again == []
    for kind in sched.SCHEDULES:
        assert _queued(migration_session, kind) == 1, kind


def test_a_restart_inside_the_current_bucket_cannot_enqueue_a_duplicate(
    migration_session: Session, engine_bound: None, handlers_registered: None
) -> None:
    """A restart resets the in-process clock, so the interval guard is gone. The
    `dedupe_key` is the second guard, and it is now bucketed per *that kind's own
    interval* — the audit found it bucketed per minute for every kind, which deduped a
    daily schedule not at all."""
    sched.enqueue_due({}, time.monotonic())
    before = {kind: _queued(migration_session, kind) for kind in sched.SCHEDULES}

    # A fresh process: empty `last_enqueued`, so every kind looks due immediately.
    sched.enqueue_due({}, time.monotonic())

    for kind in sched.SCHEDULES:
        assert _queued(migration_session, kind) == before[kind], (
            f"{kind} was enqueued twice across a simulated restart"
        )


def test_a_kind_is_due_immediately_even_on_a_freshly_booted_clock(
    migration_session: Session, engine_bound: None, handlers_registered: None
) -> None:
    """`time.monotonic()`'s reference point is undefined — a freshly booted CI runner can
    return a value far smaller than `low_stock.scan`'s 86 400-second interval. The first
    enqueue for a kind this process has never seen must not depend on the clock already
    having accumulated a day of uptime by accident; it must be due regardless."""
    last: dict[str, float] = {}
    enqueued = sched.enqueue_due(last, now_monotonic=1.0)

    assert set(enqueued) == set(sched.SCHEDULES), (
        "a kind was skipped on first enqueue because the monotonic clock started small "
        "— this is the bug where low_stock.scan never ran on a freshly booted host"
    )
    for kind in sched.SCHEDULES:
        assert _queued(migration_session, kind) == 1, kind


def test_a_kind_stays_not_due_until_its_own_interval_elapses_from_a_small_start(
    migration_session: Session, engine_bound: None, handlers_registered: None
) -> None:
    """Same near-zero starting clock as above, but now checking the *other* direction:
    once a kind has a recorded `last_enqueued`, it must still respect its own interval
    rather than being due on every call just because the clock is small."""
    last: dict[str, float] = {}
    sched.enqueue_due(last, now_monotonic=1.0)

    # 31 seconds later: short of every interval, including the 60-second kinds.
    again = sched.enqueue_due(last, now_monotonic=32.0)
    assert again == []

    # 61 seconds after the first enqueue: the 60-second kinds are due again, the daily
    # reconciliation is not.
    later = sched.enqueue_due(last, now_monotonic=62.0)
    assert set(later) == {"reminders.check_schedules", "orders.nudge"}
    assert "low_stock.scan" not in later


def test_a_kind_with_no_registered_handler_is_never_enqueued(
    migration_session: Session, engine_bound: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enqueueing a kind this process cannot run would dead-letter it with
    'No handler registered' — a job that fails for a deployment reason, silently."""
    from bm.infra.queue import runner

    monkeypatch.setattr(runner, "HANDLERS", {})
    assert sched.enqueue_due({}, time.monotonic()) == []
    for kind in sched.SCHEDULES:
        assert _queued(migration_session, kind) == 0, kind


# --- the advisory lock ----------------------------------------------------------


def test_only_one_process_holds_the_enqueue_lock(engine_bound: None) -> None:
    """Two replicas, or the parent and child of a `uvicorn --reload` pair, must not both
    enqueue. The second one to ask is told no."""
    first = sched._EnqueueLock()
    second = sched._EnqueueLock()
    try:
        assert first.acquire() is True
        assert first.held is True
        assert second.acquire() is False, "two processes both believe they may enqueue"
        assert second.held is False
    finally:
        first.release()
        second.release()


def test_the_lock_is_available_again_once_released(engine_bound: None) -> None:
    """So a replica restart, or a deploy that rolls one pod at a time, hands scheduling
    over rather than losing it permanently."""
    first = sched._EnqueueLock()
    assert first.acquire() is True
    first.release()

    second = sched._EnqueueLock()
    try:
        assert second.acquire() is True, "the lock was not released"
    finally:
        second.release()


def test_acquiring_twice_from_the_same_holder_is_idempotent(engine_bound: None) -> None:
    lock = sched._EnqueueLock()
    try:
        assert lock.acquire() is True
        assert lock.acquire() is True
    finally:
        lock.release()


# --- draining -------------------------------------------------------------------


def test_drain_runs_queued_jobs_and_reports_how_many(
    migration_session: Session, engine_bound: None, handlers_registered: None
) -> None:
    sched.enqueue_due({}, time.monotonic())
    migration_session.commit()

    ran = sched.drain()

    assert ran == len(sched.SCHEDULES)
    remaining = migration_session.execute(
        text("SELECT count(*) FROM jobs WHERE status = 'queued'")
    ).scalar_one()
    assert remaining == 0


def test_drain_is_bounded_so_one_tick_cannot_monopolise_the_thread(
    migration_session: Session, engine_bound: None, handlers_registered: None
) -> None:
    import uuid as uuid_module

    for _ in range(5):
        migration_session.execute(
            text(
                "INSERT INTO jobs (id, kind, payload, status) "
                "VALUES (:id, 'orders.nudge', '{}', 'queued')"
            ),
            {"id": uuid_module.uuid4()},
        )
    migration_session.commit()

    assert sched.drain(limit=2) == 2
    assert (
        migration_session.execute(
            text("SELECT count(*) FROM jobs WHERE status = 'queued'")
        ).scalar_one()
        == 3
    )


def test_drain_on_an_empty_queue_is_a_no_op(engine_bound: None, handlers_registered: None) -> None:
    assert sched.drain() == 0


# --- the loop: startup, shutdown, observability ---------------------------------


def test_the_scheduler_starts_runs_a_tick_and_stops_cleanly(
    migration_session: Session, engine_bound: None, handlers_registered: None
) -> None:
    """The lifecycle `bm/main.py`'s lifespan drives. A scheduler that cannot be stopped
    would hang every shutdown and every test run after this one."""

    async def exercise() -> dict[str, Any]:
        scheduler = sched.Scheduler(tick_seconds=0.05)
        await scheduler.start()
        # Long enough for several ticks, short enough not to slow the suite.
        await asyncio.sleep(0.6)
        snapshot = sched.status()
        await scheduler.stop()
        return snapshot

    during = asyncio.run(exercise())

    assert during["running"] is True
    assert during["holds_enqueue_lock"] is True
    assert during["last_tick_at"] is not None
    assert during["consecutive_failures"] == 0
    assert during["last_error"] is None

    after = sched.status()
    assert after["running"] is False
    assert after["holds_enqueue_lock"] is False

    # It really did the work, not just report that it was alive.
    for kind in sched.SCHEDULES:
        assert _queued(migration_session, kind) >= 1, kind


def test_status_reports_the_schedules_so_an_operator_can_see_the_cadences(
    engine_bound: None,
) -> None:
    assert sched.status()["schedules_seconds"] == dict(sched.SCHEDULES)


def test_a_failing_tick_is_recorded_and_the_loop_survives_it(
    engine_bound: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tick that raises must not kill the loop — a scheduler that dies on one bad
    database moment produces no 5 PM summary ever again, silently. And the failure has
    to be visible, which is what `/readyz` reports."""
    calls: list[int] = []

    def boom(self: object) -> None:
        calls.append(1)
        raise RuntimeError("database went away")

    monkeypatch.setattr(sched.Scheduler, "_tick", boom)

    async def exercise() -> dict[str, Any]:
        scheduler = sched.Scheduler(tick_seconds=0.05)
        await scheduler.start()
        await asyncio.sleep(0.3)
        snapshot = sched.status()
        await scheduler.stop()
        return snapshot

    during = asyncio.run(exercise())

    assert len(calls) >= 2, "the loop stopped after the first failure"
    assert during["consecutive_failures"] >= 1
    assert "database went away" in (during["last_error"] or "")
    assert during["running"] is True, "a failing tick must not take the scheduler down"


def test_a_recovered_tick_clears_the_failure_state(
    engine_bound: None, handlers_registered: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So a transient blip does not leave `/readyz` reporting a fault forever."""
    attempts: list[int] = []
    real_tick = sched.Scheduler._tick

    def flaky(self: sched.Scheduler) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        real_tick(self)

    monkeypatch.setattr(sched.Scheduler, "_tick", flaky)

    async def exercise() -> dict[str, Any]:
        scheduler = sched.Scheduler(tick_seconds=0.05)
        await scheduler.start()
        await asyncio.sleep(0.4)
        snapshot = sched.status()
        await scheduler.stop()
        return snapshot

    during = asyncio.run(exercise())

    assert len(attempts) >= 2
    assert during["consecutive_failures"] == 0
    assert during["last_error"] is None


def test_stopping_a_scheduler_that_never_started_is_harmless(engine_bound: None) -> None:
    asyncio.run(sched.Scheduler().stop())


def test_starting_twice_does_not_create_a_second_loop(
    engine_bound: None, handlers_registered: None
) -> None:
    """Two loops in one process would both drain and both enqueue — the duplicate
    execution the advisory lock protects *across* processes, reintroduced inside one."""

    async def exercise() -> None:
        scheduler = sched.Scheduler(tick_seconds=0.05)
        await scheduler.start()
        first = scheduler._task
        await scheduler.start()
        assert scheduler._task is first, "a second loop was created"
        await scheduler.stop()

    asyncio.run(exercise())


def test_the_lock_connection_is_not_left_idle_in_a_transaction(engine_bound: None) -> None:
    """`build_engine` sets `idle_in_transaction_session_timeout = 60000` on every
    connection, and SQLAlchemy opens a transaction on the first `execute`. A lock
    connection left in that state is terminated by Postgres after a minute — taking the
    advisory lock with it, silently, while the scheduler still believes it is the only
    process enqueueing.

    Asserted on the connection's own transaction state rather than by waiting a minute
    for Postgres to prove it, and rather than reading `pg_stat_activity`: that catalog is
    shared with every other connection the suite has open, so identifying "the lock's
    backend" from it is guesswork that depends on test order. Whether a transaction is
    open on this connection is the whole of what `idle in transaction` means, and it is
    knowable directly.

    `test_the_lock_survives_the_commit_that_closes_its_transaction` below is the other
    half: committing must not give the lock up.
    """
    lock = sched._EnqueueLock()
    try:
        assert lock.acquire() is True
        assert lock._connection is not None
        assert not lock._connection.in_transaction(), (
            "the lock connection has an open transaction, so Postgres will terminate it "
            "after idle_in_transaction_session_timeout and release the advisory lock "
            "without anything noticing"
        )
    finally:
        lock.release()


def test_the_lock_survives_the_commit_that_closes_its_transaction(
    engine_bound: None,
) -> None:
    """The reason `pg_try_advisory_lock` is correct here and `pg_advisory_xact_lock`
    would not be: a *session*-level advisory lock outlives the transaction it was taken
    in, so committing to avoid the idle timeout does not give the lock away."""
    holder = sched._EnqueueLock()
    contender = sched._EnqueueLock()
    try:
        assert holder.acquire() is True
        assert not holder._connection.in_transaction()  # type: ignore[union-attr]

        # Still exclusive after the commit.
        assert contender.acquire() is False
    finally:
        holder.release()
        contender.release()


def test_a_lost_lock_connection_is_noticed_and_re_acquired(engine_bound: None) -> None:
    """The other half: if the connection dies anyway — a Neon restart, a dropped network
    — the next tick must notice rather than carry on believing it holds the lock."""
    lock = sched._EnqueueLock()
    try:
        assert lock.acquire() is True

        # Kill it the way the database would.
        assert lock._connection is not None
        lock._connection.invalidate()
        lock._connection.close()

        # Still believed held by the naive flag; `acquire` re-checks and recovers.
        assert lock.acquire() is True
        assert lock.held is True
    finally:
        lock.release()


def test_the_lock_does_not_consume_a_request_serving_connection(engine_bound: None) -> None:
    """Held for the life of the process, a pooled connection would be one fewer for
    billing — a fifth of the budget on the production `BM_DB_POOL_SIZE=5`."""
    from bm.infra.db.session import get_engine

    pool = get_engine().pool
    before = pool.checkedout()

    lock = sched._EnqueueLock()
    try:
        assert lock.acquire() is True
        assert pool.checkedout() == before, (
            "the advisory lock checked out a connection from the request-serving pool"
        )
    finally:
        lock.release()


# --- reliability the production audit asked about --------------------------------


def test_shutdown_waits_for_the_tick_in_hand(
    engine_bound: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Graceful, not abrupt. A tick killed mid-handler would leave a `jobs` row stuck in
    `running` with no reaper to recover it — the job would simply be lost."""
    # `threading.Event`, not `asyncio.Event`: `_tick` runs in a worker thread via
    # `asyncio.to_thread`, and an asyncio primitive signalled from another thread is not
    # thread-safe (and `.wait()` there is an un-awaited coroutine, which is what the
    # first version of this test did).
    started = threading.Event()
    finished: list[bool] = []

    def slow_tick(self: object) -> None:
        started.set()
        time.sleep(0.3)
        finished.append(True)

    monkeypatch.setattr(sched.Scheduler, "_tick", slow_tick)

    async def exercise() -> None:
        scheduler = sched.Scheduler(tick_seconds=0.01)
        await scheduler.start()
        await asyncio.to_thread(started.wait, 5.0)
        await scheduler.stop()

    asyncio.run(exercise())

    assert finished, "stop() returned while a tick was still running"


def test_the_whole_database_going_away_is_survived_and_recovered_from(
    engine_bound: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neon restarts. A scheduler that dies on one bad database moment produces no 5 PM
    summary ever again, silently — so it must keep ticking and recover on its own."""
    calls: list[int] = []
    real_tick = sched.Scheduler._tick

    def flaky(self: sched.Scheduler) -> None:
        calls.append(1)
        if len(calls) <= 3:
            raise OSError("connection to server was lost")
        real_tick(self)

    monkeypatch.setattr(sched.Scheduler, "_tick", flaky)

    async def exercise() -> dict[str, Any]:
        scheduler = sched.Scheduler(tick_seconds=0.02)
        await scheduler.start()
        # Long enough to exceed the failure threshold, back off, and then recover.
        await asyncio.sleep(_backoff_window())
        snapshot = sched.status()
        await scheduler.stop()
        return snapshot

    after = asyncio.run(exercise())

    assert len(calls) > 3, "the loop stopped instead of retrying"
    assert after["consecutive_failures"] == 0, "recovery did not clear the failure state"
    assert after["last_error"] is None
    assert after["running"] is True


def _backoff_window() -> float:
    """Long enough for three failures, the backoff sleep, and a recovered tick."""
    return sched._FAILURE_BACKOFF_SECONDS + 2.0


def test_readyz_reports_the_scheduler_accurately(
    api_client: TestClient, engine_bound: None
) -> None:
    """The operator's only window onto background work. `tests/conftest.py` pins the
    scheduler **off** for the API fixture, so this asserts the honest negative — a
    `/readyz` that claimed `running: true` with no loop would be worse than no check."""
    body = api_client.get("/readyz").json()

    assert "scheduler" in body["checks"]
    reported = body["checks"]["scheduler"]
    assert reported["running"] is False
    assert reported["holds_enqueue_lock"] is False
    # The cadences are published so an operator can see them without reading the source.
    assert reported["schedules_seconds"] == dict(sched.SCHEDULES)


def test_readyz_stays_ready_even_when_the_scheduler_is_not_running(
    api_client: TestClient, engine_bound: None
) -> None:
    """Refusing traffic because a background loop is wedged would take the shop's billing
    offline to protest a reminder. The scheduler is reported, never gating."""
    response = api_client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["checks"]["scheduler"]["running"] is False
