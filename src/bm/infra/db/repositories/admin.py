"""Data access for user, device and counter administration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session

from bm.infra.db.models.identity import Device, RefreshToken, User
from bm.infra.db.models.tenancy import Counter


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


@dataclass(frozen=True, slots=True)
class AdminCounterRow:
    id: uuid.UUID
    code: str
    name: str
    branch_id: uuid.UUID
    max_open_drafts: int
    is_active: bool
    #: Devices currently billing as this counter. A list, not a single id: the schema
    #: has no unique constraint on `devices.counter_id` and the design relies on that —
    #: `OFFLINE-SYNCHRONIZATION.md §…` describes "the same counter on two devices (a
    #: tablet and the counter PC)" as a case the conflict rules exist to handle.
    devices: tuple[AdminDeviceRow, ...]


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
        return [self._to_device(r) for r in rows]

    def get_device(self, device_id: uuid.UUID, business_id: uuid.UUID) -> AdminDeviceRow | None:
        row = self._session.execute(
            select(Device).where(Device.id == device_id, Device.business_id == business_id)
        ).scalar_one_or_none()
        return self._to_device(row) if row else None

    @staticmethod
    def _to_device(row: Device) -> AdminDeviceRow:
        return AdminDeviceRow(
            id=row.id,
            label=row.label,
            status=str(row.status),
            branch_id=row.branch_id,
            counter_id=row.counter_id,
            last_seen_at=row.last_seen_at,
            revoked_at=row.revoked_at,
            revoke_reason=row.revoke_reason,
        )

    def set_device_counter(
        self, device_id: uuid.UUID, business_id: uuid.UUID, *, counter_id: uuid.UUID | None
    ) -> bool:
        """Writes `devices.counter_id` and nothing else.

        Deliberately not a general device update: label, status, branch and the
        revocation fields are governed by their own paths, and widening this would let a
        counter assignment silently un-revoke a stolen machine.
        """
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Device)
                .where(Device.id == device_id, Device.business_id == business_id)
                .values(counter_id=counter_id)
            ),
        )
        return bool(result.rowcount)

    # --- counters -------------------------------------------------------------

    def list_counters(self, business_id: uuid.UUID) -> list[AdminCounterRow]:
        counters = (
            self._session.execute(
                select(Counter).where(Counter.business_id == business_id).order_by(Counter.code)
            )
            .scalars()
            .all()
        )
        devices = (
            self._session.execute(
                select(Device)
                .where(Device.business_id == business_id, Device.counter_id.is_not(None))
                .order_by(Device.label)
            )
            .scalars()
            .all()
        )
        by_counter: dict[uuid.UUID, list[AdminDeviceRow]] = {}
        for device in devices:
            if device.counter_id is not None:
                by_counter.setdefault(device.counter_id, []).append(self._to_device(device))
        return [self._to_counter(c, tuple(by_counter.get(c.id, ()))) for c in counters]

    def get_counter(self, counter_id: uuid.UUID, business_id: uuid.UUID) -> AdminCounterRow | None:
        row = self._session.execute(
            select(Counter).where(Counter.id == counter_id, Counter.business_id == business_id)
        ).scalar_one_or_none()
        if row is None:
            return None
        devices = (
            self._session.execute(
                select(Device)
                .where(Device.business_id == business_id, Device.counter_id == counter_id)
                .order_by(Device.label)
            )
            .scalars()
            .all()
        )
        return self._to_counter(row, tuple(self._to_device(d) for d in devices))

    @staticmethod
    def _to_counter(row: Counter, devices: tuple[AdminDeviceRow, ...]) -> AdminCounterRow:
        return AdminCounterRow(
            id=row.id,
            code=row.code,
            name=row.name,
            branch_id=row.branch_id,
            max_open_drafts=row.max_open_drafts,
            is_active=row.is_active,
            devices=devices,
        )

    def update_counter(
        self,
        counter_id: uuid.UUID,
        business_id: uuid.UUID,
        *,
        name: str | None = None,
        max_open_drafts: int | None = None,
        is_active: bool | None = None,
    ) -> bool:
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if max_open_drafts is not None:
            values["max_open_drafts"] = max_open_drafts
        if is_active is not None:
            values["is_active"] = is_active
        if not values:
            return False
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Counter)
                .where(Counter.id == counter_id, Counter.business_id == business_id)
                .values(**values)
            ),
        )
        return bool(result.rowcount)
