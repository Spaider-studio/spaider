"""
JWT Authentication Middleware for FastAPI.
Validates Bearer tokens, populates request.state with agent context.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.logging_context import agent_id_var
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

# Paths that bypass authentication
_PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/favicon.ico",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that:
      1. Extracts the Bearer token from the Authorization header.
      2. Validates it via AuthService.verify_token().
      3. Populates request.state.agent_id and request.state.permissions.
      4. Returns 401 for missing/invalid tokens.
      5. Returns 403 for valid tokens with insufficient permissions.
      6. Skips validation for public paths (/health, /docs, /openapi.json).
    """

    def __init__(
        self,
        app: ASGIApp,
        auth_service: Optional[AuthService] = None,
    ) -> None:
        super().__init__(app)
        self._auth = auth_service or AuthService()

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip auth for public paths
        if request.url.path in _PUBLIC_PATHS or request.url.path.startswith("/docs"):
            return await call_next(request)

        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "detail": "Missing or malformed Authorization header. Expected: Bearer <token>",
                },
            )

        token = auth_header[len("Bearer "):]

        # Validate token
        try:
            payload = await self._auth.verify_token(token)
        except ValueError as exc:
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "detail": str(exc)},
            )

        # Populate request state
        request.state.agent_id = payload.get("sub")
        request.state.tenant_id = payload.get("tenant_id", "default")
        request.state.permissions = payload.get("permissions", [])
        request.state.swarm_access = payload.get("swarm_access", [])

        # Bind for the logging ContextFilter so downstream log lines in this
        # request carry agent_id without having to thread it through.
        if request.state.agent_id:
            agent_id_var.set(request.state.agent_id)

        # Permission check: require at least "read" permission for all non-public routes
        if "read" not in request.state.permissions and "admin" not in request.state.permissions:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Forbidden",
                    "detail": "Token does not have the required permissions.",
                },
            )

        return await call_next(request)


# ---------------------------------------------------------------------------
# FastAPI dependency (alternative to middleware for route-level auth)
# ---------------------------------------------------------------------------

async def require_auth(request: Request) -> dict:
    """
    FastAPI dependency that extracts and validates the JWT from the request.
    Use as: agent = Depends(require_auth)

    Returns:
        dict with agent_id, permissions, swarm_access, tenant_id

    Raises:
        HTTPException 401/403 on failure.
    """
    from fastapi import HTTPException

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header.",
        )

    token = auth_header[len("Bearer "):]
    auth_service = AuthService()

    try:
        payload = await auth_service.verify_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    permissions = payload.get("permissions", [])
    if "read" not in permissions and "admin" not in permissions:
        raise HTTPException(status_code=403, detail="Insufficient permissions.")

    agent_id = payload.get("sub")
    request.state.agent_id = agent_id
    if agent_id:
        agent_id_var.set(agent_id)

    return {
        "agent_id": agent_id,
        "tenant_id": payload.get("tenant_id", "default"),
        "permissions": permissions,
        "swarm_access": payload.get("swarm_access", []),
    }
