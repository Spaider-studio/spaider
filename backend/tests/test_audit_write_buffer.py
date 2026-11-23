"""A write that fails is kept and retried, not lost."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from app.core import clickhouse
from app.core.clickhouse import WriteBuffer
from app.services.analytics_service import AnalyticsService
from app.services.replay_service import ReplayService

COLUMNS = ["a", "b"]


@pytest.fixture(autouse=True)
def _fresh_throttle():
    clickhouse.reset_write_failure_throttle()
    yield
    clickhouse.reset_write_failure_throttle()


class Recorder:
    """Stands in for ClickHouse: can be told to fail."""

    def __init__(self) -> None:
        self.fail = False
        self.written: list[tuple[str, list, list]] = []

    async def insert(self, table, columns, rows):
        if self.fail:
            raise RuntimeError("clickhouse is down")
        self.written.append((table, columns, list(rows)))


class TestWriteBuffer:
    @pytest.mark.asyncio
    async def test_rows_are_written_in_the_order_they_came_in(self):
        buffer, target = WriteBuffer(), Recorder()
        for number in range(3):
            buffer.add("t", COLUMNS, [number, "x"])

        written = await buffer.flush(target.insert)

        assert written == 3
        assert target.written == [("t", COLUMNS, [[0, "x"], [1, "x"], [2, "x"]])]
        assert buffer.pending() == 0

    @pytest.mark.asyncio
    async def test_a_failed_flush_keeps_every_row_for_the_next_try(self):
        buffer, target = WriteBuffer(), Recorder()
        buffer.add("t", COLUMNS, [1, "x"])
        buffer.add("t", COLUMNS, [2, "x"])
        target.fail = True

        written = await buffer.flush(target.insert)

        assert written == 0
        assert buffer.pending() == 2

        target.fail = False
        assert await buffer.flush(target.insert) == 2
        assert buffer.pending() == 0

    @pytest.mark.asyncio
    async def test_a_large_backlog_is_written_in_batches(self):
        buffer, target = WriteBuffer(), Recorder()
        for number in range(1200):
            buffer.add("t", COLUMNS, [number, "x"])

        await buffer.flush(target.insert, batch=500)

        assert [len(rows) for _, _, rows in target.written] == [500, 500, 200]

    def test_the_backlog_is_bounded_and_the_oldest_rows_go_first(self, caplog):
        buffer = WriteBuffer(limit=3)

        with caplog.at_level(logging.WARNING):
            for number in range(5):
                buffer.add("t", COLUMNS, [number, "x"])

        assert buffer.pending() == 3
        assert buffer.dropped == 2
        assert any("dropped" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_each_table_keeps_its_own_columns(self):
        buffer, target = WriteBuffer(), Recorder()
        buffer.add("one", ["a"], [1])
        buffer.add("two", ["b", "c"], [2, 3])

        await buffer.flush(target.insert)

        assert sorted((table, columns) for table, columns, _ in target.written) == [("one", ["a"]), ("two", ["b", "c"])]


class TestServicesKeepFailedWrites:
    @pytest.mark.asyncio
    async def test_analytics_keeps_a_row_when_clickhouse_is_down_and_writes_it_later(self):
        service = AnalyticsService()
        service._client = MagicMock()
        service._client.insert.side_effect = RuntimeError("clickhouse is down")
        await service._insert_ingest(
            agent_id="kb",
            text_length=1,
            chunk_count=1,
            nodes_created=2,
            nodes_merged=0,
            edges_created=1,
            latency_ms=3.0,
        )
        assert service._buffer.pending() == 1

        service._client.insert.side_effect = None
        await service.flush_buffer()

        assert service._buffer.pending() == 0
        table, rows = service._client.insert.call_args.args[:2]
        assert table == "spaider.ingest_events"
        assert rows[0][1] == "kb"

    @pytest.mark.asyncio
    async def test_the_audit_log_keeps_an_event_when_clickhouse_is_down_and_writes_it_later(self):
        service = ReplayService()
        service._ch_client = MagicMock()
        service._ch_client.insert.side_effect = RuntimeError("clickhouse is down")
        event = {"event_id": "e1", "workflow_id": "w1", "agent_id": "kb", "event_type": "q"}
        await service._insert_event_clickhouse(event)
        assert service._buffer.pending() == 1

        service._ch_client.insert.side_effect = None
        await service.flush_buffer()

        assert service._buffer.pending() == 0
        assert service._ch_client.insert.call_args.args[0] == "spaider.workflow_events"
