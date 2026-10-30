"""Tenant and user factories for the API tests.

`CLAUDE.md §10`: fixtures build state through **real service paths**, not raw inserts,
except for reference data and deliberately corrupt states. Business, branch, counter and
the first user are reference data — there is no bootstrap endpoint, because creating the
first owner is a deployment step, not an API operation — so they are inserted directly.
Everything with a service behind it goes through that service.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.hashing import hash_password
from bm.domain.auth.permissions import SystemRole

#: Must match the namespace in migration 0005, so a membership points at the seeded role.
_ROLE_NAMESPACE = uuid.UUID("6f1c2d4e-0000-4000-8000-000000000000")

TEST_PEPPER = "test-only-password-pepper-not-a-secret-ab"
TEST_PASSWORD = "counter-password-01"


def system_role_id(role: SystemRole | str) -> uuid.UUID:
    return uuid.uuid5(_ROLE_NAMESPACE, f"system-role:{role}")


@dataclass(frozen=True, slots=True)
class Tenant:
    business_id: uuid.UUID
    branch_id: uuid.UUID
    branch_code: str
    counter_id: uuid.UUID
    timezone: str = "Asia/Kolkata"


@dataclass(frozen=True, slots=True)
class UserFixture:
    id: uuid.UUID
    username: str
    password: str
    role: str
    business_id: uuid.UUID
    branch_id: uuid.UUID | None
    device_id: uuid.UUID


def create_tenant(
    session: Session,
    *,
    display_name: str = "NATIONAL AUTO SPARES",
    timezone: str = "Asia/Kolkata",
    branch_code: str = "MAIN",
    counter_code: str = "C1",
) -> Tenant:
    """Create a business with one branch and one counter.

    `legal_name` is deliberately left NULL: the owner has not supplied it and inventing
    one is forbidden (OD-4). A blank value blocks go-live, not startup, and tests must
    exercise that state rather than paper over it.
    """
    business_id = uuid.uuid4()
    branch_id = uuid.uuid4()
    counter_id = uuid.uuid4()

    session.execute(
        text(
            "INSERT INTO businesses (id, display_name, timezone)"
            " VALUES (:id, :display_name, :timezone)"
        ),
        {"id": business_id, "display_name": display_name, "timezone": timezone},
    )
    session.execute(
        text(
            "INSERT INTO branches (id, business_id, code, name)"
            " VALUES (:id, :business_id, :code, :name)"
        ),
        {"id": branch_id, "business_id": business_id, "code": branch_code, "name": "Main Shop"},
    )
    session.execute(
        text(
            "INSERT INTO counters (id, business_id, branch_id, code, name)"
            " VALUES (:id, :business_id, :branch_id, :code, :name)"
        ),
        {
            "id": counter_id,
            "business_id": business_id,
            "branch_id": branch_id,
            "code": counter_code,
            "name": f"Counter {counter_code}",
        },
    )
    session.commit()
    return Tenant(
        business_id=business_id,
        branch_id=branch_id,
        branch_code=branch_code,
        counter_id=counter_id,
        timezone=timezone,
    )


def create_user(
    session: Session,
    tenant: Tenant,
    *,
    username: str,
    role: SystemRole = SystemRole.CASHIER,
    password: str = TEST_PASSWORD,
    all_branches: bool = False,
    is_active: bool = True,
    must_change_password: bool = False,
    register_device: bool = True,
) -> UserFixture:
    """Create a user with a seeded system role and a registered device."""
    user_id = uuid.uuid4()
    device_id = uuid.uuid4()
    branch_id = None if all_branches else tenant.branch_id

    session.execute(
        text(
            "INSERT INTO users"
            " (id, business_id, username, full_name, password_hash, is_active,"
            "  must_change_password)"
            " VALUES (:id, :business_id, :username, :full_name, :password_hash, :is_active,"
            "         :must_change)"
        ),
        {
            "id": user_id,
            "business_id": tenant.business_id,
            "username": username,
            "full_name": username.title(),
            "password_hash": hash_password(password, pepper=TEST_PEPPER),
            "is_active": is_active,
            "must_change": must_change_password,
        },
    )
    session.execute(
        text(
            "INSERT INTO memberships (id, user_id, business_id, branch_id, role_id)"
            " VALUES (:id, :user_id, :business_id, :branch_id, :role_id)"
        ),
        {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "business_id": tenant.business_id,
            "branch_id": branch_id,
            "role_id": system_role_id(role),
        },
    )
    if register_device:
        session.execute(
            text(
                "INSERT INTO devices (id, business_id, branch_id, counter_id, label)"
                " VALUES (:id, :business_id, :branch_id, :counter_id, :label)"
            ),
            {
                "id": device_id,
                "business_id": tenant.business_id,
                "branch_id": tenant.branch_id,
                "counter_id": tenant.counter_id,
                "label": f"{username} PC",
            },
        )
    session.commit()

    return UserFixture(
        id=user_id,
        username=username,
        password=password,
        role=str(role),
        business_id=tenant.business_id,
        branch_id=branch_id,
        device_id=device_id,
    )


#: Tables holding per-test business data, in child-to-parent order.
#:
#: Seeded reference data (`permissions`, system `roles`, `role_permissions`) is
#: deliberately absent: migration 0005 owns it, and a test must not be able to delete an
#: owner's role grants.
#:
#: These are `DELETE`s, not `TRUNCATE … CASCADE`. `roles.business_id` references
#: `businesses`, so a cascading truncate of `businesses` silently takes the seeded
#: system roles with it and every later login fails with a foreign-key violation that
#: looks nothing like its cause.
BUSINESS_DATA_TABLES = (
    # Child-to-parent. Every foreign key into a financial row is RESTRICT, so the
    # order here is not cosmetic: deleting a parent first fails rather than cascading,
    # which is the behaviour the live system relies on.
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
    # The queue. An import upload enqueues an `import.parse` row in the same
    # transaction, so a test that uploads leaves one behind; `uq_jobs__dedupe` would
    # then make the *next* test's enqueue a silent no-op.
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


def truncate_business_data(session: Session) -> None:
    """Reset per-test state as the migration role.

    `audit_logs` is append-only for `bm_app` by privilege, so only the DDL role can
    clear it. That asymmetry is the control working as intended, not an inconvenience to
    route around.
    """
    session.rollback()  # a previous test may have left an aborted transaction
    for name in BUSINESS_DATA_TABLES:
        if session.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).scalar():
            session.execute(
                text(f"DELETE FROM {name}")
            )  # table names come from the fixed tuple above

    # Custom roles belong to a business, so they must go before it. System roles have a
    # NULL business_id and are left alone.
    session.execute(
        text(
            "DELETE FROM role_permissions WHERE role_id IN"
            " (SELECT id FROM roles WHERE business_id IS NOT NULL)"
        )
    )
    session.execute(text("DELETE FROM roles WHERE business_id IS NOT NULL"))
    session.execute(text("DELETE FROM businesses"))
    session.commit()
