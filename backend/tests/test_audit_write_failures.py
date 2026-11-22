"""A failed audit or analytics write must be visible, and must not flood the log."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from app.core import clickhouse
from app.services.analytics_service import AnalyticsService
from app.services.replay_service import ReplayService


@pytest.fixture(autouse=True)
def _fresh_throttle():
    clickhouse.reset_write_failure_throttle()
    yield
    clickhouse.reset_write_failure_throttle()


async def _failing_ingest(service: AnalyticsService) -> None:
    await service._insert_ingest(
        agent_id="kb",
        text_length=1,
        chunk_count=1,
        nodes_created=0,
        nodes_merged=0,
        edges_created=0,
        latency_ms=1.0,
    )


def _warnings(caplog) -> list[str]:
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    return [message for message in messages if "insert failed" in message]


@pytest.mark.asyncio
async def test_a_failed_analytics_insert_is_logged_as_a_warning(caplog):
    service = AnalyticsService()
    service._client = MagicMock()
    service._client.insert.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.WARNING):
        await _failing_ingest(service)

    assert any("boom" in message for message in _warnings(caplog))


@pytest.mark.asyncio
async def test_a_failed_audit_insert_is_logged_as_a_warning(caplog):
    service = ReplayService()
    service._ch_client = MagicMock()
    service._ch_client.insert.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.WARNING):
        await service._insert_event_clickhouse({"event_id": "e", "workflow_id": "w", "event_type": "t"})

    assert any("boom" in message for message in _warnings(caplog))


@pytest.mark.asyncio
async def test_repeated_failures_are_logged_once_a_minute(caplog):
    service = AnalyticsService()
    service._client = MagicMock()
    service._client.insert.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            await _failing_ingest(service)

    assert len(_warnings(caplog)) == 1
