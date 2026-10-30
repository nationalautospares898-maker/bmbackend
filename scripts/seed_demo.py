"""Seed a realistic dataset for load testing and restore drills.

**Not production data, and not fixtures either.** Part codes are generated in the shape
the shop actually uses (`TT-1000-A`, not `PRODUCT-00001`), so search ranking is exercised
the way it will be at the counter — prefix collisions, repeated nouns, shared makes.

Scope, stated plainly: this seeds **the catalogue, its stock and its customers**. It does
not seed sales. Invoices have to come from `SalesService.finalize` to be consistent —
numbering, locks, movements, allocations — and a bulk `INSERT` of invoices would satisfy
the schema while violating most of `tests/invariants/test_restored_data.py`. The load
test creates its sales through the real engine for exactly that reason, and what it
creates is what a restore drill then verifies.

Stock is seeded as a balance row **and** a matching `opening` movement, because
`balance = Σ movements` is an invariant, and a dataset that fails it would make a restore
drill meaningless.

Nothing here invents business identity. There is no shop name, address, phone or tax
identifier, because OD-4 is unresolved and a plausible placeholder in a seeded database
is how a placeholder ends up on a printed bill. The business is called
"SEED DATA — NOT A REAL BUSINESS" so it can never be mistaken for the shop.

Usage:
    uv run python scripts/seed_demo.py --products 100000 --customers 200
    uv run python scripts/seed_demo.py --reset-only          # remove seeded data

The 100 000-product figure is the one NFR-PERF-001 names.
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

#: Part-code shapes taken from the workbook, so search ranking is exercised the way it
#: will be at the counter rather than against `PRODUCT-00001`.
_PREFIXES = ("TT", "MB", "HF", "CP", "BR", "SK", "GL", "AX", "RD", "FL")
_NOUNS = (
    "Tie Rod End",
    "Clutch Plate",
    "Oil Filter",
    "Brake Shoe Set",
    "Bearing",
    "Gasket Head",
    "Shock Absorber",
    "Wheel Cylinder",
    "Air Filter",
    "Fan Belt",
)
_MAKES = ("Bolero", "Scorpio", "Tata 407", "Ace", "Bullet", "Pulsar", "Activa", "Alto")

#: Seeded so two runs produce the same catalogue — a load-test comparison between two
#: commits is meaningless if the data underneath changed.
_RNG = random.Random(20261030)


def _database_url() -> str:
    import os

    url = os.environ.get("BM_MIGRATION_DATABASE_URL") or os.environ.get("BM_DATABASE_URL")
    if not url:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("BM_MIGRATION_DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
                    break
    if not url:
        raise SystemExit("set BM_MIGRATION_DATABASE_URL or BM_DATABASE_URL")
    return url


def _normalize_sku(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _normalize_name(value: str) -> str:
    import re

    return re.sub(r"\s+", " ", value.lower()).strip()


def reset(session: Session) -> None:
    """Remove seeded data. Only ever the seeded business."""
    business = session.execute(
        text("SELECT id FROM businesses WHERE display_name = :n"),
        {"n": SEED_BUSINESS_NAME},
    ).scalar_one_or_none()
    if business is None:
        return
    # Child-to-parent. This ordering is **taken from `tests/fixtures.py`**, which the
    # whole test suite exercises on every run — so it is known correct rather than
    # reasoned about here. Every FK into a financial row is RESTRICT, so deleting a
    # parent first fails rather than cascading; an incomplete list is what made the
    # first version of this function break once the load test created invoices.
    for table in (
        "audit_logs",
        "sales_return_items",
        "sales_returns",
        "credit_note_allocations",
        "payment_allocations",
        "credit_notes",
        "payments",
        "print_log",
        "invoice_items",
        "draft_bill_items",
        "draft_bills",
        "invoices",
        "invoice_series",
        "import_rows",
        "import_jobs",
        "purchase_items",
        "purchases",
        "suppliers",
        "stock_count_items",
        "stock_count_sessions",
        "stock_movements",
        "inventory_balances",
        "reconciliation_runs",
        "customer_credit_balances",
        "customer_vehicles",
        "customer_phones",
        "customers",
        "product_prices",
        "product_aliases",
        "products",
        "tax_rates",
        "tax_groups",
        "tax_config_versions",
        "brands",
        "categories",
        "units",
        "sync_operations",
        "outbox_events",
        "notifications",
        "idempotency_keys",
        "login_attempts",
        "refresh_tokens",
        "memberships",
        "devices",
        "settings",
        "counters",
        "users",
        "branches",
    ):
        session.execute(
            text(f"DELETE FROM {table} WHERE business_id = :b"),
            {"b": business},
        )
    session.execute(text("DELETE FROM businesses WHERE id = :b"), {"b": business})
    session.commit()
    print(f"reset: removed the seeded business {business}")


#: A name that could never be mistaken for the real shop (OD-4 is unresolved).
SEED_BUSINESS_NAME = "SEED DATA — NOT A REAL BUSINESS"


def seed_tenant(
    session: Session,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    business_id, branch_id, counter_id, user_id = (uuid.uuid4() for _ in range(4))
    session.execute(
        text(
            "INSERT INTO businesses (id, legal_name, display_name, timezone,"
            " financial_year_start_month)"
            " VALUES (:id, :name, :name, 'Asia/Kolkata', 4)"
        ),
        {"id": business_id, "name": SEED_BUSINESS_NAME},
    )
    session.execute(
        text(
            "INSERT INTO branches (id, business_id, code, name, is_active)"
            " VALUES (:id, :business_id, 'SEED', 'Seed Branch', true)"
        ),
        {"id": branch_id, "business_id": business_id},
    )
    session.execute(
        text(
            "INSERT INTO counters (id, business_id, branch_id, code, name, is_active,"
            " max_open_drafts)"
            " VALUES (:id, :business_id, :branch_id, 'S1', 'Seed Counter', true, 20)"
        ),
        {"id": counter_id, "business_id": business_id, "branch_id": branch_id},
    )
    session.execute(
        text(
            "INSERT INTO users (id, business_id, username, full_name, password_hash,"
            " is_active, must_change_password)"
            " VALUES (:id, :business_id, 'seed-operator', 'Seed Operator',"
            "         'not-a-usable-hash', true, false)"
        ),
        {"id": user_id, "business_id": business_id},
    )
    device_id = uuid.uuid4()
    # Drafts and invoices reference the device that created them, so a seeded tenant
    # needs one registered: without it every draft insert fails its foreign key.
    session.execute(
        text(
            "INSERT INTO devices (id, business_id, branch_id, counter_id, status, label)"
            " VALUES (:id, :business_id, :branch_id, :counter_id, 'active', 'Seed Device')"
        ),
        {
            "id": device_id,
            "business_id": business_id,
            "branch_id": branch_id,
            "counter_id": counter_id,
        },
    )
    session.commit()
    print(f"tenant: business={business_id} branch={branch_id}")
    return business_id, branch_id, counter_id, user_id, device_id


def seed_units(session: Session, business_id: uuid.UUID) -> uuid.UUID:
    unit_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO units (id, business_id, code, name, allows_fraction, decimal_places)"
            " VALUES (:id, :business_id, 'NOS', 'Numbers', false, 0)"
        ),
        {"id": unit_id, "business_id": business_id},
    )
    session.commit()
    return unit_id


def seed_products(
    session: Session,
    business_id: uuid.UUID,
    branch_id: uuid.UUID,
    unit_id: uuid.UUID,
    *,
    count: int,
    batch: int = 2000,
) -> list[uuid.UUID]:
    """Insert `count` products with stock, in batches.

    Stock arrives as an `opening` movement **and** a balance row, together, because
    `balance = Σ movements` is an invariant and seeding only one side would leave the
    dataset failing the very check a restore drill exists to run.
    """
    ids: list[uuid.UUID] = []
    products: list[dict[str, object]] = []
    balances: list[dict[str, object]] = []
    movements: list[dict[str, object]] = []

    for index in range(count):
        prefix = _PREFIXES[index % len(_PREFIXES)]
        sku = f"{prefix}-{1000 + index}-{_RNG.choice('ABCDEFGH')}"
        name = f"{_NOUNS[index % len(_NOUNS)]} {_MAKES[index % len(_MAKES)]}"
        product_id = uuid.uuid4()
        cost = Decimal(_RNG.randrange(40, 4000))
        price = (cost * Decimal("1.35")).quantize(Decimal("1"))
        quantity = Decimal(_RNG.randrange(5, 400))

        ids.append(product_id)
        products.append(
            {
                "id": product_id,
                "business_id": business_id,
                "name": name,
                "normalized_name": _normalize_name(name),
                "sku": sku,
                "normalized_sku": _normalize_sku(sku),
                "unit_id": unit_id,
                "price": price,
                "cost": cost,
                "search_text": f"{name} {sku}".lower(),
                "sales_rank": _RNG.randrange(0, 500),
            }
        )
        balances.append(
            {
                "id": uuid.uuid4(),
                "business_id": business_id,
                "branch_id": branch_id,
                "product_id": product_id,
                "quantity": quantity,
            }
        )
        movements.append(
            {
                "id": uuid.uuid4(),
                "business_id": business_id,
                "branch_id": branch_id,
                "product_id": product_id,
                "quantity": quantity,
                "balance_after": quantity,
                # `source_id` is NOT NULL: every movement must point at the thing that
                # caused it. For an opening balance the cause is the product's own
                # arrival, so the product id is the honest answer.
                "source_id": product_id,
            }
        )

        if len(products) >= batch or index == count - 1:
            _flush(session, products, balances, movements)
            print(f"  products: {index + 1}/{count}", end="\r", flush=True)
            products, balances, movements = [], [], []

    print(f"\nproducts: {count} seeded with stock")
    return ids


def _flush(
    session: Session,
    products: list[dict[str, object]],
    balances: list[dict[str, object]],
    movements: list[dict[str, object]],
) -> None:
    session.execute(
        text(
            "INSERT INTO products (id, business_id, name, normalized_name, sku,"
            " normalized_sku, unit_id, default_selling_price, last_cost_price, search_text,"
            " sales_rank, is_active)"
            " VALUES (:id, :business_id, :name, :normalized_name, :sku, :normalized_sku,"
            "         :unit_id, :price, :cost, :search_text, :sales_rank, true)"
        ),
        products,
    )
    session.execute(
        text(
            "INSERT INTO inventory_balances (id, business_id, branch_id, product_id, quantity)"
            " VALUES (:id, :business_id, :branch_id, :product_id, :quantity)"
        ),
        balances,
    )
    session.execute(
        text(
            "INSERT INTO stock_movements (id, business_id, branch_id, product_id,"
            " quantity_delta, balance_after, reason, source_type, source_id)"
            " VALUES (:id, :business_id, :branch_id, :product_id, :quantity, :balance_after,"
            "         'opening', 'seed', :source_id)"
        ),
        movements,
    )
    session.commit()


def seed_customers(session: Session, business_id: uuid.UUID, *, count: int) -> list[uuid.UUID]:
    rows = []
    ids = []
    for index in range(count):
        customer_id = uuid.uuid4()
        ids.append(customer_id)
        name = f"Seed Customer {index + 1}"
        rows.append(
            {
                "id": customer_id,
                "business_id": business_id,
                "name": name,
                "normalized_name": _normalize_name(name),
            }
        )
    session.execute(
        text(
            # Phones live in `customer_phones`, not on the customer row, so a
            # customer with three numbers is three rows rather than three columns.
            # Seeded customers carry none: a phone is what the duplicate detector keys
            # on (FR-CUST-004), and inventing 200 of them would seed 200 fake identities.
            "INSERT INTO customers (id, business_id, name, normalized_name, is_active)"
            " VALUES (:id, :business_id, :name, :normalized_name, true)"
        ),
        rows,
    )
    session.commit()
    print(f"customers: {count} seeded")
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=int, default=100_000)
    parser.add_argument("--customers", type=int, default=200)
    parser.add_argument("--reset", action="store_true", help="remove seeded data first")
    parser.add_argument("--reset-only", action="store_true")
    args = parser.parse_args()

    engine = create_engine(_database_url())
    with Session(engine) as session:
        if args.reset or args.reset_only:
            reset(session)
        if args.reset_only:
            return

        business_id, branch_id, counter_id, user_id, device_id = seed_tenant(session)
        unit_id = seed_units(session, business_id)
        seed_products(session, business_id, branch_id, unit_id, count=args.products)
        seed_customers(session, business_id, count=args.customers)
        session.execute(text("ANALYZE products"))
        session.execute(text("ANALYZE inventory_balances"))
        session.commit()

    print("\nseeded. ids for the load test:")
    print(f"  BM_LOAD_BUSINESS_ID={business_id}")
    print(f"  BM_LOAD_BRANCH_ID={branch_id}")
    print(f"  BM_LOAD_COUNTER_ID={counter_id}")
    print(f"  BM_LOAD_USER_ID={user_id}")
    print(f"  BM_LOAD_DEVICE_ID={device_id}")


if __name__ == "__main__":
    main()
