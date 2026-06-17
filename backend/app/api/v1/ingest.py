"""
Ingest API endpoints: push text into the knowledge graph via async Kafka queue
or synchronously for development/testing.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Annotated, Any, List, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel as _BaseModel
from pydantic import Field

from app.connectors import ConnectorRegistry, RunState, record_connector_run, set_global_registry
from app.connectors.mcp_connector import MCPConnector
from app.connectors.sql_connector import SQLConnector
from app.connectors.trafilatura_url_connector import URLConnector
from app.connectors.upload_connector import UploadConnector
from app.models.requests import IngestRequest
from app.models.responses import IngestQueuedResponse, IngestSyncResponse, SlimEdge, SlimNode
from app.models.schemas import Edge, GraphPayload, Node
from app.services.compressor import ExtractionError

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Lazy-initialised singletons (avoids import-time connection attempts)
# ---------------------------------------------------------------------------

_kafka_producer = None
_graph_service = None
_compressor = None
_resolver = None

# ---------------------------------------------------------------------------
# Connector Framework — registry + singletons
# ---------------------------------------------------------------------------

# Connector instances are stateless; RunState carries all per-run mutable state.
_upload_connector = UploadConnector()
_url_connector = URLConnector()
_mcp_connector = MCPConnector()
_sql_connector = SQLConnector()


# Maximum length of the FACT node's `label` — the label gets shown in
# graph UIs and is also what the entity_resolver would normally embed.
# We embed by `description` instead (see entity_resolver), so the label
# only needs to be human-recognisable. 200 chars is a reasonable preview.
_FACT_LABEL_PREVIEW_CHARS = 200


def _attach_fact_node(payload: GraphPayload, text: str, source: Optional[str]) -> None:
    """Append a FACT node carrying the original ingested ``text`` and a
    ``MENTIONS`` edge from it to every entity node already in ``payload``.

    Mutates ``payload`` in place. Without this, the original
    natural-language text is lost during
    extraction and synthesis-style queries (``spaider.query`` over
    multi-sentence facts) silently fail to find the original content.
    """
    if not text or not text.strip():
        return
    label_preview = text.strip().replace("\n", " ")[:_FACT_LABEL_PREVIEW_CHARS]
    if len(text) > _FACT_LABEL_PREVIEW_CHARS:
        label_preview += "…"
    fact_source = source or "ingest_text_sync"
    fact_node = Node(
        label=f"fact: {label_preview}",
        type="FACT",
        description=text,
        properties={"source": fact_source},
    )
    fact_edges = [
        Edge(
            source_id=fact_node.id,
            target_id=entity.id,
            relation="MENTIONS",
            properties={"source": fact_source},
        )
        for entity in payload.nodes
    ]
    payload.nodes.append(fact_node)
    payload.edges.extend(fact_edges)

_connector_registry = ConnectorRegistry()
_connector_registry.register(_upload_connector)
_connector_registry.register(_url_connector)
_connector_registry.register(_mcp_connector)
_connector_registry.register(_sql_connector)
set_global_registry(_connector_registry)  # exposes registry to GET /connectors/{id}/status

# RunState persistence.
#
# Each (connector_id, agent_id) pair owns one RunState; the connectors
# read it before each operation (to send conditional headers / use the
# stored cursor) and mutate it after each successful yield. We persist
# it to Redis so it survives process restarts and is shared across
# replicas — without Redis the dict-only in-process cache still works.
#
# Redis key:  spaider:connector:run_state:{connector_id}:{agent_id}
# Redis TTL:  30 days (refreshed on every save) — bounds key growth on
#             agents that are deleted but never explicitly cleaned up.
_RUN_STATE_KEY_TEMPLATE = "spaider:connector:run_state:{connector_id}:{agent_id}"
_RUN_STATE_TTL_SECONDS = 60 * 60 * 24 * 30

# In-process write-through cache so two requests in the same process
# don't both round-trip Redis. Process-local; Redis is the source of truth.
_run_states: dict[tuple[str, str], RunState] = {}

_run_state_redis = None


async def _get_run_state_redis():
    """Lazy Redis client for RunState persistence. Returns None if Redis is
    misconfigured / unreachable so callers can degrade to in-process only."""
    global _run_state_redis
    if _run_state_redis is None:
        try:
            import redis.asyncio as aioredis  # type: ignore[import]

            from app.config import settings
            _run_state_redis = aioredis.from_url(
                settings.redis_url, decode_responses=True
            )
            # Eager ping so we surface auth/network errors here, not at every
            # call site. Failure → swallow, mark as None, fall back to dict.
            await _run_state_redis.ping()
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            logger.warning(
                "RunState Redis unavailable, falling back to in-process: %s", exc
            )
            _run_state_redis = None
    return _run_state_redis


def _run_state_redis_key(connector_id: str, agent_id: str) -> str:
    return _RUN_STATE_KEY_TEMPLATE.format(
        connector_id=connector_id, agent_id=agent_id,
    )


async def _get_run_state(connector_id: str, agent_id: str) -> RunState:
    """
    Read the RunState for ``(connector_id, agent_id)``. Reads come from
    in-process cache first (a request that just saved still sees its own
    update without a Redis round-trip), then Redis, finally an empty
    RunState seeded with the connector_id.
    """
    cache_key = (connector_id, agent_id)
    if cache_key in _run_states:
        return _run_states[cache_key]

    redis = await _get_run_state_redis()
    if redis is not None:
        try:
            raw = await redis.get(_run_state_redis_key(connector_id, agent_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis GET failed for RunState %s/%s: %s",
                           connector_id, agent_id, exc)
            raw = None
        if raw:
            try:
                state = RunState.model_validate_json(raw)
                _run_states[cache_key] = state
                return state
            except Exception as exc:  # noqa: BLE001 — corrupt cache → reset
                logger.warning(
                    "Corrupt RunState in Redis for %s/%s, resetting: %s",
                    connector_id, agent_id, exc,
                )

    state = RunState(connector_id=connector_id)
    _run_states[cache_key] = state
    return state


async def _save_run_state(connector_id: str, agent_id: str, state: RunState) -> None:
    """Write the RunState to Redis with TTL, refreshing the in-process cache.
    Failures are logged but never propagated — connector callers shouldn't
    fail the user's request just because Redis is unreachable."""
    cache_key = (connector_id, agent_id)
    _run_states[cache_key] = state

    redis = await _get_run_state_redis()
    if redis is None:
        return
    try:
        await redis.set(
            _run_state_redis_key(connector_id, agent_id),
            state.model_dump_json(),
            ex=_RUN_STATE_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Redis SET failed for RunState %s/%s (in-process cache still updated): %s",
            connector_id, agent_id, exc,
        )


