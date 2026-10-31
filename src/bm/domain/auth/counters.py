"""Counter administration: a till's configurable fields, and which PC bills as it.

Two things live here, and they are deliberately separate operations:

* **The counter itself** — `name`, `max_open_drafts`, `is_active` (FR-ADMIN-003). The
  draft-capacity floor of 20 is requirement C-5 and is enforced three times over: by the
  request model, by the check below, and by `ck_counters__min_drafts` in the database.
  Three is not redundancy for its own sake — the database constraint is the one that
  holds when a future caller bypasses this service, and a 422 is a better answer to the
  administrator than a constraint violation surfacing as a 500.

* **The device→counter assignment** — `devices.counter_id`. This is what makes a freshly
  enrolled PC able to bill at all: `register_device` sets `branch_id` and deliberately
  not `counter_id` (`DATA-MODEL.md §3.2` makes the column nullable on purpose), and
  `resolve_context` falls back to the registered device's counter. Until something writes
  that column there is nothing for it to fall back to, which is the gap requirement C-2
  names and nothing implemented.

Both require `admin.settings.manage`, which `API-SPECIFICATION.md §3.13` already assigns
to `GET /admin/counters` and `PUT /admin/counters/{id}`. The assignment is held to the
same permission rather than the weaker `admin.devices.manage` on purpose: that permission
reads "Register and revoke devices", which is machine hygiene, whereas choosing a
counter decides **whose takings the next sale on that machine lands in**. Only the owner
holds `admin.settings.manage`; a manager holds `admin.devices.manage` and can still
revoke a stolen till, which is the operational case that needs to be fast.

**One counter may have several devices, and that is not an error.**
`OFFLINE-SYNCHRONIZATION.md` describes "the same counter on two devices (a tablet and the
counter PC)" as a case the conflict-resolution rules exist to serve, `DECISIONS.md` says
"the same counter may edit one draft from two devices", there is no unique constraint on
`devices.counter_id`, and `tests/fixtures.py` registers every test device against the
same counter. So nothing here rejects a second device, and nothing here should start to:
it would break a documented workflow and a large part of the existing suite at once.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import DeviceRevoked, NotFound, ValidationFailed
from bm.infra.db.enums import DeviceStatus
from bm.infra.db.repositories.admin import AdminCounterRow, AdminDeviceRow, AdminRepository

#: C-5. Mirrored from `ck_counters__min_drafts` so the refusal is a 422 naming the field
#: rather than a constraint violation.
MIN_OPEN_DRAFTS = 20


class CounterAdminService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = AdminRepository(session)

    def list_counters(self, ctx: AuthContext) -> list[AdminCounterRow]:
        ctx.require(Permission.ADMIN_SETTINGS_MANAGE)
        return self._repo.list_counters(ctx.business_id)

    def update_counter(
        self,
        ctx: AuthContext,
        counter_id: uuid.UUID,
        *,
        name: str | None = None,
        max_open_drafts: int | None = None,
        is_active: bool | None = None,
    ) -> AdminCounterRow:
        ctx.require(Permission.ADMIN_SETTINGS_MANAGE)

        before = self._repo.get_counter(counter_id, ctx.business_id)
        if before is None:
            # A cross-tenant id returns 404, never 403, so existence is not leaked
            # (`API-SPECIFICATION.md §4.1`).
            raise NotFound("That counter does not exist.")

        if max_open_drafts is not None and max_open_drafts < MIN_OPEN_DRAFTS:
            raise ValidationFailed(
                {"max_open_drafts": f"A counter must allow at least {MIN_OPEN_DRAFTS} open bills."}
            )

        if is_active is False and before.devices:
            # Deactivating a counter that a till is still pointed at would leave that PC
            # authenticated, assigned, and unable to bill — the same dead end C-2 is
            # about, reached from the other direction.
            labels = ", ".join(d.label for d in before.devices)
            raise ValidationFailed(
                {
                    "is_active": (
                        "Move these computers to another counter first, then close this "
                        f"one: {labels}."
                    )
                }
            )

        self._repo.update_counter(
            counter_id,
            ctx.business_id,
            name=name,
            max_open_drafts=max_open_drafts,
            is_active=is_active,
        )
        after = self._repo.get_counter(counter_id, ctx.business_id)
        assert after is not None  # updated a row that was just read in this transaction

        if (before.name, before.max_open_drafts, before.is_active) != (
            after.name,
            after.max_open_drafts,
            after.is_active,
        ):
            audit.record_for(
                self._session,
                ctx,
                action=AuditAction.COUNTER_UPDATED,
                entity_type="counter",
                entity_id=counter_id,
                before={
                    "name": before.name,
                    "max_open_drafts": before.max_open_drafts,
                    "is_active": before.is_active,
                },
                after={
                    "name": after.name,
                    "max_open_drafts": after.max_open_drafts,
                    "is_active": after.is_active,
                },
            )
        return after

    def assign_device(
        self, ctx: AuthContext, device_id: uuid.UUID, *, counter_id: uuid.UUID | None
    ) -> AdminDeviceRow:
        """Point a registered PC at a counter, or at nothing (`counter_id = None`).

        Unassigning is supported because it is the honest way to retire a machine from
        billing without revoking it — revocation is for a device that must be refused
        everywhere (T-4), which is a different and much heavier statement.
        """
        ctx.require(Permission.ADMIN_SETTINGS_MANAGE)

        device = self._repo.get_device(device_id, ctx.business_id)
        if device is None:
            raise NotFound("That computer is not registered.")

        if device.status == DeviceStatus.REVOKED:
            # Assigning a counter to a revoked machine would hand a stolen till a route
            # back to billing without anyone re-authorising it.
            raise DeviceRevoked("That computer has been revoked. Register it again first.")

        if counter_id is not None:
            counter = self._repo.get_counter(counter_id, ctx.business_id)
            if counter is None:
                raise NotFound("That counter does not exist.")
            if not counter.is_active:
                raise ValidationFailed({"counter_id": "That counter is closed."})
            if device.branch_id is not None and device.branch_id != counter.branch_id:
                # Branch decides which stock and which invoice series a sale draws on, so
                # a device billing as a counter in another branch would move stock in one
                # place and number the bill in another.
                raise ValidationFailed(
                    {"counter_id": "That counter belongs to a different branch."}
                )

        if device.counter_id == counter_id:
            # Idempotent: assigning the counter a machine already has is what a double
            # click or a retried request looks like, and it is not a conflict. No audit
            # row either — nothing changed, and an audit trail that records non-events is
            # harder to read when it matters.
            return device

        self._repo.set_device_counter(device_id, ctx.business_id, counter_id=counter_id)
        after = self._repo.get_device(device_id, ctx.business_id)
        assert after is not None

        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.DEVICE_COUNTER_ASSIGNED,
            entity_type="device",
            entity_id=device_id,
            before={"counter_id": str(device.counter_id) if device.counter_id else None},
            after={"counter_id": str(after.counter_id) if after.counter_id else None},
        )
        return after
