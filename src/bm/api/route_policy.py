"""Which routes are allowed to have no declared permission.

`SECURITY.md §3.1` rule 1 and M1's acceptance criterion: a route that declares no
permission **fails CI**. `tests/structural/test_route_permissions.py` walks the mounted
route table and treats these two dictionaries as the only escape hatch — each entry
carries the reason it is safe, so adding one is a visible decision rather than a quiet
omission.
"""

from __future__ import annotations

#: No authentication at all.
PUBLIC_ROUTES: dict[str, str] = {
    "/healthz": "liveness probe; returns no data",
    "/readyz": "readiness probe; reports only reachability and migration head",
    "/api/v1/openapi.json": "contract document; authenticated at the proxy in production",
    "/api/v1/docs": "disabled in production via BM_ENABLE_API_DOCS",
    "/api/v1/docs/oauth2-redirect": "docs support route",
    "/api/v1/auth/login": "issues the first token; rate-limited and subject to lockout",
    "/api/v1/auth/refresh": "cookie-authenticated rotation, bound to X-Device-Id",
    "/api/v1/auth/logout": "revokes the caller's own session only",
}

#: Authenticated, but no *specific* permission: any signed-in user may read or change
#: their own context.
AUTHENTICATED_ONLY_ROUTES: dict[str, str] = {
    "/api/v1/meta": "server version and time for the signed-in client",
    "/api/v1/auth/me": "the caller's own context",
    "/api/v1/auth/select-branch": "narrowed to the caller's own memberships",
    "/api/v1/auth/change-password": "the caller's own password",
    "/api/v1/sync/bootstrap": "the caller's own client configuration",
}
