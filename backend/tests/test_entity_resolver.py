"""
Tests for EntityResolver.

The real EntityResolver:
  - __init__(embedding_service=None)
  - async resolve(payload: GraphPayload, agent_id: str, graph_service: GraphService,
                  caller_context: Literal["api", "kafka"] = "api") -> GraphPayload

Matching strategies (in order):
  1. Exact label match (case-insensitive)
  2. Alias match
  3. Fuzzy (Levenshtein) match > 0.85
  4. Semantic (cosine) match > 0.90

BYOV (Bring Your Own Vectors) tests at the bottom of this file validate:
  - Selective embedding (only missing/mismatched nodes hit embed_batch)
  - API context: dimension mismatch → HTTPException(422)
  - Kafka context: dimension mismatch → warning + re-embed (no exception)
  - Full integration: pre-computed vector survives resolve() unmodified
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.schemas import Edge, GraphPayload, Node

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(label: str, node_type: str = "Person", node_id: str | None = None) -> Node:
    n = Node(label=label, type=node_type)
    if node_id:
        n.id = node_id
    return n


def _make_mock_graph_service(existing_nodes: list[Node]) -> AsyncMock:
    """Mock GraphService returning ``existing_nodes`` from the candidate fetches
    the resolver actually uses: ``list_nodes_for_resolver`` (strategies 1-3,
    exact/alias/fuzzy over the full graph) and ``vector_search`` (strategy 4,
    cosine over the embedding-nearest candidates)."""
    gs = AsyncMock()
    gs.list_nodes_for_resolver = AsyncMock(return_value=existing_nodes)
    gs.vector_search = AsyncMock(return_value=existing_nodes)
    return gs


def _make_mock_embedding_service(embeddings: list[list[float]]) -> AsyncMock:
    """
    Create a mock EmbeddingService.
    embed_batch returns successive batches from the embeddings list.
    cosine_similarity is tested via the real static method.
    """
    mock_es = AsyncMock()
    # Return each list in turn for successive embed_batch calls
    call_count = [0]
    batches = embeddings  # list of batches, each batch is a list[list[float]]

    async def _embed_batch(labels: list[str]) -> list[list[float]]:
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(batches):
            return batches[idx]
        # Return zeros if we run out of prepared batches
        return [[0.0] * 2 for _ in labels]

    mock_es.embed_batch = _embed_batch
    return mock_es


def _unit_vec(values: list[float]) -> list[float]:
    """Return L2-normalized vector."""
    import math
    magnitude = math.sqrt(sum(v * v for v in values))
    if magnitude == 0:
        return values
    return [v / magnitude for v in values]


# ---------------------------------------------------------------------------
# test_resolve_no_existing_nodes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_no_existing_nodes():
    """All new nodes stay as-is when graph is empty."""
    nodes = [_make_node("Alice"), _make_node("Bob")]
    payload = GraphPayload(nodes=nodes, edges=[])

    # Two embeddings for the two new nodes; no existing nodes
    e_alice = _unit_vec([1.0, 0.0])
    e_bob = _unit_vec([0.0, 1.0])
    mock_es = _make_mock_embedding_service([[e_alice, e_bob]])
    mock_gs = _make_mock_graph_service([])

    from app.services.entity_resolver import EntityResolver
    resolver = EntityResolver(embedding_service=mock_es)
    resolved = await resolver.resolve(payload, agent_id="test-agent", graph_service=mock_gs)

    assert len(resolved.nodes) == 2
    node_labels = {n.label for n in resolved.nodes}
    assert "Alice" in node_labels
    assert "Bob" in node_labels


# ---------------------------------------------------------------------------
# test_resolve_merges_duplicate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_merges_duplicate():
    """A new node with the same label as an existing one should be merged (exact match)."""
    new_node = _make_node("Elon Musk")
    payload = GraphPayload(nodes=[new_node], edges=[])

    existing = _make_node("Elon Musk", node_id="existing-elon-id")
    mock_gs = _make_mock_graph_service([existing])

    # Exact label match doesn't need embeddings for this strategy
    e_elon = _unit_vec([1.0, 0.0])
    mock_es = _make_mock_embedding_service([[e_elon], []])

    from app.services.entity_resolver import EntityResolver
    resolver = EntityResolver(embedding_service=mock_es)
    resolved = await resolver.resolve(payload, agent_id="test-agent", graph_service=mock_gs)

    # new_node merged into existing: existing is returned with updated props;
    # the new_node should NOT appear as a separate node
    new_ids = {n.id for n in resolved.nodes}
    assert new_node.id not in new_ids, "Merged new node must not appear in resolved payload"
    # Existing node should be present (with updated props)
    assert any(n.id == existing.id for n in resolved.nodes)


# ---------------------------------------------------------------------------
# test_resolve_keeps_dissimilar_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_keeps_dissimilar_node():
    """A node that doesn't match any existing node should remain as new."""
    new_node = _make_node("OpenAI")
    payload = GraphPayload(nodes=[new_node], edges=[])

    existing = _make_node("Tesla", node_id="tesla-id")
    mock_gs = _make_mock_graph_service([existing])

    # Use orthogonal embeddings → cosine similarity = 0 (well below threshold)
    e_new = _unit_vec([1.0, 0.0])
    e_existing = _unit_vec([0.0, 1.0])
    existing.embedding = e_existing

    mock_es = _make_mock_embedding_service([[e_new]])

    from app.services.entity_resolver import EntityResolver
    resolver = EntityResolver(embedding_service=mock_es)
    resolved = await resolver.resolve(payload, agent_id="test-agent", graph_service=mock_gs)

    # OpenAI should be retained (no match found)
    assert any(n.id == new_node.id for n in resolved.nodes), "Dissimilar node must be kept"


