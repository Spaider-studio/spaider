"""
Tests for ReplayService and Replay API endpoints.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.replay_service import ReplayService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    workflow_id: str = "wf-1",
    agent_id: str = "agent-a",
    event_type: str = "task_started",
    timestamp: str | None = None,
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "workflow_id": workflow_id,
        "agent_id": agent_id,
        "event_type": event_type,
        "payload": {"key": "value"},
        "graph_state_hash": None,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "schema_version": "1.0",
    }


def _make_kafka_message(value: dict) -> MagicMock:
    msg = MagicMock()
    msg.value = value
    return msg


# ---------------------------------------------------------------------------
# ReplayService.record_event
# ---------------------------------------------------------------------------


class TestRecordEvent:
    @pytest.mark.asyncio
    async def test_record_event_produces_to_kafka(self):
        """record_event should produce a message to the workflow events topic."""
        with patch("app.services.replay_service.AIOKafkaProducer") as mock_cls:
            mock_producer = AsyncMock()
            mock_cls.return_value = mock_producer

            svc = ReplayService()
            await svc.start()

            event_id = await svc.record_event(
                workflow_id="wf-123",
                agent_id="agent-a",
                event_type="task_started",
                payload={"task": "extract"},
                graph_state_hash="abc123",
            )

            assert event_id  # non-empty UUID string
            mock_producer.send_and_wait.assert_called_once()
            call_kwargs = mock_producer.send_and_wait.call_args
            assert call_kwargs.kwargs["key"] == "wf-123"
            sent_value = call_kwargs.kwargs["value"]
            assert sent_value["workflow_id"] == "wf-123"
            assert sent_value["agent_id"] == "agent-a"
            assert sent_value["event_type"] == "task_started"
            assert sent_value["graph_state_hash"] == "abc123"
            assert sent_value["schema_version"] == "1.0"

            await svc.stop()

    @pytest.mark.asyncio
    async def test_record_event_raises_when_not_started(self):
        """record_event should raise RuntimeError if not started."""
        svc = ReplayService()
        with pytest.raises(RuntimeError, match="not started"):
            await svc.record_event(
                workflow_id="wf-1",
                agent_id="agent-a",
                event_type="task_started",
                payload={},
            )


# ---------------------------------------------------------------------------
# ReplayService.get_workflow_events
# ---------------------------------------------------------------------------


class TestGetWorkflowEvents:
    @pytest.mark.asyncio
    async def test_fetches_events_for_workflow(self):
        """get_workflow_events should return only events matching the workflow_id."""
        target_event = _make_event(workflow_id="wf-target")
        other_event = _make_event(workflow_id="wf-other")

        tp = MagicMock()
        batch_data = {
            tp: [_make_kafka_message(target_event), _make_kafka_message(other_event)]
        }

        with patch("app.services.replay_service.AIOKafkaConsumer") as mock_cls:
            mock_consumer = AsyncMock()
            mock_cls.return_value = mock_consumer
            # First call returns data, second returns empty to break loop
            mock_consumer.getmany = AsyncMock(side_effect=[batch_data, {}, {}, {}])

            svc = ReplayService()
            events = await svc.get_workflow_events("wf-target")

        assert len(events) == 1
        assert events[0]["workflow_id"] == "wf-target"

    @pytest.mark.asyncio
    async def test_filters_by_event_type(self):
        """get_workflow_events should filter by event_types when specified."""
        ev1 = _make_event(event_type="task_started")
        ev2 = _make_event(event_type="llm_response")

        tp = MagicMock()
        batch_data = {tp: [_make_kafka_message(ev1), _make_kafka_message(ev2)]}

        with patch("app.services.replay_service.AIOKafkaConsumer") as mock_cls:
            mock_consumer = AsyncMock()
            mock_cls.return_value = mock_consumer
            mock_consumer.getmany = AsyncMock(side_effect=[batch_data, {}, {}, {}])

            svc = ReplayService()
            events = await svc.get_workflow_events(
                "wf-1", event_types=["task_started"]
            )

        assert len(events) == 1
        assert events[0]["event_type"] == "task_started"

    @pytest.mark.asyncio
    async def test_respects_limit(self):
        """get_workflow_events should stop at the limit."""
        events_data = [_make_event() for _ in range(10)]
        tp = MagicMock()
        batch_data = {tp: [_make_kafka_message(e) for e in events_data]}

        with patch("app.services.replay_service.AIOKafkaConsumer") as mock_cls:
            mock_consumer = AsyncMock()
            mock_cls.return_value = mock_consumer
            mock_consumer.getmany = AsyncMock(side_effect=[batch_data, {}, {}, {}])

            svc = ReplayService()
            events = await svc.get_workflow_events("wf-1", limit=3)

        assert len(events) == 3


# ---------------------------------------------------------------------------
# ReplayService.list_workflows
# ---------------------------------------------------------------------------


class TestListWorkflows:
    @pytest.mark.asyncio
    async def test_lists_distinct_workflows(self):
        """list_workflows should return distinct workflow summaries."""
        ev1 = _make_event(workflow_id="wf-a", timestamp="2026-01-01T00:00:00+00:00")
        ev2 = _make_event(workflow_id="wf-a", timestamp="2026-01-01T01:00:00+00:00")
        ev3 = _make_event(workflow_id="wf-b", timestamp="2026-01-02T00:00:00+00:00")

        tp = MagicMock()
        batch_data = {
            tp: [
                _make_kafka_message(ev1),
                _make_kafka_message(ev2),
                _make_kafka_message(ev3),
            ]
        }

        with patch("app.services.replay_service.AIOKafkaConsumer") as mock_cls:
            mock_consumer = AsyncMock()
            mock_cls.return_value = mock_consumer
            mock_consumer.getmany = AsyncMock(side_effect=[batch_data, {}, {}, {}])

            svc = ReplayService()
            workflows = await svc.list_workflows()

        assert len(workflows) == 2
        # Sorted by last_event descending — wf-b should be first
        assert workflows[0]["workflow_id"] == "wf-b"
        assert workflows[1]["workflow_id"] == "wf-a"
        assert workflows[1]["event_count"] == 2

    @pytest.mark.asyncio
    async def test_filters_by_agent_id(self):
        """list_workflows should filter by agent_id when provided."""
        ev1 = _make_event(workflow_id="wf-a", agent_id="agent-x")
        ev2 = _make_event(workflow_id="wf-b", agent_id="agent-y")

        tp = MagicMock()
        batch_data = {tp: [_make_kafka_message(ev1), _make_kafka_message(ev2)]}

        with patch("app.services.replay_service.AIOKafkaConsumer") as mock_cls:
            mock_consumer = AsyncMock()
            mock_cls.return_value = mock_consumer
            mock_consumer.getmany = AsyncMock(side_effect=[batch_data, {}, {}, {}])

            svc = ReplayService()
            workflows = await svc.list_workflows(agent_id="agent-x")

        assert len(workflows) == 1
        assert workflows[0]["agent_id"] == "agent-x"


# ---------------------------------------------------------------------------
# Replay API routes (via TestClient)
# ---------------------------------------------------------------------------


class TestReplayAPI:
    @pytest.mark.asyncio
    async def test_record_event_endpoint(self):
        """POST /replay/events should record and return event_id."""
        from httpx import ASGITransport, AsyncClient
        from app.main import app
        from app.api.v1 import replay as replay_module

        mock_svc = AsyncMock()
        mock_svc.record_event = AsyncMock(return_value="evt-123")

        replay_module._replay_service = mock_svc

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/replay/events",
                    json={
                        "workflow_id": "wf-1",
                        "agent_id": "agent-a",
                        "event_type": "task_started",
                        "payload": {"task": "extract"},
                    },
                )

            assert resp.status_code == 201
            data = resp.json()
            assert data["event_id"] == "evt-123"
            assert data["workflow_id"] == "wf-1"
        finally:
            replay_module._replay_service = None

    @pytest.mark.asyncio
    async def test_list_workflows_endpoint(self):
        """GET /replay/workflows should return workflow summaries."""
        from httpx import ASGITransport, AsyncClient
        from app.main import app
        from app.api.v1 import replay as replay_module

        mock_svc = AsyncMock()
        mock_svc.list_workflows = AsyncMock(
            return_value=[
                {
                    "workflow_id": "wf-1",
                    "agent_id": "agent-a",
                    "first_event": "2026-01-01T00:00:00+00:00",
                    "last_event": "2026-01-01T01:00:00+00:00",
                    "event_count": 5,
                }
            ]
        )

        replay_module._replay_service = mock_svc

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/replay/workflows")

            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["workflows"][0]["workflow_id"] == "wf-1"
        finally:
            replay_module._replay_service = None

    @pytest.mark.asyncio
    async def test_get_workflow_events_endpoint(self):
        """GET /replay/workflows/{id}/events should return events."""
        from httpx import ASGITransport, AsyncClient
        from app.main import app
        from app.api.v1 import replay as replay_module

        mock_svc = AsyncMock()
        mock_svc.get_workflow_events = AsyncMock(
            return_value=[_make_event(workflow_id="wf-1")]
        )

        replay_module._replay_service = mock_svc

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/replay/workflows/wf-1/events")

            assert resp.status_code == 200
            data = resp.json()
            assert data["workflow_id"] == "wf-1"
            assert data["total"] == 1
        finally:
            replay_module._replay_service = None


# ---------------------------------------------------------------------------
# ClickHouse audit-store paths (durable audit log — part 1)
# ---------------------------------------------------------------------------


def _ch_result(column_names: list[str], rows: list[list]) -> MagicMock:
    result = MagicMock()
    result.column_names = column_names
    result.result_rows = rows
    return result


class TestClickHouseAuditStore:
    @pytest.mark.asyncio
    async def test_record_event_dual_writes_to_clickhouse(self):
        svc = ReplayService()
        svc._producer = AsyncMock()
        svc._ch_ready = True
        svc._ch_client = MagicMock()

        await svc.record_event("wf-ch", "agent-a", "query_answered", {"a": 1})
        # the insert is fire-and-forget — let the scheduled task run
        import asyncio
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        svc._producer.send_and_wait.assert_awaited_once()
        assert svc._ch_client.insert.called
        table, data = svc._ch_client.insert.call_args[0][:2]
        assert table == "spaider.workflow_events"
        assert data[0][2] == "wf-ch"           # workflow_id column
        assert data[0][4] == "query_answered"  # event_type column

    @pytest.mark.asyncio
    async def test_get_workflow_events_prefers_clickhouse(self):
        svc = ReplayService()
        svc._ch_ready = True
        svc._ch_client = MagicMock()
        ts = datetime(2026, 6, 10, 12, 0, 0)
        svc._ch_client.query.return_value = _ch_result(
            ["timestamp", "event_id", "workflow_id", "agent_id",
             "event_type", "payload", "graph_state_hash"],
            [[ts, "ev-1", "wf-1", "agent-a", "ingest_received",
              json.dumps({"text_length": 12}), ""]],
        )

        events = await svc.get_workflow_events("wf-1")

        assert len(events) == 1
        ev = events[0]
        # same shape as the Kafka path — payload decoded, tz-aware ISO timestamp
        assert ev["event_type"] == "ingest_received"
        assert ev["payload"] == {"text_length": 12}
        assert ev["timestamp"].endswith("+00:00")
        assert ev["graph_state_hash"] is None
        # parameters are server-side bound, never interpolated
        sql, = svc._ch_client.query.call_args[0]
        assert "{wf:String}" in sql
        assert svc._ch_client.query.call_args[1]["parameters"]["wf"] == "wf-1"

    @pytest.mark.asyncio
    async def test_get_workflow_events_falls_back_to_kafka_on_ch_error(self):
        svc = ReplayService()
        svc._ch_ready = True
        svc._ch_client = MagicMock()
        svc._ch_client.query.side_effect = RuntimeError("ch down")

        kafka_events = [_make_event(workflow_id="wf-2")]
        with patch.object(
            svc, "_get_workflow_events_kafka", new=AsyncMock(return_value=kafka_events)
        ) as kafka_mock:
            events = await svc.get_workflow_events("wf-2")

        kafka_mock.assert_awaited_once()
        assert events == kafka_events

    @pytest.mark.asyncio
    async def test_list_workflows_prefers_clickhouse(self):
        svc = ReplayService()
        svc._ch_ready = True
        svc._ch_client = MagicMock()
        first = datetime(2026, 6, 10, 11, 0, 0)
        last = datetime(2026, 6, 10, 12, 0, 0)
        svc._ch_client.query.return_value = _ch_result(
            ["workflow_id", "agent_id", "first_event", "last_event", "event_count"],
            [["wf-1", "agent-a", first, last, 4]],
        )

        workflows = await svc.list_workflows(agent_id="agent-a", limit=10)

        assert workflows == [{
            "workflow_id": "wf-1",
            "agent_id": "agent-a",
            "first_event": first.replace(tzinfo=timezone.utc).isoformat(),
            "last_event": last.replace(tzinfo=timezone.utc).isoformat(),
            "event_count": 4,
        }]
        params = svc._ch_client.query.call_args[1]["parameters"]
        assert params["agent"] == "agent-a"
        assert params["lim"] == 10
