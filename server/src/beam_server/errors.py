"""Domain exceptions and the uniform error envelope.

Every error the API returns has the shape documented in docs/protocol.md:

    {"error": {"code": "...", "message": "...", "details": {...}, "request_id": "..."}}

Routers and services raise `BeamError` subclasses; they never build JSON error bodies
themselves and never let a raw exception reach the client with a stack trace.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from beam_server.observability.middleware import get_request_id

logger = structlog.get_logger(__name__)


class BeamError(Exception):
    """Base class for domain errors that map to a specific HTTP status and error code."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(BeamError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ConflictError(BeamError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class RateLimitedError(BeamError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"

    def __init__(
        self,
        message: str,
        *,
        retry_after: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.retry_after = retry_after


class UnauthorizedError(BeamError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"


class NotReadyError(BeamError):
    """Raised when a dependency (e.g. Redis) required for /ready is unreachable."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "NOT_READY"


def _envelope(
    code: str, message: str, details: dict[str, Any], request_id: str | None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Install exception handlers that translate every error into the uniform envelope."""

    @app.exception_handler(BeamError)
    async def _beam_error_handler(request: Request, exc: BeamError) -> JSONResponse:
        request_id = get_request_id()
        headers = {}
        if isinstance(exc, RateLimitedError):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details, request_id),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = get_request_id()
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_envelope(
                "VALIDATION_ERROR",
                "The request could not be validated.",
                {"errors": exc.errors()},
                request_id,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        request_id = get_request_id()
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, str(exc.detail), {}, request_id),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = get_request_id()
        logger.error(
            "unhandled_exception",
            path=request.url.path,
            exc_info=exc,
            request_id=request_id,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(
                "INTERNAL_ERROR",
                "An unexpected error occurred.",
                {},
                request_id,
            ),
        )


# Quiet uvicorn's own access logger; request logging is handled by our middleware
# with structured fields (see observability/middleware.py) instead of plain text lines.
logging.getLogger("uvicorn.access").disabled = True
