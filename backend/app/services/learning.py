"""
What the system keeps from each answered question.

Every answer gets a ``query_id`` and is stored as a pair of episodes, so feedback
can later be applied to the nodes the server knows were used rather than to a
list the client supplies. Citations tie sentences to nodes; nodes cited together
gain a CO_ACTIVATED link that the recall ranker reads as a signal.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.services.episode_store import EpisodeInput, EpisodeStore

logger = logging.getLogger(__name__)

HEBBIAN_STEP = 0.1
_MAX_CO_ACTIVATED = 8
_CITATION = re.compile(r"\[n(\d+)\]")


def node_clearance(node) -> int:
    value = getattr(node, "clearance_level", None)
    if value is None:
        value = (getattr(node, "properties", None) or {}).get("clearance_level", 1)
    try:
        return int(value or 1)
    except (TypeError, ValueError):
        return 1


def parse_citations(answer: str, context_nodes: list) -> list[dict]:
    """Citations that point at real context lines, in order of first use."""
    seen: set[int] = set()
    citations = []
    for match in _CITATION.finditer(answer or ""):
        number = int(match.group(1))
        if number in seen or not 1 <= number <= len(context_nodes):
            continue
        seen.add(number)
        node = context_nodes[number - 1]
        citations.append({"ref": f"n{number}", "node_id": node.id, "label": node.label, "agent_id": node.agent_id})
    return citations


def _key(query_id: str, role: str) -> str:
    return f"query:{query_id}:{role}"


async def record_exchange(
    driver,
    agent_id: str,
    *,
    query_id: str,
    question: str,
    answer: str,
    retrieved: list,
    cited: list[dict],
) -> None:
    """Store the question and answer as episodes.

    Both carry the highest clearance among the nodes the answer was built from,
    so recalling the episode cannot reveal what the nodes would hide.
    """
    clearance = max([node_clearance(node) for node in retrieved] or [1])
    metadata = {
        "query_id": query_id,
        "retrieved": [{"id": node.id, "agent_id": node.agent_id} for node in retrieved[:50]],
        "cited": [{"id": item["node_id"], "agent_id": item["agent_id"]} for item in cited],
    }
    episodes = [
        EpisodeInput(content=question, kind="query", idempotency_key=_key(query_id, "query"),
                     metadata={"query_id": query_id}, clearance_level=clearance),
        EpisodeInput(content=answer, kind="answer", idempotency_key=_key(query_id, "answer"),
                     metadata=metadata, clearance_level=clearance),
    ]
    await EpisodeStore(driver).write_batch(agent_id, episodes)


async def strengthen_co_activation(driver, agent_id: str, cited: Iterable[dict]) -> None:
    """Link nodes cited together, within each KB only."""
    by_kb: dict[str, list[str]] = {}
    for item in cited:
        by_kb.setdefault(item["agent_id"] or agent_id, []).append(item["node_id"])
    pairs = [
        {"kb": kb, "a": a, "b": b}
        for kb, ids in by_kb.items()
        for position, a in enumerate(sorted(set(ids))[:_MAX_CO_ACTIVATED])
        for b in sorted(set(ids))[:_MAX_CO_ACTIVATED][position + 1:]
    ]
    if not pairs:
        return
    async with driver.session() as session:
        await session.run(
            """
            UNWIND $pairs AS pair
            MATCH (a:SpaiderNode {id: pair.a, agent_id: pair.kb}), (b:SpaiderNode {id: pair.b, agent_id: pair.kb})
            MERGE (a)-[c:CO_ACTIVATED]->(b)
            ON CREATE SET c.count = 1, c.first_at = $now, c.last_at = $now
            ON MATCH SET c.count = c.count + 1, c.last_at = $now
            """,
            pairs=pairs,
            now=datetime.now(timezone.utc).isoformat(),
        )


async def feedback_targets(driver, agent_id: str, query_id: str, client_ids: list[str]) -> list[str]:
    """The nodes feedback applies to: what the answer really cited, if we have it.

    A stored answer overrides the client's list, so a caller cannot boost
    arbitrary nodes by naming them. Without citations, everything retrieved counts.
    """
    import json

    async with driver.session() as session:
        record = await (
            await session.run(
                "MATCH (e:Episode {agent_id: $kb, idempotency_key: $key}) RETURN e.metadata AS m",
                kb=agent_id,
                key=_key(query_id, "answer"),
            )
        ).single()
    if record is None:
        return client_ids
    metadata = json.loads(record["m"] or "{}")
    used = metadata.get("cited") or metadata.get("retrieved") or []
    return [item["id"] for item in used if item.get("agent_id") == agent_id]


_APPLY_WEIGHT = """
UNWIND $node_ids AS src_id
UNWIND $node_ids AS tgt_id
WITH src_id, tgt_id WHERE src_id <> tgt_id
MATCH (:SpaiderNode {id: src_id, agent_id: $kb})-[r:RELATION {agent_id: $kb}]->
      (:SpaiderNode {id: tgt_id, agent_id: $kb})
WITH r, coalesce(r.utility_weight, 1.0) + $delta AS w
SET r.utility_weight = CASE WHEN w > 2.0 THEN 2.0 WHEN w < 0.1 THEN 0.1 ELSE w END
RETURN count(r) AS updated
"""


async def apply_feedback(
    driver, agent_id: str, query_id: str, success: bool, node_ids: list[str]
) -> Optional[int]:
    """Record a verdict and move edge weights. The latest verdict wins.

    Repeating a verdict changes nothing; reversing one undoes the earlier step
    before applying the new one. Returns the edges updated.
    """
    verdict = 1 if success else -1
    now = datetime.now(timezone.utc).isoformat()

    async def _apply(tx) -> int:
        # The first statement takes the row lock, so concurrent verdicts on one
        # query run one after another and each sees the other's result.
        await tx.run(
            """
            MERGE (f:Feedback {agent_id: $kb, query_id: $query_id})
            ON CREATE SET f.created_at = $now
            SET f.touched_at = $now
            """,
            kb=agent_id, query_id=query_id, now=now,
        )
        record = await (
            await tx.run(
                "MATCH (f:Feedback {agent_id: $kb, query_id: $query_id}) RETURN f.verdict AS v",
                kb=agent_id, query_id=query_id,
            )
        ).single()
        previous = record["v"] or 0
        await tx.run(
            """
            MATCH (f:Feedback {agent_id: $kb, query_id: $query_id})
            SET f.verdict = $verdict, f.updated_at = $now, f.node_ids = $node_ids,
                f.lesson_done = CASE WHEN f.verdict = $verdict THEN f.lesson_done ELSE false END
            """,
            kb=agent_id, query_id=query_id, verdict=verdict, now=now, node_ids=node_ids,
        )
        if previous == verdict:
            return 0
        result = await tx.run(
            _APPLY_WEIGHT, node_ids=node_ids, kb=agent_id, delta=(verdict - previous) * HEBBIAN_STEP
        )
        return (await result.single())["updated"]

    async with driver.session() as session:
        return await session.execute_write(_apply)
