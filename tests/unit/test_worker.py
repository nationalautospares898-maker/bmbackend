"""The worker process can actually run something it claims (2026-11-10).

`bm.worker` and `bm.infra.queue.runner` are infrastructure and deliberately never
import `bm.domain` themselves (`ARCHITECTURE.md §1`, ADR-002's layering) — a handler is
registered into `runner.HANDLERS` only as the *side effect* of importing
`bm.domain.jobs.handlers`. Before this test existed, nothing made that import happen in
the actual worker entrypoint: `tests/integration/test_jobs.py` imports the handlers
module by hand to exercise `claim_and_run_one` in a *test* process, which is exactly the
same workaround a real worker needed and never had.

The practical consequence, had it shipped: `python -m bm.worker` claims a job with
`FOR UPDATE SKIP LOCKED`, finds nothing in an empty `HANDLERS`, and marks it dead with
"No handler registered" — for *every* kind, including the five that already existed
(`import.parse`, `inventory.reconcile`, `idempotency.prune`, `drafts.mark_stale`,
`notifications.dispatch`), not only the three this session added. Nightly reconciliation,
import parsing, and the alert dispatcher would never have actually run in any
deployment using this exact worker process unmodified.
"""

from __future__ import annotations

import importlib
import sys


def test_importing_the_worker_module_registers_every_job_handler() -> None:
    """Simulates `python -m bm.worker`'s own import, not a test-only workaround."""
    for name in ("bm.worker", "bm.domain.jobs.handlers"):
        sys.modules.pop(name, None)

    from bm.infra.db.enums import JobKind
    from bm.infra.queue.runner import HANDLERS

    HANDLERS.clear()
    assert HANDLERS == {}, "test setup: the registry should start empty"

    importlib.import_module("bm.worker")

    missing = [kind.value for kind in JobKind if kind.value not in HANDLERS]
    assert missing == [], (
        f"importing bm.worker left these job kinds unregistered: {missing} — "
        "the worker process would dead-letter them with 'No handler registered'"
    )
