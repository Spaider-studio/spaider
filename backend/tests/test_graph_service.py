"""Tests for GraphService (mocked Neo4j driver)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import Edge, GraphPayload, Node


def _make_session_mock(single_record=None, data_records=None):
    """
    Return a context-manager-compatible mock session.

    The GraphService code does:
        async with self._driver.session() as session:
            result = await session.run(...)
            records = await result.data()   # or result.single()

    So:
      - self._driver.session must be a regular callable (MagicMock, not AsyncMock)
        that returns an object supporting async context manager.
      - The returned session must have `run` as an AsyncMock.
      - `run()` must return an object with `.data()` and `.single()` as AsyncMocks.
    """
    if data_records is None:
        data_records = []

    mock_result = AsyncMock()
    mock_result.data = AsyncMock(return_value=data_records)
    mock_result.single = AsyncMock(return_value=single_record)

    mock_session = MagicMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    return mock_session


@pytest.fixture
def graph_service():
    with patch("app.services.graph_service.AsyncGraphDatabase") as MockDB:
        mock_driver = MagicMock()  # Must be MagicMock, NOT AsyncMock
        MockDB.driver.return_value = mock_driver

        from app.services.graph_service import GraphService
        gs = GraphService()
        gs._driver = mock_driver
        return gs


# ---------------------------------------------------------------------------
# test_write_graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_graph_creates_nodes_and_edges(graph_service):
    """write_graph() should run Cypher for each node and edge."""
    single_created = {"created": 1, "merged": 0}
    session = _make_session_mock(single_record=single_created, data_records=[single_created])
    graph_service._driver.session.return_value = session

    n1 = Node(label="Alice", type="Person")
    n2 = Node(label="Bob", type="Person")
    e1 = Edge(source_id=n1.id, target_id=n2.id, relation="KNOWS")
    payload = GraphPayload(nodes=[n1, n2], edges=[e1])

    from app.services.graph_service import WriteResult
    result = await graph_service.write_graph(payload, agent_id="test-agent")

    assert isinstance(result, WriteResult)
    # UNWIND batch: one run() for all nodes + one run() for all edges = 2 calls
    assert session.run.call_count >= 2


@pytest.mark.asyncio
async def test_write_graph_empty_payload(graph_service):
    """write_graph() with an empty payload should return zero counts."""
    session = _make_session_mock(single_record=None, data_records=[])
    graph_service._driver.session.return_value = session

    payload = GraphPayload(nodes=[], edges=[])

    result = await graph_service.write_graph(payload, agent_id="test-agent")

    assert result.nodes_created == 0
    assert result.nodes_merged == 0
    assert result.edges_created == 0
    assert result.edges_merged == 0


# ---------------------------------------------------------------------------
# test_get_full_graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_full_graph_empty(graph_service):
    """get_full_graph() with no records should return empty payload."""
    session = _make_session_mock(data_records=[])
    graph_service._driver.session.return_value = session

    result = await graph_service.get_full_graph(agent_id="test-agent")
    assert isinstance(result, GraphPayload)
    assert result.nodes == []
    assert result.edges == []


@pytest.mark.asyncio
async def test_get_full_graph_returns_nodes(graph_service):
    """get_full_graph() should convert Neo4j records to Node/Edge objects.

    The new single-query implementation returns one row per (node, optional edge).
    Columns r_id, r_relation, r_properties, r_agent_id, tgt, utility_weight are
    NULL when a node has no in-page out-edges.
    """
    # One node with no in-page edges → r_id is None
    combined_records = [
        {
            "id": "abc", "label": "Alice", "type": "Person",
            "properties": {}, "embedding": None, "agent_id": "test-agent",
            "r_id": None, "r_relation": None, "r_properties": None,
            "r_agent_id": None, "tgt": None, "utility_weight": 1.0,
        }
    ]
    session = _make_session_mock(data_records=combined_records)
    graph_service._driver.session.return_value = session

    result = await graph_service.get_full_graph(agent_id="test-agent")
    assert len(result.nodes) == 1
    assert result.nodes[0].label == "Alice"
    assert result.edges == []


@pytest.mark.asyncio
async def test_get_full_graph_returns_edges_between_page_nodes(graph_service):
    """get_full_graph() should return only edges whose both endpoints are in the page."""
    # Two nodes connected by one edge; node "abc" appears twice (once per out-edge row)
    combined_records = [
        {
            "id": "abc", "label": "Alice", "type": "Person",
            "properties": {}, "embedding": None, "agent_id": "test-agent",
            "r_id": "e1", "r_relation": "KNOWS", "r_properties": {},
            "r_agent_id": "test-agent", "tgt": "def", "utility_weight": 1.0,
        },
        {
            "id": "def", "label": "Bob", "type": "Person",
            "properties": {}, "embedding": None, "agent_id": "test-agent",
            "r_id": None, "r_relation": None, "r_properties": None,
            "r_agent_id": None, "tgt": None, "utility_weight": 1.0,
        },
    ]
    session = _make_session_mock(data_records=combined_records)
    graph_service._driver.session.return_value = session

    result = await graph_service.get_full_graph(agent_id="test-agent")
    assert len(result.nodes) == 2
    assert {n.label for n in result.nodes} == {"Alice", "Bob"}
    assert len(result.edges) == 1
    assert result.edges[0].source_id == "abc"
    assert result.edges[0].target_id == "def"
    assert result.edges[0].relation == "KNOWS"


# ---------------------------------------------------------------------------
# test_delete_node_cascade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_node_cascade_returns_edge_count(graph_service):
    """delete_node_cascade() should return a DeleteResult with correct edge count."""
    single_record = {"edge_count": 3}
    session = _make_session_mock(single_record=single_record)
    graph_service._driver.session.return_value = session

    from app.services.graph_service import DeleteResult
    result = await graph_service.delete_node_cascade("abc")

    assert isinstance(result, DeleteResult)
    assert result.deleted_nodes == 1
    assert result.deleted_edges == 3


@pytest.mark.asyncio
async def test_delete_node_cascade_no_record(graph_service):
    """delete_node_cascade() with no matching node returns zeros."""
    session = _make_session_mock(single_record=None)
    graph_service._driver.session.return_value = session

    from app.services.graph_service import DeleteResult
    result = await graph_service.delete_node_cascade("nonexistent")

    assert isinstance(result, DeleteResult)
    assert result.deleted_edges == 0


# ---------------------------------------------------------------------------
# test_delete_agent_graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_agent_graph(graph_service):
    """delete_agent_graph() should return node and edge counts."""
    single_record = {"node_count": 5, "edge_count": 4}
    session = _make_session_mock(single_record=single_record)
    graph_service._driver.session.return_value = session

    from app.services.graph_service import DeleteResult
    result = await graph_service.delete_agent_graph("test-agent")

    assert isinstance(result, DeleteResult)
    assert result.deleted_nodes == 5
    assert result.deleted_edges == 4


# ---------------------------------------------------------------------------
# test_search_nodes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_nodes_returns_matching_nodes(graph_service):
    """search_nodes() should return nodes whose label contains the query."""
    node_records = [
        {"id": "n1", "label": "Alice", "type": "Person", "properties": {}, "embedding": None, "agent_id": "test-agent"}
    ]
    session = _make_session_mock(data_records=node_records)
    graph_service._driver.session.return_value = session

    nodes = await graph_service.search_nodes(query="Alice", agent_id="test-agent", limit=10)
    assert len(nodes) == 1
    assert nodes[0].label == "Alice"


@pytest.mark.asyncio
async def test_search_nodes_empty_query_returns_all(graph_service):
    """search_nodes() with empty query should return all nodes up to limit."""
    node_records = [
        {"n": {"id": "n1", "label": "Alice", "type": "Person", "properties": {}}},
        {"n": {"id": "n2", "label": "Bob", "type": "Person", "properties": {}}},
    ]
    session = _make_session_mock(data_records=node_records)
    graph_service._driver.session.return_value = session

    nodes = await graph_service.search_nodes(query="", agent_id="test-agent", limit=10)
    assert len(nodes) == 2


@pytest.mark.asyncio
async def test_vector_search_raises_when_index_unavailable(graph_service):
    """If the vector index is missing, vector_search() must raise instead of
    silently loading 5000 nodes into Python memory for cosine similarity."""
    from app.services.graph_service import VectorIndexUnavailableError

    graph_service.vector_index_available = False

    with pytest.raises(VectorIndexUnavailableError):
        await graph_service.vector_search(
            embedding=[0.0] * 1536, agent_id="test-agent", top_k=5
        )


@pytest.mark.asyncio
async def test_vector_search_runs_when_index_available(graph_service):
    """Happy path: flag flipped on, Cypher executes and results are returned."""
    node_records = [
        {
            "id": "n1",
            "label": "Alice",
            "type": "Person",
            "properties": {},
            "embedding": None,
            "agent_id": "test-agent",
            "clearance_level": 1,
            "score": 0.91,
        }
    ]
    session = _make_session_mock(data_records=node_records)
    graph_service._driver.session.return_value = session
    graph_service.vector_index_available = True

    nodes = await graph_service.vector_search(
        embedding=[0.0] * 1536, agent_id="test-agent", top_k=5
    )

    assert len(nodes) == 1
    assert nodes[0].label == "Alice"


@pytest.mark.asyncio
async def test_initialize_auto_creates_on_empty_db():
    """Empty DB + missing index → index auto-created, flag flipped true."""
    from app.services.graph_service import GraphService

    with patch("app.services.graph_service.AsyncGraphDatabase") as MockDB:
        MockDB.driver.return_value = MagicMock()
        gs = GraphService()

        # Sequence of session.run responses:
        #   1-6: six CREATE CONSTRAINT / CREATE INDEX statements (no result read):
        #          spaider_node_id constraint, system_agent_id constraint,
        #          spaider_agent_label composite index, spaider_node_type index,
        #          spaider_agent_id standalone index, spaider_label_fulltext FTS index
        #   7:   SHOW INDEXES → no matching record (index missing)
        #   8:   MATCH count(n) → 0 (empty DB)
        #   9:   CREATE VECTOR INDEX (no result read)
        show_result = AsyncMock()
        show_result.single = AsyncMock(return_value=None)   # index missing
        count_result = AsyncMock()
        count_result.single = AsyncMock(return_value={"c": 0})  # empty DB
        generic_result = AsyncMock()

        run_mock = AsyncMock(
            side_effect=[
                generic_result, generic_result, generic_result, generic_result,
                generic_result, generic_result,
                show_result, count_result, generic_result,
            ]
        )
        session = MagicMock()
        session.run = run_mock
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        gs._driver.session = MagicMock(return_value=session)

        await gs.initialize()

    assert gs.vector_index_available is True


@pytest.mark.asyncio
async def test_initialize_refuses_auto_create_on_populated_db():
    """Populated DB + missing index → flag stays False, startup does not block."""
    from app.services.graph_service import GraphService

    with patch("app.services.graph_service.AsyncGraphDatabase") as MockDB:
        MockDB.driver.return_value = MagicMock()
        gs = GraphService()

        show_result = AsyncMock()
        show_result.single = AsyncMock(return_value=None)        # index missing
        count_result = AsyncMock()
        count_result.single = AsyncMock(return_value={"c": 42})  # DB has nodes
        generic_result = AsyncMock()

        # 6 DDL statements + SHOW FULLTEXT INDEXES definition check +
        # SHOW INDEXES + MATCH count(n) = 9 total.
        # CREATE VECTOR INDEX must NOT appear (populated DB, refuse auto-create).
        run_mock = AsyncMock(
            side_effect=[
                generic_result, generic_result, generic_result, generic_result,
                generic_result, generic_result, generic_result,
                show_result, count_result,
            ]
        )
        session = MagicMock()
        session.run = run_mock
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        gs._driver.session = MagicMock(return_value=session)

        await gs.initialize()

    assert gs.vector_index_available is False
    # CREATE VECTOR INDEX must NOT have been called — only the 9 queries above.
    assert run_mock.call_count == 9


# ---------------------------------------------------------------------------
# Null-id edge robustness (multiverse 500 regression)
# ---------------------------------------------------------------------------


def test_record_to_edge_tolerates_null_fields():
    """A legacy edge with explicit NULL id/relation/properties must still
    produce a valid Edge. `r.get("id", default)` returned None here (key
    present, value None), which failed Edge validation and 500'd the
    multiverse view.
    """
    from app.services.graph_service import GraphService

    edge = GraphService._record_to_edge(
        {"id": None, "relation": None, "properties": None, "agent_id": "a1"},
        "src-1", "tgt-1",
    )
    assert isinstance(edge.id, str) and edge.id          # a real id was generated
    assert edge.relation == "RELATED_TO"                 # null relation -> default
    assert edge.source_id == "src-1" and edge.target_id == "tgt-1"


def _data_result(records):
    r = AsyncMock()
    r.data = AsyncMock(return_value=records)
    r.single = AsyncMock(return_value=None)
    return r


@pytest.mark.asyncio
async def test_get_all_agents_graph_tolerates_null_edge_id(graph_service):
    """The multiverse aggregation must not raise when a RELATION edge carries a
    null id — it should surface a valid Edge instead of failing the response.
    """
    node_recs = [
        {"id": "n1", "label": "Alice", "type": "PERSON",
         "description": "", "properties": {}, "agent_id": "a1"},
        {"id": "n2", "label": "Bob", "type": "PERSON",
         "description": "", "properties": {}, "agent_id": "a1"},
    ]
    agent_recs = [{"id": "a1", "label": "Agent One", "agent_id": "a1"}]
    edge_recs = [{"id": None, "relation": None, "properties": None,
                  "agent_id": "a1", "src": "n1", "tgt": "n2", "utility_weight": 1.0}]

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    # 5 queries in order: nodes, agents, RELATION edges, BELONGS_TO_AGENT, SHARES_KNOWLEDGE_WITH
    session.run = AsyncMock(side_effect=[
        _data_result(node_recs), _data_result(agent_recs),
        _data_result(edge_recs), _data_result([]), _data_result([]),
    ])
    graph_service._driver.session.return_value = session

    payload = await graph_service.get_all_agents_graph(limit=100)

    assert len(payload.edges) == 1
    assert isinstance(payload.edges[0].id, str) and payload.edges[0].id   # valid, no crash
    assert payload.edges[0].relation == "RELATED_TO"
