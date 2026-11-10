"""Scheduled reminders the owner can see and change (Feature A.5).

Two built-in reminders exist per business — the 8:45 bill reminder and the 5:00 stock
reminder the owner asked for by name — plus whatever custom ones the owner adds. All
three kinds are rows in the same `reminder_schedules` table and go through the same
firing logic in `check_and_fire`; "built-in" only means `ensure_defaults` seeds them
once per business, not that they are special afterwards. The owner can disable or
delete either one exactly like a custom reminder.

## Why firing uses `notify`, not `notify_once`

`notify_once` (in `domain/notifications/service.py`) exists for conditions that are
still true the next time a scan looks — a product still below its reorder level. A
scheduled reminder is a different shape: "remind me of today's pending bills" is true
again *every day it fires*, deliberately, even if yesterday's reminder was dismissed
with nothing having changed. Using `notify_once` would mean the reminder could only ever
fire once, ever. Firing instead supersedes (resolves) whatever notification the previous
firing left open — so the bell shows one current reminder per schedule, not an
accumulating pile of stale ones.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from bm.domain.notifications import service as notifications
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import NotFound, ValidationFailed
from bm.infra.db.enums import NotificationKind, NotificationSeverity
from bm.infra.db.models.infra import ReminderSchedule
from bm.infra.db.rows import rows_affected

PERMISSION = "admin.settings.manage"

#: The two reminders the owner named explicitly. Seeded once per business by
#: `ensure_defaults`; afterwards indistinguishable from a custom one except by label.
_BUILTIN_DEFAULTS: tuple[tuple[str, str, time], ...] = (
    ("bill_reminder", "Pending bills (8:45 AM)", time(8, 45)),
    ("stock_reminder", "Low stock (5:00 PM)", time(17, 0)),
)


@dataclass(frozen=True, slots=True)
class ScheduleView:
    id: uuid.UUID
    kind: str
    label: str
    fire_time: str | None
    interval_hours: int | None
    enabled: bool
    last_fired_at: str | None


def _row_to_view(r: object) -> ScheduleView:
    return ScheduleView(
        id=r.id,  # type: ignore[attr-defined]
        kind=str(r.kind),  # type: ignore[attr-defined]
        label=r.label,  # type: ignore[attr-defined]
        fire_time=r.fire_time.isoformat() if r.fire_time else None,  # type: ignore[attr-defined]
        interval_hours=r.interval_hours,  # type: ignore[attr-defined]
        enabled=r.enabled,  # type: ignore[attr-defined]
        last_fired_at=r.last_fired_at.isoformat() if r.last_fired_at else None,  # type: ignore[attr-defined]
    )


def ensure_defaults(session: Session, business_id: uuid.UUID) -> None:
    """Seed the two built-in reminders for a business, once.

    Idempotent via a check against `kind`: a business that already has a
    `bill_reminder`/`stock_reminder` row (including one the owner has since edited or
    disabled) is left alone — this never re-creates one the owner deleted.
    """
    existing = {
        str(r[0])
        for r in session.execute(
            text(
                "SELECT kind FROM reminder_schedules"
                " WHERE business_id = :business_id AND kind IN ('bill_reminder', 'stock_reminder')"
            ),
            {"business_id": business_id},
        ).all()
    }
    for kind, label, fire_time in _BUILTIN_DEFAULTS:
        if kind in existing:
            continue
        session.execute(
            text(
                """
                INSERT INTO reminder_schedules (id, business_id, kind, label, fire_time)
                VALUES (:id, :business_id, :kind, :label, :fire_time)
                """
            ),
            {
                "id": uuid.uuid4(),
                "business_id": business_id,
                "kind": kind,
                "label": label,
                "fire_time": fire_time,
            },
        )


def list_schedules(ctx: AuthContext, session: Session) -> list[ScheduleView]:
    ctx.require(PERMISSION)
    rows = session.execute(
        text(
            "SELECT id, kind, label, fire_time, interval_hours, enabled, last_fired_at"
            " FROM reminder_schedules WHERE business_id = :business_id ORDER BY created_at"
        ),
        {"business_id": ctx.business_id},
    ).all()
    return [_row_to_view(r) for r in rows]


def create_schedule(
    ctx: AuthContext,
    session: Session,
    *,
    label: str,
    fire_time: time | None,
    interval_hours: int | None,
) -> uuid.UUID:
    """A custom reminder (owner request: "create, edit, disable, and remove additional
    reminders"). Always `kind='custom'` — the two built-ins come only from
    `ensure_defaults`, so an owner cannot accidentally create a second bill reminder
    that the firing logic cannot tell apart from the first."""
    ctx.require(PERMISSION)
    if not label.strip():
        raise ValidationFailed({"label": "Give the reminder a name."})
    if (fire_time is None) == (interval_hours is None):
        raise ValidationFailed(
            {"fire_time": "Set exactly one of a time of day or a repeat interval."}
        )
    if interval_hours is not None and not (3 <= interval_hours <= 5):
        raise ValidationFailed({"interval_hours": "Must be between 3 and 5 hours."})

    schedule_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO reminder_schedules
                (id, business_id, kind, label, fire_time, interval_hours, created_by_user_id)
            VALUES (:id, :business_id, 'custom', :label, :fire_time, :interval_hours, :user_id)
            """
        ),
        {
            "id": schedule_id,
            "business_id": ctx.business_id,
            "label": label.strip(),
            "fire_time": fire_time,
            "interval_hours": interval_hours,
            "user_id": ctx.user_id,
        },
    )
    return schedule_id


