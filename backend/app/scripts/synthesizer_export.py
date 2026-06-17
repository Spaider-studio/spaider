"""
SpAIder Model Synthesizer — DPO & RLHG Training Data Factory.

Concept: Reinforcement Learning from Graph (RLHG)
-------------------------------------------------
Standard DPO requires (prompt, chosen, rejected) triples.  SpAIder's
knowledge graph stores *implicit quality signals* in two ACT-R properties:

  energy_level  — float 0.0–1.0.  Refreshed to 1.0 on every retrieval;
                  decays over time via λ(n)=base_decay/(1+cf·√n).
                  High energy → the node was repeatedly useful.
                  Low energy  → the node was never retrieved again after
                                initial ingestion (dead-end path).

  utility_weight (on RELATION edges) — float 0.0+.  Hebbian feedback
                  increments this on positive retrieval; the V2 forget
                  threshold prunes edges below 0.3.

RLHG Extraction Strategy
-------------------------
For every (start_node, agent_id) pair that has at least two diverging
1-3 hop paths, we extract:

  chosen   — path terminating at a node with energy_level > CHOSEN_ENERGY
             (evidence: the model repeatedly found this node useful)

  rejected — path terminating at a node with energy_level < REJECTED_ENERGY
             (evidence: the model never returned to this node, or the path
              is structurally weak: avg utility_weight < REJECTED_WEIGHT)

The chosen response embeds an RLHG Reasoning Chain:

  THOUGHT: [start_label] -[:RELATION]-> (mid_label) -[:RELATION]-> (end_label)
  ANSWER: <end_node description / source_text>

This teaches the fine-tuned model to "think in graph paths" before answering.

Output
------
  data/exports/dpo_training_v1.jsonl
  One JSON object per line: {"prompt": "...", "chosen": "...", "rejected": "..."}

Usage
-----
  # From repo root (requires .env or env vars for Neo4j):
  python -m app.scripts.synthesizer_export

  # With explicit options:
  python -m app.scripts.synthesizer_export \\
      --agent-id my-agent \\
      --limit 2000 \\
      --out data/exports/dpo_custom.jsonl \\
      --min-pairs 500
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
from app.logging_config import configure_logging

configure_logging()
logger = logging.getLogger("synthesizer")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

CHOSEN_ENERGY:    float = 0.8   # Minimum ACT-R energy for a "good" terminal node
REJECTED_ENERGY:  float = 0.2   # Maximum ACT-R energy for a "bad" terminal node
REJECTED_WEIGHT:  float = 0.3   # Max avg edge utility_weight on the rejected path
MAX_HOP_DEPTH:    int   = 3     # Maximum path depth for traversal (controls query time)
DEFAULT_LIMIT:    int   = 5_000 # Max number of start-nodes to scan
DEFAULT_BATCH:    int   = 250   # Neo4j fetch batch size (SKIP/LIMIT pagination)
DEFAULT_OUT:      str   = "data/exports/dpo_training_v1.jsonl"

# B2 — Pillar 1 Hebbian quality threshold for the chosen DPO side.
# Audit finding B2: the chosen-path subquery sorts by ``c_avg_weight`` but
# previously did not filter on it, allowing chosen paths with average edge
# utility well below the V2 forget threshold (0.3) to be emitted as
# preferred examples. This decoupled the DPO output from the Pillar 1
# "utility_weight signals quality" thesis. Now the chosen side must
# additionally satisfy ``c_avg_weight >= $dpo_min_chosen_weight``.
#
# Env override: DPO_MIN_CHOSEN_WEIGHT. Default 0.7 — comfortably above the
# 0.3 forget threshold and the 0.3 rejected ceiling, so chosen and
# rejected sides are guaranteed to occupy non-overlapping utility bands.
DPO_MIN_CHOSEN_WEIGHT: float = float(
    os.environ.get("DPO_MIN_CHOSEN_WEIGHT", "0.7")
)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PathRecord:
    """One side of a DPO pair: a traversal path with its terminal node."""
    node_ids:       list[str]        # Ordered node IDs from start → end
    node_labels:    list[str]        # Human-readable labels (same order)
    relation_types: list[str]        # Edge relation types between consecutive nodes
    terminal_label: str
    terminal_type:  str
    terminal_text:  str              # description / source_text of terminal node
    energy_level:   float
    avg_weight:     float


@dataclass
class DPOSample:
    """One complete DPO training example."""
    prompt:   str
    chosen:   str
    rejected: str
    agent_id: str
    metadata: dict = field(default_factory=dict)

    def to_jsonl_line(self) -> str:
        """Serialise to a single JSONL line (no metadata — clean training format)."""
        return json.dumps({
            "prompt":   self.prompt,
            "chosen":   self.chosen,
            "rejected": self.rejected,
        }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Cypher queries
# ---------------------------------------------------------------------------

# All agent IDs that have at least one node with populated cognitive properties.
_AGENT_IDS_CYPHER = """
MATCH (n:SpaiderNode)
WHERE n.energy_level IS NOT NULL AND n.agent_id IS NOT NULL
RETURN DISTINCT n.agent_id AS agent_id
"""

# Count of candidate start-nodes for a given agent (nodes that have at
# least one outgoing RELATION edge and populated cognitive properties).
_COUNT_STARTS_CYPHER = """
MATCH (start:SpaiderNode {agent_id: $agent_id})
WHERE start.energy_level IS NOT NULL
  AND start.retrieval_count > 0
  AND EXISTS { MATCH (start)-[:RELATION]->() }
