"""
Unit tests for the SpAIder-as-MCP-server router (`app/api/v1/mcp_server.py`,
Phase 3 of the workflow plan).

Coverage:

  1. Tool catalogue — `list_tools()` returns the two read-only tools.
  2. Auth — missing / empty / invalid Bearer token → HTTP 401.
  3. `spaider.query` — passes the contextvar agent_id through to
     QueryService.query_nl and renders the answer + summary block.
  4. `spaider.list_recent` — issues the right Cypher and renders the rows.
  5. Validation — empty `question` and out-of-range `limit` raise.

The MCP transport itself is not exercised here — the SDK ships its own
integration tests; we focus on our wiring (auth + agent_id propagation +
tool body) which is where bugs would actually live.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1 import mcp_server as mcp_module
from app.api.v1.mcp_server import (
    _AGENT_ID,
    _bearer_token_from_scope,
    call_tool,
    list_tools,
    mcp_app,
)
from app.models.schemas import GraphPayload, Node


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Make sure no test leaks a singleton or contextvar to the next."""
    mcp_module._query_service = None
    mcp_module._graph_service = None
    mcp_module._auth_service = None
    token = _AGENT_ID.set(None)
    yield
    _AGENT_ID.reset(token)
    mcp_module._query_service = None
    mcp_module._graph_service = None
    mcp_module._auth_service = None


# ---------------------------------------------------------------------------
# 1. Tool catalogue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_returns_read_and_write_set():
    tools = await list_tools()
    names = [t.name for t in tools]
    assert names == [
        "spaider.query",
        "spaider.list_recent",
        "spaider.ingest_fact",
        "spaider.feedback",
        "spaider.status",
    ]
    # Schemas must declare required fields.
    query_tool = next(t for t in tools if t.name == "spaider.query")
    assert "question" in query_tool.inputSchema["required"]
    ingest_tool = next(t for t in tools if t.name == "spaider.ingest_fact")
    assert "text" in ingest_tool.inputSchema["required"]
    fb_tool = next(t for t in tools if t.name == "spaider.feedback")
    assert "used_node_ids" in fb_tool.inputSchema["required"]
    assert "success" in fb_tool.inputSchema["required"]


# ---------------------------------------------------------------------------
# 2. Auth — the Streamable HTTP ASGI wrapper (`mcp_app`) resolves the bearer
#    token to an agent_id, binds it to the ContextVar, then delegates. We test
#    the wrapper directly with fake ASGI scope/send rather than the SDK's
#    session manager (which ships its own transport tests).
# ---------------------------------------------------------------------------


def _http_scope(auth: str | None = None) -> dict:
    """A minimal ASGI http scope, optionally carrying an Authorization header."""
    headers = [] if auth is None else [(b"authorization", auth.encode("latin-1"))]
    return {"type": "http", "method": "POST", "path": "/api/v1/mcp", "headers": headers}


def _capturing_send():
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    return send, sent


async def _noop_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def test_bearer_token_from_scope_parses_and_rejects():
    assert _bearer_token_from_scope(_http_scope("Bearer sk-abc")) == "sk-abc"
    assert _bearer_token_from_scope(_http_scope("Bearer    ")) is None  # empty after strip
    assert _bearer_token_from_scope(_http_scope(None)) is None
    assert _bearer_token_from_scope(_http_scope("Basic xyz")) is None  # wrong scheme


