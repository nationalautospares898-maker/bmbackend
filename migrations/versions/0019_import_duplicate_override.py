"""Let an audited duplicate override exist without faking the file digest.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-30

FR-IMP-004 says two things that have to hold at once:

> `file_sha256` is unique per business. … Proceeding anyway requires an explicit flag
> and a reason, and is audited.

A plain `UNIQUE (business_id, file_sha256)` cannot express both: the second upload *is*
the same bytes, so the constraint rejects the very row the requirement says to create.
The first implementation worked around it by storing `"<digest>:<job_id>"` in the column
— which overflowed `varchar(64)`, and would have been wrong even if it fitted, because
it puts a value that is not a SHA-256 in a column named `file_sha256` and silently
breaks duplicate detection for every later upload of those bytes.

So the index becomes **partial on `duplicate_of_job_id IS NULL`**, which says what the
requirement means: *at most one primary job per file per business; a deliberate
re-upload is recorded as a duplicate of it, carrying the real digest and its reason.*
Duplicate detection keeps working, because it looks for the primary row.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("uq_import_jobs__file", "import_jobs", type_="unique")
    op.create_index(
        "uq_import_jobs__file",
        "import_jobs",
        ["business_id", "file_sha256"],
        unique=True,
        # `sa.text`, not `op.inline_literal`: the latter renders the predicate as a
        # quoted *string*, producing `WHERE 'duplicate_of_job_id IS NULL'` — a constant
        # truthy expression, so the index stays total and the override still collides.
        postgresql_where=sa.text("duplicate_of_job_id IS NULL"),
    )


def downgrade() -> None:
    # Reinstating the total constraint can fail on data this migration permits — two
    # jobs for the same bytes, one an override. That is correct: the downgrade cannot
    # silently discard an audited decision, so it fails loudly and an operator decides
    # which row to remove.
    op.drop_index("uq_import_jobs__file", table_name="import_jobs")
    op.create_unique_constraint(
        "uq_import_jobs__file", "import_jobs", ["business_id", "file_sha256"]
    )
