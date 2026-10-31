"""Administration endpoints: users, devices and counters.

Zero business logic here (`CLAUDE.md §4`) — each route declares its permission, validates
its body, and delegates. Settings administration lands with M4 and invoice-series
administration with M9.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import Field

from bm.api.deps import ClockDep, CurrentContext, SettingsDep, WriteSession, require
from bm.api.v1.schemas.common import ResponseModel, StrictModel
from bm.domain.auth.counters import MIN_OPEN_DRAFTS, CounterAdminService
from bm.domain.auth.devices import DeviceService
from bm.domain.auth.permissions import Permission
from bm.domain.auth.users import UserAdminService

router = APIRouter(prefix="/admin", tags=["admin"])


# --- schemas -----------------------------------------------------------------


class UserOut(ResponseModel):
    id: uuid.UUID
    username: str
    full_name: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None


class UserUpdateRequest(StrictModel):
    full_name: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None


class ResetPasswordRequest(StrictModel):
    new_password: str = Field(min_length=10, max_length=200)


class DeviceOut(ResponseModel):
    id: uuid.UUID
    label: str
    status: str
    branch_id: uuid.UUID | None
    counter_id: uuid.UUID | None
    last_seen_at: datetime | None
    revoked_at: datetime | None
    revoke_reason: str | None


class RevokeDeviceRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=500)


class AssignCounterRequest(StrictModel):
    """`null` detaches the computer from every counter.

    A sub-resource carrying one field rather than a general `PUT /admin/devices/{id}`:
    this is the only device column an administrator may set here, and a wider body would
    let a counter assignment also rewrite a device's label, branch or revocation state.
    """

    counter_id: uuid.UUID | None


class CounterDeviceOut(ResponseModel):
    id: uuid.UUID
    label: str
    status: str
    last_seen_at: datetime | None


class CounterOut(ResponseModel):
    id: uuid.UUID
    code: str
    name: str
    branch_id: uuid.UUID
    max_open_drafts: int
    is_active: bool
    #: Every computer currently billing as this counter. Plural by design — see the note
    #: in `bm.domain.auth.counters`.
    devices: list[CounterDeviceOut]


class CounterUpdateRequest(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    #: C-5's floor, refused here as well as in the service and the database.
    max_open_drafts: int | None = Field(default=None, ge=MIN_OPEN_DRAFTS)
    is_active: bool | None = None


# --- wiring ------------------------------------------------------------------


def _user_admin(session: WriteSession, clock: ClockDep, settings: SettingsDep) -> UserAdminService:
    return UserAdminService(
        session, clock, password_pepper=settings.password_pepper.get_secret_value()
    )


UserAdmin = Annotated[UserAdminService, Depends(_user_admin)]


# --- users -------------------------------------------------------------------


@router.get("/users", response_model=list[UserOut])
def list_users(
    service: UserAdmin,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_USERS_MANAGE))],
) -> list[UserOut]:
    return [UserOut.model_validate(row) for row in service.list_users(ctx)]


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdateRequest,
    service: UserAdmin,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_USERS_MANAGE))],
) -> UserOut:
    row = service.update_user(ctx, user_id, full_name=body.full_name, is_active=body.is_active)
    return UserOut.model_validate(row)


@router.post("/users/{user_id}:reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: uuid.UUID,
    body: ResetPasswordRequest,
    service: UserAdmin,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_USERS_MANAGE))],
) -> None:
    service.reset_password(ctx, user_id, new_password=body.new_password)


# --- devices -----------------------------------------------------------------


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(
    service: UserAdmin,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_DEVICES_MANAGE))],
) -> list[DeviceOut]:
    return [DeviceOut.model_validate(row) for row in service.list_devices(ctx)]


@router.post("/devices/{device_id}:revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_device(
    device_id: uuid.UUID,
    body: RevokeDeviceRequest,
    session: WriteSession,
    clock: ClockDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_DEVICES_MANAGE))],
) -> None:
    """Stops refresh immediately and refuses sync on every endpoint (T-4).

    Local data on the device is **never** deleted in response — the client shows a
    blocking screen. Unsynced drafts are lost by design; they were never sales (E-11).
    """
    DeviceService(session, clock).revoke(ctx, device_id, reason=body.reason)


@router.put("/devices/{device_id}/counter", response_model=DeviceOut)
def assign_device_counter(
    device_id: uuid.UUID,
    body: AssignCounterRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_SETTINGS_MANAGE))],
) -> DeviceOut:
    """Decide which counter a registered computer bills as (C-2).

    `admin.settings.manage`, not `admin.devices.manage`: this chooses whose takings the
    machine's next sale is attributed to, which is business configuration rather than
    device hygiene. Reasoning in `bm.domain.auth.counters`.
    """
    row = CounterAdminService(session).assign_device(ctx, device_id, counter_id=body.counter_id)
    return DeviceOut.model_validate(row)


# --- counters ----------------------------------------------------------------


@router.get("/counters", response_model=list[CounterOut])
def list_counters(
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_SETTINGS_MANAGE))],
) -> list[CounterOut]:
    return [
        CounterOut.model_validate(row) for row in CounterAdminService(session).list_counters(ctx)
    ]


@router.put("/counters/{counter_id}", response_model=CounterOut)
def update_counter(
    counter_id: uuid.UUID,
    body: CounterUpdateRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_SETTINGS_MANAGE))],
) -> CounterOut:
    """FR-ADMIN-003: a counter's name, draft capacity (minimum 20, C-5) and whether it
    is open for business."""
    row = CounterAdminService(session).update_counter(
        ctx,
        counter_id,
        name=body.name,
        max_open_drafts=body.max_open_drafts,
        is_active=body.is_active,
    )
    return CounterOut.model_validate(row)
