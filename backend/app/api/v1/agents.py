"""
Agents API endpoints: create and manage agent namespaces with API keys.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.models.requests import AgentConnectRequest, AgentCreateRequest
from app.models.responses import (
    AgentBridgeResponse,
    AgentImportResponse,
    AgentListResponse,
    AgentResponse,
    APIResponse,
    DeleteInteractionsResponse,
    RotateKeyResponse,
    SwarmLinkDeleteResponse,
    SwarmLinkResponse,
)
from app.models.schemas import Agent, Edge, GraphPayload, Node

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------

AGENT_KEY = "spaider:agent:{id}"
AGENT_INDEX_KEY = "spaider:agents"


# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_redis_client = None
_graph_service = None
_auth_service = None


async def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis

        from app.config import settings
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _get_graph_service():
    global _graph_service
    if _graph_service is None:
        from app.services.graph_service import GraphService
        _graph_service = GraphService()
    return _graph_service


def _get_auth_service():
    global _auth_service
    if _auth_service is None:
        from app.services.auth_service import AuthService
        _auth_service = AuthService()
    return _auth_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _save_agent(agent: Agent) -> None:
    redis = await _get_redis()
    key = AGENT_KEY.format(id=agent.id)
    # Never persist the transient raw `api_key` field (only ever set on the
    # creation response); the record keeps `api_key_hash` instead.
    data = agent.model_dump(mode="json", exclude={"api_key"})
    await redis.set(key, json.dumps(data))
    await redis.sadd(AGENT_INDEX_KEY, agent.id)


async def _load_agent(agent_id: str) -> Optional[Agent]:
    redis = await _get_redis()
    raw = await redis.get(AGENT_KEY.format(id=agent_id))
    if raw is None:
        return None
    return Agent(**json.loads(raw))


async def _delete_agent(agent_id: str) -> bool:
    redis = await _get_redis()
    key = AGENT_KEY.format(id=agent_id)
    deleted = await redis.delete(key)
    await redis.srem(AGENT_INDEX_KEY, agent_id)
    return deleted > 0


# ---------------------------------------------------------------------------
# NDJSON streaming export generator
# ---------------------------------------------------------------------------

# Bolt fetch size: the neo4j Python driver requests records from the server
# in batches of this size.  Python only holds one record in scope per iteration;
# the driver's internal buffer holds at most FETCH_SIZE records at a time.
# This is entirely separate from the NDJSON lines streamed to the HTTP client.
_BOLT_FETCH_SIZE = 1000

# Import micro-batch tuning.
# _IMPORT_CHUNK_SIZE: bytes read per async file.read() call.
#     64 KB balances I/O syscall overhead vs. per-chunk memory footprint.
# _IMPORT_BATCH_SIZE: items accumulated before a write_graph_batch() flush.
#     500 nodes/edges per Neo4j transaction keeps transactions short enough to
#     avoid lock-timeout issues while amortising round-trip overhead well.
_IMPORT_CHUNK_SIZE = 65_536   # 64 KB
_IMPORT_BATCH_SIZE = 500


async def _ndjson_export_generator(
    agent_id: str,
    agent: Agent,
    include_embeddings: bool,
) -> AsyncGenerator[str, None]:
    """
    Async generator — yields NDJSON lines for a complete agent graph export.

    Memory guarantee
    ----------------
    The Neo4j Bolt driver fetches records in batches of ``_BOLT_FETCH_SIZE``
    from the server cursor.  The Python generator holds exactly ONE record in
    scope per ``async for`` iteration — the entire graph is never materialised
    in Python memory regardless of graph size.

    Line envelope
    -------------
    Every line is a self-describing JSON object::

        {"type": "<kind>", "data": {...}}\\n

    Kinds emitted (in order):
        metadata     — one line; agent provenance + export parameters
        node         — one line per SpaiderNode
        edge         — one line per RELATION edge
        interaction  — one line per InteractionNode (episodic memory)
        informed_by  — one line per INFORMED_BY edge pair

    Embedding projection
    --------------------
    When ``include_embeddings=False`` the ``embedding`` column is omitted from
    the Cypher RETURN clause entirely.  Neo4j therefore never reads the vector
    property from storage, avoiding 1536 × 4 bytes = 6 KB of wire traffic per
    node — critical for graphs with 100 k+ nodes.
    """

    # ── 1. Metadata line — no DB I/O ─────────────────────────────────────
    yield json.dumps(
        {
            "type": "metadata",
            "data": {
                "format_version":     "1.0",
                "agent_id":           agent.id,
                "agent_name":         agent.name,
                "agent_description":  agent.description,
                "tenant_id":          agent.tenant_id,
                "permissions":        agent.permissions,
                "clearance_level":    agent.clearance_level,
                "interaction_memory": agent.interaction_memory,
                "created_at":         agent.created_at.isoformat() if agent.created_at else None,
                "exported_at":        datetime.now(timezone.utc).isoformat(),
                "include_embeddings": include_embeddings,
            },
        },
        ensure_ascii=False,
    ) + "\n"

    graph = _get_graph_service()

    # ── 2. SpaiderNodes — Bolt cursor, one record at a time ──────────────
    # Two separate query strings: the include_embeddings=False path does NOT
    # mention n.embedding in the RETURN clause so Neo4j skips the property
    # from storage entirely (not just filtering on the wire).
    if include_embeddings:
        _node_cypher = """
            MATCH (n:SpaiderNode {agent_id: $agent_id})
            RETURN n.id                           AS id,
                   n.label                        AS label,
                   n.type                         AS type,
                   n.properties                   AS properties,
                   n.embedding                    AS embedding,
                   n.agent_id                     AS agent_id,
                   n.created_at                   AS created_at,
                   coalesce(n.clearance_level, 1) AS clearance_level
            ORDER BY n.created_at
        """
    else:
        _node_cypher = """
            MATCH (n:SpaiderNode {agent_id: $agent_id})
            RETURN n.id                           AS id,
                   n.label                        AS label,
                   n.type                         AS type,
                   n.properties                   AS properties,
                   n.agent_id                     AS agent_id,
                   n.created_at                   AS created_at,
                   coalesce(n.clearance_level, 1) AS clearance_level
            ORDER BY n.created_at
        """

    async with graph._driver.session(fetch_size=_BOLT_FETCH_SIZE) as session:
        result = await session.run(_node_cypher, agent_id=agent_id)
        async for record in result:
            yield json.dumps(
                {"type": "node", "data": dict(record)},
                ensure_ascii=False,
                default=str,   # handles neo4j temporal types
            ) + "\n"

    # ── 3. RELATION edges — Bolt cursor ──────────────────────────────────
    async with graph._driver.session(fetch_size=_BOLT_FETCH_SIZE) as session:
        result = await session.run(
            """
            MATCH (a:SpaiderNode {agent_id: $agent_id})
                  -[r:RELATION]->
                  (b:SpaiderNode {agent_id: $agent_id})
            RETURN r.id                            AS id,
                   a.id                            AS source_id,
                   b.id                            AS target_id,
                   r.relation                      AS relation,
                   r.properties                    AS properties,
                   r.agent_id                      AS agent_id,
                   coalesce(r.utility_weight, 1.0) AS utility_weight
            """,
            agent_id=agent_id,
        )
        async for record in result:
            yield json.dumps(
                {"type": "edge", "data": dict(record)},
                ensure_ascii=False,
                default=str,
            ) + "\n"

    # ── 4. InteractionNodes — episodic memory ────────────────────────────
    # Streamed WITHOUT the OPTIONAL MATCH / collect() aggregation to keep
    # the query a simple cursor scan.  The INFORMED_BY topology is exported
    # as separate "informed_by" lines (section 5) so the import phase can
    # reconstruct the full relational structure.
    async with graph._driver.session(fetch_size=_BOLT_FETCH_SIZE) as session:
        result = await session.run(
            """
            MATCH (i:InteractionNode {agent_id: $agent_id})
            RETURN i.id             AS id,
                   i.session_id     AS session_id,
                   i.question       AS question,
                   i.answer_summary AS answer_summary,
                   i.timestamp      AS timestamp,
                   i.agent_id       AS agent_id
            ORDER BY i.timestamp
            """,
            agent_id=agent_id,
        )
        async for record in result:
            yield json.dumps(
                {"type": "interaction", "data": dict(record)},
                ensure_ascii=False,
                default=str,
            ) + "\n"

    # ── 5. INFORMED_BY edges — episodic topology ─────────────────────────
    async with graph._driver.session(fetch_size=_BOLT_FETCH_SIZE) as session:
        result = await session.run(
            """
            MATCH (i:InteractionNode {agent_id: $agent_id})
                  -[:INFORMED_BY]->
                  (n:SpaiderNode)
            RETURN i.id AS interaction_node_id,
                   n.id AS spaider_node_id
            """,
            agent_id=agent_id,
        )
        async for record in result:
            yield json.dumps(
                {"type": "informed_by", "data": dict(record)},
                ensure_ascii=False,
                default=str,
            ) + "\n"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(request: AgentCreateRequest):
    """
    Create a new agent namespace. Returns the agent record with a generated API key.
    Store this API key securely; it is only shown once.
    """
    agent_id = str(uuid.uuid4())

    # ── Generate, hash, and store the API key in Redis (AuthService owns all crypto) ──
    auth = _get_auth_service()
    try:
        raw_key, hashed_key = await auth.generate_and_store_api_key(
            agent_id=agent_id,
            tenant_id=request.tenant_id,
            permissions=request.permissions,
        )
    except Exception as exc:
        logger.exception("Failed to generate API key for agent %s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to generate API key: {exc}")

    # Store hashed_key in the agent metadata record so the rotate-key endpoint
    # can locate the current Redis entry by hash without access to the raw key.
    agent = Agent(
        id=agent_id,
        name=request.name,
        description=request.description,
        tenant_id=request.tenant_id,
        permissions=request.permissions,
        clearance_level=request.clearance_level,
        interaction_memory=request.interaction_memory,
        api_key_hash=hashed_key,   # hash stored at rest, never the raw key
        created_at=datetime.now(timezone.utc),
    )

    try:
        await _save_agent(agent)
    except Exception as exc:
        logger.exception("Failed to save agent %s: %s", agent.id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to persist agent: {exc}")

    # Ensure a SystemAgent gravity-centre node exists in Neo4j for this agent
    try:
        graph = _get_graph_service()
        await graph.create_agent_node(agent_id=agent.id, name=agent.name)
        # Persist clearance_level on the Neo4j SystemAgent node so Cypher
        # queries can read it directly without a Redis round-trip.
        async with graph._driver.session() as _session:
            await _session.run(
                "MATCH (a:SystemAgent {agent_id: $aid}) "
                "SET a.clearance_level = $level, a.memory_mode = $mode, "
                "    a.consolidation_interval_hours = $interval",
                aid=agent.id,
                level=agent.clearance_level,
                mode=settings.default_memory_mode,
                interval=settings.default_consolidation_interval_hours,
            )
    except Exception as exc:
        logger.warning("Could not create SystemAgent node for %s: %s", agent.id, exc)

    logger.info("Created agent %s (%s) for tenant=%s", agent.id, agent.name, agent.tenant_id)

    # Return raw key exactly once in the response; the stored record holds only the hash.
    response_agent = agent.model_copy(update={"api_key": raw_key})
    return AgentResponse(success=True, agent=response_agent)


class MemoryModeUpdate(BaseModel):
    memory_mode: str  # "off" | "on"


@router.post("/{agent_id}/memory-mode", response_model=APIResponse)
async def set_memory_mode(agent_id: str, body: MemoryModeUpdate):
    """
    Switch an agent's memory mode at any time.

    - ``on``  synaptic retrieval that learns from usage (auto-reinforcement +
              decay) and still accepts explicit spaider.feedback.
    - ``off`` classic retrieval, no synaptic scoring or auto-reinforcement.

    Takes effect on the next query. Existing ``utility_weight`` values are
    preserved across a switch (turning off freezes them; turning on resumes).
    """
    mode = body.memory_mode
    if mode not in ("off", "on"):
        raise HTTPException(status_code=422, detail="memory_mode must be 'off' or 'on'.")
    try:
        graph = _get_graph_service()
        async with graph._driver.session() as _session:
            result = await _session.run(
                "MATCH (a:SystemAgent {agent_id: $aid}) SET a.memory_mode = $mode "
                "RETURN a.agent_id AS aid",
                aid=agent_id,
                mode=mode,
            )
            record = await result.single()
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"No SystemAgent node for agent {agent_id}."
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not set memory_mode: {exc}")

    logger.info("Set memory_mode=%s for agent %s", mode, agent_id)
    return APIResponse(
        success=True,
        message=f"memory_mode set to '{mode}'",
        data={"memory_mode": mode},
    )


@router.get("/{agent_id}/memory-mode", response_model=APIResponse)
async def get_memory_mode(agent_id: str):
    """
    Read an agent's current memory mode (``off`` | ``on``).

    Falls back to the configured default when the SystemAgent node carries no
    explicit value (e.g. an agent created before this field existed).
    """
    try:
        graph = _get_graph_service()
        async with graph._driver.session() as _session:
            result = await _session.run(
                "MATCH (a:SystemAgent {agent_id: $aid}) "
                "RETURN coalesce(a.memory_mode, $default) AS mode",
                aid=agent_id,
                default=settings.default_memory_mode,
            )
            record = await result.single()
        mode = record["mode"] if record else settings.default_memory_mode
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read memory_mode: {exc}")

    return APIResponse(success=True, message=mode, data={"memory_mode": mode})


# ---------------------------------------------------------------------------
# Per-agent hibernation (autonomous consolidation cadence)
# ---------------------------------------------------------------------------


class ConsolidationConfigUpdate(BaseModel):
    interval_hours: int  # 0 = off; 1 = hourly, 24 = daily, 168 = weekly


@router.get("/{agent_id}/consolidation", response_model=APIResponse)
async def get_consolidation_config(agent_id: str):
    """
    Read an agent's hibernation cadence.

    Returns ``interval_hours`` (0 = off) and ``last_consolidated_at`` (ISO
    string or null). Falls back to the configured default when unset.
    """
    try:
        graph = _get_graph_service()
        async with graph._driver.session() as _session:
            result = await _session.run(
                "MATCH (a:SystemAgent {agent_id: $aid}) "
                "RETURN coalesce(a.consolidation_interval_hours, $default) AS interval_hours, "
                "       toString(a.last_consolidated_at) AS last_consolidated_at",
                aid=agent_id,
                default=settings.default_consolidation_interval_hours,
            )
            record = await result.single()
        if record is None:
            interval_hours = settings.default_consolidation_interval_hours
            last = None
        else:
            interval_hours = int(record["interval_hours"])
            last = record["last_consolidated_at"]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read consolidation config: {exc}")

    return APIResponse(
        success=True,
        message=f"{interval_hours}h",
        data={"interval_hours": interval_hours, "last_consolidated_at": last},
    )


@router.post("/{agent_id}/consolidation", response_model=APIResponse)
async def set_consolidation_config(agent_id: str, body: ConsolidationConfigUpdate):
    """
    Set an agent's hibernation cadence.

    ``interval_hours``: 0 = off, 1 = hourly, 24 = daily, 168 = weekly (any
    value in [0, 8760] is accepted). The scheduler runs a per-agent pass once
    the interval has elapsed since ``last_consolidated_at``.
    """
    hours = body.interval_hours
    if hours < 0 or hours > 8760:
        raise HTTPException(status_code=422, detail="interval_hours must be between 0 and 8760.")
    try:
        graph = _get_graph_service()
        async with graph._driver.session() as _session:
            result = await _session.run(
                "MATCH (a:SystemAgent {agent_id: $aid}) "
                "SET a.consolidation_interval_hours = $hours "
                "RETURN a.agent_id AS aid",
                aid=agent_id,
                hours=hours,
            )
            record = await result.single()
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"No SystemAgent node for agent {agent_id}."
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not set consolidation config: {exc}")

    logger.info("Set consolidation_interval_hours=%d for agent %s", hours, agent_id)
    return APIResponse(
        success=True,
        message=f"cadence set to {hours}h",
        data={"interval_hours": hours},
    )


@router.post("/{agent_id}/consolidate-now", response_model=APIResponse)
async def consolidate_now(agent_id: str):
    """
    Run a consolidation (hibernation) pass for one agent immediately.

    Runs the same passes as the scheduled cadence (prune orphans, fuse
    duplicates, decay unused synapses, optional inverse-edge proposal) and
    stamps ``last_consolidated_at``.
    """
    try:
        graph = _get_graph_service()
        from app.workers.rem_sleep_worker import REMSleepWorker
        worker = REMSleepWorker(graph._driver)
        report = await worker.consolidate_agent_now(agent_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Consolidation failed: {exc}")

    return APIResponse(success=True, message="consolidation complete", data=report)


@router.post("/connect", response_model=AgentBridgeResponse)
async def connect_agents(request: AgentConnectRequest):
    """
    Create a SHARES_KNOWLEDGE_WITH synaptic bridge between two SystemAgent nodes.
    The relationship is idempotent (safe to call multiple times).
    Returns 400 if source == target, or if either agent node does not exist in Neo4j.
    """
    if request.source_agent_id == request.target_agent_id:
        raise HTTPException(
            status_code=400,
            detail="source_agent_id and target_agent_id must be different.",
        )

    graph = _get_graph_service()
    try:
        link_type = await graph.connect_agents(
            source_agent_id=request.source_agent_id,
            target_agent_id=request.target_agent_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception(
            "Failed to create synaptic bridge %s -> %s: %s",
            request.source_agent_id, request.target_agent_id, exc,
        )
        raise HTTPException(status_code=500, detail=f"Failed to create bridge: {exc}")

    return AgentBridgeResponse(
        success=True,
        source_agent_id=request.source_agent_id,
        target_agent_id=request.target_agent_id,
        link_type=link_type,
    )


@router.post(
    "/import",
    response_model=AgentImportResponse,
    status_code=200,
    summary="Restore agent graph from NDJSON file (hibernation import)",
    description=(
        "Stream-parse a `.ndjson` file produced by `GET /{agent_id}/export` and "
        "restore the agent's full knowledge graph.\n\n"
        "**Memory safety:** The file is read in 64 KB chunks and parsed line-by-line. "
        "The full file is NEVER loaded into memory; nodes and edges are flushed to "
        "Neo4j in micro-batches of 500 items.\n\n"
        "**State machine (per line):**\n"
        "- `metadata` (line 1): parse provenance; 409 if agent exists and `merge=false`; "
        "restore agent record in Redis with a fresh API key.\n"
        "- `node`: accumulate → flush every 500 via `write_graph_batch` (MERGE semantics).\n"
        "- `edge`: flush remaining nodes first; accumulate → flush every 500.\n"
        "- `interaction` / `informed_by` / unknown: counted in `skipped`.\n\n"
        "Returns a summary with the new API key (shown once), node/edge counts, "
        "and a skip count for unprocessed lines."
    ),
)
async def import_agent_graph(
    file: UploadFile = File(..., description=".ndjson file from GET /{agent_id}/export"),
    merge: bool = Query(
        False,
        description=(
            "False (default): raise 409 if the agent already exists. "
            "True: merge imported graph into an existing namespace using "
            "idempotent MERGE — safe to re-run on partial imports."
        ),
    ),
    target_agent_id: Optional[str] = Query(
        None,
        description=(
            "If set, ignore the agent_id in the file's metadata and import the "
            "graph into this existing agent's namespace instead. Every node "
            "and edge has its `agent_id` re-keyed to this value before write. "
            "Always uses MERGE semantics (append + idempotent) — never "
            "destroys the target's existing data. The target agent must "
            "already exist (404 if not). The target's API key is left "
            "untouched; the response's `new_api_key` field is empty."
        ),
    ),
):
    """
    NDJSON import with micro-batched, memory-safe Neo4j writes.

    File iteration
    --------------
    The uploaded file is read in ``_IMPORT_CHUNK_SIZE`` async chunks.  A
    ``buffer`` accumulates bytes until a ``\\n`` is found; the completed line
    is then decoded, JSON-parsed, and dispatched by ``type``.  This guarantees
    that at most one chunk + one partial line occupies Python memory at a time,
    regardless of file size.

    Batch semantics
    ---------------
    ``node_batch`` and ``edge_batch`` accumulate items until they reach
    ``_IMPORT_BATCH_SIZE`` (500), at which point ``_flush_nodes()`` /
    ``_flush_edges()`` calls ``write_graph_batch()`` in a single Neo4j
    transaction using ``UNWIND … MERGE`` — fully idempotent.

    Node-before-edge ordering
    -------------------------
    The NDJSON format guarantees all ``node`` lines precede ``edge`` lines.
    As a safety net, ``_flush_nodes()`` is called automatically whenever
    the first ``edge`` line is encountered, ensuring referenced nodes exist
    in Neo4j before the edge ``MATCH`` runs.
    """
    # ── State ─────────────────────────────────────────────────────────────
    agent_id:       str | None  = None
    new_api_key:    str         = ""
    node_batch:     list[Node]  = []
    edge_batch:     list[Edge]  = []
    nodes_restored: int         = 0
    edges_restored: int         = 0
    skipped:        int         = 0
    first_line_seen: bool       = False

    # When target_agent_id is set we deep-copy the graph: each imported node
    # gets a fresh UUID so it doesn't collide with the original agent's nodes
    # (SpaiderNode is keyed by `id` alone in Neo4j, so reusing the source ids
    # would either be a no-op match or steal the original's data). Edges then
    # rewrite source_id/target_id through this map.
    id_map: dict[str, str] = {}

    graph = _get_graph_service()

    # ── Micro-batch flush helpers ──────────────────────────────────────────

    async def _flush_nodes() -> None:
        nonlocal nodes_restored
        if not node_batch:
            return
        result = await graph.write_graph_batch(
            [(GraphPayload(nodes=list(node_batch), edges=[]), agent_id)]
        )
        nodes_restored += (result.nodes_created or 0) + (result.nodes_merged or 0)
        node_batch.clear()

    async def _flush_edges() -> None:
        nonlocal edges_restored
        if not edge_batch:
            return
        result = await graph.write_graph_batch(
            [(GraphPayload(nodes=[], edges=list(edge_batch)), agent_id)]
        )
        edges_restored += (result.edges_created or 0) + (result.edges_merged or 0)
        edge_batch.clear()

    # ── Shared line dispatcher (used for both mid-stream and final buffer) ─

    async def _dispatch(line: str) -> None:
        nonlocal agent_id, new_api_key, nodes_restored, edges_restored
        nonlocal skipped, first_line_seen

        if not line:
            return
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("import: malformed JSON line (skipping): %.120s", line)
            skipped += 1
            return

        line_type: str       = envelope.get("type", "")
        data:      dict      = envelope.get("data") or {}

        # ── metadata ──────────────────────────────────────────────────────
        if line_type == "metadata":
            if first_line_seen:
                raise HTTPException(
                    status_code=400,
                    detail="Malformed NDJSON: duplicate metadata line.",
                )
            first_line_seen = True
            file_agent_id = data.get("agent_id")
            if not file_agent_id:
                raise HTTPException(
                    status_code=400,
                    detail="Malformed NDJSON: metadata line missing agent_id.",
                )

            # ── target_agent_id mode: re-key the import into an existing agent ─
            # The file's agent_id is ignored; every node/edge is re-keyed to
            # `target_agent_id` and written via MERGE (idempotent append).
            if target_agent_id:
                target = await _load_agent(target_agent_id)
                if target is None:
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            f"target_agent_id '{target_agent_id}' not found. "
                            "Create the agent first via POST /agents, or omit "
                            "target_agent_id to create a new agent from the file."
                        ),
                    )
                agent_id = target_agent_id
                new_api_key = ""  # caller already owns the target's key
                logger.info(
                    "import: re-keying file agent_id=%s -> target=%s (append mode)",
                    file_agent_id, target_agent_id,
                )
                return

            # ── default mode: file's agent_id is the target ──────────────────
            agent_id = file_agent_id

            # Conflict check
            existing = await _load_agent(agent_id)
            if existing is not None and not merge:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Agent '{agent_id}' already exists. "
                        "Pass ?merge=true to merge into the existing namespace, "
                        "or ?target_agent_id=<id> to import into a different agent."
                    ),
                )

            # Restore agent record in Redis with a fresh API key.
            # The original api_key is deliberately excluded from exports.
            # AuthService owns all crypto — no secrets/hashlib here.
            auth = _get_auth_service()
            raw_key_import, hashed_key_import = await auth.generate_and_store_api_key(
                agent_id=agent_id,
                tenant_id=data.get("tenant_id", "default"),
                permissions=data.get("permissions") or ["read", "write", "query"],
            )
            new_api_key = raw_key_import  # exposed once in AgentImportResponse

            restored_agent = Agent(
                id=agent_id,
                name=data.get("agent_name", "Imported Agent"),
                description=data.get("agent_description"),
                tenant_id=data.get("tenant_id", "default"),
                permissions=data.get("permissions") or ["read", "write", "query"],
                clearance_level=int(data.get("clearance_level") or 1),
                interaction_memory=bool(data.get("interaction_memory", False)),
                api_key_hash=hashed_key_import,   # hash stored at rest
                created_at=datetime.now(timezone.utc),
            )
            await _save_agent(restored_agent)

            # Ensure the SystemAgent gravity-centre node exists in Neo4j
            try:
                await graph.create_agent_node(
                    agent_id=agent_id, name=restored_agent.name
                )
            except Exception as exc:
                logger.warning(
                    "import: could not ensure SystemAgent node for %s: %s",
                    agent_id, exc,
                )
            logger.info(
                "import: metadata restored  agent=%s  merge=%s", agent_id, merge
            )
            return

        # Non-metadata lines require metadata to have been seen first
        if not first_line_seen or not agent_id:
            raise HTTPException(
                status_code=400,
                detail="Malformed NDJSON: first non-blank line must be a metadata record.",
            )

        # ── node ──────────────────────────────────────────────────────────
        if line_type == "node":
            props_raw = data.get("properties", "{}")
            try:
                props: dict = (
                    json.loads(props_raw)
                    if isinstance(props_raw, str)
                    else (props_raw or {})
                )
            except (json.JSONDecodeError, ValueError):
                props = {}

            src_node_id = data["id"]
            # Deep-copy mode: mint a new id, remember the mapping for edges.
            new_node_id = str(uuid.uuid4()) if target_agent_id else src_node_id
            if target_agent_id:
                id_map[src_node_id] = new_node_id

            node_batch.append(Node(
                id=new_node_id,
                label=data.get("label", ""),
                type=data.get("type", "OTHER"),
                properties=props,
                embedding=data.get("embedding"),   # None when exported without embeddings
                agent_id=agent_id,
            ))
            if len(node_batch) >= _IMPORT_BATCH_SIZE:
                await _flush_nodes()
            return

        # ── edge ──────────────────────────────────────────────────────────
        if line_type == "edge":
            # Safety net: flush any remaining nodes before writing edges.
            # The NDJSON format guarantees nodes precede edges, so this only
            # fires once — for the tail of the last node batch.
            if node_batch:
                await _flush_nodes()

            props_raw = data.get("properties", "{}")
            try:
                props = (
                    json.loads(props_raw)
                    if isinstance(props_raw, str)
                    else (props_raw or {})
                )
            except (json.JSONDecodeError, ValueError):
                props = {}

            src_id = data["source_id"]
            tgt_id = data["target_id"]
            if target_agent_id:
                # Both endpoints must have been seen during the node phase. If
                # not, the edge references a non-existent node and is skipped.
                if src_id not in id_map or tgt_id not in id_map:
                    skipped += 1
                    return
                src_id = id_map[src_id]
                tgt_id = id_map[tgt_id]

            edge_batch.append(Edge(
                id=str(uuid.uuid4()) if target_agent_id else data["id"],
                source_id=src_id,
                target_id=tgt_id,
                relation=data.get("relation", "RELATED_TO"),
                properties=props,
                agent_id=agent_id,
                utility_weight=float(data.get("utility_weight") or 1.0),
            ))
            if len(edge_batch) >= _IMPORT_BATCH_SIZE:
                await _flush_edges()
            return

        # ── interaction / informed_by / unknown ───────────────────────────
        # Episodic memory is preserved in the export for archival fidelity
        # but is not restored on import — interactions are re-generated
        # organically as the agent processes new queries.
        skipped += 1

    # ── Async line-by-line iteration via chunked reads + newline splitter ─
    buffer = b""
    reading_done = False

    while not reading_done:
        chunk = await file.read(_IMPORT_CHUNK_SIZE)
        if not chunk:
            # Sentinel newline forces the while-loop below to process any
            # unterminated final line that lacks a trailing newline.
            reading_done = True
            buffer += b"\n"
        else:
            buffer += chunk

        while b"\n" in buffer:
            raw_line, buffer = buffer.split(b"\n", 1)
            await _dispatch(raw_line.decode("utf-8", errors="replace").strip())

    # ── Final micro-batch flushes ──────────────────────────────────────────
    # Flush any remaining nodes first (edges reference nodes).
    await _flush_nodes()
    await _flush_edges()

    if not agent_id:
        raise HTTPException(
            status_code=400,
            detail="Empty or invalid NDJSON file — no metadata line found.",
        )

    logger.info(
        "import complete  agent=%s  nodes_restored=%d  edges_restored=%d  skipped=%d",
        agent_id, nodes_restored, edges_restored, skipped,
    )
    return AgentImportResponse(
        success=True,
        agent_id=agent_id,
        new_api_key=new_api_key,
        nodes_restored=nodes_restored,
        edges_restored=edges_restored,
        skipped=skipped,
    )


@router.post("/{agent_id}/rotate-key", response_model=RotateKeyResponse)
async def rotate_api_key(agent_id: str) -> RotateKeyResponse:
    """
    Rotate the agent's API key: revoke the current credential and issue a new one.

    The Neo4j knowledge graph is keyed on ``agent_id``, so rotation leaves every
    node, edge, and embedding untouched by design — only the ``spaider:apikey:*``
    Redis slot changes.
    """
    existing = await _load_agent(agent_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    auth = _get_auth_service()

    # Revoke the previous credential. If ``api_key_hash`` is known we DEL by
    # hash (O(1)); otherwise fall back to a SCAN over all apikey slots, which
    # handles agents that existed before this field was introduced.
    try:
        if existing.api_key_hash:
            await auth.revoke_api_key_by_hash(existing.api_key_hash)
        else:
            await auth.revoke_all_for_agent(agent_id)
    except Exception as exc:
        logger.exception("Failed to revoke old API key for %s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to revoke old key: {exc}")

    # Generate + persist the new credential.
    try:
        raw_api_key, new_hash = await auth.generate_and_store_api_key(
            agent_id=existing.id,
            tenant_id=existing.tenant_id,
            permissions=existing.permissions,
        )
    except Exception as exc:
        logger.exception("Failed to generate new API key for %s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to generate new key: {exc}")

    # Update the agent record with the new hash. On persistence failure we
    # revoke the just-minted apikey slot so we don't leave a credential
    # whose hash no agent record points to.
    existing.api_key_hash = new_hash
    try:
        await _save_agent(existing)
    except Exception as exc:
        logger.exception("Failed to persist rotated hash for %s: %s", agent_id, exc)
        try:
            await auth.revoke_api_key_by_hash(new_hash)
        except Exception:
            logger.exception("Rollback of new apikey slot for %s failed", agent_id)
        raise HTTPException(status_code=500, detail=f"Failed to persist rotated key: {exc}")

    logger.info("Rotated API key for agent %s", agent_id)
    return RotateKeyResponse(agent_id=agent_id, api_key=raw_api_key)


@router.get("", response_model=AgentListResponse)
async def list_agents():
    """List all registered agents."""
    try:
        redis = await _get_redis()
        agent_ids = await redis.smembers(AGENT_INDEX_KEY)
        agents: list[Agent] = []
        for aid in agent_ids:
            agent = await _load_agent(aid)
            if agent:
                # Redact API key + hash in listings — never exposed to callers
                agent.api_key = None
                agent.api_key_hash = None
                agents.append(agent)

        agents.sort(key=lambda a: a.created_at)
        return AgentListResponse(agents=agents, total=len(agents))
    except Exception as exc:
        logger.exception("Error listing agents: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/links", response_model=list[SwarmLinkResponse])
async def list_swarm_links():
    """
    Return every active SHARES_KNOWLEDGE_WITH bridge in the neural graph,
    enriched with human-readable agent names.
    """
    graph = _get_graph_service()
    try:
        async with graph._driver.session() as session:
            result = await session.run(
                """
                MATCH (a:SystemAgent)-[r:SHARES_KNOWLEDGE_WITH]->(b:SystemAgent)
                RETURN a.agent_id AS source_id,
                       a.name     AS source_name,
                       b.agent_id AS target_id,
                       b.name     AS target_name
                ORDER BY a.name ASC
                """
            )
            records = await result.data()
    except Exception as exc:
        logger.exception("Failed to list swarm links: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to fetch links: {exc}")

    return [
        SwarmLinkResponse(
            source_id=rec["source_id"],
            source_name=rec["source_name"] or rec["source_id"][:8],
            target_id=rec["target_id"],
            target_name=rec["target_name"] or rec["target_id"][:8],
        )
        for rec in records
    ]


@router.delete("/link", response_model=SwarmLinkDeleteResponse)
async def delete_swarm_link(
    source_agent_id: str = Query(..., description="agent_id of the source SystemAgent"),
    target_agent_id: str = Query(..., description="agent_id of the target SystemAgent"),
):
    """
    Remove the SHARES_KNOWLEDGE_WITH edge between two SystemAgent nodes.
    Returns 404 if the edge does not exist.
    """
    if source_agent_id == target_agent_id:
        raise HTTPException(
            status_code=400,
            detail="source_agent_id and target_agent_id must be different.",
        )

    graph = _get_graph_service()
    try:
        async with graph._driver.session() as session:
            result = await session.run(
                """
                MATCH (a:SystemAgent {agent_id: $source})-[r:SHARES_KNOWLEDGE_WITH]->(b:SystemAgent {agent_id: $target})
                DELETE r
                RETURN count(r) AS deleted_count
                """,
                source=source_agent_id,
                target=target_agent_id,
            )
            record = await result.single()
            deleted_count: int = record["deleted_count"] if record else 0
    except Exception as exc:
        logger.exception(
            "Failed to delete swarm link %s -> %s: %s",
            source_agent_id, target_agent_id, exc,
        )
        raise HTTPException(status_code=500, detail=f"Failed to delete link: {exc}")

    if deleted_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No SHARES_KNOWLEDGE_WITH edge found from '{source_agent_id}' to '{target_agent_id}'.",
        )

    logger.info(
        "Deleted synaptic bridge %s -> %s", source_agent_id, target_agent_id
    )
    return SwarmLinkDeleteResponse(
        success=True,
        deleted_count=deleted_count,
        source_agent_id=source_agent_id,
        target_agent_id=target_agent_id,
    )


@router.delete(
    "/{agent_id}/interactions",
    response_model=DeleteInteractionsResponse,
    summary="Wipe episodic memory for an agent",
    description=(
        "Hard-delete every `InteractionNode` that belongs to this agent. "
        "All associated `BELONGS_TO_AGENT` and `INFORMED_BY` relationships are "
        "removed automatically via `DETACH DELETE`.\n\n"
        "**SpaiderNodes are never affected** — only episodic memory records.\n\n"
        "Returns the count of deleted `InteractionNode` objects."
    ),
)
async def delete_agent_interactions(agent_id: str):
    existing = await _load_agent(agent_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    graph = _get_graph_service()
    try:
        deleted_count = await graph.delete_agent_interactions(agent_id)
    except Exception as exc:
        logger.exception(
            "Failed to delete interactions for agent %s: %s", agent_id, exc
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete interaction memory: {exc}",
        )

    logger.info(
        "Wiped episodic memory for agent %s (%d nodes deleted)",
        agent_id, deleted_count,
    )
    return DeleteInteractionsResponse(
        success=True,
        agent_id=agent_id,
        deleted_count=deleted_count,
    )


@router.get(
    "/{agent_id}/export",
    summary="Stream full agent graph as NDJSON (hibernation export)",
    description=(
        "Stream the complete knowledge graph for this agent as a "
        "Newline-Delimited JSON (NDJSON) file download.\n\n"
        "**Memory safety:** Uses the Neo4j Bolt driver's server-side cursor — "
        "the full graph is NEVER loaded into Python memory. Safe for graphs "
        "with 100 k+ nodes.\n\n"
        "**Line types** (in order):\n"
        "- `metadata` — one line; agent provenance + export parameters\n"
        "- `node` — one line per SpaiderNode\n"
        "- `edge` — one line per RELATION edge\n"
        "- `interaction` — one line per InteractionNode (episodic memory)\n"
        "- `informed_by` — one line per INFORMED_BY edge pair\n\n"
        "Pass `include_embeddings=false` to omit 1536-dimensional vectors "
        "(reduces file size ~6 KB per node)."
    ),
    response_class=StreamingResponse,
)
async def export_agent_graph_ndjson(
    agent_id: str,
    include_embeddings: bool = Query(
        True,
        description=(
            "Include embedding vectors in the export. "
            "Set to false to reduce file size when vectors are not needed "
            "for import (they will be recomputed on ingest)."
        ),
    ),
):
    agent = await _load_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    filename = f"spaider_agent_{agent_id}.ndjson"
    logger.info(
        "Starting NDJSON stream export for agent=%s include_embeddings=%s",
        agent_id, include_embeddings,
    )
    return StreamingResponse(
        _ndjson_export_generator(agent_id, agent, include_embeddings),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: str):
    """Retrieve agent details. API key is redacted."""
    try:
        agent = await _load_agent(agent_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    # Redact API key + hash
    agent.api_key = None
    agent.api_key_hash = None
    return AgentResponse(success=True, agent=agent)


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: str, request: AgentCreateRequest):
    """Update an existing agent's metadata."""
    existing = await _load_agent(agent_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    # Update mutable fields; preserve id, api_key, and created_at
    existing.name = request.name
    existing.description = request.description
    existing.tenant_id = request.tenant_id
    existing.permissions = request.permissions
    existing.clearance_level = request.clearance_level
    existing.interaction_memory = request.interaction_memory

    try:
        await _save_agent(existing)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Sync clearance_level to Neo4j SystemAgent node
    try:
        graph = _get_graph_service()
        async with graph._driver.session() as _session:
            await _session.run(
                "MATCH (a:SystemAgent {agent_id: $aid}) "
                "SET a.clearance_level = $level",
                aid=agent_id,
                level=existing.clearance_level,
            )
    except Exception as exc:
        logger.warning("Could not sync clearance_level to Neo4j for %s: %s", agent_id, exc)

    existing.api_key = None
    existing.api_key_hash = None  # Redact in response — never exposed to callers
    logger.info("Updated agent %s (clearance_level=%d)", agent_id, existing.clearance_level)
    return AgentResponse(success=True, agent=existing)


@router.delete("/{agent_id}", response_model=APIResponse)
async def delete_agent(agent_id: str):
    """
    Delete an agent and its entire knowledge graph from Neo4j.
    This is irreversible.
    """
    existing = await _load_agent(agent_id)
    # Even if the agent record doesn't exist, we should try to clean up any orphaned graph data.
    # existing might be None if the agent was partially deleted before.

    graph = _get_graph_service()
    try:
        # Delete all nodes (and their edges) belonging to this agent
        await graph.delete_agent_graph(agent_id)
        logger.info("Deleted graph data for agent %s", agent_id)
    except Exception as exc:
        logger.exception("Could not delete graph data for agent %s: %s", agent_id, exc)
        # We must not proceed with agent record deletion if graph cleanup fails,
        # otherwise we leave ghost nodes in Neo4j that appear in the Multiverse view forever.
        raise HTTPException(status_code=500, detail="Failed to delete agent graph data. Please try again.")

    try:
        await _delete_agent(agent_id)
    except Exception as exc:
        if existing is not None:
            raise HTTPException(status_code=500, detail=f"Failed to delete agent record: {exc}")

    name = existing.name if existing else "Unknown"
    logger.info("Deleted agent %s (%s)", agent_id, name)
    return APIResponse(
        success=True,
        message=f"Agent '{agent_id}' and its graph data have been permanently deleted.",
    )
