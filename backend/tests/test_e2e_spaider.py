"""
SpAIder E2E Stress-Test — 3-Phase Production Readiness Suite
-------------------------------------------------------------
Runs against the LIVE local stack (Docker Compose up required).

Phase 1 · Swarm Pulse       — live worker heartbeat health check
Phase 2 · Diplomat Protocol — clearance-level firewall (Zero Trust)
Phase 3 · Operation Matrix  — SSE event stream + pheromone pipeline
                              (routing → lock → success within 10 s)

No mocks. No stubs. Every assertion hits the real service.

Prerequisites
-------------
- docker compose up  (all services healthy)
- At least one spaider-backend-worker container running
- pytest-asyncio 0.24+, httpx 0.27+, neo4j 5.x, redis-py 5.x installed

Run
---
    cd backend
    pytest tests/test_e2e_spaider.py -v -s
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from neo4j import AsyncGraphDatabase

from app.config import settings

# ---------------------------------------------------------------------------
# Connectivity — resolved from app settings so the suite works both from the
# host (localhost) and inside the backend container (service-name hosts like
# neo4j: / redis:). Override the API base via SPAIDER_E2E_BASE_URL if needed.
# ---------------------------------------------------------------------------

BASE_URL    = os.environ.get("SPAIDER_E2E_BASE_URL", "http://localhost:8000")
REDIS_URL   = settings.redis_url
NEO4J_URI   = settings.neo4j_uri
NEO4J_AUTH  = (settings.neo4j_user, settings.neo4j_password)

# These exercise the real ingest -> extract -> query pipeline, which needs a
# live LLM. Skip when only a placeholder key is configured (e.g. CI without the
# OPENAI_API_KEY secret); they run locally and in CI when a real key is set.
_PLACEHOLDER_LLM_KEYS = {"", "sk-your-key-here", "sk-ci-dummy"}
pytestmark = pytest.mark.skipif(
    (settings.llm_api_key or "") in _PLACEHOLDER_LLM_KEYS,
    reason="needs a live LLM API key (set OPENAI_API_KEY / LLM_API_KEY)",
)

# Must match app/services/redis_service.py constants exactly.
PHEROMONE_STREAM = "pheromone_stream"
CONSUMER_GROUP   = "swarm_workers"

# ---------------------------------------------------------------------------
# Shared async fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Reusable httpx async client pointed at the live backend."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as c:
        yield c


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[aioredis.Redis]:
    """Direct Redis connection for pheromone publishing and state inspection."""
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _create_agent(
    client: httpx.AsyncClient,
    *,
    name: str,
    clearance_level: int,
) -> dict:
    """Create a SpAIder agent via the live API and return its record dict."""
    resp = await client.post(
        "/api/v1/agents",
        json={
            "name":            name,
            "permissions":     ["read", "write", "query"],
            "clearance_level": clearance_level,
        },
    )
    assert resp.status_code in (200, 201), (
        f"Agent creation failed ({resp.status_code}): {resp.text}"
    )
    payload = resp.json()
    # Backend may wrap in {"success": true, "agent": {...}} or return the
    # agent directly.  Handle both shapes.
    return payload.get("agent", payload)


async def _delete_agent(client: httpx.AsyncClient, agent_id: str) -> None:
    """Best-effort agent cleanup — never raises so cleanup never blocks tests."""
    try:
        await client.delete(f"/api/v1/agents/{agent_id}")
    except Exception:
        pass


# ===========================================================================
# PHASE 1 — The Swarm Pulse
# ===========================================================================


async def test_phase1_swarm_pulse(client: httpx.AsyncClient) -> None:
    """
    Verifies that ≥1 swarm worker is live and refreshing its Redis presence key.

    The swarm_listener writes ``agent_status:{agent_id} = "online"`` every 10 s
    with a 15 s TTL.  The /swarm/health endpoint scans for all matching keys.
    This test asserts the heartbeat pipeline is fully operational.
    """
    resp = await client.get("/api/v1/swarm/health")

    # ── Status code ──────────────────────────────────────────────────────────
    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}.\n"
        f"Body: {resp.text}\n"
        "Is spaider-backend-worker running?"
    )

    data = resp.json()

    # ── Schema ───────────────────────────────────────────────────────────────
    assert "active_agents" in data, "Response missing 'active_agents' key"
    assert "total"         in data, "Response missing 'total' key"
    assert isinstance(data["active_agents"], list), "'active_agents' must be a list"
    assert isinstance(data["total"],         int),  "'total' must be an int"

    # ── Consistency ──────────────────────────────────────────────────────────
    assert data["total"] == len(data["active_agents"]), (
        f"'total' ({data['total']}) != len(active_agents) ({len(data['active_agents'])})"
    )

    # ── Liveness — at least one worker must be online ────────────────────────
    assert data["total"] > 0, (
        f"No active swarm workers detected.\n"
        f"Start the backend-worker container and retry.\n"
        f"Response: {data}"
    )

    print(
        f"\n✓ Phase 1 PASS — {data['total']} worker(s) online: "
        f"{data['active_agents']}"
    )


# ===========================================================================
# PHASE 2 — The Diplomat Protocol (Zero Trust Clearance Firewall)
# ===========================================================================


async def test_phase2_diplomat_protocol(client: httpx.AsyncClient) -> None:
    """
    Zero-Trust verification: a Level-1 (Public) agent must NEVER receive
    content from a Level-5 (Top-Secret) node, even when querying for it
    directly by its exact label.

    Setup
    -----
    1. Create a L5 agent (owner of the secret node).
    2. Seed a SpaiderNode with clearance_level=5 directly via Neo4j driver,
       labelled with a cryptographically unique sentinel string that cannot
       appear in any pre-existing node or LLM hallucination.
    3. Create a L1 agent.

    Firewall assertion
    ------------------
    4. POST /api/v1/query with the L1 agent's ID and the sentinel as the
       question.  The query_service Cypher filter:

           coalesce(n.clearance_level, 1) <= $agent_clearance

       resolves to ``5 <= 1 = FALSE`` — the L5 node is excluded from the
       vector/text search result set.  The LLM never receives the sentinel
       as context and therefore cannot include it in its answer.

    Cleanup
    -------
    Always runs regardless of assertion outcome (try/finally).
    """
    SENTINEL = f"CLASSIFIED_TOPKEYWORD_{uuid.uuid4().hex}"

    l5_agent_id: str | None = None
    l1_agent_id: str | None = None
    secret_node_id: str | None = None

    try:
        # ── 1. Create agents ─────────────────────────────────────────────
        l5_name  = f"L5-E2E-{uuid.uuid4().hex[:6]}"
        l1_name  = f"L1-E2E-{uuid.uuid4().hex[:6]}"
        l5_agent = await _create_agent(client, name=l5_name, clearance_level=5)
        l1_agent = await _create_agent(client, name=l1_name, clearance_level=1)
        l5_agent_id = l5_agent["id"]
        l1_agent_id = l1_agent["id"]

        # ── 2. Seed a classified L5 node directly via Neo4j driver ───────
        secret_node_id = str(uuid.uuid4())
        driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        try:
            async with driver.session() as session:
                await session.run(
                    """
                    MERGE (n:SpaiderNode {id: $id})
                    SET n.label           = $label,
                        n.type            = 'SECRET',
                        n.agent_id        = $agent_id,
                        n.clearance_level = 5,
                        n.created_at      = datetime(),
                        n.updated_at      = datetime(),
                        n.properties      = $props
                    """,
                    id=secret_node_id,
                    label=SENTINEL,
                    agent_id=l5_agent_id,
                    props=json.dumps({"description": SENTINEL, "source_text": SENTINEL}),
                )
        finally:
            await driver.close()

        # ── 3. Query with the L1 agent ────────────────────────────────────
        resp = await client.post(
            "/api/v1/query",
            json={"question": SENTINEL, "agent_id": l1_agent_id},
        )
        assert resp.status_code == 200, (
            f"Query endpoint returned {resp.status_code}: {resp.text}"
        )
        answer = resp.json().get("answer", "")

        # ── 4. Zero-Trust assertion ───────────────────────────────────────
        assert SENTINEL not in answer, (
            "SECURITY VIOLATION: Diplomat Protocol BREACHED!\n"
            f"L1 agent '{l1_agent_id}' received classified L5 content.\n"
            f"Sentinel '{SENTINEL}' found in answer:\n{answer}"
        )

        print(
            f"\n✓ Phase 2 PASS — sentinel '{SENTINEL[:32]}…' "
            f"invisible to L1 agent '{l1_agent_id}'"
        )

    finally:
        # ── Cleanup: remove test node, delete agents ──────────────────────
        if secret_node_id:
            driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
            try:
                async with driver.session() as session:
                    await session.run(
                        "MATCH (n:SpaiderNode {id: $id}) DETACH DELETE n",
                        id=secret_node_id,
                    )
            except Exception:
                pass
            finally:
                await driver.close()

        if l5_agent_id:
            await _delete_agent(client, l5_agent_id)
        if l1_agent_id:
            await _delete_agent(client, l1_agent_id)


# ===========================================================================
# PHASE 3 — The Matrix (SSE Event Stream + Pheromone Pipeline)
# ===========================================================================


async def test_phase3_matrix_sse(
    client: httpx.AsyncClient,
    redis_client: aioredis.Redis,
) -> None:
    """
    Full end-to-end validation of the Operation Matrix telemetry pipeline:

      POST /ingest/sync  →  node created in Neo4j
        →  XADD pheromone_stream  →  swarm_listener XREADGROUP
          →  publish_swarm_log("routing")   →  SSE frame received
          →  claim_node_for_agent() == True
          →  publish_swarm_log("lock")      →  SSE frame received
          →  _dispatch() + clear_pheromone() + release_node_claim()
          →  publish_swarm_log("success")   →  SSE frame received

    Concurrency design
    ------------------
    1. asyncio.create_task() starts the SSE listener coroutine.
    2. An asyncio.Event (stream_opened) gates the pipeline trigger so we
       never fire the pheromone before the SSE connection is established —
       this prevents a missed-event race.
    3. asyncio.wait_for(..., timeout=10.0) kills the test with a clear
       failure message if the pipeline stalls, preventing CI hangs.

    Timeout strategy
    ----------------
    The 10 s outer deadline is generous enough for a single-worker round-trip
    on a cold LLM stub but tight enough to catch regressions in CI.  The
    5 s stream_opened guard is a separate safeguard for SSE connection setup.
    """
    INGEST_TEXT = (
        f"SpAIder E2E pipeline validation node {uuid.uuid4().hex}. "
        "Operation Matrix swarm intelligence stress test."
    )
    REQUIRED_EVENTS = {"routing", "lock", "success"}

    test_agent_id: str | None = None

    try:
        # ── Step 1: Create a disposable test agent ───────────────────────
        agent = await _create_agent(
            client,
            name=f"E2E-Matrix-{uuid.uuid4().hex[:6]}",
            clearance_level=1,
        )
        test_agent_id = agent["id"]

        # ── Step 2: Sync ingest → get real Neo4j node ID ─────────────────
        ingest_resp = await client.post(
            "/api/v1/ingest/sync",
            json={
                "text":     INGEST_TEXT,
                "agent_id": test_agent_id,
                "source":   "e2e-matrix-test",
            },
        )
        assert ingest_resp.status_code == 200, (
            f"Ingest failed ({ingest_resp.status_code}): {ingest_resp.text}"
        )
        ingest_data = ingest_resp.json()
        assert ingest_data.get("nodes"), (
            "Ingest returned zero nodes — cannot trigger pheromone pipeline.\n"
            f"Response: {ingest_data}"
        )
        node_id: str = ingest_data["nodes"][0]["id"]

        # ── Step 3: Define the SSE collector coroutine ───────────────────
        collected:     set[str]      = set()
        stream_opened: asyncio.Event = asyncio.Event()

        async def _collect_sse_events() -> None:
            """
            Open the SSE connection, signal readiness, then collect event
            types until all REQUIRED_EVENTS have been seen.

            Exits naturally (returns) once the set is complete, which causes
            asyncio.wait_for() to resolve without cancellation.

            Using a dedicated inner httpx.AsyncClient with connect_timeout=10 s
            and no read timeout so the streaming response never times out
            mid-collection.
            """
            inner_timeout = httpx.Timeout(timeout=None, connect=10.0)
            async with httpx.AsyncClient(timeout=inner_timeout) as sse_client:
                async with sse_client.stream(
                    "GET",
                    f"{BASE_URL}/api/v1/swarm/events/stream",
                ) as response:
                    assert response.status_code == 200, (
                        f"SSE endpoint returned {response.status_code} — "
                        "is the backend healthy?"
                    )
                    # Signal: connection is live, safe to fire the trigger.
                    stream_opened.set()

                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue

                        event_type: str = event.get("type", "")
                        collected.add(event_type)

                        # Exit as soon as all required events are confirmed.
                        if REQUIRED_EVENTS.issubset(collected):
                            return

        # ── Step 4: Start SSE listener as a concurrent task ──────────────
        sse_task: asyncio.Task = asyncio.create_task(_collect_sse_events())

        # ── Step 5: Wait for SSE connection before firing the trigger ─────
        # A hard 5 s limit — if the SSE endpoint never accepts the connection
        # something is fundamentally wrong with the service.
        try:
            await asyncio.wait_for(stream_opened.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            sse_task.cancel()
            pytest.fail(
                "SSE connection not established within 5 s.\n"
                "Check that GET /api/v1/swarm/events/stream is reachable."
            )

        # ── Step 6: Publish pheromone → triggers swarm_listener pipeline ──
        # XADD appends to the stream; swarm_listener XREADGROUP picks it up
        # and executes the full routing → lock → dispatch → success pipeline,
        # publishing SSE events at each stage via publish_swarm_log().
        await redis_client.xadd(
            PHEROMONE_STREAM,
            {
                "node_id":    node_id,
                "agent_type": "summariser",
                "session_id": "",
            },
        )

        # ── Step 7: Wait for all 3 events (hard 10 s deadline) ───────────
        try:
            await asyncio.wait_for(sse_task, timeout=10.0)
        except asyncio.TimeoutError:
            sse_task.cancel()
            missing = REQUIRED_EVENTS - collected
            pytest.fail(
                "SSE pipeline timeout: required events not received within 10 s.\n"
                f"Received : {collected}\n"
                f"Missing  : {missing}\n"
                f"node_id  : {node_id}\n"
                "Possible causes:\n"
                "  · swarm_listener not running / crashed\n"
                "  · publish_swarm_log() not reached (check worker logs)\n"
                "  · Redis pheromone stream consumer group missing"
            )

        # ── Step 8: Hard assertions on received event types ───────────────
        assert "routing" in collected, (
            f"'routing' event missing (worker never woke up). Got: {collected}"
        )
        assert "lock" in collected, (
            f"'lock' event missing (atomic lease claim failed). Got: {collected}"
        )
        assert "success" in collected, (
            f"'success' event missing (pipeline did not complete). Got: {collected}"
        )

        print(
            f"\n✓ Phase 3 PASS — SSE events confirmed: {sorted(collected)}\n"
            f"  node_id : {node_id}\n"
            f"  agent   : {test_agent_id}"
        )

    finally:
        # ── Cleanup: delete test agent (graph data cleaned up by backend) ─
        if test_agent_id:
            await _delete_agent(client, test_agent_id)
