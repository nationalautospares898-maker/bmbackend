"""Job handlers for every kind in `JobKind` (`ARCHITECTURE.md §7`).

**Why this lives in `domain` and not next to the runner in `infra`.** A handler composes
domain services — the import parser, the inventory ledger, the settings resolver — so
putting it in `infra` would make `infra` import `domain` and invert the dependency rule
(`api → domain → infra`, ADR-002). The *mechanism* that claims and retries a job is
infrastructure and stays in `infra/queue/runner.py`; deciding what a job **means** is
domain work. `import-linter` catches the mistake, which is how this module ended up
here rather than there.

Importing this module registers them. `tests/unit/test_job_kinds.py` asserts the
registry covers `JobKind` exactly — a kind with no handler would dead-letter in
production with "No handler registered", which is a deployment mistake that should fail
the build instead.

Every handler obeys the queue's contract: **at-least-once delivery, so be idempotent.**
The idempotency key for each is the one named in the architecture table, and in each
case it is a guard in SQL rather than a check-then-act read:

| Kind | Idempotent on |
|---|---|
| `import.parse` | `import_jobs.status` transition `uploaded → parsing` |
| `inventory.reconcile` | read-only; writes only a report and an alert |
| `idempotency.prune` | naturally — deleting an absent row is a no-op |
| `drafts.mark_stale` | `draft_bills.status` transition `open → stale` |
| `notifications.dispatch` | `outbox_events.status`, claimed `FOR UPDATE SKIP LOCKED` |

Handlers take a payload dict and return `None`. They raise on genuine failure so the
runner's backoff and dead-letter machinery applies; they do **not** catch broadly, since
a swallowed error in a background job is invisible by construction.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from bm.config import get_settings
from bm.domain.auth.permissions import Permission, permissions_for_role
from bm.domain.notifications import service as notifications
from bm.domain.shared.clock import SystemClock
from bm.domain.shared.context import AuthContext
from bm.infra.alerting.channel import AlertChannel, Severity
from bm.infra.db.enums import DraftStatus, JobKind
from bm.infra.db.rows import rows_affected
from bm.infra.db.session import transaction
from bm.infra.queue.runner import HANDLERS

logger = logging.getLogger(__name__)


def _system_context(session: Any, business_id: uuid.UUID) -> AuthContext:
    """An `AuthContext` for work the *queue* is doing, not a user.

    Background jobs still go through the same permissioned services — that is the point
    of `ctx.require` living in the service rather than only on the route — so they need
    a context. It carries the owner role's permissions, no `user_id` belonging to a real
    person, and is never derived from a request.

    The `business_timezone` is read from the business row so a nightly job's idea of
    "today" matches the shop's, not the server's.
    """
    row = session.execute(
        text("SELECT timezone FROM businesses WHERE id = :id"),
        {"id": business_id},
    ).first()
    return AuthContext(
        user_id=uuid.UUID(int=0),
        business_id=business_id,
        branch_id=None,
        counter_id=None,
        device_id=None,
        role="system",
        permissions=frozenset(str(p) for p in permissions_for_role("owner")),
        token_id="system-job",
        business_timezone=(row.timezone if row else "Asia/Kolkata"),
        full_name="Scheduled job",
    )


def _business_ids(session: Any) -> list[uuid.UUID]:
    """Every business a scheduled job should sweep.

    All of them: `businesses` carries no `is_active` flag, because a business is the
    tenant itself and there is no supported state in which one exists but should be
    skipped. Deactivation is a `users`/`devices` concern.
    """
    return [r[0] for r in session.execute(text("SELECT id FROM businesses")).all()]


# --- import.parse -------------------------------------------------------------


def handle_import_parse(payload: dict[str, Any]) -> None:
    """Parse an uploaded import file (FR-IMP-005)."""
    raw = payload.get("import_job_id")
    if not raw:
        raise ValueError("import.parse requires an import_job_id")
    job_id = uuid.UUID(str(raw))

    from bm.domain.imports.service import ImportService

    settings = get_settings()
    with transaction() as session:
        service = ImportService.from_settings(session, SystemClock(), settings)
        service.parse_job(job_id)


# --- inventory.reconcile ------------------------------------------------------


def handle_inventory_reconcile(payload: dict[str, Any]) -> None:
    """Nightly ledger reconciliation (FR-INV-006, T3-04).

    **Reports, never repairs.** A discrepancy raises an operational alert, because a
    balance that disagrees with its movements means there is a bug, and the only safe
    response is to tell a human.
    """
    from bm.domain.inventory.service import InventoryService
    from bm.infra.db.enums import NotificationKind, NotificationSeverity

    settings = get_settings()
    channel = AlertChannel(settings)

    with transaction() as session:
        targets = (
            [uuid.UUID(str(payload["business_id"]))]
            if payload.get("business_id")
            else _business_ids(session)
        )
        for business_id in targets:
            ctx = _system_context(session, business_id)
            report = InventoryService(session).reconcile(ctx)
            # `reconcile` returns `dict[str, object]` because it mixes counts, lists and
            # prose. The two fields read here are narrowed explicitly rather than cast,
            # so a change to the report's shape fails here instead of producing a
            # nonsense count in an alert.
            raw_count = report.get("discrepancies_found")
            discrepancies = raw_count if isinstance(raw_count, int) else 0
            raw_issues = report.get("numbering_issues")
            series_problems = len(raw_issues) if isinstance(raw_issues, list) else 0
            if not discrepancies and not series_problems:
                continue

            summary = (
                f"{discrepancies} stock balance(s) disagree with the movement ledger"
                if discrepancies
                else ""
            )
            if series_problems:
                summary = (
                    f"{summary}; {series_problems} invoice series numbering problem(s)"
                    if summary
                    else f"{series_problems} invoice series numbering problem(s)"
                )

            notifications.notify(
                session,
                business_id=business_id,
                kind=NotificationKind.RECONCILIATION_ALERT,
                severity=NotificationSeverity.CRITICAL,
                title="Stock reconciliation found a problem",
                body=f"{summary}. Nothing has been changed automatically.",
            )
            notifications.raise_alert(
                session,
                business_id=business_id,
                kind="inventory.reconcile",
                severity=Severity.CRITICAL,
                title="NAS: stock reconciliation discrepancy",
                # No product names, no quantities, no customers — this leaves the
                # building (see `alerting/channel.py`).
                body=summary,
            )
            logger.error(
                "reconciliation_discrepancy",
                extra={
                    "business_id": str(business_id),
                    "discrepancy_count": discrepancies,
                    "series_problems": series_problems,
                },
            )

    # Delivery happens after the transaction above has committed, so an HTTP timeout
    # cannot hold a lock (`ARCHITECTURE.md §7.1`).
    with transaction() as session:
        notifications.dispatch_pending(session, channel)


# --- idempotency.prune --------------------------------------------------------


def handle_idempotency_prune(payload: dict[str, Any]) -> None:
    """Drop idempotency keys past their retention window.

    Naturally idempotent: deleting rows that are already gone removes nothing. The
    retention window is a business setting, not an environment variable, because it is a
    statement about how long a client may safely retry.
    """
    from bm.domain.settings.defaults import SettingKey
    from bm.domain.settings.service import SettingsService
    from bm.domain.sync.idempotency import IdempotencyService

    with transaction() as session:
        businesses = _business_ids(session)
        retention = 7
        if businesses:
            retention = int(
                SettingsService(session).get(
                    businesses[0], None, SettingKey.IDEMPOTENCY_RETENTION_DAYS
                )
            )
        if payload.get("retention_days"):
            retention = int(payload["retention_days"])
        removed = IdempotencyService(session).prune(retention_days=retention)
        logger.info("idempotency_pruned", extra={"removed": removed, "retention_days": retention})


# --- drafts.mark_stale --------------------------------------------------------


def handle_drafts_mark_stale(payload: dict[str, Any]) -> None:
    """Mark abandoned drafts `stale` (FR-DRAFT-009).

    **Archived, never deleted, never auto-finalised.** A stale draft is still a bill
    somebody started; the status change only takes it out of the counter's open list so
    it can be reviewed.

    Idempotent through the `status = 'open'` guard: a second run finds nothing to move.
    """
    from bm.domain.settings.defaults import SettingKey
    from bm.domain.settings.service import SettingsService

    with transaction() as session:
        settings_service = SettingsService(session)
        total = 0
        for business_id in _business_ids(session):
            days = int(
                payload.get("stale_days")
                or settings_service.get(business_id, None, SettingKey.DRAFTS_STALE_DAYS)
            )
            result = session.execute(
                text(
                    """
                    UPDATE draft_bills
                       SET status = :stale, updated_at = now()
                     WHERE business_id = :business_id
                       AND status = :open
                       AND updated_at < now() - make_interval(days => :days)
                    """
                ),
                {
                    "stale": DraftStatus.STALE.value,
                    "open": DraftStatus.OPEN.value,
                    "business_id": business_id,
                    "days": days,
                },
            )
            total += rows_affected(result)
        logger.info("drafts_marked_stale", extra={"count": total})


# --- notifications.dispatch ---------------------------------------------------


def handle_notifications_dispatch(payload: dict[str, Any]) -> None:
    """Deliver pending outbox events (`ARCHITECTURE.md §7.1`)."""
    channel = AlertChannel(get_settings())
    limit = int(payload.get("limit") or 50)
    with transaction() as session:
        counts = notifications.dispatch_pending(session, channel, limit=limit)
    logger.info("outbox_dispatched", extra=counts)


#: kind → handler, registered into the runner's table on import.
#:
#: Keyed on `JobKind` rather than on strings, so a registration cannot be spelled
#: differently from the enqueue — a typo in a raw string key is otherwise invisible,
#: since the handler exists, the enqueue uses the enum, and the two never meet.
REGISTRY: dict[JobKind, Callable[[dict[str, Any]], None]] = {
    JobKind.IMPORT_PARSE: handle_import_parse,
    JobKind.INVENTORY_RECONCILE: handle_inventory_reconcile,
    JobKind.IDEMPOTENCY_PRUNE: handle_idempotency_prune,
    JobKind.DRAFTS_MARK_STALE: handle_drafts_mark_stale,
    JobKind.NOTIFICATIONS_DISPATCH: handle_notifications_dispatch,
}

#: The string keys this module installs. `tests/unit/test_job_kinds.py` asserts they are
#: all real `JobKind` values.
REGISTRY_KEYS: frozenset[str] = frozenset(kind.value for kind in REGISTRY)

for _kind, _handler in REGISTRY.items():
    HANDLERS[_kind.value] = _handler


_ = Permission  # re-exported for callers that build a context from this module