def _get_graph_service():
    global _graph_service
    if _graph_service is None:
        from app.services.graph_service import GraphService
        _graph_service = GraphService()
    return _graph_service


def _get_compressor():
    global _compressor
    if _compressor is None:
        from app.services.compressor import SemanticCompressor
        _compressor = SemanticCompressor()
    return _compressor


def _get_resolver():
    global _resolver
    if _resolver is None:
        from app.services.entity_resolver import EntityResolver
        _resolver = EntityResolver()
    return _resolver


def _fire_replay_event(
    workflow_id: str,
    agent_id: str,
    event_type: str,
    payload: dict,
    graph_state_hash: str | None = None,
) -> None:
    """Schedule a replay event as a fire-and-forget background task.

    Uses the same lazy singleton as the Replay HTTP endpoints.  Silently
    drops the event when Kafka is unavailable — replay is non-critical.
    """
    def _default_trace_summary(event: str, data: dict) -> str:
        et = event.lower()
        if et == "ingest_queued":
            return (
                "Ingest request was accepted and placed on the async queue "
                "for background extraction and graph write."
            )
        if et == "ingest_received":
            return (
                "Ingest pipeline started: text is being normalized, then entities and "
                "relationships will be extracted before persistence."
            )
        if et == "graph_mutation":
            return (
                "Extracted entities were merged into the knowledge graph using agent-scoped "
                "upserts; counts in metadata describe created/merged nodes and edges."
            )
        if et == "ingest_completed":
            nodes_total = data.get("nodes_total")
            edges_total = data.get("edges_total")
            return (
                "Ingest pipeline finished successfully "
                f"(nodes={nodes_total}, edges={edges_total})."
            )
        if et == "ingest_failed":
            return "Ingest pipeline failed; see metadata/error fields for root cause."
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
                graph_state_hash=graph_state_hash,
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("Replay event skipped (%s): %s", event_type, exc)

    try:
        asyncio.get_running_loop().create_task(_emit())
    except RuntimeError:
        pass  # No running loop — not in an async context, skip silently


async def _get_kafka_producer():
    """Lazy initialised Kafka producer singleton."""
    global _kafka_producer
    if _kafka_producer is None:
        try:
            from app.services.kafka_producer import KafkaProducerService
            _kafka_producer = KafkaProducerService()
            await _kafka_producer.start()
            logger.info("Kafka producer initialised")
        except Exception as exc:
            logger.warning("Kafka unavailable, producer not started: %s", exc)
            _kafka_producer = None
    return _kafka_producer


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=IngestQueuedResponse, status_code=202)
async def ingest_text_async(request: IngestRequest):
    """
    Async ingest: serialise the payload and push to the Kafka topic.
    The compressor worker will consume and write to Neo4j asynchronously.
    """
    producer = await _get_kafka_producer()
    if producer is None:
        raise HTTPException(
            status_code=503,
            detail="Kafka producer unavailable. Use /ingest/sync for synchronous ingestion.",
        )

    try:
        message_id = await producer.produce_ingest_event(
            text=request.text,
            agent_id=request.agent_id,
            source=request.source,
            metadata=request.metadata,
        )
        logger.info("Queued ingest message %s for agent %s", message_id, request.agent_id)
        # Replay: record the enqueue so this workflow appears in the Replay UI
        # even before the worker processes it.  message_id doubles as workflow_id.
        _fire_replay_event(
            message_id,
            request.agent_id,
            "ingest_queued",
            {"text_length": len(request.text), "source": request.source},
        )
    except Exception as exc:
        logger.error("Failed to queue ingest message: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to queue message: {exc}")

    return IngestQueuedResponse(
        status="queued",
        message_id=message_id,
        agent_id=request.agent_id,
    )


