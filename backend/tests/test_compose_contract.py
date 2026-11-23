"""Settings in docker-compose.yml that the stack's behaviour depends on."""
from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _service(name: str) -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"][name]


def test_kong_forgets_a_backend_address_within_seconds():
    """A recreated backend gets a new IP. With Docker's default TTL Kong answered 502 for over 100 seconds."""
    environment = _service("kong")["environment"]

    assert int(environment["KONG_DNS_VALID_TTL"]) <= 10
    assert int(environment["KONG_DNS_STALE_TTL"]) <= 10


def test_the_shipped_env_file_does_not_lower_the_rate_limit():
    """`spaider init` copies .env.example, so a value there beats the default in the code.

    It said 60 a minute while the code default is 600. The Studio's pages make a few
    requests each, so a new install hit "API rate limit exceeded" within a minute.
    """
    from app.config import Settings

    shipped = {}
    for line in (COMPOSE.parent / ".env.example").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            shipped[key.strip()] = value.strip()

    assert int(shipped["RATE_LIMIT_REQUESTS_PER_MINUTE"]) == Settings().rate_limit_requests_per_minute
    assert Settings().rate_limit_requests_per_minute >= 300


def test_the_studio_image_can_be_told_where_the_api_is():
    """NEXT_PUBLIC_* is read at build time. Setting it only at run time, as the compose file used to, does nothing."""
    assert "NEXT_PUBLIC_BACKEND_URL" in _service("frontend")["build"]["args"]
