"""Request dependencies: database session, auth context, permission guard.

`require()` is how a route **declares** its permission. The marker it leaves on the
dependency is what `tests/structural/test_route_permissions.py` walks, so a route with
no declared permission and no entry in `route_policy.py` fails CI
(`SECURITY.md §3.1` rule 1, M1 acceptance).

The guard is the first gate, not the only one: services re-assert anything destructive
through `AuthContext.require` so an internal caller cannot bypass the router.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from bm.config import Settings, get_settings
from bm.domain.auth.permissions import Permission
from bm.domain.auth.service import AuthConfig, AuthService
from bm.domain.auth.tokens import verify_access_token
from bm.domain.shared.clock import Clock, SystemClock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    PermissionDenied,
    TokenExpired,
    ValidationFailed,
)
from bm.infra.db.session import read_session, transaction
from bm.infra.logging.setup import branch_id_var, business_id_var, user_id_var

#: Attribute name the structural test looks for.
PERMISSION_MARKER = "__bm_required_permission__"

#: Routes that authenticate but need no specific permission mark themselves with this.
ANY_AUTHENTICATED = "__any_authenticated__"


# --- infrastructure dependencies ---------------------------------------------


def settings_dep(request: Request) -> Settings:
    """The settings the application was **created with**, not a fresh environment read.

    Reading `get_settings()` here would silently ignore whatever was passed to
    `create_app`, so an app configured explicitly would authenticate against a
    different pepper than the one it was given. The process-wide cache is only the
    fallback for a context with no app attached.
    """
    configured = getattr(request.app.state, "settings", None)
    if isinstance(configured, Settings):
        return configured
    return get_settings()


def clock_dep() -> Clock:
    return SystemClock()


def db_write() -> Iterator[Session]:
    """One request, one transaction. Committed on success, rolled back on any
    exception (`ARCHITECTURE.md §5.1`)."""
    with transaction() as session:
        yield session


def db_read() -> Iterator[Session]:
    """Read-only. Rolls back rather than committing, so a stray flush during a `GET`
    cannot persist anything."""
    with read_session() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(settings_dep)]
ClockDep = Annotated[Clock, Depends(clock_dep)]
WriteSession = Annotated[Session, Depends(db_write)]
ReadSession = Annotated[Session, Depends(db_read)]


def auth_config(settings: SettingsDep) -> AuthConfig:
    return AuthConfig(
        jwt_signing_key=settings.jwt_signing_key.get_secret_value(),
        jwt_previous_key=(
            settings.jwt_previous_key.get_secret_value() if settings.jwt_previous_key else None
        ),
        access_token_ttl_seconds=settings.access_token_ttl_seconds,
        refresh_token_ttl_days=settings.refresh_token_ttl_days,
        password_pepper=settings.password_pepper.get_secret_value(),
        ip_hash_pepper=settings.ip_hash_pepper.get_secret_value(),
    )


AuthConfigDep = Annotated[AuthConfig, Depends(auth_config)]


def auth_service(session: WriteSession, config: AuthConfigDep, clock: ClockDep) -> AuthService:
    return AuthService(session, config, clock)


AuthServiceDep = Annotated[AuthService, Depends(auth_service)]


# --- identity ----------------------------------------------------------------


def client_ip(request: Request) -> str | None:
    """Trust the proxy's forwarded address only for its first hop.

    Reading the whole `X-Forwarded-For` chain would let a client prepend any address and
    evade per-IP rate limiting.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise TokenExpired("Please sign in.")
    token = authorization[7:].strip()
    if not token:
        raise TokenExpired("Please sign in.")
    return token


def current_context(
    request: Request,
    session: ReadSession,
    config: AuthConfigDep,
    clock: ClockDep,
    authorization: Annotated[str | None, Header()] = None,
    x_device_id: Annotated[str | None, Header()] = None,
    x_counter_id: Annotated[str | None, Header()] = None,
) -> AuthContext:
    """Verify the access token and hand the claims to the domain for resolution.

    The device check (registered, not revoked) happens on **every** authenticated
    request, but it lives in `AuthService.resolve_context` — the API layer must not
    reach into a repository (`ARCHITECTURE.md §3` rule 1).
    """
    claims = verify_access_token(
        _bearer_token(authorization),
        signing_key=config.jwt_signing_key,
        previous_key=config.jwt_previous_key,
    )

    ctx = AuthService(session, config, clock).resolve_context(
        claims,
        device_id=_parse_uuid_header(x_device_id, "X-Device-Id"),
        counter_id=_parse_uuid_header(x_counter_id, "X-Counter-Id"),
    )

    # Bind the identity into the logging context so every log line for this request
    # carries it without each call site passing it along.
    user_id_var.set(str(ctx.user_id))
    business_id_var.set(str(ctx.business_id))
    branch_id_var.set(str(ctx.branch_id) if ctx.branch_id else None)
    request.state.auth = ctx

    return ctx


def _parse_uuid_header(value: str | None, header_name: str) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationFailed({header_name: "Must be a UUID."}) from exc


CurrentContext = Annotated[AuthContext, Depends(current_context)]


# --- permission guard --------------------------------------------------------


def require(permission: Permission) -> Callable[..., AuthContext]:
    """Declare a route's required permission.

    Also enforces `must_change_password`: a user who has not rotated a seeded
    credential is refused every non-auth endpoint (`SECURITY.md §2.1`). That check
    lives here rather than in middleware so the handful of auth routes that must stay
    reachable — change-password, logout, `me` — simply do not use this guard.
    """

    def guard(ctx: CurrentContext) -> AuthContext:
        if ctx.must_change_password:
            raise PermissionDenied(str(permission), "Change your password before using the system.")
        if not ctx.has(permission):
            raise PermissionDenied(str(permission))
        return ctx

    setattr(guard, PERMISSION_MARKER, str(permission))
    return guard


def require_any(*permissions: Permission) -> Callable[..., AuthContext]:
    """For the few endpoints the contract grants to either of two permissions —
    `GET /customers/search` is `customer.manage` **or** `credit.read`."""

    def guard(ctx: CurrentContext) -> AuthContext:
        if ctx.must_change_password:
            raise PermissionDenied(
                str(permissions[0]), "Change your password before using the system."
            )
        if not any(ctx.has(p) for p in permissions):
            raise PermissionDenied(
                str(permissions[0]),
                "You do not have permission to do this.",
            )
        return ctx

    setattr(guard, PERMISSION_MARKER, "|".join(str(p) for p in permissions))
    return guard


def authenticated_only() -> Callable[..., AuthContext]:
    """Marks a route as authenticated with no specific permission.

    Its path must also appear in `route_policy.AUTHENTICATED_ONLY_ROUTES`, so the
    exemption is recorded in two places and cannot be granted by accident.
    """

    def guard(ctx: CurrentContext) -> AuthContext:
        return ctx

    setattr(guard, PERMISSION_MARKER, ANY_AUTHENTICATED)
    return guard


# --- idempotency -------------------------------------------------------------


def idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> uuid.UUID:
    """Required on every mutating financial endpoint (`API-SPECIFICATION.md §5`).

    A missing key is `422 VALIDATION_ERROR` with `fields: {"Idempotency-Key":
    "required"}` — never a silent pass, because an unkeyed retry is how a duplicate
    invoice gets created.
    """
    if not idempotency_key:
        raise ValidationFailed({"Idempotency-Key": "required"})
    try:
        return uuid.UUID(idempotency_key)
    except ValueError as exc:
        raise ValidationFailed({"Idempotency-Key": "Must be a UUID."}) from exc


IdempotencyKey = Annotated[uuid.UUID, Depends(idempotency_key)]
