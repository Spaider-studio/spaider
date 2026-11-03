"""FastAPI application factory: token auth on every route, restricted CORS,
uniform error bodies, routers."""

from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..context import AppContext
from ..models.jobs import JobError
from ..store.common import NotFound
from .errors import ApiError
from .routes_activity import router as activity_router
from .routes_agent import router as agent_router
from .routes_conversations import router as conversations_router
from .routes_documents import router as documents_router
from .routes_live import router as live_router
from .routes_memory import router as memory_router
from .routes_models import router as models_router
from .routes_system import router as system_router

logger = logging.getLogger(__name__)

TOKEN_HEADER = "x-spaider-token"
ALLOWED_ORIGINS = ["null", "http://localhost:5173", "http://127.0.0.1:5173"]


def create_app(ctx: AppContext, token: str) -> FastAPI:
    app = FastAPI(title="SpAIder Foldable Core", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-SpAIder-Token"],
    )

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        provided = request.headers.get(TOKEN_HEADER, "")
        if not provided or not hmac.compare_digest(provided.encode(), token.encode()):
            return JSONResponse({"detail": "Nicht autorisiert.", "code": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound):
        return JSONResponse({"detail": "Nicht gefunden.", "code": "not_found"}, status_code=404)

    @app.exception_handler(JobError)
    async def _job_error(_: Request, exc: JobError):
        status = 404 if exc.code == "not_found" else 409
        return JSONResponse({"detail": exc.detail, "code": exc.code, **exc.extra}, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse(
            {"detail": "Ungültige Anfrage.", "code": "invalid_request", "errors": exc.errors()}, status_code=422
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("unhandled error")
        return JSONResponse({"detail": f"Interner Fehler: {exc}", "code": "internal"}, status_code=500)

    app.include_router(system_router, prefix="/api")
    app.include_router(models_router, prefix="/api")
    app.include_router(conversations_router, prefix="/api")
    app.include_router(documents_router, prefix="/api")
    app.include_router(memory_router, prefix="/api")
    app.include_router(activity_router, prefix="/api")
    app.include_router(live_router, prefix="/api")
    app.include_router(agent_router, prefix="/api")
    return app