@router.post("/sync", response_model=IngestSyncResponse)
async def ingest_text_sync(request: IngestRequest):
    """
    Synchronous ingest: run the full extraction + write pipeline inline.
    Intended for development, testing, and low-volume use-cases.
    """
    t0 = time.perf_counter()
    # One workflow_id ties together all replay events for this ingest operation.
    workflow_id = str(uuid.uuid4())

    # Replay: pipeline started
    _fire_replay_event(
        workflow_id, request.agent_id, "ingest_received",
        {"text_length": len(request.text), "source": request.source},
    )

    try:
        compressor = _get_compressor()
        resolver = _get_resolver()
        graph = _get_graph_service()

        # 1. Extract graph payload from raw text
        logger.debug("Extracting entities for agent_id=%s", request.agent_id)
        payload = await compressor.extract(request.text, context={"source": request.source} if request.source else None)

        # 1a. Append a FACT node carrying the verbatim ingested text.
        # Without this, the extraction pipeline would drop the
        # original sentence and only keep the entity nodes — which is fine
        # for entity-graph queries but loses everything synthesis questions
        # need. The FACT node is linked to every extracted entity via a
        # MENTIONS edge so graph traversal can find the original text from
        # any of its entities.
        #
        # ALWAYS attach it, even when extraction found no entities: terse
        # facts ("...recovered in 18s.") often yield an empty payload, and
        # gating the FACT node on `payload.nodes` silently dropped the whole
        # fact — an unrecoverable ingestion loss. A standalone FACT node is
        # still fully retrievable (fulltext + embedding both cover it), so
        # the fact is never lost; the small graph-size cost is worth it.
        if request.text and request.text.strip():
            _attach_fact_node(payload, request.text, request.source)

        # 2. Attach agent_id to nodes/edges
        for node in payload.nodes:
            node.agent_id = request.agent_id
        for edge in payload.edges:
            edge.agent_id = request.agent_id

        # 3. Resolve / deduplicate entities against existing graph
        resolved_payload = await resolver.resolve(payload, request.agent_id, graph, caller_context="api")

        # 4. Write to Neo4j
        result = await graph.write_graph(resolved_payload, request.agent_id)
        nodes_written = result.nodes_created + result.nodes_merged
        edges_written = result.edges_created + result.edges_merged
        nodes_merged = result.nodes_merged
        nodes_created = result.nodes_created

        # Replay: graph state mutated — capture node IDs now (while resolved_payload
        # is in scope) but defer sort + SHA-256 into the background task so the
        # CPU work doesn't add to request latency.
        _replay_node_ids = [n.id for n in resolved_payload.nodes]
        _replay_mutation_payload = {
            "nodes_created": result.nodes_created,
            "nodes_merged": result.nodes_merged,
            "edges_created": result.edges_created,
            "edges_merged": result.edges_merged,
        }

        async def _emit_graph_mutation() -> None:
            try:
                _graph_hash = hashlib.sha256(
                    json.dumps(sorted(_replay_node_ids)).encode()
                ).hexdigest()[:16]
                _fire_replay_event(
                    workflow_id, request.agent_id, "graph_mutation",
                    _replay_mutation_payload,
                    graph_state_hash=_graph_hash,
                )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.debug("graph_mutation replay skipped for workflow %s", workflow_id)

        asyncio.get_running_loop().create_task(_emit_graph_mutation())

        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "Sync ingest completed: agent=%s nodes=%d edges=%d merged=%d latency=%.1fms",
            request.agent_id,
            nodes_written,
            edges_written,
            nodes_merged,
            latency_ms,
        )

        slim_nodes = [
            SlimNode(
                id=n.id,
                label=n.label,
                type=n.type,
                properties={k: v for k, v in (n.properties or {}).items() if k != "embedding"},
                agent_id=n.agent_id,
            )
            for n in resolved_payload.nodes
        ]
        slim_edges = [
            SlimEdge(
                id=e.id,
                source=e.source_id,
                target=e.target_id,
                relation=e.relation,
                agent_id=e.agent_id,
            )
            for e in resolved_payload.edges
        ]

        response = IngestSyncResponse(
            success=True,
            agent_id=request.agent_id,
            nodes_created=max(nodes_created, 0),
            nodes_merged=nodes_merged,
            edges_created=edges_written,
            edges_merged=0,
            nodes=slim_nodes,
            edges=slim_edges,
            latency_ms=round(latency_ms, 2),
        )

        # Fire-and-forget analytics event
        try:
            import app.main as _main
            svc = getattr(_main, "_analytics_service", None)
            if svc and svc._ready:
                chunk_count = getattr(compressor, "_last_chunk_count", 1)
                svc.record_ingest(
                    agent_id=request.agent_id,
                    text_length=len(request.text),
                    chunk_count=chunk_count,
                    nodes_created=max(nodes_created, 0),
                    nodes_merged=nodes_merged,
                    edges_created=edges_written,
                    latency_ms=round(latency_ms, 2),
                )
        except Exception:
            pass

        # Replay: pipeline completed
        _fire_replay_event(
            workflow_id, request.agent_id, "ingest_completed",
            {
                "nodes_total": nodes_written,
                "edges_total": edges_written,
                "latency_ms": round(latency_ms, 2),
                "source": request.source,
            },
        )

        return response

    except ExtractionError as exc:
        logger.warning(
            "Sync ingest EXTRACTION_FAILED: agent=%s attempts=%d last_error=%s",
            request.agent_id, exc.attempts, exc.last_error,
        )
        _fire_replay_event(
            workflow_id, request.agent_id, "ingest_failed",
            {
                "reason": "extraction_failed",
                "attempts": exc.attempts,
                "last_error": exc.last_error,
            },
        )
        try:
            import app.main as _main
            svc = getattr(_main, "_analytics_service", None)
            if svc and svc._ready:
                svc.record_extraction_failed(
                    agent_id=request.agent_id,
                    source="sync",
                    text_length=len(request.text),
                    attempts=exc.attempts,
                    last_error=exc.last_error or str(exc),
                )
        except Exception:
            pass
        raise HTTPException(
            status_code=422,
            detail=(
                f"Extraction failed after {exc.attempts} attempts: "
                f"{exc.last_error or exc}"
            ),
        )
    except ValueError as exc:
        logger.warning("Validation error during sync ingest: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during sync ingest")
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Streaming ingest — 202 Accepted + WebSocket events
# ---------------------------------------------------------------------------

