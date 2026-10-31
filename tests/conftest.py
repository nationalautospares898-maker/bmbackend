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
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from bm.config import Settings
from tests.fixtures import Tenant, create_tenant, truncate_business_data

REPO_ROOT = Path(__file__).resolve().parents[1]

TEST_SIGNING_KEY = "test-only-signing-key-not-a-secret-abcdef"
TEST_PEPPER = "test-only-password-pepper-not-a-secret-ab"
TEST_IP_PEPPER = "test-only-ip-hash-pepper-not-a-secret-abc"


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

#: Database names this suite is allowed to touch.
#:
#: The suite **truncates every business table** between tests (`clean_db`), so the
#: database it points at is destroyed by running it. That is fine for a scratch
#: database and catastrophic for any other, and until this guard existed there was
#: nothing keeping the two apart:
#:
#: `_load_dotenv()` above loads the developer's `.env`, which necessarily points
#: `BM_DATABASE_URL` at the database the application uses — on a development machine,
#: the one with their data in it. The `setdefault` below it then cannot help, because the
#: value is already set. So a bare `uv run pytest` truncated the working database, which
#: is exactly what happened on 31 October 2026: a demo tenant with its customers,
#: invoices and stock was wiped an evening before it was needed, and there was no backup.
#:
#: Hence an allow-list on the database **name**, checked against the resolved URL rather
#: than against an environment variable, and a refusal rather than a skip. A skip would
#: report green.
#: `bm_test`, `bm_migrations` and `bm_perf` are the three CI databases — the guard has to
#: name them or it breaks the pipeline it is meant to protect. The rest are the scratch
#: databases this project already accumulated locally.
_ALLOWED_TEST_DATABASES = frozenset(
    {
        "bm_test",
        "bm_migrations",
        "bm_perf",
        "bm_ci",
        "bm_scratch",
        "bm_fresh",
        "bm_issue_tests",
        "bm_drift",
    }
)


def _database_name(url: str) -> str:
    return urlsplit(url.replace("postgresql+psycopg://", "postgresql://")).path.lstrip("/")


def _require_a_test_database(url: str) -> None:
    """Refuse to run the truncating suite against anything but a scratch database."""
    name = _database_name(url)
    if name in _ALLOWED_TEST_DATABASES or name.endswith(("_test", "_ci", "_scratch")):
        return
    raise pytest.UsageError(
        f"refusing to run the test suite against database {name!r}: this suite TRUNCATES "
        "every business table between tests and would destroy its contents.\n\n"
        "Point it at a scratch database explicitly, e.g.\n"
        "    BM_TEST_DATABASE_URL=postgresql+psycopg://bm_app:bm_app_local_only"
        "@localhost:55439/bm_test\n\n"
        f"Allowed names: any of {sorted(_ALLOWED_TEST_DATABASES)}, or any name ending "
        "_test / _ci / _scratch."
    )


def _app_database_url() -> str:
    """The database the tests run against.

    `BM_TEST_DATABASE_URL` is consulted **first and separately** from
    `BM_DATABASE_URL`, so that pointing the application at a database is not the same
    act as pointing the test suite at it. They were the same variable, and that is how
    the application's database came to be the suite's default target.
    """
    url = os.environ.get("BM_TEST_DATABASE_URL") or os.environ["BM_DATABASE_URL"]
    _require_a_test_database(url)
    return url


def _migration_database_url() -> str:
    url = (
        os.environ.get("BM_TEST_MIGRATION_DATABASE_URL")
        or os.environ.get("BM_MIGRATION_DATABASE_URL")
        or _app_database_url()
    )
    _require_a_test_database(url)
    return url


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


# --- API-level fixtures -------------------------------------------------------


@pytest.fixture(scope="session")
def api_settings() -> Settings:
    """Settings for the application under test, pinned explicitly.

    Nothing is left to the ambient environment: a developer's local `.env` must not be
    able to decide whether a security assertion holds.
    """
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        database_url=_app_database_url(),
        jwt_signing_key=TEST_SIGNING_KEY,
        password_pepper=TEST_PEPPER,
        ip_hash_pepper=TEST_IP_PEPPER,
        cors_allowed_origins="http://localhost:5173",
        enable_api_docs=False,
        access_token_ttl_seconds=900,
        refresh_token_ttl_days=30,
    )


@pytest.fixture
def api_client(
    api_settings: Settings, app_engine: Engine, schema_at_head: None
) -> Iterator[TestClient]:
    """A client bound to the real database.

    The engine is initialised for the process and disposed afterwards so each test gets
    a clean pool; the application code path is otherwise untouched, which is the point —
    these tests exercise the real dependency chain, not a stub.
    """
    from bm.infra.db import session as session_module
    from bm.main import create_app

    session_module.dispose_engine()
    session_module.init_engine(api_settings)
    app = create_app(api_settings)
    # HTTPS base URL, because the refresh cookie is correctly marked `Secure` outside
    # local development and a client would not send it over plain HTTP. Testing against
    # http:// would mean either skipping the refresh tests or weakening the flag — both
    # worse than matching the real transport.
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield client
    session_module.dispose_engine()


@pytest.fixture
def clean_db(migration_session: Session) -> Iterator[None]:
    """Truncate per-test business data before and after each API test."""
    truncate_business_data(migration_session)
    yield
    truncate_business_data(migration_session)


@pytest.fixture
def tenant(migration_session: Session, clean_db: None) -> Tenant:
    return create_tenant(migration_session)
