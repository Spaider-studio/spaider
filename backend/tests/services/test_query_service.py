"""
Unit tests for QueryService — (agentic QA loop) and
Unified Cognitive Graph (synaptic score fusion).

Mocking strategy
----------------
``QueryService`` depends on ``GraphService``, ``EmbeddingService``, and
``CognitiveGraphService`` (all network-bound) and makes LLM calls via
``litellm``.  Every external call is replaced with an ``AsyncMock`` so the
tests run in milliseconds with no real network or ML inference.

``_verify_evidence`` is patched directly so tests can control exactly when
the verifier signals sufficiency — this keeps the loop-control assertions
deterministic regardless of LLM availability.

Test 1 — Fast path:
    Verifier returns ``is_sufficient=True`` on the first iteration.
    Assert ``iterations_used == 1`` and ``re_query_happened == False``.

Test 2 — Iterative path:
    Verifier returns insufficient on iteration 1 (with a new query),
    sufficient on iteration 2.
    Assert ``iterations_used == 2``, ``re_query_happened == True``, and
    the cumulative subgraph contains nodes from both retrieval calls.

Test 3 — Synaptic Score algorithm (Unified Cognitive Graph):
    Unit-tests for the Python-side ``_synaptic_score_py`` helper:
    • Fresh node (Δt ≈ 0) → score ≈ utility × energy.
    • Old node with high utility ranks BELOW a fresh node with medium
      utility — temporal decay dominates raw weight.
    • Consolidation (high retrieval_count) slows decay.

Test 4 — V2 Managed Forgetting uses synaptic score:
    A stale high-utility edge (synaptic_score < 0.3) must be excluded from
    the V2 subgraph while a fresh moderate-utility edge survives.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.models.schemas import Edge, GraphPayload, Node, VerifierResult
from app.services.query_service import QueryService, QueryResult, _synaptic_score_py


# ---------------------------------------------------------------------------
# Helpers — fake graph / embedding / cognitive services
# ---------------------------------------------------------------------------


def _node(node_id: str, label: str = "TestNode") -> Node:
    return Node(id=node_id, label=label, type="CONCEPT")


def _make_subgraph(*node_ids: str) -> GraphPayload:
    return GraphPayload(nodes=[_node(nid) for nid in node_ids], edges=[])


def _make_query_service() -> tuple[QueryService, MagicMock, MagicMock]:
    """Return (service, mock_graph, mock_embedding) with all IO stubbed out."""
    mock_graph = MagicMock()
    mock_embedding = MagicMock()
    mock_cognitive = MagicMock()

    # async stubs on graph service
    mock_graph.vector_search = AsyncMock(return_value=[])
    mock_graph.search_nodes = AsyncMock(return_value=[])
    mock_graph.get_subgraph = AsyncMock(return_value=GraphPayload(nodes=[], edges=[]))
    mock_graph._driver = MagicMock()

    # embedding stub
    mock_embedding.embed = AsyncMock(return_value=[0.1] * 8)

    # cognitive stub
    mock_cognitive.boost_nodes = AsyncMock(return_value=None)

    svc = QueryService.__new__(QueryService)
    svc._graph = mock_graph
    svc._embedding = mock_embedding
    svc._redis = False  # disable Redis cache
    svc._cognitive = mock_cognitive
    return svc, mock_graph, mock_embedding


# ---------------------------------------------------------------------------
# Shared patches applied to every test in this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_engine_and_swarm(monkeypatch):
    """Make engine version and swarm context deterministic across all tests."""
    async def _engine_v1(self):
        return "v1"

    async def _clearance(self, agent_id):
        return 5  # admin — no clearance filtering

    async def _swarm(self, target_agent_id, query, agent_clearance=1, top_k=None):
        return "Swarm context text.", ["agent-1"]

    monkeypatch.setattr(QueryService, "_get_engine_version", _engine_v1)
    monkeypatch.setattr(QueryService, "_get_agent_clearance", _clearance)
    monkeypatch.setattr(QueryService, "retrieve_swarm_context", _swarm)
    monkeypatch.setattr(QueryService, "_cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(QueryService, "_cache_set", AsyncMock(return_value=None))


# ---------------------------------------------------------------------------
# Test 1 — Fast path: verifier is sufficient on iteration 1
# ---------------------------------------------------------------------------


class TestFastPath:
    @pytest.mark.asyncio
    async def test_single_iteration_no_requery(self, monkeypatch):
        svc, mock_graph, _ = _make_query_service()

        # Seed nodes returned by vector search
        seed_node = _node("n-seed-1", "SeedEntity")
        mock_graph.vector_search = AsyncMock(return_value=[seed_node])

        # 1-hop expansion returns one additional node
        expanded_node = _node("n-expand-1", "ExpandedEntity")
        mock_graph.get_subgraph = AsyncMock(
            return_value=GraphPayload(nodes=[seed_node, expanded_node], edges=[])
        )

        # Verifier: sufficient on first call
        sufficient_result = VerifierResult(
            is_sufficient=True,
            confidence=0.95,
            missing_information_categories=[],
            next_search_query=None,
        )

        # Final synthesis answer
        async def _answer(self, question, context, *, is_swarm=False, v2_mode=False):
            return "The answer is 42."

        monkeypatch.setattr(QueryService, "_verify_evidence", AsyncMock(return_value=sufficient_result))
        monkeypatch.setattr(QueryService, "_answer_with_context", _answer)

        result = await svc.query_nl("What is the meaning of life?", "agent-1")

        # ── Assertions ──────────────────────────────────────────────────────
        assert result.iterations_used == 1
        assert result.re_query_happened is False
        assert result.confidence_score == pytest.approx(0.95)
        assert result.verifier_feedback is None  # nothing was missing

        # Verify _verify_evidence was called exactly once
        svc._verify_evidence.assert_awaited_once()

        # search_nodes should NOT have been called — no re-query needed
        mock_graph.search_nodes.assert_not_called()

        # Subgraph should contain both seed and expanded nodes
        node_ids = {n.id for n in result.subgraph.nodes}
        assert "n-seed-1" in node_ids
        assert "n-expand-1" in node_ids


# ---------------------------------------------------------------------------
# Test 2 — Iterative path: insufficient iter 1, sufficient iter 2
# ---------------------------------------------------------------------------


class TestIterativePath:
    @pytest.mark.asyncio
    async def test_two_iterations_with_node_accumulation(self, monkeypatch):
        svc, mock_graph, _ = _make_query_service()

        # ── Iter 1: vector search returns first batch of nodes ───────────────
        seed_iter1 = [_node("n-iter1-seed")]
        expand_iter1 = GraphPayload(
            nodes=[_node("n-iter1-seed"), _node("n-iter1-a"), _node("n-iter1-b")],
            edges=[],
        )
        mock_graph.vector_search = AsyncMock(return_value=seed_iter1)

        # ── Iter 2: text search (re-query) returns new nodes ─────────────────
        seed_iter2 = [_node("n-iter2-seed")]
        expand_iter2 = GraphPayload(
            nodes=[_node("n-iter2-seed"), _node("n-iter2-a")],
            edges=[],
        )

        # get_subgraph returns different expansions per call
        mock_graph.get_subgraph = AsyncMock(
            side_effect=[expand_iter1, expand_iter2]
        )
        # search_nodes called only on iter 2 with the verifier's new query
        mock_graph.search_nodes = AsyncMock(return_value=seed_iter2)

        # ── Verifier: insufficient on iter 1, sufficient on iter 2 ───────────
        insufficient = VerifierResult(
            is_sufficient=False,
            confidence=0.35,
            missing_information_categories=["founding year", "revenue figures"],
            next_search_query="Acme Corp founding year revenue",
        )
        sufficient = VerifierResult(
            is_sufficient=True,
            confidence=0.88,
            missing_information_categories=[],
            next_search_query=None,
        )
        verify_mock = AsyncMock(side_effect=[insufficient, sufficient])

        async def _answer(self, question, context, *, is_swarm=False, v2_mode=False):
            return "Acme Corp was founded in 1990."

        monkeypatch.setattr(QueryService, "_verify_evidence", verify_mock)
        monkeypatch.setattr(QueryService, "_answer_with_context", _answer)

        result = await svc.query_nl("When was Acme Corp founded?", "agent-1")

        # ── Loop-control assertions ──────────────────────────────────────────
        assert result.iterations_used == 2
        assert result.re_query_happened is True
        assert result.confidence_score == pytest.approx(0.88)

        # Both missing categories from iter 1 should appear in feedback
        assert result.verifier_feedback is not None
        assert "founding year" in result.verifier_feedback
        assert "revenue figures" in result.verifier_feedback

        # Verifier called twice
        assert verify_mock.await_count == 2

        # search_nodes called exactly once — only for the re-query
        mock_graph.search_nodes.assert_awaited_once_with(
            query="Acme Corp founding year revenue",
            agent_id="agent-1",
            limit=8,  # _DEFAULT_TOP_K
        )

        # ── Node accumulation assertions ─────────────────────────────────────
        # Cumulative subgraph must contain nodes from BOTH iterations.
        accumulated_ids = {n.id for n in result.subgraph.nodes}
        # Iter 1 nodes
        assert "n-iter1-seed" in accumulated_ids
        assert "n-iter1-a" in accumulated_ids
        assert "n-iter1-b" in accumulated_ids
        # Iter 2 nodes
        assert "n-iter2-seed" in accumulated_ids
        assert "n-iter2-a" in accumulated_ids

    @pytest.mark.asyncio
    async def test_iteration_cap_enforced(self, monkeypatch):
        """Loop must stop at max_qa_iterations even if verifier always returns False."""
        from app.config import settings

        svc, mock_graph, _ = _make_query_service()
        mock_graph.vector_search = AsyncMock(return_value=[])
        mock_graph.search_nodes = AsyncMock(return_value=[])

        always_insufficient = VerifierResult(
            is_sufficient=False,
            confidence=0.1,
            missing_information_categories=["everything"],
            next_search_query="more info needed",
        )
        verify_mock = AsyncMock(return_value=always_insufficient)

        async def _answer(self, question, context, *, is_swarm=False, v2_mode=False):
            return "Best effort answer."

        monkeypatch.setattr(QueryService, "_verify_evidence", verify_mock)
        monkeypatch.setattr(QueryService, "_answer_with_context", _answer)

        result = await svc.query_nl("Unanswerable question?", "agent-1")

        # Must not exceed the configured cap
        assert result.iterations_used <= settings.max_qa_iterations
        assert result.re_query_happened is True

    @pytest.mark.asyncio
    async def test_no_requery_when_next_query_is_none(self, monkeypatch):
        """Loop must stop if verifier returns is_sufficient=False but no next_search_query."""
        svc, mock_graph, _ = _make_query_service()
        seed = _node("n-seed")
        mock_graph.vector_search = AsyncMock(return_value=[seed])
        mock_graph.get_subgraph = AsyncMock(
            return_value=GraphPayload(nodes=[seed], edges=[])
        )

        no_next_query = VerifierResult(
            is_sufficient=False,
            confidence=0.2,
            missing_information_categories=["unknown"],
            next_search_query=None,  # verifier couldn't formulate a re-query
        )
        verify_mock = AsyncMock(return_value=no_next_query)

        async def _answer(self, question, context, *, is_swarm=False, v2_mode=False):
            return "Partial answer."

        monkeypatch.setattr(QueryService, "_verify_evidence", verify_mock)
        monkeypatch.setattr(QueryService, "_answer_with_context", _answer)

        result = await svc.query_nl("Unknown topic?", "agent-1")

        # Loop breaks after 1 iteration — no re-query without next_search_query
        assert result.iterations_used == 1
        assert result.re_query_happened is False
        mock_graph.search_nodes.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers for cognitive tests
# ---------------------------------------------------------------------------


def _node_with_props(node_id: str, **props) -> Node:
    """Create a Node carrying explicit cognitive properties."""
    return Node(id=node_id, label="TestNode", type="CONCEPT", properties=dict(props))


# ---------------------------------------------------------------------------
# Test 3 — Synaptic Score algorithm unit tests
# ---------------------------------------------------------------------------


class TestSynapticScorePy:
    @pytest.mark.smoke
    def test_fresh_node_no_meaningful_decay(self):
        """A node activated right now has exp decay ≈ 1, so score ≈ utility × energy."""
        now_iso = datetime.now(timezone.utc).isoformat()
        node = _node_with_props(
            "n-fresh",
            energy_level=1.0,
            retrieval_count=1,
            last_activation=now_iso,
        )
        score = _synaptic_score_py(1.5, node)
        # Δt ≈ 0  →  decay ≈ 1.0  →  score ≈ 1.5 * 1.0
        assert score == pytest.approx(1.5, abs=0.02)

    def test_never_activated_uses_zero_delta_t(self):
        """Node with no last_activation defaults Δt=0 → score = utility × energy."""
        node = _node_with_props("n-none", energy_level=0.8, retrieval_count=5)
        score = _synaptic_score_py(1.0, node)
        assert score == pytest.approx(0.8, abs=0.001)

    def test_none_node_yields_unit_defaults(self):
        """Passing node=None should fall back to E=1, rc=1, Δt=0 → score = utility."""
        score = _synaptic_score_py(1.2, None)
        assert score == pytest.approx(1.2, abs=0.001)

    def test_old_high_utility_ranks_below_fresh_medium_utility(self):
        """
        Algorithmic proof — temporal decay must dominate raw utility:

          Old node:   U=2.0, E=1.0, rc=1, Δt=72 h
            λ = 0.05/(1+0.1·√1) ≈ 0.04545
            score = 2.0 · exp(−0.04545·72) ≈ 0.076

          Fresh node: U=0.8, E=1.0, rc=1, Δt=1 h
            score = 0.8 · exp(−0.04545·1) ≈ 0.764

        old_score (0.076) must be strictly less than fresh_score (0.764).
        """
        now = datetime.now(timezone.utc)

        old_node = _node_with_props(
            "n-old",
            energy_level=1.0,
            retrieval_count=1,
            last_activation=(now - timedelta(hours=72)).isoformat(),
        )
        fresh_node = _node_with_props(
            "n-fresh",
            energy_level=1.0,
            retrieval_count=1,
            last_activation=(now - timedelta(hours=1)).isoformat(),
        )

        old_score = _synaptic_score_py(2.0, old_node)
        fresh_score = _synaptic_score_py(0.8, fresh_node)

        lam = 0.05 / (1.0 + 0.1 * math.sqrt(1))
        expected_old = 2.0 * 1.0 * math.exp(-lam * 72)
        expected_fresh = 0.8 * 1.0 * math.exp(-lam * 1)

        assert old_score == pytest.approx(expected_old, rel=0.01)
        assert fresh_score == pytest.approx(expected_fresh, rel=0.01)
        assert old_score < fresh_score, (
            f"Old high-utility node (score={old_score:.4f}) must rank LOWER than "
            f"fresh medium-utility node (score={fresh_score:.4f})"
        )

    def test_consolidation_slows_decay(self):
        """
        A node retrieved 100 times decays slower than one retrieved once.
        Both have the same age — higher retrieval_count → higher remaining score.
        """
        now = datetime.now(timezone.utc)
        age_iso = (now - timedelta(hours=24)).isoformat()

        rarely_retrieved = _node_with_props(
            "n-rare", energy_level=1.0, retrieval_count=1, last_activation=age_iso
        )
        well_consolidated = _node_with_props(
            "n-consolidated", energy_level=1.0, retrieval_count=100, last_activation=age_iso
        )

        rare_score = _synaptic_score_py(1.0, rarely_retrieved)
        consolidated_score = _synaptic_score_py(1.0, well_consolidated)

        assert consolidated_score > rare_score, (
            f"Well-consolidated node (score={consolidated_score:.4f}) must decay "
            f"more slowly than rarely-retrieved node (score={rare_score:.4f})"
        )


# ---------------------------------------------------------------------------
# Test 4 — V2 Managed Forgetting: synaptic score gates edge inclusion
# ---------------------------------------------------------------------------


class TestV2ManagedForgetting:
    @pytest.mark.asyncio
    async def test_stale_high_utility_edge_filtered_in_v2(self, monkeypatch):
        """
        A stale edge (utility_weight=2.0 but Δt=96 h, energy=0.4) has
        synaptic_score < 0.3 and must be dropped from the V2 subgraph.

        A fresh edge (utility_weight=0.7, Δt=2 h, energy=1.0) has
        synaptic_score ≥ 0.3 and must survive.
        """
        now = datetime.now(timezone.utc)

        stale_src = _node_with_props(
            "n-stale-src",
            energy_level=0.4,
            retrieval_count=1,
            last_activation=(now - timedelta(hours=96)).isoformat(),
        )
        fresh_src = _node_with_props(
            "n-fresh-src",
            energy_level=1.0,
            retrieval_count=1,
            last_activation=(now - timedelta(hours=2)).isoformat(),
        )
        target = _node("n-target")

        stale_edge = Edge(
            id="e-stale",
            source_id="n-stale-src",
            target_id="n-target",
            relation="RELATES_TO",
            utility_weight=2.0,
        )
        fresh_edge = Edge(
            id="e-fresh",
            source_id="n-fresh-src",
            target_id="n-target",
            relation="RELATES_TO",
            utility_weight=0.7,
        )

        # Verify the arithmetic before wiring up the service
        stale_score = _synaptic_score_py(stale_edge.utility_weight, stale_src)
        fresh_score = _synaptic_score_py(fresh_edge.utility_weight, fresh_src)
        assert stale_score < 0.3, (
            f"Pre-condition: stale score must be < 0.3, got {stale_score:.4f}"
        )
        assert fresh_score >= 0.3, (
            f"Pre-condition: fresh score must be >= 0.3, got {fresh_score:.4f}"
        )

        # ── Service in V2 mode ────────────────────────────────────────────────
        svc, mock_graph, _ = _make_query_service()

        # Override the autouse fixture's V1 engine patch
        monkeypatch.setattr(
            QueryService, "_get_engine_version", AsyncMock(return_value="v2")
        )
        monkeypatch.setattr(
            QueryService,
            "retrieve_swarm_context_v2",
            AsyncMock(return_value=("V2 context.", ["agent-1"])),
        )

        mock_graph.vector_search = AsyncMock(return_value=[stale_src, fresh_src])
        mock_graph.get_subgraph = AsyncMock(
            return_value=GraphPayload(
                nodes=[stale_src, fresh_src, target],
                edges=[stale_edge, fresh_edge],
            )
        )

        monkeypatch.setattr(
            QueryService,
            "_verify_evidence",
            AsyncMock(return_value=VerifierResult(
                is_sufficient=True,
                confidence=0.9,
                missing_information_categories=[],
                next_search_query=None,
            )),
        )
        monkeypatch.setattr(
            QueryService,
            "_answer_with_context",
            AsyncMock(return_value="V2 answer."),
        )

        result = await svc.query_nl("V2 forgetting test?", "agent-1")

        edge_ids = {e.id for e in result.subgraph.edges}
        assert "e-fresh" in edge_ids, "Fresh edge must survive V2 synaptic filtering"
        assert "e-stale" not in edge_ids, (
            "Stale high-utility edge must be excluded: synaptic_score "
            f"({stale_score:.4f}) is below the V2 forget threshold (0.3)"
        )


# ---------------------------------------------------------------------------
# Question decomposition (multi-subquery seed expansion)
# ---------------------------------------------------------------------------


def _make_decompose_response(content: str) -> MagicMock:
    """Shape a fake litellm acompletion response with the given content."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.mark.asyncio
