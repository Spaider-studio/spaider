"""
Query API endpoints: natural-language queries, direct Cypher, and graph traversal.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.models.requests import CypherQueryRequest, QueryRequest, TraverseRequest
from app.models.schemas import GraphPayload, QueryResult
from app.services.auth_service import _check_idor, verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Blocked Cypher write keywords (READ-ONLY enforcement)
# ---------------------------------------------------------------------------

_WRITE_PATTERN = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH\s+DELETE|SET|REMOVE|DROP|CALL\s+apoc\..*write)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_query_service = None
_graph_service = None


def _get_graph_service():
    global _graph_service
    if _graph_service is None:
        # Reuse main.py's already-initialized singleton (vector index ready).
        try:
            import app.main as _main
            svc = getattr(_main, "_graph_service", None)
            if svc is not None:
                _graph_service = svc
                return _graph_service
        except Exception:
            pass
        from app.services.graph_service import GraphService
        _graph_service = GraphService()
    return _graph_service


def _get_query_service():
    global _query_service
    if _query_service is None:
        try:
            from app.services.query_service import QueryService
            _query_service = QueryService(graph_service=_get_graph_service())
        except Exception as exc:
            logger.warning("QueryService init failed: %s", exc)
            _query_service = None
    return _query_service


def _fire_replay_event(
    workflow_id: str,
    agent_id: str,
    event_type: str,
    payload: dict,
) -> None:
    """Fire-and-forget: emit one replay event. Never raises."""
    def _default_trace_summary(event: str, data: dict) -> str:
        et = event.lower()
        if et == "query_received":
            return (
                "Question was accepted for GraphRAG processing: retrieve relevant graph context, "
                "then synthesize an answer constrained by retrieved facts."
            )
        if et == "query_answered":
            return (
                "Query finished: answer was generated from retrieved graph context; metadata "
                "contains result size and latency diagnostics."
            )
        if et == "query_failed":
            return "Query processing failed; inspect metadata/error fields for failure details."
        return "Replay audit event captured for deterministic workflow reconstruction."

    async def _emit() -> None:
        try:
            from app.api.v1.replay import get_replay_service
            svc = await get_replay_service()
            if svc is None:
                return
            event_payload = dict(payload or {})
            if not str(event_payload.get("trace_summary", "")).strip():
                event_payload["trace_summary"] = _default_trace_summary(event_type, event_payload)
            await svc.record_event(
                workflow_id=workflow_id,
                agent_id=agent_id,
                event_type=event_type,
                payload=event_payload,
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("Replay event skipped (%s): %s", event_type, exc)

    try:
        asyncio.get_running_loop().create_task(_emit())
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=QueryResult)
async def query_nl(
    request: QueryRequest,
    background_tasks: BackgroundTasks,
    auth: dict = Depends(verify_api_key),
):
    """
    Natural-language query over the knowledge graph.
    Translates the question to Cypher using an LLM, executes it, and synthesises
    a human-readable answer with supporting subgraph context.

    **Auth:** Requires a valid API key via ``X-Api-Key`` header (or
    ``Authorization: Bearer``) when ``REQUIRE_API_KEY_AUTH=true``. The
    authenticated agent_id must match ``request.agent_id`` (IDOR guard).
    When the flag is off, both checks are bypassed for backwards compat.

    **Episodic Memory:** When the agent has ``interaction_memory=True``, the
    query/response pair is recorded asynchronously as an ``InteractionNode``
    in Neo4j (fire-and-forget via BackgroundTasks — zero added latency).
    """
    _check_idor(auth, request.agent_id)
    query_service = _get_query_service()
    if query_service is None:
        raise HTTPException(status_code=503, detail="QueryService unavailable")

    import time as _time
    t0 = _time.perf_counter()
    workflow_id = str(uuid.uuid4())

    # Replay: record query start
    _fire_replay_event(
        workflow_id, request.agent_id, "query_received",
        {"question_length": len(request.question)},
    )

    try:
        # Run the query and the interaction-memory flag check concurrently.
        # get_agent_interaction_memory() is a fast Redis read that completes
        # during the LLM call, adding zero observable latency.
        svc_result, interaction_memory = await asyncio.gather(
            query_service.query_nl(
                question=request.question,
                agent_id=request.agent_id,
                top_k=request.top_k,
            ),
            query_service.get_agent_interaction_memory(request.agent_id),
        )
        latency_ms = (_time.perf_counter() - t0) * 1000

        # Fire-and-forget episodic memory write — BackgroundTasks runs after
        # the response is sent, so the caller sees zero additional latency.
        if interaction_memory:
            session_id = request.session_id or str(uuid.uuid4())
            source_node_ids = [n.id for n in svc_result.subgraph.nodes]
            background_tasks.add_task(
                _get_graph_service().record_interaction,
                agent_id=request.agent_id,
                session_id=session_id,
                question=request.question,
                answer_summary=svc_result.answer or "",
                source_node_ids=source_node_ids,
            )

        # Backend token usage from query_nl. Coerce to a plain dict-or-None
        # so the response model never sees a non-dict (e.g. a test MagicMock
        # auto-attribute) — only a real {"prompt_tokens", "completion_tokens"}
        # dict or None (cache hit) flows downstream.
        _raw_tok = getattr(svc_result, "token_usage", None)
        token_usage = _raw_tok if isinstance(_raw_tok, dict) else None

        # Fire-and-forget analytics
        try:
            import app.main as _main
            svc = getattr(_main, "_analytics_service", None)
            if svc and svc._ready:
                _tok = token_usage or {}
                svc.record_query(
                    agent_id=request.agent_id,
                    question_length=len(request.question),
                    answer_length=len(svc_result.answer or ""),
                    nodes_in_result=len(svc_result.subgraph.nodes),
                    edges_in_result=len(svc_result.subgraph.edges),
                    cypher_used=bool(svc_result.cypher),
                    latency_ms=round(latency_ms, 2),
                    prompt_tokens=int(_tok.get("prompt_tokens", 0)),
                    completion_tokens=int(_tok.get("completion_tokens", 0)),
                )
        except Exception:
            pass

        # Replay: record query answer
        _fire_replay_event(
            workflow_id, request.agent_id, "query_answered",
            {
                "question_length": len(request.question),
                "answer_length": len(svc_result.answer or ""),
                "answer": (svc_result.answer or "")[:2000],
                "nodes_in_result": len(svc_result.subgraph.nodes),
                "edges_in_result": len(svc_result.subgraph.edges),
                "latency_ms": round(latency_ms, 2),
            },
        )

        return QueryResult(
            question=svc_result.question,
            answer=svc_result.answer,
            subgraph=svc_result.subgraph,
            cypher_used=svc_result.cypher,
            iterations_used=getattr(svc_result, "iterations_used", 1),
            re_query_happened=getattr(svc_result, "re_query_happened", False),
            confidence_score=getattr(svc_result, "confidence_score", 1.0),
            verifier_feedback=getattr(svc_result, "verifier_feedback", None),
            token_usage=token_usage,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Error processing NL query: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/cypher", response_model=list[dict])
async def query_cypher(
    request: CypherQueryRequest,
    auth: dict = Depends(verify_api_key),
):
    """
    Execute a raw Cypher query against the graph.
    Only READ operations are permitted; write keywords are rejected.
    Results are filtered to the requesting agent's namespace.

    **Auth:** Same X-Api-Key requirement as POST /query. IDOR-guards
    ``request.agent_id`` against the authenticated identity. Note that
    even with auth on, this endpoint exposes raw Cypher — the manifest
    flags it for server-side parsing or removal in a follow-up patch.
    """
    _check_idor(auth, request.agent_id)
    # Enforce read-only
    if _WRITE_PATTERN.search(request.cypher):
        raise HTTPException(
            status_code=403,
            detail="Write operations (CREATE, MERGE, DELETE, SET, REMOVE) are not permitted via this endpoint.",
        )

    query_service = _get_query_service()
    if query_service is None:
        raise HTTPException(status_code=503, detail="QueryService unavailable")

    try:
        records = await query_service.query_cypher(
            cypher=request.cypher,
            agent_id=request.agent_id,
        )
        return records
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        logger.error("Cypher query error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/traverse", response_model=GraphPayload)
async def traverse(
    request: TraverseRequest,
    auth: dict = Depends(verify_api_key),  # noqa: ARG001 — IDOR on start_node_id is Phase 2 work
):
    """
    Traverse the graph from a start node up to the requested depth.
    Optionally filter by relationship type.

    **Auth:** Requires X-Api-Key when REQUIRE_API_KEY_AUTH=true.
    TraverseRequest has no agent_id field — IDOR on start_node_id
    (verifying the start node belongs to the authenticated agent)
    requires a graph lookup and is deferred to Phase 2 per the manifest.
    """
    query_service = _get_query_service()
    if query_service is None:
        raise HTTPException(status_code=503, detail="QueryService unavailable")

    try:
        subgraph: GraphPayload = await query_service.traverse(
            start_node_id=request.start_node_id,
            depth=request.depth,
            relation_filter=request.relation_filter,
        )
        return subgraph
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Traversal error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/stream")
async def stream_query(
    request: QueryRequest,
    auth: dict = Depends(verify_api_key),
):
    """
    Streaming natural-language query via Server-Sent Events.
    Tokens arrive as they're generated — no waiting for the full response.

    **Auth:** Same as POST /query — X-Api-Key + IDOR on request.agent_id.

    SSE format:
        data: <token>\\n\\n
        data: [DONE]\\n\\n
    """
    _check_idor(auth, request.agent_id)
    query_service = _get_query_service()
    if query_service is None:
        raise HTTPException(status_code=503, detail="QueryService unavailable")

    async def event_stream():
        try:
            async for token in query_service.stream_query_nl(
                question=request.question,
                agent_id=request.agent_id,
                top_k=request.top_k,
            ):
                yield f"data: {token}\n\n"
        except Exception as exc:
            logger.exception("Streaming query error: %s", exc)
            yield f"data: [ERROR] {exc}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