@pytest.mark.asyncio
async def test_mcp_app_missing_header_sends_401_without_delegating():
    send, sent = _capturing_send()
    with patch.object(
        mcp_module.mcp_session_manager, "handle_request", new=AsyncMock()
    ) as handle:
        await mcp_app(_http_scope(None), _noop_receive, send)
    assert sent[0]["status"] == 401
    handle.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_app_empty_token_sends_401():
    send, sent = _capturing_send()
    with patch.object(mcp_module.mcp_session_manager, "handle_request", new=AsyncMock()):
        await mcp_app(_http_scope("Bearer "), _noop_receive, send)
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_mcp_app_invalid_token_sends_401():
    auth_mock = AsyncMock()
    auth_mock.verify_token = AsyncMock(side_effect=ValueError("nope"))
    send, sent = _capturing_send()
    with patch.object(mcp_module, "_get_auth_service", return_value=auth_mock), patch.object(
        mcp_module.mcp_session_manager, "handle_request", new=AsyncMock()
    ) as handle:
        await mcp_app(_http_scope("Bearer sk-bogus"), _noop_receive, send)
    assert sent[0]["status"] == 401
    handle.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_app_valid_token_binds_agent_id_then_resets():
    """The decisive isolation test: the agent_id must be visible to the
    delegated handler (proving the ContextVar reaches the tool callbacks) and
    cleared again once the request returns."""
    auth_mock = AsyncMock()
    auth_mock.verify_token = AsyncMock(return_value={"sub": "agent-123", "tenant_id": "default"})
    seen: dict[str, object] = {}

    async def fake_handle_request(scope, receive, send):
        seen["agent_id"] = _AGENT_ID.get()

    send, sent = _capturing_send()
    with patch.object(mcp_module, "_get_auth_service", return_value=auth_mock), patch.object(
        mcp_module.mcp_session_manager, "handle_request", new=fake_handle_request
    ):
        await mcp_app(_http_scope("Bearer sk-real"), _noop_receive, send)

    assert seen["agent_id"] == "agent-123"  # contextvar propagated into the handler
    assert sent == []                       # no 401 emitted; the handler owns the response
    assert _AGENT_ID.get() is None          # reset after the request returns


# ---------------------------------------------------------------------------
# 3. spaider.query — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_query_uses_agent_id_from_context():
    _AGENT_ID.set("agent-x")

    fake_subgraph = GraphPayload(
        nodes=[
            Node(id="n1", label="Acme Corp", type="ORG"),
            Node(id="n2", label="acquired", type="EVENT"),
        ],
        edges=[],
    )
    fake_result = type(
        "QR", (), {
            "answer": "Acme acquired Beta in 2024.",
            "subgraph": fake_subgraph,
            "confidence_score": 0.87,
            "iterations_used": 1,
            "from_cache": False,
        },
    )()

    qs_mock = AsyncMock()
    qs_mock.query_nl = AsyncMock(return_value=fake_result)
    with patch.object(mcp_module, "_get_query_service", return_value=qs_mock):
        contents = await call_tool("spaider.query", {"question": "what happened to Acme?"})

    qs_mock.query_nl.assert_awaited_once_with("what happened to Acme?", "agent-x", top_k=15)
    assert len(contents) == 1
    body = contents[0].text
    assert "Acme acquired Beta in 2024" in body
    assert "Confidence: 0.87" in body
    assert "Acme Corp(ORG)" in body
    # No FACT nodes in this fixture → no facts section, only entities.
    assert "Top supporting facts:" not in body
    assert "Top supporting entities:" in body


@pytest.mark.asyncio
async def test_call_tool_query_surfaces_fact_descriptions_separately():
    """when the subgraph contains FACT-type nodes, their
    descriptions must appear in a dedicated `Top supporting facts:`
    section, not be collapsed into the entity summary. Ensures the
    receiving model has raw text to synthesise from."""
    _AGENT_ID.set("agent-x")

    fact_text = (
        "Stark Industries (Tony, VP Product) on a call with Olivia: "
        "they will churn unless feature Y ships before their Q2 board "
        "review on June 20. Renewal at risk: $480k ARR."
    )
    fake_subgraph = GraphPayload(
        nodes=[
            Node(id="f1", label="fact: …", type="FACT", description=fact_text),
            Node(id="n1", label="Stark Industries", type="ORGANIZATION"),
            Node(id="n2", label="Tony", type="PERSON"),
        ],
        edges=[],
    )
    fake_result = type(
        "QR", (), {
            "answer": "synthesised answer here",
            "subgraph": fake_subgraph,
            "confidence_score": 0.92,
            "iterations_used": 2,
            "from_cache": False,
        },
    )()

    qs_mock = AsyncMock()
    qs_mock.query_nl = AsyncMock(return_value=fake_result)
    with patch.object(mcp_module, "_get_query_service", return_value=qs_mock):
        contents = await call_tool("spaider.query", {"question": "stark?"})

    body = contents[0].text
    assert "Top supporting facts:" in body
    assert fact_text in body
    # Entities still appear, but in their own section — not duplicated
    # in the facts list.
    assert "Top supporting entities:" in body
    assert "Stark Industries(ORGANIZATION)" in body
    assert "Tony(PERSON)" in body
    # The FACT node's label should NOT appear in the entity summary.
    assert "fact: …(FACT)" not in body


