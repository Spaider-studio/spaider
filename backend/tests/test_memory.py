"""
Tests for the Interaction Memory (Episodic Memory) feature.

Covered behaviour
-----------------
  test_query_memory_disabled
    POST /api/v1/query with interaction_memory=False →
    record_interaction is never called.

  test_query_memory_enabled
    POST /api/v1/query with interaction_memory=True →
    BackgroundTask fires record_interaction with the correct agent_id,
    session_id, and source_node_ids derived from the subgraph.

  test_record_interaction_truncates
    GraphService.record_interaction() enforces question[:200] and
    answer_summary[:500] before any Neo4j write, regardless of how
    long the caller-supplied strings are.

  test_delete_interactions_wipes_only_interactions
    DELETE /api/v1/agents/{id}/interactions returns the deleted count
    and only calls delete_agent_interactions() — write_graph() and
    delete_agent_graph() are never touched.

Strategy
--------
All tests are pure unit tests; no Neo4j, Redis, or LLM calls are made.
Lazy-singleton globals are patched directly:
  app.api.v1.query._query_service
  app.api.v1.query._graph_service
  app.api.v1.agents._redis_client
  app.api.v1.agents._graph_service

asyncio_mode = auto is set in pytest.ini — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.schemas import Agent, Edge, GraphPayload, Node


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def client():
    """Async httpx client backed by the FastAPI ASGI app (no real network)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(label: str = "TestNode") -> Node:
    return Node(
        id=str(uuid.uuid4()),
        label=label,
        type="CONCEPT",
        properties={"description": f"Test: {label}"},
    )


def _make_svc_result(answer: str = "Test answer.", n_nodes: int = 2) -> MagicMock:
    """
    Fake return value for QueryService.query_nl().

    The endpoint accesses: .question, .answer, .subgraph.nodes, .cypher
    """
    nodes = [_make_node(f"Node{i}") for i in range(n_nodes)]
    subgraph = GraphPayload(nodes=nodes, edges=[])

    result = MagicMock()
    result.question = "What is the test question?"
    result.answer = answer
    result.subgraph = subgraph
    result.cypher = None
    return result


def _make_agent_json(
    agent_id: str,
    *,
    interaction_memory: bool = False,
) -> str:
    """Return a JSON string matching the shape stored by agents.py in Redis."""
    agent = Agent(
        id=agent_id,
        name="Test Agent",
        tenant_id="default",
        permissions=["read", "write", "query"],
        clearance_level=1,
        interaction_memory=interaction_memory,
        created_at=datetime.now(timezone.utc),
    )
    return json.dumps(agent.model_dump(mode="json"))


def _mock_query_service(
    svc_result: MagicMock,
    *,
    interaction_memory: bool,
) -> MagicMock:
    """Build a QueryService mock wired for the asyncio.gather() call pattern."""
    mock = MagicMock()
    mock.query_nl = AsyncMock(return_value=svc_result)
    mock.get_agent_interaction_memory = AsyncMock(return_value=interaction_memory)
    return mock


def _mock_graph_service_for_query() -> MagicMock:
    """GraphService mock for the query endpoint (only record_interaction needed)."""
    mock = MagicMock()
    mock.record_interaction = AsyncMock()
    return mock


# ---------------------------------------------------------------------------
# Test 1 — interaction_memory=False: record_interaction is never called
# ---------------------------------------------------------------------------

