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
    """Record an in-app notification. Part of the caller's transaction."""
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


def unread_for(ctx: AuthContext, session: Session, *, limit: int = 50) -> list[NotificationView]:
    """The owner's unread notifications. No permission beyond being signed in: these are
    notices about the caller's own business, and gating them behind a reports permission
    would mean a counter hand cannot be told stock ran out."""
    rows = session.execute(
        text(
            """
            SELECT id, kind, severity, title, body, created_at
              FROM notifications
             WHERE business_id = :business_id
               AND read_at IS NULL
             ORDER BY created_at DESC
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
        )
        for r in rows
    ]


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