RETURN count(start) AS total
"""

# ---------------------------------------------------------------------------
# Core extraction query.
#
# Cartesian-Explosion mitigation
# --------------------------------
# Two independent CALL { ... } subqueries are each scoped to `start` via
# an explicit WITH.  Each subquery returns at most ONE candidate (LIMIT 1
# inside the subquery).  Neo4j evaluates them sequentially, not as a join,
# so the result cardinality is O(start_nodes) not O(chosen × rejected).
#
# Path metadata is assembled inline inside each subquery so we avoid
# re-traversing the graph after the main RETURN.
# ---------------------------------------------------------------------------

_DPO_PAIRS_CYPHER = """
MATCH (start:SpaiderNode {agent_id: $agent_id})
WHERE start.energy_level IS NOT NULL
  AND start.retrieval_count > 0
  AND coalesce(start.clearance_level, 0) <= $caller_clearance
  AND EXISTS { MATCH (start)-[:RELATION]->() }

// ── Chosen path ───────────────────────────────────────────────────────────
CALL {
    WITH start
    MATCH path = (start)-[rels:RELATION*1..{max_depth}]->(chosen_end:SpaiderNode)
    WHERE chosen_end.id <> start.id
      AND chosen_end.energy_level > $chosen_energy
      AND all(n IN nodes(path) WHERE coalesce(n.clearance_level, 0) <= $caller_clearance)
    WITH
        path,
        chosen_end,
        chosen_end.energy_level AS c_energy,
        [r IN relationships(path) | coalesce(r.utility_weight, 1.0)] AS weights
    WITH
        path,
        chosen_end,
        c_energy,
        weights,
        reduce(s = 0.0, w IN weights | s + w) / size(weights) AS c_avg_weight
    // B2 — Pillar 1 Hebbian quality gate: the chosen path must clear the
    // utility threshold. Bound as a parameter ($dpo_min_chosen_weight)
    // so the Neo4j plan cache is stable across threshold tuning.
    // (Filter and ORDER BY live in separate WITH steps: Cypher's WITH
    // grammar puts a trailing WHERE *after* ORDER BY/LIMIT, which would
    // filter post-LIMIT — and the inline `WHERE … ORDER BY` form is a
    // syntax error.)
    WHERE c_avg_weight >= $dpo_min_chosen_weight
    WITH path, chosen_end, c_energy, c_avg_weight
    ORDER BY c_energy DESC, c_avg_weight DESC
    LIMIT 1
    RETURN
        path                                                AS c_path,
        chosen_end                                          AS c_node,
        c_energy,
        c_avg_weight,
        [n IN nodes(path) | n.id]                          AS c_node_ids,
        [n IN nodes(path) | coalesce(n.label, n.id)]       AS c_labels,
        [r IN relationships(path) | type(r)]               AS c_relations
}

