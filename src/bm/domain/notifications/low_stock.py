"""Low-stock detection (Feature A.3).

## Two entry points, one rule

`sync_for_products` is the **primary** detector, and it is event-driven: it is called by
`InventoryService.apply_movements` — the single code path that is allowed to write
`inventory_balances` or `stock_movements` — inside the same transaction as the movement
itself. So a balance that crosses its reorder level raises a notification at the instant
it crosses, on *every* route that can move stock (sale finalisation, stock entry,
purchase receipt, return, cancellation, amendment, adjustment, stock count), and a sale
that rolls back takes its notification with it.

Hooking the one writer rather than the eight callers is deliberate. The audit of
2026-11-22 found exactly the drift that per-call-site detection produces: the periodic
scan compared `quantity < reorder_level` while the sale path compared
`remaining <= reorder_level`, so a product landing exactly on its minimum was alerted by
a sale and then un-alerted by the next scan, with nothing having changed.

`scan_business` is the **reconciliation** — the same rule applied to a whole business,
now run once a day rather than every five minutes (`infra/queue/scheduler.py`). It is a
safety net, not a generator: because it raises through `notify_once` and resolves only
products that are genuinely no longer low, a product already correctly represented
produces no row and no change. It exists to repair what event-driven detection could
have missed — a balance changed by a path that bypassed `apply_movements` (there is none
today, and a structural test keeps it that way), or a notification lost to a partial
failure.

## The threshold: `<=`, and why the inconsistency was resolved in this direction

Both entry points use **`quantity <= reorder_level`** — at or below.

That is the rule notifications were *already* raised under. The sale path
(`SalesService._low_stock_warnings`) used `<=`, documented as deliberate: "a part that
has just landed exactly on its minimum warns — which is the useful moment to say
'reorder this'", and `LOW_STOCK_AFTER_SALE` is specified as firing when stock "fell **to
or** below" the level (`API-SPECIFICATION.md §4.3`). `reorder_level` means *reorder when
you reach this*, so reaching it is the moment that matters.

The periodic scan used `<` instead, and that mismatch is what produced the defect: a part
sold down to exactly its minimum was alerted by the sale and then silently **un-alerted**
by the next scan five minutes later, with nothing having changed. Nobody agreed to that —
it was an accident of two call sites computing their own rule.

It is resolved by moving the scan onto the notification rule rather than the reverse,
because the alternative direction would mean telling the owner about *fewer* parts than
before, which is not a change a bug fix gets to make.

## One rule, everywhere (owner decision, 2026-11-22)

There is no longer a second threshold anywhere in the application. The listing surfaces
used to compare `<`, which left a part sitting exactly on its minimum raising a bell
alert while the Stock screen refused to list it. The owner settled it: **at the minimum
is low**, and every comparison now reads `<=`.

The six implementations, which move together or not at all:

| Where | What it decides |
|---|---|
| `CatalogService.ProductView.is_low_stock` | a product's own badge — **the authority; its docstring is the record** |
| `InventoryService.balances(low_stock_only=True)` | the Stock screen's "Low stock only" filter |
| `ReportService.stock_position` | the stock report |
| `sync_for_products` / `scan_business` (here) | which products carry a notification |
| `SalesService._low_stock_warnings` | `LOW_STOCK_AFTER_SALE` — always was `<=`, unchanged |
| `bmfrontend/src/features/stock/lowStock.ts::isLow` | the client-side badge |

`tests/api/test_low_stock_threshold.py` asserts all of them agree on 9/10, 10/10 and
11/10 through the real endpoints, so a future change to one of the six fails the build
rather than reintroducing the split.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.notifications import service as notifications
from bm.infra.db.enums import NotificationKind, NotificationSeverity
from bm.infra.db.rows import rows_affected

#: The low rows among a given set of products, business-wide.
#:
#: Business-wide rather than branch-scoped because the notification's `entity_id` is a
#: **product**, so one open alert exists per product however many branches hold it —
#: which is the same thing `scan_business` has always done, and is what lets the two
#: entry points dedup against each other instead of fighting over the slot.
_LOW_ROWS_SQL = text(
    """
    SELECT ib.product_id, p.name, ib.quantity, p.reorder_level
      FROM inventory_balances ib
      JOIN products p ON p.id = ib.product_id
     WHERE ib.business_id = :business_id
       AND ib.product_id = ANY(:product_ids)
       AND p.reorder_level IS NOT NULL
       AND ib.quantity <= p.reorder_level
     ORDER BY ib.quantity ASC
    """
)


def _title_and_body(name: str, quantity: Decimal, reorder_level: Decimal) -> tuple[str, str]:
    """The owner-facing wording. Shared so the event path and the reconciliation can
    never describe the same condition differently.

    Three phrasings, because the rule is `<=` and "below the minimum" would be a plain
    falsehood for the boundary case: a part with 10 on the shelf and a minimum of 10 is
    *at* its minimum, and telling the owner it is below 10 when the screen beside it says
    10 is the kind of small wrongness that makes people stop trusting the figure.
    """
    title = f"{name} is running low"
    if quantity <= 0:
        body = f"Out of stock (minimum is {reorder_level})."
    elif quantity == reorder_level:
        body = f"{quantity} left — down to the minimum of {reorder_level}."
    else:
        body = f"{quantity} left, below the minimum of {reorder_level}."
    return title, body


def _severity(quantity: Decimal) -> NotificationSeverity:
    return NotificationSeverity.CRITICAL if quantity <= 0 else NotificationSeverity.WARNING


def sync_for_products(
    session: Session, business_id: uuid.UUID, product_ids: Sequence[uuid.UUID]
) -> dict[str, int]:
    """Raise or resolve the low-stock alert for exactly the products just touched.

    Called from inside `InventoryService.apply_movements`, in the caller's transaction.
    One query for the whole set, not one per product: this sits on the billing hot path
    and a sale can carry fifteen lines.

    * A product now below its minimum gets an alert, unless one is already open for it
      (`notify_once`, backed by `uq_notifications__active_per_entity`). Stock crossing
      the threshold repeatedly cannot therefore produce a pile of alerts.
    * A product **no longer** below its minimum has its open alert resolved — which is
      what frees the dedup slot so a *later* dip raises a genuinely new alert rather
      than being absorbed by a stale one.

    Returns `{"raised": n, "resolved": n}` for the caller's log.
    """
    unique_ids = list(dict.fromkeys(product_ids))
    if not unique_ids:
        return {"raised": 0, "resolved": 0}

    low_rows = session.execute(
        _LOW_ROWS_SQL, {"business_id": business_id, "product_ids": unique_ids}
    ).all()

    raised = 0
    seen: set[uuid.UUID] = set()
    for row in low_rows:
        # `ORDER BY quantity ASC` plus this guard means a multi-branch product is
        # described by its *worst* branch, which is the figure worth acting on.
        if row.product_id in seen:
            continue
        seen.add(row.product_id)
        title, body = _title_and_body(row.name, row.quantity, row.reorder_level)
        if (
            notifications.notify_once(
                session,
                business_id=business_id,
                kind=NotificationKind.LOW_STOCK,
                severity=_severity(row.quantity),
                title=title,
                body=body,
                entity_type="product",
                entity_id=row.product_id,
            )
            is not None
        ):
            raised += 1

    # Every touched product that did **not** come back low is, by definition, at or
    # above its minimum now (or has no minimum set) — so any open alert for it is stale.
    # Scoped to the touched products only: this must never resolve an alert for a
    # product this movement had nothing to do with, which is the difference between
    # this and the reconciliation sweep below.
    recovered = [pid for pid in unique_ids if pid not in seen]
    resolved = 0
    if recovered:
        result = session.execute(
            text(
                """
                UPDATE notifications
                   SET status = 'resolved', status_changed_at = now()
                 WHERE business_id = :business_id
                   AND kind = 'low_stock'
                   AND status = 'active'
                   AND entity_id = ANY(:product_ids)
                """
            ),
            {"business_id": business_id, "product_ids": recovered},
        )
        resolved = rows_affected(result)

    return {"raised": raised, "resolved": resolved}


def scan_business(session: Session, business_id: uuid.UUID) -> dict[str, int]:
    """Reconcile every low-stock alert for one business, across every branch.

    The daily safety net, and the query behind the 5 PM summary's count. Not scoped
    through `InventoryService.balances` because that requires a selected branch
    (`ctx.requires_branch`) and this runs business-wide, the same way
    `InventoryService.reconcile` does.

    Idempotent, which is what makes it safe to run on a schedule and safe to retry: a
    product already carrying an open alert is left alone, and a product that is no
    longer low has its alert resolved whether or not this run is the one that noticed.
    """
    low_rows = session.execute(
        text(
            """
            SELECT ib.product_id, p.name, ib.quantity, p.reorder_level
              FROM inventory_balances ib
              JOIN products p ON p.id = ib.product_id
             WHERE ib.business_id = :business_id
               AND p.reorder_level IS NOT NULL
               AND ib.quantity <= p.reorder_level
             ORDER BY ib.quantity ASC
            """
        ),
        {"business_id": business_id},
    ).all()

    raised = 0
    seen: set[uuid.UUID] = set()
    for row in low_rows:
        if row.product_id in seen:
            continue
        seen.add(row.product_id)
        title, body = _title_and_body(row.name, row.quantity, row.reorder_level)
        created = notifications.notify_once(
            session,
            business_id=business_id,
            kind=NotificationKind.LOW_STOCK,
            severity=_severity(row.quantity),
            title=title,
            body=body,
            entity_type="product",
            entity_id=row.product_id,
        )
        if created is not None:
            raised += 1

    # Resolve any open alert for a product that is no longer low. `NOT IN` over the
    # just-computed low set, scoped to this business and this kind, so a product that
    # was never low and has no alert is untouched.
    low_ids = list(seen)
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


def active_low_stock_count(session: Session, business_id: uuid.UUID) -> int:
    """How many products currently carry an open low-stock alert.

    The 5 PM summary's figure. Reads the notification state rather than recomputing the
    balances, so the summary can never claim a different number from the bell sitting
    next to it.
    """
    return int(
        session.execute(
            text(
                "SELECT count(*) FROM notifications"
                " WHERE business_id = :business_id AND kind = 'low_stock' AND status = 'active'"
            ),
            {"business_id": business_id},
        ).scalar_one()
    )