# ---------------------------------------------------------------------------
# test_resolve_remaps_edge_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_remaps_edge_ids():
    """Edges whose source/target is merged should be remapped to the canonical id."""
    n1 = _make_node("Elon Musk")
    n2 = _make_node("Tesla")
    edge = Edge(source_id=n1.id, target_id=n2.id, relation="CEO_OF")
    payload = GraphPayload(nodes=[n1, n2], edges=[edge])

    existing_n1 = _make_node("Elon Musk", node_id="existing-elon-id")
    mock_gs = _make_mock_graph_service([existing_n1])

    e_n1 = _unit_vec([1.0, 0.0])
    e_n2 = _unit_vec([0.0, 1.0])
    mock_es = _make_mock_embedding_service([[e_n1, e_n2], []])

    from app.services.entity_resolver import EntityResolver
    resolver = EntityResolver(embedding_service=mock_es)
    resolved = await resolver.resolve(payload, agent_id="test-agent", graph_service=mock_gs)

    # n1 is merged into existing_n1 → edge source should point to existing_n1.id
    assert len(resolved.edges) == 1
    assert resolved.edges[0].source_id == existing_n1.id


# ---------------------------------------------------------------------------
# test_resolve_drops_self_loop_edges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_drops_self_loop_edges():
    """If both edge endpoints merge to the same node, the edge should be dropped."""
    n1 = _make_node("Elon")
    n2 = _make_node("Elon Musk")
    edge = Edge(source_id=n1.id, target_id=n2.id, relation="SAME_AS")
    payload = GraphPayload(nodes=[n1, n2], edges=[edge])

    # Both n1 and n2 have the same label prefix → both match existing "Elon Musk" exactly (n2)
    # and n1 matches via exact or fuzzy
    existing = _make_node("Elon Musk", node_id="canonical-elon-id")
    mock_gs = _make_mock_graph_service([existing])

    perfect_emb = _unit_vec([1.0, 0.0])
    # Both new nodes embed identically; existing node has same embedding
    existing.embedding = perfect_emb
    mock_es = _make_mock_embedding_service([[perfect_emb, perfect_emb]])

    from app.services.entity_resolver import EntityResolver
    resolver = EntityResolver(embedding_service=mock_es)
    resolved = await resolver.resolve(payload, agent_id="test-agent", graph_service=mock_gs)

    # Both endpoints merged to same canonical node → self-loop → dropped
    assert len(resolved.edges) == 0, "Self-loop edge must be dropped"


