"""Authentication endpoints.

The refresh token is set as an `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth`
cookie and is **never** in a response body (NFR-SEC-003). `SameSite=Lax` plus the narrow
path is the CSRF control: every other endpoint authenticates with a bearer header, so a
cross-site form post cannot act as the user, and `/auth/refresh` only mints a token into
the same browser and is additionally bound to `X-Device-Id`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response, status
from pydantic import Field

from bm.api.deps import (
    AuthServiceDep,
    CurrentContext,
    SettingsDep,
    authenticated_only,
    client_ip,
)
from bm.api.v1.schemas.common import ResponseModel, StrictModel
from bm.domain.auth.service import LoginResult

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "bm_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"


# --- schemas -----------------------------------------------------------------


class DeviceInput(StrictModel):
    #: Client-generated and stable per browser profile, so a reload is not a new device.
    id: uuid.UUID
    label: str = Field(default="Counter PC", max_length=200)


class LoginRequest(StrictModel):
    username: str
    password: str
    device: DeviceInput


class UserOut(ResponseModel):
    id: uuid.UUID
    full_name: str
    must_change_password: bool


class BusinessOut(ResponseModel):
    id: uuid.UUID
    display_name: str


class BranchOut(ResponseModel):
    id: uuid.UUID
    code: str
    name: str


class SessionOut(ResponseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
    business: BusinessOut
    branches: list[BranchOut]
    active_branch_id: uuid.UUID | None
    role: str
    permissions: list[str]
    server_time: datetime


class MeOut(ResponseModel):
    user: UserOut
    business_id: uuid.UUID
    branch_id: uuid.UUID | None
    counter_id: uuid.UUID | None
    device_id: uuid.UUID | None
    role: str
    permissions: list[str]
    server_time: datetime
    #: So the client knows whether to render `ESTIMATE`. It can never be `TAX INVOICE`
    #: until the FR-TAX-003 gate passes.
    tax_mode: str


class SelectBranchRequest(StrictModel):
    branch_id: uuid.UUID


class SelectBranchOut(ResponseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    active_branch_id: uuid.UUID


class ChangePasswordRequest(StrictModel):
    current_password: str
    new_password: str


# --- helpers -----------------------------------------------------------------


def _set_refresh_cookie(response: Response, token: str, *, settings: SettingsDep) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        # `secure` is relaxed only for local HTTP development; staging and production
        # are HTTPS-only, so the cookie is never sent in clear there.
        secure=settings.env != "local",
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
    )


def _session_out(result: LoginResult) -> SessionOut:
    return SessionOut(
        access_token=result.access_token,
        expires_in=result.expires_in,
        user=UserOut(
            id=result.user.id,
            full_name=result.user.full_name,
            must_change_password=result.user.must_change_password,
        ),
        business=BusinessOut(id=result.business.id, display_name=result.business.display_name),
        branches=[BranchOut(id=b.id, code=b.code, name=b.name) for b in result.branches],
        active_branch_id=result.active_branch_id,
        role=result.role,
        permissions=sorted(result.permissions),
        server_time=result.server_time,
    )


# --- routes ------------------------------------------------------------------


@router.post("/login", response_model=SessionOut)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> SessionOut:
    result = service.login(
        username=body.username,
        password=body.password,
        device_id=body.device.id,
        device_label=str(body.device.label),
        user_agent=user_agent,
        ip=client_ip(request),
    )
    _set_refresh_cookie(response, result.refresh_token, settings=settings)
    return _session_out(result)


@router.post("/refresh", response_model=SessionOut)
def refresh(
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    x_device_id: Annotated[uuid.UUID | None, Header()] = None,
    bm_refresh: Annotated[str | None, Cookie()] = None,
) -> SessionOut:
    """Rotates the token. Presenting a consumed one revokes the whole family
    (FR-AUTH-002) — a consumed token in the wild means the cookie was copied."""
    from bm.domain.shared.errors import DeviceNotRegistered, RefreshTokenReused

    if not bm_refresh:
        raise RefreshTokenReused("Please sign in again.")
    if x_device_id is None:
        raise DeviceNotRegistered("This device is not registered.")

    result = service.refresh(presented_token=bm_refresh, device_id=x_device_id)
    _set_refresh_cookie(response, result.refresh_token, settings=settings)
    return _session_out(result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    service: AuthServiceDep,
    bm_refresh: Annotated[str | None, Cookie()] = None,
) -> None:
    service.logout(presented_token=bm_refresh)
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


@router.get("/me", response_model=MeOut)
def me(ctx: Annotated[CurrentContext, Depends(authenticated_only())]) -> MeOut:
    """The caller's own context. Needs no specific permission."""
    return MeOut(
        user=UserOut(
            id=ctx.user_id,
            full_name=ctx.full_name,
            must_change_password=ctx.must_change_password,
        ),
        business_id=ctx.business_id,
        branch_id=ctx.branch_id,
        counter_id=ctx.counter_id,
        device_id=ctx.device_id,
        role=ctx.role,
        permissions=sorted(ctx.permissions),
        server_time=datetime.now(UTC),
        # Resolved from settings in M4; until then the documented default is the only
        # possible value, and it is never silently upgraded.
        tax_mode="estimate_only",
    )


@router.post("/select-branch", response_model=SelectBranchOut)
def select_branch(
    body: SelectBranchRequest,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
    service: AuthServiceDep,
) -> SelectBranchOut:
    token, expires_in = service.select_branch(ctx, body.branch_id)
    return SelectBranchOut(
        access_token=token, expires_in=expires_in, active_branch_id=body.branch_id
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordRequest,
    ctx: Annotated[CurrentContext, Depends(authenticated_only())],
    service: AuthServiceDep,
) -> None:
    """Reachable while `must_change_password` is set — it is the one thing such a user
    must be able to do."""
    service.change_own_password(
        ctx, current_password=body.current_password, new_password=body.new_password
    )
