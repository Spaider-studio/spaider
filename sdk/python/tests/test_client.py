"""
Tests for the Spaider synchronous client.

Uses pytest-httpx to mock HTTP responses.
"""

from __future__ import annotations

import json

import httpx
import pytest

from spaider.client import Spaider
from spaider.exceptions import AuthError, NotFoundError, RateLimitError, ServerError, SpaiderError
from spaider.models import GraphPayload, IngestResult, Node, QueryResult, SwarmQueryResult

# ── Fixtures ──────────────────────────────────────────────────────────────────

BASE_URL = "https://api.spaider.studio"
API_KEY = "sk-test-key"
AGENT_ID = "test-agent"


def make_client(transport: httpx.MockTransport) -> Spaider:
    """Create a Spaider client backed by a mock transport."""
    mock_http = httpx.Client(
        base_url=BASE_URL,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "X-Spaider-Agent": AGENT_ID,
        },
        transport=transport,
    )
    return Spaider(api_key=API_KEY, agent_id=AGENT_ID, http_client=mock_http)


# ── Constructor ───────────────────────────────────────────────────────────────

class TestConstructor:
    def test_raises_on_empty_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            Spaider(api_key="")

    def test_default_agent_id(self):
        sp = Spaider(api_key="sk-x")
        assert sp.agent_id == "default"

    def test_custom_agent_id(self):
        sp = Spaider(api_key="sk-x", agent_id="my-agent")
        assert sp.agent_id == "my-agent"


# ── Ingest ────────────────────────────────────────────────────────────────────