# ===========================================================================
# BYOV — Bring Your Own Vectors
# ===========================================================================

_DIM = 1536  # must match settings.embedding_dimensions


def _byov_vec(seed: float) -> list[float]:
    """Return a reproducible 1536-dim vector filled with ``seed``."""
    return [seed] * _DIM


def _wrong_dim_vec() -> list[float]:
    """Return a 768-dim vector — intentionally wrong for mismatch tests."""
    return [0.1] * 768


def _make_async_embed_mock(batches: list[list[list[float]]]) -> MagicMock:
    """
    Return a mock EmbeddingService whose ``embed_batch`` attribute is an
    AsyncMock with ``side_effect=batches``.

    The resolver calls ``self._embedding_service.embed_batch(labels)``, so
    assertions must target ``mock.embed_batch``, not the mock object itself.
    Using MagicMock as the container and AsyncMock as the method allows the
    standard ``assert_called_once`` / ``call_args`` API on ``mock.embed_batch``.
    """
    svc = MagicMock()
    svc.embed_batch = AsyncMock(side_effect=batches)
    return svc


# ---------------------------------------------------------------------------
# test_mixed_payload
# ---------------------------------------------------------------------------


async def test_mixed_payload() -> None:
    """
    2 nodes carry valid 1536-dim BYOV embeddings; 2 nodes have no embedding.

    Assertions
    ----------
    * embed_batch() is called EXACTLY ONCE.
    * It receives only the 2 label strings of the nodes without embeddings.
    * The final payload contains all 4 nodes, each with a 1536-dim embedding.
    * The 2 BYOV embeddings are IDENTICAL to the originals (not overwritten).
    """
    from app.services.entity_resolver import EntityResolver

    # Nodes A and B come with pre-computed BYOV embeddings.
    node_a = Node(label="QuantumComputer", type="TECHNOLOGY")
    node_b = Node(label="CRISPR", type="TECHNOLOGY")
    node_a.embedding = _byov_vec(0.11)
    node_b.embedding = _byov_vec(0.22)

    # Nodes C and D have no embedding — must be embedded by the service.
    node_c = Node(label="Starship", type="PRODUCT")
    node_d = Node(label="mRNA", type="CONCEPT")

    payload = GraphPayload(nodes=[node_a, node_b, node_c, node_d], edges=[])

    # embed_batch should return exactly 2 embeddings for the 2 missing nodes.
    fresh_c = _byov_vec(0.33)
    fresh_d = _byov_vec(0.44)
    mock_embed = _make_async_embed_mock([[fresh_c, fresh_d]])

    mock_gs = _make_mock_graph_service([])  # empty graph — no merging
    resolver = EntityResolver(embedding_service=mock_embed)

    resolved = await resolver.resolve(
        payload, agent_id="test-agent", graph_service=mock_gs, caller_context="api"
    )

    # ── embed_batch called once, with only the 2 missing labels ──────────────
    mock_embed.embed_batch.assert_called_once()
    called_labels: list[str] = mock_embed.embed_batch.call_args[0][0]
    assert set(called_labels) == {"Starship", "mRNA"}, (
        f"embed_batch must receive only missing labels; got {called_labels}"
    )

    # ── All 4 nodes present with 1536-dim embeddings ──────────────────────────
    assert len(resolved.nodes) == 4, f"Expected 4 nodes, got {len(resolved.nodes)}"
    for node in resolved.nodes:
        assert node.embedding is not None, f"Node '{node.label}' has no embedding"
        assert len(node.embedding) == _DIM, (
            f"Node '{node.label}' embedding has wrong dim: {len(node.embedding)}"
        )

    # ── BYOV embeddings are preserved exactly (not overwritten) ──────────────
    result_map = {n.label: n for n in resolved.nodes}
    assert result_map["QuantumComputer"].embedding == _byov_vec(0.11), (
        "BYOV embedding for QuantumComputer was overwritten"
    )
    assert result_map["CRISPR"].embedding == _byov_vec(0.22), (
        "BYOV embedding for CRISPR was overwritten"
    )


# ---------------------------------------------------------------------------
# test_mismatch_api_context
# ---------------------------------------------------------------------------


