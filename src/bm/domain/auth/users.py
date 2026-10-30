"""User administration.

Deactivation is the only "delete": a user is referenced by every financial row they
created, so removing them would orphan the audit trail (FR-AUDIT-001). The foreign keys
from `audit_logs` are `RESTRICT` for exactly that reason.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.hashing import hash_password, validate_password_strength
from bm.domain.auth.permissions import Permission
from bm.domain.shared.clock import Clock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import NotFound, ValidationFailed
from bm.infra.db.repositories.admin import AdminDeviceRow, AdminRepository, AdminUserRow


class UserAdminService:
    def __init__(self, session: Session, clock: Clock, *, password_pepper: str) -> None:
        self._session = session
        self._repo = AdminRepository(session)
        self._clock = clock
        self._pepper = password_pepper

    def list_users(self, ctx: AuthContext) -> list[AdminUserRow]:
        ctx.require(Permission.ADMIN_USERS_MANAGE)
        return self._repo.list_users(ctx.business_id)

    def list_devices(self, ctx: AuthContext) -> list[AdminDeviceRow]:
        ctx.require(Permission.ADMIN_DEVICES_MANAGE)
        return self._repo.list_devices(ctx.business_id)

    def update_user(
        self,
        ctx: AuthContext,
        user_id: uuid.UUID,
        *,
        full_name: str | None,
        is_active: bool | None,
    ) -> AdminUserRow:
        ctx.require(Permission.ADMIN_USERS_MANAGE)

        before = self._repo.get_user(user_id, ctx.business_id)
        if before is None:
            # A cross-tenant id returns 404, never 403, so existence is not leaked
            # (`API-SPECIFICATION.md §4.1`).
            raise NotFound("That user does not exist.")

        if is_active is False and user_id == ctx.user_id:
            # T-15: break-glass exists for a genuine lockout, not for an accidental
            # self-disable that leaves the shop with no owner account.
            raise ValidationFailed({"is_active": "You cannot deactivate your own account."})

        self._repo.update_user(user_id, ctx.business_id, full_name=full_name, is_active=is_active)
        after = self._repo.get_user(user_id, ctx.business_id)
        assert after is not None  # updated a row that was just read in this transaction

        if (before.full_name, before.is_active) != (after.full_name, after.is_active):
            action = AuditAction.ROLE_CHANGED
            if before.is_active != after.is_active:
                action = (
                    AuditAction.USER_ACTIVATED if after.is_active else AuditAction.USER_DEACTIVATED
                )
            audit.record_for(
                self._session,
                ctx,
                action=action,
                entity_type="user",
                entity_id=user_id,
                before={"full_name": before.full_name, "is_active": before.is_active},
                after={"full_name": after.full_name, "is_active": after.is_active},
            )
        return after

    def reset_password(self, ctx: AuthContext, user_id: uuid.UUID, *, new_password: str) -> None:
        """Forces a change on next login and signs out every live session."""
        ctx.require(Permission.ADMIN_USERS_MANAGE)

        if self._repo.get_user(user_id, ctx.business_id) is None:
            raise NotFound("That user does not exist.")

        try:
            validate_password_strength(new_password)
        except ValueError as exc:
            raise ValidationFailed({"new_password": str(exc)}) from exc

        self._repo.force_password_reset(
            user_id, ctx.business_id, password_hash=hash_password(new_password, pepper=self._pepper)
        )
        revoked = self._repo.revoke_all_sessions(user_id)
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.USER_PASSWORD_RESET,
            entity_type="user",
            entity_id=user_id,
            after={"must_change_password": True, "sessions_revoked": revoked},
        )
