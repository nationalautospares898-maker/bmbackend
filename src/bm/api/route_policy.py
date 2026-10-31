"""Which routes are allowed to have no declared permission, and how to enumerate them.

`SECURITY.md §3.1` rule 1 and M1's acceptance criterion: a route that declares no
permission **fails CI**. `tests/structural/test_route_permissions.py` walks the mounted
route table and treats these two dictionaries as the only escape hatch — each entry
carries the reason it is safe, so adding one is a visible decision rather than a quiet
omission.

The walker lives here, beside the policy it enforces, rather than in the tests. It is
load-bearing for an authorization gate, so it is worth having mypy and ruff look at it —
and two separate test packages need it, which previously meant two hand-rolled copies of
a one-line comprehension over `app.routes`.

That comprehension stopped being correct at FastAPI 0.141. Up to 0.140 `include_router`
copied each child route into the parent's `routes` list, so a flat scan saw everything.
From 0.141 it appends one opaque `_IncludedRouter` whose `path` is `None` and whose
children are reachable only through `effective_route_contexts()`. A flat
`isinstance(r, APIRoute)` scan therefore returned **3 routes instead of 87** — the three
declared directly on the app — and the gate that is supposed to fail CI when a route
forgets its permission guard silently stopped looking at 97% of the API. Nothing failed;
the check simply had almost nothing left to check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

#: No authentication at all.
PUBLIC_ROUTES: dict[str, str] = {
    "/healthz": "liveness probe; returns no data",
    "/readyz": "readiness probe; reports only reachability and migration head",
    "/api/v1/openapi.json": "contract document; authenticated at the proxy in production",
    "/api/v1/docs": "disabled in production via BM_ENABLE_API_DOCS",
    "/docs/oauth2-redirect": "docs support route; mounted at the root by FastAPI",
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
    # Publishes which operation types may be queued offline, and that financial
    # operations are refused (ADR-011). It is a statement about the protocol, not about
    # the business's data, and the client needs it to explain the refusal rather than
    # invent a reason.
    "/api/v1/sync/operation-types": "the offline protocol's own rules",
    # Notices about the business the caller is already signed in to. Gating "stock has
    # run out" behind a reports permission would mean the person at the counter is the
    # one person who cannot be told (M26).
    "/api/v1/notifications": "the caller's own business's notices",
    "/api/v1/notifications/{notification_id}:read": "marks one of those notices read",
}


@dataclass(frozen=True, slots=True)
class MountedRoute:
    """One route as actually mounted, at its effective (prefix-applied) path.

    `dependant` is FastAPI's resolved dependency tree, which is where `require()` leaves
    its marker. It is `None` for routes that are not API routes — the docs and
    `openapi.json` endpoints are plain Starlette routes and carry no dependency tree.

    `endpoint` is the handler function, kept so the structural tests can read its
    signature: parameter order decides whether authorisation runs before body validation.
    """

    path: str
    methods: frozenset[str]
    dependant: Any | None
    endpoint: Any | None


def _visit(routes: object, found: list[MountedRoute], seen: set[int]) -> None:
    if not isinstance(routes, (list, tuple)):
        return
    for route in routes:
        if id(route) in seen:
            continue
        seen.add(id(route))

        # FastAPI >= 0.141: an inclusion marker. `effective_route_contexts()` is the only
        # way to the children, and it already resolves nested inclusions and applies every
        # prefix, so the 15 sub-routers of the v1 router arrive here fully flattened.
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            for ctx in contexts():
                path = getattr(ctx, "path", None)
                if isinstance(path, str):
                    found.append(
                        MountedRoute(
                            path=path,
                            methods=frozenset(getattr(ctx, "methods", None) or ()),
                            dependant=getattr(ctx, "dependant", None),
                            endpoint=getattr(ctx, "endpoint", None),
                        )
                    )
            continue

        path = getattr(route, "path", None)
        if isinstance(path, str):
            found.append(
                MountedRoute(
                    path=path,
                    methods=frozenset(getattr(route, "methods", None) or ()),
                    dependant=getattr(route, "dependant", None),
                    endpoint=getattr(route, "endpoint", None),
                )
            )

        # Sub-applications and mounts, and the pre-0.141 flattened shape. Harmless on
        # 0.141 (nothing else exposes `.routes`) and keeps a future bump from silently
        # narrowing the walk again.
        _visit(getattr(route, "routes", None), found, seen)


def mounted_routes(app: FastAPI) -> list[MountedRoute]:
    """Every route reachable from `app`, at the path a client would actually call.

    Deliberately duck-typed rather than `isinstance`-based: `_IncludedRouter` and
    `_EffectiveRouteContext` are private FastAPI names, and a test that imports them
    breaks on upgrade in the one way that matters least. Asking whether a route can
    enumerate its own children survives the rename; what it cannot survive is the
    children disappearing, which is what the count assertions in the tests are for.
    """
    found: list[MountedRoute] = []
    _visit(app.routes, found, set())
    return found
