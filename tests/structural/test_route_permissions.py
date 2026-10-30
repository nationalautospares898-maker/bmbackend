"""M1/M3 acceptance — every route declares a permission, or is explicitly public.

`SECURITY.md §3.1` rule 1: a route with no declared permission **fails CI**. This is the
test that enforces it, and it is why the whole permission-guard mechanism leaves a
marker on the dependency rather than just raising inside the handler.

A new endpoint added without a guard fails here. That is the point: forgetting the guard
is silent at runtime (the endpoint simply works for everyone), so it has to be loud at
build time.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from bm.api.deps import ANY_AUTHENTICATED, PERMISSION_MARKER
from bm.api.route_policy import AUTHENTICATED_ONLY_ROUTES, PUBLIC_ROUTES
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


def _declared_permission(route: APIRoute) -> str | None:
    """The marker `require()` / `authenticated_only()` leaves on the dependency."""
    for dependency in route.dependant.dependencies:
        call = dependency.call
        if call is not None and hasattr(call, PERMISSION_MARKER):
            return str(getattr(call, PERMISSION_MARKER))
    return None


def _api_routes(app: FastAPI) -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


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
    # Every mounted route, not only APIRoute: the docs and openapi endpoints are
    # plain Starlette routes and are legitimately listed as public.
    existing = {getattr(route, "path", "") for route in app.routes}
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
    """
    schema = app.openapi()
    offenders: list[str] = []
    for name, component in (schema.get("components", {}).get("schemas", {})).items():
        if not name.endswith(("Request", "Input")):
            continue
        for field in component.get("properties") or {}:
            if field in {"payment_status", "amount_paid", "is_paid", "paid"}:
                offenders.append(f"{name}.{field}")
    assert offenders == [], f"Derived payment fields must never be settable: {offenders}"


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