async def test_decompose_question_returns_parsed_array(monkeypatch):
    """Happy path — LLM returns valid JSON array, helper passes it through."""
    svc, _, _ = _make_query_service()
    fake = _make_decompose_response(
        '["What is Scott Derrickson\'s nationality?", "What is Ed Wood\'s nationality?"]'
    )
    monkeypatch.setattr(
        "litellm.acompletion", AsyncMock(return_value=fake)
    )
    out = await svc._decompose_question(
        "Were Scott Derrickson and Ed Wood of the same nationality?"
    )
    assert out == [
        "What is Scott Derrickson's nationality?",
        "What is Ed Wood's nationality?",
    ]


@pytest.mark.asyncio
async def test_decompose_question_strips_code_fence(monkeypatch):
    """Some models wrap JSON in ```json fences. Helper strips them."""
    svc, _, _ = _make_query_service()
    fake = _make_decompose_response('```json\n["sub1", "sub2"]\n```')
    monkeypatch.setattr(
        "litellm.acompletion", AsyncMock(return_value=fake)
    )
    assert await svc._decompose_question("anything") == ["sub1", "sub2"]


@pytest.mark.asyncio
async def test_decompose_question_caps_at_max_subqueries(monkeypatch):
    """The router MUST NOT explode retrieval cost. Cap is enforced even if
    the LLM ignores the prompt's "1 to 5" hint."""
    from app.services.query_service import _DECOMPOSITION_MAX_SUBQUERIES
    svc, _, _ = _make_query_service()
    too_many = [f"sub{i}" for i in range(10)]
    import json
    fake = _make_decompose_response(json.dumps(too_many))
    monkeypatch.setattr(
        "litellm.acompletion", AsyncMock(return_value=fake)
    )
    out = await svc._decompose_question("anything")
    assert len(out) == _DECOMPOSITION_MAX_SUBQUERIES


