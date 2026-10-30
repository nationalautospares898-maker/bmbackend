"""Schema-wide invariants asserted against `information_schema`.

These are the invariants that are cheap to state and catastrophic to get wrong, so they
are checked against the live schema rather than trusted to review:

* **NFR-DATA-009** — every money column is `NUMERIC`. A single `double precision` column
  would corrupt a total silently and permanently.
* **NFR-DATA-010** — every business-scoped row carries `business_id NOT NULL`.
* Native enum types in the database match `bm.infra.db.enums` exactly, so a migration
  cannot drift from the code that reads it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.infra.db.enums import ALL_ENUM_TYPES

pytestmark = pytest.mark.db

#: Tables that are deliberately not business-scoped, with the reason.
NON_TENANT_TABLES: dict[str, str] = {
    "alembic_version": "migration bookkeeping",
    "businesses": "it is the tenant",
    "permissions": "global vocabulary, seeded from code",
    "roles": "system roles are shared; business_id is NULL for them by design",
    "role_permissions": "join table; scope comes from the role",
    "jobs": "infrastructure queue; payload carries the scope",
    "tax_rates": "scoped through tax_groups",
    # `DATA-MODEL.md §3.2` defines refresh_tokens without a business_id: a token is
    # scoped to a (user, device) pair, and the business follows from the user. Adding a
    # business_id would create a second, divergeable source of the same truth.
    "refresh_tokens": "scoped through user_id and device_id",
}

#: Tables whose `business_id` is legitimately nullable, with the reason.
NULLABLE_BUSINESS_ID: dict[str, str] = {
    # A failed login for an unknown username has no business to attribute it to.
    "login_attempts": "an attempt against an unknown username has no business yet",
}

#: Column-name fragments that indicate a monetary or quantitative value.
MONEY_FRAGMENTS = (
    "amount",
    "price",
    "total",
    "subtotal",
    "quantity",
    "balance",
    "cost",
    "discount",
    "rate_percent",
    "outstanding",
    "limit",
    "delta",
)

FLOAT_TYPES = {"double precision", "real", "float", "money"}


def _columns(session: Session) -> list[tuple[str, str, str, str]]:
    rows = session.execute(
        text(
            """
            SELECT c.table_name, c.column_name, c.data_type, c.is_nullable
              FROM information_schema.columns c
              JOIN information_schema.tables t
                ON t.table_name = c.table_name AND t.table_schema = c.table_schema
             WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
             ORDER BY c.table_name, c.ordinal_position
            """
        )
    ).all()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def test_no_float_columns_anywhere(app_session: Session) -> None:
    """ADR-013 / NFR-DATA-009. Not just money columns — no float column at all, because
    the next schema change should not have to re-argue it."""
    offenders = [
        f"{table}.{column} is {data_type}"
        for table, column, data_type, _ in _columns(app_session)
        if data_type in FLOAT_TYPES
    ]
    assert offenders == [], (
        "Money and quantity must be NUMERIC (ADR-013, NFR-DATA-009). Found: " + ", ".join(offenders)
    )


def test_money_shaped_columns_are_numeric(app_session: Session) -> None:
    offenders = [
        f"{table}.{column} is {data_type}"
        for table, column, data_type, _ in _columns(app_session)
        if any(fragment in column for fragment in MONEY_FRAGMENTS)
        and data_type not in {"numeric", "integer", "smallint", "bigint", "jsonb", "text"}
    ]
    assert offenders == [], "Money/quantity columns must be NUMERIC. Found: " + ", ".join(offenders)


def test_business_scoped_tables_have_a_not_null_business_id(app_session: Session) -> None:
    """NFR-DATA-010. The exemption list is explicit so a new table cannot quietly join
    it."""
    columns = _columns(app_session)
    tables = {table for table, _, _, _ in columns}
    business_id_nullability = {
        table: nullable for table, column, _, nullable in columns if column == "business_id"
    }

    missing: list[str] = []
    nullable: list[str] = []
    for table in sorted(tables):
        if table in NON_TENANT_TABLES:
            continue
        if table not in business_id_nullability:
            missing.append(table)
        elif business_id_nullability[table] == "YES":
            nullable.append(table)

    nullable = [t for t in nullable if t not in NULLABLE_BUSINESS_ID]

    assert missing == [], f"Tables without business_id: {missing}"
    assert nullable == [], f"Tables with a nullable business_id: {nullable}"


def test_database_enum_types_match_the_code(app_session: Session) -> None:
    """A migration must not drift from `bm.infra.db.enums`.

    Enum values are never removed — issued financial rows reference them forever — so
    the comparison is exact in both directions.
    """
    rows = app_session.execute(
        text(
            """
            SELECT t.typname, e.enumlabel
              FROM pg_type t
              JOIN pg_enum e ON e.enumtypid = t.oid
              JOIN pg_namespace n ON n.oid = t.typnamespace
             WHERE n.nspname = 'public'
             ORDER BY t.typname, e.enumsortorder
            """
        )
    ).all()

    in_database: dict[str, list[str]] = {}
    for type_name, label in rows:
        in_database.setdefault(type_name, []).append(label)

    in_code = {name: list(values) for name, values in ALL_ENUM_TYPES}

    assert set(in_database) == set(in_code), (
        f"Only in database: {sorted(set(in_database) - set(in_code))}; "
        f"only in code: {sorted(set(in_code) - set(in_database))}"
    )
    for name, expected in in_code.items():
        assert in_database[name] == expected, f"Enum {name} differs: {in_database[name]}"


def test_settings_scope_uniqueness_treats_null_branch_as_a_value(
    migration_session: Session,
) -> None:
    """Fix S-3. A plain UNIQUE (business_id, branch_id, key) would allow two
    business-wide rows for one key, because NULL is not equal to NULL."""
    index = migration_session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_settings__scope_key'")
    ).scalar()
    assert index is not None, "uq_settings__scope_key is missing"
    assert "UNIQUE" in index
    assert "COALESCE" in index.upper()


def test_counter_draft_floor_is_a_database_check(migration_session: Session) -> None:
    """C-5: at least 20 active draft bills per counter. A floor in code only would let a
    configuration change break a confirmed requirement."""
    constraint = migration_session.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'ck_counters__min_drafts'"
        )
    ).scalar()
    assert constraint is not None
    assert "20" in constraint


def test_financial_year_start_month_is_bounded(migration_session: Session) -> None:
    constraint = migration_session.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'ck_businesses__fy_start_month'"
        )
    ).scalar()
    assert constraint is not None


def test_business_timezone_exists_and_defaults(app_session: Session) -> None:
    """T3-02. Without this column the financial-year boundary would silently use UTC."""
    row = app_session.execute(
        text(
            "SELECT is_nullable, column_default FROM information_schema.columns"
            " WHERE table_name = 'businesses' AND column_name = 'timezone'"
        )
    ).first()
    assert row is not None, "businesses.timezone is missing (T3-02)"
    assert row[0] == "NO"
    assert "Asia/Kolkata" in (row[1] or "")


def test_required_extensions_are_installed(app_session: Session) -> None:
    installed = {
        row[0] for row in app_session.execute(text("SELECT extname FROM pg_extension")).all()
    }
    assert {"pg_trgm", "unaccent", "citext"} <= installed
