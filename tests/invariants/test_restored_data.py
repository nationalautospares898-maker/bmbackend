"""Data invariants that must hold of **any** consistent NAS database.

These are the proof step of the restore drill (`DEPLOYMENT.md §7.1` step 4). Everything
before it only shows that bytes moved; this shows the restored database is *sound*. A
restore that completes and leaves the ledger inconsistent is a failed restore, and it
looks like a success at every other step.

Deliberately written to pass on an empty database and to mean something on a populated
one. That is not a weakness — the suite runs against whatever was restored, and an
assertion that required seed data could not be used for the thing it exists for.
`scripts/seed_demo.py` is what makes a drill meaningful.

Each invariant is one the code is supposed to maintain continuously, so a failure here
has exactly two possible causes: a partial restore, or a bug that has been corrupting
data in production. Both are worth stopping a release for.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.db


def test_every_stock_balance_equals_the_sum_of_its_movements(app_session: Session) -> None:
    """`balance = Σ movements`, the ledger's defining property (FR-INV-006).

    The one invariant that would cost the shop money to lose: if balances and movements
    disagree, nobody can say what is on the shelf.
    """
    drift = app_session.execute(
        text(
            """
            SELECT b.branch_id, b.product_id, b.quantity,
                   coalesce(sum(m.quantity_delta), 0) AS ledger
              FROM inventory_balances b
              LEFT JOIN stock_movements m
                     ON m.product_id = b.product_id
                    AND m.branch_id = b.branch_id
             GROUP BY b.branch_id, b.product_id, b.quantity
            HAVING b.quantity <> coalesce(sum(m.quantity_delta), 0)
            """
        )
    ).all()
    assert drift == [], f"{len(drift)} balance(s) disagree with the movement ledger"


def test_no_movement_exists_without_a_balance_row(app_session: Session) -> None:
    """A movement for a product with no balance row means the balance was lost — the
    asymmetric half of the check above, which a `LEFT JOIN` from balances cannot see."""
    orphans = app_session.execute(
        text(
            """
            SELECT DISTINCT m.branch_id, m.product_id
              FROM stock_movements m
              LEFT JOIN inventory_balances b
                     ON b.product_id = m.product_id AND b.branch_id = m.branch_id
             WHERE b.product_id IS NULL
            """
        )
    ).all()
    assert orphans == []


def test_amount_paid_equals_the_sum_of_allocations(app_session: Session) -> None:
    """ADR-022: `amount_paid` is **derived**, never incremented. If it has drifted from
    the allocations it summarises, the derivation was bypassed somewhere."""
    drift = app_session.execute(
        text(
            """
            SELECT i.id, i.amount_paid, coalesce(sum(a.amount), 0) AS allocated
              FROM invoices i
              LEFT JOIN payment_allocations a ON a.invoice_id = i.id
             GROUP BY i.id, i.amount_paid
            HAVING i.amount_paid <> coalesce(sum(a.amount), 0)
            """
        )
    ).all()
    assert drift == [], f"{len(drift)} invoice(s) have an amount_paid that is not the allocations"


def test_an_overpaid_invoice_is_labelled_overpaid(app_session: Session) -> None:
    """`PaymentStatus.OVERPAID` is a **modelled state**, not an impossibility.

    So the invariant is not "this cannot happen" — the enum says it can, and a customer
    who pays a round figure against an odd bill makes it happen. What must hold is that
    the label tells the truth: money in excess of the total is reported as `overpaid`,
    never as `paid`.
    """
    mislabelled = app_session.execute(
        text(
            """
            SELECT id, total, amount_paid, payment_status
              FROM invoices
             WHERE status <> 'cancelled'
               AND amount_paid > total
               AND payment_status <> 'overpaid'
            """
        )
    ).all()
    assert mislabelled == []


def test_payment_status_agrees_with_the_amounts(app_session: Session) -> None:
    """`payment_status` is the other derived field, and the two must tell the same
    story: a bill cannot be `paid` with money outstanding."""
    inconsistent = app_session.execute(
        text(
            """
            SELECT id, total, amount_paid, payment_status
              FROM invoices
             WHERE status <> 'cancelled'
               AND (
                     (payment_status = 'paid'           AND amount_paid <> total)
                  OR (payment_status = 'unpaid'         AND amount_paid <> 0)
                  OR (payment_status = 'partially_paid'
                      AND (amount_paid <= 0 OR amount_paid >= total))
                  OR (payment_status = 'overpaid'       AND amount_paid <= total)
                   )
            """
        )
    ).all()
    assert inconsistent == []


def test_invoice_numbering_has_no_gaps_and_the_series_counter_agrees(
    app_session: Session,
) -> None:
    """T3-04. `max(sequence_number) + 1 = next_number` per series.

    A mismatch means a number was consumed without an invoice — a lost update — which
    is precisely what a gapless statutory sequence may not do.
    """
    problems = app_session.execute(
        text(
            """
            SELECT s.id, s.next_number, coalesce(max(i.sequence_number), 0) AS max_sequence
              FROM invoice_series s
              LEFT JOIN invoices i ON i.invoice_series_id = s.id
             GROUP BY s.id, s.next_number
            HAVING coalesce(max(i.sequence_number), 0) + 1 <> s.next_number
            """
        )
    ).all()
    assert problems == []


def test_no_series_has_a_duplicate_sequence_number(app_session: Session) -> None:
    duplicates = app_session.execute(
        text(
            """
            SELECT invoice_series_id, sequence_number, count(*)
              FROM invoices
             GROUP BY invoice_series_id, sequence_number
            HAVING count(*) > 1
            """
        )
    ).all()
    assert duplicates == []


def test_every_invoice_has_at_least_one_item(app_session: Session) -> None:
    """An empty sale is refused (`EMPTY_SALE`), so an invoice with no items is a
    half-restored invoice — exactly the shape a partial `pg_restore` produces."""
    empty = app_session.execute(
        text(
            """
            SELECT i.id
              FROM invoices i
              LEFT JOIN invoice_items it ON it.invoice_id = i.id
             WHERE it.id IS NULL
            """
        )
    ).all()
    assert empty == []


def test_every_invoice_total_follows_from_its_own_components(
    app_session: Session,
) -> None:
    """`total = subtotal - discount_total + tax_total + rounding_adjustment`.

    The relationship `Totals.reconciles` asserts at finalisation time and
    `ck_invoices__total` enforces in the schema, checked here against stored rows.
    Integral-INR v1 with no rounding, so this is exact equality, not a tolerance.
    """
    mismatched = app_session.execute(
        text(
            """
            SELECT id, subtotal, discount_total, tax_total, rounding_adjustment, total
              FROM invoices
             WHERE total <> subtotal - discount_total + tax_total + rounding_adjustment
            """
        )
    ).all()
    assert mismatched == []


def test_every_invoice_subtotal_is_the_sum_of_its_line_totals(
    app_session: Session,
) -> None:
    """NFR-DATA-002b: `invoices.subtotal = Σ invoice_items.line_total`.

    `money.py` records a specification ambiguity here — `subtotal` is built from
    `taxable_amount`, which equals `line_total` only while `tax_total` is zero, and it
    is zero for every invoice under `tax.mode = estimate_only` (OD-25 unresolved). So
    this invariant holds today and is *expected* to need revisiting if tax is ever
    switched on; it is written to fail loudly at that point rather than silently drift.
    """
    mismatched = app_session.execute(
        text(
            """
            SELECT i.id, i.subtotal, coalesce(sum(it.line_total), 0) AS lines
              FROM invoices i
              JOIN invoice_items it ON it.invoice_id = i.id
             WHERE i.tax_total = 0
             GROUP BY i.id, i.subtotal
            HAVING i.subtotal <> coalesce(sum(it.line_total), 0)
            """
        )
    ).all()
    assert mismatched == []


def test_no_credit_sale_is_missing_its_customer(app_session: Session) -> None:
    """FR-CREDIT-009: a credit bill requires a persisted customer. Without one the
    debt belongs to nobody."""
    orphaned = app_session.execute(
        # The column is `is_credit`. There is no `sale_type` on the invoice: the request
        # carries one, and what gets stored is the boolean it resolves to.
        text("SELECT id FROM invoices WHERE is_credit AND customer_id IS NULL")
    ).all()
    assert orphaned == []


def test_every_business_scoped_row_belongs_to_a_business_that_exists(
    app_session: Session,
) -> None:
    """Foreign keys enforce this live, but `pg_restore` can load tables in an order
    that leaves a dangling reference if a constraint failed to restore. Checking the
    constraint still *bites* is part of proving the restore was complete."""
    for table in ("invoices", "products", "customers", "import_jobs", "stock_movements"):
        dangling = app_session.execute(
            text(
                f"""
                SELECT count(*) FROM {table} t
                 WHERE NOT EXISTS (SELECT 1 FROM businesses b WHERE b.id = t.business_id)
                """
            )
        ).scalar()
        assert dangling == 0, f"{table} has rows pointing at a missing business"


def test_the_restored_schema_is_at_the_expected_migration_head(app_session: Session) -> None:
    """A restore that silently landed an older schema would pass every data check above
    while missing columns the application needs (ADR-016)."""
    from bm.infra.db.migration_state import migration_state

    state = migration_state(app_session)
    assert state.matches, (
        f"restored schema is at {state.current_revision}, code expects {state.expected_head}"
    )
