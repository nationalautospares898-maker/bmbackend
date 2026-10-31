"""M1/M3 acceptance — every route declares a permission, or is explicitly public.

`SECURITY.md §3.1` rule 1: a route with no declared permission **fails CI**. This is the
test that enforces it, and it is why the whole permission-guard mechanism leaves a
marker on the dependency rather than just raising inside the handler.

A new endpoint added without a guard fails here. That is the point: forgetting the guard
is silent at runtime (the endpoint simply works for everyone), so it has to be loud at
build time.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi import FastAPI

from bm.api.deps import ANY_AUTHENTICATED, PERMISSION_MARKER
from bm.api.route_policy import (
    AUTHENTICATED_ONLY_ROUTES,
    PUBLIC_ROUTES,
    MountedRoute,
    mounted_routes,
)
from bm.config import Settings
from bm.domain.auth.permissions import Permission
from bm.main import create_app


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_app(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            env="test",
            database_url="postgresql+psycopg://bm_app:pw@localhost:5432/bm",
            jwt_signing_key="a" * 40,
            password_pepper="b" * 40,
            ip_hash_pepper="c" * 40,
            enable_api_docs=True,  # so the docs routes are present and get checked
        )
    )


def _declared_permission(route: MountedRoute) -> str | None:
    """The marker `require()` / `authenticated_only()` leaves on the dependency."""
    if route.dependant is None:
        return None
    for dependency in route.dependant.dependencies:
        call = dependency.call
        if call is not None and hasattr(call, PERMISSION_MARKER):
            return str(getattr(call, PERMISSION_MARKER))
    return None


def _api_routes(app: FastAPI) -> list[MountedRoute]:
    """Routes carrying a dependency tree, i.e. everything except the docs endpoints.

    `mounted_routes` rather than a flat scan of `app.routes`: see the note in
    `bm.api.route_policy`. A flat `isinstance(r, APIRoute)` filter returns 3 of 90 on
    FastAPI 0.141, which turns every assertion in this file into a tautology.
    """
    return [r for r in mounted_routes(app) if r.dependant is not None]


def test_the_route_walk_sees_the_whole_api(app: FastAPI) -> None:
    """The anti-vacuity guard for every other test in this file.

    Each one below is of the form "no route does X". That shape passes trivially when the
    walk returns nothing, which is exactly what happened when FastAPI 0.141 stopped
    flattening included routers. A count floor is crude, but it is the one assertion that
    cannot be satisfied by an empty enumeration — and it fails loudly on the upgrade that
    would otherwise quietly disarm the suite.
    """
    guarded = _api_routes(app)
    assert len(guarded) >= 80, (
        f"Only {len(guarded)} routes were found; this file's guarantees are vacuous. "
        "bm.api.route_policy.mounted_routes is no longer reaching the included routers."
    )

    # Spot-check one route from each of three different included sub-routers, so a walk
    # that finds *a* router but silently drops the rest also fails.
    paths = {r.path for r in guarded}
    for expected in (
        "/api/v1/auth/login",
        "/api/v1/admin/devices",
        "/api/v1/products/search",
    ):
        assert expected in paths, f"{expected} was not reached by the route walk"


def test_every_route_declares_a_permission_or_is_listed_as_public(app: FastAPI) -> None:
    undeclared: list[str] = []
    for route in _api_routes(app):
        if route.path in PUBLIC_ROUTES:
            continue
        if _declared_permission(route) is None:
            undeclared.append(f"{sorted(route.methods or [])} {route.path}")

    assert undeclared == [], (
        "These routes declare no permission and are not listed in "
        "bm.api.route_policy.PUBLIC_ROUTES:\n  " + "\n  ".join(undeclared)
    )


def test_authenticated_only_routes_are_listed_in_both_places(app: FastAPI) -> None:
    """A route may skip a *specific* permission only if the policy file agrees.

    Two independent records are required so the exemption cannot be granted by one
    careless edit.
    """
    marked = {
        route.path for route in _api_routes(app) if _declared_permission(route) == ANY_AUTHENTICATED
    }
    listed = set(AUTHENTICATED_ONLY_ROUTES)

    assert marked <= listed, (
        "Marked authenticated-only but not listed in AUTHENTICATED_ONLY_ROUTES: "
        f"{sorted(marked - listed)}"
    )


def test_declared_permissions_exist_in_the_vocabulary(app: FastAPI) -> None:
    """A permission string that is not in the enum grants nothing and silently
    locks everyone out of that endpoint."""
    known = {str(p) for p in Permission}
    unknown: list[str] = []
    for route in _api_routes(app):
        declared = _declared_permission(route)
        if declared is None or declared == ANY_AUTHENTICATED:
            continue
        for part in declared.split("|"):
            if part not in known:
                unknown.append(f"{route.path} -> {part}")
    assert unknown == [], f"Unknown permission strings: {unknown}"


def test_public_route_list_has_no_stale_entries(app: FastAPI) -> None:
    """A path that no longer exists but is still listed as public is how an exemption
    gets inherited by a future route with the same path."""
    # Every mounted route, not only the API ones: the docs and openapi endpoints are
    # plain Starlette routes and are legitimately listed as public.
    existing = {route.path for route in mounted_routes(app)}
    stale = sorted(set(PUBLIC_ROUTES) - existing)
    assert stale == [], f"PUBLIC_ROUTES lists paths that no longer exist: {stale}"


def test_no_route_mutates_a_finalised_financial_record(app: FastAPI) -> None:
    """ADR-015 / FR-SALE-010, asserted against the real route table.

    There is deliberately no `PUT`/`PATCH`/`DELETE` on invoices, invoice items,
    payments, stock movements or audit logs. Corrections are cancellation, return,
    credit note or reversal.
    """
    forbidden_segments = (
        "/invoices/{invoice_id}",
        "/invoice-items",
        "/payments/{payment_id}",
        "/stock-movements",
        "/audit-logs/{",
    )
    offenders: list[str] = []
    for route in _api_routes(app):
        methods = set(route.methods or ())
        if not methods & {"PUT", "PATCH", "DELETE"}:
            continue
        if any(route.path.endswith(segment) for segment in forbidden_segments):
            offenders.append(f"{sorted(methods)} {route.path}")
    assert offenders == [], f"Finalised records must be immutable: {offenders}"


def test_no_endpoint_sets_payment_status_or_amount_paid(app: FastAPI) -> None:
    """ADR-022. `payment_status` and `amount_paid` are derived, written only in the
    same transaction as the allocations. There is no "mark as paid" endpoint, and no
    paid checkbox anywhere in this system.

    The schema is scanned rather than the route paths, because the risk is a request
    *field* appearing, not a route named after it.

    Request schemas are found by following every operation's `requestBody`, not by
    guessing from the name: Pydantic emits a `…Out-Input` variant for any **response**
    model carrying a serialiser, and a name-based check flagged those — reporting an
    `amount_paid` that is only ever read.
    """
    schema = app.openapi()
    components: dict[str, Any] = schema.get("components", {}).get("schemas", {})

    def referenced(node: Any, seen: set[str]) -> set[str]:
        """Every schema name reachable from a node, following `$ref` transitively."""
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[-1]
                if name not in seen:
                    seen.add(name)
                    referenced(components.get(name, {}), seen)
            for value in node.values():
                referenced(value, seen)
        elif isinstance(node, list):
            for value in node:
                referenced(value, seen)
        return seen

    request_schemas: set[str] = set()
    for operations in (schema.get("paths") or {}).values():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            body = operation.get("requestBody")
            if body:
                referenced(body, request_schemas)

    offenders: list[str] = []
    for name in sorted(request_schemas):
        for field in components.get(name, {}).get("properties") or {}:
            if field in {"payment_status", "amount_paid", "is_paid", "paid"}:
                offenders.append(f"{name}.{field}")
    assert offenders == [], f"Derived payment fields must never be settable: {offenders}"


def test_the_settable_field_scan_actually_covers_the_request_bodies(app: FastAPI) -> None:
    """The test above is only meaningful if it finds the request schemas at all.

    A `$ref`-following scan that silently matched nothing would pass forever. This
    asserts it reaches the finalisation body — the one request where a settable
    `amount_paid` would do the most damage.
    """
    schema = app.openapi()
    components: dict[str, Any] = schema.get("components", {}).get("schemas", {})
    found: set[str] = set()
    for operations in (schema.get("paths") or {}).values():
        for operation in operations.values():
            if isinstance(operation, dict) and operation.get("requestBody"):
                for ref in str(operation["requestBody"]).split("'"):
                    if ref.startswith("#/components/schemas/"):
                        found.add(ref.rsplit("/", 1)[-1])
    assert "FinalizeRequest" in found
    assert "PaymentRequest" in found
    assert "FinalizeRequest" in components


def test_no_second_finalisation_path(app: FastAPI) -> None:
    """M10's hard rule, asserted structurally: cash and credit finalise through the
    same endpoint. No "prepare credit" or "credit details" endpoint exists."""
    paths = [route.path.lower() for route in _api_routes(app)]
    offenders = [
        path
        for path in paths
        if any(
            fragment in path
            for fragment in ("prepare-credit", "credit-details", "creditdetails", "finalize-credit")
        )
    ]
    assert offenders == [], f"There is exactly one finalisation path: {offenders}"


def test_authorisation_is_decided_before_the_request_is_validated(app: FastAPI) -> None:
    """A caller without permission must see a refusal, not a validation error.

    FastAPI solves dependencies in **parameter order**, so a route that declared
    `idempotency_key: IdempotencyKey` before its permission-guarded `ctx` answered
    `422 VALIDATION_ERROR` to a caller who should have seen `403 PERMISSION_DENIED` —
    the permission check was never reached.

    That is not cosmetic. It tells an unauthorised caller about the request schema, and
    it means `test_every_permissioned_route_refuses_a_role_without_it` can be satisfied
    by a route that does not actually refuse. Parameter order is invisible on review,
    so it is asserted here instead.
    """
    offenders: list[str] = []
    for route in _api_routes(app):
        parameters = list(inspect.signature(route.endpoint).parameters)
        if "idempotency_key" not in parameters:
            continue
        if "ctx" not in parameters:
            offenders.append(f"{sorted(route.methods or ())} {route.path} has no ctx")
        elif parameters.index("ctx") > parameters.index("idempotency_key"):
            offenders.append(f"{sorted(route.methods or ())} {route.path}")
    assert offenders == [], (
        f"The permission dependency must precede the idempotency-key header check: {offenders}"
    )