async def _run_stream_ingest(request: IngestRequest, job_id: str) -> None:
    """
    Full ingest pipeline running as a background task.
    Broadcasts progress events to the frontend via WebSocket.
    job_id doubles as workflow_id so the Replay UI can correlate events.
    """
    from app.services.ws_manager import ws_manager

    async def broadcast(event_type: str, data: dict) -> None:
        await ws_manager.broadcast(
            request.agent_id, {"type": event_type, "job_id": job_id, **data}
        )

    # Replay: pipeline started (job_id == workflow_id)
    _fire_replay_event(
        job_id, request.agent_id, "ingest_received",
        {"text_length": len(request.text), "source": request.source},
    )

    try:
        await broadcast("status", {"message": "Extracting entities from text…"})

        compressor = _get_compressor()
        resolver = _get_resolver()
        graph = _get_graph_service()

        payload = await compressor.extract(
            request.text,
            context={"source": request.source} if request.source else None,
        )

        await broadcast(
            "status",
            {"message": f"Extracted {len(payload.nodes)} entities — resolving duplicates…"},
        )

        for node in payload.nodes:
            node.agent_id = request.agent_id
        for edge in payload.edges:
            edge.agent_id = request.agent_id

        resolved = await resolver.resolve(payload, request.agent_id, graph, caller_context="api")

        await broadcast(
            "status",
            {"message": f"Writing {len(resolved.nodes)} nodes to graph…"},
        )

        result = await graph.write_graph(resolved, request.agent_id)

        # Replay: graph mutation committed — defer fingerprinting into task.
        _stream_node_ids = [n.id for n in resolved.nodes]
        _stream_mutation_payload = {
            "nodes_created": result.nodes_created,
            "nodes_merged": result.nodes_merged,
            "edges_created": result.edges_created,
            "edges_merged": result.edges_merged,
        }

        async def _emit_stream_graph_mutation() -> None:
            try:
                _stream_hash = hashlib.sha256(
                    json.dumps(sorted(_stream_node_ids)).encode()
                ).hexdigest()[:16]
                _fire_replay_event(
                    job_id, request.agent_id, "graph_mutation",
                    _stream_mutation_payload,
                    graph_state_hash=_stream_hash,
                )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.debug("graph_mutation replay skipped for job %s", job_id)

        asyncio.get_running_loop().create_task(_emit_stream_graph_mutation())

        # Stream nodes one-by-one — creates the "popping" visual effect
        for node in resolved.nodes:
            await broadcast(
                "node",
                {
                    "node": {
                        "id": node.id,
                        "label": node.label,
                        "type": node.type,
                        "properties": {
                            k: v
                            for k, v in (node.properties or {}).items()
                            if k != "embedding"
                        },
                        "agent_id": node.agent_id,
                    }
                },
            )
            await asyncio.sleep(0.1)  # 100 ms between nodes → visible pop-in

        for edge in resolved.edges:
            await broadcast(
                "edge",
                {
                    "edge": {
                        "id": edge.id,
                        "source": edge.source_id,
                        "target": edge.target_id,
                        "relation": edge.relation,
                        "properties": {},
                    }
                },
            )
            await asyncio.sleep(0.03)

        await broadcast(
            "done",
            {
                "nodes_created": result.nodes_created,
                "nodes_merged": result.nodes_merged,
                "edges_created": result.edges_created,
                "edges_merged": result.edges_merged,
            },
        )

        # Replay: pipeline completed
        _fire_replay_event(
            job_id, request.agent_id, "ingest_completed",
            {
                "nodes_total": result.nodes_created + result.nodes_merged,
                "edges_total": result.edges_created + result.edges_merged,
                "source": request.source,
            },
        )

    except ExtractionError as exc:
        logger.warning(
            "Stream ingest EXTRACTION_FAILED job=%s agent=%s attempts=%d last_error=%s",
            job_id, request.agent_id, exc.attempts, exc.last_error,
        )
        _fire_replay_event(
            job_id, request.agent_id, "ingest_failed",
            {
                "reason": "extraction_failed",
                "attempts": exc.attempts,
                "last_error": exc.last_error,
            },
        )
        try:
            import app.main as _main
            svc = getattr(_main, "_analytics_service", None)
            if svc and svc._ready:
                svc.record_extraction_failed(
                    agent_id=request.agent_id,
                    source="stream",
                    text_length=len(request.text),
                    attempts=exc.attempts,
                    last_error=exc.last_error or str(exc),
                )
        except Exception:
            pass
        await ws_manager.broadcast(
            request.agent_id,
            {
                "type": "error",
                "job_id": job_id,
                "message": (
                    f"Extraction failed after {exc.attempts} attempts: "
                    f"{exc.last_error or exc}"
                ),
                "reason": "extraction_failed",
            },
        )
    except Exception as exc:
        logger.exception("Stream ingest job %s failed", job_id)
        await ws_manager.broadcast(
            request.agent_id,
            {"type": "error", "job_id": job_id, "message": str(exc)},
        )


@router.post("/stream", status_code=202)
async def ingest_text_stream(request: IngestRequest, background_tasks: BackgroundTasks):
    """
    Async streaming ingest: returns 202 immediately, runs extraction in background.
    Results are pushed via WebSocket at ws://localhost:8000/ws/{agent_id}.
    """
    job_id = str(uuid.uuid4())
    background_tasks.add_task(_run_stream_ingest, request, job_id)
    logger.info("Queued stream ingest job %s for agent %s", job_id, request.agent_id)
    return {"job_id": job_id, "agent_id": request.agent_id, "status": "processing"}


@router.post("/file/stream", status_code=202)
async def ingest_file_stream(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    agent_id: str = Form(default="default"),
    source: Optional[str] = Form(None),
):
    """Upload a .txt file for async streaming ingestion."""
    if not file.filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt files are supported")
    content = await file.read()
    text = content.decode("utf-8")
    req = IngestRequest(text=text, agent_id=agent_id, source=source or file.filename)
    job_id = str(uuid.uuid4())
    background_tasks.add_task(_run_stream_ingest, req, job_id)
    return {"job_id": job_id, "agent_id": agent_id, "status": "processing"}


