"""Credit Bill amendment reason becomes optional (owner correction, 2026-11-14).

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-14

`0024` made `invoice_revisions.reason` `NOT NULL` with a floor
(`ck_invoice_revisions__reason_present`: `length(btrim(reason)) >= 5`), on the belief
that every edit should be explained. The owner tried the resulting "Edit Credit Bill"
screen and reported the opposite: they want to save a correction without being made to
type a reason first, and they explicitly do not want one invented on their behalf to
satisfy the column.

So the column becomes nullable, and the `CHECK` becomes conditional on presence rather
than requiring it: `reason IS NULL OR length(btrim(reason)) >= 5`. A reason that *is*
given is still held to the same five-character floor — the point was never that short
reasons are fine, only that no reason is. `InvoiceAmendService.amend` (`amend.py`)
enforces the identical rule in Python before this `CHECK` is ever reached, so a too-short
reason is a field error, not an integrity error; this migration only has to stop
rejecting the blank case the service now permits.

No existing row is affected: every `invoice_revisions.reason` written before this
migration already satisfies the new, looser `CHECK` (it is a strict relaxation of the
old one), so no data `UPDATE` is needed.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_invoice_revisions__reason_present", "invoice_revisions", type_="check")
    op.alter_column("invoice_revisions", "reason", nullable=True)
    op.create_check_constraint(
        "ck_invoice_revisions__reason_present",
        "invoice_revisions",
        "reason IS NULL OR length(btrim(reason)) >= 5",
    )


def downgrade() -> None:
    # A reversal has to pick something for the rows this version let through with no
    # reason at all — reverting is not pretending that information back into existence,
    # just restoring the old floor's shape for a rollback.
    op.execute("UPDATE invoice_revisions SET reason = '(no reason recorded)' WHERE reason IS NULL")
    op.drop_constraint("ck_invoice_revisions__reason_present", "invoice_revisions", type_="check")
    op.alter_column("invoice_revisions", "reason", nullable=False)
    op.create_check_constraint(
        "ck_invoice_revisions__reason_present",
        "invoice_revisions",
        "length(btrim(reason)) >= 5",
    )
