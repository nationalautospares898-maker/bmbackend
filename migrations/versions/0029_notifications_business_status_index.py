"""Add a `(business_id, status)` index to `notifications` (architecture/performance audit).

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-21

`notifications.active_for()` (`domain/notifications/service.py`) — the query behind
`GET /notifications`, which the counter application polls every 30 seconds while a tab
is open and signed in — filters

    WHERE business_id = :business_id
      AND (status = 'active' OR (status = 'snoozed' AND snoozed_until <= now()))

`0015` created `ix_notifications__unread` on `(business_id, created_at DESC) WHERE
read_at IS NULL`, back when "unread" was the only state. `0025` replaced that model with
the five-state `status` lifecycle and added only a dedup index
(`uq_notifications__active_per_entity`) — no index covering `(business_id, status)` was
added for the general listing query above. `read_at` is independent of `status` (service
comment, same file: `mark_read` only ever sets `read_at`, never `status`), so Postgres
cannot use the old partial index for this predicate at all: every poll is a sequential
scan of the whole `notifications` table for the business, on a table every low-stock
scan, every reminder firing and every order creation appends a row to, forever.

A plain (non-partial) index on `(business_id, status)` is deliberately simple rather than
a partial index matching the exact `OR` predicate: the `snoozed_until <= now()` half of
the condition is evaluated at query time, not something a partial index's `WHERE` clause
can usefully bake in, and a plain two-column index already turns "scan every row for this
business" into "look up this business's `active` rows directly, and separately its
`snoozed` rows" — the `snoozed_until` filter and the final `ORDER BY` (a computed
severity rank, which no column index can satisfy regardless) still apply after that, over
a result set the index has already made small. `CREATE INDEX CONCURRENTLY` because
`notifications` is a live, continuously-written production table — a plain
`CREATE INDEX` would hold an exclusive lock for the build's duration, which is exactly
what `CLAUDE.md §7` prohibits on a populated table.

`migrations/env.py` runs every migration inside its own transaction
(`transaction_per_migration=True`), and `CREATE INDEX CONCURRENTLY` is refused inside a
transaction block — so this is wrapped in `op.get_context().autocommit_block()`, which
temporarily steps outside that transaction for the one statement that needs to.
(No earlier migration in this repository does this; several — `0017`, `0018`, `0019`,
`0020`, `0025` — added indexes to already-populated tables with a plain, non-concurrent
`CREATE INDEX` instead. That gap is noted in the audit; it is not fixed retroactively
here, since redoing five already-applied migrations would not change anything about the
schema those indexes now already exist in.)
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_notifications__business_status"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            _INDEX_NAME,
            "notifications",
            ["business_id", "status"],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX_NAME,
            table_name="notifications",
            postgresql_concurrently=True,
        )