@router.post(
    "/file",
    response_model=IngestSyncResponse,
    summary="Upload a single file for synchronous ingestion (PDF, DOCX, PPTX, HTML, MD, TXT)",
    description=(
        "Thin wrapper around the Upload Connector. Accepts any supported document format — "
        "PDF, DOCX, PPTX, HTML, Markdown, and plain text — via `multipart/form-data`.\n\n"
        "Internally delegates to `UploadConnector` → `parser_service.parse()` → "
        "full extraction pipeline. The response shape is identical to `/ingest/sync` "
        "for backward compatibility."
    ),
)
async def ingest_file_sync(
    file: UploadFile = File(...),
    agent_id: str = Form(default="default"),
    source: Optional[str] = Form(None),
):
    """
    Legacy single-file upload — now delegates to the Upload Connector so all
    supported MIME types (PDF, DOCX, PPTX, HTML, Markdown, TXT) are accepted.
    Previously restricted to .txt; that guard is removed here.
    """
    content = await file.read()
    filename = file.filename or "upload"
    mime_type = file.content_type or "text/plain"

    records = []
    async for record in _upload_connector.run(
        agent_id,
        RunState(connector_id=_upload_connector.connector_id),
        files=[(filename, content, mime_type)],
    ):
        records.append(record)

    if not records:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Could not extract text from '{filename}'. "
                "Check that the file is not empty, password-protected, or an unsupported format."
            ),
        )

    # Single file → single record; delegate to the established sync pipeline.
    record = records[0]
    req = IngestRequest(
        text=record.text,
        agent_id=agent_id,
        source=source or record.source_uri,
    )
    return await ingest_text_sync(req)


# ---------------------------------------------------------------------------
# Multi-file upload — (Document Connector)
# ---------------------------------------------------------------------------

@router.post(
    "/files",
    response_model=IngestSyncResponse,
    summary="Upload multiple files for synchronous ingestion",
    description=(
        "Batch document upload. Accepts up to **20 files** per request in any "
        "supported format (PDF, DOCX, PPTX, HTML, Markdown, TXT).\n\n"
        "Each file is parsed independently by the Upload Connector. A parse failure "
        "on one file is routed to the DLQ and does not abort the remaining files.\n\n"
        "All successfully parsed files are extracted **concurrently** via "
        "`asyncio.gather` — a batch of N files completes in roughly the time of one. "
        "Counts in the response are aggregated across all files."
    ),
)
async def ingest_files_multi(
    files: List[UploadFile] = File(...),
    agent_id: str = Form(default="default"),
):
    """Upload multiple files and ingest all of them in one request."""
    if len(files) > 20:
        raise HTTPException(
            status_code=422,
            detail="Maximum 20 files per request. Split your upload into smaller batches.",
        )

    t0 = time.perf_counter()

    # Read all files into memory before streaming the async generator so we
    # release the HTTP connection as early as possible.
    file_tuples: list[tuple[str, bytes, str]] = []
    for uf in files:
        content = await uf.read()
        file_tuples.append((
            uf.filename or "upload",
            content,
            uf.content_type or "text/plain",
        ))

    # Parse all files through the Upload Connector.
    records = []
    async for record in _upload_connector.run(
        agent_id,
        RunState(connector_id=_upload_connector.connector_id),
        files=file_tuples,
    ):
        records.append(record)

    if not records:
        raise HTTPException(
            status_code=422,
            detail="No text could be extracted from any of the uploaded files.",
        )

    logger.info(
        "ingest_files_multi: agent=%s files=%d parsed=%d — running concurrent extraction",
        agent_id, len(file_tuples), len(records),
    )

    # Run the full ingest pipeline for each record concurrently.
    ingest_requests = [
        IngestRequest(text=r.text, agent_id=agent_id, source=r.source_uri)
        for r in records
    ]
    responses: list[IngestSyncResponse] = await asyncio.gather(
        *[ingest_text_sync(req) for req in ingest_requests]
    )

    # Aggregate counts and node/edge lists across all per-file responses.
    latency_ms = (time.perf_counter() - t0) * 1000
    record_connector_run(_upload_connector.connector_id, records_processed=len(records))
    return IngestSyncResponse(
        success=True,
        agent_id=agent_id,
        nodes_created=sum(r.nodes_created for r in responses),
        nodes_merged=sum(r.nodes_merged for r in responses),
        edges_created=sum(r.edges_created for r in responses),
        edges_merged=sum(r.edges_merged for r in responses),
        nodes=[n for r in responses for n in r.nodes],
        edges=[e for r in responses for e in r.edges],
        latency_ms=round(latency_ms, 2),
    )


# ---------------------------------------------------------------------------
# URL ingest — (URL Connector with incremental sync)
# ---------------------------------------------------------------------------

class _IngestURLRequest(_BaseModel):
    urls: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=20,
            description="Absolute HTTP/HTTPS URLs to fetch and ingest (max 20 per request).",
        ),
    ]
    agent_id: str = Field(default="default", description="Target agent namespace.")


