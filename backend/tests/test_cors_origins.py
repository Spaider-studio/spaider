"""Which web origins may call the API from a browser."""
from __future__ import annotations

from starlette.middleware.cors import CORSMiddleware

from app.config import Settings


def test_the_studio_on_localhost_is_allowed_by_default():
    origins = Settings().cors_origin_list

    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins


def test_origins_can_be_set_in_the_environment():
    """The list used to be written into main.py, so a Studio on any other host or port failed with CORS errors."""
    settings = Settings(cors_origins="https://studio.example.com, https://other.example.com ,")

    assert settings.cors_origin_list == ["https://studio.example.com", "https://other.example.com"]


def test_the_app_uses_the_configured_origins():
    import app.main as main
    from app.config import settings

    middleware = next(m for m in main.app.user_middleware if m.cls is CORSMiddleware)

    assert middleware.kwargs["allow_origins"] == settings.cors_origin_list