async def test_mismatch_api_context() -> None:
    """
    A node with a 768-dim embedding (expected 1536) passed to an API caller
    must raise HTTPException(422) immediately — before embed_batch is called.
    """
    from app.services.entity_resolver import EntityResolver

    bad_node = Node(label="MismatchNode", type="CONCEPT")
    bad_node.embedding = _wrong_dim_vec()  # 768 dims — wrong

    payload = GraphPayload(nodes=[bad_node], edges=[])

    mock_embed = _make_async_embed_mock([[]])  # must NOT be called
    mock_gs = _make_mock_graph_service([])
    resolver = EntityResolver(embedding_service=mock_embed)

    with pytest.raises(HTTPException) as exc_info:
        await resolver.resolve(
            payload, agent_id="test-agent", graph_service=mock_gs, caller_context="api"
        )

    # ── Correct HTTP status and message ──────────────────────────────────────
    assert exc_info.value.status_code == 422, (
        f"Expected 422, got {exc_info.value.status_code}"
    )
    assert "1536" in str(exc_info.value.detail), (
        "Error detail must contain the expected dimension (1536)"
    )
    assert "768" in str(exc_info.value.detail), (
        "Error detail must contain the received dimension (768)"
    )

    # ── embed_batch must NOT have been called (fail-fast) ────────────────────
    mock_embed.embed_batch.assert_not_called()


# ---------------------------------------------------------------------------
# test_mismatch_kafka_context
# ---------------------------------------------------------------------------


