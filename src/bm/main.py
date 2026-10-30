"""FastAPI application factory.

Operational contract (`ARCHITECTURE.md §13`):

* `/healthz`  — liveness: the process responds.
* `/readyz`   — readiness: the database is reachable **and** the Alembic head matches
  the code. A replica with a pending migration must not take traffic (ADR-016).
* `/api/v1/openapi.json` — the contract the frontend generates its types from.
* `/api/v1/meta` — API version, server time, tax mode, feature flags.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from bm.api.deps import authenticated_only
from bm.api.errors import register_exception_handlers
from bm.api.middleware import (
    BodySizeLimitMiddleware,
    CorrelationMiddleware,
    RequestLogMiddleware,
    SecurityHeadersMiddleware,
)
from bm.config import Settings, get_settings
from bm.domain.shared.context import AuthContext
from bm.infra.db.migration_state import migration_state
from bm.infra.db.session import (
    database_reachable,
    dispose_engine,
    init_engine,
    read_session,
)
from bm.infra.logging.setup import configure_logging

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
API_VERSION = "1.0.0"


def _startup_self_checks(settings: Settings) -> None:
    """Emit the self-checks from `ARCHITECTURE.md §13` without leaking secrets.

    These are warnings, not failures: an unconfigured business identity blocks go-live,
    not startup. Refusing to boot would leave the owner unable to log in and fix it.
    """
    if settings.is_production:
        if settings.enable_api_docs:
            logger.warning("startup_check", extra={"check": "api_docs_enabled_in_production"})
        if not settings.cors_origins:
            logger.warning("startup_check", extra={"check": "cors_allow_list_empty"})
    logger.info(
        "startup",
        extra={
            "env": settings.env,
            "release": settings.release,
            "storage_backend": settings.storage_backend,
            # tax.mode is a database setting, reported by /api/v1/meta rather than here.
        },
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    init_engine(settings)
    _startup_self_checks(settings)
    try:
        yield
    finally:
        dispose_engine()
        logger.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="National Auto Spares — Billing & Inventory API",
        version=API_VERSION,
        docs_url=f"{API_PREFIX}/docs" if settings.enable_api_docs else None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
        # Pydantic models declare their own examples; FastAPI's default 422 schema is
        # replaced by the project envelope in api/errors.py.
        responses={},
    )
    app.state.settings = settings

    # Outermost first.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_body_bytes,
        exempt_prefixes=(f"{API_PREFIX}/imports",),
    )
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(CorrelationMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,  # explicit list, never a wildcard
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-Request-Id",
                "X-Device-Id",
                "X-Counter-Id",
                "Idempotency-Key",
                "If-Match",
            ],
            expose_headers=["X-Request-Id", "Idempotent-Replay", "X-Api-Version"],
        )

    register_exception_handlers(app)
    _register_operational_routes(app)
    _register_v1_routers(app)
    return app


def _register_operational_routes(app: FastAPI) -> None:
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        """Liveness only. Deliberately does not touch the database: a database blip
        must not cause the orchestrator to kill a healthy process."""
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        checks: dict[str, Any] = {}
        with read_session() as session:
            db_ok = database_reachable(session)
            checks["database"] = "ok" if db_ok else "unreachable"
            if db_ok:
                state = migration_state(session)
                checks["migrations"] = {
                    "expected_head": state.expected_head,
                    "current_revision": state.current_revision,
                    "status": "ok" if state.matches else "mismatch",
                }
                ready = state.matches
            else:
                ready = False
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "checks": checks},
        )


def _register_v1_routers(app: FastAPI) -> None:
    """Mount the versioned API.

    Routers are added as their milestones land. `api/v1/__init__.py` holds the single
    list so the route-permission structural test has one place to enumerate.
    """
    from bm.api.v1 import build_v1_router

    app.include_router(build_v1_router(), prefix=API_PREFIX)

    @app.get(f"{API_PREFIX}/meta", tags=["operational"])
    def meta(
        _ctx: Annotated[AuthContext, Depends(authenticated_only())],
    ) -> dict[str, Any]:
        """Authenticated, but needs no specific permission: it reports the server's own
        version and time to whoever is signed in."""
        settings: Settings = app.state.settings
        return {
            "api_version": API_VERSION,
            "release": settings.release,
            "server_time": datetime.now(UTC).isoformat(),
            "environment": settings.env,
        }


app_factory = create_app
