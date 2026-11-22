"""The recurring-job scheduler, running inside the API process.

## Why this exists, and what it replaced

`bm/worker.py` is a second entrypoint on the same image: a one-second loop that enqueued
three recurring job kinds from an in-process ticker and drained the queue. On Railway it
had to be a **second service**, and `docs/RAILWAY-ENVIRONMENT-VARIABLES.md` described it
as optional — so a deployment could (and by the audit's own account may well have) run
the API alone, in which case the low-stock scan, every reminder and every order nudge
simply never happened, silently, with nothing in the product reporting it.

The owner asked for that dependency to go away. This module is where the scheduling now
lives: an `asyncio` task owned by the FastAPI lifespan (`bm/main.py`), so scheduled work
runs exactly when the API runs and there is nothing extra to deploy.

## The four things that make it safe to put in a request-serving process

1. **It never blocks the event loop.** Every database call is synchronous SQLAlchemy —
   the same code the handlers already use — so each one is dispatched through
   `asyncio.to_thread`. A slow reconciliation delays the next tick, never a request.

2. **Exactly one process enqueues.** Enqueueing is guarded by a Postgres *session-level
   advisory lock* held on a dedicated connection for the scheduler's lifetime. A second
   replica, or the parent process of a `uvicorn --reload` pair, fails to acquire it and
   quietly does not enqueue. The per-minute `dedupe_key` from the old worker is kept as
   well — the lock makes duplicate enqueueing impossible, the dedupe key makes it
   impossible *again* across a lock handover, and the audit found the old pairing had a
   gap (a 300-second schedule deduped on a 60-second bucket).

3. **Draining is deliberately NOT lock-guarded.** `claim_and_run_one` claims with
   `FOR UPDATE SKIP LOCKED` and transitions `status`, which is what makes a Postgres
   queue safe for several consumers (ADR-004). Gating it would mean a replica that lost
   the enqueue lock also stopped parsing uploaded import files — turning a scheduling
   concern into a feature outage.

4. **Ticks never overlap.** One sequential loop. A tick that overruns its interval
   delays the next one rather than running beside it, so no handler can ever be entered
   twice concurrently by this scheduler.

## Observability

`status()` returns a plain dict reported by `/readyz`, so an operator can see whether the
scheduler is alive, whether this replica holds the enqueue lock, when each kind was last
enqueued, and the last error — without a new authenticated endpoint. A scheduler that has
silently died is the failure this exists to make visible.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

from bm.infra.db.session import get_engine, transaction

logger = logging.getLogger(__name__)

#: How often the loop wakes. Two seconds, not the worker's one: this process is also
#: serving requests, and the only cost of an idle tick is one indexed claim query that
#: returns no rows. Two seconds still means an uploaded import file starts parsing
#: within two seconds of its job row being committed, which is the responsiveness that
#: interval is actually chosen for.
TICK_SECONDS = 2.0

#: Recurring job kinds and how often each is enqueued, in seconds.
#:
#: `reminders.check_schedules` and `orders.nudge` stay at 60 s because both resolve
#: times to the minute — the 5 PM stock summary fires on the first check at or after
#: 17:00 business-local, and a one-time per-order `remind_at` is meant to land at the
#: minute the owner asked for. **The cadence a reminder re-fires at is a different
#: thing** and lives with the reminder: hourly for credit bills
#: (`reminder_schedules.interval_hours`) and hourly for pending orders
#: (`orders.NUDGE_INTERVAL_MINUTES`).
#:
#: `low_stock.scan` dropped from every 300 s to once a day. It is no longer the thing
#: that *notices* low stock — `InventoryService.apply_movements` does that the moment a
#: balance crosses its reorder level — so running it every five minutes was a
#: full-inventory sweep per business, forever, to re-derive a fact already known. It
#: stays as a daily reconciliation: a safety net that repairs anything event-driven
#: detection missed, and which is idempotent by construction (`notify_once` plus the
#: bulk resolve), so it raises nothing for a product already correctly represented.
SCHEDULES: dict[str, int] = {
    "low_stock.scan": 86_400,
    "reminders.check_schedules": 60,
    "orders.nudge": 60,
}

#: The advisory-lock key. An arbitrary but fixed 64-bit integer; it only has to be
#: unique among whatever else takes advisory locks on this database, and nothing else in
#: this application takes one.
ENQUEUE_LOCK_KEY = 7_315_204_118_002_461

#: Consecutive tick failures before the loop backs off to this longer sleep, so a
#: database that has gone away is not hammered once every two seconds.
_FAILURE_BACKOFF_THRESHOLD = 3
_FAILURE_BACKOFF_SECONDS = 30.0

#: Jobs drained per tick. Bounded so one tick cannot monopolise the thread pool while a
#: backlog clears; the next tick picks up where this one stopped.
_MAX_DRAIN_PER_TICK = 10


@dataclass
class _State:
    """Everything `status()` reports. Plain data, mutated only by the loop."""

    started_at: datetime | None = None
    holds_enqueue_lock: bool = False
    last_tick_at: datetime | None = None
    last_enqueued_at: dict[str, str] = field(default_factory=dict)
    jobs_run: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None


_state = _State()


def status() -> dict[str, Any]:
    """A snapshot for `/readyz`. Never raises; never touches the database."""
    return {
        "running": _state.started_at is not None,
        "started_at": _state.started_at.isoformat() if _state.started_at else None,
        "holds_enqueue_lock": _state.holds_enqueue_lock,
        "last_tick_at": _state.last_tick_at.isoformat() if _state.last_tick_at else None,
        "last_enqueued_at": dict(_state.last_enqueued_at),
        "jobs_run": _state.jobs_run,
        "consecutive_failures": _state.consecutive_failures,
        "last_error": _state.last_error,
        "schedules_seconds": dict(SCHEDULES),
    }


# --- the enqueue lock ---------------------------------------------------------


class _EnqueueLock:
    """A Postgres session-level advisory lock on its own connection.

    Session-level rather than transaction-level on purpose: the lock has to outlive the
    transactions the scheduler runs, so that "am I the enqueuing process" is a stable
    answer for the life of the process rather than a race re-run every tick.

    ## Two things this has to get right, both learned the hard way

    **1. The connection must not sit idle *in a transaction*.** `build_engine` sets
    `idle_in_transaction_session_timeout = 60000` on every connection, and SQLAlchemy
    opens a transaction implicitly on the first `execute`. Left as it was, Postgres
    terminated the lock connection sixty seconds after it was taken, releasing the
    advisory lock — while this object still believed it held one, because nothing
    re-checked. Two replicas could then both enqueue, silently. So the acquiring
    statement is **committed**: a session-level advisory lock survives `COMMIT` (that is
    precisely the difference from `pg_advisory_xact_lock`), and an idle-but-not-in-
    transaction connection is not subject to that timeout.

    **2. It must not eat a request-serving connection.** Held for the life of the
    process, a pooled connection would be one fewer for billing — on the production
    `BM_DB_POOL_SIZE=5` that is a fifth of the API's budget, spent on a lock. So it uses
    its own `NullPool` engine: the same single extra connection to Postgres, but outside
    the pool requests draw from.

    Liveness is re-checked on every tick rather than trusted, so a connection the
    database closed for any other reason (a Neon restart, a network drop) is noticed and
    the lock re-acquired — or found to be somebody else's now, which is equally correct.
    """

    def __init__(self) -> None:
        self._connection: Connection | None = None
        self._engine: Engine | None = None

    @property
    def held(self) -> bool:
        return self._connection is not None

    def _lock_engine(self) -> Engine:
        """A dedicated, unpooled engine for the one connection this holds."""
        if self._engine is None:
            self._engine = create_engine(
                get_engine().url,
                poolclass=NullPool,
                pool_pre_ping=True,
                future=True,
            )
        return self._engine

    def acquire(self) -> bool:
        """Try to take the lock. Returns whether this process now holds it."""
        if self._connection is not None:
            # Verify rather than assume. One trivial round trip per tick is the price of
            # "this process really is still the only one enqueueing"; believing a stale
            # flag is how two schedulers end up running at once.
            alive = False
            with contextlib.suppress(Exception):
                self._connection.execute(text("SELECT 1"))
                alive = True
            if alive:
                return True
            # The connection is gone, so the lock went with it. Fall through and take it
            # again — or discover that another process now holds it, which is equally
            # correct and is exactly what this re-check exists to notice.
            self.drop()

        connection: Connection | None = None
        try:
            connection = self._lock_engine().connect()
            acquired = bool(
                connection.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": ENQUEUE_LOCK_KEY}
                ).scalar()
            )
            # See the docstring: committing is what keeps Postgres from terminating this
            # connection — and the lock with it — after 60 seconds of idleness.
            connection.commit()
        except Exception:
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()
            raise
        if not acquired:
            # Somebody else is the enqueuing process. Release the connection rather than
            # holding an idle one open against Neon's connection budget for nothing.
            with contextlib.suppress(Exception):
                connection.close()
            return False
        self._connection = connection
        return True

    def release(self) -> None:
        """Give up the lock. Closing the connection would be enough — Postgres releases
        session locks on disconnect — but unlocking explicitly means a pooled connection
        cannot carry the lock away with it."""
        connection, self._connection = self._connection, None
        if connection is None:
            return
        with contextlib.suppress(Exception):
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ENQUEUE_LOCK_KEY})
            connection.commit()
        with contextlib.suppress(Exception):
            connection.close()
        self._dispose_engine()

    def drop(self) -> None:
        """Forget the connection without trying to talk to it. For the error path, where
        the connection is the thing that failed."""
        connection, self._connection = self._connection, None
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.close()
        self._dispose_engine()

    def _dispose_engine(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            with contextlib.suppress(Exception):
                engine.dispose()


# --- the work a tick does (synchronous; always called in a worker thread) -----


def enqueue_due(last_enqueued: dict[str, float], now_monotonic: float) -> list[str]:
    """Insert a job row for each kind whose interval has elapsed. Returns the kinds.

    Only ever called by the lock holder. `dedupe_key` is retained from the old worker
    and widened to match each kind's own interval rather than a fixed one-minute bucket:
    a daily schedule deduped per minute is not deduped at all across a restart, which is
    the gap the audit found. The bucket is now `floor(epoch / interval)`, so one bucket
    exists per interval per kind and a restart inside the current bucket cannot enqueue
    a second row.

    A kind this process has never enqueued is always due, whatever `now_monotonic` is —
    `time.monotonic()`'s reference point is undefined (it is commonly host uptime, not
    process start), so a freshly booted machine can return a value smaller than a kind's
    own interval. Anchoring "never enqueued" to `0.0` made that case silently wait out a
    full interval before ever running it once: harmless for the 60-second kinds (almost
    any host has more than 60 seconds of monotonic time by the first tick), but it meant
    `low_stock.scan`'s 86 400-second interval would not run on first boot unless the host
    already happened to have a day of monotonic uptime behind it — true by accident on a
    long-lived machine, false by construction on a fresh CI runner. `float("-inf")` makes
    "always due the first time" explicit rather than a coincidence of host uptime; the
    per-interval `dedupe_key` bucket above is what still stops two processes, or a
    restart within the same bucket, from enqueueing it twice.

    Separated from the loop so a test can drive it with an explicit clock.
    """
    from bm.infra.queue.runner import HANDLERS

    enqueued: list[str] = []
    epoch = time.time()
    for kind, interval in SCHEDULES.items():
        if kind not in HANDLERS:
            # No handler registered in this process: enqueueing would dead-letter.
            continue
        if now_monotonic < last_enqueued.get(kind, float("-inf")) + interval:
            continue
        last_enqueued[kind] = now_monotonic
        bucket = int(epoch // interval)
        with transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO jobs (id, kind, payload, status, dedupe_key)
                    VALUES (:id, :kind, '{}', 'queued', :dedupe)
                    ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
                    """
                ),
                {"id": uuid.uuid4(), "kind": kind, "dedupe": f"{kind}:{interval}:{bucket}"},
            )
        enqueued.append(kind)
    return enqueued