@pytest.mark.asyncio
async def test_call_tool_query_caps_long_fact_descriptions():
    """A pathologically large FACT description should not blow past the
    600-char per-fact cap — bounded so a single huge ingest can't
    overwhelm the receiving model's context."""
    _AGENT_ID.set("agent-x")
    long_text = "X" * 5_000
    fake_subgraph = GraphPayload(
        nodes=[Node(id="f", label="fact:", type="FACT", description=long_text)],
        edges=[],
    )
    fake_result = type(
        "QR", (), {
            "answer": "stub", "subgraph": fake_subgraph,
            "confidence_score": 0.5, "iterations_used": 1, "from_cache": False,
        },
    )()
    qs_mock = AsyncMock()
    qs_mock.query_nl = AsyncMock(return_value=fake_result)
    with patch.object(mcp_module, "_get_query_service", return_value=qs_mock):
        contents = await call_tool("spaider.query", {"question": "q"})
    body = contents[0].text
    # 600 X's allowed; the 601st would be a sign the cap is leaking.
    assert "X" * 600 in body
    assert "X" * 601 not in body


@pytest.mark.asyncio
async def test_call_tool_query_appends_node_ids_trailer_for_feedback():
    """every spaider.query response must include a
    machine-parseable ``Node IDs (for feedback): id1, id2, ...`` line so
    feedback-loop-aware callers can echo them to /api/v1/feedback."""
    _AGENT_ID.set("agent-x")
    fake_subgraph = GraphPayload(
        nodes=[
            Node(id="fact-1", label="fact: …", type="FACT", description="d"),
            Node(id="ent-1", label="Stark", type="ORGANIZATION"),
            Node(id="ent-2", label="Tony", type="PERSON"),
        ],
        edges=[],
    )
    fake_result = type(
        "QR", (), {
            "answer": "stub", "subgraph": fake_subgraph,
            "confidence_score": 0.5, "iterations_used": 1, "from_cache": False,
        },
    )()
    qs_mock = AsyncMock()
    qs_mock.query_nl = AsyncMock(return_value=fake_result)
    with patch.object(mcp_module, "_get_query_service", return_value=qs_mock):
        contents = await call_tool("spaider.query", {"question": "q"})
    body = contents[0].text
    assert "Node IDs (for feedback): " in body
    # Trailer should list every retrieved node id, in subgraph order.
    trailer_line = next(
        line for line in body.splitlines() if line.startswith("Node IDs (for feedback):")
    )
    ids_part = trailer_line.split(":", 1)[1].strip()
    assert ids_part == "fact-1, ent-1, ent-2"


@pytest.mark.asyncio
async def test_call_tool_query_caps_node_id_trailer_at_50():
    """Pathological-case guard: a 200-node subgraph must not produce a
    200-id trailer."""
    _AGENT_ID.set("agent-x")
    nodes = [Node(id=f"n{i}", label=f"L{i}", type="OTHER") for i in range(200)]
    fake_subgraph = GraphPayload(nodes=nodes, edges=[])
    fake_result = type(
        "QR", (), {
            "answer": "stub", "subgraph": fake_subgraph,
            "confidence_score": 0.5, "iterations_used": 1, "from_cache": False,
        },
    )()
    qs_mock = AsyncMock()
    qs_mock.query_nl = AsyncMock(return_value=fake_result)
    with patch.object(mcp_module, "_get_query_service", return_value=qs_mock):
        contents = await call_tool("spaider.query", {"question": "q"})
    body = contents[0].text
    trailer_line = next(
        line for line in body.splitlines() if line.startswith("Node IDs (for feedback):")
    )
    ids_part = trailer_line.split(":", 1)[1].strip()
    assert len(ids_part.split(",")) == 50


