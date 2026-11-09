"""
Workflow audit events stored in Neo4j.

Stands in for the Kafka + ClickHouse pipeline when neither is configured and
exposes the same calls as ``ReplayService``, so routes do not care which one
they hold. Payloads are stored as JSON strings because Neo4j properties cannot
hold nested maps.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _row_to_event(row: dict) -> dict:
    try:
        payload = json.loads(row.get("payload") or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {
        "event_id": row["event_id"],
        "workflow_id": row["workflow_id"],
        "agent_id": row["agent_id"],
        "event_type": row["event_type"],
        "payload": payload,
        "graph_state_hash": row.get("graph_state_hash"),
        "timestamp": row["timestamp"],
        "schema_version": "1.0",
    }


class AuditStore:
    def __init__(self, driver) -> None:
        self._driver = driver

    async def start(self) -> None:
        """Present for parity with ReplayService; the driver is already open."""

    async def stop(self) -> None:
        """Present for parity with ReplayService; the driver is owned elsewhere."""

    async def record_event(
        self,
        workflow_id: str,
        agent_id: str,
        event_type: str,
        payload: dict,
        graph_state_hash: Optional[str] = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        async with self._driver.session() as session:
            await session.run(
                """
                CREATE (:AuditEvent {
                    event_id: $event_id, workflow_id: $workflow_id, agent_id: $agent_id,
                    event_type: $event_type, payload: $payload,
                    graph_state_hash: $graph_state_hash, timestamp: $timestamp
                })
                """,
                event_id=event_id,
                workflow_id=workflow_id,
                agent_id=agent_id,
                event_type=event_type,
                payload=json.dumps(payload, default=str),
                graph_state_hash=graph_state_hash,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        return event_id

    async def get_workflow_events(
        self,
        workflow_id: str,
        *,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        event_types: Optional[list[str]] = None,
        limit: int = 1000,
    ) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e:AuditEvent {workflow_id: $workflow_id})
                WHERE ($start IS NULL OR e.timestamp >= $start)
                  AND ($end IS NULL OR e.timestamp <= $end)
                  AND ($types IS NULL OR e.event_type IN $types)
                RETURN properties(e) AS e
                ORDER BY e.timestamp ASC
                LIMIT $limit
                """,
                workflow_id=workflow_id,
                start=_iso(start_time),
                end=_iso(end_time),
                types=event_types,
                limit=limit,
            )
            return [_row_to_event(record["e"]) async for record in result]

    async def list_workflows(
        self, *, agent_id: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e:AuditEvent)
                WHERE $agent_id IS NULL OR e.agent_id = $agent_id
                WITH e.workflow_id AS workflow_id, head(collect(e.agent_id)) AS agent_id,
                     min(e.timestamp) AS first_event, max(e.timestamp) AS last_event,
                     count(e) AS event_count
                RETURN workflow_id, agent_id, first_event, last_event, event_count
                ORDER BY last_event DESC
                LIMIT $limit
                """,
                agent_id=agent_id,
                limit=limit,
            )
            return [dict(record) async for record in result]
