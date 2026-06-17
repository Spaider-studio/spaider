"""
Swarm API endpoints: cross-agent knowledge connections and Swarm Intelligence queries.

Two distinct modes:
  1. Connection management  — create / list / revoke access grants between agents
  2. Swarm Intelligence query — global multi-agent retrieval + LLM synthesis with
     source-node highlighting payload for the 3D frontend
"""
from __future__ import annotations

import asyncio
import json
import logging

# Fort Knox Patch — default clearance for multi-agent searches.
# Mirrors query_service._CLEARANCE_DEFAULT_VALUE: 5 (admin-only) when
# CLEARANCE_DEFAULT_DENY=true, else 1 (public, legacy). Read once at
# module load — the env flag is process-lifetime stable.
import os as _os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.lib.litellm_retry import acompletion_with_retry
from app.models.requests import SwarmConnectionRequest, SwarmQueryRequest
from app.models.responses import APIResponse, SwarmConnectionResponse, SwarmQueryResponse
from app.models.schemas import SwarmConnection
from app.services.auth_service import _check_idor, verify_api_key
from app.services.redis_service import subscribe_to_swarm_logs

_CLEARANCE_DEFAULT_VALUE: int = (
    5 if _os.environ.get("CLEARANCE_DEFAULT_DENY", "false").lower() == "true" else 1
)

logger = logging.getLogger(__name__)

# db.index.vector.queryNodes returns the GLOBAL top-k nearest nodes across every
# agent, with no metadata pre-filtering. If we ask for top_k and only then filter
# by agent_id, a few large tenants crowd the target agents out of the candidate
# set entirely. Overfetch candidate_k = top_k * factor, then post-filter + LIMIT.
# Mirrors graph_service._VECTOR_OVERFETCH_FACTOR (same root cause, same fix).
_SWARM_VECTOR_OVERFETCH: int = 50

# Common stop-words dropped from the text-search fallback so the keyword set is
# dominated by meaningful entity tokens, not question scaffolding.
_TEXT_SEARCH_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "with", "what", "where", "when", "which", "who",
    "does", "did", "that", "this", "from", "into", "are", "was", "were",
    "has", "have", "had", "about", "between", "their", "there",
})

router = APIRouter()

# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------

CONNECTION_KEY = "spaider:swarm:connection:{id}"
CONNECTION_INDEX_KEY = "spaider:swarm:connections"
AGENT_CONNECTIONS_KEY = "spaider:swarm:agent:{agent_id}:connections"

_SWARM_SYSTEM_PROMPT = (
    "Du bist die SpAIder Swarm Intelligence. "
    "Du hast gleichzeitigen Zugriff auf das Wissen mehrerer Agenten-Gehirne im Multiversum. "
    "Synthetisiere aus dem gegebenen Kontext eine präzise, informative Antwort. "
    "Zitiere immer den Agenten, von dem eine Information stammt "
    "(z.B. 'Laut dem Tech-Research-Bot...' oder 'Agent [finance-agent] berichtet...'). "
    "Antworte auf Deutsch wenn die Frage auf Deutsch gestellt wird, sonst auf Englisch. "
    "Wenn der Kontext die Frage nicht beantworten kann, sage das klar."
)

# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_redis_client = None
_graph_service = None
_embedding_service = None


async def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _get_graph():
    global _graph_service
    if _graph_service is None:
        # Reuse main.py's already-initialized singleton so the vector index is
        # ready (vector_index_available=True). Without this, query_nl's
        # vector_search guard trips — the old node-only path masked it by calling
        # db.index.vector.queryNodes directly. Mirrors query.py's accessor.
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


def _get_embedding():
    global _embedding_service
    if _embedding_service is None:
        from app.services.embedding_service import EmbeddingService
        _embedding_service = EmbeddingService()
    return _embedding_service


# ---------------------------------------------------------------------------
# Connection helpers (Redis-backed)
# ---------------------------------------------------------------------------


async def _save_connection(conn: SwarmConnection) -> None:
    redis = await _get_redis()
    key = CONNECTION_KEY.format(id=conn.id)
    await redis.set(key, conn.model_dump_json())
    await redis.sadd(CONNECTION_INDEX_KEY, conn.id)
    await redis.sadd(AGENT_CONNECTIONS_KEY.format(agent_id=conn.source_agent_id), conn.id)
    await redis.sadd(AGENT_CONNECTIONS_KEY.format(agent_id=conn.target_agent_id), conn.id)


async def _load_connection(conn_id: str) -> Optional[SwarmConnection]:
    redis = await _get_redis()
    raw = await redis.get(CONNECTION_KEY.format(id=conn_id))
    if raw is None:
        return None
    return SwarmConnection(**json.loads(raw))


