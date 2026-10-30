"""Every ORM mapper, imported so Alembic's autogenerate sees the full metadata.

ORM models live here and never leave a repository as an API response type
(`ARCHITECTURE.md §3` rule 4). Services exchange domain dataclasses instead, which is
what keeps a database column rename from becoming an API change.
"""

from __future__ import annotations

from bm.infra.db.models.identity import (
    AuditLog,
    Device,
    LoginAttempt,
    Membership,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    User,
)
from bm.infra.db.models.tenancy import Branch, Business, Counter, Setting

__all__ = [
    "AuditLog",
    "Branch",
    "Business",
    "Counter",
    "Device",
    "LoginAttempt",
    "Membership",
    "Permission",
    "RefreshToken",
    "Role",
    "RolePermission",
    "Setting",
    "User",
]
