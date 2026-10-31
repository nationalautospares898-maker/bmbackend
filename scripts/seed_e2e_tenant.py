"""Seed one small, known tenant for the full-stack end-to-end suite.

Audit Issue F. The front end's existing E2E specs intercept the API at the network
layer, so none of them proves the built client can talk to the real server. That suite
needs a server with a real tenant, a real user it can log in as, and a product with real
stock — and it needs them to be the *same* every run, because an E2E assertion against
randomly generated data is an assertion nobody can read.

Deliberate choices:

* **Isolated and disposable.** Everything is created under a business whose display name
  could not be mistaken for the shop, and `--reset` removes exactly that business and
  nothing else. This script is for a throwaway database; it is not a fixture loader for
  a database anyone cares about.
* **Through real service paths where one exists.** The business, branch, counter, user
  and device are reference data inserted directly — there is no bootstrap endpoint,
  because creating the first owner is a deployment step (`CLAUDE.md §10`). The password
  hash comes from `bm.domain.auth.hashing`, so the credential below really does
  authenticate. The *product and its stock* are left to the E2E suite, which creates
  them through the API precisely so that the API is what is being tested.
* **The credential is a test credential and is printed.** It is not a secret: it exists
  only in a disposable database created seconds earlier, and the suite needs it. It is
  also deliberately not the kind of string anyone would reuse.

Usage:
    python scripts/seed_e2e_tenant.py            # create (idempotent)
    python scripts/seed_e2e_tenant.py --reset    # remove, then create
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from bm.config import Settings
from bm.domain.auth.hashing import hash_password

#: A name that could never be mistaken for the real shop.
E2E_BUSINESS_NAME = "E2E FIXTURE — NOT A REAL BUSINESS"

#: Fixed ids, so the suite can assert against them without reading them back.
BUSINESS_ID = uuid.UUID("0192f100-0000-7000-8000-0000000000b1")
BRANCH_ID = uuid.UUID("0192f100-0000-7000-8000-0000000000a1")
COUNTER_ID = uuid.UUID("0192f100-0000-7000-8000-0000000000c1")
USER_ID = uuid.UUID("0192f100-0000-7000-8000-0000000000e1")
DEVICE_ID = uuid.UUID("0192f100-0000-7000-8000-0000000000d1")

E2E_USERNAME = "e2e-owner"
#: Test-only, in a disposable database. Long enough to satisfy any policy minimum.
E2E_PASSWORD = "e2e-only-not-a-real-password-01"

#: Must match the namespace migration 0005 seeds system roles under, so the membership
#: below points at a role that exists.
_ROLE_NAMESPACE = uuid.UUID("6f1c2d4e-0000-4000-8000-000000000000")


def _system_role_id(role: str) -> uuid.UUID:
    return uuid.uuid5(_ROLE_NAMESPACE, f"system-role:{role}")


def _pepper() -> str:
    """The password pepper the API will run with.

    `Settings` holds it as a `SecretStr` so it cannot be logged by accident, which is
    the right default — it just has to be unwrapped here. Read from configuration rather
    than hard-coded: a hash peppered with a different value authenticates nobody, and
    that failure is indistinguishable from a wrong password.
    """
    return Settings().password_pepper.get_secret_value()


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


#: Child-to-parent. Taken from `tests/fixtures.py`, which the whole suite exercises on
#: every run, so the ordering is known correct rather than reasoned about here.
_TABLES = (
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
    "jobs",
    "idempotency_keys",
    "login_attempts",
    "refresh_tokens",
    "memberships",
    "devices",
    "settings",
    "counters",
    "users",
    "branches",
)


def reset(session: Session) -> None:
    """Remove the E2E business, and only it."""
    business = session.execute(
        text("SELECT id FROM businesses WHERE display_name = :n"), {"n": E2E_BUSINESS_NAME}
    ).scalar_one_or_none()
    if business is None:
        return
    for table in _TABLES:
        if session.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": table}).scalar():
            session.execute(
                text(f"DELETE FROM {table} WHERE business_id = :b"),  # fixed tuple above
                {"b": business},
            )
    session.execute(text("DELETE FROM businesses WHERE id = :b"), {"b": business})
    session.commit()
    print(f"reset: removed {business}")


def seed(session: Session) -> None:
    existing = session.execute(
        text("SELECT id FROM businesses WHERE display_name = :n"), {"n": E2E_BUSINESS_NAME}
    ).scalar_one_or_none()
    if existing is not None:
        print(f"already seeded: {existing}")
        return

    # `legal_name` is deliberately NULL: the owner has not supplied one and inventing a
    # plausible placeholder is forbidden (OD-4).
    session.execute(
        text(
            "INSERT INTO businesses (id, display_name, timezone, financial_year_start_month)"
            " VALUES (:id, :name, 'Asia/Kolkata', 4)"
        ),
        {"id": BUSINESS_ID, "name": E2E_BUSINESS_NAME},
    )
    session.execute(
        text(
            "INSERT INTO branches (id, business_id, code, name, is_active)"
            " VALUES (:id, :b, 'MAIN', 'Main', true)"
        ),
        {"id": BRANCH_ID, "b": BUSINESS_ID},
    )
    session.execute(
        text(
            "INSERT INTO counters (id, business_id, branch_id, code, name, is_active,"
            " max_open_drafts) VALUES (:id, :b, :br, 'C1', 'Counter 1', true, 20)"
        ),
        {"id": COUNTER_ID, "b": BUSINESS_ID, "br": BRANCH_ID},
    )
    session.execute(
        text(
            "INSERT INTO users (id, business_id, username, full_name, password_hash,"
            " is_active, must_change_password)"
            " VALUES (:id, :b, :u, 'E2E Owner', :h, true, false)"
        ),
        {
            "id": USER_ID,
            "b": BUSINESS_ID,
            "u": E2E_USERNAME,
            # The real hasher, with the **same pepper the API will run with** — read
            # from the environment rather than hard-coded, because a hash peppered with
            # a different value authenticates nobody and the failure looks exactly like
            # a wrong password.
            "h": hash_password(E2E_PASSWORD, pepper=_pepper()),
        },
    )
    session.execute(
        text(
            "INSERT INTO memberships (id, user_id, business_id, branch_id, role_id)"
            " VALUES (:id, :u, :b, NULL, :r)"
        ),
        {
            "id": uuid.uuid4(),
            "u": USER_ID,
            "b": BUSINESS_ID,
            # All branches, so the suite can reach every screen.
            "r": _system_role_id("owner"),
        },
    )
    session.execute(
        text(
            "INSERT INTO devices (id, business_id, branch_id, counter_id, status, label)"
            " VALUES (:id, :b, :br, :c, 'active', 'E2E Browser')"
        ),
        {"id": DEVICE_ID, "b": BUSINESS_ID, "br": BRANCH_ID, "c": COUNTER_ID},
    )
    session.commit()
    print(f"seeded business={BUSINESS_ID}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="remove the tenant first")
    args = parser.parse_args()

    engine = create_engine(_database_url())
    with Session(engine) as session:
        if args.reset:
            reset(session)
        seed(session)
    engine.dispose()

    print("\nfull-stack E2E credentials (disposable database only):")
    print(f"  BM_E2E_USERNAME={E2E_USERNAME}")
    print(f"  BM_E2E_PASSWORD={E2E_PASSWORD}")
    print(f"  BM_E2E_COUNTER_ID={COUNTER_ID}")
    print(f"  BM_E2E_DEVICE_ID={DEVICE_ID}")


if __name__ == "__main__":
    main()
