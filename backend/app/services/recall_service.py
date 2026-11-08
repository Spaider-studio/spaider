"""
Recall: what do we remember about this, without calling a model.

Episodes match by hash embedding and nodes by the full-text index, so there is
no embedding request or LLM in the path and latency depends only on Neo4j. The
two ranked lists are merged by reciprocal rank.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.hashing import hash_embed
from app.core.temporal import node_visible, to_utc_iso
from app.services.episode_store import Location
from app.services.federation import FederationService, policy_predicate, sources_param

_RRF_K = 60
_OVERFETCH = 20
_LUCENE_SPECIAL = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')

_EPISODE_FIELDS = """
    e.id AS id, e.content AS text, e.kind AS kind, e.session_id AS session_id, e.source AS source,
    e.occurred_at AS occurred_at, e.agent_id AS agent_id, e.metadata AS metadata
"""

_EPISODE_FILTER = """
    e.agent_id = $kb AND e.clearance_level <= $clearance AND e.status <> 'compacted'
    AND ($kinds IS NULL OR e.kind IN $kinds)
    AND ($session IS NULL OR e.session_id = $session)
    AND ($since IS NULL OR e.occurred_at >= $since)
    AND ($until IS NULL OR e.occurred_at <= $until)
    AND ($as_of IS NULL OR e.recorded_at <= $as_of)
"""


@dataclass
class RecallQuery:
    agent_id: str
    text: str = ""
    limit: int = 10
    clearance: int = 1
    kinds: Optional[list[str]] = None
    session_id: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    near: Optional[Location] = None
    radius: float = 100.0
    federated: bool = False
    as_of: Optional[datetime] = None
    include_nodes: bool = True
    extra: dict = field(default_factory=dict)


def lucene_query(text: str) -> Optional[str]:
    # Lowercase so AND, OR and NOT in user text are words, not operators.
    terms = [term.lower() for term in _LUCENE_SPECIAL.sub(" ", text).split() if len(term) > 1][:16]
    return " OR ".join(terms) if terms else None


class RecallService:
    def __init__(self, driver) -> None:
        self._driver = driver

    async def recall(self, query: RecallQuery) -> list[dict]:
        episodes = await self._episodes(query)
        nodes = await self._nodes(query) if query.include_nodes and query.text and query.near is None else []
        return self._merge(episodes, nodes, query.limit)

    def _params(self, query: RecallQuery) -> dict:
        return {
            "kb": query.agent_id,
            "clearance": query.clearance,
            "kinds": query.kinds,
            "session": query.session_id,
            "since": to_utc_iso(query.since),
            "until": to_utc_iso(query.until),
            "as_of": to_utc_iso(query.as_of),
            "limit": query.limit,
        }

    async def _episodes(self, query: RecallQuery) -> list[dict]:
        params = self._params(query)
        if query.near is not None:
            cypher = f"""
                MATCH (e:Episode {{agent_id: $kb}})
                WHERE e.location IS NOT NULL AND point.distance(e.location, point($center)) <= $radius
                  AND {_EPISODE_FILTER}
                WITH e, point.distance(e.location, point($center)) AS distance
                RETURN {_EPISODE_FIELDS}, -distance AS score
                ORDER BY distance ASC LIMIT $limit
            """
            params.update(center=query.near.as_point(), radius=query.radius)
        elif query.text:
            # The vector index cannot pre-filter by KB, so overfetch.
            cypher = f"""
                CALL db.index.vector.queryNodes('episode_hash_embedding', $k, $embedding)
                YIELD node AS e, score
                WHERE {_EPISODE_FILTER}
                RETURN {_EPISODE_FIELDS}, score
                ORDER BY score DESC LIMIT $limit
            """
            params.update(k=max(query.limit * _OVERFETCH, 200), embedding=hash_embed(query.text))
        else:
            cypher = f"""
                MATCH (e:Episode {{agent_id: $kb}})
                WHERE {_EPISODE_FILTER}
                RETURN {_EPISODE_FIELDS}, 0.0 AS score
                ORDER BY e.occurred_at DESC LIMIT $limit
            """
        async with self._driver.session() as session:
            result = await session.run(cypher, **params)
            rows = [dict(record) async for record in result]
        for row in rows:
            row["type"] = "episode"
            row["metadata"] = json.loads(row.get("metadata") or "{}")
        return rows

    async def _nodes(self, query: RecallQuery) -> list[dict]:
        search = lucene_query(query.text)
        if search is None:
            return []
        if query.federated:
            sources = await FederationService(self._driver).sources_for(query.agent_id, query.clearance)
        else:
            sources = []
        allowed = sources_param(sources) if sources else [
            {"kb": query.agent_id, "max_clearance": query.clearance, "node_types": None, "relation_types": None}
        ]
        as_of_filter = f"AND {node_visible('n')}"
        cypher = f"""
            CALL db.index.fulltext.queryNodes('spaider_label_fulltext', $search, {{limit: $k}})
            YIELD node AS n, score
            WHERE n.agent_id IN [s IN $fed_sources | s.kb]
              AND {policy_predicate('n')}
              AND coalesce(n.status, 'active') = 'active'
              AND coalesce(n.type, '') <> 'SKILL'
              AND NOT n:SystemAgent
              {as_of_filter}
            RETURN n.id AS id, coalesce(n.description, n.label) AS text, n.type AS kind, n.label AS label,
                   n.agent_id AS agent_id, score
            ORDER BY score DESC LIMIT $limit
        """
        async with self._driver.session() as session:
            result = await session.run(
                cypher,
                search=search,
                k=max(query.limit * _OVERFETCH, 100),
                fed_sources=allowed,
                limit=query.limit,
                as_of=to_utc_iso(query.as_of),
            )
            rows = [dict(record) async for record in result]
        for row in rows:
            row["type"] = "node"
        return rows

    @staticmethod
    def _merge(episodes: list[dict], nodes: list[dict], limit: int) -> list[dict]:
        scored: list[tuple[float, dict]] = []
        for ranked in (episodes, nodes):
            for position, row in enumerate(ranked):
                row["score"] = round(1.0 / (_RRF_K + position + 1), 6)
                scored.append((row["score"], row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in scored[:limit]]
