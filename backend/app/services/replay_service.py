"""
Audit-log service: record workflow events durably and read them back.

Events are written to two places:

- **Kafka** (``kafka_topic_workflow_events``) — the streaming transport,
  unchanged for any consumer that tails the topic live.
- **ClickHouse** (``spaider.workflow_events``) — the durable audit store.
  Kafka retention is ~7 days, which made the audit trail evaporate;
  ClickHouse keeps it queryable indefinitely and makes the read endpoints
  fast (indexed lookups instead of a full topic scan).

Reads prefer ClickHouse and fall back to the legacy Kafka scan when
ClickHouse is unavailable, so the API degrades instead of breaking.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from app.config import settings

logger = logging.getLogger(__name__)

# ClickHouse DDL — executed once on start(), same pattern as AnalyticsService.
_WORKFLOW_EVENTS_DDL = [
    """
    CREATE DATABASE IF NOT EXISTS spaider
    """,
    """
    CREATE TABLE IF NOT EXISTS spaider.workflow_events (
        timestamp        DateTime64(3),
        event_id         String,
        workflow_id      String,
        agent_id         LowCardinality(String),
        event_type       LowCardinality(String),
        payload          String,
        graph_state_hash String
    ) ENGINE = MergeTree()
    PARTITION BY toYYYYMM(timestamp)
    ORDER BY (workflow_id, timestamp)
    """,
]


class ReplayService:
    """Records workflow events (Kafka + ClickHouse) and serves the audit log."""

    def __init__(self) -> None:
        self._producer: Optional[AIOKafkaProducer] = None
        self._ch_client = None
        self._ch_ready = False

    async def start(self) -> None:
        """Start the Kafka producer and connect the ClickHouse audit store."""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            acks="all",
            enable_idempotence=True,
        )
        await self._producer.start()
        logger.info("ReplayService producer started")
        await self._init_clickhouse()

    async def _init_clickhouse(self) -> None:
        """Best-effort ClickHouse connection. The audit log degrades to the
        Kafka scan when ClickHouse is down — never blocks event recording."""
        try:
            import clickhouse_connect

            self._ch_client = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: clickhouse_connect.get_client(
                    host=settings.clickhouse_host,
                    port=settings.clickhouse_port,
                    username=settings.clickhouse_user,
                    password=settings.clickhouse_password,
                ),
            )
            for ddl in _WORKFLOW_EVENTS_DDL:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda q=ddl: self._ch_client.command(q)
                )
            self._ch_ready = True
            logger.info("ReplayService: ClickHouse audit store ready")
        except Exception as exc:
            logger.warning(
                "ReplayService: ClickHouse unavailable — audit log will use "
                "the Kafka scan fallback (7-day retention): %s", exc,
            )
            self._ch_ready = False

    async def stop(self) -> None:
        """Stop the internal producer and the ClickHouse client."""
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
            logger.info("ReplayService producer stopped")
        if self._ch_client is not None:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._ch_client.close
                )
            except Exception:
                pass
            self._ch_client = None
            self._ch_ready = False

    async def record_event(
        self,
        workflow_id: str,
        agent_id: str,
        event_type: str,
        payload: dict,
        *,
        graph_state_hash: Optional[str] = None,
    ) -> str:
        """
        Record a workflow event to the events topic.

        Args:
            workflow_id: Unique identifier for the workflow run.
            agent_id: Agent that produced the event.
            event_type: Category of event (e.g. "task_started", "task_completed",
                        "llm_request", "llm_response", "graph_mutation").
            payload: Event-specific data.
            graph_state_hash: Optional hash of the graph state before this event,
                              enabling deterministic reconstruction.

        Returns:
            event_id: Unique identifier for this event.
        """
        if self._producer is None:
            raise RuntimeError("ReplayService is not started. Call start() first.")

        event_id = str(uuid.uuid4())
        event = {
            "event_id": event_id,
            "workflow_id": workflow_id,
            "agent_id": agent_id,
            "event_type": event_type,
            "payload": payload,
            "graph_state_hash": graph_state_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "schema_version": "1.0",
        }

        await self._producer.send_and_wait(
            topic=settings.kafka_topic_workflow_events,
            value=event,
            key=workflow_id,
        )
        # Durable copy: fire-and-forget into ClickHouse so the audit trail
        # survives Kafka's retention window. Never blocks the caller.
        if self._ch_ready:
            asyncio.create_task(self._insert_event_clickhouse(event))
        logger.info(
            "Recorded workflow event | event_id=%s workflow_id=%s type=%s",
            event_id,
            workflow_id,
            event_type,
        )
        return event_id

    async def _insert_event_clickhouse(self, event: dict) -> None:
        try:
            ts = self._parse_event_ts(event.get("timestamp", ""))
            ts_naive = (
                ts.astimezone(timezone.utc).replace(tzinfo=None)
                if ts else datetime.now(timezone.utc).replace(tzinfo=None)
            )
            data = [[
                ts_naive,
                event.get("event_id", ""),
                event.get("workflow_id", ""),
                event.get("agent_id") or "",
                event.get("event_type", ""),
                json.dumps(event.get("payload") or {}, ensure_ascii=False),
                event.get("graph_state_hash") or "",
            ]]
            cols = ["timestamp", "event_id", "workflow_id", "agent_id",
                    "event_type", "payload", "graph_state_hash"]
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._ch_client.insert(
                    "spaider.workflow_events", data, column_names=cols
                ),
            )
        except Exception as exc:
            logger.debug("ClickHouse workflow_events insert failed: %s", exc)

    @staticmethod
    def _parse_event_ts(ts_str: str) -> Optional[datetime]:
        """Parse an ISO-8601 timestamp string to a timezone-aware datetime."""
        if not ts_str:
            return None
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None

    @staticmethod
    def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
        """Normalise a datetime to UTC, treating naive datetimes as UTC."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    async def get_workflow_events(
        self,
        workflow_id: str,
        *,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        event_types: Optional[list[str]] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """
        Fetch all events for a given workflow_id.

        Reads from the durable ClickHouse audit store when available
        (indexed by workflow_id — fast, survives Kafka retention); falls
        back to the legacy Kafka topic scan otherwise.

        Args:
            workflow_id: The workflow run to fetch events for.
            start_time: Only return events after this timestamp.
            end_time: Only return events before this timestamp.
            event_types: Optional filter for specific event types.
            limit: Maximum number of events to return.

        Returns:
            List of event dicts, ordered by timestamp.
        """
        if self._ch_ready:
            events = await self._get_workflow_events_clickhouse(
                workflow_id,
                start_time=self._ensure_utc(start_time),
                end_time=self._ensure_utc(end_time),
                event_types=event_types,
                limit=limit,
            )
            if events is not None:
                return events
        return await self._get_workflow_events_kafka(
            workflow_id,
            start_time=start_time,
            end_time=end_time,
            event_types=event_types,
            limit=limit,
        )

    async def _get_workflow_events_clickhouse(
        self,
        workflow_id: str,
        *,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        event_types: Optional[list[str]],
        limit: int,
    ) -> Optional[list[dict]]:
        """ClickHouse read path. Returns None on failure so the caller can
        fall back to the Kafka scan. All user-supplied values are bound as
        server-side parameters — never interpolated."""
        sql = (
            "SELECT timestamp, event_id, workflow_id, agent_id, event_type, "
            "payload, graph_state_hash "
            "FROM spaider.workflow_events "
            "WHERE workflow_id = {wf:String}"
        )
        params: dict = {"wf": workflow_id, "lim": limit}
        if start_time is not None:
            sql += " AND timestamp >= {start:DateTime64(3)}"
            params["start"] = start_time.replace(tzinfo=None)
        if end_time is not None:
            sql += " AND timestamp <= {end:DateTime64(3)}"
            params["end"] = end_time.replace(tzinfo=None)
        if event_types:
            sql += " AND event_type IN {types:Array(String)}"
            params["types"] = list(event_types)
        sql += " ORDER BY timestamp ASC LIMIT {lim:UInt32}"

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._ch_client.query(sql, parameters=params)
            )
        except Exception as exc:
            logger.warning(
                "ClickHouse audit read failed — falling back to Kafka: %s", exc
            )
            return None
        return [self._ch_row_to_event(dict(zip(result.column_names, row)))
                for row in result.result_rows]

    @staticmethod
    def _ch_row_to_event(row: dict) -> dict:
        """Reshape a ClickHouse row into the same event dict the Kafka path
        returns, so both backends are interchangeable to the API."""
        ts = row.get("timestamp")
        if isinstance(ts, datetime):
            ts = ts.replace(tzinfo=timezone.utc).isoformat()
        try:
            payload = json.loads(row.get("payload") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        return {
            "event_id": row.get("event_id", ""),
            "workflow_id": row.get("workflow_id", ""),
            "agent_id": row.get("agent_id", ""),
            "event_type": row.get("event_type", ""),
            "payload": payload,
            "graph_state_hash": row.get("graph_state_hash") or None,
            "timestamp": ts or "",
            "schema_version": "1.0",
        }

    async def _get_workflow_events_kafka(
        self,
        workflow_id: str,
        *,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        event_types: Optional[list[str]] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Legacy Kafka topic scan (bounded by topic retention)."""
        start_utc = self._ensure_utc(start_time)
        end_utc = self._ensure_utc(end_time)

        topic = settings.kafka_topic_workflow_events
        group_id = f"{settings.kafka_replay_consumer_group}-{uuid.uuid4().hex[:8]}"

        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            value_deserializer=lambda raw: json.loads(raw.decode("utf-8")),
            consumer_timeout_ms=5000,
        )

        events: list[dict] = []
        try:
            await consumer.start()

            # If start_time given, seek to offsets near that timestamp
            if start_utc is not None:
                partitions = consumer.assignment()
                if not partitions:
                    # Force assignment by polling
                    await asyncio.wait_for(consumer.getmany(timeout_ms=100), timeout=5)
                    partitions = consumer.assignment()

                ts_ms = int(start_utc.timestamp() * 1000)
                offsets = await consumer.offsets_for_times(
                    {tp: ts_ms for tp in partitions}
                )
                for tp, offset_and_ts in offsets.items():
                    if offset_and_ts is not None:
                        consumer.seek(tp, offset_and_ts.offset)

            # Read messages until we hit the limit or run out of data
            empty_polls = 0
            while len(events) < limit and empty_polls < 3:
                batch = await consumer.getmany(timeout_ms=2000, max_records=500)
                if not batch:
                    empty_polls += 1
                    continue

                empty_polls = 0
                for _tp, messages in batch.items():
                    for msg in messages:
                        event = msg.value
                        if event.get("workflow_id") != workflow_id:
                            continue

                        event_ts_dt = self._parse_event_ts(event.get("timestamp", ""))
                        if start_utc and (event_ts_dt is None or event_ts_dt < start_utc):
                            continue
                        if end_utc and (event_ts_dt is None or event_ts_dt > end_utc):
                            continue
                        if event_types and event.get("event_type") not in event_types:
                            continue

                        events.append(event)
                        if len(events) >= limit:
                            break
                    if len(events) >= limit:
                        break

        except Exception as exc:
            logger.exception("Error fetching workflow events: %s", exc)
            raise
        finally:
            await consumer.stop()

        events.sort(key=lambda e: e.get("timestamp", ""))
        return events

    async def list_workflows(
        self,
        *,
        agent_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        List distinct workflow runs (workflow_id, agent_id, first/last event
        timestamps, event count). ClickHouse-first; Kafka-scan fallback.
        """
        if self._ch_ready:
            workflows = await self._list_workflows_clickhouse(
                agent_id=agent_id, limit=limit
            )
            if workflows is not None:
                return workflows
        return await self._list_workflows_kafka(agent_id=agent_id, limit=limit)

    async def _list_workflows_clickhouse(
        self,
        *,
        agent_id: Optional[str],
        limit: int,
    ) -> Optional[list[dict]]:
        """Aggregate workflow summaries from the audit table. Returns None on
        failure so the caller falls back to Kafka."""
        sql = (
            "SELECT workflow_id, any(agent_id) AS agent_id, "
            "min(timestamp) AS first_event, max(timestamp) AS last_event, "
            "count() AS event_count "
            "FROM spaider.workflow_events"
        )
        params: dict = {"lim": limit}
        if agent_id:
            sql += " WHERE agent_id = {agent:String}"
            params["agent"] = agent_id
        sql += (
            " GROUP BY workflow_id"
            " ORDER BY last_event DESC"
            " LIMIT {lim:UInt32}"
        )
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._ch_client.query(sql, parameters=params)
            )
        except Exception as exc:
            logger.warning(
                "ClickHouse workflow list failed — falling back to Kafka: %s", exc
            )
            return None

        def _iso(value) -> str:
            if isinstance(value, datetime):
                return value.replace(tzinfo=timezone.utc).isoformat()
            return str(value or "")

        out: list[dict] = []
        for row in result.result_rows:
            record = dict(zip(result.column_names, row))
            out.append({
                "workflow_id": record.get("workflow_id", ""),
                "agent_id": record.get("agent_id", ""),
                "first_event": _iso(record.get("first_event")),
                "last_event": _iso(record.get("last_event")),
                "event_count": int(record.get("event_count") or 0),
            })
        return out

    async def _list_workflows_kafka(
        self,
        *,
        agent_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Legacy Kafka topic scan (bounded by topic retention)."""
        topic = settings.kafka_topic_workflow_events
        group_id = f"{settings.kafka_replay_consumer_group}-{uuid.uuid4().hex[:8]}"

        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            value_deserializer=lambda raw: json.loads(raw.decode("utf-8")),
            consumer_timeout_ms=5000,
        )

        workflows: dict[str, dict] = {}
        try:
            await consumer.start()

            empty_polls = 0
            while empty_polls < 3:
                batch = await consumer.getmany(timeout_ms=2000, max_records=500)
                if not batch:
                    empty_polls += 1
                    continue

                empty_polls = 0
                for _tp, messages in batch.items():
                    for msg in messages:
                        event = msg.value
                        wf_id = event.get("workflow_id")
                        if not wf_id:
                            continue

                        ev_agent = event.get("agent_id", "")
                        if agent_id and ev_agent != agent_id:
                            continue

                        ts = event.get("timestamp", "")

                        if wf_id not in workflows:
                            workflows[wf_id] = {
                                "workflow_id": wf_id,
                                "agent_id": ev_agent,
                                "first_event": ts,
                                "last_event": ts,
                                "event_count": 0,
                            }

                        wf = workflows[wf_id]
                        wf["event_count"] += 1
                        if ts < wf["first_event"]:
                            wf["first_event"] = ts
                        if ts > wf["last_event"]:
                            wf["last_event"] = ts

        except Exception as exc:
            logger.error("Error listing workflows: %s", exc)
            raise
        finally:
            await consumer.stop()

        result = sorted(
            workflows.values(), key=lambda w: w["last_event"], reverse=True
        )
        return result[:limit]
