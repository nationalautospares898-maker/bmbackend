"""A per-order custom reminder (owner request, 2026-11-11).

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-11

The existing ten-minute nudge (`orders.nudge`, migration `0025`) is a flat cadence: every
pending order resurfaces every ten minutes, no matter what it is about. The owner asked
for something more specific — "remind me about *this* order tomorrow at 3pm" — which the
flat cadence cannot express.

`remind_at`, nullable, is that one-time request. `orders.nudge` (see
`domain/orders/service.py`) treats it as **due now** the instant it is reached, ahead of
and independent of the ten-minute cadence, and **clears it back to `NULL`** once it has
fired — a one-time request should not keep firing every ten minutes forever after the
moment the owner actually asked for. The flat cadence resumes immediately afterwards,
exactly as if the order had just been nudged normally, because the column also updates
`last_reminder_at` on that same firing.

No index: `orders.nudge` only ever looks at `pending` orders, already narrowed by
`ix_orders__pending`, and a shop with enough simultaneous pending orders for a sequential
scan of `remind_at` to matter does not exist.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "remind_at")
