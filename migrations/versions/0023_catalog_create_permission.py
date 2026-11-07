"""Re-synchronise permissions and system roles, for `catalog.create`.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-07

The owner approved letting a cashier add a part that is missing at the billing counter,
and **did not** approve giving them the catalogue generally (Decision B, 2026-11-07). So
`catalog.create` exists as its own permission — create only — rather than `catalog.write`
being handed to the cashier role.

## Why this migration exists at all

Permissions live in the database. `ROLE_PERMISSIONS` in
`domain/auth/permissions.py` is the *source*, but a login reads `role_permissions`, so
adding an enum member changes nothing until a migration carries it across. `0005`
anticipated exactly this — "adding a permission to the enum must also reach the roles
that should hold it — otherwise an owner silently loses access to a new feature" — and
was written as a **re-runnable synchronisation** for that reason.

This migration therefore re-runs that same synchronisation rather than hand-writing the
one grant. The difference matters: a hand-written `INSERT` would add `catalog.create` to
the cashier and quietly leave the owner, manager and stock keeper without it, because
`frozenset(Permission)` for the owner is computed in Python and never stored anywhere.

Custom (non-system) roles an owner has created are left untouched, as in `0005`.

## What it does not do

No grant is removed from any role that is not also removed from `ROLE_PERMISSIONS`. The
synchronisation deletes a role's grants that the code no longer lists, which is what
makes it a synchronisation — and the only change to that list in this release is an
addition.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from bm.domain.auth.permissions import (
    PERMISSION_DESCRIPTIONS,
    ROLE_NAMES,
    ROLE_PERMISSIONS,
    Permission,
    SystemRole,
)

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The same namespace `0005` uses, so a membership row keeps pointing at the same role.
_ROLE_NAMESPACE = uuid.UUID("6f1c2d4e-0000-4000-8000-000000000000")


def _role_id(code: str) -> uuid.UUID:
    return uuid.uuid5(_ROLE_NAMESPACE, f"system-role:{code}")


def _synchronise() -> None:
    """`0005`'s upgrade, re-executed against the current enum."""
    bind = op.get_bind()

    for permission in Permission:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (code, description) VALUES (:code, :description)"
                " ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description"
            ),
            {"code": str(permission), "description": PERMISSION_DESCRIPTIONS[permission]},
        )

    for role in SystemRole:
        role_id = _role_id(str(role))
        bind.execute(
            sa.text(
                "INSERT INTO roles (id, business_id, code, name, is_system)"
                " VALUES (:id, NULL, :code, :name, true)"
                " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name"
            ),
            {"id": role_id, "code": str(role), "name": ROLE_NAMES[role]},
        )

        granted = sorted({str(p) for p in ROLE_PERMISSIONS[role]})
        bind.execute(
            sa.text(
                "DELETE FROM role_permissions"
                " WHERE role_id = :role_id AND NOT (permission_code = ANY(:granted))"
            ),
            {"role_id": role_id, "granted": granted},
        )
        for code in granted:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_code)"
                    " VALUES (:role_id, :code) ON CONFLICT DO NOTHING"
                ),
                {"role_id": role_id, "code": code},
            )


def upgrade() -> None:
    _synchronise()


def downgrade() -> None:
    """Remove the grant and the permission row.

    Deliberately **not** a full re-synchronisation: on the way down the code still has
    `catalog.create` in its enum, so re-synchronising would re-grant the thing being
    withdrawn. The grants are dropped first because `role_permissions.permission_code`
    references `permissions.code`.
    """
    bind = op.get_bind()
    code = str(Permission.CATALOG_CREATE)
    bind.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_code = :code"), {"code": code}
    )
    bind.execute(sa.text("DELETE FROM permissions WHERE code = :code"), {"code": code})
