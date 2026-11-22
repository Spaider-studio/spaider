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
