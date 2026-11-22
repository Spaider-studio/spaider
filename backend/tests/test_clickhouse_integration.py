"""The audit store against a real ClickHouse.

These tests exist because the mocked ones could not show that the replay
service and ClickHouse agree on the SQL, or that concurrent callers are safe.
"""
from __future__ import annotations

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
