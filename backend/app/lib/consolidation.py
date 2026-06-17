"""
Graph consolidation — three-pass memory hygiene used by both the
``run_consolidation`` CLI (``backend/app/scripts/run_consolidation.py``)
and the Airflow ``graph_maintenance`` DAG.

Passes
------
1. **Orphan prune**   — delete ``SpaiderNode``s with zero relationships
                        older than ``ORPHAN_MIN_AGE_DAYS`` (default 7).
2. **Duplicate fusion** — merge node pairs with cosine similarity
                          above ``MERGE_SIMILARITY_THRESHOLD`` (default 0.95).
                          The higher-degree node is kept; edges are
                          redirected.
3. **Stats snapshot**  — log per-agent node counts.

Designed to run as an async coroutine over a shared Neo4j ``AsyncDriver``;
never blocks the event loop. All errors land on the returned
``ConsolidationReport`` so callers (CLI, DAG, future on-demand endpoint)
can surface them uniformly.

History
-------
Lifted out of the dead-code ``ReflectionService`` and made the
canonical home of the consolidation logic. The duplicate copy in the
Airflow DAG file remains for now because the Airflow image doesn't
install the ``app`` package; a follow-up will install ``app`` into the
Airflow image and route the DAG here too.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)


class ProposedEdge(BaseModel):
    """Structured LLM output for the Alchemist inverse pass."""
    is_related: bool
    proposed_label: str
    confidence: float

# Tunables (env-overridable — same names as the Airflow DAG so deployments
# need to set them in only one place).
_ORPHAN_MIN_AGE_DAYS = int(os.environ.get("ORPHAN_MIN_AGE_DAYS", "7"))
_SIMILARITY_THRESHOLD = float(os.environ.get("MERGE_SIMILARITY_THRESHOLD", "0.95"))
_BATCH_SIZE = 256  # pairwise cosine batch width


@dataclass
class ConsolidationReport:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    deleted_orphans: int = 0
    merged_duplicates: int = 0
    agents_scanned: int = 0
    proposed_edges: int = 0
    error: str | None = None

    @property
    def duration_s(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()


async def run_consolidation(driver) -> ConsolidationReport:
    """Run the full 3-pass pipeline against the supplied Neo4j ``AsyncDriver``.

    Never raises — all errors land on ``report.error`` so a scheduling
    layer (Airflow / cron / FastAPI lifespan) keeps running.
    """
    report = ConsolidationReport()
    logger.info("Reflection Engine: consolidation cycle started")

    try:
        report.deleted_orphans = await _prune_orphans(driver)
        merged, scanned = await _fuse_duplicates(driver)
        report.merged_duplicates = merged
        report.agents_scanned = scanned
        if settings.consolidation_propose_edges:
            report.proposed_edges = await _run_alchemist_pass(driver)
        await _log_stats(driver)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Reflection Engine: consolidation error — %s", exc)
        report.error = str(exc)
    finally:
        report.finished_at = datetime.now(timezone.utc)
        logger.info(
            "Reflection Engine: cycle complete in %.1fs | "
            "orphans_removed=%d  duplicates_merged=%d  agents_scanned=%d  edges_proposed=%d",
            report.duration_s,
            report.deleted_orphans,
            report.merged_duplicates,
            report.agents_scanned,
            report.proposed_edges,
        )

    return report


# --------------------------------------------------------------------------
# Pass 1 — Orphan pruning
# --------------------------------------------------------------------------


async def _prune_orphans(driver) -> int:
    """Delete isolated SpaiderNodes older than ``_ORPHAN_MIN_AGE_DAYS``."""
    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=_ORPHAN_MIN_AGE_DAYS)).timestamp()
        * 1000
    )
    async with driver.session() as session:
        count_result = await session.run(
            """
            MATCH (n:SpaiderNode)
            WHERE NOT (n)--()
              AND n.created_at IS NOT NULL
              AND n.created_at < $cutoff_ms
              AND NOT n:SystemAgent
            RETURN count(n) AS total
            """,
            cutoff_ms=cutoff_ms,
        )
        record = await count_result.single()
        total: int = record["total"] if record else 0

        if total > 0:
            await session.run(
                """
                MATCH (n:SpaiderNode)
                WHERE NOT (n)--()
                  AND n.created_at IS NOT NULL
                  AND n.created_at < $cutoff_ms
                  AND NOT n:SystemAgent
                DETACH DELETE n
                """,
                cutoff_ms=cutoff_ms,
            )

    logger.info("Reflection Engine [pass 1]: pruned %d orphan nodes", total)
    return total


# --------------------------------------------------------------------------
# Pass 2 — Duplicate fusion
# --------------------------------------------------------------------------


async def _fuse_duplicates(driver) -> tuple[int, int]:
    """Cosine-similarity merge of near-identical SpaiderNodes per agent.

    Returns ``(total_merged, agents_scanned)``. Falls back gracefully if
    numpy is unavailable.
    """
    try:
        import numpy as np
    except ImportError:
        logger.warning("Reflection Engine [pass 2]: numpy unavailable — skipping duplicate fusion")
        return 0, 0

    total_merged = 0
    agents_scanned = 0

    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (n:SpaiderNode)
            WHERE n.agent_id IS NOT NULL AND n.embedding IS NOT NULL
            RETURN DISTINCT n.agent_id AS agent_id
            """
        )
        agent_ids: list[str] = [r["agent_id"] async for r in result]

    for agent_id in agent_ids:
        merged = await _fuse_agent_duplicates(driver, agent_id, np)
        total_merged += merged
        agents_scanned += 1

    logger.info(
        "Reflection Engine [pass 2]: merged %d duplicates across %d agents",
        total_merged, agents_scanned,
    )
    return total_merged, agents_scanned