class TestIngest:
    def test_ingest_success(self):
        response_body = {
            "nodes_created": 2,
            "nodes_merged": 1,
            "edges_created": 1,
            "edges_merged": 0,
            "status": "ok",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert "/api/v1/ingest" in request.url.path
            body = json.loads(request.content)
            assert body["text"] == "Max works at Google."
            assert body["agent_id"] == AGENT_ID
            return httpx.Response(200, json=response_body)

        sp = make_client(httpx.MockTransport(handler))
        result = sp.ingest("Max works at Google.")

        assert isinstance(result, IngestResult)
        assert result.nodes_created == 2
        assert result.edges_created == 1
        assert result.status == "ok"

    def test_ingest_with_source(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["source"] == "wikipedia"
            return httpx.Response(200, json={"nodes_created": 1, "edges_created": 0, "status": "ok"})

        sp = make_client(httpx.MockTransport(handler))
        result = sp.ingest("Some text.", source="wikipedia")
        assert isinstance(result, IngestResult)

    def test_ingest_server_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        sp = make_client(httpx.MockTransport(handler))
        with pytest.raises(ServerError):
            sp.ingest("text")

    def test_ingest_auth_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Unauthorized")

        sp = make_client(httpx.MockTransport(handler))
        with pytest.raises(AuthError):
            sp.ingest("text")


# ── Query ─────────────────────────────────────────────────────────────────────

class TestQuery:
    def test_query_success(self):
        # Mirrors the real backend QueryResult: `answer`, `confidence_score`,
        # graph `node_count`/`edge_count`, and edges keyed by label `source`/`target`.
        response_body = {
            "question": "Where does Max work?",
            "answer": "Max works at Google as an Engineer.",
            "subgraph": {
                "nodes": [
                    {"id": "uuid-1", "label": "Max", "type": "Person", "properties": {}},
                    {"id": "uuid-2", "label": "Google", "type": "Organization", "properties": {}},
                ],
                "edges": [
                    {
                        "id": "edge-1",
                        "source": "Max",
                        "target": "Google",
                        "relation": "WORKS_AT",
                        "properties": {},
                    }
                ],
                "node_count": 2,
                "edge_count": 1,
            },
            "confidence_score": 0.95,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert "/api/v1/query" in request.url.path
            return httpx.Response(200, json=response_body)

        sp = make_client(httpx.MockTransport(handler))
        result = sp.query("Where does Max work?")

        assert isinstance(result, QueryResult)
        assert result.answer == "Max works at Google as an Engineer."
        assert len(result.subgraph.nodes) == 2
        assert result.subgraph.edges[0].source == "Max"
        assert result.confidence_score == 0.95

    def test_query_rate_limit_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "30"}, text="Too Many Requests")

        sp = make_client(httpx.MockTransport(handler))
        with pytest.raises(RateLimitError) as exc_info:
            sp.query("question")
        assert exc_info.value.retry_after == 30


# ── Traverse ──────────────────────────────────────────────────────────────────

class TestTraverse:
    def test_traverse_success(self):
        response_body = {
            "nodes": [{"id": "uuid-1", "label": "Max", "type": "Person", "properties": {}}],
            "edges": [],
            "node_count": 1,
            "edge_count": 0,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert "traverse/uuid-1" in request.url.path
            return httpx.Response(200, json=response_body)

        sp = make_client(httpx.MockTransport(handler))
        result = sp.traverse("uuid-1", depth=2)

        assert isinstance(result, GraphPayload)
        assert len(result.nodes) == 1


# ── Get Node ──────────────────────────────────────────────────────────────────

class TestGetNode:
    def test_get_node_success(self):
        response_body = {"id": "uuid-1", "label": "Max", "type": "Person", "properties": {}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response_body)

        sp = make_client(httpx.MockTransport(handler))
        node = sp.get_node("uuid-1")

        assert isinstance(node, Node)
        assert node.label == "Max"

    def test_get_node_not_found_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        sp = make_client(httpx.MockTransport(handler))
        with pytest.raises(NotFoundError):
            sp.get_node("nonexistent")


# ── Delete Node ───────────────────────────────────────────────────────────────

class TestDeleteNode:
    def test_delete_node_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "DELETE"
            assert "uuid-1" in request.url.path
            return httpx.Response(204)

        sp = make_client(httpx.MockTransport(handler))
        result = sp.delete_node("uuid-1")
        assert result is None

    def test_delete_node_not_found_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        sp = make_client(httpx.MockTransport(handler))
        with pytest.raises(NotFoundError):
            sp.delete_node("ghost-id")


# ── Swarm ─────────────────────────────────────────────────────────────────────

class TestSwarm:
    def test_swarm_query_success(self):
        # Mirrors the real backend SwarmQueryResponse.
        response_body = {
            "answer": "Based on all agents, the top client is Acme Corp.",
            "source_node_ids": ["n1", "n2"],
            "agents_involved": ["agent-hr", "agent-sales"],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert "/api/v1/swarm/query" in request.url.path
            return httpx.Response(200, json=response_body)

        sp = make_client(httpx.MockTransport(handler))
        result = sp.swarm_query("Who are the top clients?", target_agents=["agent-sales"])

        assert isinstance(result, SwarmQueryResult)
        assert "Acme Corp" in result.answer
        assert result.agents_involved == ["agent-hr", "agent-sales"]


# ── API contract (regression) ─────────────────────────────────────────────────
# These lock the SDK models to the *real* backend response shapes (verified
# against a live instance), since the two endpoints serialise edges differently.

class TestApiContract:
    def test_graph_endpoint_edge_shape(self):
        """GET /graph returns label `source`/`target` + node_count/edge_count."""
        payload = {
            "nodes": [{"id": "n1", "label": "Port Llanfair", "type": "LOCATION",
                       "properties": {}, "agent_id": "a"}],
            "edges": [{"id": "e1", "source": "Quvendor Thackeray", "target": "Port Llanfair",
                       "relation": "HARBOURMASTER_OF", "type": "RELATION",
                       "properties": {}, "agent_id": "a"}],
            "node_count": 1, "edge_count": 1, "agent_id": "a",
        }
        g = GraphPayload(**payload)
        assert g.node_count == 1 and g.edge_count == 1
        assert g.edges[0].source == "Quvendor Thackeray"
        assert g.edges[0].target == "Port Llanfair"

    def test_query_subgraph_edge_shape(self):
        """POST /query returns `answer` and a subgraph whose edges use source_id/target_id."""
        payload = {
            "question": "q", "answer": "the answer",
            "subgraph": {"nodes": [], "edges": [
                {"id": "e1", "source_id": "n1", "target_id": "n2", "relation": "R", "properties": {}}]},
            "confidence_score": 1.0,
        }
        q = QueryResult(**payload)
        assert q.answer == "the answer"
        assert q.subgraph.edges[0].source_id == "n1"

    def test_async_ingest_response_shape(self):
        """POST /ingest (async) returns status + message_id, not counts."""
        r = IngestResult(**{"status": "queued", "message_id": "m1", "agent_id": "a"})
        assert r.status == "queued" and r.message_id == "m1"


# ── Context Manager ───────────────────────────────────────────────────────────

class TestContextManager:
    def test_context_manager_closes_client(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"nodes_created": 0, "edges_created": 0, "status": "ok"})

        transport = httpx.MockTransport(handler)
        with Spaider(
            api_key=API_KEY,
            agent_id=AGENT_ID,
            http_client=httpx.Client(base_url=BASE_URL, transport=transport),
        ) as sp:
            sp.ingest("test text")
        # No error = client closed cleanly


# ── Error handling ────────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_unexpected_status_raises_spaider_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, text="Conflict")

        sp = make_client(httpx.MockTransport(handler))
        with pytest.raises(SpaiderError) as exc_info:
            sp.ingest("text")
        assert exc_info.value.status_code == 409