@pytest.mark.asyncio
async def test_call_tool_query_skips_fact_nodes_with_empty_description():
    """A FACT node without a description (shouldn't happen in practice,
    but defend the renderer) must not appear in the facts section."""
    _AGENT_ID.set("agent-x")
    fake_subgraph = GraphPayload(
        nodes=[
            Node(id="f1", label="empty fact", type="FACT", description=None),
            Node(id="n1", label="Stark", type="ORGANIZATION"),
        ],
        edges=[],
    )
    fake_result = type(
        "QR", (), {
            "answer": "stub", "subgraph": fake_subgraph,
            "confidence_score": 0.5, "iterations_used": 1, "from_cache": False,
        },
    )()
    qs_mock = AsyncMock()
    qs_mock.query_nl = AsyncMock(return_value=fake_result)
    with patch.object(mcp_module, "_get_query_service", return_value=qs_mock):
        contents = await call_tool("spaider.query", {"question": "q"})
    body = contents[0].text
    assert "Top supporting facts:" not in body
    assert "Stark(ORGANIZATION)" in body


# ---------------------------------------------------------------------------
# 4. spaider.list_recent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_list_recent_runs_cypher_with_agent_id():
    _AGENT_ID.set("agent-y")

    qs_mock = AsyncMock()
    qs_mock.query_cypher = AsyncMock(return_value=[
        {"label": "Project X", "type": "PROJECT", "created_at": "2026-04-28T08:00:00Z"},
        {"label": "Note 1", "type": "NOTE", "created_at": "2026-04-27T18:00:00Z"},
    ])
    with patch.object(mcp_module, "_get_query_service", return_value=qs_mock):
        contents = await call_tool("spaider.list_recent", {"limit": 5})

    args, _ = qs_mock.query_cypher.call_args
    cypher_body, agent_id = args
    assert "MATCH (n:SpaiderNode {agent_id: $agent_id})" in cypher_body
    assert "LIMIT 5" in cypher_body
    assert agent_id == "agent-y"
    body = contents[0].text
    assert "Project X" in body
    assert "[PROJECT]" in body
    assert "Note 1" in body


@pytest.mark.asyncio
async def test_call_tool_list_recent_handles_empty_graph():
    _AGENT_ID.set("agent-z")
    qs_mock = AsyncMock()
    qs_mock.query_cypher = AsyncMock(return_value=[])
    with patch.object(mcp_module, "_get_query_service", return_value=qs_mock):
        contents = await call_tool("spaider.list_recent", {})
    assert contents[0].text == "(no nodes yet)"


# ---------------------------------------------------------------------------
# 5. Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_query_rejects_empty_question():
    _AGENT_ID.set("agent-x")
    with pytest.raises(ValueError):
        await call_tool("spaider.query", {"question": "   "})


@pytest.mark.asyncio
async def test_call_tool_list_recent_rejects_out_of_range_limit():
    _AGENT_ID.set("agent-x")
    with pytest.raises(ValueError):
        await call_tool("spaider.list_recent", {"limit": 0})
    with pytest.raises(ValueError):
        await call_tool("spaider.list_recent", {"limit": 999})