async def test_query_memory_disabled(client):
    """
    When the agent has interaction_memory=False, the query endpoint must
    complete successfully and must NOT schedule a record_interaction task.
    """
    svc_result = _make_svc_result()
    mock_qs = _mock_query_service(svc_result, interaction_memory=False)
    mock_gs = _mock_graph_service_for_query()

    with (
        patch("app.api.v1.query._query_service", mock_qs),
        patch("app.api.v1.query._graph_service", mock_gs),
    ):
        resp = await client.post(
            "/api/v1/query",
            json={"question": "What is quantum entanglement?", "agent_id": "agent-mem-off"},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["answer"] == svc_result.answer

    # The memory flag is False — record_interaction must never be touched.
    mock_gs.record_interaction.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 — interaction_memory=True: BackgroundTask fires, args are correct
# ---------------------------------------------------------------------------

async def test_query_memory_enabled(client):
    """
    When the agent has interaction_memory=True, record_interaction must be
    called exactly once (by the BackgroundTask, which runs before the httpx
    response object is returned in the ASGI test transport) with:
      - agent_id matching the request
      - session_id matching the request (when supplied)
      - source_node_ids matching the subgraph node IDs returned by query_nl
    """
    agent_id = "agent-mem-on"
    session_id = "session-abc-123"
    svc_result = _make_svc_result(n_nodes=3)
    expected_source_ids = [n.id for n in svc_result.subgraph.nodes]

    mock_qs = _mock_query_service(svc_result, interaction_memory=True)
    mock_gs = _mock_graph_service_for_query()

    with (
        patch("app.api.v1.query._query_service", mock_qs),
        patch("app.api.v1.query._graph_service", mock_gs),
    ):
        resp = await client.post(
            "/api/v1/query",
            json={
                "question": "Tell me about quantum computing.",
                "agent_id": agent_id,
                "session_id": session_id,
            },
        )

    assert resp.status_code == 200, resp.text

    # BackgroundTask has completed by the time the ASGI transport returns.
    mock_gs.record_interaction.assert_called_once()

    # Verify the keyword arguments forwarded to record_interaction.
    kwargs = mock_gs.record_interaction.call_args.kwargs
    assert kwargs["agent_id"] == agent_id
    assert kwargs["session_id"] == session_id
    assert kwargs["source_node_ids"] == expected_source_ids
    # answer_summary is the full answer at the call site; truncation
    # is enforced inside record_interaction itself (tested separately below).
    assert kwargs["answer_summary"] == svc_result.answer


# ---------------------------------------------------------------------------
# Test 3 — record_interaction enforces truncation before Neo4j write
# ---------------------------------------------------------------------------

async def test_record_interaction_truncates():
    """
    GraphService.record_interaction() must truncate question to 200 chars
    and answer_summary to 500 chars before passing them to Neo4j,
    regardless of the length of the caller-supplied strings.

    Strategy: capture the _write transaction function that execute_write
    receives, replay it against a mock tx, and inspect the Cypher parameters.
    """
    from app.services.graph_service import GraphService

    # ── Build a mock Neo4j driver that captures the write transaction fn ──
    mock_tx = AsyncMock()

    captured: list = []

    async def _capture_execute_write(fn):
        captured.append(fn)
        await fn(mock_tx)

    mock_session = MagicMock()
    mock_session.execute_write = _capture_execute_write
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)

    gs = GraphService()
    gs._driver = mock_driver

    long_question = "Q" * 350   # 350 chars — must be truncated to 200
    long_answer   = "A" * 800   # 800 chars — must be truncated to 500

    await gs.record_interaction(
        agent_id="agent-truncate-test",
        session_id="sess-trunc",
        question=long_question,
        answer_summary=long_answer,
        source_node_ids=[],
    )

    # execute_write must have been called exactly once with our _write fn.
    assert len(captured) == 1, "execute_write was not called"

    # Inspect the parameters that _write passed to tx.run().
    assert mock_tx.run.called, "tx.run was never called inside _write"
    first_call_kwargs = mock_tx.run.call_args_list[0].kwargs

    assert len(first_call_kwargs["question"]) == 200, (
        f"question not truncated: got {len(first_call_kwargs['question'])} chars"
    )
    assert len(first_call_kwargs["answer_summary"]) == 500, (
        f"answer_summary not truncated: got {len(first_call_kwargs['answer_summary'])} chars"
    )
    # Content must be the leading slice, not arbitrary characters.
    assert first_call_kwargs["question"]       == long_question[:200]
    assert first_call_kwargs["answer_summary"] == long_answer[:500]


# ---------------------------------------------------------------------------
# Test 4 — DELETE /{agent_id}/interactions wipes only episodic memory
# ---------------------------------------------------------------------------

async def test_delete_interactions_wipes_only_interactions(client):
    """
    DELETE /api/v1/agents/{id}/interactions must:
      - Return 200 with success=True and the correct deleted_count.
      - Call delete_agent_interactions() exactly once with the correct agent_id.
      - Never call write_graph() or delete_agent_graph() — SpaiderNodes
        and knowledge edges must be completely unaffected.
    """
    agent_id = str(uuid.uuid4())
    deleted_count = 7

    # Redis mock: _load_agent reads from spaider:agent:{id}
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(
        return_value=_make_agent_json(agent_id, interaction_memory=True)
    )

    mock_gs = MagicMock()
    mock_gs.delete_agent_interactions = AsyncMock(return_value=deleted_count)
    mock_gs.write_graph = AsyncMock()
    mock_gs.delete_agent_graph = AsyncMock()

    with (
        patch("app.api.v1.agents._redis_client", mock_redis),
        patch("app.api.v1.agents._graph_service", mock_gs),
    ):
        resp = await client.delete(f"/api/v1/agents/{agent_id}/interactions")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["agent_id"] == agent_id
    assert data["deleted_count"] == deleted_count

    # Only the interaction-memory method was called.
    mock_gs.delete_agent_interactions.assert_called_once_with(agent_id)

    # Knowledge graph methods must be completely untouched.
    mock_gs.write_graph.assert_not_called()
    mock_gs.delete_agent_graph.assert_not_called()
