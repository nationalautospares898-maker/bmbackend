"""Hourly pending-credit-bill reminders (owner request, NAS-NEW-05).

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-14

The built-in `bill_reminder` schedule (migration `0025`) fired once a day at a fixed
clock time (8:45 business-local). The owner now wants it to resurface every hour for
as long as a credit bill sits with an outstanding balance — this reuses the exact same
firing path (`domain/reminders/service._fire_one`'s `bill_reminder` branch, which
already queries `invoices` for `is_credit AND payment_status IN ('unpaid',
'partially_paid')` and already no-ops when nothing is outstanding) and the exact same
worker ticker (`reminders.check_schedules`, already polled every 60s by
`worker.py`) — only the *cadence* changes, from a once-daily `fire_time` to an
`interval_hours` of 1.

`ck_reminder_schedules__interval_range` was `interval_hours IS NULL OR interval_hours
BETWEEN 3 AND 5` — deliberately 3-5 for the *custom* reminders an owner creates
through the UI (`reminders.create_schedule` still enforces that same 3-5 range in
Python, unchanged by this migration). Widening the column-wide bound instead of adding
a kind-specific exception would let a future custom reminder be created at 1 hour too,
which nothing in this change asks for — so the constraint becomes conditional on
`kind`: `bill_reminder` may use 1-5, everything else keeps 3-5.

The data `UPDATE` below flips every **already-provisioned** business's existing
`bill_reminder` row from `fire_time=08:45` to `interval_hours=1` in place — new
businesses already get this via the updated `_BUILTIN_DEFAULTS` in
`domain/reminders/service.py`, but `ensure_defaults` is deliberately idempotent (never
touches a `bill_reminder` row that already exists), so without this `UPDATE` every
business created before this release would be stuck on the old daily cadence forever.
The label rename is guarded to the exact old default string, so an owner who already
renamed their own copy keeps whatever they called it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_LABEL = "Pending bills (8:45 AM)"
_NEW_LABEL = "Pending bills (hourly)"


def upgrade() -> None:
    op.drop_constraint("ck_reminder_schedules__interval_range", "reminder_schedules", type_="check")
    op.create_check_constraint(
        "ck_reminder_schedules__interval_range",
        "reminder_schedules",
        "interval_hours IS NULL"
        " OR (kind = 'bill_reminder' AND interval_hours BETWEEN 1 AND 5)"
        " OR (kind <> 'bill_reminder' AND interval_hours BETWEEN 3 AND 5)",
    )
    op.execute(
        sa.text(
            """
            UPDATE reminder_schedules
               SET interval_hours = 1,
                   fire_time = NULL,
                   label = CASE WHEN label = :old_label THEN :new_label ELSE label END,
                   updated_at = now()
             WHERE kind = 'bill_reminder'
               AND interval_hours IS NULL
            """
        ).bindparams(old_label=_OLD_LABEL, new_label=_NEW_LABEL)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE reminder_schedules
               SET fire_time = '08:45',
                   interval_hours = NULL,
                   label = CASE WHEN label = :new_label THEN :old_label ELSE label END,
                   updated_at = now()
             WHERE kind = 'bill_reminder'
               AND fire_time IS NULL
            """
        ).bindparams(old_label=_OLD_LABEL, new_label=_NEW_LABEL)
    )
    op.drop_constraint("ck_reminder_schedules__interval_range", "reminder_schedules", type_="check")
    op.create_check_constraint(
        "ck_reminder_schedules__interval_range",
        "reminder_schedules",
        "interval_hours IS NULL OR interval_hours BETWEEN 3 AND 5",
    )
