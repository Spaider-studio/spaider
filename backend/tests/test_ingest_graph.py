"""
Tests for the BYOV direct GraphPayload ingest endpoints.

Covered endpoints
-----------------
  POST /api/v1/ingest/graph        — single GraphPayload ingest
  POST /api/v1/ingest/graph/batch  — batch GraphPayload ingest (max 100)

Strategy
--------
All four tests are pure unit tests — no Neo4j, no embedding service, no LLM.
The lazy-singleton globals (_resolver, _graph_service) inside
`app.api.v1.ingest` are patched directly so the handler code never touches
real infrastructure.

asyncio_mode = auto is set in pytest.ini, so no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.schemas import Edge, GraphPayload, Node
from app.models.responses import IngestSyncResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(label: str = "TestNode", agent_id: str | None = None) -> Node:
    return Node(
        id=str(uuid.uuid4()),
        label=label,
        type="CONCEPT",
        properties={"description": f"Test node: {label}"},
        agent_id=agent_id,
    )


def _make_edge(source_id: str, target_id: str) -> Edge:
    return Edge(
        id=str(uuid.uuid4()),
        source_id=source_id,
        target_id=target_id,
        relation="RELATED_TO",
        properties={},
    )


def _make_payload(n_nodes: int = 2) -> GraphPayload:
    """Return a minimal valid GraphPayload with n_nodes nodes and one edge."""
    nodes = [_make_node(f"Node{i}") for i in range(n_nodes)]
    edges = [_make_edge(nodes[0].id, nodes[1].id)] if n_nodes >= 2 else []
    return GraphPayload(nodes=nodes, edges=edges)


def _make_write_result(
    nodes_created: int = 2,
    nodes_merged: int = 0,
    edges_created: int = 1,
    edges_merged: int = 0,
) -> MagicMock:
    """Minimal WriteResult-like mock."""
    r = MagicMock()
    r.nodes_created = nodes_created
    r.nodes_merged = nodes_merged
    r.edges_created = edges_created
    r.edges_merged = edges_merged
    return r


# ---------------------------------------------------------------------------
# Shared fixture: ASGI test client
# ---------------------------------------------------------------------------

@pytest.fixture
async def client():
    """
    Async httpx client pointed at the FastAPI ASGI app.

    Uses ASGITransport so requests never touch the network.
    The lifespan is disabled to avoid startup side effects
    (Neo4j driver, Redis, Kafka) in unit tests.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

def _mock_resolver(resolved_payload: GraphPayload) -> MagicMock:
    """Return a mock EntityResolver whose .resolve() returns resolved_payload."""
    mock = MagicMock()
    mock.resolve = AsyncMock(return_value=resolved_payload)
    return mock


def _mock_graph_service(write_result: MagicMock) -> MagicMock:
    """Return a mock GraphService whose write_graph / write_graph_batch return write_result."""
    mock = MagicMock()
    mock.write_graph = AsyncMock(return_value=write_result)
    mock.write_graph_batch = AsyncMock(return_value=write_result)
    # search_nodes is called inside resolver.resolve — but since we mock the
    # entire resolver, graph_service.search_nodes is never reached.
    mock.search_nodes = AsyncMock(return_value=[])
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_ingest_graph_success(client):
    """
    POST /ingest/graph with a valid GraphPayload returns 200 OK and the
    expected IngestSyncResponse shape.

    The resolver and graph service are mocked so no real I/O occurs.
    """
    payload = _make_payload(n_nodes=2)
    resolved = _make_payload(n_nodes=2)  # resolver returns same-shape payload
    write_result = _make_write_result(nodes_created=2, edges_created=1)

    mock_resolver = _mock_resolver(resolved)
    mock_graph = _mock_graph_service(write_result)

    with (
        patch("app.api.v1.ingest._resolver", mock_resolver),
        patch("app.api.v1.ingest._graph_service", mock_graph),
    ):
        resp = await client.post(
            "/api/v1/ingest/graph",
            json={
                "agent_id": "test-agent",
                "nodes": [
                    {"id": n.id, "label": n.label, "type": n.type, "properties": n.properties}
                    for n in payload.nodes
                ],
                "edges": [
                    {
                        "id": e.id,
                        "source_id": e.source_id,
                        "target_id": e.target_id,
                        "relation": e.relation,
                        "properties": e.properties,
                    }
                    for e in payload.edges
                ],
            },
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["agent_id"] == "test-agent"
    assert data["nodes_created"] == 2
    assert data["edges_created"] == 1
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)
    assert data["latency_ms"] >= 0.0

    # Resolver was called exactly once with the correct agent_id.
    mock_resolver.resolve.assert_called_once()
    call_kwargs = mock_resolver.resolve.call_args
    assert call_kwargs.args[1] == "test-agent"
    assert call_kwargs.kwargs.get("caller_context") == "api"

    # GraphService.write_graph was called once with the resolved payload.
    mock_graph.write_graph.assert_called_once()