async def test_mismatch_kafka_context(caplog: pytest.LogCaptureFixture) -> None:
    """
    A node with a 768-dim embedding passed to a Kafka caller must NOT raise.
    Instead the resolver must:
      1. Log a WARNING containing "dimension mismatch".
      2. Pass the node's label to embed_batch() for re-embedding.
      3. Return a payload with a correctly dimensioned embedding.
    """
    from app.services.entity_resolver import EntityResolver

    bad_node = Node(label="MismatchNode", type="CONCEPT")
    bad_node.embedding = _wrong_dim_vec()  # 768 dims — wrong

    payload = GraphPayload(nodes=[bad_node], edges=[])

    fresh_emb = _byov_vec(0.99)
    mock_embed = _make_async_embed_mock([[fresh_emb]])
    mock_gs = _make_mock_graph_service([])
    resolver = EntityResolver(embedding_service=mock_embed)

    with caplog.at_level(logging.WARNING, logger="app.services.entity_resolver"):
        resolved = await resolver.resolve(
            payload, agent_id="kafka-agent", graph_service=mock_gs, caller_context="kafka"
        )

    # ── No exception raised ───────────────────────────────────────────────────
    assert resolved is not None

    # ── Warning was logged ────────────────────────────────────────────────────
    warning_messages = [
        r.message for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert any("dimension mismatch" in str(m) for m in warning_messages), (
        f"Expected a 'dimension mismatch' warning; got log records: {warning_messages}"
    )

    # ── embed_batch called once with the mismatched node's label ─────────────
    mock_embed.embed_batch.assert_called_once()
    called_labels: list[str] = mock_embed.embed_batch.call_args[0][0]
    assert called_labels == ["MismatchNode"], (
        f"embed_batch must receive the mismatched label; got {called_labels}"
    )

    # ── Final embedding has correct dimension ─────────────────────────────────
    assert len(resolved.nodes) == 1
    assert resolved.nodes[0].embedding == fresh_emb, (
        "Node embedding was not replaced with the fresh embed_batch result"
    )


# ---------------------------------------------------------------------------
# test_integration_byov
# ---------------------------------------------------------------------------


async def test_integration_byov() -> None:
    """
    Integration test: a BYOV node injected through the full resolve() pipeline
    with caller_context="api" (the path taken by /ingest/sync).

    Guarantees
    ----------
    * The original vector is byte-for-byte identical in the resolved payload.
    * embed_batch() is never called (zero embedding API cost for BYOV nodes).
    * The node survives the deduplication pipeline (no false merge against
      an empty graph).
    """
    from app.services.entity_resolver import EntityResolver

    # Build a reproducible, unique BYOV vector that would be impossible to
    # accidentally reproduce from embed_batch (filled with a non-trivial pattern).
    ORIGINAL_VECTOR: list[float] = [float(i % 256) / 256.0 for i in range(_DIM)]

    byov_node = Node(label="NeuralInterface", type="TECHNOLOGY")
    byov_node.embedding = ORIGINAL_VECTOR.copy()  # defensive copy

    payload = GraphPayload(nodes=[byov_node], edges=[])

    # embed_batch must never be reached — if it is, the test will fail via
    # assert_not_called() below.
    mock_embed = _make_async_embed_mock([])
    mock_gs = _make_mock_graph_service([])  # empty graph — no existing nodes
    resolver = EntityResolver(embedding_service=mock_embed)

    resolved = await resolver.resolve(
        payload,
        agent_id="api-integration-agent",
        graph_service=mock_gs,
        caller_context="api",
    )

    # ── Node present and not merged away ─────────────────────────────────────
    assert len(resolved.nodes) == 1, (
        f"Expected 1 node in resolved payload; got {len(resolved.nodes)}"
    )
    result_node = resolved.nodes[0]
    assert result_node.id == byov_node.id, "Node ID must not change during resolve()"

    # ── Original vector preserved exactly ────────────────────────────────────
    assert result_node.embedding == ORIGINAL_VECTOR, (
        "BYOV vector was modified during resolve() — BYOV contract violated"
    )

    # ── embedding service never touched ──────────────────────────────────────
    mock_embed.embed_batch.assert_not_called()


def test_build_embed_text_uses_full_semantic_surface():
    """Non-FACT nodes must embed label + description + source_text — a bare
    label ('Olivia') is never near a question embedding ('who is the CTO?')."""
    from app.services.entity_resolver import build_embed_text

    n = Node(
        label="Olivia", type="PERSON",
        properties={"description": "CTO who confirmed the review",
                    "source_text": "Olivia (CTO) confirmed launch-readiness review"},
    )
    text = build_embed_text(n)
    assert "Olivia" in text and "CTO who confirmed the review" in text
    assert "launch-readiness review" in text

    # FACT nodes keep their full-description behaviour
    fact = Node(label="f", type="FACT", description="full ingested paragraph")
    assert build_embed_text(fact) == "full ingested paragraph"

    # Label-only nodes degrade gracefully
    bare = Node(label="Initech", type="ORGANIZATION")
    assert build_embed_text(bare) == "Initech"

    # source_text is bounded
    big = Node(label="X", type="CONCEPT", properties={"source_text": "y" * 1000})
    assert len(build_embed_text(big)) < 400


# ---------------------------------------------------------------------------
# test_resolve_never_merges_fact_nodes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_never_merges_fact_nodes():
    """FACT nodes are atomic, append-only records and must never be deduped —
    even when an embedding-similar OR identically-labelled FACT already exists.

    Regression: distinct facts sharing a template ("project X for client A,
    budget $1M" vs "project Y for client B, budget $2M") embed highly similar
    and were otherwise silently merged, dropping ingested facts. Only entities
    resolve; facts never do.
    """
    new_fact = _make_node("fact: budget was 4,440,000", node_type="FACT")
    payload = GraphPayload(nodes=[new_fact], edges=[])

    # Existing FACT with the SAME label (would exact-match in Phase 1) and the
    # SAME embedding (would cosine-match in Phase 2) — both must be bypassed.
    existing_fact = _make_node(
        "fact: budget was 4,440,000", node_type="FACT", node_id="existing-fact-id"
    )
    mock_gs = _make_mock_graph_service([existing_fact])
    e = _unit_vec([1.0, 0.0])
    mock_es = _make_mock_embedding_service([[e], [e]])

    from app.services.entity_resolver import EntityResolver
    resolver = EntityResolver(embedding_service=mock_es)
    resolved = await resolver.resolve(payload, agent_id="test-agent", graph_service=mock_gs)

    new_ids = {n.id for n in resolved.nodes}
    assert new_fact.id in new_ids, "FACT nodes must never be merged away"
