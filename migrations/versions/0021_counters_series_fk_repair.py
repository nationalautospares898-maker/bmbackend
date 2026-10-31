"""Make `counters.invoice_series_id`'s foreign key unconditional.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-31

Migration `0010` adds the deferred foreign key from `counters.invoice_series_id` to
`invoice_series.id` — `counters` is created back in `0002`, before the target table
exists, so the constraint cannot be declared at table-creation time. That part is
correct and deliberate.

What is not correct is that it is **conditional on a database role existing**:

    if _app_role_exists():
        op.execute(
            "ALTER TABLE counters ADD CONSTRAINT fk_counters__invoice_series ..."
        )

`_app_role_exists()` is there so the `GRANT`/`REVOKE` statements elsewhere in that
revision can be skipped on a developer machine that runs everything as one role — a
reasonable accommodation, and the docstring explains it. The foreign key was placed
inside the same guard, and referential integrity has nothing to do with whether `bm_app`
has been created. On any cluster without that role the constraint is **silently never
created**, and nothing reports it: `counters.invoice_series_id` still has its column, the
application still works, and a row pointing at a deleted series simply becomes possible.

That is not hypothetical. The `migrations` job in `.github/workflows/ci.yml` runs
`alembic upgrade head` against a database where `bm_app` is never created, so every
migration run in that job produces a schema missing this constraint — which is also why
a schema-drift check could not be added to that job until now.

This revision repairs it, forward-only and idempotently:

* `0010` is left byte-for-byte as it was. It has already run on real databases, and
  editing an applied revision so it would behave differently is how two environments
  end up believing they are at the same version while holding different schemas.
* The constraint is added here only if it is absent, so this is a no-op on a database
  where `0010` did create it, and a repair on one where it did not.

`ON DELETE SET NULL` matches `0010` exactly: a counter whose optional series is removed
loses the pointer rather than blocking the delete. Per-counter series remain the
documented escalation path and must not be used without re-checking OD-25 first
(`ARCHITECTURE.md §5.4`).
"""

from __future__ import annotations

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | None = None
depends_on: str | None = None

CONSTRAINT = "fk_counters__invoice_series"


def upgrade() -> None:
    # `IF NOT EXISTS` is not available for `ADD CONSTRAINT`, so the guard is explicit.
    # Keyed on `pg_constraint` rather than on the role, which was the original mistake.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{CONSTRAINT}'
            ) THEN
                ALTER TABLE counters
                    ADD CONSTRAINT {CONSTRAINT}
                    FOREIGN KEY (invoice_series_id)
                    REFERENCES invoice_series (id)
                    ON DELETE SET NULL;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    # Dropped unconditionally-but-safely. `0010`'s own downgrade also drops it with
    # `IF EXISTS`, so a full downgrade past `0010` stays correct either way.
    op.execute(f"ALTER TABLE counters DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