async def test_ingest_graph_dimension_mismatch(client):
    """
    POST /ingest/graph where the resolver raises HTTPException(422) due to an
    embedding dimension mismatch must propagate the 422 to the client.

    This verifies that the endpoint does NOT swallow the 422 in its generic
    `except Exception` handler (the `except HTTPException: raise` guard).
    """
    from fastapi import HTTPException as FastAPIHTTPException

    mock_resolver = MagicMock()
    mock_resolver.resolve = AsyncMock(
        side_effect=FastAPIHTTPException(
            status_code=422,
            detail="Embedding dimension mismatch: expected 1536, got 768.",
        )
    )
    mock_graph = _mock_graph_service(_make_write_result())

    with (
        patch("app.api.v1.ingest._resolver", mock_resolver),
        patch("app.api.v1.ingest._graph_service", mock_graph),
    ):
        resp = await client.post(
            "/api/v1/ingest/graph",
            json={
                "agent_id": "dim-test-agent",
                "nodes": [
                    {
                        "id": str(uuid.uuid4()),
                        "label": "MismatchNode",
                        "type": "CONCEPT",
                        "properties": {},
                        # wrong-dimension embedding (768 instead of 1536)
                        "embedding": [0.1] * 768,
                    }
                ],
                "edges": [],
            },
        )

    assert resp.status_code == 422, resp.text
    detail = resp.json().get("detail", "")
    assert "dimension" in detail.lower() or "mismatch" in detail.lower()

    # write_graph must never be reached when the resolver raises.
    mock_graph.write_graph.assert_not_called()


async def test_ingest_graph_batch_success(client):
    """
    POST /ingest/graph/batch with 3 valid payloads returns 200 OK,
    reports payloads_processed=3, and calls write_graph_batch exactly once.

    Verifies that asyncio.gather resolves all 3 payloads and that the single-
    transaction write path is taken (not 3 individual write_graph calls).
    """
    payloads = [_make_payload(n_nodes=2) for _ in range(3)]
    resolved = _make_payload(n_nodes=2)
    write_result = _make_write_result(nodes_created=6, edges_created=3)

    mock_resolver = MagicMock()
    # resolve() is called 3 times concurrently; always return the same resolved payload.
    mock_resolver.resolve = AsyncMock(return_value=resolved)
    mock_graph = _mock_graph_service(write_result)

    batch_body = {
        "agent_id": "batch-agent",
        "payloads": [
            {
                "nodes": [
                    {"id": n.id, "label": n.label, "type": n.type, "properties": n.properties}
                    for n in p.nodes
                ],
                "edges": [
                    {
                        "id": e.id,
                        "source_id": e.source_id,
                        "target_id": e.target_id,
                        "relation": e.relation,
                        "properties": e.properties,
                    }
                    for e in p.edges
                ],
            }
            for p in payloads
        ],
    }

    with (
        patch("app.api.v1.ingest._resolver", mock_resolver),
        patch("app.api.v1.ingest._graph_service", mock_graph),
    ):
        resp = await client.post("/api/v1/ingest/graph/batch", json=batch_body)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["agent_id"] == "batch-agent"
    assert data["payloads_processed"] == 3

    # resolve() called 3 times (one per payload, concurrently via gather).
    assert mock_resolver.resolve.call_count == 3

    # write_graph_batch called exactly once — single Neo4j transaction.
    mock_graph.write_graph_batch.assert_called_once()
    # write_graph (single-payload variant) must NOT be called.
    mock_graph.write_graph.assert_not_called()


async def test_ingest_graph_batch_validation_error(client):
    """
    POST /ingest/graph/batch with 101 payloads must be rejected with HTTP 422
    by Pydantic V2 field validation (max_length=100) — before any service code runs.

    Uses a minimal single-node payload repeated 101 times to keep the request
    body small while still exceeding the limit.
    """
    single_node_payload = {
        "nodes": [{"id": str(uuid.uuid4()), "label": "N", "type": "OTHER", "properties": {}}],
        "edges": [],
    }

    mock_resolver = MagicMock()
    mock_resolver.resolve = AsyncMock()
    mock_graph = _mock_graph_service(_make_write_result())

    with (
        patch("app.api.v1.ingest._resolver", mock_resolver),
        patch("app.api.v1.ingest._graph_service", mock_graph),
    ):
        resp = await client.post(
            "/api/v1/ingest/graph/batch",
            json={
                "agent_id": "overflow-agent",
                "payloads": [single_node_payload] * 101,
            },
        )

    # Pydantic V2 max_length constraint → FastAPI returns 422 before the handler runs.
    assert resp.status_code == 422, resp.text

    # Neither service must have been touched.
    mock_resolver.resolve.assert_not_called()
    mock_graph.write_graph_batch.assert_not_called()
