"""
CORS configuration for SpAIder FastAPI application.
Development: allow all origins.
Production: restrict to configured origins.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings


def get_cors_config() -> dict:
    """
    Returns the CORS configuration dict suitable for passing to CORSMiddleware.

    Development (environment != "production"):
        allow_origins=["*"]

    Production:
        allow_origins restricted to ALLOWED_ORIGINS env variable (comma-separated)
        or a safe default of no origins.
    """
    if settings.environment.lower() != "production":
        return {
            "allow_origins": ["*"],
            "allow_credentials": True,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
        }

    # Production: read from settings; fall back to empty (deny all cross-origin)
    allowed_origins_raw: str = getattr(settings, "allowed_origins", "")
    allowed_origins = (
        [o.strip() for o in allowed_origins_raw.split(",") if o.strip()]
        if allowed_origins_raw
        else []
    )

    return {
        "allow_origins": allowed_origins,
        "allow_credentials": True,
        "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        "allow_headers": [
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-Request-ID",
        ],
    }


def configure_cors(app: FastAPI) -> None:
    """
    Attach CORSMiddleware to a FastAPI application using the appropriate config.

    Usage:
        from app.middleware.cors import configure_cors
        configure_cors(app)
    """
    config = get_cors_config()
    app.add_middleware(CORSMiddleware, **config)
