"""The audit store against a real ClickHouse.

These tests exist because the mocked ones could not show that the replay
service and ClickHouse agree on the SQL, or that concurrent callers are safe.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.replay_service import ReplayService


async def _started_service() -> ReplayService:
    service = ReplayService()
    await service._init_clickhouse()
    assert service._ch_ready, "the replay service could not reach the test ClickHouse"
    return service


async def _record(service: ReplayService, workflow_id: str, agent_id: str) -> None:
    await service._insert_event_clickhouse(
        {
            "event_id": f"{workflow_id}-1",
            "workflow_id": workflow_id,
            "agent_id": agent_id,
            "event_type": "query",
            "payload": {"q": "who?"},
            "graph_state_hash": "h",
            "timestamp": "2026-11-22T10:00:00+00:00",
        }
    )


@pytest.mark.asyncio
async def test_a_recorded_event_is_listed(clickhouse_test):
    service = await _started_service()
    await _record(service, "wf-1", "kb-a")

    runs = await service.list_workflows(limit=10)

    assert [(run["workflow_id"], run["agent_id"], run["event_count"]) for run in runs] == [("wf-1", "kb-a", 1)]


@pytest.mark.asyncio
async def test_the_listing_can_be_filtered_by_kb(clickhouse_test):
    """Calls ClickHouse directly: through list_workflows a Kafka fallback hides a rejected query."""
    service = await _started_service()
    await _record(service, "wf-a", "kb-a")
    await _record(service, "wf-b", "kb-b")

    runs = await service._list_workflows_clickhouse(agent_id="kb-a", limit=10)

    assert runs is not None, "ClickHouse rejected the filtered query"
    assert [(run["workflow_id"], run["agent_id"]) for run in runs] == [("wf-a", "kb-a")]


async def _recorded_analytics(agent_id: str):
    from app.services.analytics_service import AnalyticsService

    service = AnalyticsService()
    await service.initialize()
    assert service._ready, "the analytics service could not reach the test ClickHouse"
    await service._insert_ingest(
        agent_id=agent_id,
        text_length=10,
        chunk_count=1,
        nodes_created=2,
        nodes_merged=0,
        edges_created=1,
        latency_ms=12.5,
    )
    return service


@pytest.mark.asyncio
async def test_the_analytics_route_reads_what_ingest_recorded(auth_world, clickhouse_test, monkeypatch):
    """Ingest writes to the service the app started, so the route has to read from that one."""
    import app.main as main

    kb = await auth_world.create_kb("analytics-kb")
    monkeypatch.setattr(main, "_analytics_service", await _recorded_analytics(kb["id"]))

    response = await auth_world.client.get(
        "/api/v1/analytics/overview",
        params={"agent_id": kb["id"]},
        headers=auth_world.headers(auth_world.owner_key),
    )

    assert response.status_code == 200, response.text
    assert response.json()["ingest"]["total_ingests"] == 1
    # No query was recorded, so ClickHouse averages nothing. That is null, not nan.
    assert response.json()["query"]["avg_query_latency_ms"] is None


@pytest.mark.asyncio
async def test_a_hostile_kb_id_cannot_read_other_kbs(clickhouse_test):
    """The value is checked against the KB list before it gets here, but the SQL must not rely on that."""
    service = await _recorded_analytics("kb-a")

    overview = await service.get_overview("x' OR '1'='1")

    assert overview["ingest"].get("total_ingests", 0) == 0


@pytest.mark.asyncio
async def test_concurrent_audit_listings_all_succeed(clickhouse_test):
    """One client is shared by every request. ClickHouse refuses parallel queries in one session."""
    service = await _started_service()
    await _record(service, "wf-1", "kb-a")

    runs = await asyncio.gather(*[service._list_workflows_clickhouse(agent_id=None, limit=10) for _ in range(30)])

    failed = sum(run is None for run in runs)
    assert failed == 0, f"{failed} of 30 concurrent audit listings failed"
    assert all(len(run) == 1 for run in runs)


@pytest.mark.asyncio
async def test_concurrent_analytics_queries_all_succeed(clickhouse_test):
    service = await _recorded_analytics("kb-a")

    overviews = await asyncio.gather(*[service.get_overview("kb-a") for _ in range(30)])

    failed = sum(overview["ingest"].get("total_ingests") != 1 for overview in overviews)
    assert failed == 0, f"{failed} of 30 concurrent analytics queries returned nothing"


@pytest.mark.asyncio
async def test_audit_and_analytics_can_run_at_the_same_time(clickhouse_test):
    audit = await _started_service()
    await _record(audit, "wf-1", "kb-a")
    analytics = await _recorded_analytics("kb-a")

    listings, overviews = await asyncio.gather(
        asyncio.gather(*[audit._list_workflows_clickhouse(agent_id="kb-a", limit=10) for _ in range(10)]),
        asyncio.gather(*[analytics.get_overview("kb-a") for _ in range(10)]),
    )

    assert all(run is not None for run in listings)
    assert all(overview["ingest"].get("total_ingests") == 1 for overview in overviews)


class _DownOnce:
    """A real ClickHouse client whose inserts fail for a while, as during an outage."""

    def __init__(self, real, failures: int) -> None:
        self._real, self.failures = real, failures

    def insert(self, *args, **kwargs):
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("clickhouse is down")
        return self._real.insert(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.mark.asyncio
async def test_audit_events_written_during_an_outage_arrive_when_clickhouse_is_back(clickhouse_test):
    service = await _started_service()
    service._ch_client = _DownOnce(service._ch_client, failures=3)

    for number in range(3):
        await _record(service, f"wf-{number}", "kb-a")
    assert service._buffer.pending() == 3
    assert await service._list_workflows_clickhouse(agent_id=None, limit=10) == []

    await service.flush_buffer()

    runs = await service._list_workflows_clickhouse(agent_id=None, limit=10)
    assert sorted(run["workflow_id"] for run in runs) == ["wf-0", "wf-1", "wf-2"]
    assert service._buffer.pending() == 0
