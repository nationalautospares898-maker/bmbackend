"""Alembic head comparison for `/readyz`.

ADR-016: migrations never run on application start. A replica whose code expects a newer
schema than the database has must **not** take traffic, so `/readyz` fails on a head
mismatch. That is the whole reason this module exists.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class MigrationState:
    expected_head: str | None
    current_revision: str | None

    @property
    def matches(self) -> bool:
        return (
            self.expected_head is not None
            and self.current_revision is not None
            and self.expected_head == self.current_revision
        )


def _project_root() -> Path:
    # src/bm/infra/db/migration_state.py → repository root
    return Path(__file__).resolve().parents[4]


@functools.lru_cache(maxsize=1)
def expected_head() -> str | None:
    """The head revision the deployed code expects, read from `migrations/versions/`."""
    ini = _project_root() / "alembic.ini"
    if not ini.exists():
        return None
    config = Config(str(ini))
    config.set_main_option("script_location", str(_project_root() / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        # Multiple heads mean an unmerged branch; treat it as "not a known head" so
        # readiness fails loudly rather than picking one arbitrarily.
        return None
    return heads[0]


def current_revision(session: Session) -> str | None:
    """The revision the database is actually at."""
    row = session.execute(
        text(
            "SELECT version_num FROM alembic_version"
            " WHERE to_regclass('alembic_version') IS NOT NULL"
        )
    ).first()
    return row[0] if row else None


def migration_state(session: Session) -> MigrationState:
    return MigrationState(expected_head=expected_head(), current_revision=current_revision(session))