@pytest.mark.asyncio
async def test_decompose_question_falls_back_on_llm_error(monkeypatch):
    """LLM call raises → fall back to single-shot retrieval. Never blocks
    the query pipeline."""
    svc, _, _ = _make_query_service()
    monkeypatch.setattr(
        "litellm.acompletion",
        AsyncMock(side_effect=RuntimeError("api down")),
    )
    out = await svc._decompose_question("Some question?")
    assert out == ["Some question?"]


@pytest.mark.asyncio
async def test_decompose_question_falls_back_on_malformed_json(monkeypatch):
    svc, _, _ = _make_query_service()
    fake = _make_decompose_response("not json at all")
    monkeypatch.setattr(
        "litellm.acompletion", AsyncMock(return_value=fake)
    )
    assert await svc._decompose_question("Some question?") == ["Some question?"]


@pytest.mark.asyncio
async def test_decompose_question_falls_back_on_empty_array(monkeypatch):
    svc, _, _ = _make_query_service()
    fake = _make_decompose_response("[]")
    monkeypatch.setattr(
        "litellm.acompletion", AsyncMock(return_value=fake)
    )
    assert await svc._decompose_question("Some question?") == ["Some question?"]


@pytest.mark.asyncio
async def test_decompose_question_dedupes_and_trims_whitespace(monkeypatch):
    svc, _, _ = _make_query_service()
    fake = _make_decompose_response(
        '["  what about A?  ", "what about A?", "what about B?"]'
    )
    monkeypatch.setattr(
        "litellm.acompletion", AsyncMock(return_value=fake)
    )
    assert await svc._decompose_question("anything") == [
        "what about A?", "what about B?",
    ]