@router.post(
    "/url",
    response_model=IngestSyncResponse,
    summary="Fetch one or more URLs and ingest their content",
    description=(
        "Fetches each URL via `httpx` (15 s timeout) and parses the response with "
        "Trafilatura for precision boilerplate removal.\n\n"
        "**Incremental sync:** conditional HTTP headers (`If-None-Match` / "
        "`If-Modified-Since`) are sent on repeat calls for the same URL. "
        "A `304 Not Modified` response yields zero records for that URL with no "
        "graph write — perfect for scheduled re-crawls.\n\n"
        "RunState (ETags / Last-Modified values) is stored in-process and survives "
        "across requests for the lifetime of the server. Persist to Redis for "
        "multi-process or restart-durable incremental sync.\n\n"
        "Failed URLs (4xx, 5xx, timeout, parse error) are routed to the DLQ and do "
        "not abort the remaining URLs in the batch."
    ),
)
async def ingest_url(request: _IngestURLRequest):
    """Fetch URLs, parse with Trafilatura, ingest via the standard extraction pipeline."""
    t0 = time.perf_counter()

    # Retrieve (or create) the persistent RunState for this agent so ETags
    # from previous calls are forwarded as conditional request headers.
    run_state = await _get_run_state(_url_connector.connector_id, request.agent_id)

    records = []
    async for record in _url_connector.run(
        request.agent_id,
        run_state,
        urls=request.urls,
    ):
        records.append(record)

    # Persist updated ETags / Last-Modified entries for the next call.
    await _save_run_state(
        _url_connector.connector_id, request.agent_id, run_state,
    )

    # All URLs returned 304 or failed — return a zero-count 200 (not an error;
    # the caller's data is simply already up to date).
    if not records:
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "ingest_url: agent=%s urls=%d — all cached/failed, nothing to ingest (%.1fms)",
            request.agent_id, len(request.urls), latency_ms,
        )
        return IngestSyncResponse(
            success=True,
            agent_id=request.agent_id,
            nodes_created=0,
            nodes_merged=0,
            edges_created=0,
            edges_merged=0,
            nodes=[],
            edges=[],
            latency_ms=round(latency_ms, 2),
        )

    logger.info(
        "ingest_url: agent=%s urls=%d fetched=%d — running concurrent extraction",
        request.agent_id, len(request.urls), len(records),
    )

    ingest_requests = [
        IngestRequest(text=r.text, agent_id=request.agent_id, source=r.source_uri)
        for r in records
    ]
    responses: list[IngestSyncResponse] = await asyncio.gather(
        *[ingest_text_sync(req) for req in ingest_requests]
    )

    latency_ms = (time.perf_counter() - t0) * 1000
    record_connector_run(_url_connector.connector_id, records_processed=len(records))
    return IngestSyncResponse(
        success=True,
        agent_id=request.agent_id,
        nodes_created=sum(r.nodes_created for r in responses),
        nodes_merged=sum(r.nodes_merged for r in responses),
        edges_created=sum(r.edges_created for r in responses),
        edges_merged=sum(r.edges_merged for r in responses),
        nodes=[n for r in responses for n in r.nodes],
        edges=[e for r in responses for e in r.edges],
        latency_ms=round(latency_ms, 2),
    )


# ---------------------------------------------------------------------------
# MCP — Model Context Protocol resource ingest
# Streams resources from any MCP-compliant server (stdio or SSE) into the
# agent's knowledge graph through the standard extraction pipeline.
# ---------------------------------------------------------------------------


class _IngestMCPRequest(_BaseModel):
    agent_id: str = Field(default="default", description="Target agent namespace.")
    # stdio transport
    command: Optional[str] = Field(
        default=None,
        description=(
            "Executable for stdio transport (e.g. `uvx`, `node`). Mutually "
            "exclusive with `server_url`."
        ),
    )
    args: Optional[list[str]] = Field(
        default=None,
        description="Arguments to the stdio command (e.g. `[\"mcp-server-fetch\"]`).",
    )
    env: Optional[dict[str, str]] = Field(
        default=None,
        description="Environment overrides for the stdio subprocess.",
    )
    # SSE transport
    server_url: Optional[str] = Field(
        default=None,
        description=(
            "Absolute URL of an HTTP/SSE MCP server. Mutually exclusive "
            "with `command`."
        ),
    )
    headers: Optional[dict[str, str]] = Field(
        default=None,
        description="Extra HTTP headers for the SSE connection (auth tokens, etc.).",
    )
    source_label: Optional[str] = Field(
        default=None,
        description="Human label for provenance metadata on every ingested record.",
    )


@router.post(
    "/mcp",
    response_model=IngestSyncResponse,
    summary="Ingest resources from an MCP server (stdio or SSE)",
    description=(
        "Connects to a Model Context Protocol server, pages through "
        "`list_resources`, and ingests each resource whose content has "
        "changed since the last run for this agent.\n\n"
        "**Transports:** exactly one of `command` (stdio subprocess) or "
        "`server_url` (SSE) must be set.\n\n"
        "**Incremental sync:** identical to `/ingest/url` — RunState is "
        "stored per `(connector_id, agent_id)` in process. Resources whose "
        "`lastModified` matches the stored value are fast-skipped (no "
        "read). Otherwise SHA-256 hashing decides whether to ingest. \n\n"
        "Failed resources are routed to the DLQ; one bad resource never "
        "aborts the rest of the run."
    ),
)
async def ingest_mcp(request: _IngestMCPRequest):
    """Stream MCP resources, parse with parser_service, ingest via standard pipeline."""
    t0 = time.perf_counter()
    run_state = await _get_run_state(_mcp_connector.connector_id, request.agent_id)

    records = []
    async for record in _mcp_connector.run(
        request.agent_id,
        run_state,
        command=request.command,
        args=request.args,
        env=request.env,
        server_url=request.server_url,
        headers=request.headers,
        source_label=request.source_label,
    ):
        records.append(record)

    # Persist per-resource hashes / lastModified for the next call.
    await _save_run_state(
        _mcp_connector.connector_id, request.agent_id, run_state,
    )

    if not records:
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "ingest_mcp: agent=%s — no changed resources (%.1fms)",
            request.agent_id, latency_ms,
        )
        return IngestSyncResponse(
            success=True,
            agent_id=request.agent_id,
            nodes_created=0,
            nodes_merged=0,
            edges_created=0,
            edges_merged=0,
            nodes=[],
            edges=[],
            latency_ms=round(latency_ms, 2),
        )

    logger.info(
        "ingest_mcp: agent=%s changed=%d — running concurrent extraction",
        request.agent_id, len(records),
    )

    ingest_requests = [
        IngestRequest(text=r.text, agent_id=request.agent_id, source=r.source_uri)
        for r in records
    ]
    responses: list[IngestSyncResponse] = await asyncio.gather(
        *[ingest_text_sync(req) for req in ingest_requests]
    )

    latency_ms = (time.perf_counter() - t0) * 1000
    record_connector_run(_mcp_connector.connector_id, records_processed=len(records))
    return IngestSyncResponse(
        success=True,
        agent_id=request.agent_id,
        nodes_created=sum(r.nodes_created for r in responses),
        nodes_merged=sum(r.nodes_merged for r in responses),
        edges_created=sum(r.edges_created for r in responses),
        edges_merged=sum(r.edges_merged for r in responses),
        nodes=[n for r in responses for n in r.nodes],
        edges=[e for r in responses for e in r.edges],
        latency_ms=round(latency_ms, 2),
    )