async def _delete_connection(conn_id: str) -> bool:
    conn = await _load_connection(conn_id)
    if conn is None:
        return False
    redis = await _get_redis()
    await redis.delete(CONNECTION_KEY.format(id=conn_id))
    await redis.srem(CONNECTION_INDEX_KEY, conn_id)
    await redis.srem(AGENT_CONNECTIONS_KEY.format(agent_id=conn.source_agent_id), conn_id)
    await redis.srem(AGENT_CONNECTIONS_KEY.format(agent_id=conn.target_agent_id), conn_id)
    return True


async def _get_connections_for_agent(agent_id: str) -> list[SwarmConnection]:
    redis = await _get_redis()
    conn_ids = await redis.smembers(AGENT_CONNECTIONS_KEY.format(agent_id=agent_id))
    connections: list[SwarmConnection] = []
    for cid in conn_ids:
        conn = await _load_connection(cid)
        if conn:
            connections.append(conn)
    return connections


# ---------------------------------------------------------------------------
# Swarm Intelligence — retrieval helpers
# ---------------------------------------------------------------------------


async def _vector_search_multi_agent(
    embedding: list[float],
    agent_ids: Optional[list[str]],
    top_k: int = 20,
    agent_clearance: int = 1,
) -> list[dict]:
    """
    Run vector search across the entire graph (or filtered agents).
    Returns raw dicts: {node_id, label, type, agent_id, properties}.

    Fort Knox Patch (Phase 1, Proposal 2): clearance predicate is now
    enforced unconditionally in the Cypher WHERE clause. Previous
    behavior bypassed Diplomat Protocol entirely — see
    SWARM_SECURITY_MANIFEST.md §1.4 secondary findings. The clearance
    default ($clearance_default) is bound from the module-level
    _CLEARANCE_DEFAULT_VALUE so it stays in lock-step with the rest of
    the system when CLEARANCE_DEFAULT_DENY flips on.

    The filter operates on the integer-valued ``node.clearance_level``
    property (set per-node at ingest, defaulting via coalesce). Agents
    receive only nodes their clearance level dominates.
    """
    graph = _get_graph()
    # Overfetch so the agent post-filter doesn't starve target agents (see
    # _SWARM_VECTOR_OVERFETCH). The ANN traversal stays fully index-backed.
    candidate_k = top_k * _SWARM_VECTOR_OVERFETCH
    try:
        async with graph._driver.session() as session:
            if agent_ids:
                result = await session.run(
                    """
                    CALL db.index.vector.queryNodes('spaider_embedding', $candidate_k, $embedding)
                    YIELD node, score
                    WHERE node.agent_id IN $agent_ids
                      AND node.type <> 'agent_core'
                      AND coalesce(node.clearance_level, $clearance_default) <= $agent_clearance
                    RETURN node.id AS node_id, node.label AS label, node.type AS type,
                           node.agent_id AS agent_id, node.properties AS properties,
                           node.description AS description, score
                    ORDER BY score DESC
                    LIMIT $top_k
                    """,
                    candidate_k=candidate_k,
                    top_k=top_k,
                    embedding=embedding,
                    agent_ids=agent_ids,
                    agent_clearance=agent_clearance,
                    clearance_default=_CLEARANCE_DEFAULT_VALUE,
                )
            else:
                result = await session.run(
                    """
                    CALL db.index.vector.queryNodes('spaider_embedding', $candidate_k, $embedding)
                    YIELD node, score
                    WHERE node.type <> 'agent_core'
                      AND coalesce(node.clearance_level, $clearance_default) <= $agent_clearance
                    RETURN node.id AS node_id, node.label AS label, node.type AS type,
                           node.agent_id AS agent_id, node.properties AS properties,
                           node.description AS description, score
                    ORDER BY score DESC
                    LIMIT $top_k
                    """,
                    candidate_k=candidate_k,
                    top_k=top_k,
                    embedding=embedding,
                    agent_clearance=agent_clearance,
                    clearance_default=_CLEARANCE_DEFAULT_VALUE,
                )
            return await result.data()
    except Exception as exc:
        logger.warning("Swarm vector search failed, will use text fallback: %s", exc)
        return []