def update_schedule(
    ctx: AuthContext,
    session: Session,
    schedule_id: uuid.UUID,
    *,
    label: str | None = None,
    enabled: bool | None = None,
) -> bool:
    """Edit a label, or enable/disable — the two things every reminder (built-in or
    custom) supports changing. Changing *when* a built-in fires is not exposed: the
    owner asked for 8:45 and 5:00 by name, and a schedule that silently drifted off
    those times would stop being the reminder it was named for."""
    ctx.require(PERMISSION)
    values: dict[str, object] = {}
    if label is not None:
        if not label.strip():
            raise ValidationFailed({"label": "Give the reminder a name."})
        values["label"] = label.strip()
    if enabled is not None:
        values["enabled"] = enabled
    if not values:
        return True
    values["updated_at"] = text("now()")
    result = session.execute(
        update(ReminderSchedule)
        .where(
            ReminderSchedule.id == schedule_id,
            ReminderSchedule.business_id == ctx.business_id,
        )
        .values(**values)
    )
    return bool(rows_affected(result))


def delete_schedule(ctx: AuthContext, session: Session, schedule_id: uuid.UUID) -> None:
    ctx.require(PERMISSION)
    result = session.execute(
        text("DELETE FROM reminder_schedules WHERE id = :id AND business_id = :business_id"),
        {"id": schedule_id, "business_id": ctx.business_id},
    )
    if not rows_affected(result):
        raise NotFound("That reminder does not exist.")


# --- firing --------------------------------------------------------------------


def _business_today(business_timezone: str, now_utc: datetime) -> date:
    return now_utc.astimezone(ZoneInfo(business_timezone)).date()


def _is_due(
    *,
    fire_time: time | None,
    interval_hours: int | None,
    last_fired_at: datetime | None,
    business_timezone: str,
    now_utc: datetime,
) -> bool:
    if interval_hours is not None:
        if last_fired_at is None:
            return True
        return now_utc - last_fired_at >= timedelta(hours=interval_hours)

    # `fire_time`-based: due once the business-local clock has passed it today, and
    # not yet fired today in business-local terms.
    assert fire_time is not None
    local_now = now_utc.astimezone(ZoneInfo(business_timezone))
    if local_now.time() < fire_time:
        return False
    if last_fired_at is None:
        return True
    return _business_today(business_timezone, last_fired_at) < local_now.date()


def _fire_one(session: Session, business_id: uuid.UUID, row: object) -> bool:
    """Returns whether a notification was actually raised.

    "Nothing to report yet" (no outstanding bill, nothing low) must **not** consume the
    day's firing slot — `check_and_fire` only advances `last_fired_at` when this
    returns `True`, so a bill that appears later the same day still gets its reminder
    on the next tick rather than being silently skipped until tomorrow.
    """
    schedule_id = row.id  # type: ignore[attr-defined]
    kind = str(row.kind)  # type: ignore[attr-defined]
    label = row.label  # type: ignore[attr-defined]

    if kind == "bill_reminder":
        outstanding = session.execute(
            text(
                """
                SELECT count(*), coalesce(sum(total - amount_paid), 0)
                  FROM invoices
                 WHERE business_id = :business_id
                   AND is_credit = true
                   AND status != 'cancelled'
                   AND payment_status IN ('unpaid', 'partially_paid')
                """
            ),
            {"business_id": business_id},
        ).one()
        count, total = outstanding
        if not count:
            return False
        title = f"{count} credit bill(s) still pending"
        body = f"₹{total} outstanding across {count} bill(s)."
        severity = NotificationSeverity.WARNING
    elif kind == "stock_reminder":
        from bm.domain.notifications.low_stock import scan_business

        scan_business(session, business_id)
        low_count = session.execute(
            text(
                "SELECT count(*) FROM notifications"
                " WHERE business_id = :business_id AND kind = 'low_stock' AND status = 'active'"
            ),
            {"business_id": business_id},
        ).scalar_one()
        if not low_count:
            return False
        title = f"{low_count} item(s) running low"
        body = "See Stock for the full list."
        severity = NotificationSeverity.WARNING
    else:
        title = label
        body = None
        severity = NotificationSeverity.INFO

    # `reminder_schedules.kind` and `NotificationKind` are not the same vocabulary —
    # `'custom'` (a schedule kind) is `CUSTOM_REMINDER` (a notification kind); the
    # built-in two happen to share their spelling, which is a coincidence, not a rule.
    notification_kind = (
        NotificationKind.CUSTOM_REMINDER if kind == "custom" else NotificationKind(kind)
    )

    # Supersede whatever this schedule's previous firing left open, so the bell holds
    # one current reminder per schedule rather than an accumulating pile.
    notifications.resolve_for_entity(
        session,
        business_id=business_id,
        kind=notification_kind,
        entity_id=schedule_id,
    )
    notifications.notify(
        session,
        business_id=business_id,
        kind=notification_kind,
        severity=severity,
        title=title,
        body=body,
        entity_type="reminder_schedule",
        entity_id=schedule_id,
    )
    return True


def check_and_fire(
    session: Session, business_id: uuid.UUID, business_timezone: str, now_utc: datetime
) -> int:
    """Fire every enabled, due schedule for one business. Returns how many fired."""
    rows = session.execute(
        text(
            "SELECT id, kind, label, fire_time, interval_hours, last_fired_at"
            " FROM reminder_schedules WHERE business_id = :business_id AND enabled = true"
        ),
        {"business_id": business_id},
    ).all()

    fired = 0
    for row in rows:
        if not _is_due(
            fire_time=row.fire_time,
            interval_hours=row.interval_hours,
            last_fired_at=row.last_fired_at,
            business_timezone=business_timezone,
            now_utc=now_utc,
        ):
            continue
        if not _fire_one(session, business_id, row):
            continue
        session.execute(
            text("UPDATE reminder_schedules SET last_fired_at = :now WHERE id = :id"),
            {"now": now_utc, "id": row.id},
        )
        fired += 1
    return fired
