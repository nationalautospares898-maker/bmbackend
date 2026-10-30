"""Concurrency tests with **real parallel connections**.

`CLAUDE.md §10`: these use real threads against real connections with barriers — not
assertions that a lock statement appears in the source. A lock you can grep for is not a
lock you have tested.

The four properties under test are the ones that cost real money if they fail:

* **20 parallel finalisations on one series → 20 invoices, no gaps, no duplicates.**
* **Two counters racing for the last unit → one sale, one `INSUFFICIENT_STOCK`.**
* **Concurrent same-key submits → exactly one invoice** (AC-IDEM-3).
* **50 parallel multi-product sales → zero deadlocks**, which is what the fixed lock
  order buys.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from bm.domain.auth.permissions import Permission, SystemRole
from bm.domain.sales.service import (
    CustomerSelection,
    FinalizeCommand,
    PaymentCommand,
    SaleHeader,
    SaleLineCommand,
    SalesService,
)
from bm.domain.shared.clock import SystemClock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import DomainError
from tests.fixtures import Tenant, create_user

pytestmark = [pytest.mark.db, pytest.mark.concurrency]

D = Decimal


# --- fixtures -----------------------------------------------------------------


@pytest.fixture
def actor(migration_session: Session, tenant: Tenant) -> AuthContext:
    """A full-permission context, reused across threads.

    `AuthContext` is a frozen dataclass with no session attached, so sharing it between
    threads is safe — each thread opens its own session.
    """
    user = create_user(
        migration_session, tenant, username="race", role=SystemRole.OWNER, all_branches=True
    )
    return AuthContext(
        user_id=user.id,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id,
        device_id=user.device_id,
        role="owner",
        permissions=frozenset(str(p) for p in Permission),
        token_id="concurrency-test",
    )


@pytest.fixture
def seeded(migration_session: Session, tenant: Tenant) -> dict[str, Any]:
    """A product with 100 units, plus a second product for the deadlock probe.

    Inserted directly: this is reference data, and routing it through the purchase
    workflow would make the test about purchases rather than about concurrency.
    """
    unit_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO units (id, business_id, code, name, allows_fraction, decimal_places)"
            " VALUES (:id, :business_id, 'NOS', 'Numbers', false, 0)"
        ),
        {"id": unit_id, "business_id": tenant.business_id},
    )

    products: list[uuid.UUID] = []
    for index in range(2):
        product_id = uuid.uuid4()
        migration_session.execute(
            text(
                "INSERT INTO products"
                " (id, business_id, name, normalized_name, sku, normalized_sku, unit_id,"
                "  default_selling_price, search_text)"
                " VALUES (:id, :business_id, :name, :normalized, :sku, :nsku, :unit_id,"
                "         100, :search)"
            ),
            {
                "id": product_id,
                "business_id": tenant.business_id,
                "name": f"Race Part {index}",
                "normalized": f"race part {index}",
                "sku": f"RACE-{index}",
                "nsku": f"race{index}",
                "unit_id": unit_id,
                "search": f"race part {index} race-{index}",
            },
        )
        migration_session.execute(
            text(
                "INSERT INTO inventory_balances"
                " (id, business_id, branch_id, product_id, quantity)"
                " VALUES (gen_random_uuid(), :business_id, :branch_id, :product_id, 100)"
            ),
            {
                "business_id": tenant.business_id,
                "branch_id": tenant.branch_id,
                "product_id": product_id,
            },
        )
        # The matching opening movement. Without it the ledger would be missing its
        # first row, and `balance = Σ movements` (NFR-DATA-001) could not be asserted
        # at all — the fixture, not the service, would be the thing that broke it.
        migration_session.execute(
            text(
                "INSERT INTO stock_movements"
                " (id, business_id, branch_id, product_id, quantity_delta, balance_after,"
                "  reason, source_type, source_id, oversell_override, created_at)"
                " VALUES (gen_random_uuid(), :business_id, :branch_id, :product_id, 100, 100,"
                "         'opening', 'fixture', gen_random_uuid(), false, now())"
            ),
            {
                "business_id": tenant.business_id,
                "branch_id": tenant.branch_id,
                "product_id": product_id,
            },
        )
        products.append(product_id)
    migration_session.commit()
    return {"products": products, "unit_id": unit_id}


def _finalize(
    engine: Engine,
    ctx: AuthContext,
    tenant: Tenant,
    product_ids: list[uuid.UUID],
    *,
    quantity: str,
    key: uuid.UUID,
    barrier: Barrier | None = None,
) -> tuple[str, Any]:
    """Run one finalisation on its **own** session and connection.

    Returns `("ok", reference)` or `("error", code)` so a caller can assert on the
    distribution of outcomes rather than on exceptions crossing thread boundaries.
    """
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        # Quantised to the amount scale: `D("1.000") * 100` is `100.000`, and the API
        # correctly rejects a payment with more precision than `NUMERIC(14,2)` holds.
        total = (D(quantity) * D("100") * len(product_ids)).quantize(D("0.01"))
        command = FinalizeCommand(
            draft_id=None,
            draft_version=None,
            counter_id=tenant.counter_id,
            sale_type="cash",
            customer=CustomerSelection(mode="none"),
            header=SaleHeader(customer_name="Race"),
            lines=[
                SaleLineCommand(
                    product_id=product_id,
                    quantity=D(quantity),
                    unit_price=D("100.0000"),
                )
                for product_id in product_ids
            ],
            payment=PaymentCommand(method="cash", amount=total),
            client_computed_total=total,
        )
        raw = {
            "counter_id": str(tenant.counter_id),
            "sale_type": "cash",
            "lines": [
                {
                    "product_id": str(product_id),
                    "quantity": quantity,
                    "unit_price": "100.0000",
                }
                for product_id in product_ids
            ],
            "payment": {"method": "cash", "amount": format(total, "f")},
        }

        if barrier is not None:
            # Every thread waits here, so the contended section is entered as close to
            # simultaneously as the runtime allows.
            barrier.wait(timeout=30)

        result = SalesService(session, SystemClock()).finalize(
            ctx, command, idempotency_key=key, raw_body=raw
        )
        session.commit()
        return ("replay" if result.replayed else "ok", result.internal_reference)
    except DomainError as exc:
        session.rollback()
        return ("error", str(exc.code))
    except Exception as exc:  # noqa: BLE001 - surfaced to the assertion, not swallowed
        session.rollback()
        return ("exception", f"{type(exc).__name__}: {exc}")
    finally:
        session.close()


# --- the numbering probe ------------------------------------------------------


def test_twenty_parallel_finalisations_produce_twenty_unique_sequential_references(
    app_engine: Engine,
    migration_session: Session,
    actor: AuthContext,
    tenant: Tenant,
    seeded: dict[str, Any],
) -> None:
    """M9's exit gate.

    Twenty concurrent sales on one series must produce twenty invoices with sequence
    numbers `n…n+19` — no gaps, no duplicates. This works because `next_number` is
    taken under `FOR UPDATE` **inside** the transaction, so a rolled-back sale consumes
    nothing and two winners cannot read the same value.
    """
    product_id = seeded["products"][0]
    barrier = Barrier(20)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [
            pool.submit(
                _finalize,
                app_engine,
                actor,
                tenant,
                [product_id],
                quantity="1.000",
                key=uuid.uuid4(),
                barrier=barrier,
            )
            for _ in range(20)
        ]
        outcomes = [f.result() for f in as_completed(futures)]

    failures = [o for o in outcomes if o[0] not in {"ok"}]
    assert failures == [], f"every sale should have committed: {failures}"

    rows = (
        migration_session.execute(
            text(
                "SELECT sequence_number FROM invoices"
                " WHERE business_id = :business_id ORDER BY sequence_number"
            ),
            {"business_id": tenant.business_id},
        )
        .scalars()
        .all()
    )

    assert len(rows) == 20
    assert len(set(rows)) == 20, "no duplicate sequence numbers"
    assert rows == list(range(min(rows), min(rows) + 20)), "no gaps in committed numbers"

    # And `next_number` agrees with the highest committed sequence (T3-04).
    next_number = migration_session.execute(
        text("SELECT next_number FROM invoice_series WHERE business_id = :b"),
        {"b": tenant.business_id},
    ).scalar_one()
    assert next_number == max(rows) + 1

    # Stock moved exactly twenty times.
    assert migration_session.execute(
        text("SELECT quantity FROM inventory_balances WHERE product_id = :id"),
        {"id": product_id},
    ).scalar_one() == D("80.000")


def test_a_failed_sale_consumes_no_reference(
    app_engine: Engine,
    migration_session: Session,
    actor: AuthContext,
    tenant: Tenant,
    seeded: dict[str, Any],
) -> None:
    """The number is allocated **after** stock is verified, so a stock failure leaves
    `next_number` untouched (`Blueprint §5.1` property 5)."""
    product_id = seeded["products"][0]

    _finalize(app_engine, actor, tenant, [product_id], quantity="1.000", key=uuid.uuid4())
    before = migration_session.execute(
        text("SELECT next_number FROM invoice_series WHERE business_id = :b"),
        {"b": tenant.business_id},
    ).scalar_one()

    outcome = _finalize(
        app_engine, actor, tenant, [product_id], quantity="9999.000", key=uuid.uuid4()
    )
    assert outcome == ("error", "INSUFFICIENT_STOCK")

    after = migration_session.execute(
        text("SELECT next_number FROM invoice_series WHERE business_id = :b"),
        {"b": tenant.business_id},
    ).scalar_one()
    assert after == before


# --- the last-unit race -------------------------------------------------------


def test_two_counters_racing_for_the_last_unit_yield_one_sale(
    app_engine: Engine,
    migration_session: Session,
    actor: AuthContext,
    tenant: Tenant,
    seeded: dict[str, Any],
) -> None:
    """C-3 under contention: one sale commits, the other is refused with
    `available: 0`. Overselling is prevented by default, including in a race."""
    product_id = seeded["products"][0]
    migration_session.execute(
        text("UPDATE inventory_balances SET quantity = 1 WHERE product_id = :id"),
        {"id": product_id},
    )
    migration_session.commit()

    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                _finalize,
                app_engine,
                actor,
                tenant,
                [product_id],
                quantity="1.000",
                key=uuid.uuid4(),
                barrier=barrier,
            )
            for _ in range(2)
        ]
        outcomes = [f.result() for f in as_completed(futures)]

    statuses = sorted(o[0] for o in outcomes)
    assert statuses == ["error", "ok"], outcomes
    refused = next(o for o in outcomes if o[0] == "error")
    assert refused[1] == "INSUFFICIENT_STOCK"

    assert migration_session.execute(
        text("SELECT quantity FROM inventory_balances WHERE product_id = :id"),
        {"id": product_id},
    ).scalar_one() == D("0.000")


def test_ten_sales_against_stock_of_three_commit_exactly_three(
    app_engine: Engine,
    migration_session: Session,
    actor: AuthContext,
    tenant: Tenant,
    seeded: dict[str, Any],
) -> None:
    """The quantity sold can never exceed the quantity held, however many tills try."""
    product_id = seeded["products"][0]
    migration_session.execute(
        text("UPDATE inventory_balances SET quantity = 3 WHERE product_id = :id"),
        {"id": product_id},
    )
    migration_session.commit()

    barrier = Barrier(10)
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [
            pool.submit(
                _finalize,
                app_engine,
                actor,
                tenant,
                [product_id],
                quantity="1.000",
                key=uuid.uuid4(),
                barrier=barrier,
            )
            for _ in range(10)
        ]
        outcomes = [f.result() for f in as_completed(futures)]

    committed = [o for o in outcomes if o[0] == "ok"]
    refused = [o for o in outcomes if o[0] == "error"]
    assert len(committed) == 3, outcomes
    assert len(refused) == 7
    assert all(o[1] == "INSUFFICIENT_STOCK" for o in refused)

    assert migration_session.execute(
        text("SELECT quantity FROM inventory_balances WHERE product_id = :id"),
        {"id": product_id},
    ).scalar_one() == D("0.000")


# --- idempotency under concurrency -------------------------------------------


def test_concurrent_same_key_submits_create_exactly_one_invoice(
    app_engine: Engine,
    migration_session: Session,
    actor: AuthContext,
    tenant: Tenant,
    seeded: dict[str, Any],
) -> None:
    """AC-IDEM-3. The unique constraint on `(business_id, endpoint, key)` is the
    serialisation point: one insert wins, the losers re-read the row and either replay
    the stored response or report `IDEMPOTENT_REQUEST_IN_PROGRESS`."""
    product_id = seeded["products"][0]
    shared_key = uuid.uuid4()
    barrier = Barrier(5)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(
                _finalize,
                app_engine,
                actor,
                tenant,
                [product_id],
                quantity="1.000",
                key=shared_key,
                barrier=barrier,
            )
            for _ in range(5)
        ]
        outcomes = [f.result() for f in as_completed(futures)]

    committed = [o for o in outcomes if o[0] == "ok"]
    replayed = [o for o in outcomes if o[0] == "replay"]
    in_progress = [
        o for o in outcomes if o[0] == "error" and o[1] == "IDEMPOTENT_REQUEST_IN_PROGRESS"
    ]
    unexpected = [
        o for o in outcomes if o[0] == "exception" or (o[0] == "error" and o not in in_progress)
    ]

    assert unexpected == [], f"unexpected outcomes: {unexpected}"
    assert len(committed) == 1, f"exactly one sale may commit: {outcomes}"
    assert len(replayed) + len(in_progress) == 4

    assert (
        migration_session.execute(
            text("SELECT count(*) FROM invoices WHERE business_id = :b"),
            {"b": tenant.business_id},
        ).scalar_one()
        == 1
    )
    # Stock moved once, not five times.
    assert migration_session.execute(
        text("SELECT quantity FROM inventory_balances WHERE product_id = :id"),
        {"id": product_id},
    ).scalar_one() == D("99.000")


# --- the deadlock probe -------------------------------------------------------


def test_fifty_parallel_multi_product_sales_produce_no_deadlock(
    app_engine: Engine,
    migration_session: Session,
    actor: AuthContext,
    tenant: Tenant,
    seeded: dict[str, Any],
) -> None:
    """ADR-008's payoff.

    Half the threads send the products in one order and half in the reverse. Without a
    fixed lock order that is the textbook deadlock; with ascending `product_id` it is
    simply contention. Any deadlock surfaces as a `DeadlockDetected` exception, which
    this asserts the absence of.
    """
    first, second = seeded["products"]
    barrier = Barrier(50)

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [
            pool.submit(
                _finalize,
                app_engine,
                actor,
                tenant,
                # Deliberately opposite orders.
                [first, second] if index % 2 == 0 else [second, first],
                quantity="1.000",
                key=uuid.uuid4(),
                barrier=barrier,
            )
            for index in range(50)
        ]
        outcomes = [f.result() for f in as_completed(futures)]

    deadlocks = [o for o in outcomes if o[0] == "exception" and "Deadlock" in str(o[1])]
    assert deadlocks == [], f"the fixed lock order must make deadlock impossible: {deadlocks}"

    other_exceptions = [o for o in outcomes if o[0] == "exception"]
    assert other_exceptions == [], other_exceptions

    committed = [o for o in outcomes if o[0] == "ok"]
    assert len(committed) == 50, f"all 50 should commit with stock of 100: {outcomes}"

    # Both products down by exactly fifty.
    for product_id in (first, second):
        assert migration_session.execute(
            text("SELECT quantity FROM inventory_balances WHERE product_id = :id"),
            {"id": product_id},
        ).scalar_one() == D("50.000")


def test_the_ledger_sum_equals_the_balance_after_parallel_billing(
    app_engine: Engine,
    migration_session: Session,
    actor: AuthContext,
    tenant: Tenant,
    seeded: dict[str, Any],
) -> None:
    """NFR-DATA-001 under contention.

    `balance = Σ movements` is the invariant a single-writer service exists to protect.
    Checking it after twenty concurrent sales is the only way to know the single writer
    actually held.
    """
    product_id = seeded["products"][0]
    barrier = Barrier(20)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [
            pool.submit(
                _finalize,
                app_engine,
                actor,
                tenant,
                [product_id],
                quantity="2.000",
                key=uuid.uuid4(),
                barrier=barrier,
            )
            for _ in range(20)
        ]
        [f.result() for f in as_completed(futures)]

    row = migration_session.execute(
        text(
            "SELECT b.quantity AS balance,"
            "       (SELECT coalesce(sum(quantity_delta), 0) FROM stock_movements m"
            "         WHERE m.product_id = b.product_id AND m.branch_id = b.branch_id)"
            "         AS ledger"
            "  FROM inventory_balances b WHERE b.product_id = :id"
        ),
        {"id": product_id},
    ).one()
    assert row.balance == row.ledger, (
        f"balance {row.balance} must equal the ledger sum {row.ledger}"
    )


def test_concurrent_payments_against_one_invoice_cannot_over_allocate(
    app_engine: Engine,
    migration_session: Session,
    actor: AuthContext,
    tenant: Tenant,
    seeded: dict[str, Any],
) -> None:
    """NFR-DATA-004 under contention.

    Two payments racing for the same invoice: `amount_paid` must end up equal to the
    allocation sum, and the total allocated must never exceed the invoice.
    """
    product_id = seeded["products"][0]
    customer_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO customers (id, business_id, name, normalized_name)"
            " VALUES (:id, :business_id, 'Race Customer', 'race customer')"
        ),
        {"id": customer_id, "business_id": tenant.business_id},
    )
    migration_session.commit()

    # One credit invoice of 500.
    factory = sessionmaker(bind=app_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    command = FinalizeCommand(
        draft_id=None,
        draft_version=None,
        counter_id=tenant.counter_id,
        sale_type="credit",
        customer=CustomerSelection(mode="existing", customer_id=customer_id),
        header=SaleHeader(),
        lines=[
            SaleLineCommand(product_id=product_id, quantity=D("5.000"), unit_price=D("100.0000"))
        ],
        client_computed_total=D("500.00"),
    )
    result = SalesService(session, SystemClock()).finalize(
        actor,
        command,
        idempotency_key=uuid.uuid4(),
        raw_body={"sale_type": "credit", "total": "500.00"},
    )
    session.commit()
    session.close()
    invoice_id = result.invoice_id

    def pay(amount: str, barrier: Barrier) -> tuple[str, Any]:
        from bm.domain.credit.service import AllocationTarget, CreditService

        local = factory()
        try:
            barrier.wait(timeout=30)
            CreditService(local, SystemClock()).record_payment(
                actor,
                customer_id=customer_id,
                amount=amount,
                method="cash",
                strategy="invoice_specific",
                allocations=[AllocationTarget(invoice_id=invoice_id, amount=D(amount))],
            )
            local.commit()
            return ("ok", amount)
        except DomainError as exc:
            local.rollback()
            return ("error", str(exc.code))
        except Exception as exc:  # noqa: BLE001
            local.rollback()
            return ("exception", f"{type(exc).__name__}: {exc}")
        finally:
            local.close()

    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [
            f.result()
            for f in as_completed(
                [
                    pool.submit(pay, "400.00", barrier),
                    pool.submit(pay, "400.00", barrier),
                ]
            )
        ]

    assert [o for o in outcomes if o[0] == "exception"] == [], outcomes
    committed = [o for o in outcomes if o[0] == "ok"]
    assert len(committed) == 1, f"the second payment must be refused: {outcomes}"
    assert next(o for o in outcomes if o[0] == "error")[1] == "OVER_ALLOCATION"

    row = migration_session.execute(
        text(
            "SELECT i.amount_paid,"
            "       (SELECT coalesce(sum(a.amount), 0)::numeric(14,2)"
            "          FROM payment_allocations a WHERE a.invoice_id = i.id) AS allocated,"
            "       i.total"
            "  FROM invoices i WHERE i.id = :id"
        ),
        {"id": invoice_id},
    ).one()
    assert row.amount_paid == row.allocated, "NFR-DATA-003"
    assert row.allocated <= row.total, "NFR-DATA-004"
    assert row.amount_paid == D("400.00")