async def _text_search_multi_agent(
    query: str,
    agent_ids: Optional[list[str]],
    limit: int = 20,
    agent_clearance: int = 1,
) -> list[dict]:
    """
    Fallback full-text label search across all (or filtered) agents.
    Returns raw dicts: {node_id, label, type, agent_id, properties}.

    Fort Knox Patch: clearance filter now enforced in Cypher — see
    ``_vector_search_multi_agent`` docstring for the rationale.
    """
    graph = _get_graph()
    query_lower = query.lower()
    # Keep meaningful entity tokens: drop stop-words and very short tokens so the
    # keyword set isn't dominated by question scaffolding (where/does/and/...),
    # which previously pushed the actual entities past the cap.
    tokens = [
        w.strip(".,;:!?\"'()")
        for w in query_lower.replace("\n", " ").split()
        if len(w) > 2 and w not in _TEXT_SEARCH_STOPWORDS
    ]
    keywords = [w for w in tokens if w][:10] or [query_lower[:40]]

    async with graph._driver.session() as session:
        if agent_ids:
            result = await session.run(
                """
                MATCH (n:SpaiderNode)
                WHERE n.agent_id IN $agent_ids
                  AND n.type <> 'agent_core'
                  AND coalesce(n.clearance_level, $clearance_default) <= $agent_clearance
                  AND any(kw IN $keywords WHERE toLower(n.label) CONTAINS kw
                         OR toLower(n.type) CONTAINS kw
                         OR toLower(toString(n.properties)) CONTAINS kw)
                RETURN n.id AS node_id, n.label AS label, n.type AS type,
                       n.agent_id AS agent_id, n.properties AS properties,
                       n.description AS description
                LIMIT $limit
                """,
                agent_ids=agent_ids,
                keywords=keywords,
                limit=limit,
                agent_clearance=agent_clearance,
                clearance_default=_CLEARANCE_DEFAULT_VALUE,
            )
        else:
            result = await session.run(
                """
                MATCH (n:SpaiderNode)
                WHERE n.type <> 'agent_core'
                  AND coalesce(n.clearance_level, $clearance_default) <= $agent_clearance
                  AND any(kw IN $keywords WHERE toLower(n.label) CONTAINS kw
                                                    OR toLower(n.type) CONTAINS kw
                                                    OR toLower(toString(n.properties)) CONTAINS kw)
                RETURN n.id AS node_id, n.label AS label, n.type AS type,
                       n.agent_id AS agent_id, n.properties AS properties,
                       n.description AS description
                LIMIT $limit
                """,
                keywords=keywords,
                limit=limit,
                agent_clearance=agent_clearance,
                clearance_default=_CLEARANCE_DEFAULT_VALUE,
            )
        return await result.data()


def _build_swarm_context(records: list[dict]) -> str:
    """
    Build the attributed context string for the LLM.
    Format per line: [Agent: <agent_id>] Weiß: <label> (<type>)[: <description>]
    """
    if not records:
        return "Keine relevanten Fakten im Multiversum gefunden."

    lines: list[str] = []
    for rec in records:
        agent_id = rec.get("agent_id") or "unknown"
        label = rec.get("label") or ""
        node_type = rec.get("type") or ""

        # FACT nodes carry their verbatim ingested text in the top-level
        # `description` column — prefer it. Fall back to a
        # description/summary stored in properties.
        desc = rec.get("description") or ""
        raw_props = rec.get("properties")
        if not desc and raw_props:
            try:
                props = json.loads(raw_props) if isinstance(raw_props, str) else raw_props
                desc = props.get("description", "") or props.get("summary", "") or ""
                if not desc:
                    # Use any string value from properties as context
                    for v in props.values():
                        if isinstance(v, str) and len(v) > 5:
                            desc = v[:200]
                            break
            except (json.JSONDecodeError, AttributeError):
                pass

        line = f"[Agent: {agent_id}] Weiß: {label} ({node_type})"
        if desc:
            line += f": {desc[:300]}"
        lines.append(line)

    return "\n".join(lines)


async def _synthesize_with_llm(query: str, context: str) -> str:
    """Call LiteLLM to synthesise the final Swarm Intelligence answer."""
    call_kwargs: dict = dict(
        model=settings.litellm_model,
        messages=[
            {"role": "system", "content": _SWARM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"=== Swarm-Kontext aus dem Multiversum ===\n{context}\n\n"
                    f"=== Frage ===\n{query}"
                ),
            },
        ],
        temperature=0.2,
        max_tokens=1024,
        request_timeout=60,
    )
    if settings.llm_base_url:
        call_kwargs["api_base"] = settings.llm_base_url
    if settings.llm_api_key:
        call_kwargs["api_key"] = settings.llm_api_key

    response = await acompletion_with_retry(**call_kwargs)
    return response.choices[0].message.content or "Keine Antwort verfügbar."


