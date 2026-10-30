"""Postgres job queue consumer.

`FOR UPDATE SKIP LOCKED` so several workers can claim disjoint jobs without a broker
(ADR-004). Handlers register themselves in `HANDLERS`; each one must be idempotent on
the key named in `ARCHITECTURE.md §7`, because at-least-once delivery is the contract.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from bm.infra.db.session import transaction

logger = logging.getLogger(__name__)

#: kind → handler. Populated by the milestone that owns each job.
HANDLERS: dict[str, Callable[[dict[str, Any]], None]] = {}

_CLAIM_SQL = text(
    """
    UPDATE jobs
       SET status = 'running',
           attempts = attempts + 1,
           locked_at = now(),
           locked_by = :worker
     WHERE id = (
            SELECT id
              FROM jobs
             WHERE status = 'queued'
               AND run_after <= now()
             ORDER BY run_after
             FOR UPDATE SKIP LOCKED
             LIMIT 1
           )
    RETURNING id, kind, payload, attempts, max_attempts
    """
)


def _jobs_table_exists() -> bool:
    with transaction() as session:
        row = session.execute(text("SELECT to_regclass('jobs') IS NOT NULL")).scalar()
    return bool(row)


def claim_and_run_one(worker_id: str = "worker") -> bool:
    """Claim and execute one job. Returns False if nothing was available."""
    if not _jobs_table_exists():
        return False

    with transaction() as session:
        claimed = session.execute(_CLAIM_SQL, {"worker": worker_id}).first()
        if claimed is None:
            return False
        job_id, kind, payload, attempts, max_attempts = claimed

    handler = HANDLERS.get(kind)
    if handler is None:
        _finish(job_id, status="dead", error=f"No handler registered for kind {kind!r}")
        logger.error("job_no_handler", extra={"job_id": str(job_id), "kind": kind})
        return True

    try:
        handler(payload or {})
    except Exception as exc:  # noqa: BLE001 - recorded on the row, never swallowed
        # Exponential backoff, then a visible dead-letter state. Never silently
        # abandoned (`ARCHITECTURE.md §7`).
        if attempts >= max_attempts:
            _finish(job_id, status="dead", error=str(exc)[:2000])
            logger.error("job_dead", extra={"job_id": str(job_id), "kind": kind})
        else:
            backoff_seconds = min(2**attempts, 3600)
            _retry(job_id, error=str(exc)[:2000], backoff_seconds=backoff_seconds)
            logger.warning(
                "job_retry",
                extra={"job_id": str(job_id), "kind": kind, "in_seconds": backoff_seconds},
            )
        return True

    _finish(job_id, status="succeeded", error=None)
    logger.info("job_succeeded", extra={"job_id": str(job_id), "kind": kind})
    return True


def _finish(job_id: Any, *, status: str, error: str | None) -> None:
    with transaction() as session:
        session.execute(
            text(
                "UPDATE jobs SET status = :status, last_error = :error,"
                " locked_by = NULL, locked_at = NULL, updated_at = now()"
                " WHERE id = :id"
            ),
            {"status": status, "error": error, "id": job_id},
        )


def _retry(job_id: Any, *, error: str, backoff_seconds: int) -> None:
    with transaction() as session:
        session.execute(
            text(
                "UPDATE jobs SET status = 'queued', last_error = :error,"
                " run_after = now() + make_interval(secs => :backoff),"
                " locked_by = NULL, locked_at = NULL, updated_at = now()"
                " WHERE id = :id"
            ),
            {"error": error, "backoff": backoff_seconds, "id": job_id},
        )