// ── Rejected path ─────────────────────────────────────────────────────────
CALL {
    WITH start
    MATCH path = (start)-[rels:RELATION*1..{max_depth}]->(rejected_end:SpaiderNode)
    WHERE rejected_end.id <> start.id
      AND (
            rejected_end.energy_level < $rejected_energy
            OR coalesce(rejected_end.needs_human, false) = true
          )
      AND all(n IN nodes(path) WHERE coalesce(n.clearance_level, 0) <= $caller_clearance)
    WITH
        path,
        rejected_end,
        rejected_end.energy_level AS r_energy,
        [r IN relationships(path) | coalesce(r.utility_weight, 1.0)] AS weights
    WITH
        path,
        rejected_end,
        r_energy,
        weights,
        reduce(s = 0.0, w IN weights | s + w) / size(weights) AS r_avg_weight
    WHERE r_avg_weight <= $rejected_weight
    WITH path, rejected_end, r_energy, r_avg_weight
    ORDER BY r_energy ASC, r_avg_weight ASC
    LIMIT 1
    RETURN
        path                                                AS r_path,
        rejected_end                                        AS r_node,
        r_energy,
        r_avg_weight,
        [n IN nodes(path) | n.id]                          AS r_node_ids,
        [n IN nodes(path) | coalesce(n.label, n.id)]       AS r_labels,
        [r IN relationships(path) | type(r)]               AS r_relations
}

// ── Guard: only emit rows where both paths exist ──────────────────────────
// (A bare WHERE can't follow a CALL block — it needs a WITH to attach to.)
WITH *
WHERE c_node IS NOT NULL AND r_node IS NOT NULL
  AND c_node.id <> r_node.id

RETURN
    start.id                                     AS start_id,
    coalesce(start.label, start.id)              AS start_label,
    coalesce(start.type, 'UNKNOWN')              AS start_type,
    start.agent_id                               AS agent_id,

    // Chosen
    c_node_ids,
    c_labels,
    c_relations,
    coalesce(c_node.label,  c_node.id)           AS c_terminal_label,
    coalesce(c_node.type,   'UNKNOWN')           AS c_terminal_type,
    coalesce(
        c_node.properties,  '{}'
    )                                            AS c_terminal_props,
    c_energy,
    c_avg_weight,

    // Rejected
    r_node_ids,
    r_labels,
    r_relations,
    coalesce(r_node.label,  r_node.id)           AS r_terminal_label,
    coalesce(r_node.type,   'UNKNOWN')           AS r_terminal_type,
    coalesce(
        r_node.properties,  '{}'
    )                                            AS r_terminal_props,
    r_energy,
    r_avg_weight