# ---------------------------------------------------------------------------
# Routes — Connection Management
# ---------------------------------------------------------------------------


@router.post("/connections", response_model=SwarmConnectionResponse, status_code=201)
async def create_connection(
    request: SwarmConnectionRequest,
    auth: dict = Depends(verify_api_key),
):
    """
    Establish a swarm connection granting one agent access to another agent's graph.

    Permissions:
    - read_only: target can query source's graph but not modify it
    - read_write: target can read and write to source's graph

    Scope:
    - full: all node/edge types
    - filtered: restricted to allowed_node_types / allowed_relation_types

    **Auth:** X-Api-Key required when REQUIRE_API_KEY_AUTH=true. The
    authenticated agent must be the source of the connection — only
    the data-owner can grant access to its own graph. IDOR guard
    enforces ``auth.agent_id == request.source_agent_id``.
    """
    _check_idor(auth, request.source_agent_id)
    if request.source_agent_id == request.target_agent_id:
        raise HTTPException(status_code=400, detail="An agent cannot connect to itself.")

    conn = SwarmConnection(
        id=str(uuid.uuid4()),
        source_agent_id=request.source_agent_id,
        target_agent_id=request.target_agent_id,
        permission=request.permission,
        scope=request.scope,
        allowed_node_types=request.allowed_node_types,
        allowed_relation_types=request.allowed_relation_types,
        created_at=datetime.now(timezone.utc),
    )

    try:
        await _save_connection(conn)
    except Exception as exc:
        logger.exception("Failed to create swarm connection: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info(
        "Swarm connection %s created: %s -> %s (%s)",
        conn.id,
        conn.source_agent_id,
        conn.target_agent_id,
        conn.permission,
    )
    return SwarmConnectionResponse(success=True, connection=conn)


@router.get("/connections")
async def list_connections(
    agent_id: str = Query(..., description="Agent ID to list connections for"),
    auth: dict = Depends(verify_api_key),
):
    """List all swarm connections for a given agent (as source or target).

    **Auth:** Caller must be the agent whose connections are being listed
    (IDOR guard). When the auth flag is off, behavior is unchanged.
    """
    _check_idor(auth, agent_id)
    try:
        connections = await _get_connections_for_agent(agent_id)
        return {
            "connections": [c.model_dump(mode="json") for c in connections],
            "total": len(connections),
        }
    except Exception as exc:
        logger.exception("Error listing connections for agent %s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/connections/{connection_id}", response_model=APIResponse)
