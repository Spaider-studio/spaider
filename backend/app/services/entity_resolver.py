"""
Entity Resolver: Deduplicates nodes using multi-strategy matching.
Strategies (in order): exact label match -> alias match -> fuzzy (Levenshtein) -> semantic (cosine).
Merges near-duplicate entities before writing to the graph.

Bring Your Own Vectors (BYOV)
------------------------------
Callers may pre-compute embeddings and attach them to Node objects.
EntityResolver respects these embeddings when their dimensionality matches
``settings.embedding_dimensions``, avoiding redundant embedding API calls.
Dimension mismatches are handled differently per caller context:
  - ``caller_context="api"``   → raises HTTP 422 immediately.
  - ``caller_context="kafka"`` → logs a warning and re-embeds the affected nodes.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal, Optional

from fastapi import HTTPException

from app.config import settings
from app.models.schemas import Edge, GraphPayload, Node
from app.services.embedding_service import EmbeddingService

if TYPE_CHECKING:
    from app.services.graph_service import GraphService

logger = logging.getLogger(__name__)

_FUZZY_THRESHOLD    = 0.85   # Levenshtein similarity
_SEMANTIC_THRESHOLD = 0.90   # Cosine similarity of embeddings
# top-K Neo4j vector-index probe per new node when strategies
# 1-3 (exact / alias / fuzzy) miss. Sized generously: even with a 0.90
# cosine threshold, the embedding-nearest 20 candidates virtually always
# include any node that would have been a true match in the prior 2000-
# row scan. Increase if precision/recall regression is observed in the
# resolver test suite.
_SEMANTIC_CANDIDATE_K = 20


def build_embed_text(node: Node) -> str:
    """Text a node is embedded under — its full semantic surface.

    Bare labels ("Olivia", "$90k") are nearly orthogonal to question
    embeddings ("who is the CTO?"), so label-only vectors cripple recall.
    Combine label + description + a bounded slice of source_text. FACT
    nodes carry their whole text in description — unchanged.
    """
    if node.type == "FACT" and node.description:
        return node.description
    props = node.properties or {}
    description = node.description or props.get("description") or ""
    source_text = props.get("source_text") or ""
    text = node.label or ""
    if description:
        text += f": {description}"
    if source_text and source_text != description:
        text += f" — {source_text[:300]}"
    return text


def _levenshtein_similarity(a: str, b: str) -> float:
    """Normalized Levenshtein similarity in [0.0, 1.0]."""
    a = a.lower()
    b = b.lower()
    if a == b:
        return 1.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0

    dp = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        new_dp = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            new_dp.append(min(new_dp[-1] + 1, dp[j] + 1, dp[j - 1] + cost))
        dp = new_dp

    distance = dp[len(b)]
    return 1.0 - distance / max_len


class EntityResolver:
    """
    Resolves a new GraphPayload against the existing agent graph using four
    matching strategies:
      1. Exact label match (case-insensitive)
      2. Alias match (label is in aliases list of existing node)
      3. Fuzzy match via Levenshtein similarity > 0.85
      4. Semantic match via cosine similarity of embeddings > 0.90

    When a match is found the new node is merged into the existing node:
    properties are unioned, the new label is added to aliases, and all
    edges are remapped to the canonical node id.

    BYOV (Bring Your Own Vectors)
    ------------------------------
    Nodes carrying a pre-computed ``embedding`` of the correct dimension
    (``settings.embedding_dimensions``) are passed through without touching
    the embedding service.  Only nodes with a missing or mismatched embedding
    are sent to ``embed_batch()``.
    """

    def __init__(self, embedding_service: Optional[EmbeddingService] = None) -> None:
        self._embedding_service = embedding_service or EmbeddingService()

    async def resolve(
        self,
        payload: GraphPayload,
        agent_id: str,
        graph_service: "GraphService",
        caller_context: Literal["api", "kafka"] = "api",
    ) -> GraphPayload:
        """
        Resolve and deduplicate nodes in payload against the existing agent graph.

        Args:
            payload:        Freshly extracted GraphPayload (from SemanticCompressor).
            agent_id:       The agent whose graph is being updated.
            graph_service:  GraphService to query existing nodes.
            caller_context: ``"api"`` raises HTTP 422 on embedding dimension mismatch;
                            ``"kafka"`` logs a warning and re-embeds the affected nodes.

        Returns:
            A resolved GraphPayload with deduplicated nodes, BYOV embeddings preserved,
            and edges remapped to canonical node IDs.

        Raises:
            HTTPException(422): When ``caller_context="api"`` and a node carries an
                embedding whose length differs from ``settings.embedding_dimensions``.
        """
        if not payload.nodes:
            return payload

        expected_dim: int = settings.embedding_dimensions

        # ── BYOV: classify nodes into valid / mismatch / missing ─────────────
        #
        # valid_nodes   — embedding present AND correct dimension → keep as-is.
        # mismatch_nodes — embedding present BUT wrong dimension.
        # missing_nodes  — no embedding at all.
        #
        # mismatch handling depends on caller_context (see docstring).
        valid_nodes:    list[Node] = []
        mismatch_nodes: list[Node] = []
        missing_nodes:  list[Node] = []

        for node in payload.nodes:
            if node.embedding is not None:
                if len(node.embedding) == expected_dim:
                    valid_nodes.append(node)
                else:
                    if caller_context == "api":
                        raise HTTPException(
                            status_code=422,
                            detail=(
                                f"Embedding dimension mismatch: "
                                f"expected {expected_dim}, got {len(node.embedding)}."
                            ),
                        )
                    # kafka context: collect for re-embedding after the loop.
                    mismatch_nodes.append(node)
            else:
                missing_nodes.append(node)

        # Log a single, consolidated warning for all Kafka-context mismatches.
        if mismatch_nodes:
            logger.warning(
                "BYOV | %d node(s) with embedding dimension mismatch "
                "(expected=%d) for agent=%s — falling back to re-embedding.",
                len(mismatch_nodes),
                expected_dim,
                agent_id,
            )

        # ── Selective embedding: only nodes that actually need it ─────────────
        # Embed each node's full semantic surface (label + description +
        # source_text — see build_embed_text). A question is phrased around
        # the fact ("who is the CTO?"), so a bare-label embedding ("Olivia")
        # is never near it and vector recall collapses.
        nodes_to_embed: list[Node] = mismatch_nodes + missing_nodes
        if nodes_to_embed:
            embed_texts = [build_embed_text(n) for n in nodes_to_embed]
            fresh_embeddings = await self._embedding_service.embed_batch(embed_texts)
            for node, emb in zip(nodes_to_embed, fresh_embeddings):
                node.embedding = emb

        logger.debug(
            "BYOV | agent=%s — %d preserved, %d re-embedded (%d mismatch, %d missing)",
            agent_id,
            len(valid_nodes),
            len(nodes_to_embed),
            len(mismatch_nodes),
            len(missing_nodes),
        )

        # All nodes in payload.nodes now carry a valid embedding.
        # Build a lookup so _find_match receives the correct vector per node.
        node_embedding_map: dict[str, list[float]] = {
            n.id: n.embedding  # type: ignore[misc]
            for n in payload.nodes
            if n.embedding is not None
        }

        # ── Fetch existing graph for deduplication ───────────────────────────
        # split the previously-single ``search_nodes(limit=2000)``
        # into two cheaper probes:
        #
        #   1. ``list_nodes_for_resolver`` — light, label-only fetch with no
        #      LIMIT cap and no embedding payload. Feeds strategies 1-3
        #      (exact label / alias / fuzzy Levenshtein) — those need labels
        #      and aliases, nothing more.
        #
        #   2. Per-new-node ``vector_search(top_k=_SEMANTIC_CANDIDATE_K)`` —
        #      a Neo4j vector-index ANN probe that returns embedding-similar
        #      candidates only for strategy 4 (cosine). Bounded; doesn't
        #      grow with graph size.
        #
        # Result: O(new_nodes × small_constant) instead of
        # O(new_nodes × min(graph_size, 2000)). Match precision is preserved
        # because strategies 1-3 see the FULL graph (no cap), and strategy
        # 4 sees the embedding-nearest candidates which is precisely what it
        # wants.
        existing_nodes: list[Node] = await graph_service.list_nodes_for_resolver(
            agent_id=agent_id,
        )

        id_map: dict[str, str] = {}           # new_node_id → canonical_node_id
        nodes_to_add: list[Node] = []
        nodes_to_update: dict[str, dict] = {} # canonical_id → merged props

        for new_node in payload.nodes:
            new_emb: list[float] = node_embedding_map.get(new_node.id, [])

            # FACT nodes are atomic, append-only records and must NEVER be
            # deduplicated against other facts. Distinct facts that share a
            # template ("<company>'s <problem> project for <client>, budget
            # $<X>") embed highly similar — and their truncated "fact: …"
            # labels fuzzy-match too — so resolution would otherwise silently
            # merge factually distinct facts, dropping ingested facts and the
            # answers they hold. Entities still resolve; facts never do.
            if new_node.type == "FACT":
                id_map[new_node.id] = new_node.id
                nodes_to_add.append(new_node)
                continue

            # Strategies 1-3 see the full label-only set; strategy 4 sees
            # the vector-index candidates fetched per new node only when
            # the prior strategies miss.
            match = await self._find_match_two_phase(
                new_node, existing_nodes, new_emb, agent_id, graph_service,
            )

            if match is not None:
                canonical_id = match.id
                id_map[new_node.id] = canonical_id

                merged_props = {**match.properties, **new_node.properties}
                aliases: list[str] = list(match.properties.get("aliases", []))
                if (
                    new_node.label.lower() != match.label.lower()
                    and new_node.label not in aliases
                ):
                    aliases.append(new_node.label)
                merged_props["aliases"] = aliases
                nodes_to_update[canonical_id] = merged_props

                logger.debug(
                    "Merged node '%s' into existing '%s' (id=%s)",
                    new_node.label,
                    match.label,
                    canonical_id,
                )
            else:
                id_map[new_node.id] = new_node.id
                nodes_to_add.append(new_node)

        # Apply property updates to existing nodes in-memory
        # (graph_service.write_graph will handle persistence via MERGE).
        existing_map = {n.id: n for n in existing_nodes}
        for canonical_id, props in nodes_to_update.items():
            if canonical_id in existing_map:
                existing_map[canonical_id].properties.update(props)

        # ── Remap edges ──────────────────────────────────────────────────────
        resolved_edges: list[Edge] = []
        for edge in payload.edges:
            src = id_map.get(edge.source_id, edge.source_id)
            tgt = id_map.get(edge.target_id, edge.target_id)
            if src == tgt:
                continue  # skip self-loops introduced by merging
            resolved_edges.append(
                Edge(
                    id=edge.id,
                    source_id=src,
                    target_id=tgt,
                    relation=edge.relation,
                    properties=edge.properties,
                    agent_id=edge.agent_id,
                )
            )

        # Include updated existing nodes so write_graph can persist merged props.
        updated_existing = [
            existing_map[cid]
            for cid in nodes_to_update
            if cid in existing_map
        ]

        return GraphPayload(
            nodes=nodes_to_add + updated_existing,
            edges=resolved_edges,
        )

    # -------------------------------------------------------------------------
    # Matching strategies
    # -------------------------------------------------------------------------

    async def _find_match_two_phase(
        self,
        new_node: Node,
        label_only_existing: list[Node],
        new_embedding: list[float],
        agent_id: str,
        graph_service: "GraphService",
    ) -> Optional[Node]:
        """split-strategy matcher.

        Phase 1: run strategies 1-3 (exact label / alias / fuzzy
        Levenshtein) against ``label_only_existing`` — the full agent
        graph, fetched cheaply without embeddings. These are the
        deterministic strategies; if any hits, we return immediately
        without paying for a vector probe.

        Phase 2: only when phase 1 misses, run strategy 4 (cosine
        similarity) against the top-K embedding-nearest candidates from
        the Neo4j vector index. Per-call cost is constant regardless
        of graph size.

        Match precision is preserved because phase 1 sees the *complete*
        label-set (the prior 2000-row cap is gone), and phase 2 sees
        precisely the embedding-similar candidates that strategy 4 ever
        cared about.
        """
        # ── Phase 1: deterministic label strategies on the full graph ──
        new_label_lower = new_node.label.lower()
        for existing in label_only_existing:
            if existing.label.lower() == new_label_lower:
                return existing
            aliases = [a.lower() for a in existing.properties.get("aliases", [])]
            if new_label_lower in aliases:
                return existing
        for existing in label_only_existing:
            if _levenshtein_similarity(new_node.label, existing.label) >= _FUZZY_THRESHOLD:
                return existing

        # ── Phase 2: vector-index probe for cosine-similarity match ────
        if not new_embedding:
            return None
        try:
            candidates = await graph_service.vector_search(
                embedding=new_embedding,
                agent_id=agent_id,
                top_k=_SEMANTIC_CANDIDATE_K,
            )
        except Exception as exc:  # noqa: BLE001
            # If the vector index is unavailable we degrade gracefully —
            # the deterministic strategies already ran on the full graph,
            # and merging on weaker evidence is worse than missing a
            # legitimate semantic duplicate (which the next consolidation
            # pass would catch anyway).
            logger.warning(
                "Vector-index probe failed during resolve (%s) — "
                "skipping semantic match.", exc,
            )
            return None
        for cand in candidates:
            if cand.embedding and new_embedding:
                sim = EmbeddingService.cosine_similarity(new_embedding, cand.embedding)
                if sim >= _SEMANTIC_THRESHOLD:
                    return cand
        return None

    def _find_match(
        self,
        new_node: Node,
        existing_nodes: list[Node],
        new_embedding: list[float],
    ) -> Optional[Node]:
        """
        Try all four matching strategies in order.  Returns the first match found,
        or None if no match exceeds the thresholds.

        Kept for legacy callers and the existing unit-test suite
        (``backend/tests/services/test_entity_resolver.py``) that pass
        a pre-fetched ``existing_nodes`` list. New code paths should use
        ``_find_match_two_phase`` which decouples per-fact
        match cost from graph size.
        """
        new_label_lower = new_node.label.lower()

        for existing in existing_nodes:
            # Strategy 1: Exact label match (case-insensitive)
            if existing.label.lower() == new_label_lower:
                return existing

            # Strategy 2: Alias match
            aliases = [a.lower() for a in existing.properties.get("aliases", [])]
            if new_label_lower in aliases:
                return existing

        for existing in existing_nodes:
            # Strategy 3: Fuzzy (Levenshtein) match
            if _levenshtein_similarity(new_node.label, existing.label) >= _FUZZY_THRESHOLD:
                return existing

        for existing in existing_nodes:
            # Strategy 4: Semantic (cosine) match
            if existing.embedding and new_embedding:
                sim = EmbeddingService.cosine_similarity(new_embedding, existing.embedding)
                if sim >= _SEMANTIC_THRESHOLD:
                    return existing

        return None
