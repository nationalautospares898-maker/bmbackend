"""Job worker entrypoint.

Same image as the API, different entrypoint (`ARCHITECTURE.md §1`), so a job can never
drift from the service it calls. Jobs are rows in a Postgres table claimed with
`FOR UPDATE SKIP LOCKED` — no Redis, no broker to operate (ADR-004), and enqueueing is
transactional with business data.

The job handlers themselves land with their milestones (`import.parse` with M23,
`inventory.reconcile` with M6/M26, `idempotency.prune` with M10). This module is the
loop and the shutdown handling.
"""

from __future__ import annotations

import logging
import signal
import time
import uuid
from types import FrameType

from sqlalchemy import text

# Registers every handler into `bm.infra.queue.runner.HANDLERS` as a side effect of
# import. Without this, the worker process's registry is empty — `runner.py`
# deliberately never imports `domain` itself (`ARCHITECTURE.md §1`'s layering, ADR-002)
# — and `claim_and_run_one` would mark every job "No handler registered" and dead-letter
# it, silently, for every kind that exists: this one import is what makes the worker
# able to run anything at all. `tests/integration/test_jobs.py` already has to do this
# same import by hand to exercise `claim_and_run_one` in a test process; this is the
# same requirement, met once, for the process that actually matters.
import bm.domain.jobs.handlers  # noqa: F401
from bm.config import get_settings
from bm.infra.db.session import dispose_engine, init_engine
from bm.infra.logging.setup import configure_logging

logger = logging.getLogger("bm.worker")

_POLL_INTERVAL_SECONDS = 1.0

#: How often this process enqueues each recurring job, in seconds. Deliberately an
#: in-process ticker rather than an external cron (`ARCHITECTURE.md §7` notes nothing
#: enqueues `inventory.reconcile`/`drafts.mark_stale` today beyond an operator's own
#: schedule) — Feature A/B's reminders need to work out of the box on a single-owner
#: install with nothing extra to configure.
_SCHEDULE_INTERVALS_SECONDS: dict[str, int] = {
    "low_stock.scan": 300,
    "reminders.check_schedules": 60,
    "orders.nudge": 60,
}


class _ShutdownFlag:
    """Graceful shutdown: finish the job in hand, then stop."""

    def __init__(self) -> None:
        self.requested = False

    def request(self, signum: int, _frame: FrameType | None) -> None:
        logger.info("shutdown_requested", extra={"signal": signum})
        self.requested = True


def run() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    init_engine(settings)

    flag = _ShutdownFlag()
    signal.signal(signal.SIGTERM, flag.request)
    signal.signal(signal.SIGINT, flag.request)

    logger.info("worker_started", extra={"concurrency": settings.worker_concurrency})
    last_enqueued: dict[str, float] = {}
    try:
        while not flag.requested:
            _maybe_enqueue_scheduled_jobs(last_enqueued)
            claimed = _claim_and_run_one()
            if not claimed:
                time.sleep(_POLL_INTERVAL_SECONDS)
    finally:
        dispose_engine()
        logger.info("worker_stopped")


def _maybe_enqueue_scheduled_jobs(last_enqueued: dict[str, float]) -> None:
    """Enqueue each recurring job kind once its interval has elapsed.

    `dedupe_key` is still time-bucketed (per-minute) on top of the in-process
    `last_enqueued` guard, so a worker restart — which resets `last_enqueued` to empty —
    cannot enqueue a duplicate within the same minute another process (or the previous
    run) already queued; the two guards cover each other's gap.
    """
    from bm.infra.db.session import transaction
    from bm.infra.queue.runner import HANDLERS

    now = time.monotonic()
    for kind, interval in _SCHEDULE_INTERVALS_SECONDS.items():
        if kind not in HANDLERS:
            continue
        due_at = last_enqueued.get(kind, 0.0) + interval
        if now < due_at:
            continue
        last_enqueued[kind] = now
        bucket = int(time.time() // 60)
        try:
            with transaction() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO jobs (id, kind, payload, status, dedupe_key)
                        VALUES (:id, :kind, '{}', 'queued', :dedupe)
                        ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
                        """
                    ),
                    {"id": uuid.uuid4(), "kind": kind, "dedupe": f"{kind}:{bucket}"},
                )
        except Exception:
            logger.exception("scheduled_enqueue_failed", extra={"kind": kind})


def _claim_and_run_one() -> bool:
    """Claim a single job and run it. Returns False when the queue is empty.

    The `jobs` table lands with migration 0015 (M14/M23). Until then the loop idles,
    which keeps the container in the compose topology honest rather than pretending the
    queue exists.
    """
    from bm.infra.queue.runner import claim_and_run_one

    return claim_and_run_one()


if __name__ == "__main__":
    run()