# ---------------------------------------------------------------------------
# SQL — stream rows from any SQLAlchemy DSN
# ---------------------------------------------------------------------------


class _IngestSQLRequest(_BaseModel):
    agent_id: str = Field(default="default", description="Target agent namespace.")
    dsn: str = Field(
        ...,
        description=(
            "SQLAlchemy DSN (e.g. `postgresql+asyncpg://user:pass@host/db`, "
            "`sqlite+aiosqlite:///path/to/db`). Plaintext for v1 — secrets-"
            "service backed DSNs are a follow-up."
        ),
    )
    query: str = Field(
        ...,
        description=(
            "Arbitrary SELECT/WITH query. Wrapped in a subquery before pagination "
            "so it can include GROUP BY / HAVING / ORDER BY / LIMIT."
        ),
    )
    cursor_column: str = Field(default="id", description="Monotonic cursor column.")
    content_columns: Optional[list[str]] = Field(
        default=None,
        description="Columns concatenated into record.text. Empty/None = all non-cursor columns.",
    )
    title_column: Optional[str] = Field(default=None)
    id_column: Optional[str] = Field(default=None)
    batch_size: int = Field(default=1000, ge=1, le=50_000)
    initial_cursor: Optional[Any] = Field(
        default=None,
        description="Resume value. None = use stored RunState cursor or sentinel.",
    )
    source_label: str = Field(default="sql")


@router.post(
    "/sql",
    response_model=IngestSyncResponse,
    summary="Stream rows from any SQLAlchemy DSN and ingest each row as a record",
    description=(
        "Connects to the supplied DSN, runs the wrapped query with cursor-"
        "based pagination, and ingests each row as a record. Per-stream "
        "cursor is persisted in RunState (`source_states[\"__sql_cursor__\"]`) "
        "so subsequent calls resume from where the previous one left off.\n\n"
        "**Supported dialects:** PostgreSQL (`postgresql+asyncpg://`), "
        "SQLite (`sqlite+aiosqlite:///`), and any other dialect SQLAlchemy "
        "supports as an async driver.\n\n"
        "**Security:** for v1, the DSN is plaintext on the wire. For "
        "scheduled / persisted SQL connectors, store credentials through "
        "SecretsService (separate follow-up).\n\n"
        "Per-row exceptions are logged and skipped (single bad row never "
        "aborts the stream)."
    ),
)
async def ingest_sql(request: _IngestSQLRequest):
    """Stream SQL rows, ingest via the standard extraction pipeline."""
    t0 = time.perf_counter()
    run_state = await _get_run_state(_sql_connector.connector_id, request.agent_id)

    records = []
    async for record in _sql_connector.run(
        request.agent_id,
        run_state,
        dsn=request.dsn,
        query=request.query,
        cursor_column=request.cursor_column,
        content_columns=request.content_columns,
        title_column=request.title_column,
        id_column=request.id_column,
        batch_size=request.batch_size,
        initial_cursor=request.initial_cursor,
        source_label=request.source_label,
    ):
        records.append(record)

    # Persist updated cursor for incremental sync on the next call.
    await _save_run_state(
        _sql_connector.connector_id, request.agent_id, run_state,
    )

    if not records:
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "ingest_sql: agent=%s — no rows after cursor (%.1fms)",
            request.agent_id, latency_ms,
        )
        return IngestSyncResponse(
            success=True,
            agent_id=request.agent_id,
            nodes_created=0,
            nodes_merged=0,
            edges_created=0,
            edges_merged=0,
            nodes=[],
            edges=[],
            latency_ms=round(latency_ms, 2),
        )

    logger.info(
        "ingest_sql: agent=%s rows=%d — running concurrent extraction",
        request.agent_id, len(records),
    )

    ingest_requests = [
        IngestRequest(text=r.text, agent_id=request.agent_id, source=r.source_uri)
        for r in records
    ]
    responses: list[IngestSyncResponse] = await asyncio.gather(
        *[ingest_text_sync(req) for req in ingest_requests]
    )

    latency_ms = (time.perf_counter() - t0) * 1000
    record_connector_run(_sql_connector.connector_id, records_processed=len(records))
    return IngestSyncResponse(
        success=True,
        agent_id=request.agent_id,
        nodes_created=sum(r.nodes_created for r in responses),
        nodes_merged=sum(r.nodes_merged for r in responses),
        edges_created=sum(r.edges_created for r in responses),
        edges_merged=sum(r.edges_merged for r in responses),
        nodes=[n for r in responses for n in r.nodes],
        edges=[e for r in responses for e in r.edges],
        latency_ms=round(latency_ms, 2),
    )


# ---------------------------------------------------------------------------
# BYOV — Bring Your Own Vectors
# Clients supply a fully-formed GraphPayload instead of raw text, bypassing
# the LLM extraction phase entirely.  Pre-computed embeddings on Node objects
# are respected by EntityResolver (BYOV support).
# ---------------------------------------------------------------------------

class _IngestGraphRequest(GraphPayload):
    """
    GraphPayload extended with routing metadata for direct graph ingest.
    ``agent_id`` determines which agent's namespace receives the nodes/edges.
    """
    agent_id: str = Field(default="default", description="Target agent namespace.")


