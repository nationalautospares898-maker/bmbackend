"""Scheduled reminders the owner can see and change (Feature A.5)."""

from __future__ import annotations

import uuid
from datetime import time as dt_time
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from bm.api.deps import CurrentContext, ReadSession, WriteSession, require
from bm.api.v1.schemas.responses import CreatedIdOut, ReminderListOut, UpdatedOut
from bm.domain.auth.permissions import Permission
from bm.domain.reminders import service as reminders

router = APIRouter(prefix="/reminders", tags=["reminders"])


def _out(r: reminders.ScheduleView) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "kind": r.kind,
        "label": r.label,
        "fire_time": r.fire_time,
        "interval_hours": r.interval_hours,
        "enabled": r.enabled,
        "last_fired_at": r.last_fired_at,
    }


@router.get("", responses={200: {"model": ReminderListOut}})
def list_reminders(
    session: ReadSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_SETTINGS_MANAGE))],
) -> dict[str, Any]:
    reminders.ensure_defaults(session, ctx.business_id)
    items = reminders.list_schedules(ctx, session)
    return {"items": [_out(r) for r in items]}


class ReminderIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    #: `HH:MM`, business-local. Mutually exclusive with `interval_hours`.
    fire_time: str | None = None
    interval_hours: int | None = Field(default=None, ge=3, le=5)

    @field_validator("fire_time")
    @classmethod
    def _parse_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        dt_time.fromisoformat(value)  # raises ValueError -> 422, on a bad shape
        return value


@router.post("", status_code=201, responses={201: {"model": CreatedIdOut}})
def create_reminder(
    body: ReminderIn,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_SETTINGS_MANAGE))],
) -> dict[str, Any]:
    fire_time = dt_time.fromisoformat(body.fire_time) if body.fire_time else None
    schedule_id = reminders.create_schedule(
        ctx, session, label=body.label, fire_time=fire_time, interval_hours=body.interval_hours
    )
    return {"id": str(schedule_id)}


class ReminderUpdateIn(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None


@router.put("/{reminder_id}", responses={200: {"model": UpdatedOut}})
def update_reminder(
    reminder_id: uuid.UUID,
    body: ReminderUpdateIn,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_SETTINGS_MANAGE))],
) -> dict[str, Any]:
    from bm.domain.shared.errors import NotFound

    if not reminders.update_schedule(
        ctx, session, reminder_id, label=body.label, enabled=body.enabled
    ):
        raise NotFound("That reminder does not exist.")
    return {"updated": True}


@router.delete("/{reminder_id}", status_code=204)
def delete_reminder(
    reminder_id: uuid.UUID,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_SETTINGS_MANAGE))],
) -> None:
    reminders.delete_schedule(ctx, session, reminder_id)
