"""seed permissions and system roles

Revision ID: 0005
Revises: 0004
Created: 2026-10-30

Seeds **permissions and system roles only** (`CLAUDE.md §7`). Never a business, tax
rate, product, demo account or default password.

Idempotent and re-runnable: `ON CONFLICT DO UPDATE` for descriptions, and a full
re-synchronisation of `role_permissions` for the system roles. Re-synchronising matters
because adding a permission to the enum must also reach the roles that should hold it —
otherwise an owner silently loses access to a new feature.

Custom (non-system) roles an owner has created are left untouched.

Numbered 0005 rather than the plan's 0016 because the identity tables it depends on now
exist at 0003 and no table between them needs seeding. Migrations are a dependency
chain, not a fixed numbering, and `DATA-MODEL.md §6.1` records the intended order rather
than mandatory revision ids.
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

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Stable role ids so re-running the seed does not create duplicates and so a
#: membership row keeps pointing at the same role across environments.
_ROLE_NAMESPACE = uuid.UUID("6f1c2d4e-0000-4000-8000-000000000000")


def _role_id(code: str) -> uuid.UUID:
    return uuid.uuid5(_ROLE_NAMESPACE, f"system-role:{code}")


def upgrade() -> None:
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

        granted = {str(p) for p in ROLE_PERMISSIONS[role]}

        # Remove grants this role should no longer hold, then add the missing ones.
        # Doing both makes the seed a true synchronisation rather than an append.
        bind.execute(
            sa.text(
                "DELETE FROM role_permissions"
                " WHERE role_id = :role_id"
                "   AND NOT (permission_code = ANY(:granted))"
            ),
            {"role_id": role_id, "granted": sorted(granted)},
        )
        for code in sorted(granted):
            bind.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_code)"
                    " VALUES (:role_id, :code) ON CONFLICT DO NOTHING"
                ),
                {"role_id": role_id, "code": code},
            )


def downgrade() -> None:
    bind = op.get_bind()
    role_ids = [_role_id(str(role)) for role in SystemRole]

    # Only the system roles are removed. A membership still referencing one will block
    # this via the RESTRICT foreign key, which is correct: silently detaching a user
    # from their role would leave them authenticated with no permissions and no
    # explanation.
    bind.execute(
        sa.text("DELETE FROM role_permissions WHERE role_id = ANY(:ids)"), {"ids": role_ids}
    )
    bind.execute(sa.text("DELETE FROM roles WHERE id = ANY(:ids)"), {"ids": role_ids})
    bind.execute(
        sa.text("DELETE FROM permissions WHERE code = ANY(:codes)"),
        {"codes": sorted(str(p) for p in Permission)},
    )
