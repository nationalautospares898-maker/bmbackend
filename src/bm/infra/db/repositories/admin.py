"""Data access for user and device administration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session

from bm.infra.db.models.identity import Device, RefreshToken, User


@dataclass(frozen=True, slots=True)
class AdminUserRow:
    id: uuid.UUID
    username: str
    full_name: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdminDeviceRow:
    id: uuid.UUID
    label: str
    status: str
    branch_id: uuid.UUID | None
    counter_id: uuid.UUID | None
    last_seen_at: datetime | None
    revoked_at: datetime | None
    revoke_reason: str | None


class AdminRepository:
    """Every query carries the `business_id` predicate here, not at the call site."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- users ---------------------------------------------------------------

    def list_users(self, business_id: uuid.UUID) -> list[AdminUserRow]:
        rows = (
            self._session.execute(
                select(User).where(User.business_id == business_id).order_by(User.full_name)
            )
            .scalars()
            .all()
        )
        return [self._to_user(r) for r in rows]

    def get_user(self, user_id: uuid.UUID, business_id: uuid.UUID) -> AdminUserRow | None:
        row = self._session.execute(
            select(User).where(User.id == user_id, User.business_id == business_id)
        ).scalar_one_or_none()
        return self._to_user(row) if row else None

    @staticmethod
    def _to_user(row: User) -> AdminUserRow:
        return AdminUserRow(
            id=row.id,
            username=str(row.username),
            full_name=row.full_name,
            is_active=row.is_active,
            must_change_password=row.must_change_password,
            last_login_at=row.last_login_at,
        )

    def update_user(
        self,
        user_id: uuid.UUID,
        business_id: uuid.UUID,
        *,
        full_name: str | None = None,
        is_active: bool | None = None,
    ) -> bool:
        values: dict[str, Any] = {}
        if full_name is not None:
            values["full_name"] = full_name
        if is_active is not None:
            values["is_active"] = is_active
        if not values:
            return False
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(User)
                .where(User.id == user_id, User.business_id == business_id)
                .values(**values)
            ),
        )
        return bool(result.rowcount)

    def force_password_reset(
        self, user_id: uuid.UUID, business_id: uuid.UUID, *, password_hash: str
    ) -> bool:
        """Always sets `must_change_password`.

        The administrator knows the temporary value, so it is not yet a credential the
        staff member owns (T-5).
        """
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(User)
                .where(User.id == user_id, User.business_id == business_id)
                .values(password_hash=password_hash, must_change_password=True)
            ),
        )
        return bool(result.rowcount)

    def revoke_all_sessions(self, user_id: uuid.UUID) -> int:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=func.now())
            ),
        )
        return int(result.rowcount or 0)

    # --- devices --------------------------------------------------------------

    def list_devices(self, business_id: uuid.UUID) -> list[AdminDeviceRow]:
        rows = (
            self._session.execute(
                select(Device).where(Device.business_id == business_id).order_by(Device.label)
            )
            .scalars()
            .all()
        )
        return [
            AdminDeviceRow(
                id=r.id,
                label=r.label,
                status=str(r.status),
                branch_id=r.branch_id,
                counter_id=r.counter_id,
                last_seen_at=r.last_seen_at,
                revoked_at=r.revoked_at,
                revoke_reason=r.revoke_reason,
            )
            for r in rows
        ]
