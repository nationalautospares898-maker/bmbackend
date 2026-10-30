"""extensions and enum types

Revision ID: 0001
Revises:
Created: 2026-10-30

First migration. Creates the three extensions the search and identity design depends on
and every native ENUM type, so no later migration has to create or diverge from one.

* `pg_trgm`  — GIN trigram index for fuzzy/contains product search.
* `unaccent` — normalisation, so `Alliéd` matches `allied`.
* `citext`   — case-insensitive usernames without lowercasing at every call site.

Enum values are never removed: issued financial rows reference them forever.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from bm.infra.db.enums import ALL_ENUM_TYPES

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("pg_trgm", "unaccent", "citext")


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')

    for type_name, values in ALL_ENUM_TYPES:
        rendered = ", ".join(f"'{value}'" for value in values)
        op.execute(f"CREATE TYPE {type_name} AS ENUM ({rendered})")


def downgrade() -> None:
    for type_name, _values in reversed(ALL_ENUM_TYPES):
        op.execute(f"DROP TYPE IF EXISTS {type_name}")

    # Extensions are deliberately NOT dropped. Another schema in the same database may
    # depend on them, and dropping an extension cascades to objects we do not own.
    # Leaving them is the conservative choice for a reversible migration.
