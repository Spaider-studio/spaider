"""
SpAIder as an MCP **server** (Phase 3 of the workflow plan).

This is the opposite direction of the MCP **client** that already lives in
``app/connectors/mcp_connector.py``: that one *pulls* resources from external
MCP servers into SpAIder's graph. *This* module *exposes* SpAIder over MCP so
any MCP client (Claude Code in particular) can use it as durable, agent-scoped
memory.

Architecture
------------
- Mounted under ``/api/v1/mcp`` in ``app/main.py`` (and ``app/mcp_standalone.py``).
- A single **Streamable HTTP** endpoint (the modern MCP transport): clients POST
  JSON-RPC to ``/api/v1/mcp`` and may open a GET stream on the same URL. Served
  by ``StreamableHTTPSessionManager`` in stateless mode.
- Auth is HTTP-layer: every request must carry
  ``Authorization: Bearer <api-key>``. The key resolves to an agent_id via
  ``AuthService.verify_token`` (already supports ``sk-…`` raw API keys).
  The agent_id is then bound to a ``contextvars.ContextVar`` so the per-tool
  handlers can read it without leaking it through the MCP message envelope.
- The session manager's ``run()`` context must be entered in the host app's
  lifespan (done in ``app.main`` and ``app.mcp_standalone``).
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
             "type":    "http",
             "url":     "http://localhost:8000/api/v1/mcp",
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
import json
import logging
from typing import Any, Optional

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from app.services.auth_service import AuthService
from app.services.graph_service import GraphService
from app.services.query_service import QueryService

logger = logging.getLogger(__name__)

# Per-request agent_id binding. Set in the ASGI auth wrapper after the bearer
# token is verified, read inside the tool callbacks. ContextVar (not a global)
# so concurrent requests don't trample each other's identity.
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
        mcp_types.Tool(
            name="spaider.status",
            description=(
                "Read the calling agent's memory configuration (READ-ONLY). "
                "Returns its synaptic memory mode (on/off), autonomous "
                "consolidation cadence + last run, and clearance level. These "
                "settings are governed by a human operator in the Studio; this "
                "tool only reports them and cannot change them."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent]:
    """Dispatch tool calls to the underlying services. Auth was checked in the
    ASGI wrapper (`mcp_app`); here we just read the agent_id contextvar."""
    agent_id = _AGENT_ID.get()
    if agent_id is None:
        raise ValueError("Tool called without authenticated agent_id (auth bug?)")

    if name == "spaider.status":
        graph = _get_graph_service()
        async with graph._driver.session() as session:
            result = await session.run(
                """
                MATCH (a:SystemAgent {agent_id: $aid})
                RETURN coalesce(a.memory_mode, 'on') AS memory_mode,
                       coalesce(a.consolidation_interval_hours, 0) AS interval_hours,
                       toString(a.last_consolidated_at) AS last_consolidated_at,
                       coalesce(a.clearance_level, 1) AS clearance_level
                """,
                aid=agent_id,
            )
            rec = await result.single()
        if rec is None:
            text = f"No SystemAgent record found for agent {agent_id}."
        else:
            hrs = int(rec["interval_hours"])
            cadence = {0: "off", 1: "hourly", 24: "daily", 168: "weekly"}.get(
                hrs, f"every {hrs}h"
            )
            text = (
                f"Agent: {agent_id}\n"
                f"Synaptic memory: {rec['memory_mode']}\n"
                f"Consolidation cadence: {cadence}\n"
                f"Last consolidated: {rec['last_consolidated_at'] or 'never'}\n"
                f"Clearance level: L{int(rec['clearance_level'])}\n"
                "(These settings are managed in the Studio; this tool is read-only.)"
            )
        return [mcp_types.TextContent(type="text", text=text)]

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
# Streamable HTTP transport wired into FastAPI
# ---------------------------------------------------------------------------

# One session manager per process (an SDK requirement — it can't be reused once
# its run() context has closed). `stateless=True` is deliberate: each HTTP
# request is handled independently and the MCP server loop runs *inside* the
# request's task, so the `_AGENT_ID` ContextVar we set just before
# `handle_request` propagates into the tool callbacks (per-agent isolation). A
# stateful manager spawns the session task once and would never see a later
# request's identity. These four tools need no cross-request session, server
# push, or resumability, so stateless is both correct and simpler.
mcp_session_manager = StreamableHTTPSessionManager(
    app=mcp_server,
    event_store=None,
    json_response=False,
    stateless=True,
)


def _bearer_token_from_scope(scope: dict) -> Optional[str]:
    """Pull the raw bearer token out of the ASGI scope headers, or None."""
    for key, value in scope.get("headers", []):
        if key == b"authorization":
            raw = value.decode("latin-1")
            if raw.startswith("Bearer "):
                return raw[len("Bearer "):].strip() or None
            return None
    return None


async def _send_json_401(send, detail: str) -> None:
    """Emit a 401 straight onto the ASGI channel (no Starlette Request needed)."""
    body = json.dumps({"detail": detail}).encode()
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b"Bearer"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def mcp_app(scope, receive, send) -> None:
    """ASGI entrypoint for the Streamable HTTP MCP endpoint.

    Auth happens here: the bearer token is resolved to an agent_id and bound to
    the `_AGENT_ID` ContextVar *before* handing the request to the session
    manager. Because the manager is stateless, the MCP server loop runs in a
    task spawned within this request — inheriting the context — so the tool
    callbacks (which read `_AGENT_ID.get()`) execute in the right agent's
    namespace.

    Mounted at `/api/v1/mcp` by both `app.main` and `app.mcp_standalone`. The
    session manager's `run()` lifespan must be active (entered in those apps'
    lifespans) before any request arrives.
    """
    if scope["type"] != "http":
        # The streamable transport is HTTP-only; let the manager reject anything
        # else (mounted sub-apps never receive the lifespan scope).
        await mcp_session_manager.handle_request(scope, receive, send)
        return

    token = _bearer_token_from_scope(scope)
    if not token:
        await _send_json_401(send, "Missing Bearer token")
        return
    try:
        payload = await _get_auth_service().verify_token(token)
    except Exception as exc:  # noqa: BLE001 — degrade to 401 on any auth error
        await _send_json_401(send, f"Invalid token: {exc}")
        return
    agent_id = payload.get("sub") if isinstance(payload, dict) else None
    if not agent_id:
        await _send_json_401(send, "Token has no agent_id")
        return

    ctx_token = _AGENT_ID.set(agent_id)
    try:
        await mcp_session_manager.handle_request(scope, receive, send)
    finally:
        _AGENT_ID.reset(ctx_token)
