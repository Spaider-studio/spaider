"""
Cognitive Engine: Dynamic Half-Life Memory for SpAIder's knowledge graph.

Implements synapse consolidation modelled after the ACT-R memory framework:

  λ(n) = base_decay / (1 + consolidation_factor * √n)

  E(t) = E₀ · exp(−λ(n) · Δt_hours)

Where:
  λ(n)  — dynamic decay rate: lower for frequently retrieved memories
  n     — retrieval_count: the key consolidation signal
  E(t)  — energy level after Δt hours of inactivity
  E₀    — energy level at last activation

Frequently retrieved nodes consolidate (lower λ → slower decay), modelling
long-term potentiation in biological synaptic systems.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from neo4j import AsyncDriver

logger = logging.getLogger(__name__)


class CognitiveGraphService:
    """
    Manages dynamic memory decay and cognitive graph initialisation.

    Args:
        driver:               Active Neo4j async driver.
        base_decay:           λ₀ — baseline hourly decay rate (default 0.05 → ~20 h half-life).
        consolidation_factor: k  — consolidation strength per √retrieval (default 0.5).
    """

    def __init__(
        self,
        driver: AsyncDriver,
        base_decay: float = 0.05,
        consolidation_factor: float = 0.5,
    ) -> None:
        self._driver = driver
        self.base_decay = base_decay
        self.consolidation_factor = consolidation_factor

    # ------------------------------------------------------------------
    # Core mathematics
    # ------------------------------------------------------------------

    def calculate_dynamic_decay(
        self,
        current_energy: float,
        last_activation: datetime,
        current_time: datetime,
        retrieval_count: int,
    ) -> float:
        """
        Compute the post-decay energy level of a memory trace.

        The dynamic decay rate λ(n) decreases with each successful retrieval,
        reflecting synaptic consolidation: memories that are used often decay
        more slowly than memories that are never revisited.

        Args:
            current_energy:  E₀ — energy at the moment of last activation [0.0, 1.0].
            last_activation: Timestamp of the most recent retrieval or encoding.
            current_time:    Wall-clock reference for Δt computation.
            retrieval_count: n — total number of times this node has been retrieved.

        Returns:
            new_energy: float in [0.0, 1.0] (clamped, never negative).
        """
        if retrieval_count < 0:
            raise ValueError(f"retrieval_count must be >= 0, got {retrieval_count}")

        # Ensure both datetimes are timezone-aware for safe subtraction
        def _as_utc(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        delta_seconds = (
            _as_utc(current_time) - _as_utc(last_activation)
        ).total_seconds()

        # Negative delta (clock skew / test fixtures) → no decay
        delta_time_hours = max(0.0, delta_seconds / 3600.0)

        # λ(n) — dynamic decay rate: asymptotically approaches 0 for large n
        lambda_dynamic = self.base_decay / (
            1.0 + self.consolidation_factor * math.sqrt(retrieval_count)
        )

        new_energy = current_energy * math.exp(-lambda_dynamic * delta_time_hours)

        # Clamp to [0.0, 1.0] — energy cannot be negative or exceed full strength
        return max(0.0, min(1.0, new_energy))

    # ------------------------------------------------------------------
    # Graph initialisation
    # ------------------------------------------------------------------

    _INIT_CYPHER = """
    MATCH (n:SpaiderNode)
    WHERE n.energy_level   IS NULL
       OR n.last_activation IS NULL
       OR n.retrieval_count IS NULL
    SET
      n.energy_level    = coalesce(n.energy_level,    1.0),
      n.last_activation = coalesce(n.last_activation, datetime()),
      n.retrieval_count = coalesce(n.retrieval_count, 0)
    RETURN count(n) AS initialised_count
    """

    # ------------------------------------------------------------------
    # Activation boost (called on every retrieval)
    # ------------------------------------------------------------------

    _BOOST_CYPHER = """
    MATCH (n:SpaiderNode {id: $node_id})
    SET
      n.energy_level    = 1.0,
      n.retrieval_count = coalesce(n.retrieval_count, 0) + 1,
      n.last_activation = datetime()
    """

    async def upsert_node_activation(self, node_id: str) -> None:
        """
        Boost a single node after it has been retrieved and sent to the LLM.

        Atomically:
          • energy_level    → reset to 1.0  (full potentiation)
          • retrieval_count → incremented by 1  (consolidation signal)
          • last_activation → set to current UTC datetime  (resets decay clock)

        This is designed to be fire-and-forget via asyncio.create_task().
        Errors are logged but never propagated to the calling request path.

        Args:
            node_id: The UUID stored in SpaiderNode.id.
        """
        try:
            async with self._driver.session() as session:
                await session.run(self._BOOST_CYPHER, node_id=node_id)
        except Exception as exc:
            logger.warning(
                "CognitiveGraphService.upsert_node_activation failed for "
                "node_id=%s: %s",
                node_id, exc,
            )

    async def boost_nodes(self, node_ids: list[str]) -> None:
        """
        Boost a batch of nodes in parallel after a retrieval call.

        Wraps upsert_node_activation with asyncio.gather so that N nodes
        are potentiated concurrently rather than sequentially.

        Args:
            node_ids: List of SpaiderNode UUIDs returned by the retrieval query.
        """
        import asyncio

        if not node_ids:
            return

        await asyncio.gather(
            *[self.upsert_node_activation(nid) for nid in node_ids],
            return_exceptions=True,  # one failing node must not abort the rest
        )
        logger.debug(
            "CognitiveGraphService.boost_nodes: potentiated %d node(s)",
            len(node_ids),
        )

    # Implicit Hebbian reinforcement over co-retrieved edges. Fires when an
    # agent in "on" mode produces a grounded, confident answer: the RELATION
    # edges among the used nodes are strengthened by `step` (capped at 2.0).
    # This mirrors the explicit spaider.feedback update but with a smaller step
    # and no client call; disuse is undone by the decay pass in consolidation.
    _REINFORCE_EDGES_CYPHER = """
    UNWIND $node_ids AS src_id
    UNWIND $node_ids AS tgt_id
    WITH src_id, tgt_id WHERE src_id <> tgt_id
    MATCH (a:SpaiderNode {id: src_id})-[r:RELATION]->(b:SpaiderNode {id: tgt_id})
    WITH r, coalesce(r.utility_weight, 1.0) AS w
    SET r.utility_weight = CASE WHEN w + $step >= 2.0 THEN 2.0 ELSE w + $step END
    RETURN count(r) AS updated_count
    """

    async def reinforce_edges(self, node_ids: list[str], step: float) -> None:
        """
        Strengthen RELATION edges among ``node_ids`` by ``step`` (capped 2.0).

        Fire-and-forget: errors are logged, never raised (must not disturb the
        query path it is launched from). No-op for fewer than two nodes.
        """
        if not node_ids or len(node_ids) < 2 or step <= 0:
            return
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    self._REINFORCE_EDGES_CYPHER, node_ids=node_ids, step=step,
                )
                record = await result.single()
                updated = int(record["updated_count"]) if record else 0
            logger.debug(
                "CognitiveGraphService.reinforce_edges: +%.3f on %d edge(s) over %d node(s)",
                step, updated, len(node_ids),
            )
        except Exception as exc:  # noqa: BLE001 — background reinforcement must not crash
            logger.warning("reinforce_edges failed: %s", exc)

    # ------------------------------------------------------------------
    # Graph initialisation
    # ------------------------------------------------------------------

    async def initialize_graph_cognition(self) -> int:
        """
        Backfill cognitive properties on all SpaiderNode instances that lack them.

        Idempotent — nodes that already carry all three properties are untouched.

        Properties set (when absent):
          energy_level    (Float)   — 1.0   initial full-strength memory trace
          last_activation (DateTime)— now() — treated as just-encoded
          retrieval_count (Integer) — 0     — no retrievals yet

        Returns:
            Number of nodes that were initialised in this run.
        """
        async with self._driver.session() as session:
            result = await session.run(self._INIT_CYPHER)
            record = await result.single()
            count: int = record["initialised_count"] if record else 0

        logger.info(
            "CognitiveGraphService.initialize_graph_cognition: "
            "backfilled %d node(s) with energy_level / last_activation / retrieval_count",
            count,
        )
        return count
