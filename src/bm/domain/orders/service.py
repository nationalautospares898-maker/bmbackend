"""Chitti orders — a phoned-in order written down now, billed later (Feature B).

Deliberately not an invoice, a draft bill, or anything the billing domain already owns.
ADR-015's lineage treats a correction as cancellation, return, credit note or reversal;
an order is a different thing again — it is not a sale that happened and needs undoing,
it is a sale that **has not happened yet**. So this is its own small table with its own
three-state lifecycle (`pending` / `completed` / `discarded`) and no foreign key to
`invoices` in either direction. Turning one into a bill is the Billing screen reading an
order's `body`/`customer_name`/`phone` to pre-fill a new, ordinary bill — a read, not a
conversion — so nothing here can create stock movements or financial records by itself.

No specific permission beyond being signed in, matching `domain/notifications/service.py`
precedent: this is the owner's own scratchpad for calls taken at the counter, not a
report or a financial action, and a counter hand who may bill is exactly who answers
the phone.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from bm.domain.notifications import service as notifications
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import ValidationFailed
from bm.infra.db.enums import NotificationKind, NotificationSeverity
from bm.infra.db.models.infra import Order
from bm.infra.db.rows import rows_affected

#: How often a pending order resurfaces if left unattended (owner request).
NUDGE_INTERVAL_MINUTES = 10


@dataclass(frozen=True, slots=True)
class OrderView:
    id: uuid.UUID
    customer_name: str | None
    phone: str | None
    body: str
    status: str
    created_at: str
    updated_at: str
    completed_at: str | None
    discarded_at: str | None
    remind_at: str | None


def _row_to_view(r: object) -> OrderView:
    return OrderView(
        id=r.id,  # type: ignore[attr-defined]
        customer_name=r.customer_name,  # type: ignore[attr-defined]
        phone=r.phone,  # type: ignore[attr-defined]
        body=r.body,  # type: ignore[attr-defined]
        status=str(r.status),  # type: ignore[attr-defined]
        created_at=r.created_at.isoformat(),  # type: ignore[attr-defined]
        updated_at=r.updated_at.isoformat(),  # type: ignore[attr-defined]
        completed_at=r.completed_at.isoformat() if r.completed_at else None,  # type: ignore[attr-defined]
        discarded_at=r.discarded_at.isoformat() if r.discarded_at else None,  # type: ignore[attr-defined]
        remind_at=r.remind_at.isoformat() if r.remind_at else None,  # type: ignore[attr-defined]
    )


def list_orders(
    ctx: AuthContext, session: Session, *, status: str | None = None, limit: int = 100
) -> list[OrderView]:
    """Newest first, except an order carrying a reminder (owner request) jumps to the
    front — soonest/most-overdue first — so the thing that needs attention is the thing
    the owner sees without scrolling. An order with no reminder keeps the original,
    simple newest-first order among itself."""
    sql = (
        "SELECT id, customer_name, phone, body, status, created_at, updated_at,"
        " completed_at, discarded_at, remind_at FROM orders WHERE business_id = :business_id"
    )
    params: dict[str, object] = {"business_id": ctx.business_id, "limit": min(limit, 200)}
    if status is not None:
        sql += " AND status = :status"
        params["status"] = status
    sql += " ORDER BY (remind_at IS NULL) ASC, remind_at ASC, created_at DESC LIMIT :limit"
    rows = session.execute(text(sql), params).all()
    return [_row_to_view(r) for r in rows]


def set_reminder(
    ctx: AuthContext, session: Session, order_id: uuid.UUID, *, remind_at: datetime | None
) -> bool:
    """Set or clear the owner's one-time reminder for a single order.

    Pending only — matching `update_order`'s own rule: an order that is already decided
    (completed/discarded) has nothing left to be reminded about, and its reminder (if
    any) was already resolved by `complete_order`/`discard_order`.
    """
    result = session.execute(
        update(Order)
        .where(
            Order.id == order_id,
            Order.business_id == ctx.business_id,
            Order.status == "pending",
        )
        .values(remind_at=remind_at, updated_at=text("now()"))
    )
    return bool(rows_affected(result))


def create_order(
    ctx: AuthContext,
    session: Session,
    *,
    body: str,
    customer_name: str | None,
    phone: str | None,
) -> uuid.UUID:
    """Save an order. No field beyond the note itself is required (owner request: "do
    not force the owner to fill unnecessary structured fields before saving")."""
    if not body.strip():
        raise ValidationFailed({"body": "Write something down before saving."})
    order_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO orders
                (id, business_id, branch_id, created_by_user_id, customer_name, phone, body)
            VALUES (:id, :business_id, :branch_id, :user_id, :customer_name, :phone, :body)
            """
        ),
        {
            "id": order_id,
            "business_id": ctx.business_id,
            "branch_id": ctx.requires_branch,
            "user_id": ctx.user_id,
            "customer_name": (customer_name or "").strip() or None,
            "phone": (phone or "").strip() or None,
            "body": body.strip(),
        },
    )
    return order_id


def update_order(
    ctx: AuthContext,
    session: Session,
    order_id: uuid.UUID,
    *,
    body: str | None = None,
    customer_name: str | None = None,
    phone: str | None = None,
) -> bool:
    """Continue editing a pending order. Refused once it is completed or discarded —
    the owner's record of what was decided must not change after the fact."""
    values: dict[str, object] = {"updated_at": text("now()")}
    if body is not None:
        if not body.strip():
            raise ValidationFailed({"body": "Write something down before saving."})
        values["body"] = body.strip()
    if customer_name is not None:
        values["customer_name"] = customer_name.strip() or None
    if phone is not None:
        values["phone"] = phone.strip() or None
    result = session.execute(
        update(Order)
        .where(
            Order.id == order_id,
            Order.business_id == ctx.business_id,
            Order.status == "pending",
        )
        .values(**values)
    )
    return bool(rows_affected(result))


def complete_order(ctx: AuthContext, session: Session, order_id: uuid.UUID) -> bool:
    """Mark done — stops future reminders. Does not touch billing or stock; converting
    an order into a bill, if the owner does that, is the Billing screen's own action."""
    result = session.execute(
        text(
            """
            UPDATE orders SET status = 'completed', completed_at = now(), updated_at = now()
             WHERE id = :id AND business_id = :business_id AND status = 'pending'
            """
        ),
        {"id": order_id, "business_id": ctx.business_id},
    )
    if not rows_affected(result):
        return False
    notifications.resolve_for_entity(
        session,
        business_id=ctx.business_id,
        kind=NotificationKind.ORDER_REMINDER,
        entity_id=order_id,
    )
    return True


def discard_order(ctx: AuthContext, session: Session, order_id: uuid.UUID) -> bool:
    """Trash — removed from the active list and its reminders stop. The row itself is
    kept (status transition, not a delete) for the same reason every other lifecycle in
    this codebase prefers a status to a `DELETE`: an owner who discarded the wrong one
    can still be told what it was."""
    result = session.execute(
        text(
            """
            UPDATE orders SET status = 'discarded', discarded_at = now(), updated_at = now()
             WHERE id = :id AND business_id = :business_id AND status = 'pending'
            """
        ),
        {"id": order_id, "business_id": ctx.business_id},
    )
    if not rows_affected(result):
        return False
    notifications.resolve_for_entity(
        session,
        business_id=ctx.business_id,
        kind=NotificationKind.ORDER_REMINDER,
        entity_id=order_id,
    )
    return True


# --- the ten-minute nudge -------------------------------------------------------


def nudge_pending(session: Session, business_id: uuid.UUID, now: datetime) -> int:
    """Resurface every pending order due a nudge — either the flat ten-minute cadence,
    or the owner's own one-time `remind_at` for that specific order.

    A `remind_at` in the past or present is due **ahead of** the flat cadence, even if
    the order was nudged moments ago — the owner asked for that specific moment, and
    making it wait out the rest of a ten-minute window would defeat the point of
    scheduling it. A `remind_at` still in the **future** suppresses the flat cadence
    for that order instead of letting both fire independently, which is what a
    "remind me about this one tomorrow at 3pm" request means: quiet until then, not
    quiet *in addition to* the usual nudging.

    One open notification per order at a time (the `uq_notifications__active_per_entity`
    index), refreshed rather than duplicated: a still-open reminder is left exactly as
    it is — re-touching it would clear a read/dismissed state the owner may have set a
    moment before this runs — and only an order with **no** open reminder (new, or its
    last one was dismissed) gets a fresh one.
    """
    cutoff = now - timedelta(minutes=NUDGE_INTERVAL_MINUTES)
    due = session.execute(
        text(
            """
            SELECT o.id, o.customer_name, o.body, o.remind_at
              FROM orders o
             WHERE o.business_id = :business_id
               AND o.status = 'pending'
               AND NOT EXISTS (
                     SELECT 1 FROM notifications n
                      WHERE n.business_id = o.business_id
                        AND n.kind = 'order_reminder'
                        AND n.entity_id = o.id
                        AND n.status = 'active'
                   )
               AND (
                     (o.remind_at IS NOT NULL AND o.remind_at <= :now)
                     OR (
                           o.remind_at IS NULL
                           AND (o.last_reminder_at IS NULL OR o.last_reminder_at <= :cutoff)
                         )
                   )
            """
        ),
        {"business_id": business_id, "cutoff": cutoff, "now": now},
    ).all()

    nudged = 0
    for row in due:
        label = (row.customer_name or row.body[:40]).strip()
        notifications.notify_once(
            session,
            business_id=business_id,
            kind=NotificationKind.ORDER_REMINDER,
            severity=NotificationSeverity.INFO,
            title=f"Pending order: {label}",
            body=row.body[:200],
            entity_type="order",
            entity_id=row.id,
        )
        session.execute(
            text(
                """
                UPDATE orders
                   SET last_reminder_at = :now,
                       -- A one-time request is consumed the instant it fires; the flat
                       -- cadence takes back over from this same moment (via the fresh
                       -- `last_reminder_at` above), never a second firing of the same
                       -- `remind_at`.
                       remind_at = CASE
                         WHEN remind_at IS NOT NULL AND remind_at <= :now THEN NULL
                         ELSE remind_at
                       END
                 WHERE id = :id
                """
            ),
            {"now": now, "id": row.id},
        )
        nudged += 1
    return nudged