async def _fuse_agent_duplicates(driver, agent_id: str, np) -> int:
    """Merge near-duplicate nodes for a single agent. Returns merge count."""
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (n:SpaiderNode {agent_id: $agent_id})
            WHERE n.embedding IS NOT NULL AND NOT n:SystemAgent
            RETURN n.id AS id,
                   n.embedding AS embedding,
                   COUNT { (n)--() } AS degree
            """,
            agent_id=agent_id,
        )
        nodes = [dict(r) async for r in result]

    if len(nodes) < 2:
        return 0

    ids = [n["id"] for n in nodes]
    degrees = [n["degree"] for n in nodes]
    embeddings = np.array([n["embedding"] for n in nodes], dtype=np.float32)

    # L2-normalise for cosine similarity via dot product
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    embeddings = embeddings / norms

    merged_ids: set[str] = set()
    total_merged = 0

    for i in range(0, len(ids), _BATCH_SIZE):
        chunk = embeddings[i: i + _BATCH_SIZE]
        sims = chunk @ embeddings.T  # (batch, N)

        for local_i, global_i in enumerate(range(i, min(i + _BATCH_SIZE, len(ids)))):
            if ids[global_i] in merged_ids:
                continue
            for j in range(global_i + 1, len(ids)):
                if ids[j] in merged_ids:
                    continue
                if sims[local_i, j] < _SIMILARITY_THRESHOLD:
                    continue

                # Keep the node with more relationships
                if degrees[global_i] >= degrees[j]:
                    keep_id, drop_id = ids[global_i], ids[j]
                else:
                    keep_id, drop_id = ids[j], ids[global_i]

                await _merge_node_pair(driver, keep_id, drop_id)
                merged_ids.add(drop_id)
                total_merged += 1
                logger.debug(
                    "Merged node %s → %s (sim=%.4f)", drop_id, keep_id, sims[local_i, j]
                )

    return total_merged


async def _merge_node_pair(driver, keep_id: str, drop_id: str) -> None:
    """Redirect all edges from drop_id to keep_id, then delete drop_id."""
    async with driver.session() as session:
        # Outgoing edges from drop → re-attach to keep
        await session.run(
            """
            MATCH (drop:SpaiderNode {id: $drop_id})-[r]->(target)
            WHERE target.id <> $keep_id
            MATCH (keep:SpaiderNode {id: $keep_id})
            MERGE (keep)-[nr:RELATED_TO]->(target)
            ON CREATE SET nr = properties(r)
            """,
            drop_id=drop_id, keep_id=keep_id,
        )
        # Incoming edges to drop → re-attach to keep
        await session.run(
            """
            MATCH (source)-[r]->(drop:SpaiderNode {id: $drop_id})
            WHERE source.id <> $keep_id
            MATCH (keep:SpaiderNode {id: $keep_id})
            MERGE (source)-[nr:RELATED_TO]->(keep)
            ON CREATE SET nr = properties(r)
            """,
            drop_id=drop_id, keep_id=keep_id,
        )
        # Delete the duplicate
        await session.run(
            "MATCH (n:SpaiderNode {id: $drop_id}) DETACH DELETE n",
            drop_id=drop_id,
        )


# --------------------------------------------------------------------------
# Pass 3 — Stats snapshot
# --------------------------------------------------------------------------


async def _log_stats(driver) -> None:
    """Compute and log per-agent graph health metrics."""
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (a:SystemAgent)
            WHERE a.agent_id IS NOT NULL
            OPTIONAL MATCH (n:SpaiderNode {agent_id: a.agent_id})
            WHERE NOT n:SystemAgent
            WITH a.agent_id AS agent_id, a.name AS name, count(n) AS node_count
            RETURN agent_id, name, node_count
            ORDER BY node_count DESC
            """
        )
        rows = [dict(r) async for r in result]

    total_nodes = sum(r["node_count"] for r in rows)
    logger.info(
        "Reflection Engine [pass 3]: %d agents | %d total nodes",
        len(rows), total_nodes,
    )
    for r in rows:
        logger.info(
            "  Agent %-36s (%s): %d nodes",
            r["agent_id"], r["name"] or "—", r["node_count"],
        )


