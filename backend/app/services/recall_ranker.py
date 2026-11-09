"""
A small per-KB model that learns which retrieved nodes turn out to be useful.

Logistic regression over node signals, fitted during sleep from answer feedback
and stored on the KB. It reorders retrieval candidates and never adds or removes
any, so a bad model can cost relevance but cannot widen what a caller sees.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

FEATURES = ("bias", "energy", "retrievals", "edge_weight", "co_activation", "is_fact", "position")
_LEARNING_RATE = 0.5
_EPOCHS = 300
_L2 = 0.001
_PRIOR_WEIGHT = 0.3

_NODE_SIGNALS = """
UNWIND $ids AS node_id
MATCH (n:SpaiderNode {id: node_id, agent_id: $kb})
OPTIONAL MATCH (n)-[r:RELATION]-()
WITH n, avg(coalesce(r.utility_weight, 1.0)) AS edge_weight
OPTIONAL MATCH (n)-[c:CO_ACTIVATED]-()
WITH n, edge_weight, sum(coalesce(c.count, 0)) AS co_activation
RETURN n.id AS id, coalesce(n.energy_level, 1.0) AS energy, coalesce(n.retrieval_count, 0) AS retrievals,
       coalesce(edge_weight, 1.0) AS edge_weight, co_activation, n.type = 'FACT' AS is_fact
"""


@dataclass
class TrainingResult:
    trained: bool
    examples: int
    accuracy: Optional[float] = None


def feature_vector(signals: dict, position: float) -> list[float]:
    return [
        1.0,
        float(signals["energy"]),
        math.log1p(signals["retrievals"]) / 5.0,
        float(signals["edge_weight"]) / 2.0,
        math.log1p(signals["co_activation"]) / 3.0,
        1.0 if signals["is_fact"] else 0.0,
        position,
    ]


def predict(weights: list[float], features: list[float]) -> float:
    score = sum(w * x for w, x in zip(weights, features))
    return 1.0 / (1.0 + math.exp(-max(min(score, 30.0), -30.0)))


def fit(examples: list[tuple[list[float], int]]) -> list[float]:
    """Batch gradient descent. Deterministic: same examples, same weights."""
    weights = [0.0] * len(FEATURES)
    for _ in range(_EPOCHS):
        gradient = [_L2 * w for w in weights]
        for features, label in examples:
            error = predict(weights, features) - label
            for index, value in enumerate(features):
                gradient[index] += error * value / len(examples)
        weights = [w - _LEARNING_RATE * g for w, g in zip(weights, gradient)]
    return weights


async def _signals(driver, agent_id: str, ids: list[str]) -> dict[str, dict]:
    async with driver.session() as session:
        result = await session.run(_NODE_SIGNALS, ids=ids, kb=agent_id)
        return {record["id"]: dict(record) async for record in result}


async def train(driver, agent_id: str) -> TrainingResult:
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (f:Feedback {agent_id: $kb})
            MATCH (e:Episode {agent_id: $kb, idempotency_key: 'query:' + f.query_id + ':answer'})
            RETURN f.verdict AS verdict, e.metadata AS metadata
            ORDER BY f.updated_at DESC LIMIT 500
            """,
            kb=agent_id,
        )
        rows = [dict(record) async for record in result]

    labelled: list[tuple[str, float, int]] = []
    for row in rows:
        metadata = json.loads(row["metadata"] or "{}")
        retrieved = [item["id"] for item in metadata.get("retrieved", []) if item.get("agent_id") == agent_id]
        cited = {item["id"] for item in metadata.get("cited", [])}
        for index, node_id in enumerate(retrieved):
            position = index / max(len(retrieved) - 1, 1)
            if node_id in cited:
                labelled.append((node_id, position, 1 if row["verdict"] > 0 else 0))
            elif row["verdict"] > 0:
                labelled.append((node_id, position, 0))

    if len(labelled) < settings.ranker_min_examples or len({label for _, _, label in labelled}) < 2:
        return TrainingResult(trained=False, examples=len(labelled))

    signals = await _signals(driver, agent_id, sorted({node_id for node_id, _, _ in labelled}))
    examples = [
        (feature_vector(signals[node_id], position), label)
        for node_id, position, label in labelled
        if node_id in signals
    ]
    weights = fit(examples)
    correct = sum((predict(weights, features) >= 0.5) == bool(label) for features, label in examples)
    accuracy = round(correct / len(examples), 4)
    async with driver.session() as session:
        await session.run(
            """
            MATCH (a:SystemAgent {agent_id: $kb})
            SET a.ranker_weights = $weights, a.ranker_trained_at = $now, a.ranker_examples = $examples
            """,
            kb=agent_id,
            weights=json.dumps(weights),
            now=datetime.now(timezone.utc).isoformat(),
            examples=len(examples),
        )
    return TrainingResult(trained=True, examples=len(examples), accuracy=accuracy)


async def rerank(driver, agent_id: str, nodes: list) -> list:
    """Reorder candidates by blending retrieval order with learned usefulness.

    Any failure returns the original order: ranking is an optimisation, and
    retrieval must not depend on it.
    """
    own_ids = [node.id for node in nodes if node.agent_id == agent_id]
    if len(own_ids) < 2:
        return nodes
    try:
        async with driver.session() as session:
            record = await (
                await session.run(
                    "MATCH (a:SystemAgent {agent_id: $kb}) RETURN a.ranker_weights AS w", kb=agent_id
                )
            ).single()
        if not record or not record["w"]:
            return nodes
        weights = json.loads(record["w"])
        signals = await _signals(driver, agent_id, own_ids)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Ranker skipped for %s: %s", agent_id, type(exc).__name__)
        return nodes

    last = max(len(nodes) - 1, 1)

    def blended(item: tuple[int, object]) -> float:
        index, node = item
        position = index / last
        prior = 1.0 - position
        node_signals = signals.get(node.id)
        if node_signals is None:
            return prior
        return _PRIOR_WEIGHT * prior + (1 - _PRIOR_WEIGHT) * predict(weights, feature_vector(node_signals, position))

    return [node for _, node in sorted(enumerate(nodes), key=blended, reverse=True)]
