"""Give import jobs a monotonic ordinal, so "newest first" is a guarantee.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-30

`GET /imports` promised newest-first and could not keep it. It ordered by
`created_at DESC, id DESC`, and both halves of that are wrong in the same way:

* `created_at` is **not unique**. It comes from `now()`, which is the transaction
  timestamp at the operating system's clock resolution. On an idle machine two uploads
  are microseconds apart; under load they tie. The full test suite reproduced it — the
  history came back oldest-first — while the same test passed in isolation.
* `id` is `uuid4`, so the tiebreak is **random**. On a tie the user's most recent upload
  appears second about half the time.

The same ties break the cursor. `?before=<created_at>` with a non-unique column either
**skips** a row or **returns it twice**, depending on which side of the boundary it
lands — the classic offset-pagination bug arriving through a timestamp cursor.

A `bigserial` fixes both properly. A sequence is monotonic per insert, which is exactly
what "newest" means, and it gives the keyset cursor a unique column to compare against.
`created_at` stays as the thing a human reads.

Backfilled in `created_at` order so existing rows get a sensible ordinal rather than an
arbitrary one.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Added nullable, backfilled, then made NOT NULL — so this applies cleanly to a
    # populated database. `bigserial` would assign values in physical order, which is
    # not necessarily upload order on a table that has seen updates.
    op.add_column("import_jobs", sa.Column("ordinal", sa.BigInteger(), nullable=True))
    op.execute(
        """
        WITH ordered AS (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS rn
              FROM import_jobs
        )
        UPDATE import_jobs j SET ordinal = ordered.rn
          FROM ordered WHERE ordered.id = j.id
        """
    )
    op.execute("CREATE SEQUENCE import_jobs_ordinal_seq OWNED BY import_jobs.ordinal")
    op.execute(
        "SELECT setval('import_jobs_ordinal_seq',"
        " coalesce((SELECT max(ordinal) FROM import_jobs), 0) + 1, false)"
    )
    op.execute(
        "ALTER TABLE import_jobs ALTER COLUMN ordinal"
        " SET DEFAULT nextval('import_jobs_ordinal_seq')"
    )
    op.alter_column("import_jobs", "ordinal", nullable=False)
    # The ordering and cursor index. Business-scoped, because every list is.
    op.create_index(
        "ix_import_jobs__ordinal", "import_jobs", ["business_id", "ordinal"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_import_jobs__ordinal", table_name="import_jobs")
    op.drop_column("import_jobs", "ordinal")
    op.execute("DROP SEQUENCE IF EXISTS import_jobs_ordinal_seq")
