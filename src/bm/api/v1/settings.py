"""Settings administration.

`GET /admin/settings` · `PUT /admin/settings/{key}` — both `admin.settings.manage`.

These are the owner's business rules (`CLAUDE.md §11`): tax mode, draft limits, credit
terms, discount caps, the cancellation window, the print profile. They live in the
database precisely so none of them needs a deploy to change.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from bm.api.deps import CurrentContext, WriteSession, require
from bm.api.v1.schemas.common import ResponseModel, StrictModel
from bm.domain.auth.permissions import Permission
from bm.domain.settings.service import ResolvedSetting, SettingsService

router = APIRouter(prefix="/admin", tags=["admin"])


class SettingOut(ResponseModel):
    key: str
    #: Decimals are emitted as strings, like every other money-shaped value (ADR-013).
    value: Any
    value_type: str
    description: str
    #: True when no row exists and the documented code default is in force.
    is_default: bool
    allowed: list[str] | None


class SettingUpdateRequest(StrictModel):
    #: Typed per key by `domain.settings.defaults.coerce`, which rejects an unknown
    #: enum value or an out-of-range number rather than clamping it.
    value: Any


def _out(resolved: ResolvedSetting) -> SettingOut:
    value = resolved.value
    return SettingOut(
        key=resolved.key,
        value=str(value) if isinstance(value, Decimal) else value,
        value_type=resolved.value_type,
        description=resolved.description,
        is_default=resolved.is_default,
        allowed=list(resolved.allowed) if resolved.allowed else None,
    )


@router.get("/settings", response_model=list[SettingOut])
def list_settings(
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_SETTINGS_MANAGE))],
) -> list[SettingOut]:
    return [_out(item) for item in SettingsService(session).list_for(ctx)]


@router.put("/settings/{key}", response_model=SettingOut)
def update_setting(
    key: str,
    body: SettingUpdateRequest,
    session: WriteSession,
    ctx: Annotated[CurrentContext, Depends(require(Permission.ADMIN_SETTINGS_MANAGE))],
) -> SettingOut:
    """Audited with before/after values (FR-AUDIT-003).

    A discount cap or a credit term is a business rule that moves money, so "who
    changed this and when" has to be answerable.
    """
    return _out(SettingsService(session).set(ctx, key, body.value))
