"""In-app notifications and the alerting self-report (M26, OD-19).

`GET /notifications/alerting` exists so the OD-19 gap is **visible in the product**, not
only in a readiness probe an operator has to know to curl. If alerting is switched on
with no destination configured, this endpoint says so, and the owner's screen can say so
too. An unmonitored system that looks monitored is the failure OD-19 names.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from bm.api.deps import (
    CurrentContext,
    ReadSession,
    SettingsDep,
    WriteSession,
    authenticated_only,
    require,
)
from bm.api.v1.schemas.responses import AlertingStatusOut, LifecycleStatusOut, NotificationListOut
from bm.domain.auth.permissions import Permission
from bm.domain.notifications import service as notifications
from bm.domain.shared.errors import NotFound
from bm.infra.alerting.channel import AlertChannel

router = APIRouter(tags=["notifications"])


@router.get("/notifications", responses={200: {"model": NotificationListOut}})
def list_notifications(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """The caller's business's active notices — Feature A.1/A.2.

    No specific permission: these are notices about the business the caller is already
    signed in to, and gating "stock has run out" behind a reports permission would mean
    the person at the counter is the one person who cannot be told.
    """
    items = notifications.active_for(ctx, session, limit=limit)
    return {
        "notifications": [
            {
                "id": str(n.id),
                "kind": n.kind,
                "severity": n.severity,
                "title": n.title,
                "body": n.body,
                "created_at": n.created_at,
                "status": n.status,
                "entity_type": n.entity_type,
                "entity_id": str(n.entity_id) if n.entity_id else None,
                "read_at": n.read_at,
            }
            for n in items
        ]
    }


@router.post(
    "/notifications/{notification_id}:read", responses={200: {"model": LifecycleStatusOut}}
)
def mark_notification_read(
    notification_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
) -> dict[str, Any]:
    if not notifications.mark_read(ctx, session, notification_id):
        raise NotFound("That notification does not exist, or was already read.")
    return {"read": True}


@router.post(
    "/notifications/{notification_id}:dismiss", responses={200: {"model": LifecycleStatusOut}}
)
def dismiss_notification(
    notification_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
) -> dict[str, Any]:
    """ "Check later." Stops the reminder; changes nothing it refers to (owner
    request, 2026-11-10 — dismissing a low-stock notice must not raise stock)."""
    if not notifications.dismiss(ctx, session, notification_id):
        raise NotFound("That notification does not exist, or is no longer open.")
    return {"status": "dismissed"}


@router.post(
    "/notifications/{notification_id}:acknowledge",
    responses={200: {"model": LifecycleStatusOut}},
)
def acknowledge_notification(
    notification_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
) -> dict[str, Any]:
    """ "Seen, and I'm on it." For notifications with no server-verifiable resolution
    of their own. Acknowledging a bill reminder must not mark the bill paid."""
    if not notifications.acknowledge(ctx, session, notification_id):
        raise NotFound("That notification does not exist, or is no longer open.")
    return {"status": "acknowledged"}


class SnoozeIn(BaseModel):
    #: Minutes from now. Bounded well short of a day, so "snooze" cannot become a
    #: second, quieter way to dismiss something permanently.
    minutes: int = Field(ge=5, le=480)


@router.post(
    "/notifications/{notification_id}:snooze", responses={200: {"model": LifecycleStatusOut}}
)
def snooze_notification(
    notification_id: uuid.UUID,
    body: SnoozeIn,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
) -> dict[str, Any]:
    until = datetime.now(UTC) + timedelta(minutes=body.minutes)
    if not notifications.snooze(ctx, session, notification_id, until=until):
        raise NotFound("That notification does not exist, or is no longer open.")
    return {"status": "snoozed", "snoozed_until": until.isoformat()}


@router.get("/notifications/alerting", responses={200: {"model": AlertingStatusOut}})
def alerting_status(
    settings: SettingsDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.AUDIT_READ))],
) -> dict[str, Any]:
    """Whether an operational alert would actually reach anybody (OD-19).

    Behind `audit.read` rather than open to any signed-in user: it reports on the
    deployment's monitoring posture, which is an operator's concern, and the destination
    hint — even reduced to scheme and host — is infrastructure detail a counter hand has
    no reason to see.
    """
    _ = ctx
    status = AlertChannel(settings).describe()
    return {
        "state": status.state.value,
        "recipient_name": status.recipient_name,
        "destination_hint": status.destination_hint,
        "min_severity": status.min_severity,
    }