ORDER BY c_energy DESC
SKIP $skip
LIMIT $batch_size
"""


# ---------------------------------------------------------------------------
# Helper — extract human-readable text from the properties JSON blob
# ---------------------------------------------------------------------------

def _extract_text(raw_props: str | dict | None) -> str:
    """
    Pull the most informative text field from a SpaiderNode.properties blob.

    Priority: description → source_text → summary → first string value.
    Returns empty string if nothing useful is found.
    """
    if not raw_props:
        return ""
    try:
        props: dict = (
            json.loads(raw_props)
            if isinstance(raw_props, str)
            else raw_props
        )
    except (json.JSONDecodeError, TypeError):
        return str(raw_props)[:300]

    for key in ("description", "source_text", "summary"):
        val = props.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:500]

    # Fallback: first non-empty string value
    for val in props.values():
        if isinstance(val, str) and val.strip():
            return val.strip()[:500]

    return ""


# ---------------------------------------------------------------------------
# Reasoning Chain builder (RLHG core)
# ---------------------------------------------------------------------------

def _build_reasoning_chain(
    labels:    list[str],
    relations: list[str],
) -> str:
    """
    Construct the RLHG THOUGHT line from a sequence of node labels and
    edge relation types.

    Example output:
        SpAIder -[:POWERED_BY]-> Neo4j -[:SUPPORTS]-> Redis Streams

    The chain is intentionally kept abstract (labels, not UUIDs) so the
    fine-tuned model learns structural graph reasoning, not memorised IDs.
    """
    if not labels:
        return "?"
    if len(labels) == 1:
        return labels[0]

    parts: list[str] = [labels[0]]
    for i, rel in enumerate(relations):
        next_label = labels[i + 1] if i + 1 < len(labels) else "?"
        parts.append(f" -[:{rel}]-> {next_label}")

    return "".join(parts)


def _build_chosen_response(
    path_record: PathRecord,
) -> str:
    """
    Construct the full chosen response for a DPO sample.

    Format:
        THOUGHT: <reasoning_chain>
        ANSWER: <terminal_node_description>

    The THOUGHT prefix signals to the fine-tuned model that it should
    "think through" a graph traversal before committing to an answer —
    the core RLHG training signal.
    """
    chain = _build_reasoning_chain(
        path_record.node_labels,
        path_record.relation_types,
    )
    answer_text = path_record.terminal_text or path_record.terminal_label

    return f"THOUGHT: {chain}\nANSWER: {answer_text}"


def _build_rejected_response(
    path_record: PathRecord,
) -> str:
    """
    Construct the rejected response for a DPO sample.

    Rejected responses deliberately omit the THOUGHT chain (or provide a
    weak one) to teach the model that answers without graph-grounded
    reasoning are inferior, even when the terminal text is plausible.
    """
    answer_text = path_record.terminal_text or path_record.terminal_label
    if not answer_text:
        answer_text = (
            f"I couldn't find reliable information about "
            f"{path_record.terminal_label}."
        )
    return f"ANSWER: {answer_text}"


# ---------------------------------------------------------------------------
# Neo4j row → PathRecord
# ---------------------------------------------------------------------------

def _row_to_path_record(
    node_ids:    list[str],
    labels:      list[str],
    relations:   list[str],
    term_label:  str,
    term_type:   str,
    term_props:  str | dict | None,
    energy:      float,
    avg_weight:  float,
) -> PathRecord:
    return PathRecord(
        node_ids       = node_ids,
        node_labels    = labels,
        relation_types = relations,
        terminal_label = term_label,
        terminal_type  = term_type,
        terminal_text  = _extract_text(term_props),
        energy_level   = float(energy or 0.0),
        avg_weight     = float(avg_weight or 0.0),
    )


# ---------------------------------------------------------------------------
# Async data pipeline
# ---------------------------------------------------------------------------

async def _stream_dpo_pairs(
    driver,
    agent_id:    str,
    limit:       int,
    batch_size:  int,
    max_depth:   int,
    caller_clearance: int = 5,
) -> AsyncIterator[DPOSample]:
    """
    Async generator: yield DPOSample objects for one agent_id.

    Uses paginated SKIP/LIMIT to avoid loading the full result set into
    memory.  Each page is fetched, transformed, and yielded before the
    next page is requested.

    ``caller_clearance`` gates every node on both paths (Diplomat
    Protocol). The CLI default of 5 (max) preserves the original
    operator-run behaviour; the REST endpoint binds the clearance it
    resolved from the caller's API key.
    """
    # Build the query once — interpolate max_depth as a literal (it is an
    # integer constant, not user input, so no injection risk).
    query = _DPO_PAIRS_CYPHER.replace("{max_depth}", str(max_depth))

    skip  = 0
    total = 0

    while skip < limit:
        fetch = min(batch_size, limit - skip)

        async with driver.session() as session:
            result = await session.run(
                query,
                agent_id              = agent_id,
                chosen_energy         = CHOSEN_ENERGY,
                rejected_energy       = REJECTED_ENERGY,
                rejected_weight       = REJECTED_WEIGHT,
                dpo_min_chosen_weight = DPO_MIN_CHOSEN_WEIGHT,  # B2 — Pillar 1 quality gate
                caller_clearance      = caller_clearance,       # B4 — Diplomat Protocol
                skip                  = skip,
                batch_size            = fetch,
            )
            rows = await result.data()

        if not rows:
            logger.debug(
                "agent=%s skip=%d — no more rows, stopping pagination",
                agent_id, skip,
            )
            break

        for row in rows:
            # ── Build PathRecord objects ───────────────────────────────────
            chosen = _row_to_path_record(
                node_ids   = row["c_node_ids"],
                labels     = row["c_labels"],
                relations  = row["c_relations"],
                term_label = row["c_terminal_label"],
                term_type  = row["c_terminal_type"],
                term_props = row["c_terminal_props"],
                energy     = row["c_energy"],
                avg_weight = row["c_avg_weight"],
            )
            rejected = _row_to_path_record(
                node_ids   = row["r_node_ids"],
                labels     = row["r_labels"],
                relations  = row["r_relations"],
                term_label = row["r_terminal_label"],
                term_type  = row["r_terminal_type"],
                term_props = row["r_terminal_props"],
                energy     = row["r_energy"],
                avg_weight = row["r_avg_weight"],
            )

            # ── Prompt: the start-node provides the "question" context ─────
            prompt = (
                f"What can you tell me about "
                f"{row['start_label']} ({row['start_type']})?"
            )

            # ── Build DPO sample ──────────────────────────────────────────
            sample = DPOSample(
                prompt   = prompt,
                chosen   = _build_chosen_response(chosen),
                rejected = _build_rejected_response(rejected),
                agent_id = agent_id,
                metadata = {
                    "start_id":      row["start_id"],
                    "c_energy":      chosen.energy_level,
                    "c_avg_weight":  chosen.avg_weight,
                    "r_energy":      rejected.energy_level,
                    "r_avg_weight":  rejected.avg_weight,
                    "path_depth_c":  len(chosen.relation_types),
                    "path_depth_r":  len(rejected.relation_types),
                },
            )
            yield sample
            total += 1

        skip += len(rows)
        logger.info(
            "agent=%s | page done — rows_this_page=%d total_so_far=%d",
            agent_id, len(rows), total,
        )

        if len(rows) < fetch:
            break  # Last page was partial — no more data

    logger.info("agent=%s | stream complete — %d DPO pairs emitted", agent_id, total)


# ---------------------------------------------------------------------------
# Main export coroutine
# ---------------------------------------------------------------------------

async def export(
    agent_ids: list[str] | None,
    limit:     int,
    batch:     int,
    out_path:  Path,
    max_depth: int,
    min_pairs: int,
) -> int:
    """
    Full export pipeline.

    1. Connect to Neo4j using settings from config / environment.
    2. Discover agent IDs (or use the explicit list).
    3. For each agent, stream DPO pairs and write to JSONL.
    4. Return total lines written.
    """
    import neo4j  # noqa: PLC0415  (lazy import)

    from app.config import settings  # noqa: PLC0415

    out_path.parent.mkdir(parents=True, exist_ok=True)

    driver = neo4j.AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        max_connection_pool_size=5,
    )

    try:
        # ── Discover agents if not specified ──────────────────────────────
        if not agent_ids:
            async with driver.session() as session:
                result    = await session.run(_AGENT_IDS_CYPHER)
                rows      = await result.data()
                agent_ids = [r["agent_id"] for r in rows if r.get("agent_id")]

            if not agent_ids:
                logger.warning("No agents with cognitive properties found — nothing to export.")
                return 0

            logger.info("Discovered %d agent(s): %s", len(agent_ids), agent_ids)

        # ── Count candidates per agent ────────────────────────────────────
        for aid in agent_ids:
            async with driver.session() as session:
                result = await session.run(_COUNT_STARTS_CYPHER, agent_id=aid)
                row    = await result.single()
                count  = row["total"] if row else 0
            logger.info("agent=%s — %d candidate start-nodes", aid, count)

        # ── Stream and write ──────────────────────────────────────────────
        total_written = 0
        skipped       = 0

        with out_path.open("w", encoding="utf-8") as fh:
            for aid in agent_ids:
                agent_count = 0

                async for sample in _stream_dpo_pairs(
                    driver    = driver,
                    agent_id  = aid,
                    limit     = limit,
                    batch_size= batch,
                    max_depth = max_depth,
                ):
                    # Quality gate: skip samples where chosen == rejected text
                    if sample.chosen.strip() == sample.rejected.strip():
                        skipped += 1
                        continue

                    # Quality gate: skip empty prompts
                    if not sample.prompt.strip():
                        skipped += 1
                        continue

                    fh.write(sample.to_jsonl_line() + "\n")
                    total_written += 1
                    agent_count   += 1

                logger.info(
                    "agent=%s | wrote %d samples to %s",
                    aid, agent_count, out_path,
                )

        logger.info(
            "Export complete | total_written=%d skipped=%d file=%s",
            total_written, skipped, out_path,
        )

        if total_written < min_pairs:
            logger.warning(
                "Only %d pairs generated — below min_pairs=%d threshold. "
                "Consider lowering CHOSEN_ENERGY / REJECTED_ENERGY thresholds "
                "or ingesting more data before running the synthesizer.",
                total_written, min_pairs,
            )

        return total_written

    finally:
        await driver.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "SpAIder DPO & RLHG Data Synthesizer — "
            "exports (prompt, chosen, rejected) triples from the knowledge graph."
        )
    )
    parser.add_argument(
        "--agent-id",
        dest="agent_ids",
        action="append",
        metavar="AGENT_ID",
        default=None,
        help=(
            "Agent ID to export. Can be specified multiple times. "
            "Defaults to all agents with cognitive properties."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Max start-nodes to scan per agent (default: {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=DEFAULT_BATCH,
        help=f"Pagination batch size for Neo4j queries (default: {DEFAULT_BATCH}).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(DEFAULT_OUT),
        help=f"Output JSONL path (default: {DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=MAX_HOP_DEPTH,
        choices=range(1, 6),
        metavar="1-5",
        help=f"Maximum traversal depth for path extraction (default: {MAX_HOP_DEPTH}).",
    )
    parser.add_argument(
        "--min-pairs",
        type=int,
        default=100,
        help="Warn if fewer than this many pairs are generated (default: 100).",
    )
    parser.add_argument(
        "--chosen-energy",
        type=float,
        default=CHOSEN_ENERGY,
        help=f"Min ACT-R energy for chosen terminal node (default: {CHOSEN_ENERGY}).",
    )
    parser.add_argument(
        "--rejected-energy",
        type=float,
        default=REJECTED_ENERGY,
        help=f"Max ACT-R energy for rejected terminal node (default: {REJECTED_ENERGY}).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Allow CLI overrides of module-level thresholds
    global CHOSEN_ENERGY, REJECTED_ENERGY
    CHOSEN_ENERGY   = args.chosen_energy
    REJECTED_ENERGY = args.rejected_energy

    total = asyncio.run(
        export(
            agent_ids = args.agent_ids,
            limit     = args.limit,
            batch     = args.batch,
            out_path  = args.out,
            max_depth = args.max_depth,
            min_pairs = args.min_pairs,
        )
    )
    sys.exit(0 if total > 0 else 1)


if __name__ == "__main__":
    main()
