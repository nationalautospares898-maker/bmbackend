"""Exception handlers that produce the one documented error envelope.

`docs/API-SPECIFICATION.md §4`: **every** non-2xx response carries
`{"error": {code, message, details, request_id, retryable}}`. No endpoint returns a bare
500 or a stack trace; an unexpected error becomes `INTERNAL_ERROR` carrying the request
id so a counter can read it out over the phone.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from bm.domain.shared.errors import DomainError, ErrorCode
from bm.infra.logging.setup import request_id_var

logger = logging.getLogger(__name__)


def error_body(
    code: ErrorCode | str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "error": {
            "code": str(code),
            "message": message,
            "details": details or {},
            "request_id": request_id or request_id_var.get() or "",
            "retryable": retryable,
        }
    }


def _response(
    status_code: int,
    code: ErrorCode | str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(code, message, details=details, retryable=retryable),
        headers=headers,
    )


async def domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    headers: dict[str, str] = {}
    if exc.status_code == 429:
        retry_after = exc.details.get("retry_after_seconds", 60)
        headers["Retry-After"] = str(int(retry_after))
    logger.info(
        "domain_error",
        extra={"error_code": str(exc.code), "status": exc.status_code},
    )
    return _response(
        exc.status_code,
        exc.code,
        exc.message,
        details=exc.details,
        retryable=exc.retryable,
        headers=headers or None,
    )


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Pydantic rejection → `422 VALIDATION_ERROR` with per-field messages.

    The frontend maps `details.fields` straight back onto the form, so the key has to be
    the field path the client sent, not pydantic's internal location tuple.
    """
    assert isinstance(exc, RequestValidationError)
    fields: dict[str, str] = {}
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ()) if part not in ("body", "query")]
        key = ".".join(location) or "body"
        fields.setdefault(key, error.get("msg", "Invalid value"))
    return _response(
        422,
        ErrorCode.VALIDATION_ERROR,
        "Some values need correcting.",
        details={"fields": fields},
    )


async def http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Starlette's own errors (404 on an unknown path, 405, body too large) still have
    to come out in the project envelope rather than FastAPI's `{"detail": …}`."""
    assert isinstance(exc, StarletteHTTPException)
    mapping: dict[int, tuple[ErrorCode, str]] = {
        400: (ErrorCode.VALIDATION_ERROR, "The request could not be read."),
        401: (ErrorCode.TOKEN_EXPIRED, "Please sign in again."),
        403: (ErrorCode.PERMISSION_DENIED, "You do not have permission to do this."),
        404: (ErrorCode.NOT_FOUND, "Not found."),
        405: (ErrorCode.VALIDATION_ERROR, "That action is not available here."),
        413: (ErrorCode.FILE_TOO_LARGE, "This upload is too large."),
        415: (ErrorCode.UNSUPPORTED_FILE_TYPE, "That file type is not supported."),
        429: (ErrorCode.RATE_LIMITED, "Too many requests. Please wait a moment."),
        503: (ErrorCode.SERVICE_UNAVAILABLE, "The service is briefly unavailable."),
    }
    code, message = mapping.get(exc.status_code, (ErrorCode.INTERNAL_ERROR, "Request failed."))
    detail = exc.detail if isinstance(exc.detail, str) else None
    return _response(
        exc.status_code,
        code,
        detail or message,
        retryable=exc.status_code in (429, 503),
        headers=dict(exc.headers) if exc.headers else None,
    )


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Last resort. The message is generic on purpose: no SQL, no paths, no ids."""
    logger.exception("unhandled_error", exc_info=exc)
    return _response(
        500,
        ErrorCode.INTERNAL_ERROR,
        "Something went wrong. Nothing was saved. Please try again.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
