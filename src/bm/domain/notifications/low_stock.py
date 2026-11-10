"""Low-stock detection (Feature A.3).

A periodic scan, not a hook on every stock-affecting endpoint: the owner wants to know
promptly, not instantly, and a scan is one place to get the comparison right rather than
duplicating `InventoryBalance.quantity < Product.reorder_level` (the exact rule
`InventoryService.balances(low_stock_only=True)` already uses) at every call site that
can move stock — billing, purchases, returns, the stock sheet, the inline +/- control.

Idempotent via `notifications.notify_once`: a product already carrying an open
`low_stock` alert is left alone, so a scan every five minutes does not restate the same
fact every five minutes. A product that has recovered above its reorder level has its
open alert resolved, which is what lets a *later* dip raise a fresh one rather than
being silently absorbed by an alert the owner dismissed weeks ago.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.notifications import service as notifications
from bm.infra.db.enums import NotificationKind, NotificationSeverity
from bm.infra.db.rows import rows_affected


def scan_business(session: Session, business_id: uuid.UUID) -> dict[str, int]:
    """Raise/resolve low-stock alerts for one business, across every branch.

    Not scoped through `InventoryService.balances` because that requires a selected
    branch (`ctx.requires_branch`) and this runs business-wide, the same way
    `InventoryService.reconcile` does.
    """
    low_rows = session.execute(
        text(
            """
            SELECT ib.product_id, p.name, ib.quantity, p.reorder_level
              FROM inventory_balances ib
              JOIN products p ON p.id = ib.product_id
             WHERE ib.business_id = :business_id
               AND p.reorder_level IS NOT NULL
               AND ib.quantity < p.reorder_level
            """
        ),
        {"business_id": business_id},
    ).all()

    raised = 0
    for row in low_rows:
        severity = (
            NotificationSeverity.CRITICAL if row.quantity <= 0 else NotificationSeverity.WARNING
        )
        created = notifications.notify_once(
            session,
            business_id=business_id,
            kind=NotificationKind.LOW_STOCK,
            severity=severity,
            title=f"{row.name} is running low",
            body=(
                f"{row.quantity} left, below the minimum of {row.reorder_level}."
                if row.quantity > 0
                else f"Out of stock (minimum is {row.reorder_level})."
            ),
            entity_type="product",
            entity_id=row.product_id,
        )
        if created is not None:
            raised += 1

    # Resolve any open alert for a product that is no longer low. `NOT IN` over the
    # just-computed low set, scoped to this business and this kind, so a product that
    # was never low and has no alert is untouched.
    low_ids = [row.product_id for row in low_rows]
    resolved = session.execute(
        text(
            """
            UPDATE notifications
               SET status = 'resolved', status_changed_at = now()
             WHERE business_id = :business_id
               AND kind = 'low_stock'
               AND status = 'active'
               AND NOT (entity_id = ANY(:low_ids))
            """
        ),
        {"business_id": business_id, "low_ids": low_ids or [uuid.UUID(int=0)]},
    )
    return {"raised": raised, "resolved": rows_affected(resolved)}
