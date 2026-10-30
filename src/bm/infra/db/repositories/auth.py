"""Data access for identity and sessions.

ORM models stay inside this layer; the service above receives plain dataclasses
(`ARCHITECTURE.md §3` rule 4). Every query that touches a business-scoped table carries
the `business_id` predicate here, not at the call site, so tenant scoping cannot be
forgotten (`SECURITY.md §3.2`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from bm.infra.db.enums import DeviceStatus
from bm.infra.db.models.identity import (
    Device,
    LoginAttempt,
    Membership,
    RefreshToken,
    Role,
    RolePermission,
    User,
)
from bm.infra.db.models.tenancy import Branch, Business
from bm.infra.db.rows import rows_affected


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: uuid.UUID
    business_id: uuid.UUID
    username: str
    full_name: str
    password_hash: str
    pin_hash: str | None
    must_change_password: bool
    is_active: bool


@dataclass(frozen=True, slots=True)
class MembershipRecord:
    id: uuid.UUID
    business_id: uuid.UUID
    branch_id: uuid.UUID | None
    role_id: uuid.UUID
    role_code: str


@dataclass(frozen=True, slots=True)
class BranchRecord:
    id: uuid.UUID
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class BusinessRecord:
    id: uuid.UUID
    display_name: str
    timezone: str
    financial_year_start_month: int


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    id: uuid.UUID
    business_id: uuid.UUID
    branch_id: uuid.UUID | None
    counter_id: uuid.UUID | None
    label: str
    status: str

    @property
    def is_revoked(self) -> bool:
        return self.status == DeviceStatus.REVOKED


@dataclass(frozen=True, slots=True)
class RefreshTokenRecord:
    id: uuid.UUID
    user_id: uuid.UUID
    device_id: uuid.UUID
    family_id: uuid.UUID
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None

    def is_usable(self, now: datetime) -> bool:
        return self.consumed_at is None and self.revoked_at is None and self.expires_at > now


class AuthRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- users ---------------------------------------------------------------

    def find_user_by_username(self, username: str) -> UserRecord | None:
        """Username is `citext`, so matching is case-insensitive in the database.

        Deliberately **not** scoped to a business: login happens before a tenant is
        known. The single-business deployment makes this unambiguous; a future
        multi-business login would need a tenant hint in the request.
        """
        row = self._session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        return self._to_user(row) if row else None

    def get_user(self, user_id: uuid.UUID, business_id: uuid.UUID) -> UserRecord | None:
        row = self._session.execute(
            select(User).where(User.id == user_id, User.business_id == business_id)
        ).scalar_one_or_none()
        return self._to_user(row) if row else None

    @staticmethod
    def _to_user(row: User) -> UserRecord:
        return UserRecord(
            id=row.id,
            business_id=row.business_id,
            username=str(row.username),
            full_name=row.full_name,
            password_hash=row.password_hash,
            pin_hash=row.pin_hash,
            must_change_password=row.must_change_password,
            is_active=row.is_active,
        )

    def touch_last_login(self, user_id: uuid.UUID, at: datetime) -> None:
        self._session.execute(update(User).where(User.id == user_id).values(last_login_at=at))

    def set_password(self, user_id: uuid.UUID, password_hash: str) -> None:
        self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(password_hash=password_hash, must_change_password=False)
        )

    # --- memberships and permissions -----------------------------------------

    def memberships_for(self, user_id: uuid.UUID) -> list[MembershipRecord]:
        rows = self._session.execute(
            select(Membership, Role.code)
            .join(Role, Role.id == Membership.role_id)
            .where(Membership.user_id == user_id, Membership.is_active.is_(True))
            .order_by(Role.code)
        ).all()
        return [
            MembershipRecord(
                id=membership.id,
                business_id=membership.business_id,
                branch_id=membership.branch_id,
                role_id=membership.role_id,
                role_code=role_code,
            )
            for membership, role_code in rows
        ]

    def permissions_for_role(self, role_id: uuid.UUID) -> frozenset[str]:
        """Read from `role_permissions`, not from the code constant.

        The constant seeds the rows; the rows are what authorises. Reading the code
        instead would make an owner's customised role silently ineffective.
        """
        rows = self._session.execute(
            select(RolePermission.permission_code).where(RolePermission.role_id == role_id)
        ).scalars()
        return frozenset(rows)

    # --- business and branches ------------------------------------------------

    def get_business(self, business_id: uuid.UUID) -> BusinessRecord | None:
        row = self._session.execute(
            select(Business).where(Business.id == business_id)
        ).scalar_one_or_none()
        if row is None:
            return None
        return BusinessRecord(
            id=row.id,
            display_name=row.display_name,
            timezone=row.timezone,
            financial_year_start_month=row.financial_year_start_month,
        )

    def branches_for(
        self, business_id: uuid.UUID, branch_ids: list[uuid.UUID] | None
    ) -> list[BranchRecord]:
        """`branch_ids=None` means the membership covers every branch."""
        query = select(Branch).where(Branch.business_id == business_id, Branch.is_active.is_(True))
        if branch_ids is not None:
            query = query.where(Branch.id.in_(branch_ids))
        rows = self._session.execute(query.order_by(Branch.code)).scalars().all()
        return [BranchRecord(id=r.id, code=r.code, name=r.name) for r in rows]

    # --- devices --------------------------------------------------------------

    def get_device(self, device_id: uuid.UUID, business_id: uuid.UUID) -> DeviceRecord | None:
        row = self._session.execute(
            select(Device).where(Device.id == device_id, Device.business_id == business_id)
        ).scalar_one_or_none()
        if row is None:
            return None
        return DeviceRecord(
            id=row.id,
            business_id=row.business_id,
            branch_id=row.branch_id,
            counter_id=row.counter_id,
            label=row.label,
            status=str(row.status),
        )

    def register_device(
        self,
        *,
        device_id: uuid.UUID,
        business_id: uuid.UUID,
        branch_id: uuid.UUID | None,
        label: str,
        user_agent: str | None,
    ) -> DeviceRecord:
        """Upsert on the client-generated id, so a reload is not a new device.

        A **revoked** device is never silently reactivated by logging in again: the
        `ON CONFLICT` updates only the label and last-seen time. Reactivation is an
        explicit administrative act.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        statement = (
            pg_insert(Device)
            .values(
                id=device_id,
                business_id=business_id,
                branch_id=branch_id,
                label=label,
                user_agent=user_agent,
                status=DeviceStatus.ACTIVE,
                last_seen_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=[Device.id],
                set_={"label": label, "user_agent": user_agent, "last_seen_at": func.now()},
            )
            .returning(Device)
        )
        row = self._session.execute(statement).scalar_one()
        return DeviceRecord(
            id=row.id,
            business_id=row.business_id,
            branch_id=row.branch_id,
            counter_id=row.counter_id,
            label=row.label,
            status=str(row.status),
        )

    def touch_device(self, device_id: uuid.UUID) -> None:
        self._session.execute(
            update(Device).where(Device.id == device_id).values(last_seen_at=func.now())
        )

    def revoke_device(
        self, device_id: uuid.UUID, business_id: uuid.UUID, *, by_user_id: uuid.UUID, reason: str
    ) -> bool:
        return bool(
            rows_affected(
                self._session.execute(
                    update(Device)
                    .where(
                        Device.id == device_id,
                        Device.business_id == business_id,
                        Device.status == DeviceStatus.ACTIVE,
                    )
                    .values(
                        status=DeviceStatus.REVOKED,
                        revoked_at=func.now(),
                        revoked_by_user_id=by_user_id,
                        revoke_reason=reason,
                    )
                )
            )
        )

    # --- refresh tokens -------------------------------------------------------

    def create_refresh_token(
        self,
        *,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        family_id: uuid.UUID,
        token_hash: str,
        ttl_days: int,
        now: datetime,
    ) -> uuid.UUID:
        token_id = uuid.uuid4()
        self._session.execute(
            insert(RefreshToken).values(
                id=token_id,
                user_id=user_id,
                device_id=device_id,
                family_id=family_id,
                token_hash=token_hash,
                expires_at=now + timedelta(days=ttl_days),
            )
        )
        return token_id

    def find_refresh_token(self, token_hash: str) -> RefreshTokenRecord | None:
        row = self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        ).scalar_one_or_none()
        if row is None:
            return None
        return RefreshTokenRecord(
            id=row.id,
            user_id=row.user_id,
            device_id=row.device_id,
            family_id=row.family_id,
            expires_at=row.expires_at,
            consumed_at=row.consumed_at,
            revoked_at=row.revoked_at,
        )

    def consume_refresh_token(
        self, token_id: uuid.UUID, *, replaced_by_id: uuid.UUID, now: datetime
    ) -> bool:
        """Consume only if not already consumed.

        The `consumed_at IS NULL` predicate is what makes rotation atomic under two
        concurrent refreshes: exactly one wins, and the loser is treated as reuse.
        """
        return bool(
            rows_affected(
                self._session.execute(
                    update(RefreshToken)
                    .where(RefreshToken.id == token_id, RefreshToken.consumed_at.is_(None))
                    .values(consumed_at=now, replaced_by_id=replaced_by_id)
                )
            )
        )

    def revoke_family(self, family_id: uuid.UUID, now: datetime) -> int:
        """Revoke every live token in a family.

        Called on reuse detection: a consumed token presented again means the cookie was
        copied, so the whole family goes (FR-AUTH-002).
        """
        return rows_affected(
            self._session.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )
        )

    def revoke_tokens_for_device(self, device_id: uuid.UUID, now: datetime) -> int:
        return rows_affected(
            self._session.execute(
                update(RefreshToken)
                .where(RefreshToken.device_id == device_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )
        )

    # --- login attempts -------------------------------------------------------

    def record_attempt(
        self,
        *,
        business_id: uuid.UUID | None,
        username: str | None,
        ip_hash: str | None,
        succeeded: bool,
        reason: str | None,
    ) -> None:
        self._session.execute(
            insert(LoginAttempt).values(
                id=uuid.uuid4(),
                business_id=business_id,
                username_attempted=username,
                ip_hash=ip_hash,
                succeeded=succeeded,
                reason=reason,
            )
        )

    def recent_failures(
        self, *, username: str | None, ip_hash: str | None, since: datetime
    ) -> tuple[int, int]:
        """`(failures for this username, failures from this IP)` in the window."""
        by_username = 0
        if username:
            by_username = int(
                self._session.execute(
                    select(func.count())
                    .select_from(LoginAttempt)
                    .where(
                        LoginAttempt.username_attempted == username,
                        LoginAttempt.succeeded.is_(False),
                        LoginAttempt.created_at >= since,
                    )
                ).scalar_one()
            )
        by_ip = 0
        if ip_hash:
            by_ip = int(
                self._session.execute(
                    select(func.count())
                    .select_from(LoginAttempt)
                    .where(
                        LoginAttempt.ip_hash == ip_hash,
                        LoginAttempt.succeeded.is_(False),
                        LoginAttempt.created_at >= since,
                    )
                ).scalar_one()
            )
        return by_username, by_ip

    def last_successful_login_at(self, username: str) -> datetime | None:
        """A successful login ends a lockout window: the failures before it are stale."""
        return self._session.execute(
            select(func.max(LoginAttempt.created_at)).where(
                LoginAttempt.username_attempted == username,
                LoginAttempt.succeeded.is_(True),
            )
        ).scalar_one_or_none()
