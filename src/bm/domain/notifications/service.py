"""Notifications and the transactional outbox dispatcher (`ARCHITECTURE.md §7.1`).

Two audiences, one mechanism:

* **In-app notifications** — a `notifications` row the owner sees next time they look.
  Written inside the business transaction, so a notification cannot exist for a sale
  that rolled back.
* **Operational alerts** — delivered off-box to whoever OD-19 names. Written as an
  `outbox_events` row in the same transaction and dispatched *after* commit, because
  rule 3 of `ARCHITECTURE.md §5.1` forbids a side effect inside a write transaction and
  an HTTP call inside one would hold locks for the length of a network timeout.

The dispatcher is deliberately dull: claim a pending event, try to deliver, record the
outcome. It is idempotent on `outbox_event_id` — a redelivered event that was already
dispatched is a no-op — because at-least-once is what the queue guarantees.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert, text, update
from sqlalchemy.orm import Session

from bm.domain.shared.context import AuthContext
from bm.infra.alerting.channel import Alert, AlertChannel, Severity
from bm.infra.db.enums import NotificationKind, NotificationSeverity, OutboxStatus
from bm.infra.db.models.infra import Notification, OutboxEvent
from bm.infra.db.rows import rows_affected

#: Event type for an operational alert on the outbox. Other event types (future
#: webhooks, e-invoice pushes) share the table and are routed by this column.
ALERT_EVENT = "operational.alert"

#: Deliveries to attempt before an event is left alone.
#:
#: Lower than the queue's own `max_attempts` because a webhook that has refused five
#: times is not going to accept the sixth, and a retry storm against a pager provider is
#: its own incident.
MAX_DELIVERY_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class NotificationView:
    id: uuid.UUID
    kind: str
    severity: str
    title: str
    body: str | None
    created_at: str
    status: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    read_at: str | None


def notify(
    session: Session,
    *,
    business_id: uuid.UUID,
    kind: NotificationKind,
    severity: NotificationSeverity,
    title: str,
    body: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Record an in-app notification. Part of the caller's transaction.

    Always inserts — use `notify_once` where the condition can recur unchanged (a
    low-stock item still low, a bill still outstanding) and a second notification for
    the same still-open one would just be noise.
    """
    notification_id = uuid.uuid4()
    session.execute(
        insert(Notification).values(
            id=notification_id,
            business_id=business_id,
            kind=kind.value,
            severity=severity.value,
            title=title[:200],
            body=body,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    )
    return notification_id


def notify_once(
    session: Session,
    *,
    business_id: uuid.UUID,
    kind: NotificationKind,
    severity: NotificationSeverity,
    title: str,
    body: str | None,
    entity_type: str,
    entity_id: uuid.UUID,
) -> uuid.UUID | None:
    """Raise an alert for one ongoing condition, without duplicating an open one.

    Relies on migration `0025`'s partial unique index — `(business_id, kind, entity_id)
    WHERE status = 'active'` — so "is there already an open alert for this exact thing"
    is a database guarantee, not a check-then-insert race the caller has to get right.
    Returns the new id, or `None` when one was already open (a no-op, not an error).

    Once dismissed/acknowledged/resolved, the slot is free: a *recurrence* of the same
    condition (stock dips low again after being restocked) raises a fresh notification,
    which is the intended behaviour, not a bug in the dedup.
    """
    notification_id = uuid.uuid4()
    result = session.execute(
        text(
            """
            INSERT INTO notifications
                (id, business_id, kind, severity, title, body, entity_type, entity_id)
            VALUES (:id, :business_id, :kind, :severity, :title, :body, :entity_type, :entity_id)
            ON CONFLICT (business_id, kind, entity_id)
            WHERE status = 'active' AND entity_id IS NOT NULL
            DO NOTHING
            """
        ),
        {
            "id": notification_id,
            "business_id": business_id,
            "kind": kind.value,
            "severity": severity.value,
            "title": title[:200],
            "body": body,
            "entity_type": entity_type,
            "entity_id": entity_id,
        },
    )
    return notification_id if rows_affected(result) else None


def resolve_for_entity(
    session: Session, *, business_id: uuid.UUID, kind: NotificationKind, entity_id: uuid.UUID
) -> None:
    """Close the open alert for one condition because the condition itself was fixed.

    Called only by the application code that actually performed the fix — receiving
    stock past the reorder level, marking an order completed — never by a user action
    on the bell. That is the distinction the lifecycle exists to keep: a dismiss must
    never look like a resolve.
    """
    session.execute(
        update(Notification)
        .where(
            Notification.business_id == business_id,
            Notification.kind == kind.value,
            Notification.entity_id == entity_id,
            Notification.status == "active",
        )
        .values(status="resolved", status_changed_at=text("now()"))
    )


def raise_alert(
    session: Session,
    *,
    business_id: uuid.UUID,
    kind: str,
    severity: Severity,
    title: str,
    body: str,
) -> uuid.UUID:
    """Queue an operational alert for delivery after commit.

    Returns the outbox event id. Nothing is sent here — see the module docstring.
    """
    event_id = uuid.uuid4()
    session.execute(
        insert(OutboxEvent).values(
            id=event_id,
            business_id=business_id,
            event_type=ALERT_EVENT,
            aggregate_type="alert",
            aggregate_id=event_id,
            payload={
                "kind": kind,
                "severity": severity.value,
                "title": title,
                "body": body,
            },
            status=OutboxStatus.PENDING.value,
        )
    )
    return event_id


def active_for(ctx: AuthContext, session: Session, *, limit: int = 50) -> list[NotificationView]:
    """The notifications the bell shows: `active`, plus any `snoozed` one whose snooze
    has expired (it reappears on its own — a snooze that silently lapsed forever would
    be a dismiss wearing a different name).

    No permission beyond being signed in: these are notices about the caller's own
    business, and gating "stock has run out" behind a reports permission would mean the
    person at the counter is the one person who cannot be told.
    """
    rows = session.execute(
        text(
            """
            SELECT id, kind, severity, title, body, created_at, status, entity_type,
                   entity_id, read_at
              FROM notifications
             WHERE business_id = :business_id
               AND (status = 'active' OR (status = 'snoozed' AND snoozed_until <= now()))
             ORDER BY
               CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
               created_at DESC
             LIMIT :limit
            """
        ),
        {"business_id": ctx.business_id, "limit": limit},
    ).all()
    return [
        NotificationView(
            id=r.id,
            kind=str(r.kind),
            severity=str(r.severity),
            title=r.title,
            body=r.body,
            created_at=r.created_at.isoformat(),
            status=str(r.status),
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            read_at=r.read_at.isoformat() if r.read_at else None,
        )
        for r in rows
    ]


#: Backward-compatible alias — `GET /notifications` originally read unread notices
#: only; it now reads the active set, which `read_at` is independent of.
unread_for = active_for


def mark_read(ctx: AuthContext, session: Session, notification_id: uuid.UUID) -> bool:
    return bool(
        rows_affected(
            session.execute(
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.business_id == ctx.business_id,
                    Notification.read_at.is_(None),
                )
                .values(read_at=text("now()"))
            )
        )
    )


