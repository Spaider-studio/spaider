"""
Short-term memory: raw episodes, written without an LLM.

An episode is one observation, message or event, stored as it arrived with a
hash embedding. Consolidation later turns episodes into long-term knowledge.

``idempotency_key`` makes retries safe: a robot resending over a bad link
creates nothing new. Without a key, every episode is distinct.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.hashing import hash_embed
from app.core.temporal import to_utc_iso


@dataclass
class Location:
    """Geographic when latitude and longitude are given, otherwise a local frame."""

    latitude: Optional[float] = None
    longitude: Optional[float] = None
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None

    def as_point(self) -> dict:
        if self.latitude is not None and self.longitude is not None:
            return {"latitude": self.latitude, "longitude": self.longitude}
        if self.x is None or self.y is None:
            raise ValueError("A location needs latitude/longitude or x/y.")
        point = {"x": self.x, "y": self.y}
        if self.z is not None:
            point["z"] = self.z
        return point


@dataclass
class EpisodeInput:
    content: str
    kind: str = "observation"
    idempotency_key: Optional[str] = None
    session_id: Optional[str] = None
    source: Optional[str] = None
    occurred_at: Optional[datetime] = None
    location: Optional[Location] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    clearance_level: int = 1


_WRITE = """
UNWIND $rows AS row
MERGE (e:Episode {agent_id: $agent_id, idempotency_key: row.key})
ON CREATE SET
    e.id = row.id, e.content = row.content, e.kind = row.kind,
    e.session_id = row.session_id, e.source = row.source,
    e.occurred_at = row.occurred_at, e.recorded_at = $now, e.status = 'new',
    e.hash_embedding = row.embedding, e.metadata = row.metadata,
    e.clearance_level = row.clearance_level,
    e.location = CASE WHEN row.location IS NULL THEN null ELSE point(row.location) END
RETURN e.id AS id, e.id = row.id AS created
"""

_FIELDS = (
    "e.id AS id, e.content AS content, e.kind AS kind, e.session_id AS session_id, "
    "e.source AS source, e.occurred_at AS occurred_at, e.recorded_at AS recorded_at, "
    "e.metadata AS metadata, e.clearance_level AS clearance_level"
)


def _row(record: Any) -> dict:
    row = dict(record)
    row["metadata"] = json.loads(row.get("metadata") or "{}")
    return row


class EpisodeStore:
    def __init__(self, driver) -> None:
        self._driver = driver

    async def write_batch(self, agent_id: str, episodes: list[EpisodeInput]) -> list[dict]:
        """Store episodes; returns ``{id, created}`` in input order."""
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for episode in episodes:
            episode_id = str(uuid.uuid4())
            rows.append({
                "id": episode_id,
                "key": episode.idempotency_key or episode_id,
                "content": episode.content,
                "kind": episode.kind,
                "session_id": episode.session_id,
                "source": episode.source,
                "occurred_at": to_utc_iso(episode.occurred_at) or now,
                "embedding": hash_embed(episode.content),
                "metadata": json.dumps(episode.metadata, default=str),
                "clearance_level": episode.clearance_level,
                "location": episode.location.as_point() if episode.location else None,
            })

        async def _write(tx):
            result = await tx.run(_WRITE, rows=rows, agent_id=agent_id, now=now)
            return [dict(record) async for record in result]

        async with self._driver.session() as session:
            # Managed transaction: concurrent writers of one key conflict on the
            # unique constraint and the driver retries, so the loser sees the
            # winner's node instead of an error.
            return await session.execute_write(_write)

    async def recent(
        self, agent_id: str, *, limit: int = 20, max_clearance: int = 5
    ) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                MATCH (e:Episode {{agent_id: $agent_id}})
                WHERE e.clearance_level <= $max_clearance
                RETURN {_FIELDS}
                ORDER BY e.occurred_at DESC LIMIT $limit
                """,
                agent_id=agent_id, limit=limit, max_clearance=max_clearance,
            )
            return [_row(record) async for record in result]

    async def similar(
        self, agent_id: str, text: str, *, limit: int = 10, max_clearance: int = 5
    ) -> list[dict]:
        """Nearest episodes by hash embedding. Matches wording, not meaning."""
        async with self._driver.session() as session:
            # The vector index cannot pre-filter by KB, so overfetch.
            result = await session.run(
                f"""
                CALL db.index.vector.queryNodes('episode_hash_embedding', $k, $embedding)
                YIELD node AS e, score
                WHERE e.agent_id = $agent_id AND e.clearance_level <= $max_clearance
                RETURN {_FIELDS}, score
                ORDER BY score DESC LIMIT $limit
                """,
                k=max(limit * 20, 200), embedding=hash_embed(text),
                agent_id=agent_id, limit=limit, max_clearance=max_clearance,
            )
            return [_row(record) async for record in result]

    async def near(
        self,
        agent_id: str,
        location: Location,
        radius: float,
        *,
        limit: int = 20,
        max_clearance: int = 5,
    ) -> list[dict]:
        """Episodes within ``radius``: metres for geographic, frame units otherwise."""
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                MATCH (e:Episode {{agent_id: $agent_id}})
                WHERE point.distance(e.location, point($center)) <= $radius
                  AND e.clearance_level <= $max_clearance
                WITH e, point.distance(e.location, point($center)) AS distance
                RETURN {_FIELDS}, distance
                ORDER BY distance ASC LIMIT $limit
                """,
                agent_id=agent_id, center=location.as_point(), radius=radius,
                limit=limit, max_clearance=max_clearance,
            )
            return [_row(record) async for record in result]
