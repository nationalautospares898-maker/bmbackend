"""Device registry administration.

Separate from `AuthService` because revocation needs no signing key, pepper or token
lifetime — only a session and a clock. Forcing it through `AuthService` would mean
constructing credentials that the operation never uses, which is the kind of thing that
later gets filled in with a placeholder and then a real secret.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.shared.clock import Clock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import NotFound, ValidationFailed
from bm.infra.db.repositories.auth import AuthRepository


class DeviceService:
    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._repo = AuthRepository(session)
        self._clock = clock

    def revoke(self, ctx: AuthContext, device_id: uuid.UUID, *, reason: str) -> None:
        """Revoking stops refresh immediately and refuses sync on every endpoint (T-4).

        Local data on the device is **never** deleted in response — the client shows a
        blocking screen (`CLAUDE.md §8`). Unsynced drafts are lost by design; they were
        never sales (E-11).

        The permission is re-asserted here even though the route declares it, because
        this is a destructive action (`SECURITY.md §3.1` rule 2).
        """
        ctx.require(Permission.ADMIN_DEVICES_MANAGE)
        if len(reason.strip()) < 3:
            raise ValidationFailed({"reason": "Say briefly why this device is being removed."})

        existing = self._repo.get_device(device_id, ctx.business_id)
        if existing is None:
            # A cross-tenant id must look identical to an absent one (§4.1).
            raise NotFound("That device is not registered.")

        if not self._repo.revoke_device(
            device_id, ctx.business_id, by_user_id=ctx.user_id, reason=reason
        ):
            # Already revoked. Revoking twice is the same outcome the caller asked for,
            # so it is a no-op rather than a conflict.
            return

        self._repo.revoke_tokens_for_device(device_id, self._clock.now())
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.DEVICE_REVOKED,
            entity_type="device",
            entity_id=device_id,
            reason=reason,
            before={"status": existing.status},
            after={"status": "revoked"},
        )