def _transition(
    ctx: AuthContext,
    session: Session,
    notification_id: uuid.UUID,
    *,
    to_status: str,
    extra: dict[str, object] | None = None,
) -> bool:
    """Shared machinery for the three user-facing lifecycle actions below.

    Only ever moves a notification **out of** `active`/`snoozed` — never resolves one,
    which only `resolve_for_entity` may do. That restriction is what keeps a dismiss
    from ever reading as "the underlying problem went away".
    """
    values: dict[str, object] = {"status": to_status, "status_changed_at": text("now()")}
    if extra:
        values.update(extra)
    return bool(
        rows_affected(
            session.execute(
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.business_id == ctx.business_id,
                    Notification.status.in_(("active", "snoozed")),
                )
                .values(**values)
            )
        )
    )


def dismiss(ctx: AuthContext, session: Session, notification_id: uuid.UUID) -> bool:
    """ "Check later" — stop showing it, change nothing it refers to.

    Dismissing a low-stock alert must never increase stock; dismissing a bill reminder
    must never touch the bill. Nothing here does either — it only ever flips `status`.
    """
    return _transition(ctx, session, notification_id, to_status="dismissed")


def acknowledge(ctx: AuthContext, session: Session, notification_id: uuid.UUID) -> bool:
    """ "I've seen this and I'm on it" — for notifications with no server-verifiable
    resolution of their own (a custom reminder, an informational notice). Distinct from
    `dismiss` only in the word a screen reader and an audit log would use for it; the
    effect on the underlying record is identical: none."""
    return _transition(ctx, session, notification_id, to_status="acknowledged")


def snooze(
    ctx: AuthContext, session: Session, notification_id: uuid.UUID, *, until: datetime
) -> bool:
    """Remind me again later, rather than never."""
    return _transition(
        ctx, session, notification_id, to_status="snoozed", extra={"snoozed_until": until}
    )


def dispatch_pending(session: Session, channel: AlertChannel, *, limit: int = 50) -> dict[str, int]:
    """Deliver pending outbox events. Returns counts, for the worker's log.

    `FOR UPDATE SKIP LOCKED` so two workers never deliver the same alert twice, matching
    how the job queue itself claims work (ADR-004).
    """
    claimed = session.execute(
        text(
            """
            SELECT id, event_type, payload, attempts
              FROM outbox_events
             WHERE status = 'pending'
             ORDER BY created_at
             FOR UPDATE SKIP LOCKED
             LIMIT :limit
            """
        ),
        {"limit": limit},
    ).all()

    sent = failed = skipped = 0
    for row in claimed:
        if str(row.event_type) != ALERT_EVENT:
            # Not ours. Left pending for whichever consumer owns it rather than marked
            # dispatched — silently consuming another handler's event is how a future
            # e-invoice push would vanish.
            skipped += 1
            continue

        payload = dict(row.payload or {})
        delivered = channel.send(
            Alert(
                title=str(payload.get("title", "Alert")),
                body=str(payload.get("body", "")),
                severity=Severity(str(payload.get("severity", "warning"))),
                kind=str(payload.get("kind", "unknown")),
            )
        )
        attempts = int(row.attempts or 0) + 1

        if delivered:
            session.execute(
                text(
                    "UPDATE outbox_events SET status = 'dispatched', attempts = :attempts,"
                    " dispatched_at = now() WHERE id = :id"
                ),
                {"id": row.id, "attempts": attempts},
            )
            sent += 1
        elif attempts >= MAX_DELIVERY_ATTEMPTS:
            # `failed` is a visible dead-letter state, not a deletion. An operator can
            # see exactly which alerts never reached anyone. `outbox_events` carries no
            # `last_error` column — the reason is already in the structured log from
            # `AlertChannel.send`, and widening the table to duplicate it would mean a
            # migration for something already recorded.
            session.execute(
                text(
                    "UPDATE outbox_events SET status = 'failed', attempts = :attempts"
                    " WHERE id = :id"
                ),
                {"id": row.id, "attempts": attempts},
            )
            failed += 1
        else:
            session.execute(
                text("UPDATE outbox_events SET attempts = :attempts WHERE id = :id"),
                {"id": row.id, "attempts": attempts},
            )
            failed += 1

    return {"sent": sent, "failed": failed, "skipped": skipped}