def drain(limit: int = _MAX_DRAIN_PER_TICK) -> int:
    """Run up to `limit` queued jobs. Returns how many ran.

    Not lock-guarded — see this module's docstring, point 3.
    """
    from bm.infra.queue.runner import claim_and_run_one

    ran = 0
    while ran < limit and claim_and_run_one(worker_id="api-scheduler"):
        ran += 1
    return ran


# --- the loop ------------------------------------------------------------------


class Scheduler:
    """Owns the asyncio task. One instance per process, created by the lifespan."""

    def __init__(self, *, tick_seconds: float = TICK_SECONDS) -> None:
        self._tick_seconds = tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._lock = _EnqueueLock()
        self._last_enqueued: dict[str, float] = {}

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        _state.started_at = datetime.now(UTC)
        _state.consecutive_failures = 0
        _state.last_error = None
        self._task = asyncio.create_task(self._run(), name="bm-scheduler")
        logger.info(
            "scheduler_started",
            extra={"tick_seconds": self._tick_seconds, "kinds": sorted(SCHEDULES)},
        )

    async def stop(self) -> None:
        """Ask the loop to finish the tick in hand, then stop. Mirrors the worker's
        graceful shutdown: a job half-run is worse than a shutdown that takes a moment."""
        self._stopping.set()
        task, self._task = self._task, None
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=self._tick_seconds + 10.0)
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await asyncio.to_thread(self._lock.release)
        _state.started_at = None
        _state.holds_enqueue_lock = False
        logger.info("scheduler_stopped", extra={"jobs_run": _state.jobs_run})

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.to_thread(self._tick)
                _state.consecutive_failures = 0
                _state.last_error = None
                sleep_for = self._tick_seconds
            except Exception as exc:  # recorded on the state and logged, never swallowed
                _state.consecutive_failures += 1
                _state.last_error = f"{type(exc).__name__}: {exc}"[:500]
                # The connection the lock rides on may be the thing that broke. Dropping
                # it means the next tick re-acquires cleanly rather than believing it
                # still holds a lock Postgres has already released.
                await asyncio.to_thread(self._lock.drop)
                _state.holds_enqueue_lock = False
                logger.exception(
                    "scheduler_tick_failed",
                    extra={"consecutive_failures": _state.consecutive_failures},
                )
                sleep_for = (
                    _FAILURE_BACKOFF_SECONDS
                    if _state.consecutive_failures >= _FAILURE_BACKOFF_THRESHOLD
                    else self._tick_seconds
                )
            # `wait_for` on the stop event rather than `sleep`, so shutdown is immediate
            # instead of waiting out the rest of an interval (or a 30-second backoff).
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=sleep_for)

    def _tick(self) -> None:
        """One scheduling pass. Synchronous — runs in a worker thread."""
        _state.last_tick_at = datetime.now(UTC)

        if self._lock.acquire():
            _state.holds_enqueue_lock = True
            enqueued = enqueue_due(self._last_enqueued, time.monotonic())
            for kind in enqueued:
                _state.last_enqueued_at[kind] = _state.last_tick_at.isoformat()
            if enqueued:
                logger.info("scheduler_enqueued", extra={"kinds": enqueued})
        else:
            _state.holds_enqueue_lock = False

        ran = drain()
        if ran:
            _state.jobs_run += ran