# ---------------------------------------------------------------------------
# Write tool: spaider.ingest_fact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_ingest_fact_passes_agent_id_and_default_source():
    """The ingest tool must build an IngestRequest with agent_id from the
    contextvar and default `source = 'claude-code-session'` when omitted."""
    _AGENT_ID.set("agent-w")

    fake_response = type(
        "ISR", (), {
            "nodes_created": 2, "nodes_merged": 0,
            "edges_created": 1, "edges_merged": 0,
            "latency_ms": 12.34,
        },
    )()

    captured: dict[str, Any] = {}

    async def fake_ingest_text_sync(req):
        # IngestRequest must carry the contextvar's agent_id + the default
        # source string when the caller omitted `source`.
        captured["agent_id"] = req.agent_id
        captured["source"] = req.source
        captured["text"] = req.text
        captured["metadata"] = req.metadata
        return fake_response

    with patch("app.api.v1.ingest.ingest_text_sync", new=fake_ingest_text_sync):
        contents = await call_tool(
            "spaider.ingest_fact",
            {"text": "user prefers merge commits over squash"},
        )

    assert captured["agent_id"] == "agent-w"
    assert captured["source"] == "claude-code-session"
    assert captured["text"] == "user prefers merge commits over squash"
    assert captured["metadata"] is None

    body = contents[0].text
    assert "Ingested under agent agent-w" in body
    assert "Nodes: 2 created / 0 merged" in body
    assert "Edges: 1 created / 0 merged" in body


@pytest.mark.asyncio
async def test_call_tool_ingest_fact_honours_custom_source_and_metadata():
    _AGENT_ID.set("agent-w")
    captured: dict[str, Any] = {}

    async def fake_ingest_text_sync(req):
        captured["source"] = req.source
        captured["metadata"] = req.metadata
        return type("ISR", (), {
            "nodes_created": 0, "nodes_merged": 0,
            "edges_created": 0, "edges_merged": 0, "latency_ms": 1.0,
        })()

    with patch("app.api.v1.ingest.ingest_text_sync", new=fake_ingest_text_sync):
        await call_tool(
            "spaider.ingest_fact",
            {
                "text": "fact body",
                "source": "lessons-learned",
                "metadata": {"session_id": "abc", "kind": "preference"},
            },
        )

    assert captured["source"] == "lessons-learned"
    assert captured["metadata"] == {"session_id": "abc", "kind": "preference"}


@pytest.mark.asyncio
async def test_call_tool_ingest_fact_rejects_empty_text():
    _AGENT_ID.set("agent-w")
    with pytest.raises(ValueError):
        await call_tool("spaider.ingest_fact", {"text": "   "})


@pytest.mark.asyncio
async def test_call_tool_ingest_fact_rejects_oversized_text():
    _AGENT_ID.set("agent-w")
    with pytest.raises(ValueError):
        await call_tool("spaider.ingest_fact", {"text": "x" * 50_001})


@pytest.mark.asyncio
async def test_call_tool_ingest_fact_rejects_non_object_metadata():
    _AGENT_ID.set("agent-w")
    with pytest.raises(ValueError):
        await call_tool(
            "spaider.ingest_fact",
            {"text": "hi", "metadata": "not a dict"},
        )


@pytest.mark.asyncio
async def test_call_tool_ingest_fact_requires_agent_context():
    """Defensive: writes must never go to the wrong agent. If the ASGI auth
    wrapper forgot to set the contextvar we fail closed, never silently
    write under a default identity."""
    # _AGENT_ID is None per the autouse fixture
    with pytest.raises(ValueError):
        await call_tool("spaider.ingest_fact", {"text": "anything"})


@pytest.mark.asyncio
async def test_call_tool_unknown_name_raises():
    _AGENT_ID.set("agent-x")
    with pytest.raises(ValueError):
        await call_tool("spaider.unknown", {})


def test_query_service_factory_passes_graph_service_dependency():
    """Regression: a real end-to-end MCP smoke caught
    `QueryService.__init__() missing 1 required positional argument:
    'graph_service'`. The factory must wire the graph service in."""
    from unittest.mock import patch as _patch
    fake_graph = object()
    with _patch.object(mcp_module, "_get_graph_service", return_value=fake_graph):
        # Patch QueryService class so we observe the constructor call without
        # actually building a Neo4j-bound instance.
        with _patch.object(mcp_module, "QueryService") as qs_cls:
            qs_cls.return_value = "instance"
            instance = mcp_module._get_query_service()
        qs_cls.assert_called_once_with(graph_service=fake_graph)
        assert instance == "instance"


@pytest.mark.asyncio
async def test_call_tool_without_agent_context_raises():
    """Defensive: if the ASGI auth wrapper ever forgets to set the contextvar,
    we fail loudly rather than serve cross-agent data."""
    # _AGENT_ID is None per the autouse fixture
    with pytest.raises(ValueError):
        await call_tool("spaider.query", {"question": "anything"})


