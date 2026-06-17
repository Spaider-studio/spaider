"""
Tests for QueryService: NL->Cypher translation, read-only enforcement,
graph traversal, and GraphRAG fallback.

The real QueryService:
  - __init__(graph_service, embedding_service=None)
  - query_nl(question, agent_id) -> QueryResult  (from query_service module)
  - query_cypher(cypher, agent_id) -> list[dict]
  - traverse(start_node_id, depth, relation_filter) -> GraphPayload

The read-only write-pattern guard lives in app.api.v1.query as _WRITE_PATTERN.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: F401

import pytest

from app.models.schemas import Edge, GraphPayload, Node

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(label: str, ntype: str = "PERSON", agent_id: str = "default") -> Node:
    return Node(id=str(uuid.uuid4()), label=label, type=ntype, agent_id=agent_id)


def _make_edge(src: Node, tgt: Node, relation: str = "RELATED_TO") -> Edge:
    return Edge(
        id=str(uuid.uuid4()),
        source_id=src.id,
        target_id=tgt.id,
        source=src.label,
        target=tgt.label,
        relation=relation,
        agent_id=src.agent_id,
    )


def _litellm_cypher_response(cypher: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = cypher
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _litellm_answer_response(answer: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = answer
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def nodes_and_edges():
    n1 = _make_node("Alice")
    n2 = _make_node("Acme", "ORGANIZATION")
    e1 = _make_edge(n1, n2, "WORKS_AT")
    return [n1, n2], [e1]


@pytest.fixture
def mock_graph_service(nodes_and_edges):
    nodes, edges = nodes_and_edges
    gs = AsyncMock()
    gs.get_schema = AsyncMock(return_value={"node_types": ["PERSON", "ORGANIZATION"], "relation_types": ["WORKS_AT"]})
    gs.get_all_nodes = AsyncMock(return_value=nodes)
    gs.get_all_edges = AsyncMock(return_value=edges)
    gs.get_subgraph = AsyncMock(return_value=GraphPayload(nodes=nodes, edges=edges))
    gs.traverse = AsyncMock(return_value=GraphPayload(nodes=nodes, edges=edges))
    gs.vector_search = AsyncMock(return_value=[])
    # Expose _driver with a mock session for query_cypher
    mock_result = AsyncMock()
    mock_result.data = AsyncMock(return_value=[
        {"n": {"id": nodes[0].id, "label": nodes[0].label, "type": nodes[0].type}}
    ])
    mock_session = AsyncMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    gs._driver = MagicMock()
    gs._driver.session.return_value = mock_session
    return gs


@pytest.fixture(autouse=True)
def _hermetic_embedding():
    """Keep every query_service test off the real embedding API.

    QueryService builds a real EmbeddingService when none is injected, and
    query_nl calls ``.embed()`` — which would hit OpenAI (and fail in CI,
    where there's no key). Patch it to a fixed vector; vector_search itself
    is already mocked on the graph service.
    """
    with patch("app.services.query_service.EmbeddingService") as MockEmb:
        inst = AsyncMock()
        inst.embed = AsyncMock(return_value=[0.1] * 1536)
        MockEmb.return_value = inst
        yield


# ---------------------------------------------------------------------------
# test_query_nl_returns_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_nl_returns_result(mock_graph_service, nodes_and_edges):
    """QueryService.query_nl() uses vector search + single LLM call for GraphRAG answers."""
    nodes, edges = nodes_and_edges

    answer_text = "Alice works at Acme."

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _litellm_answer_response(answer_text)

        try:
            from app.services.query_service import QueryService
        except ImportError:
            pytest.skip("QueryService not yet implemented")

        qs = QueryService(graph_service=mock_graph_service)
        # Disable Redis cache so test is deterministic
        qs._cache_get = AsyncMock(return_value=None)
        qs._cache_set = AsyncMock(return_value=None)

        result = await qs.query_nl(question="Who works at Acme?", agent_id="default")

    assert result.question == "Who works at Acme?"
    assert len(result.answer) > 0


# ---------------------------------------------------------------------------
# test_query_nl_retries_on_bad_cypher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_nl_retries_on_bad_cypher(mock_graph_service):
    """QueryService should retry Cypher generation if the first attempt fails."""
    good_cypher = "MATCH (n:SpaiderNode) WHERE n.agent_id = $agent_id RETURN n LIMIT 50"
    answer_text = "Some answer."

    # Return one real-looking record so we don't fall into the graphrag path
    fake_record = {"n": {"id": "x1", "label": "Alice", "type": "PERSON", "properties": {}}}

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        # The V2 retrieval engine replaced V1's generate-and-retry Cypher flow,
        # so this now guards that query_nl runs end-to-end over a mocked graph +
        # LLM without crashing. Non-exhausting mocks: the V2 path issues a
        # variable number of session.run / acompletion calls (retrieval + the
        # QA-verify loop), so a fixed side_effect list would run dry.
        mock_llm.return_value = _litellm_answer_response(answer_text)

        try:
            from app.services.query_service import QueryService
        except ImportError:
            pytest.skip("QueryService not yet implemented")

        qs = QueryService(graph_service=mock_graph_service)

        mock_result_ok = AsyncMock()
        mock_result_ok.data = AsyncMock(return_value=[fake_record])

        mock_session = MagicMock()
        mock_session.run = AsyncMock(return_value=mock_result_ok)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_graph_service._driver.session.return_value = mock_session

        result = await qs.query_nl(question="Who is Alice?", agent_id="default")

    assert result.question == "Who is Alice?"


# ---------------------------------------------------------------------------
# test_cypher_blocks_write_operations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("write_keyword", [
    "CREATE (n:Entity {id: '1'})",
    "MERGE (n:Entity {id: '1'})",
    "DELETE n",
    "MATCH (n) DETACH DELETE n",
    "MATCH (n) SET n.foo = 'bar'",
    "MATCH (n) REMOVE n.foo",
])
def test_cypher_blocks_write_operations(write_keyword: str):
    """The write-operation guard regex must reject all Cypher write keywords."""
    from app.api.v1.query import _WRITE_PATTERN
    assert _WRITE_PATTERN.search(write_keyword) is not None, (
        f"Expected write pattern to match: {write_keyword!r}"
    )


def test_cypher_allows_read_operations():
    """The write-operation guard must NOT flag legitimate read-only Cypher."""
    from app.api.v1.query import _WRITE_PATTERN
    read_queries = [
        "MATCH (n:Entity) WHERE n.agent_id = $agent_id RETURN n LIMIT 50",
        "MATCH (a)-[r]->(b) RETURN a.label, type(r), b.label",
        "MATCH (n) WHERE n.type = 'PERSON' RETURN count(n)",
    ]
    for q in read_queries:
        assert _WRITE_PATTERN.search(q) is None, (
            f"Read query incorrectly flagged as write: {q!r}"
        )


# ---------------------------------------------------------------------------
# test_traverse_returns_subgraph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_traverse_returns_subgraph(mock_graph_service, nodes_and_edges):
    """GraphService.traverse() should be called and return a GraphPayload."""
    nodes, edges = nodes_and_edges
    start_node_id = nodes[0].id

    result = await mock_graph_service.traverse(
        start_node_id=start_node_id,
        depth=2,
        relation_filter=None,
    )

    assert isinstance(result, GraphPayload)
    assert len(result.nodes) > 0
    mock_graph_service.traverse.assert_called_once_with(
        start_node_id=start_node_id,
        depth=2,
        relation_filter=None,
    )


# ---------------------------------------------------------------------------
# test_graphrag_fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graphrag_fallback(mock_graph_service, nodes_and_edges):
    """
    If Cypher execution returns no results, QueryService should fall back to
    a semantic/embedding-based search and still return a result.
    """
    nodes, edges = nodes_and_edges

    cypher = "MATCH (n:SpaiderNode) WHERE n.agent_id = $agent_id RETURN n LIMIT 50"
    answer_text = "Based on graph context: Alice works at Acme."

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch("app.services.query_service.EmbeddingService") as MockEmbedding,
    ):
        mock_llm.side_effect = [
            _litellm_cypher_response(cypher),
            _litellm_answer_response(answer_text),
        ]

        # Mock EmbeddingService so it doesn't call Redis/LLM
        mock_embed_instance = AsyncMock()
        mock_embed_instance.embed = AsyncMock(return_value=[0.1] * 768)
        MockEmbedding.return_value = mock_embed_instance

        try:
            from app.services.query_service import QueryService
        except ImportError:
            pytest.skip("QueryService not yet implemented")

        qs = QueryService(graph_service=mock_graph_service)
        # Replace the embedding service with our mock
        qs._embedding = mock_embed_instance

        # Simulate Cypher returning empty results → triggers fallback
        mock_result_empty = AsyncMock()
        mock_result_empty.data = AsyncMock(return_value=[])
        mock_session = MagicMock()
        mock_session.run = AsyncMock(return_value=mock_result_empty)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_graph_service._driver.session.return_value = mock_session

        # vector_search fallback returns empty → graphrag returns empty subgraph
        mock_graph_service.vector_search.return_value = []

        result = await qs.query_nl(question="Tell me about Alice", agent_id="default")

    assert result.question == "Tell me about Alice"


def test_format_context_surfaces_properties_and_source_text():
    """Regression for the retrieval->answer gap (acmeai audit).

    Answer-bearing data — an extracted `temporal` date, custom scalar props,
    and the raw `source_text` — must reach the LLM context. Pre-fix the builder
    emitted only `label` + `description`, so the model answered "not available"
    even when retrieval hit the right node (the date lived in `properties`).
    """
    from app.services.query_service import QueryService

    records = [{
        "label": "acmeai.com",
        "type": "CONCEPT",
        "source_agent": "a1",
        "properties": {
            "description": "Domain name for acmeai",
            "source_text": "the acmeai.com domain expires",
            "temporal": "2026-06-15",
            "confidence": 1.0,       # internal bookkeeping — must be skipped
            "owner": "Olivia",       # custom answer-bearing scalar — must surface
        },
    }]

    ctx, involved = QueryService._format_context_records(records, v2_mode=False)

    assert "2026-06-15" in ctx                       # the date answer is visible
    assert "the acmeai.com domain expires" in ctx    # source_text surfaced too
    assert "Domain name for acmeai" in ctx           # description still present
    assert "owner: Olivia" in ctx                    # custom scalar surfaced
    assert "confidence" not in ctx                   # internal prop skipped
    assert involved == {"a1"}


# ---------------------------------------------------------------------------
# Retrieval-recall overhaul: hybrid fusion, embed text, token caps
# ---------------------------------------------------------------------------


def test_rrf_fuse_ranks_dual_modality_hits_first():
    from app.services.query_service import _rrf_fuse

    a, b, c = Node(id="a", label="A"), Node(id="b", label="B"), Node(id="c", label="C")
    # "b" appears in both lists (rank 2 vector, rank 1 fulltext) — it must
    # outrank "a" and "c", each found by only one retriever at rank 1.
    fused = _rrf_fuse([[a, b], [b, c]])
    assert [n.id for n in fused][0] == "b"
    assert {n.id for n in fused} == {"a", "b", "c"}


def test_rrf_fuse_empty_lists():
    from app.services.query_service import _rrf_fuse
    assert _rrf_fuse([]) == []
    assert _rrf_fuse([[], []]) == []


def test_keyword_ft_query_builds_prefix_or():
    from app.services.query_service import _keyword_ft_query
    q = _keyword_ft_query("When is the Atlas database migration window scheduled?")
    assert "atlas*" in q and "migration*" in q and " OR " in q
    assert _keyword_ft_query("a be of") is None  # all-stopword question


def test_format_context_caps_extra_props():
    """A property-heavy node must not flood the context — at most
    _CONTEXT_MAX_EXTRA_PROPS extra scalar props are surfaced."""
    from app.services.query_service import _CONTEXT_MAX_EXTRA_PROPS, QueryService

    props = {f"key_{i}": f"value_{i}" for i in range(20)}
    records = [{
        "label": "Heavy", "type": "CONCEPT", "source_agent": "a1",
        "properties": props,
    }]
    ctx, _ = QueryService._format_context_records(records, v2_mode=False)
    surfaced = sum(1 for i in range(20) if f"key_{i}:" in ctx)
    assert surfaced == _CONTEXT_MAX_EXTRA_PROPS


def test_format_context_prefers_promoted_columns():
    """Post-migration rows carry description/source_text as top-level record
    fields; the formatter must use them even when properties lacks both."""
    from app.services.query_service import QueryService

    records = [{
        "label": "acmeai.com", "type": "CONCEPT", "source_agent": "a1",
        "description": "Domain name for acmeai",
        "source_text": "the acmeai.com domain expires",
        "properties": {},
    }]
    ctx, _ = QueryService._format_context_records(records, v2_mode=False)
    assert "Domain name for acmeai" in ctx
    assert "the acmeai.com domain expires" in ctx


# ---------------------------------------------------------------------------
# Direct-answer extraction (#1 — F1/EM answer-format alignment)
# ---------------------------------------------------------------------------


def test_factoid_regex_matches_factoid_questions():
    from app.services.query_service import _FACTOID_RE
    for q in ["Who is AcmeAI's CTO?", "When is the migration window?",
              "How much ARR is at risk?", "Which customer churned?",
              "What scope change was requested?", "How many pods recovered?"]:
        assert _FACTOID_RE.match(q.strip().lower()), q
    for q in ["Explain the auth-service race.", "Summarise the standup.",
              "Tell me about the Beacon rollout.", "Describe the incident."]:
        assert not _FACTOID_RE.match(q.strip().lower()), q


@pytest.mark.asyncio
async def test_extract_direct_answer_skips_when_not_needed(mock_graph_service):
    """Terse answers, open-ended questions, and refusals are skipped without
    an LLM call (so the prose answer is always returned unchanged)."""
    from app.services.query_service import QueryService
    qs = QueryService(graph_service=mock_graph_service)
    # Patch the LLM so any accidental call would be visible
    with patch("app.services.query_service.acompletion_with_retry", new=AsyncMock()) as llm:
        assert await qs._extract_direct_answer("Who is CTO?", "Olivia") is None        # already terse
        assert await qs._extract_direct_answer("Explain X.", "A long prose answer here about X.") is None  # open-ended
        assert await qs._extract_direct_answer("Who is CTO?", "The data does not contain the answer.") is None  # refusal
        llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_direct_answer_trims_verbose_factoid(mock_graph_service):
    from app.services.query_service import QueryService
    qs = QueryService(graph_service=mock_graph_service)
    fake = AsyncMock()
    fake.choices = [MagicMock(message=MagicMock(content="headcount freeze"))]
    with patch("app.services.query_service.acompletion_with_retry", new=AsyncMock(return_value=fake)):
        span = await qs._extract_direct_answer(
            "What hiring decision was made for Q2?",
            "No additional hiring in Q2; headcount freeze pending Series B close.",
        )
    assert span == "headcount freeze"


@pytest.mark.asyncio
async def test_extract_direct_answer_none_sentinel(mock_graph_service):
    from app.services.query_service import QueryService
    qs = QueryService(graph_service=mock_graph_service)
    fake = AsyncMock()
    fake.choices = [MagicMock(message=MagicMock(content="NONE"))]
    with patch("app.services.query_service.acompletion_with_retry", new=AsyncMock(return_value=fake)):
        assert await qs._extract_direct_answer(
            "How much ARR is at risk?", "The passage discusses several unrelated topics at length.",
        ) is None


# ---------------------------------------------------------------------------
# Live Pheromone Stream — node labels, not opaque IDs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_pheromone_shows_labels_and_truncates(mock_graph_service):
    """The pheromone preview must use human-readable labels (truncated for long
    FACT text), falling back to an id prefix when a label is missing."""
    from app.services.query_service import QueryService

    qs = QueryService(graph_service=mock_graph_service)
    qs._get_redis = AsyncMock(return_value=object())

    long_fact = "fact: [2026-03-25] [DECISION] the board approved the Series B at a $400M valuation"
    labels = {"n1": "Sara", "n2": long_fact}  # n3 intentionally absent → id-prefix fallback
    node_ids = ["n1", "n2", "n3abcdef0123"]

    captured = {}

    async def _capture(_redis, event_type, agent, message, **kwargs):
        captured.update(type=event_type, agent=agent, message=message, **kwargs)

    with patch("app.services.redis_service.publish_swarm_log", new=_capture):
        await qs._publish_pheromone(node_ids, agent_id="bench", labels=labels)

    msg = captured["message"]
    assert captured["type"] == "pheromone"
    assert "Sara" in msg                       # short label shown verbatim
    assert "fact: [2026-03-25]" in msg and "…" in msg  # long label truncated to 24 chars + ellipsis
    assert long_fact not in msg                # full FACT text never leaks
    assert "n3abcdef" in msg                   # missing label → 8-char id prefix
    assert captured["count"] == 3


@pytest.mark.asyncio
async def test_publish_pheromone_noop_without_nodes(mock_graph_service):
    """No touched nodes → no Redis traffic at all."""
    from app.services.query_service import QueryService

    qs = QueryService(graph_service=mock_graph_service)
    redis_mock = AsyncMock()
    qs._get_redis = AsyncMock(return_value=redis_mock)

    with patch("app.services.redis_service.publish_swarm_log", new=AsyncMock()) as pub:
        await qs._publish_pheromone([], agent_id="bench")

    pub.assert_not_awaited()
