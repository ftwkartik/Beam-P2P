"""Request-ID propagation and structured access logging.

Every request gets a UUID, exposed to handlers via a contextvar (so error handlers and
deeply nested calls can attach it to logs and error envelopes without threading it
through every function signature) and returned to the client as `X-Request-ID`, which
is invaluable for correlating a user's bug report with server logs.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = structlog.get_logger(__name__)

_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Return the request ID for the request currently being handled, if any."""
    return _request_id_ctx.get()


def _internal_error_envelope(request_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred.",
            "details": {},
            "request_id": request_id,
        }
    }


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request ID, binds it to structlog's contextvars, and logs completion.

    This is also the last line of defense against leaking a stack trace: FastAPI's own
    `@app.exception_handler(Exception)` (see errors.py) is registered on Starlette's
    ServerErrorMiddleware, but that layer's interaction with a `BaseHTTPMiddleware`
    subclass like this one does not reliably intercept exceptions raised downstream in
    every Starlette release (see server/tests/unit/test_errors.py for the regression
    test). Catching here, at the one middleware every request passes through, is what
    actually guarantees a clean envelope instead of a bare 500 with no body.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = str(uuid.uuid4())
        token = _request_id_ctx.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                duration_ms = round((time.perf_counter() - start) * 1000, 2)
                logger.error(
                    "unhandled_exception",
                    method=request.method,
                    path=request.url.path,
                    duration_ms=duration_ms,
                    exc_info=exc,
                )
                response = JSONResponse(
                    status_code=500,
                    content=_internal_error_envelope(request_id),
                )
            else:
                duration_ms = round((time.perf_counter() - start) * 1000, 2)
                logger.info(
                    "request_completed",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
            _request_id_ctx.reset(token)