# ---------------------------------------------------------------------------
# MMR Semantic Diversity (feat/mmr-semantic-diversity)
# ---------------------------------------------------------------------------

import numpy as np
from app.services.query_service import apply_mmr


class TestApplyMMR:
    def test_returns_k_nodes_from_larger_pool(self):
        nodes = [{"id": str(i)} for i in range(10)]
        emb = np.eye(10)            # each node has a perfectly unique embedding
        scores = np.ones(10)
        result = apply_mmr(nodes, emb, scores, k=4)
        assert len(result) == 4

    def test_returns_all_when_pool_smaller_than_k(self):
        nodes = [{"id": "a"}, {"id": "b"}]
        emb = np.array([[1.0, 0.0], [0.0, 1.0]])
        scores = np.array([0.9, 0.8])
        result = apply_mmr(nodes, emb, scores, k=10)
        assert len(result) == 2

    def test_empty_pool_returns_empty(self):
        result = apply_mmr([], np.empty((0, 4)), np.empty(0), k=5)
        assert result == []

    def test_pure_relevance_lambda_1_matches_top_k_by_score(self):
        """λ=1 collapses MMR to greedy top-K relevance; order must follow scores."""
        nodes = [{"id": str(i)} for i in range(5)]
        # Orthogonal embeddings so similarity never interferes
        emb = np.eye(5)
        scores = np.array([0.1, 0.9, 0.5, 0.8, 0.3])
        result = apply_mmr(nodes, emb, scores, k=3, lambda_param=1.0)
        # Expected order: indices 1 (0.9), 3 (0.8), 2 (0.5)
        assert result[0]["id"] == "1"
        assert result[1]["id"] == "3"
        assert result[2]["id"] == "2"

    def test_diversity_avoids_near_duplicate_nodes(self):
        """With λ=0.0 (pure diversity), two nearly identical nodes should not
        both appear in the selection when a diverse alternative exists."""
        # Node 0 and node 1 are near-duplicates; node 2 is orthogonal.
        nodes = [{"id": "dup-a"}, {"id": "dup-b"}, {"id": "diverse"}]
        emb = np.array([
            [1.0, 0.0, 0.0],   # dup-a
            [0.99, 0.14, 0.0], # dup-b  — almost identical to dup-a
            [0.0, 0.0, 1.0],   # diverse — orthogonal
        ])
        scores = np.array([1.0, 1.0, 0.5])   # duplicates score higher
        # With λ=0 the second pick should prefer 'diverse' over 'dup-b'
        result = apply_mmr(nodes, emb, scores, k=2, lambda_param=0.0)
        ids = [r["id"] for r in result]
        assert "diverse" in ids

    def test_identical_scores_do_not_cause_division_by_zero(self):
        nodes = [{"id": str(i)} for i in range(5)]
        emb = np.eye(5)
        scores = np.full(5, 0.7)   # all identical — triggers the s_max==s_min branch
        result = apply_mmr(nodes, emb, scores, k=3)
        assert len(result) == 3

    def test_minmax_normalisation_neutralises_ucb_sentinel(self):
        """UCB sentinel value 1000.0 must not dominate; after normalisation the
        sentinel node ties with others and diversity can still win."""
        nodes = [{"id": "sentinel"}, {"id": "low"}, {"id": "diverse"}]
        emb = np.array([
            [1.0, 0.0],   # sentinel
            [0.98, 0.2],  # low — similar to sentinel
            [0.0, 1.0],   # diverse — orthogonal
        ])
        scores = np.array([1000.0, 0.5, 0.4])
        # After normalisation: sentinel=1.0, low≈0.0, diverse≈0.0
        # First pick = sentinel (highest norm_score with no selected yet).
        # Second pick with λ=0.5: diverse gains because low is similar to sentinel.
        result = apply_mmr(nodes, emb, scores, k=2, lambda_param=0.5)
        assert result[0]["id"] == "sentinel"
        assert result[1]["id"] == "diverse"

    def test_zero_magnitude_embeddings_handled_gracefully(self):
        nodes = [{"id": "zero"}, {"id": "normal"}]
        emb = np.array([[0.0, 0.0], [1.0, 0.0]])
        scores = np.array([0.9, 0.5])
        result = apply_mmr(nodes, emb, scores, k=2)
        assert len(result) == 2
