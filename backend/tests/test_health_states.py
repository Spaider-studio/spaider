"""/health must say what is true, and tell apart "off" from "broken"."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class _Graph:
    def __init__(self, ok: bool = True, vector_index: bool = True) -> None:
        self._ok = ok
        self.vector_index_available = vector_index

    async def ping(self) -> None:
        if not self._ok:
            raise RuntimeError("neo4j is down")


class _Analytics:
    def __init__(self, reachable: bool) -> None:
        self.ping = AsyncMock(return_value=reachable)


async def _health(monkeypatch, *, graph=None, kafka=None, analytics=None, postgres=True, **settings_values):
    from httpx import ASGITransport, AsyncClient

    import app.main as main
    from app.config import settings

    values = {"kafka_bootstrap_servers": "", "clickhouse_host": "", "postgres_url": "", "redis_url": "redis://x"}
    values.update(settings_values)
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(main, "_graph_service", graph if graph is not None else _Graph())
    monkeypatch.setattr(main, "_redis_client", MagicMock(ping=AsyncMock()))
    monkeypatch.setattr(main, "_kafka_producer", kafka)
    monkeypatch.setattr(main, "_analytics_service", analytics)
    monkeypatch.setattr(main, "_postgres_reachable", AsyncMock(return_value=postgres))
    main.reset_health_cache()
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://t") as client:
        return (await client.get("/health")).json()


@pytest.mark.asyncio
async def test_a_stack_without_optional_services_is_ok_not_degraded(monkeypatch):
    """The Lite profile runs on Neo4j alone. Services that are off are not failures."""
    body = await _health(monkeypatch)

    assert body["services"]["kafka"] == "disabled"
    assert body["services"]["clickhouse"] == "disabled"
    assert body["services"]["postgres"] == "disabled"
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_redis_is_disabled_when_the_in_process_store_is_used(monkeypatch):
    body = await _health(monkeypatch, redis_url="")

    assert body["services"]["redis"] == "disabled"
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_a_configured_service_that_does_not_answer_is_degraded(monkeypatch):
    body = await _health(monkeypatch, clickhouse_host="clickhouse", analytics=_Analytics(reachable=False))

    assert body["services"]["clickhouse"] == "unavailable"
    assert body["status"] == "degraded"


@pytest.mark.asyncio
async def test_a_configured_service_that_answers_is_ok(monkeypatch):
    body = await _health(monkeypatch, clickhouse_host="clickhouse", analytics=_Analytics(reachable=True))

    assert body["services"]["clickhouse"] == "ok"
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_postgres_is_reported_from_a_real_query(monkeypatch):
    up = await _health(monkeypatch, postgres_url="postgresql://x", postgres=True)
    down = await _health(monkeypatch, postgres_url="postgresql://x", postgres=False)

    assert up["services"]["postgres"] == "ok"
    assert down["services"]["postgres"] == "unavailable"
    assert down["status"] == "degraded"


@pytest.mark.asyncio
async def test_a_missing_vector_index_is_degraded(monkeypatch):
    """The state the Quickstart test hit: everything up, but semantic search broken."""
    body = await _health(monkeypatch, graph=_Graph(vector_index=False))

    assert body["services"]["vector_index"] == "unavailable"
    assert body["status"] == "degraded"


@pytest.mark.asyncio
async def test_neo4j_down_is_down(monkeypatch):
    body = await _health(monkeypatch, graph=_Graph(ok=False))

    assert body["services"]["neo4j"] == "unavailable"
    assert body["status"] == "down"
