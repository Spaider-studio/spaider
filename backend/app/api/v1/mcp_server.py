"""
SpAIder as an MCP **server** (Phase 3 of the workflow plan).

This is the opposite direction of the MCP **client** that already lives in
``app/connectors/mcp_connector.py``: that one *pulls* resources from external
MCP servers into SpAIder's graph. *This* module *exposes* SpAIder over MCP so
any MCP client (Claude Code in particular) can use it as durable, agent-scoped
memory.

Architecture
------------
- Mounted under ``/api/v1/mcp`` in ``app/api/router.py``.
- Two routes:
    GET  /api/v1/mcp/sse        — opens an SSE stream (long-lived).
    POST /api/v1/mcp/messages/  — receives client→server JSON-RPC messages.
- Auth is HTTP-layer: the GET request must carry
  ``Authorization: Bearer <api-key>``. The key resolves to an agent_id via
  ``AuthService.verify_token`` (already supports ``sk-…`` raw API keys).
  The agent_id is then bound to a ``contextvars.ContextVar`` so the per-tool
  handlers can read it without leaking it through the MCP message envelope.
- Same process as the main FastAPI app — reuses ``QueryService``,
  ``GraphService`` and ``AuthService``. No new datastore.

Tools exposed (read-only first per the plan's cut-point — ``spaider.ingest_fact``
ships in a follow-up)::

    spaider.query(question, top_k=10)
        Wraps QueryService.query_nl; returns the LLM answer and a brief
        subgraph summary.

    spaider.list_recent(limit=10)
        Lists the most recently-created SpaiderNodes for the calling agent
        — useful for a "what did we last talk about?" session-start probe.

Per-developer setup
-------------------
1. Create a ``dev-{username}`` agent::

       POST /api/v1/agents  {"name": "dev-{username}", ...}

   Capture the returned ``api_key``.

2. Add the server to your Claude Code ``~/.claude/.mcp.json``::

       {
         "mcpServers": {
           "spaider": {
             "url":     "http://localhost:8000/api/v1/mcp/sse",
             "headers": {"Authorization": "Bearer sk-..."}
           }
         }
       }

3. In a session, invoke the tools by name (``spaider.query``, etc.). All
   reads are scoped to the agent the API key resolves to — different
   developers see different graphs.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any, Optional

import mcp.types as mcp_types
from fastapi import HTTPException
from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

from app.services.auth_service import AuthService
from app.services.graph_service import GraphService
from app.services.query_service import QueryService

logger = logging.getLogger(__name__)

# Per-request agent_id binding. Set in the SSE handler after auth, read
# inside the tool callbacks. ContextVar (not a global) so concurrent
# connections don't trample each other's identity.
_AGENT_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "spaider_mcp_agent_id", default=None,
)


# ---------------------------------------------------------------------------
# Tool registration on a single MCP Server instance
# ---------------------------------------------------------------------------

mcp_server = Server("spaider")

# Lazy singletons — same pattern as the rest of app/api/v1.
_query_service: Optional[QueryService] = None
_graph_service: Optional[GraphService] = None
_auth_service: Optional[AuthService] = None


def _get_graph_service() -> GraphService:
    """Return the lifespan-initialised GraphService singleton.

    Importantly we reuse the instance that ``main.lifespan`` constructed
    and ran ``initialize()`` on — that's the one that has detected the
    Neo4j vector index. Constructing a fresh ``GraphService()`` here
    leaves ``vector_index_available=False`` permanently and breaks
    ``spaider.query`` even when the index is ONLINE. Fall back to a
    bare instance only if main has not yet initialised one (e.g. when
    the standalone MCP app from ``app.mcp_standalone`` is starting up
    before its own lifespan has run)."""
    global _graph_service
    if _graph_service is None:
        from app import main as _app_main
        if _app_main._graph_service is not None:
            _graph_service = _app_main._graph_service
        else:
            _graph_service = GraphService()
    return _graph_service


def _get_query_service() -> QueryService:
    global _query_service
    if _query_service is None:
        # QueryService requires a GraphService dependency for Cypher access
        # — same construction pattern used by app/api/v1/query.py.
        _query_service = QueryService(graph_service=_get_graph_service())
    return _query_service


def _get_auth_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service


@mcp_server.list_tools()
async def list_tools() -> list[mcp_types.Tool]:
    """Tool catalogue. Read tools (`spaider.query`, `spaider.list_recent`)
    plus the write tool (`spaider.ingest_fact`)."""
    return [
        mcp_types.Tool(
            name="spaider.query",
            description=(
                "Ask SpAIder a question. Searches the calling agent's "
                "knowledge graph and returns an LLM-generated answer plus a "
                "short summary of the supporting nodes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Natural-language question.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Optional retrieval depth (default 10).",
                        "default": 10,
                    },
                },
                "required": ["question"],
            },
        ),
        mcp_types.Tool(
            name="spaider.list_recent",
            description=(
                "List the most recently created SpaiderNodes belonging to the "
                "calling agent. Useful as a session-start probe to recall "
                "prior context."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of nodes to return (default 10).",
                        "default": 10,
                    },
                },
            },
        ),
        mcp_types.Tool(
            name="spaider.ingest_fact",
            description=(
                "Write a fact into the calling agent's knowledge graph. "
                "The text is run through the standard extraction pipeline "
                "(LLM extraction -> entity resolution -> Neo4j MERGE) just "
                "like POST /api/v1/ingest/sync. Use this at session end to "
                "record lessons learned, user preferences, project state — "
                "anything you want a future session to recall via "
                "`spaider.query` or `spaider.list_recent`."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "The fact to ingest. Plain natural language is "
                            "fine; the extractor will produce nodes and edges."
                        ),
                    },
                    "source": {
                        "type": "string",
                        "description": (
                            "Optional provenance tag stored on the resulting "
                            "nodes. Defaults to `claude-code-session`."
                        ),
                    },
                    "metadata": {
                        "type": "object",
                        "description": (
                            "Optional free-form dict attached to the ingest "
                            "request — surfaced to downstream services."
                        ),
                        "additionalProperties": True,
                    },
                },
                "required": ["text"],
            },
        ),
        mcp_types.Tool(
            name="spaider.feedback",
            description=(
                "Apply Hebbian feedback to the calling agent's knowledge "
                "graph based on whether a recent retrieval helped. Wraps "
                "POST /api/v1/system/feedback synchronously — by the time "
                "this tool returns, every RELATION edge between the named "
                "nodes has had its `utility_weight` nudged ±0.1 (capped to "
                "[0.1, 2.0]). Use after `spaider.query` when you can "
                "confidently judge the outcome."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "used_node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Node IDs from a recent `spaider.query` "
                            "response (machine-readable trailer "
                            "`Node IDs (for feedback): id1, id2, ...`)."
                        ),
                        "minItems": 1,
                    },
                    "success": {
                        "type": "boolean",
                        "description": (
                            "True if the retrieved nodes shaped a useful "
                            "answer; False if they led the model astray. "
                            "Do NOT call with a fabricated value when "
                            "uncertain — just skip the feedback call."
                        ),
                    },
                    "rationale": {
                        "type": "string",
                        "description": (
                            "Optional one-sentence note for log analysis. "
                            "Not stored on edges; only logged."
                        ),
                    },
                },
                "required": ["used_node_ids", "success"],
            },
        ),
    ]


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent]:
    """Dispatch tool calls to the underlying services. Auth was checked
    at SSE-open time; here we just read the agent_id contextvar."""
    agent_id = _AGENT_ID.get()
    if agent_id is None:
        raise ValueError("Tool called without authenticated agent_id (auth bug?)")

    if name == "spaider.query":
        question = arguments.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("`question` is required and must be a non-empty string")
        # Default top_k bumped to 15 (was 10) — for synthesis questions the
        # model needs enough context that the relevant FACT-type nodes
        # actually surface alongside the entity nodes.
        top_k = arguments.get("top_k", 15)
        result = await _get_query_service().query_nl(question, agent_id, top_k=top_k)
        nodes = result.subgraph.nodes if result.subgraph else []

        # Split the subgraph by node type. FACT nodes carry the
        # original ingested text in `description`; surface them verbatim so
        # the calling model has raw facts to synthesise from, not just the
        # secondary entity-name list. Other types stay in the entity summary.
        fact_nodes = [n for n in nodes if n.type == "FACT" and n.description]
        entity_nodes = [n for n in nodes if n.type != "FACT"]

        # Lead with the bare answer span for factoid questions so a caller
        # answering "in as few words as possible" echoes it verbatim instead of
        # re-paraphrasing the prose; the full grounded answer follows for
        # callers that want the explanation.
        sections: list[str] = []
        direct = getattr(result, "direct_answer", None)
        if direct:
            sections.append(f"Direct answer: {direct}")
        sections.append(f"Answer:\n{result.answer}")
        sections.append(
            f"Confidence: {result.confidence_score:.2f}  |  "
            f"Iterations: {result.iterations_used}  |  "
            f"From cache: {result.from_cache}"
        )

        if fact_nodes:
            # Cap the per-fact preview so a single huge ingest can't blow
            # past the model's context. 600 chars ≈ 150 tokens, plenty for
            # the facts we ingest in `spaider.ingest_fact` (capped at 50k).
            fact_lines = [
                f"- {(n.description or '').strip()[:600]}"
                for n in fact_nodes[:8]
            ]
            sections.append("Top supporting facts:\n" + "\n".join(fact_lines))

        entity_summary = ", ".join(
            f"{n.label}({n.type})" for n in entity_nodes[:8]
        ) or "(no supporting entities)"
        sections.append(f"Top supporting entities: {entity_summary}")

        # Machine-parseable trailer. Lets a feedback-loop-aware
        # caller (e.g. the benchmark runner) echo these node IDs back to
        # /api/v1/feedback so Hebbian utility_weight updates fire on every
        # successful query. Strictly additive — clients ignoring this line
        # behave exactly as before. ID list capped at the 50 most relevant
        # so the trailer can't bloat the response on huge subgraphs.
        node_ids = [n.id for n in nodes if getattr(n, "id", None)][:50]
        if node_ids:
            sections.append("Node IDs (for feedback): " + ", ".join(node_ids))

        # Machine-parseable backend token line (counts only, no prompt text).
        # The benchmark runner parses this to add SpAIder's server-side
        # grounding spend to the agent-side tokens for a true total cost.
        # Always emitted (0/0 on a cache hit) so the parser never has to
        # special-case its absence.
        _raw_tok = getattr(result, "token_usage", None)
        _tok = _raw_tok if isinstance(_raw_tok, dict) else {}
        sections.append(
            f"Backend tokens: in={int(_tok.get('prompt_tokens', 0))} "
            f"out={int(_tok.get('completion_tokens', 0))}"
        )

        return [mcp_types.TextContent(type="text", text="\n\n".join(sections))]

    if name == "spaider.ingest_fact":
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("`text` is required and must be a non-empty string")
        # Reasonable upper bound — bigger payloads should go through
        # `/api/v1/ingest/file` etc. instead.
        if len(text) > 50_000:
            raise ValueError("`text` must be <= 50_000 characters")
        source = arguments.get("source") or "claude-code-session"
        if not isinstance(source, str):
            raise ValueError("`source` must be a string when provided")
        metadata = arguments.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("`metadata` must be an object when provided")

        # Late-import the ingest endpoint module to avoid a circular import
        # at module load time (ingest.py imports a lot of services).
        from app.api.v1 import ingest as _ingest_module
        from app.models.requests import IngestRequest

        request_obj = IngestRequest(
            text=text,
            agent_id=agent_id,
            source=source,
            metadata=metadata,
        )
        result = await _ingest_module.ingest_text_sync(request_obj)
        body = (
            f"Ingested under agent {agent_id}.\n"
            f"Nodes: {result.nodes_created} created / {result.nodes_merged} merged\n"
            f"Edges: {result.edges_created} created / {result.edges_merged} merged\n"
            f"Latency: {result.latency_ms:.1f} ms"
        )
        return [mcp_types.TextContent(type="text", text=body)]

    if name == "spaider.list_recent":
        limit = int(arguments.get("limit", 10))
        if limit < 1 or limit > 200:
            raise ValueError("`limit` must be between 1 and 200")
        cypher = (
            "MATCH (n:SpaiderNode {agent_id: $agent_id}) "
            "WHERE n.created_at IS NOT NULL "
            "RETURN n.label AS label, n.type AS type, n.created_at AS created_at "
            "ORDER BY n.created_at DESC "
            f"LIMIT {limit}"
        )
        rows = await _get_query_service().query_cypher(cypher, agent_id)
        if not rows:
            return [mcp_types.TextContent(type="text", text="(no nodes yet)")]
        lines = [
            f"- {row.get('label', '?')} [{row.get('type', '?')}]  "
            f"({row.get('created_at', '?')})"
            for row in rows
        ]
        return [mcp_types.TextContent(type="text", text="\n".join(lines))]

    if name == "spaider.feedback":
        # Hebbian feedback from a Claude Code session, mirroring
        # what the benchmark runner does. Synchronous (we await the
        # Cypher write) so the caller gets confirmation rather than a fire-
        # and-forget 202.
        used_node_ids = arguments.get("used_node_ids")
        if not isinstance(used_node_ids, list) or not used_node_ids:
            raise ValueError(
                "`used_node_ids` is required and must be a non-empty array"
            )
        if not all(isinstance(nid, str) and nid for nid in used_node_ids):
            raise ValueError("`used_node_ids` must contain only non-empty strings")
        # Defensive cap matching the REST endpoint's pydantic dedup behaviour.
        if len(used_node_ids) > 200:
            raise ValueError("`used_node_ids` must contain at most 200 IDs")
        # De-duplicate while preserving insertion order — same as the
        # validator on `FeedbackPayload.used_node_ids`.
        seen: set[str] = set()
        used_node_ids = [
            x for x in used_node_ids if not (x in seen or seen.add(x))
        ]
        success = arguments.get("success")
        if not isinstance(success, bool):
            raise ValueError("`success` is required and must be a boolean")
        rationale = arguments.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("`rationale` must be a string when provided")

        # Late-import to avoid a circular at module load.
        import uuid as _uuid
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        from app.api.v1 import feedback as _feedback_module

        query_id = str(_uuid.uuid4())
        await _feedback_module._apply_hebbian_update(
            query_id=query_id,
            node_ids=used_node_ids,
            success=success,
            received_at=_dt.now(_tz.utc).isoformat(),
        )
        body = (
            f"Feedback applied for agent {agent_id}.\n"
            f"Direction: {'↑ +0.1 (success)' if success else '↓ -0.1 (failure)'}\n"
            f"Nodes touched: {len(used_node_ids)} (every RELATION edge between "
            f"them was nudged within [0.1, 2.0]).\n"
            f"query_id: {query_id}"
        )
        if rationale:
            body += f"\nrationale: {rationale}"
        return [mcp_types.TextContent(type="text", text=body)]

    raise ValueError(f"Unknown tool: {name!r}")


# ---------------------------------------------------------------------------
# SSE transport wired into FastAPI
# ---------------------------------------------------------------------------

# The transport's POST path must be reachable from the SSE handler — the
# server announces it to the client as a *relative* URL. Keep the value
# matching the Mount path inside `mcp_app` below; the absolute URL the
# client ends up POSTing to is `<sse_url's parent>/messages/?session_id=…`,
# i.e. mount-prefix + this path. If the value has the full prefix in it,
# resolution doubles up (`/api/v1/mcp/api/v1/mcp/messages/` 404).
_sse_transport = SseServerTransport("/messages/")


async def _resolve_agent_id(request: Request) -> str:
    """Extract agent_id from the bearer token. 401 on missing/invalid."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = header[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty Bearer token")
    try:
        payload = await _get_auth_service().verify_token(token)
    except Exception as exc:  # noqa: BLE001 — degrade to 401 on any auth error
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc
    agent_id = payload.get("sub") if isinstance(payload, dict) else None
    if not agent_id:
        raise HTTPException(status_code=401, detail="Token has no agent_id")
    return agent_id


