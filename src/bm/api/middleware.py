"""Request-id correlation, structured request logging, body cap, security headers.

Order matters and is set in `main.py`: correlation first (so everything downstream can
log an id), then logging, then the body cap.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from bm.api.errors import error_body
from bm.domain.shared.errors import ErrorCode
from bm.infra.logging.setup import (
    branch_id_var,
    business_id_var,
    request_id_var,
    user_id_var,
)

logger = logging.getLogger("bm.request")

_MAX_REQUEST_ID_LENGTH = 64


def _clean_request_id(raw: str | None) -> str:
    """Accept a well-formed client id, otherwise generate one (`ARCHITECTURE.md §6.3`).

    The value is echoed in logs and in error bodies, so an unvalidated one would be a
    log-injection vector.
    """
    if raw and len(raw) <= _MAX_REQUEST_ID_LENGTH and raw.replace("-", "").isalnum():
        return raw
    return str(uuid.uuid4())


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Binds the request id (and later the auth identity) into the logging context."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _clean_request_id(request.headers.get("X-Request-Id"))
        token = request_id_var.set(request_id)
        user_token = user_id_var.set(None)
        business_token = business_id_var.set(None)
        branch_token = branch_id_var.set(None)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
            user_id_var.reset(user_token)
            business_id_var.reset(business_token)
            branch_id_var.reset(branch_token)
        response.headers["X-Request-Id"] = request_id
        return response


class RequestLogMiddleware(BaseHTTPMiddleware):
    """One JSON line per request: route, status, duration. No bodies, no headers."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "request_failed",
                extra={
                    "method": request.method,
                    "route": request.url.path,
                    "status": 500,
                    "duration_ms": duration_ms,
                },
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "request",
            extra={
                "method": request.method,
                "route": request.scope.get("route").path  # type: ignore[union-attr]
                if request.scope.get("route") is not None
                else request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Global 1 MB body cap (`SECURITY.md §6`), except the upload route.

    Uploads are capped separately with a streaming byte counter so an attacker-controlled
    blob is never buffered.
    """

    def __init__(self, app: object, *, max_bytes: int, exempt_prefixes: tuple[str, ...]) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.max_bytes = max_bytes
        self.exempt_prefixes = exempt_prefixes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not any(request.url.path.startswith(p) for p in self.exempt_prefixes):
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self.max_bytes:
                return JSONResponse(
                    status_code=413,
                    content=error_body(ErrorCode.FILE_TOO_LARGE, "This request is too large."),
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """`SECURITY.md §6`. The API serves JSON only, so the CSP can be maximally strict."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        response.headers.setdefault("Cache-Control", "no-store")
        return response
