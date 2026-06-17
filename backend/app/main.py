"""
SpAIder FastAPI application entry point.
Handles lifespan (startup/shutdown), middleware, routing, and health checks.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.config import settings
from app.logging_config import configure_logging
from app.middleware.logging_middleware import RequestLoggingMiddleware
from app.services.graph_service import VectorIndexUnavailableError

configure_logging()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Neo4j Graph Migrations
# ---------------------------------------------------------------------------


async def run_graph_migrations(driver) -> None:
    """
    Idempotent startup migrations applied once per boot.

    Migration 1 — SystemSettings singleton:
        Ensures the global settings node exists with all required fields
        including the new `engine_version` property (defaults to "v1").

    Migration 2 — Synapse weights:
        Backfills `utility_weight = 1.0` on every relationship that was
        created before the V2 Cognitive Graph feature existed.  Safe to
        re-run: the WHERE guard prevents touching already-weighted edges.

    Migration 3 — Edge id backfill:
        Assigns a `randomUUID()` to any RELATION edge missing an `id` (a
        legacy ingest path created edges without one). Without this the
        multiverse graph response fails Edge validation. Safe to re-run.

    Migration 4 — Promote description/source_text out of the properties JSON:
        Older ingest paths stored both only inside the `properties` JSON
        string, leaving the top-level columns NULL — which made the fulltext
        index blind to the fact text and crippled retrieval recall. Backfills
        the columns via APOC JSON parsing; the WHERE guard keeps it a no-op
        once applied. Requires APOC (shipped in the compose stack); fails
        soft like every other migration if APOC is absent.
    """
    migrations = [
        (
            "SystemSettings bootstrap (engine_version)",
            """
            MERGE (s:SystemSettings {id: "global"})
            ON CREATE SET
                s.auto_reflection = false,
                s.engine_version  = "v1"
            ON MATCH SET
                s.engine_version = coalesce(s.engine_version, "v1")
            """,
            {},
        ),
        (
            "Backfill utility_weight on existing edges",
            """
            MATCH ()-[r]->()
            WHERE r.utility_weight IS NULL
            SET r.utility_weight = 1.0
            """,
            {},
        ),
        (
            "Backfill id on legacy RELATION edges",
            """
            MATCH ()-[r:RELATION]->()
            WHERE r.id IS NULL
            SET r.id = randomUUID()
            """,
            {},
        ),
        (
            "Promote description/source_text from properties JSON",
            """
            MATCH (n:SpaiderNode)
            WHERE n.properties IS NOT NULL
              AND (n.description IS NULL OR n.source_text IS NULL)
            WITH n, apoc.convert.fromJsonMap(n.properties) AS p
            WHERE p IS NOT NULL
            SET n.description = coalesce(n.description, p.description),
                n.source_text = coalesce(n.source_text, p.source_text)
            """,
            {},
        ),
    ]

    async with driver.session() as session:
        for name, cypher, params in migrations:
            try:
                await session.run(cypher, **params)
                logger.info("Migration OK: %s", name)
            except Exception as exc:
                # Migrations are non-fatal — log and continue
                logger.error("Migration FAILED (%s): %s", name, exc)


async def check_embedding_dimension_consistency(driver) -> None:
    """guard against silent vector-index corruption when
    operators switch embedding providers without re-seeding the agent.

    Probes the graph for at least one existing embedding and compares its
    dimensionality against ``settings.embedding_dimensions``. Logs a
    loud ERROR on mismatch but does NOT crash the boot — the operator
    sees the warning and decides whether to revert env or rotate agents.

    No-op when the graph has no embeddings yet (fresh deployment).
    """
    expected = settings.embedding_dimensions
    try:
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (n:SpaiderNode)
                WHERE n.embedding IS NOT NULL
                RETURN size(n.embedding) AS dim, count(n) AS total
                LIMIT 1
                """
            )
            record = await result.single()
            if not record or record["total"] == 0:
                logger.info(
                    "Embedding dimension check: no existing embeddings; "
                    "fresh deployment will use %d-dim vectors.", expected,
                )
                return
            actual = record["dim"]
            if actual != expected:
                logger.error(
                    "EMBEDDING_DIMENSIONS=%d but existing graph has %d-dim vectors. "
                    "Mixing dimensions corrupts the Neo4j vector index — search "
                    "results will silently degrade. Either revert the env var to %d, "
                    "or rotate the agent and re-seed with the new dimension.",
                    expected, actual, actual,
                )
            else:
                logger.info(
                    "Embedding dimension check OK: existing %d-dim vectors match "
                    "EMBEDDING_DIMENSIONS=%d.", actual, expected,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Embedding dimension check skipped: %s", exc)


# ---------------------------------------------------------------------------
# Service singletons (initialised in lifespan)
# ---------------------------------------------------------------------------

_graph_service = None
_kafka_producer = None
_redis_client = None
_analytics_service = None
_swarm_listener_task = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    global _graph_service, _kafka_producer, _redis_client, _analytics_service, _swarm_listener_task

    # --- Startup ---

    # 1. Neo4j
    try:
        from app.services.graph_service import GraphService
        _graph_service = GraphService()
        await _graph_service.initialize()
        logger.info("Neo4j connected and initialised")
    except Exception as exc:
        logger.error("Neo4j initialisation failed: %s", exc)

    # 1a. Graph migrations (non-fatal — runs after Neo4j is ready)
    if _graph_service is not None:
        try:
            await run_graph_migrations(_graph_service._driver)
            logger.info("Graph migrations complete")
        except Exception as exc:
            logger.error("Graph migration runner failed: %s", exc)

    # 1b. Embedding dimension consistency check. Catches the
    # operator-switched-providers-without-reseed corruption case before it
    # produces silently-wrong search results.
    if _graph_service is not None:
        try:
            await check_embedding_dimension_consistency(_graph_service._driver)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Embedding dimension check failed unexpectedly: %s", exc)

    # 2. Redis
    try:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await _redis_client.ping()
        logger.info("Redis connected at %s", settings.redis_url)
    except Exception as exc:
        logger.warning("Redis connection failed: %s", exc)
        _redis_client = None

    # 2b. Redis Stream + Consumer Group bootstrap (idempotent)
    if _redis_client is not None:
        try:
            from app.services.redis_service import initialize_stream
            await initialize_stream(_redis_client)
        except Exception as exc:
            logger.warning("Redis Stream init failed (non-fatal): %s", exc)

    # 3. Kafka producer
    try:
        from app.services.kafka_producer import KafkaProducerService
        _kafka_producer = KafkaProducerService()
        await _kafka_producer.start()
        logger.info("Kafka producer started")
    except Exception as exc:
        logger.warning("Kafka producer start failed (async ingest unavailable): %s", exc)
        _kafka_producer = None

    # 4. ClickHouse analytics
    try:
        from app.services.analytics_service import AnalyticsService
        _analytics_service = AnalyticsService()
        await _analytics_service.initialize()
        # expose globally so routes can import it
        import app.main as _self
        _self._analytics_service = _analytics_service
    except Exception as exc:
        logger.warning("ClickHouse analytics init failed (non-fatal): %s", exc)
        _analytics_service = None

    logger.info("%s started in %s mode", settings.app_name, settings.environment)

    # 4.5. Postgres — create connector tables (idempotent)
    try:
        from app.db.postgres import init_db
        await init_db()
        logger.info("PostgreSQL connector tables ready")
    except Exception as exc:
        logger.warning("PostgreSQL init failed (non-fatal): %s", exc)

    # 5. Connector scheduler — polls connector configs every 5 min for
    #    SQL/URL ingest dispatch (non-blocking). NOT memory consolidation —
    #    that is the Airflow ``graph_maintenance`` DAG.
    from app.scheduler import start_connector_scheduler
    _scheduler_task = asyncio.create_task(start_connector_scheduler(), name="connector-scheduler")
    logger.info("Connector scheduler started")

    # 6. Swarm Listener — Stigmergic Event Consumer (non-blocking)
    # Only started when both Redis and Neo4j are available, as the listener
    # depends on both.  Missing either is non-fatal: the app runs without the
    # swarm routing layer (pheromones accumulate in Neo4j until next boot).
    if _redis_client is not None and _graph_service is not None:
        from app.workers.swarm_listener import swarm_listener
        _swarm_listener_task = asyncio.create_task(
            swarm_listener(
                redis_client=_redis_client,
                graph_service=_graph_service,
            ),
            name="swarm-listener",
        )
        logger.info("Swarm listener started — subscribed to Redis channel 'swarm_events'")
    else:
        logger.warning(
            "Swarm listener NOT started (Redis=%s, Neo4j=%s) — "
            "event-driven routing unavailable",
            "ok" if _redis_client else "unavailable",
            "ok" if _graph_service else "unavailable",
        )

    yield

    # --- Shutdown ---

    # Cancel background tasks in reverse startup order
    if _swarm_listener_task is not None:
        _swarm_listener_task.cancel()
        await asyncio.gather(_swarm_listener_task, return_exceptions=True)
        logger.info("Swarm listener stopped")

    _scheduler_task.cancel()
    await asyncio.gather(_scheduler_task, return_exceptions=True)
    logger.info("Background scheduler stopped")

    if _analytics_service is not None:
        try:
            await _analytics_service.close()
        except Exception:
            pass

    if _kafka_producer is not None:
        try:
            await _kafka_producer.stop()
            logger.info("Kafka producer stopped")
        except Exception as exc:
            logger.warning("Kafka producer stop error: %s", exc)

    try:
        from app.api.v1.replay import shutdown_replay_service
        await shutdown_replay_service()
        logger.info("Replay service stopped")
    except Exception as exc:
        logger.warning("Replay service stop error: %s", exc)

    if _redis_client is not None:
        try:
            await _redis_client.aclose()
            logger.info("Redis connection closed")
        except Exception as exc:
            logger.warning("Redis close error: %s", exc)

    if _graph_service is not None:
        try:
            await _graph_service.close()
            logger.info("Neo4j connection closed")
        except Exception as exc:
            logger.warning("Neo4j close error: %s", exc)

    logger.info("%s shutdown complete", settings.app_name)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


app = FastAPI(
    title=settings.app_name,
    description=(
        "SpAIder — A multi-agent semantic knowledge graph platform powered by LLMs and Neo4j. "
        "Ingest unstructured text, build a persistent knowledge graph, query it in natural language, "
        "and synthesise fine-tuning datasets for downstream models."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# Outermost middleware — wraps CORS so every request (including OPTIONS
# preflights and auth failures) gets a request_id header + structured log.
app.add_middleware(RequestLoggingMiddleware)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(api_router, prefix="/api/v1")

# MCP server sub-app — mounted directly on the FastAPI app because
# `APIRouter.include_router(prefix=...)` doesn't propagate Starlette
# `Mount` nodes (only `APIRoute`s). Result: putting the mount inside
# api_router silently 404s. See app/api/v1/mcp_server.py for the rest
# of the rationale.
#
# Gated on `settings.spaider_mcp_enabled` so deployments that don't want
# the MCP surface (compliance / policy / "REST only") can disable it
# without code changes.
if settings.spaider_mcp_enabled:
    from app.api.v1.mcp_server import mcp_app
    app.mount("/api/v1/mcp", mcp_app)
    logger.info("MCP server mounted at /api/v1/mcp")
else:
    logger.info("MCP server disabled via settings.spaider_mcp_enabled=False")

# WebSocket routes — no /api/v1 prefix so frontend connects at ws://host/ws/{agent_id}
from app.api.v1.ws import router as _ws_router  # noqa: E402  (mounted after app init)

app.include_router(_ws_router)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"], summary="Service health check")
async def health() -> dict[str, Any]:
    """
    Check the liveness and readiness of all backend services.
    Returns a per-service status dict and an overall healthy flag.
    """
    status: dict[str, Any] = {
        "app": settings.app_name,
        "version": "0.1.0",
        "environment": settings.environment,
        "services": {},
    }

    # Neo4j
    neo4j_ok = False
    try:
        if _graph_service is not None:
            await _graph_service.ping()
            neo4j_ok = True
    except Exception as exc:
        logger.debug("Neo4j health check failed: %s", exc)
    status["services"]["neo4j"] = "ok" if neo4j_ok else "unavailable"

    # Redis
    redis_ok = False
    try:
        if _redis_client is not None:
            await _redis_client.ping()
            redis_ok = True
    except Exception as exc:
        logger.debug("Redis health check failed: %s", exc)
    status["services"]["redis"] = "ok" if redis_ok else "unavailable"

    # Kafka
    kafka_ok = _kafka_producer is not None
    status["services"]["kafka"] = "ok" if kafka_ok else "unavailable"

    # Neo4j vector index — surfaced separately so operators can tell
    # "DB reachable but semantic search broken" apart from "DB down".
    vector_index_ok = bool(
        _graph_service is not None and _graph_service.vector_index_available
    )
    status["services"]["vector_index"] = "ok" if vector_index_ok else "unavailable"

    all_ok = neo4j_ok and redis_ok
    status["healthy"] = all_ok

    return status


@app.get(
    "/health/embedding",
    tags=["health"],
    summary="Embedding-dimension consistency check",
)
async def health_embedding() -> dict[str, Any]:
    """On-demand check that every stored embedding matches
    ``EMBEDDING_DIMENSIONS``. Kept off the frequent ``/health`` path
    because it scans for distinct vector sizes. Used by ``spaider doctor``.

    ``consistent`` is true when the graph has no embeddings yet, or when the
    only dimension present equals the configured one. A mismatch means vector
    search silently degrades until the offending data is re-seeded.
    """
    expected = settings.embedding_dimensions
    report: dict[str, Any] = {
        "expected_dims": expected,
        "present_dims": [],
        "embedded_nodes": 0,
        "consistent": True,
    }
    if _graph_service is None:
        report["consistent"] = False
        report["error"] = "graph service unavailable"
        return report
    try:
        async with _graph_service._driver.session() as session:
            result = await session.run(
                """
                MATCH (n:SpaiderNode)
                WHERE n.embedding IS NOT NULL
                RETURN size(n.embedding) AS dim, count(n) AS cnt
                ORDER BY cnt DESC
                """
            )
            rows = await result.data()
    except Exception as exc:  # noqa: BLE001
        report["consistent"] = False
        report["error"] = str(exc)
        return report

    report["present_dims"] = [r["dim"] for r in rows]
    report["embedded_nodes"] = sum(r["cnt"] for r in rows)
    report["consistent"] = not rows or report["present_dims"] == [expected]
    return report


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(VectorIndexUnavailableError)
async def vector_index_unavailable_handler(
    request: Request, exc: VectorIndexUnavailableError
) -> JSONResponse:
    logger.error(
        "Vector index unavailable on %s %s — returning 503",
        request.method, request.url,
    )
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc)
            or "Semantic search is unavailable because the Neo4j "
            "vector index is missing. Check /health.",
            "type": "VectorIndexUnavailableError",
        },
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc), "type": "ValueError"})


@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc), "type": "PermissionError"})


@app.exception_handler(KeyError)
async def key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"detail": f"Resource not found: {exc}", "type": "KeyError"},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred.", "type": type(exc).__name__},
    )
