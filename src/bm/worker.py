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
from types import FrameType

from bm.config import get_settings
from bm.infra.db.session import dispose_engine, init_engine
from bm.infra.logging.setup import configure_logging

logger = logging.getLogger("bm.worker")

_POLL_INTERVAL_SECONDS = 1.0


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
    try:
        while not flag.requested:
            claimed = _claim_and_run_one()
            if not claimed:
                time.sleep(_POLL_INTERVAL_SECONDS)
    finally:
        dispose_engine()
        logger.info("worker_stopped")


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
