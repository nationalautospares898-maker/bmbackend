"""Alembic environment.

Deliberate choices:

* The URL comes from `BM_MIGRATION_DATABASE_URL`, falling back to `BM_DATABASE_URL` for
  local development only. In production the DDL-capable role exists solely in the
  migration job (`CLAUDE.md §11`).
* `compare_type` is on so an accidental type drift shows up in an autogenerate diff —
  important when every money column must stay `NUMERIC` (NFR-DATA-009).
* Migrations are never invoked from application code (ADR-016).
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

from bm.infra.db import models as _models  # noqa: F401  (registers every mapper)
from bm.infra.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _load_dotenv() -> None:
    """Make `alembic upgrade head` work locally without exporting variables by hand.

    Real environments supply the variable directly; this only fills gaps, never
    overrides, so a deploy job's `BM_MIGRATION_DATABASE_URL` always wins over a stray
    local file.
    """
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _database_url() -> str:
    _load_dotenv()
    url = os.environ.get("BM_MIGRATION_DATABASE_URL") or os.environ.get("BM_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Set BM_MIGRATION_DATABASE_URL (preferred) or BM_DATABASE_URL before running "
            "Alembic. See docs/DEPLOYMENT.md §3."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            # Each revision runs in its own transaction so a failure leaves the database
            # at a known revision rather than halfway through two.
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
