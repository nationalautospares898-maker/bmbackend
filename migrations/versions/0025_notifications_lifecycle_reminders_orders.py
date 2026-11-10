"""Notification lifecycle, scheduled reminders, and Chitti orders.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-10

Three owner requests (2026-11-10), one migration, because the second and third build on
the first:

## 1. A real notification lifecycle (Feature A.1/A.2)

`notifications` (migration `0015`) had exactly one state transition: unread -> read.
That is not enough to tell a dismissal from a resolution — dismissing a low-stock
notice must never look like the stock was restocked, and acknowledging a bill reminder
must never look like the bill was paid, both of which the owner was explicit about.

So `status` replaces the binary with five states (`active`, `dismissed`, `acknowledged`,
`resolved`, `snoozed`), each reachable only by an explicit action, and `status_changed_at`
+ `snoozed_until` record when and until when. `read_at` is kept exactly as it was — "has
anyone looked at this" is a different question from "is this still open" and the two
must not collapse into one column.

A partial unique index (`business_id`, `kind`, `entity_id`) where `status = 'active'`
is the mechanism behind "do not duplicate an unchanged condition": a second attempt to
raise the same still-open alert for the same thing is a no-op at the database level, not
a discipline the application has to remember. Once that alert is dismissed or resolved,
the slot is free and a *recurrence* of the same condition raises a fresh one — which is
the other half of what the owner asked for.

Four new `notification_kind` values back the reminder and order features below:
`bill_reminder`, `stock_reminder`, `order_reminder`, `custom_reminder`. The four that
already existed (`low_stock`, `overdue_credit`, `reconciliation_alert`,
`import_complete`) are untouched.

## 2. Scheduled reminders the owner can see and change (Feature A.5)

`reminder_schedules` is deliberately small: a kind, a label, *either* a business-local
fire time (`08:45` for the bill reminder, `17:00` for the stock reminder) *or* a repeat
interval in hours (for "every 3-5 hours, remind me of pending bills"), whether it is
enabled, and `last_fired_at` — the exact moment it last fired, which is what stops a
worker tick from firing a daily reminder six times in the same business-day, and is
also how an interval-based reminder knows whether its interval has actually elapsed.
The two built-in reminders (bill, stock) are seeded by `seed_dev.py`/application
bootstrap, not by this migration — a migration that inserts business data was the exact
anti-pattern flagged in `PRODUCTION-DATABASE-READINESS.md`.

## 3. Chitti orders (Feature B)

A phoned-in order the owner writes down to turn into a bill later. `orders` is
independent of `invoices` by design (ADR-015's lineage: a correction is cancellation,
return, credit note or reversal — and an order is simply not a sale yet, so it is not an
invoice at all, draft or otherwise). `status` is `pending` / `completed` / `discarded`;
`last_reminder_at` drives the ten-minute nudge in `jobs/handlers.py` without a second
table, the same way `reminder_schedules.last_fired_at` does for scheduled reminders.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_KINDS = ("bill_reminder", "stock_reminder", "order_reminder", "custom_reminder")


def upgrade() -> None:
    # --- 1. notification lifecycle ---------------------------------------------
    for kind in _NEW_KINDS:
        op.execute(f"ALTER TYPE notification_kind ADD VALUE IF NOT EXISTS '{kind}'")

    op.add_column(
        "notifications",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "notifications",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_notifications__status",
        "notifications",
        "status IN ('active', 'dismissed', 'acknowledged', 'resolved', 'snoozed')",
    )
    # One open alert per condition. `entity_id IS NOT NULL` because a notification with
    # no entity (none exist yet, but nothing requires one) has nothing to deduplicate
    # against — two such rows are not "the same condition" by construction.
    op.create_index(
        "uq_notifications__active_per_entity",
        "notifications",
        ["business_id", "kind", "entity_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND entity_id IS NOT NULL"),
    )

    # --- 2. scheduled reminders --------------------------------------------------
    op.create_table(
        "reminder_schedules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        #: Business-local time of day this fires at, e.g. `08:45`. Mutually exclusive
        #: with `interval_hours` — a schedule is either a clock time or a repeat cadence.
        sa.Column("fire_time", sa.Time(), nullable=True),
        sa.Column("interval_hours", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        #: The exact moment this last fired. Serves both trigger kinds: a `fire_time`
        #: schedule compares its business-local *date* against today's; an
        #: `interval_hours` schedule compares elapsed time against the interval. One
        #: column does both, so firing logic never has to reconcile two clocks.
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('bill_reminder', 'stock_reminder', 'custom')",
            name="ck_reminder_schedules__kind",
        ),
        sa.CheckConstraint(
            "(fire_time IS NOT NULL) <> (interval_hours IS NOT NULL)",
            name="ck_reminder_schedules__one_trigger",
        ),
        sa.CheckConstraint(
            "interval_hours IS NULL OR interval_hours BETWEEN 3 AND 5",
            name="ck_reminder_schedules__interval_range",
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reminder_schedules__business", "reminder_schedules", ["business_id"])

    # --- 3. Chitti orders ---------------------------------------------------------
    op.create_table(
        "orders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("customer_name", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        #: The free-form note itself — items, quantities, anything the owner jotted
        #: down. Deliberately unstructured (FR owner request: "no unnecessary
        #: structured fields before saving").
        sa.Column("body", sa.String(length=4000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        #: Last time the ten-minute nudge fired for this order. Null for a brand new
        #: order so the first reminder is not delayed ten minutes after creation.
        sa.Column("last_reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'discarded')", name="ck_orders__status"
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="ck_orders__completed_fields",
        ),
        sa.CheckConstraint(
            "(status = 'discarded') = (discarded_at IS NOT NULL)",
            name="ck_orders__discarded_fields",
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_orders__pending",
        "orders",
        ["business_id", sa.literal_column("created_at DESC")],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_orders__pending", table_name="orders")
    op.drop_table("orders")

    op.drop_index("ix_reminder_schedules__business", table_name="reminder_schedules")
    op.drop_table("reminder_schedules")

    op.drop_index("uq_notifications__active_per_entity", table_name="notifications")
    op.drop_constraint("ck_notifications__status", "notifications", type_="check")
    op.drop_column("notifications", "snoozed_until")
    op.drop_column("notifications", "status_changed_at")
    op.drop_column("notifications", "status")
    # Postgres cannot drop an enum value, so the four added above are left in place —
    # harmless (ADR precedent: enum values are additive-only in this project) rather
    # than requiring a type rebuild that would lock every row in `notifications`.
