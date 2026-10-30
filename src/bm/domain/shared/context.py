"""`AuthContext` — the only source of tenant identity.

`SECURITY.md §3.2`: endpoints never accept `business_id`. Every repository method takes
this context (or a `Scope` derived from it) and builds the tenant predicate itself, so
scoping cannot be forgotten at a call site. A generated isolation matrix test
(`tests/api/test_isolation.py`) is what proves it holds across every endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from bm.domain.auth.permissions import Permission
from bm.domain.shared.errors import PermissionDenied


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Resolved identity and scope for one request."""

    user_id: UUID
    business_id: UUID
    branch_id: UUID | None
    counter_id: UUID | None
    device_id: UUID | None
    role: str
    permissions: frozenset[str]
    token_id: str
    business_timezone: str = "Asia/Kolkata"
    full_name: str = ""
    must_change_password: bool = False

    def has(self, permission: Permission | str) -> bool:
        return str(permission) in self.permissions

    def require(self, permission: Permission | str) -> None:
        """Re-assert a permission inside a service.

        `SECURITY.md §3.1` rule 2: the route declaration is the first gate, but anything
        destructive or financially sensitive checks again here, so an internal caller
        cannot bypass the router.
        """
        if not self.has(permission):
            raise PermissionDenied(str(permission))

    @property
    def requires_branch(self) -> UUID:
        """Branch-scoped operations need a selected branch, not a guess."""
        if self.branch_id is None:
            raise PermissionDenied("branch.scope", "Select a branch before doing this.")
        return self.branch_id


@dataclass(frozen=True, slots=True)
class Scope:
    """A narrowed view of `AuthContext` for repositories that need nothing else."""

    business_id: UUID
    branch_id: UUID | None = None

    @classmethod
    def of(cls, ctx: AuthContext) -> Scope:
        return cls(business_id=ctx.business_id, branch_id=ctx.branch_id)
