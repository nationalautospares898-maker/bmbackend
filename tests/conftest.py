"""Shared test fixtures.

**Real PostgreSQL. SQLite is prohibited** (ADR-017) — it cannot test row locks,
`NUMERIC`, partial indexes, enums or column privileges, which is most of what this
system relies on. If no database is reachable, the database-backed tests **skip with a
clear reason** rather than silently passing against a weaker engine.

Fixtures build state through **real service paths** wherever a service exists; raw
inserts are reserved for reference data and deliberately corrupt states
(`CLAUDE.md §10`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# Values good enough to construct Settings in tests that never touch a database.
os.environ.setdefault("BM_ENV", "test")
os.environ.setdefault("BM_JWT_SIGNING_KEY", "test-only-signing-key-not-a-secret-abcdef")
os.environ.setdefault("BM_PASSWORD_PEPPER", "test-only-password-pepper-not-a-secret-ab")
os.environ.setdefault("BM_IP_HASH_PEPPER", "test-only-ip-hash-pepper-not-a-secret-abc")
os.environ.setdefault(
    "BM_DATABASE_URL", "postgresql+psycopg://bm_app:bm_app_local_only@localhost:55439/bm"
)


def _app_database_url() -> str:
    return os.environ["BM_DATABASE_URL"]


def _migration_database_url() -> str:
    return os.environ.get("BM_MIGRATION_DATABASE_URL") or _app_database_url()


def _probe(url: str) -> str | None:
    """Return None if usable, otherwise the reason to skip."""
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except Exception as exc:  # noqa: BLE001
        return f"PostgreSQL is not reachable at the configured URL: {type(exc).__name__}"
    return None


@pytest.fixture(scope="session")
def migration_engine() -> Iterator[Engine]:
    """DDL-capable connection, used by the migration and privilege tests."""
    url = _migration_database_url()
    reason = _probe(url)
    if reason:
        pytest.skip(reason)
    engine = create_engine(url, poolclass=None, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def app_engine() -> Iterator[Engine]:
    """Application-role connection. This is the one that must *fail* to mutate an
    append-only table, which is why the privilege tests must not use the DDL role."""
    url = _app_database_url()
    reason = _probe(url)
    if reason:
        pytest.skip(reason)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def schema_at_head(migration_engine: Engine) -> None:
    """Assert the database is migrated. Tests do not migrate implicitly.

    Running migrations from a fixture would hide the ADR-016 rule that migrations are an
    explicit step, and would make a failing migration look like a failing test.
    """
    with migration_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT version_num FROM alembic_version"
                " WHERE to_regclass('alembic_version') IS NOT NULL"
            )
        ).first()
    if row is None:
        pytest.skip("Database is not migrated. Run: alembic upgrade head")


@pytest.fixture
def app_session(app_engine: Engine, schema_at_head: None) -> Iterator[Session]:
    """A session as the application role, rolled back at the end of the test."""
    factory = sessionmaker(bind=app_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def migration_session(migration_engine: Engine, schema_at_head: None) -> Iterator[Session]:
    factory = sessionmaker(bind=migration_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def app_role_present(migration_session: Session) -> bool:
    """True when `bm_app` exists as a distinct role.

    The privilege tests skip loudly when it does not, so a developer never sees a green
    immutability test that actually proved nothing.
    """
    return bool(
        migration_session.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'bm_app'")).first()
    )


@pytest.fixture
def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