@router.post(
    "/graph",
    response_model=IngestSyncResponse,
    summary="Direct GraphPayload ingest (BYOV)",
    description=(
        "Push a fully-formed `GraphPayload` directly into the knowledge graph, "
        "**bypassing the LLM text-extraction phase entirely**.\n\n"
        "This endpoint is designed for clients that already hold structured entity/relation "
        "data or have pre-computed their own embeddings (Bring Your Own Vectors).\n\n"
        "**BYOV behaviour** (inherited from `EntityResolver`):\n"
        "- Node carries a valid embedding of the correct dimension → preserved as-is (zero embedding cost).\n"
        "- Node carries an embedding with the wrong dimension → HTTP 422 raised immediately.\n"
        "- Node has no embedding → embedded automatically by the service.\n\n"
        "Deduplication against the existing agent graph is always performed via "
        "`EntityResolver` before writing to Neo4j."
    ),
)
async def ingest_graph(request: _IngestGraphRequest) -> IngestSyncResponse:
    t0 = time.perf_counter()

    try:
        resolver = _get_resolver()
        graph = _get_graph_service()

        # Stamp agent_id onto every node and edge supplied by the client.
        for node in request.nodes:
            node.agent_id = request.agent_id
        for edge in request.edges:
            edge.agent_id = request.agent_id

        payload = GraphPayload(nodes=request.nodes, edges=request.edges)

        # Resolve / deduplicate; raises HTTP 422 on embedding dimension mismatch.
        resolved = await resolver.resolve(
            payload, request.agent_id, graph, caller_context="api"
        )

        result = await graph.write_graph(resolved, request.agent_id)

        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "BYOV graph ingest: agent=%s nodes_created=%d nodes_merged=%d "
            "edges_created=%d latency=%.1fms",
            request.agent_id,
            result.nodes_created,
            result.nodes_merged,
            result.edges_created,
            latency_ms,
        )

        slim_nodes = [
            SlimNode(
                id=n.id,
                label=n.label,
                type=n.type,
                properties={k: v for k, v in (n.properties or {}).items() if k != "embedding"},
                agent_id=n.agent_id,
            )
            for n in resolved.nodes
        ]
        slim_edges = [
            SlimEdge(
                id=e.id,
                source=e.source_id,
                target=e.target_id,
                relation=e.relation,
                agent_id=e.agent_id,
            )
            for e in resolved.edges
        ]

        return IngestSyncResponse(
            success=True,
            agent_id=request.agent_id,
            nodes_created=result.nodes_created,
            nodes_merged=result.nodes_merged,
            edges_created=result.edges_created,
            edges_merged=result.edges_merged,
            nodes=slim_nodes,
            edges=slim_edges,
            latency_ms=round(latency_ms, 2),
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error during BYOV graph ingest")
        raise HTTPException(status_code=500, detail=str(exc))


class _BatchBody(_BaseModel):
    agent_id: str = Field(default="default", description="Target agent namespace.")
    payloads: Annotated[
        list[GraphPayload],
        Field(max_length=100, description="List of GraphPayload objects to ingest (max 100)."),
    ]


class _BatchIngestResponse(_BaseModel):
    success: bool
    agent_id: str
    payloads_processed: int
    nodes_created: int
    nodes_merged: int
    edges_created: int
    edges_merged: int
    latency_ms: float


@router.post(
    "/graph/batch",
    response_model=_BatchIngestResponse,
    summary="Batch GraphPayload ingest (BYOV)",
    description=(
        "Push up to **100** `GraphPayload` objects in a single request, "
        "bypassing the LLM extraction phase.\n\n"
        "All payloads are resolved concurrently via `asyncio.gather` — each "
        "payload's deduplication and (optional) embedding call runs in parallel, "
        "so a batch of N payloads completes in roughly the time of one rather "
        "than N × one.\n\n"
        "After resolution the entire batch is written to Neo4j in a **single "
        "transaction** via `GraphService.write_graph_batch()` for maximum "
        "throughput.\n\n"
        "**Limits:** maximum 100 payloads per request (HTTP 422 if exceeded).\n\n"
        "**BYOV behaviour** is identical to `POST /ingest/graph`: correct-dimension "
        "embeddings are preserved; wrong-dimension embeddings raise HTTP 422 immediately."
    ),
)
async def ingest_graph_batch(body: _BatchBody) -> _BatchIngestResponse:
    t0 = time.perf_counter()

    if not body.payloads:
        raise HTTPException(status_code=422, detail="payloads must not be empty.")

    try:
        resolver = _get_resolver()
        graph = _get_graph_service()

        # Stamp agent_id onto all nodes/edges in every payload.
        for payload in body.payloads:
            for node in payload.nodes:
                node.agent_id = body.agent_id
            for edge in payload.edges:
                edge.agent_id = body.agent_id

        # Resolve all payloads concurrently — N I/O-bound coroutines in parallel.
        resolved_payloads: list[GraphPayload] = await asyncio.gather(
            *[
                resolver.resolve(p, body.agent_id, graph, caller_context="api")
                for p in body.payloads
            ]
        )

        # Write all resolved payloads in a single Neo4j transaction.
        items = [(p, body.agent_id) for p in resolved_payloads]
        result = await graph.write_graph_batch(items)

        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "BYOV batch ingest: agent=%s payloads=%d nodes_created=%d "
            "nodes_merged=%d edges_created=%d latency=%.1fms",
            body.agent_id,
            len(body.payloads),
            result.nodes_created,
            result.nodes_merged,
            result.edges_created,
            latency_ms,
        )

        return _BatchIngestResponse(
            success=True,
            agent_id=body.agent_id,
            payloads_processed=len(resolved_payloads),
            nodes_created=result.nodes_created,
            nodes_merged=result.nodes_merged,
            edges_created=result.edges_created,
            edges_merged=result.edges_merged,
            latency_ms=round(latency_ms, 2),
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error during BYOV batch graph ingest")
        raise HTTPException(status_code=500, detail=str(exc))
