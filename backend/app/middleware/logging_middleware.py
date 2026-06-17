"""Request-scoped logging middleware.

Generates (or accepts) an `X-Request-ID` for every request, binds it to the
`request_id_var` contextvar for the duration of the request, and emits a
single structured log line at response time with method, path, status,
and duration.
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.logging_context import (
    agent_id_var,
    bind_request_context,
    reset_request_context,
)

logger = logging.getLogger("spaider.request")

_REQUEST_ID_HEADER = "X-Request-ID"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(_REQUEST_ID_HEADER, "").strip()
        request_id = incoming or uuid.uuid4().hex
        request.state.request_id = request_id

        # Each HTTP request runs in its own asyncio task with a fresh context
        # copy, so agent_id starts at its default (None). `require_auth` sets
        # it on success.
        tokens = bind_request_context(request_id=request_id)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[_REQUEST_ID_HEADER] = request_id
            return response
        except Exception:
            # An unhandled error escaped the app. Still return a response that
            # carries the request id so the 500 is traceable, and log the
            # traceback so nothing is lost.
            status_code = 500
            logger.exception("unhandled error during request")
            response = Response("Internal Server Error", status_code=500)
            response.headers[_REQUEST_ID_HEADER] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            # `request.state` is shared via the ASGI scope, so it's visible
            # here even when BaseHTTPMiddleware runs in a different task than
            # the endpoint that set it (via `require_auth`).
            agent_id = getattr(request.state, "agent_id", None) or agent_id_var.get()
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "request_id": request_id,
                    "agent_id": agent_id,
                },
            )
            reset_request_context(tokens)