async def revoke_connection(
    connection_id: str,
    auth: dict = Depends(verify_api_key),  # noqa: ARG001 — connection-owner IDOR is Phase 2 work
):
    """Revoke (delete) a swarm connection by ID.

    **Auth:** Requires X-Api-Key. Per-connection IDOR (verifying the
    caller is the source of the connection being revoked) requires a
    Redis lookup and is deferred to Phase 2 — for now, any authenticated
    caller can revoke any connection. This is intentional scope for
    Phase 1; tracked in the manifest's Phase 2 follow-ups.
    """
    try:
        deleted = await _delete_connection(connection_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Connection '{connection_id}' not found")

    logger.info("Swarm connection %s revoked", connection_id)
    return APIResponse(success=True, message=f"Connection '{connection_id}' revoked.")


# ---------------------------------------------------------------------------
# Route — Swarm Intelligence Query
# ---------------------------------------------------------------------------


_NO_FACTS_MSG = (
    "Im Multiversum wurden keine relevanten Fakten für diese Frage gefunden. "
    "Bitte stelle sicher, dass Agenten Wissen ingested haben."
)


@router.post("/query", response_model=SwarmQueryResponse)
async def swarm_query(
    request: SwarmQueryRequest,
    auth: dict = Depends(verify_api_key),
):
    """
    Swarm Intelligence: federated multi-agent query with LLM synthesis.

    Two retrieval strategies:
      * Explicit ``agent_ids`` → federated **deep** query. Run the full
        single-agent graph pipeline (vector seeds + 1-hop traversal + verify)
        per agent via ``QueryService.query_nl``, then synthesise one answer from
        the per-agent, graph-grounded answers. This captures relational facts
        that live in *edges* (e.g. A —TWINNED_WITH→ B), which a node-only scan
        cannot see.
      * No ``agent_ids`` → broad **multiverse scan**: a single cross-agent
        vector search (breadth over depth, no per-agent agentic loop).

    **Auth & access control (Fort Knox Patch):**
    When ``REQUIRE_API_KEY_AUTH=true``:
      - X-Api-Key required
      - ``request.agent_ids=None`` (legacy "query all") is RESTRICTED to
        the caller's own agent_id only — closes the global cross-tenant
        search hole documented in SWARM_SECURITY_MANIFEST.md §1.4.
      - Any explicit ``agent_ids`` must contain only the caller's own
        agent_id. Cross-agent federation via ``:SwarmConnection`` /
        ``SHARES_KNOWLEDGE_WITH`` is Phase 2 work (manifest §3).
      - Clearance filter is applied to every retrieved node using the
        caller's clearance level (resolved from the authenticated
        agent record).

    When the flag is off, behavior is unchanged for backwards compat.
    """
    # --- Auth-aware scope resolution ---------------------------------
    auth_agent_id: Optional[str] = (
        None if auth.get("auth_bypassed") else auth.get("agent_id")
    )
    requested_agent_ids = request.agent_ids if request.agent_ids else None

    if auth_agent_id is not None:
        # Default empty → caller only. Closes the global-search hole.
        if requested_agent_ids is None:
            requested_agent_ids = [auth_agent_id]
        else:
            # Phase 1 strict mode: caller can only query themselves.
            illegal = [aid for aid in requested_agent_ids if aid != auth_agent_id]
            if illegal:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Authenticated agent '{auth_agent_id}' cannot query "
                        f"agents {illegal}. Cross-agent federation is "
                        "Phase 2 work."
                    ),
                )

    # Resolve caller's clearance level. Falls back to 1 (legacy public)
    # when auth is bypassed OR when the auth record lacks clearance_level.
    agent_clearance: int = int(auth.get("clearance_level") or 1)
    agent_ids = requested_agent_ids

    # Dispatch on the RESOLVED scope (not raw request.agent_ids) so the
    # auth restriction above cannot be bypassed by the retrieval split.
    query = request.query.strip()

    # Observability: mirror the single-agent /query path so swarm queries also
    # land in the ClickHouse-backed Audit Log and analytics. Reuse query.py's
    # event helper (handles query_received/answered/failed). The events are
    # tagged mode="swarm" so the Audit Log can distinguish them; the federated
    # path additionally logs per-agent query events underneath this one.
    import time as _time

    from app.api.v1.query import _fire_replay_event  # late import: avoid import cycle

    workflow_id = str(uuid.uuid4())
    obs_agent_id = auth_agent_id or (agent_ids[0] if agent_ids else "swarm")
    t0 = _time.perf_counter()
    _fire_replay_event(
        workflow_id, obs_agent_id, "query_received",
        {"question_length": len(query), "mode": "swarm", "agent_ids": agent_ids or []},
    )
    try:
        if agent_ids:
            resp = await _federated_deep_query(query, agent_ids)
        else:
            resp = await _broad_vector_scan(query, agent_clearance=agent_clearance)
    except Exception:
        _fire_replay_event(
            workflow_id, obs_agent_id, "query_failed",
            {"question_length": len(query), "mode": "swarm"},
        )
        raise

    latency_ms = (_time.perf_counter() - t0) * 1000
    try:
        import app.main as _main
        svc = getattr(_main, "_analytics_service", None)
        if svc and svc._ready:
            svc.record_query(
                agent_id=obs_agent_id,
                question_length=len(query),
                answer_length=len(resp.answer or ""),
                nodes_in_result=len(resp.source_node_ids),
                edges_in_result=0,
                cypher_used=False,
                latency_ms=round(latency_ms, 2),
            )
    except Exception:
        pass

    _fire_replay_event(
        workflow_id, obs_agent_id, "query_answered",
        {
            "question_length": len(query),
            "answer_length": len(resp.answer or ""),
            "answer": (resp.answer or "")[:2000],
            "nodes_in_result": len(resp.source_node_ids),
            "edges_in_result": 0,
            "latency_ms": round(latency_ms, 2),
            "mode": "swarm",
            "agents_involved": resp.agents_involved,
        },
    )
    return resp


