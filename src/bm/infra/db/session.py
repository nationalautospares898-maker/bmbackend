"""Database engine, session factory and the unit-of-work boundary.

Design points that are load-bearing:

* **Sync SQLAlchemy** (ADR-003). Explicit transactions and `SELECT … FOR UPDATE` are
  easier to reason about and to test than their async equivalents, and this application
  is database-bound rather than fan-out-bound.
* **`statement_timeout` and `lock_timeout` per connection** (`SECURITY.md §7`). A
  finalisation that cannot get its locks fails fast with a retryable error instead of
  hanging a counter while a customer waits.
* **One request, one transaction** (`ARCHITECTURE.md §5.1`). The session is opened by a
  dependency, committed on success, rolled back on any exception. Services never commit.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from bm.config import Settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def build_engine(settings: Settings) -> Engine:
    """Create the engine and attach the per-connection timeouts."""
    engine = create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
        echo=False,
    )

    statement_timeout = settings.db_statement_timeout_ms
    lock_timeout = settings.db_lock_timeout_ms

    @event.listens_for(engine, "connect")
    def _set_timeouts(dbapi_connection: Any, _record: Any) -> None:
        # Applied on the raw connection so every session inherits them, including the
        # ones a test opens directly for concurrency probes.
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f"SET statement_timeout = {int(statement_timeout)}")
            cursor.execute(f"SET lock_timeout = {int(lock_timeout)}")
            cursor.execute("SET idle_in_transaction_session_timeout = 60000")

    return engine


def init_engine(settings: Settings) -> Engine:
    """Initialise the process-wide engine. Called once at startup."""
    global _engine, _session_factory
    if _engine is None:
        _engine = build_engine(settings)
        _session_factory = sessionmaker(
            bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database engine is not initialised. Call init_engine() first.")
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        raise RuntimeError("Session factory is not initialised. Call init_engine() first.")
    return _session_factory


def dispose_engine() -> None:
    """Release pooled connections on shutdown, and reset state between test runs."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextmanager
def transaction() -> Iterator[Session]:
    """One unit of work: commit on success, roll back on any exception.

    This is the only place a commit happens for a write request. A service that needs
    atomicity receives this session and must not commit or begin a nested transaction —
    `ARCHITECTURE.md §5.1` rule 2.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def read_session() -> Iterator[Session]:
    """Read-only unit of work. Rolls back rather than committing, so a stray flush
    cannot persist anything from a `GET`."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def database_reachable(session: Session) -> bool:
    """Used by `/readyz`. A trivial query, not a connection-pool guess.

    Any failure means "not ready", so the exception class is deliberately broad: a
    readiness probe that raised would be reported as a 500 rather than an honest 503.
    """
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("readiness_database_unreachable", extra={"reason": type(exc).__name__})
        return False
    return True