async def mcp_sse_endpoint(request: Request) -> Response:
    """Open the SSE stream. Auth happens here; agent_id is bound to a
    ContextVar that the tool callbacks read.

    Returns an empty Response after the stream closes so Starlette's
    Route handler doesn't crash with ``TypeError: 'NoneType' object is
    not callable`` while trying to call the (None) return value as an
    ASGI app. The actual response was already sent inside
    ``_sse_transport.connect_sse``; this trailing Response is a no-op
    on the wire because the connection is closed by the time we get here.
    """
    agent_id = await _resolve_agent_id(request)
    token = _AGENT_ID.set(agent_id)
    try:
        async with _sse_transport.connect_sse(
            request.scope, request.receive, request._send,  # type: ignore[attr-defined]
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )
    finally:
        _AGENT_ID.reset(token)
    return Response()


# Build a self-contained Starlette ASGI sub-app rather than a FastAPI
# APIRouter. Why: FastAPI's `include_router(prefix=...)` plus
# Starlette's `Mount` interact badly — the trailing-slash + query-string
# URL `/messages/?session_id=…` that the SSE transport advertises ends
# up 404'ing because the prefix-stripping doesn't quite hand the
# remainder to the Mount the way Starlette expects.
#
# Mounting a clean Starlette sub-app sidesteps that whole class of bug:
# the routes inside it route exactly as if they were the top-level app.
#
# Routes:
#   /sse        — Route. The handler hands the underlying ASGI primitives
#                 to _sse_transport.connect_sse which writes the response.
#   /messages/  — Mount, NOT Route. handle_post_message is an ASGI app
#                 with signature (scope, receive, send), not (request),
#                 so it must be mounted, not registered as an endpoint.
mcp_app = Starlette(routes=[
    Route("/sse", mcp_sse_endpoint, methods=["GET"]),
    Mount("/messages/", app=_sse_transport.handle_post_message),
])