# --------------------------------------------------------------------------
# Pass 4 — Alchemist: proactive knowledge-graph completion
# --------------------------------------------------------------------------


async def _run_alchemist_pass(driver) -> int:
    """Outer loop: iterate all agents and propose edges. Returns total count."""
    try:
        import numpy as np
    except ImportError:
        logger.warning("Alchemist [pass 4]: numpy unavailable — skipping")
        return 0

    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (n:SpaiderNode)
            WHERE n.agent_id IS NOT NULL AND n.embedding IS NOT NULL
            RETURN DISTINCT n.agent_id AS agent_id
            """
        )
        agent_ids: list[str] = [r["agent_id"] async for r in result]

    total_proposed = 0
    for agent_id in agent_ids:
        total_proposed += await _propose_relations(driver, agent_id, np)

    logger.info(
        "Reflection Engine [pass 4 / Alchemist]: proposed %d new edges across %d agents",
        total_proposed, len(agent_ids),
    )
    return total_proposed


async def _propose_relations(driver, agent_id: str, np) -> int:
    """Propose new RELATION edges for one agent. Returns edges persisted."""
    cosine_min = settings.consolidation_propose_cosine_min
    cosine_max = settings.consolidation_propose_cosine_max
    min_conf   = settings.consolidation_propose_min_confidence
    path_max   = settings.consolidation_propose_path_max

    # ── Step 1: fetch all embedded nodes ─────────────────────────────────────
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (n:SpaiderNode {agent_id: $agent_id})
            WHERE n.embedding IS NOT NULL AND NOT n:SystemAgent
            RETURN n.id AS id, n.label AS label, n.type AS type,
                   n.properties AS properties, n.embedding AS embedding
            """,
            agent_id=agent_id,
        )
        nodes = [dict(r) async for r in result]

    if len(nodes) < 2:
        return 0

    # ── Step 2: cosine similarity matrix (reuses fuse-pass pattern) ──────────
    ids = [n["id"] for n in nodes]
    embeddings = np.array([n["embedding"] for n in nodes], dtype=np.float32)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    embeddings = embeddings / norms
    sim_matrix = embeddings @ embeddings.T  # (N, N)

    # ── Step 3: candidate pairs in the [cosine_min, cosine_max] band ─────────
    rows_idx, cols_idx = np.where(
        (sim_matrix >= cosine_min) & (sim_matrix <= cosine_max)
    )
    candidate_pairs = [
        {"id1": ids[int(i)], "id2": ids[int(j)]}
        for i, j in zip(rows_idx.tolist(), cols_idx.tolist())
        if i < j  # upper triangle only — no duplicates, no self-pairs
    ]

    if not candidate_pairs:
        return 0

    # ── Step 4: batched path check — ONE Cypher round-trip, zero Python loop ─
    # Neo4j Cypher does not allow parameters in variable-length path syntax,
    # so we interpolate ``path_max`` directly into the query string. Safe
    # because the value comes from a Pydantic-validated Settings field
    # (``ge=1, le=4``), not from user input — no injection vector.
    path_clause = f"WHERE NOT (a)-[*1..{path_max}]-(b)"
    async with driver.session() as session:
        result = await session.run(
            f"""
            UNWIND $pairs AS pair
            MATCH (a:SpaiderNode {{id: pair.id1}}), (b:SpaiderNode {{id: pair.id2}})
            {path_clause}
            RETURN a.id AS id1, a.label AS label1, a.type AS type1,
                   a.properties AS props1,
                   b.id AS id2, b.label AS label2, b.type AS type2,
                   b.properties AS props2
            """,
            pairs=candidate_pairs,
        )
        unconnected_pairs = [dict(r) async for r in result]

    if not unconnected_pairs:
        return 0

    # ── Step 5: closed vocabulary of existing edge labels ────────────────────
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (:SpaiderNode {agent_id: $agent_id})-[r:RELATION]->()
            RETURN DISTINCT r.relation AS label
            """,
            agent_id=agent_id,
        )
        label_records = [dict(r) async for r in result]

    allowed_labels: set[str] = {r["label"] for r in label_records if r.get("label")}
    if not allowed_labels:
        logger.debug(
            "Alchemist [pass 4]: no existing RELATION labels for agent %s — skipping",
            agent_id,
        )
        return 0

    # ── Step 6: LLM evaluation loop ──────────────────────────────────────────
    proposed_count = 0
    for pair in unconnected_pairs:
        proposal = await _llm_propose_edge(pair, allowed_labels)
        if proposal is None:
            continue

        if not proposal.is_related:
            continue

        if proposal.proposed_label not in allowed_labels:
            logger.debug(
                "Alchemist: discarding hallucinated label '%s' for %s↔%s",
                proposal.proposed_label, pair.get("id1"), pair.get("id2"),
            )
            continue

        if proposal.confidence < min_conf:
            logger.debug(
                "Alchemist: confidence %.2f below threshold %.2f for %s↔%s",
                proposal.confidence, min_conf, pair.get("id1"), pair.get("id2"),
            )
            continue

        await _create_proposed_edge(
            driver, pair["id1"], pair["id2"],
            proposal.proposed_label, proposal.confidence,
        )
        proposed_count += 1
        logger.debug(
            "Alchemist: proposed %s — %s → %s  (confidence=%.2f)",
            proposal.proposed_label,
            pair.get("label1"), pair.get("label2"),
            proposal.confidence,
        )

    return proposed_count


async def _llm_propose_edge(
    pair: dict, allowed_labels: set[str]
) -> "ProposedEdge | None":
    """Ask the LLM whether a logical edge exists between two candidate nodes.

    Returns None on any LLM or parse failure — callers treat None as 'skip'.
    """
    import litellm

    def _node_text(label: str, type_: str, props_raw) -> str:
        props: dict = {}
        if isinstance(props_raw, str):
            try:
                props = json.loads(props_raw)
            except Exception:
                pass
        elif isinstance(props_raw, dict):
            props = props_raw
        desc = props.get("description") or props.get("source_text") or ""
        return f"{label} ({type_}){': ' + desc if desc else ''}"

    node_a_text = _node_text(
        pair.get("label1", ""), pair.get("type1", ""), pair.get("props1")
    )
    node_b_text = _node_text(
        pair.get("label2", ""), pair.get("type2", ""), pair.get("props2")
    )
    labels_list = ", ".join(sorted(allowed_labels))

    system_prompt = (
        "You are an expert Knowledge Graph architect. "
        "Respond ONLY with a valid JSON object matching this schema exactly:\n"
        '{"is_related": <bool>, "proposed_label": <str>, "confidence": <float 0.0-1.0>}\n'
        "If is_related is false, set proposed_label to an empty string."
    )
    user_prompt = (
        f"You are an expert Knowledge Graph architect. "
        f"Examine these entities:\n"
        f"Entity A: {node_a_text}\n"
        f"Entity B: {node_b_text}\n\n"
        f"Could there be a direct logical relationship? "
        f"If yes, choose EXACTLY ONE label from this list: {labels_list}. "
        f"Do NOT invent new labels."
    )

    call_kwargs: dict = dict(
        model=settings.litellm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=128,
        response_format={"type": "json_object"},
        request_timeout=15,
    )
    if settings.llm_base_url:
        call_kwargs["api_base"] = settings.llm_base_url
    if settings.llm_api_key:
        call_kwargs["api_key"] = settings.llm_api_key

    try:
        response = await litellm.acompletion(**call_kwargs)
        content = response.choices[0].message.content or ""
        return ProposedEdge.model_validate_json(content)
    except Exception as exc:
        logger.warning(
            "Alchemist: LLM call failed for %s↔%s — %s",
            pair.get("id1"), pair.get("id2"), exc,
        )
        return None


async def _create_proposed_edge(
    driver, id1: str, id2: str, label: str, confidence: float
) -> None:
    """Persist a proposed RELATION edge with alchemist provenance metadata."""
    async with driver.session() as session:
        await session.run(
            """
            MATCH (a:SpaiderNode {id: $id1}), (b:SpaiderNode {id: $id2})
            MERGE (a)-[r:RELATION {relation: $label}]->(b)
            ON CREATE SET
                r.proposed       = true,
                r.source         = "inverse_pass",
                r.confidence     = $confidence,
                r.utility_weight = 1.0,
                r.created_at     = datetime()
            """,
            id1=id1, id2=id2, label=label, confidence=confidence,
        )