# ---------------------------------------------------------------------------
# 7. Spaider.feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_feedback_applies_hebbian_update():
    """Happy path — calls _apply_hebbian_update with the right args and
    returns a confirmation payload."""
    _AGENT_ID.set("agent-z")
    captured: dict[str, Any] = {}

    async def fake_apply(query_id, node_ids, success, received_at):
        captured["query_id"] = query_id
        captured["node_ids"] = node_ids
        captured["success"] = success

    with patch("app.api.v1.feedback._apply_hebbian_update", new=fake_apply):
        contents = await call_tool(
            "spaider.feedback",
            {"used_node_ids": ["n1", "n2", "n3"], "success": True},
        )

    body = contents[0].text
    assert "Feedback applied" in body
    assert "agent-z" in body
    assert "↑ +0.1" in body
    assert "Nodes touched: 3" in body
    assert captured["node_ids"] == ["n1", "n2", "n3"]
    assert captured["success"] is True


@pytest.mark.asyncio
async def test_call_tool_feedback_failure_direction_renders():
    """When success=False, the response should call out the negative
    direction so the operator immediately knows whether they reinforced
    or punished a path."""
    _AGENT_ID.set("agent-z")

    async def fake_apply(query_id, node_ids, success, received_at):
        pass

    with patch("app.api.v1.feedback._apply_hebbian_update", new=fake_apply):
        contents = await call_tool(
            "spaider.feedback",
            {"used_node_ids": ["n1"], "success": False, "rationale": "wrong attribution"},
        )
    body = contents[0].text
    assert "↓ -0.1" in body
    assert "wrong attribution" in body


@pytest.mark.asyncio
async def test_call_tool_feedback_dedupes_node_ids():
    """The REST endpoint dedupes via a Pydantic validator. The MCP wrapper
    must mirror that or the per-edge update count drifts."""
    _AGENT_ID.set("agent-z")
    captured: dict[str, Any] = {}

    async def fake_apply(query_id, node_ids, success, received_at):
        captured["node_ids"] = node_ids

    with patch("app.api.v1.feedback._apply_hebbian_update", new=fake_apply):
        await call_tool(
            "spaider.feedback",
            {
                "used_node_ids": ["a", "b", "a", "c", "b"],
                "success": True,
            },
        )
    # Order preserved, duplicates dropped.
    assert captured["node_ids"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_call_tool_feedback_rejects_empty_node_ids():
    _AGENT_ID.set("agent-z")
    with pytest.raises(ValueError, match="non-empty array"):
        await call_tool("spaider.feedback", {"used_node_ids": [], "success": True})


@pytest.mark.asyncio
async def test_call_tool_feedback_rejects_non_string_ids():
    _AGENT_ID.set("agent-z")
    with pytest.raises(ValueError, match="non-empty strings"):
        await call_tool(
            "spaider.feedback",
            {"used_node_ids": ["a", "", "b"], "success": True},
        )


@pytest.mark.asyncio
async def test_call_tool_feedback_rejects_missing_success():
    _AGENT_ID.set("agent-z")
    with pytest.raises(ValueError, match="must be a boolean"):
        await call_tool("spaider.feedback", {"used_node_ids": ["a"]})


@pytest.mark.asyncio
async def test_call_tool_feedback_caps_node_id_count():
    """200-id cap matches the REST endpoint's validator."""
    _AGENT_ID.set("agent-z")
    too_many = [f"id{i}" for i in range(201)]
    with pytest.raises(ValueError, match="at most 200"):
        await call_tool(
            "spaider.feedback",
            {"used_node_ids": too_many, "success": True},
        )


@pytest.mark.asyncio
async def test_call_tool_feedback_requires_agent_context():
    """No agent_id contextvar → refuse, never apply cross-agent updates."""
    with pytest.raises(ValueError, match="auth bug"):
        await call_tool("spaider.feedback", {"used_node_ids": ["a"], "success": True})
