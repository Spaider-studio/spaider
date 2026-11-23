"""Services that are not up yet when the API starts must be picked up later.

After a Docker restart every container starts at once. The API used to try to
reach Neo4j, Redis, ClickHouse and Postgres exactly once and then run without
them for as long as it stayed up.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.startup import retry_until_ready


class TestRetryUntilReady:
    @pytest.mark.asyncio
    async def test_it_keeps_trying_until_the_attempt_succeeds(self):
        results = iter([False, False, True])
        waited: list[float] = []

        async def sleep(seconds: float) -> None:
            waited.append(seconds)

        await retry_until_ready("neo4j", AsyncMock(side_effect=lambda: next(results)), delays=(1, 2, 4), sleep=sleep)

        assert waited == [1, 2]

    @pytest.mark.asyncio
    async def test_it_waits_no_longer_than_its_longest_delay(self):
        results = iter([False] * 5 + [True])
        waited: list[float] = []

        async def sleep(seconds: float) -> None:
            waited.append(seconds)

        await retry_until_ready("neo4j", AsyncMock(side_effect=lambda: next(results)), delays=(1, 2, 4), sleep=sleep)

        assert waited == [1, 2, 4, 4, 4]

    @pytest.mark.asyncio
    async def test_an_attempt_that_raises_is_a_failed_attempt_not_the_end(self):
        calls = {"count": 0}

        async def attempt() -> bool:
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError("not yet")
            return True

        await retry_until_ready("redis", attempt, delays=(0,), sleep=AsyncMock())

        assert calls["count"] == 3

    @pytest.mark.asyncio
    async def test_cancelling_stops_it(self):
        async def never() -> bool:
            return False

        task = asyncio.create_task(retry_until_ready("redis", never, delays=(10,)))
        await asyncio.sleep(0.01)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task


class TestEachServiceIsPickedUpLater:
    @pytest.mark.asyncio
    async def test_neo4j_is_set_up_once_it_answers_and_not_before(self, monkeypatch):
        import app.main as main
        from app.services import graph_service as graph_module

        graph = MagicMock()
        graph.initialize = AsyncMock(side_effect=[ConnectionError("neo4j is starting"), None])
        graph._driver = MagicMock()
        monkeypatch.setattr(graph_module, "get_graph_service", lambda: graph)
        migrate = AsyncMock()
        check = AsyncMock()
        monkeypatch.setattr(main, "run_graph_migrations", migrate)
        monkeypatch.setattr(main, "check_embedding_dimension_consistency", check)

        assert await main._prepare_neo4j() is False
        migrate.assert_not_awaited()

        assert await main._prepare_neo4j() is True
        migrate.assert_awaited_once()
        check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_redis_is_used_once_it_answers_and_the_swarm_listener_starts_then(self, monkeypatch):
        import app.main as main
        from app.core import kv as kv_module

        client = MagicMock()
        client.ping = AsyncMock(side_effect=[ConnectionError("redis is starting"), True])
        monkeypatch.setattr(kv_module, "get_kv", lambda **_: client)
        monkeypatch.setattr(main, "_redis_client", None)
        monkeypatch.setattr(main, "_graph_service", MagicMock())
        monkeypatch.setattr(main, "_swarm_listener_task", None)
        started = []
        monkeypatch.setattr(main, "_start_swarm_listener", lambda: started.append(True))
        monkeypatch.setattr("app.services.redis_service.initialize_stream", AsyncMock())

        assert await main._prepare_redis() is False
        assert main._redis_client is None
        assert started == []

        assert await main._prepare_redis() is True
        assert main._redis_client is client
        assert started == [True]

    @pytest.mark.asyncio
    async def test_analytics_is_used_once_clickhouse_answers(self, monkeypatch):
        import app.main as main
        from app.config import settings
        from app.services import analytics_service as analytics_module

        monkeypatch.setattr(settings, "clickhouse_host", "clickhouse")
        monkeypatch.setattr(main, "_analytics_service", None)
        ready = iter([False, True])

        class Fake:
            _ready = False

            async def initialize(self):
                self._ready = next(ready)

        monkeypatch.setattr(analytics_module, "AnalyticsService", Fake)

        assert await main._prepare_analytics() is False
        assert await main._prepare_analytics() is True
        assert main._analytics_service is not None and main._analytics_service._ready

    @pytest.mark.asyncio
    async def test_the_connector_tables_are_created_once_postgres_answers(self, monkeypatch):
        import app.main as main
        from app.db import postgres as postgres_module

        init = AsyncMock(side_effect=[ConnectionError("postgres is starting"), None])
        monkeypatch.setattr(postgres_module, "init_db", init)

        assert await main._prepare_postgres() is False
        assert await main._prepare_postgres() is True