# Synthesis Ensemble — (Best-of-N verifier ranking)
# ---------------------------------------------------------------------------

from unittest.mock import patch as stdlib_patch


def _llm_response(content: str):
    """Build a minimal fake litellm response object."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


class TestSynthesisEnsemble:

    @pytest.mark.asyncio
    async def test_best_of_n_returns_highest_confidence_candidate(self, monkeypatch):
        """Core correctness: 4 generation calls (3 succeed, 1 raises), verifier assigns
        distinct confidence scores, result must be the highest-confidence answer."""
        svc, _, _ = _make_query_service()

        # ── Mock litellm: 3 successes + 1 exception interleaved ───────────────
        acomp_mock = AsyncMock(side_effect=[
            _llm_response("Answer A"),          # confidence → 0.60
            _llm_response("Answer B"),          # confidence → 0.92  ← winner
            RuntimeError("upstream 502"),        # must be discarded silently
            _llm_response("Answer C"),          # confidence → 0.75
        ])

        # ── Mock verifier: score by which answer string is in the context ─────
        confidence_map = {"Answer A": 0.60, "Answer B": 0.92, "Answer C": 0.75}

        async def _mock_verify(self_arg, question, context_text, mode="sufficiency"):
            for ans, conf in confidence_map.items():
                if ans in context_text:
                    return VerifierResult(
                        is_sufficient=True, confidence=conf,
                        missing_information_categories=[], next_search_query=None,
                    )
            return VerifierResult(
                is_sufficient=True, confidence=0.5,
                missing_information_categories=[], next_search_query=None,
            )

        monkeypatch.setattr(QueryService, "_verify_evidence", _mock_verify)

        with stdlib_patch("app.services.query_service.settings") as mock_cfg:
            mock_cfg.synthesis_ensemble_n = 4
            mock_cfg.synthesis_ensemble_temperature = 0.7
            mock_cfg.litellm_model = "gpt-4o-mini"
            mock_cfg.llm_base_url = None
            mock_cfg.llm_api_key = ""
            with stdlib_patch("litellm.acompletion", acomp_mock):
                answer = await svc._answer_with_context(
                    "What is X?", "Some graph context."
                )

        assert answer == "Answer B"
        # Exception was swallowed — only 3 candidates were evaluated
        assert acomp_mock.call_count == 4

    @pytest.mark.asyncio
    async def test_exception_in_generation_does_not_crash(self, monkeypatch):
        """A single generation failure must be silently discarded, not propagated."""
        svc, _, _ = _make_query_service()

        acomp_mock = AsyncMock(side_effect=[
            _llm_response("Only survivor"),
            RuntimeError("rate limit"),
        ])

        async def _mock_verify(self_arg, question, context_text, mode="sufficiency"):
            return VerifierResult(
                is_sufficient=True, confidence=0.8,
                missing_information_categories=[], next_search_query=None,
            )

        monkeypatch.setattr(QueryService, "_verify_evidence", _mock_verify)

        with stdlib_patch("app.services.query_service.settings") as mock_cfg:
            mock_cfg.synthesis_ensemble_n = 2
            mock_cfg.synthesis_ensemble_temperature = 0.7
            mock_cfg.litellm_model = "gpt-4o-mini"
            mock_cfg.llm_base_url = None
            mock_cfg.llm_api_key = ""
            with stdlib_patch("litellm.acompletion", acomp_mock):
                answer = await svc._answer_with_context("Q?", "context")

        assert answer == "Only survivor"

    @pytest.mark.asyncio
    async def test_all_generation_failures_raise_runtime_error(self, monkeypatch):
        """All N generation calls failing must raise RuntimeError, not return silently."""
        svc, _, _ = _make_query_service()

        acomp_mock = AsyncMock(side_effect=[
            RuntimeError("fail 1"),
            RuntimeError("fail 2"),
        ])

        with stdlib_patch("app.services.query_service.settings") as mock_cfg:
            mock_cfg.synthesis_ensemble_n = 2
            mock_cfg.synthesis_ensemble_temperature = 0.7
            mock_cfg.litellm_model = "gpt-4o-mini"
            mock_cfg.llm_base_url = None
            mock_cfg.llm_api_key = ""
            with stdlib_patch("litellm.acompletion", acomp_mock):
                with pytest.raises(RuntimeError, match="all 2 generation calls failed"):
                    await svc._answer_with_context("Q?", "context")

    @pytest.mark.asyncio
    async def test_n_equals_1_uses_fast_path_single_call(self, monkeypatch):
        """N=1 must hit the fast path — exactly one acompletion call, no verifier."""
        svc, _, _ = _make_query_service()

        acomp_mock = AsyncMock(return_value=_llm_response("Fast answer"))
        verify_mock = AsyncMock()
        monkeypatch.setattr(QueryService, "_verify_evidence", verify_mock)

        with stdlib_patch("app.services.query_service.settings") as mock_cfg:
            mock_cfg.synthesis_ensemble_n = 1
            mock_cfg.litellm_model = "gpt-4o-mini"
            mock_cfg.llm_base_url = None
            mock_cfg.llm_api_key = ""
            with stdlib_patch("litellm.acompletion", acomp_mock):
                answer = await svc._answer_with_context("Q?", "context")

        assert answer == "Fast answer"
        acomp_mock.assert_awaited_once()
        verify_mock.assert_not_awaited()   # verifier never called on fast path

    @pytest.mark.asyncio
    async def test_all_verifications_fail_returns_first_candidate(self, monkeypatch):
        """If every verification call fails, the first valid candidate is returned
        as a fallback rather than crashing the caller."""
        svc, _, _ = _make_query_service()

        acomp_mock = AsyncMock(side_effect=[
            _llm_response("Candidate 1"),
            _llm_response("Candidate 2"),
        ])

        async def _failing_verify(self_arg, question, context_text, mode="sufficiency"):
            raise RuntimeError("verifier LLM down")

        monkeypatch.setattr(QueryService, "_verify_evidence", _failing_verify)

        with stdlib_patch("app.services.query_service.settings") as mock_cfg:
            mock_cfg.synthesis_ensemble_n = 2
            mock_cfg.synthesis_ensemble_temperature = 0.7
            mock_cfg.litellm_model = "gpt-4o-mini"
            mock_cfg.llm_base_url = None
            mock_cfg.llm_api_key = ""
            with stdlib_patch("litellm.acompletion", acomp_mock):
                answer = await svc._answer_with_context("Q?", "context")

        assert answer == "Candidate 1"

    @pytest.mark.asyncio
    async def test_ensemble_invokes_verifier_in_validator_mode(self, monkeypatch):
        """Regression guard for Phase 3: every ensemble verification call MUST
        request mode='validator'. The agentic-QA loop's mode='sufficiency' default
        is reserved for re-query decisions; the ensemble must never use it.

        If this test fails, the ensemble is scoring candidates with the loose
        sufficiency rubric instead of the strict validator rubric — exactly the
        regression that drives F1 negative on the 1536d embedding stack."""
        svc, _, _ = _make_query_service()

        acomp_mock = AsyncMock(side_effect=[
            _llm_response("Candidate alpha"),
            _llm_response("Candidate beta"),
        ])

        observed_modes: list[str] = []

        async def _capturing_verify(self_arg, question, context_text, mode="sufficiency"):
            observed_modes.append(mode)
            return VerifierResult(
                is_sufficient=True, confidence=0.9,
                missing_information_categories=[], next_search_query=None,
            )

        monkeypatch.setattr(QueryService, "_verify_evidence", _capturing_verify)

        with stdlib_patch("app.services.query_service.settings") as mock_cfg:
            mock_cfg.synthesis_ensemble_n = 2
            mock_cfg.synthesis_ensemble_temperature = 0.7
            mock_cfg.litellm_model = "gpt-4o-mini"
            mock_cfg.llm_base_url = None
            mock_cfg.llm_api_key = ""
            with stdlib_patch("litellm.acompletion", acomp_mock):
                await svc._answer_with_context("Q?", "context")

        assert observed_modes == ["validator", "validator"], (
            f"Ensemble must call verifier with mode='validator'; got {observed_modes}"
        )

    @pytest.mark.asyncio
    async def test_verify_evidence_validator_prompt_contains_failure_rubric(
        self, monkeypatch
    ):
        """The validator prompt must explicitly enumerate the failure modes
        (hallucination, drift, contradiction). If somebody soft-edits the prompt
        back to a passive 'is this good?' selector, this test catches it."""
        from app.services.query_service import QueryService as QS

        svc, _, _ = _make_query_service()

        captured_kwargs: dict = {}

        async def _capture_kwargs(**kwargs):
            captured_kwargs.update(kwargs)
            return _llm_response(
                '{"is_sufficient": true, "confidence": 0.95, '
                '"missing_information_categories": [], "next_search_query": null}'
            )

        monkeypatch.setattr(
            "app.services.query_service.acompletion_with_retry", _capture_kwargs
        )

        result = await svc._verify_evidence(
            "Was Tom Hanks born in California?",
            "Tom Hanks was born in Concord, CA.\n\n---\nProposed Answer:\nYes.",
            mode="validator",
        )

        assert isinstance(result, VerifierResult)
        system_prompt = captured_kwargs["messages"][0]["content"].lower()
        for required_token in ("validator", "hallucination", "drift", "contradiction"):
            assert required_token in system_prompt, (
                f"Validator prompt missing required failure-mode token: {required_token!r}"
            )
        # The validator never triggers re-retrieval — sufficiency mode owns that.
        assert "next_search_query=null" in system_prompt.replace(" ", "")

    @pytest.mark.asyncio
    async def test_verify_evidence_sufficiency_mode_unchanged(self, monkeypatch):
        """Regression guard: the agentic-QA loop's sufficiency prompt MUST NOT
        contain the validator-specific rubric tokens. Drift here would change
        re-query behavior and risk iteration-cap hits in the main loop."""
        svc, _, _ = _make_query_service()

        captured_kwargs: dict = {}

        async def _capture_kwargs(**kwargs):
            captured_kwargs.update(kwargs)
            return _llm_response(
                '{"is_sufficient": true, "confidence": 0.8, '
                '"missing_information_categories": [], "next_search_query": null}'
            )

        monkeypatch.setattr(
            "app.services.query_service.acompletion_with_retry", _capture_kwargs
        )

        await svc._verify_evidence("Q?", "Some context.")  # default mode

        system_prompt = captured_kwargs["messages"][0]["content"].lower()
        assert "sufficiency verifier" in system_prompt
        # The validator-mode rubric must NOT leak into sufficiency mode
        for forbidden_token in ("hallucination", "drift", "contradiction", "validator"):
            assert forbidden_token not in system_prompt, (
                f"Sufficiency prompt unexpectedly contains validator token: {forbidden_token!r}"
            )