async def _federated_deep_query(query: str, agent_ids: list[str]) -> SwarmQueryResponse:
    """Federate across explicit agents by reusing the graph-aware single-agent
    pipeline per agent, then synthesising one answer from the grounded results.

    Each agent's ``query_nl`` traverses its own graph (so edge-borne facts are
    captured) and returns an answer + subgraph; we keep the agents whose graph
    actually produced nodes, attribute their answers, and run one merge pass.
    """
    from app.services.query_service import QueryService

    qs = QueryService(graph_service=_get_graph())

    async def _one(aid: str):
        try:
            # Decompose: a federated question often spans agents, so each agent
            # must retrieve on its own sub-entities (the global default stays off
            # for single-agent queries / benchmarks).
            return aid, await qs.query_nl(question=query, agent_id=aid, decompose=True)
        except Exception as exc:
            logger.warning("swarm federated: query_nl failed for agent %s: %s", aid, exc)
            return aid, None

    pairs = await asyncio.gather(*[_one(a) for a in agent_ids])
    grounded = [
        (aid, res)
        for aid, res in pairs
        if res is not None and res.subgraph and res.subgraph.nodes
    ]
    if not grounded:
        return SwarmQueryResponse(answer=_NO_FACTS_MSG, source_node_ids=[], agents_involved=[])

    # Each per-agent answer is already grounded in that agent's full graph;
    # synthesise a single attributed answer over them.
    context = "\n".join(f"[Agent: {aid}] Antwort: {res.answer}" for aid, res in grounded)
    try:
        answer = await _synthesize_with_llm(query, context)
    except Exception as exc:
        logger.exception("Swarm federated synthesis failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"LLM synthesis failed: {exc}")

    source_node_ids = list(
        {n.id for _, res in grounded for n in res.subgraph.nodes if n.id}
    )
    agents_involved = [aid for aid, _ in grounded]
    logger.info(
        "Swarm federated query | agents=%s sources=%d",
        agents_involved, len(source_node_ids),
    )
    return SwarmQueryResponse(
        answer=answer, source_node_ids=source_node_ids, agents_involved=agents_involved,
    )


async def _broad_vector_scan(query: str, agent_clearance: int = 1) -> SwarmQueryResponse:
    """Cross-agent vector scan over the whole multiverse (no agent filter).

    Only reached when auth is bypassed (flag off) or the caller resolved to no
    explicit scope; clearance still gates which nodes are visible.
    """
    records: list[dict] = []
    try:
        embedding_svc = _get_embedding()
        q_embedding = await embedding_svc.embed(query)
        records = await _vector_search_multi_agent(
            q_embedding, None, top_k=20, agent_clearance=agent_clearance,
        )
        logger.info("Swarm vector search returned %d records", len(records))
    except Exception as exc:
        logger.warning("Swarm vector search failed: %s", exc)

    # Text-search fallback / supplement when vector search is thin.
    if len(records) < 5:
        try:
            text_records = await _text_search_multi_agent(
                query, None, limit=20, agent_clearance=agent_clearance,
            )
            # Merge, deduplicate by node_id
            existing_ids = {r["node_id"] for r in records}
            for rec in text_records:
                if rec["node_id"] not in existing_ids:
                    records.append(rec)
                    existing_ids.add(rec["node_id"])
            logger.info("After text search supplement: %d total records", len(records))
        except Exception as exc:
            logger.warning("Swarm text search failed: %s", exc)

    if not records:
        return SwarmQueryResponse(answer=_NO_FACTS_MSG, source_node_ids=[], agents_involved=[])

    context = _build_swarm_context(records[:25])  # cap at 25 facts for token budget
    try:
        answer = await _synthesize_with_llm(query, context)
    except Exception as exc:
        logger.exception("Swarm LLM synthesis failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"LLM synthesis failed: {exc}")

    source_node_ids = list({r["node_id"] for r in records if r.get("node_id")})
    agents_involved = list({r["agent_id"] for r in records if r.get("agent_id")})
    logger.info(
        "Swarm broad scan complete | sources=%d agents=%s",
        len(source_node_ids), agents_involved,
    )
    return SwarmQueryResponse(
        answer=answer, source_node_ids=source_node_ids, agents_involved=agents_involved,
    )


# ---------------------------------------------------------------------------
# Route — Swarm Pulse: live worker health discovery
# ---------------------------------------------------------------------------

# Redis key prefix written by every swarm_listener worker heartbeat.
# Full key schema: agent_status:{agent_id}   value: "online"   TTL: 15 s
_HEARTBEAT_KEY_PREFIX = "agent_status:*"

# SSE heartbeat cadence (seconds). The browser's EventSource and any intervening
# proxy (Kong/Nginx) drop an idle connection after their read timeout, so we MUST
# emit something on a quiet channel. 15s sits comfortably under typical 30–60s
# proxy idle windows. Module-level so it's tunable / patchable in tests.
_HEARTBEAT_S = 15.0


@router.get("/health", summary="Live worker presence — Swarm Pulse")
async def swarm_health():
    """
    Aggregate all live swarm worker heartbeats into a single health snapshot.

    Discovery mechanism
    -------------------
    Each ``swarm_listener`` worker refreshes a Redis key
    ``agent_status:{agent_id}`` every 10 s with a TTL of 15 s
    (see ``_heartbeat_loop`` in ``workers/swarm_listener.py``).

    This endpoint scans for all keys matching ``agent_status:*`` and
    returns the agent IDs of workers that are currently online.  A worker
    that has stopped refreshing its key is automatically absent from the
    list within ≤15 s — no explicit de-registration required.

    SCAN vs KEYS
    ------------
    ``redis.scan_iter()`` uses Redis' cursor-based SCAN under the hood.
    Unlike ``KEYS *``, it never blocks the Redis event loop for longer
    than O(1) per iteration, making it safe for production key-spaces
    with tens of thousands of entries.

    Response
    --------
    ``200 OK``::

        {
            "active_agents": ["swarm_worker_3f9a1c02", "swarm_worker_a81b4d77"],
            "total": 2
        }

    An empty ``active_agents`` list means no workers are currently running.
    """
    redis = await _get_redis()

    active_agents: list[str] = []
    try:
        async for raw_key in redis.scan_iter(match=_HEARTBEAT_KEY_PREFIX):
            # raw_key is already decoded (decode_responses=True on the client).
            # Strip the "agent_status:" prefix to get the bare agent_id.
            agent_id = raw_key.removeprefix("agent_status:")
            active_agents.append(agent_id)
    except Exception as exc:
        logger.error("SwarmHealth | Redis scan failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Redis unavailable — cannot determine worker health: {exc}",
        )

    active_agents.sort()   # deterministic ordering for dashboard diffing

    logger.info(
        "SwarmHealth | scan complete — %d active worker(s): %s",
        len(active_agents), active_agents or "none",
    )

    return {
        "active_agents": active_agents,
        "total": len(active_agents),
    }


# ---------------------------------------------------------------------------
# Route — Swarm Live-Log: Server-Sent Events stream
# ---------------------------------------------------------------------------


@router.get(
    "/events/stream",
    summary="Swarm Live-Log — Server-Sent Events",
    response_class=StreamingResponse,
)
async def swarm_events_stream(request: Request) -> StreamingResponse:
    """
    Stream live swarm worker activity as Server-Sent Events (SSE).

    Architecture
    ------------
    Worker events are published to the Redis Pub/Sub channel
    ``swarm_log_channel`` by ``publish_swarm_log()`` throughout the
    swarm pipeline (pheromone stamp, lease claim, specialist dispatch,
    XACK, etc.).  This endpoint subscribes to that channel and fans
    the events out to every connected browser tab simultaneously.

    Why Pub/Sub, not the pheromone Redis Stream
    --------------------------------------------
    The existing ``pheromone_stream`` uses Consumer Groups — each
    message is delivered to exactly ONE worker.  Multiple browser tabs
    subscribing to it would steal messages from each other.  Pub/Sub
    broadcasts every message to ALL subscribers, making it the correct
    primitive for live-log fan-out.

    Zombie-connection prevention
    ----------------------------
    ``await request.is_disconnected()`` is checked on every iteration.
    When the user closes the browser tab, FastAPI raises a disconnect
    signal; the loop breaks, the generator is garbage-collected, and
    the Pub/Sub subscriber is cleaned up via its ``finally`` block.
    Without this check, a closed connection would keep a coroutine and
    a Redis Pub/Sub slot open indefinitely — a memory and connection
    leak under sustained traffic.

    SSE format (per W3C EventSource spec)
    --------------------------------------
    Each event frame::

        data: {"type":"pheromone","node_id":"abc123","message":"Node stamped"}\\n\\n

    The double newline (``\\n\\n``) is the SSE frame delimiter that
    triggers the browser's ``EventSource.onmessage`` callback.

    Response headers
    ----------------
    ``Cache-Control: no-cache``  — prevents proxies from buffering the stream.
    ``Connection: keep-alive``   — keeps the TCP connection open for the
                                   lifetime of the subscription.
    ``X-Accel-Buffering: no``    — disables Nginx proxy buffering when the
                                   backend sits behind an Nginx reverse proxy.

    Client usage (JavaScript)
    -------------------------
    ::

        const es = new EventSource("/api/v1/swarm/events/stream");
        es.onmessage = (e) => {
            const event = JSON.parse(e.data);
            console.log(event.type, event.agent, event.message);
        };
        // Close when done to release the server-side subscription:
        es.close();

    Event schema (JSON fields in every frame)
    ------------------------------------------
    .. code-block:: json

        {
            "type":      "pheromone | lease | dispatch | ack | error | heartbeat",
            "agent":     "<worker_id or specialist_name>",
            "message":   "<human-readable log line>",
            "timestamp": "<ISO-8601 UTC>",
            "node_id":   "<optional>",
            "session_id":"<optional>"
        }
    """
    redis = await _get_redis()

    def _heartbeat_frame(message: str) -> str:
        return "data: " + json.dumps(
            {"type": "heartbeat", "message": message}
        ) + "\n\n"

    async def _event_generator():
        """
        Inner async generator: subscribe → yield SSE frames → cleanup.

        Separated from the route handler so that StreamingResponse owns
        the generator lifetime and calls aclose() when the response ends,
        which in turn triggers subscribe_to_swarm_logs()'s finally block.

        Heartbeats
        ----------
        We race each Pub/Sub read against a ``_HEARTBEAT_S`` timeout. On a
        quiet channel the read times out and we emit a ``heartbeat`` frame
        instead of blocking forever — this is what keeps the proxy/browser
        from tearing down an idle stream (the old "always disconnected" bug).
        The frontend treats ``type:"heartbeat"`` as keep-alive, not a log row.
        """
        logger.info(
            "SwarmSSE | client connected from %s",
            request.client.host if request.client else "unknown",
        )

        # Immediate connect frame so EventSource.onopen fires reliably even
        # before the first real event — without this the panel can sit in
        # "Connecting…" until the first pheromone, reading as "disconnected".
        yield _heartbeat_frame("connected")

        # If Redis is unavailable we can't subscribe, but we still keep the
        # stream alive with heartbeats rather than 500-ing — the panel shows
        # "connected" (degraded) instead of flapping in a reconnect loop.
        if redis is None:
            logger.warning("SwarmSSE | Redis unavailable — heartbeat-only stream")
            try:
                while not await request.is_disconnected():
                    await asyncio.sleep(_HEARTBEAT_S)
                    yield _heartbeat_frame("keep-alive")
            except asyncio.CancelledError:
                raise
            finally:
                logger.info("SwarmSSE | heartbeat-only generator exited")
            return

        agen = subscribe_to_swarm_logs(redis)
        # One persistent read task that SURVIVES heartbeat timeouts. We race it
        # against the heartbeat interval with asyncio.wait (which leaves the task
        # PENDING on timeout) rather than asyncio.wait_for (which CANCELS it).
        # Cancelling the read throws CancelledError into subscribe_to_swarm_logs()
        # and runs its finally — closing the Pub/Sub subscription and tearing the
        # whole stream down every _HEARTBEAT_S seconds on a quiet channel, which
        # the browser sees as a flickering connect/disconnect loop.
        read_task: asyncio.Task = asyncio.ensure_future(agen.__anext__())
        try:
            while True:
                # ── Zombie-connection guard ────────────────────────────────
                # request.is_disconnected() is a coroutine that queries the
                # ASGI receive channel without blocking the event loop.
                if await request.is_disconnected():
                    logger.info("SwarmSSE | client disconnected — closing subscription")
                    break

                # ── Race the read against the heartbeat WITHOUT cancelling it ─
                done, _pending = await asyncio.wait({read_task}, timeout=_HEARTBEAT_S)
                if read_task not in done:
                    # Quiet channel — emit a keep-alive; the read stays pending.
                    yield _heartbeat_frame("keep-alive")
                    continue

                try:
                    raw_json = read_task.result()
                except StopAsyncIteration:
                    break

                # ── SSE frame (W3C format) ─────────────────────────────────
                # The raw_json string already contains no trailing newlines
                # (JSON serialiser never adds them).
                yield f"data: {raw_json}\n\n"
                # Start the next read; only one read is ever in flight.
                read_task = asyncio.ensure_future(agen.__anext__())

        except asyncio.CancelledError:
            # Server is shutting down — exit cleanly without logging an error.
            logger.info("SwarmSSE | generator cancelled (server shutdown)")
            raise

        except Exception as exc:
            # Unexpected error — log and close; don't crash the whole worker.
            logger.error("SwarmSSE | unexpected error in event generator: %s", exc)

        finally:
            # Cancel the in-flight read and let it settle before aclose(), so the
            # subscription is unwound exactly once without an "already running"
            # race on the async generator.
            if not read_task.done():
                read_task.cancel()
                try:
                    await read_task
                except BaseException:
                    pass
            # aclose() drives subscribe_to_swarm_logs()'s finally block
            # (unsubscribe + aclose on the Pub/Sub connection).
            await agen.aclose()
            logger.info("SwarmSSE | event generator exited")

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "Connection":       "keep-alive",
            # Nginx-specific: disable proxy buffering so events are
            # forwarded to the client immediately instead of being held
            # until the buffer fills.
            "X-Accel-Buffering": "no",
        },
    )
