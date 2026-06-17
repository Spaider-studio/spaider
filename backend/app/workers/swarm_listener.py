"""
Swarm Listener — Enterprise Reliable Stream Consumer.

Full pipeline per message
-------------------------
  1. XREADGROUP delivers a stream entry into this worker's PEL.
  2. Extract  node_id / agent_type / session_id from stream fields.
  3. Context Scent — load working memory (chat history) from Redis using
     session_id.  Specialists receive full conversational context before
     their LLM call, preventing "context amnesia" in multi-step workflows.
  4. Atomic Lease — claim_node_for_agent() acquires an exclusive Neo4j lock.
     If another worker holds the lock → skip (no XACK, message stays in PEL,
     lease-holder's worker will ACK after it finishes).
  5. Dispatch to the appropriate specialist coroutine with working_memory.
  6. Clear the Neo4j pheromone (REMOVE n.needs_agent).
  7. Release the atomic lease.
  8. XACK — message leaves the PEL permanently.

On any failure between steps 5-7: no XACK, no lease release.
The lease expires naturally after lease_duration_sec seconds.
XAUTOCLAIM reclaims the stream message after _CLAIM_MIN_IDLE_MS ms.

Delivery contract
-----------------
At-Least-Once — a message is never permanently lost.
Ghost-Work-Free — the atomic lease ensures only one worker processes
a node at a time, even under concurrent XAUTOCLAIM recovery.

Swarm Pulse (Observability)
---------------------------
Each worker registers a unique agent_id at startup and runs a background
heartbeat task that refreshes a Redis key every _HEARTBEAT_INTERVAL_S
seconds with a TTL of _HEARTBEAT_TTL_S.  When the worker stops, the key
expires naturally — ephemeral presence without a cleanup step.

  Key schema:  agent_status:{agent_id}   value: "online"   TTL: 15 s

Telemetry Hooks (Operation Matrix)
-----------------------------------
Four lifecycle events are published to the Redis Pub/Sub channel
``swarm_log_channel`` via ``publish_swarm_log``.  The SSE endpoint fans
these out to all connected browser clients in real-time:

  routing  — message extracted, context evaluation starting
  lock     — atomic Neo4j lease successfully claimed
  success  — full pipeline completed, ACT-R energy boost triggered
  error    — lease denied (contention) OR pipeline exception
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from app.services.graph_service import GraphService

from app.config import settings
from app.lib.litellm_retry import acompletion_with_retry
from app.models.schemas import Edge, GraphPayload, Node, NodeType, RelationType
from app.services.embedding_service import EmbeddingService
from app.services.redis_service import (
    CONSUMER_GROUP,
    PHEROMONE_STREAM,
    PheromoneService,
    WorkingMemoryService,
    publish_swarm_log,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants — stream consumer
# ---------------------------------------------------------------------------

_CONSUMER_NAME: str      = f"{socket.gethostname()}-{os.getpid()}"
_READ_COUNT: int         = 10
_BLOCK_MS: int           = 5_000
_CLAIM_MIN_IDLE_MS: int  = 60_000
_CLAIM_INTERVAL_S: float = 30.0
_RECONNECT_DELAY: float  = 5.0

# Socket read timeout for the dedicated stream-consumer connection.
#
# redis-py 8.x derives a client-side read timeout from the XREADGROUP ``BLOCK``
# argument.  When the socket read timeout equals the block time, the client
# timeout deterministically wins the race against the server's BLOCK response:
# every idle read raises ``TimeoutError: Timeout reading from redis`` and the
# connection is torn down.  That spun this worker in a 10 s dead-loop (5 s block
# + 5 s reconnect) and churned connections in the shared pool, intermittently
# wedging the whole backend (SSE feed, queries) until a restart.
#
# Giving the blocking read its own socket timeout, comfortably LONGER than the
# block, lets the server's BLOCK response always arrive first — idle reads
# return a clean empty result instead of a spurious timeout.
_STREAM_SOCKET_TIMEOUT_S: float = _BLOCK_MS / 1000 + 5.0  # 10 s for a 5 s block

# Lease duration granted per node.  Set longer than the slowest specialist
# to prevent spurious expiry under load.
_LEASE_DURATION_SEC: int = 60

# ---------------------------------------------------------------------------
# Tuning constants — Swarm Pulse heartbeat
# ---------------------------------------------------------------------------

# How often the worker refreshes its presence key (seconds).
_HEARTBEAT_INTERVAL_S: float = 10.0

# Redis TTL for the presence key (seconds).  Must be > _HEARTBEAT_INTERVAL_S
# so a single slow iteration does not prematurely mark the worker offline.
_HEARTBEAT_TTL_S: int = 15


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_agent_id() -> str:
    """
    Generate a stable, human-readable worker identity.

    Format: ``swarm_worker_{8-char hex}``
    Example: ``swarm_worker_3f9a1c02``

    Uses uuid4 for collision-free generation across replicas.
    The short hex suffix keeps log lines readable without sacrificing uniqueness
    at typical swarm sizes (< 10 000 concurrent workers).
    """
    return f"swarm_worker_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Swarm Pulse — heartbeat background task
# ---------------------------------------------------------------------------

async def _heartbeat_loop(
    redis_client: "aioredis.Redis",
    agent_id: str,
) -> None:
    """
    Background coroutine: refresh the agent's presence key in Redis every
    _HEARTBEAT_INTERVAL_S seconds.

    Key schema
    ----------
    ``agent_status:{agent_id}``  →  value ``"online"``  TTL ``_HEARTBEAT_TTL_S``

    Lifecycle
    ---------
    • The key is created on first iteration (no separate registration step).
    • Each setex call resets the TTL, so the key survives as long as the
      worker is alive and the loop keeps running.
    • When the worker shuts down (CancelledError), the loop exits without
      touching the key — it expires naturally after _HEARTBEAT_TTL_S seconds,
      giving the dashboard a ~15-second grace window to mark the agent offline.
    • Redis errors are swallowed with a warning; the heartbeat resumes on the
      next iteration rather than crashing the worker.

    Called from swarm_listener() via asyncio.create_task().
    """
    logger.info(
        "SwarmPulse | heartbeat started — agent_id=%s interval=%.0fs ttl=%ds",
        agent_id, _HEARTBEAT_INTERVAL_S, _HEARTBEAT_TTL_S,
    )
    while True:
        try:
            await redis_client.setex(
                name=f"agent_status:{agent_id}",
                time=_HEARTBEAT_TTL_S,
                value="online",
            )
            logger.debug("SwarmPulse | pulse sent — agent_id=%s", agent_id)
        except asyncio.CancelledError:
            # Propagate cancellation immediately — do not swallow it.
            raise
        except Exception as exc:
            logger.warning(
                "SwarmPulse | setex failed for agent_id=%s: %s — "
                "will retry in %.0fs",
                agent_id, exc, _HEARTBEAT_INTERVAL_S,
            )
        await asyncio.sleep(_HEARTBEAT_INTERVAL_S)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def swarm_listener(
    redis_client: "aioredis.Redis",
    graph_service: "GraphService",
) -> None:
    """
    Long-running coroutine: consume pheromone events from the Redis Stream.

    Accepts the full GraphService instance (not just the driver) so that
    Atomic Leasing (claim_node_for_agent / release_node_claim) is available
    alongside the Neo4j write operations in PheromoneService.

    Started via asyncio.create_task() from main.py lifespan.

    Swarm Pulse
    -----------
    A unique agent_id is assigned at startup.  A background heartbeat task
    is created immediately and cancelled on exit so the presence key expires
    gracefully after _HEARTBEAT_TTL_S seconds.
    """
    import redis.asyncio as aioredis
    from redis.exceptions import TimeoutError as RedisTimeoutError

    pheromone = PheromoneService(
        redis_client=redis_client,
        neo4j_driver=graph_service._driver,
    )
    working_memory = WorkingMemoryService(redis_client=redis_client)

    # Dedicated connection for the blocking XREADGROUP read, with a socket
    # timeout longer than the block (see _STREAM_SOCKET_TIMEOUT_S).  Kept off
    # the shared request pool so a stream read can never stall or churn the
    # connections that serve HTTP queries and the SSE pheromone feed.  All
    # short ops (publishes, acks, leases) still use the passed-in client.
    stream_redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=_STREAM_SOCKET_TIMEOUT_S,
    )

    # ── Swarm Pulse — assign identity and start heartbeat ─────────────────
    agent_id = _make_agent_id()
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(redis_client, agent_id),
        name=f"heartbeat-{agent_id}",
    )

    logger.info(
        "SwarmListener | starting as consumer '%s' (agent_id=%s) "
        "on stream '%s' group '%s'",
        _CONSUMER_NAME, agent_id, PHEROMONE_STREAM, CONSUMER_GROUP,
    )

    _last_claim_at: float = asyncio.get_event_loop().time()

    try:
        while True:
            try:
                # ── XAUTOCLAIM recovery (periodic) ────────────────────────
                now = asyncio.get_event_loop().time()
                if now - _last_claim_at >= _CLAIM_INTERVAL_S:
                    await _recover_pending(
                        redis_client, pheromone, working_memory,
                        graph_service, agent_id,
                    )
                    _last_claim_at = asyncio.get_event_loop().time()

                # ── XREADGROUP — new messages only ────────────────────────
                try:
                    results = await stream_redis.xreadgroup(
                        groupname=CONSUMER_GROUP,
                        consumername=_CONSUMER_NAME,
                        streams={PHEROMONE_STREAM: ">"},
                        count=_READ_COUNT,
                        block=_BLOCK_MS,
                    )
                except RedisTimeoutError:
                    # An idle block window with no new messages — benign.  Loop
                    # straight back round without the reconnect penalty rather
                    # than treating "nothing arrived" as a fault.
                    continue

                if not results:
                    continue

                for _stream_name, entries in results:
                    for message_id, fields in entries:
                        await _process_message(
                            redis_client=redis_client,
                            pheromone=pheromone,
                            working_memory=working_memory,
                            graph_service=graph_service,
                            message_id=message_id,
                            fields=fields,
                            agent_id=agent_id,
                        )

            except asyncio.CancelledError:
                raise  # Propagate to outer try/finally

            except Exception as exc:
                logger.warning(
                    "SwarmListener | unexpected error (%s: %s) — "
                    "reconnecting in %.0fs",
                    type(exc).__name__, exc, _RECONNECT_DELAY,
                )
                await asyncio.sleep(_RECONNECT_DELAY)

    finally:
        # ── Graceful shutdown — cancel heartbeat, let key expire ──────────
        if not heartbeat_task.done():
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        logger.info(
            "SwarmPulse | heartbeat stopped for agent_id=%s — "
            "presence key will expire in %ds",
            agent_id, _HEARTBEAT_TTL_S,
        )
        # Release the dedicated stream connection.
        try:
            await stream_redis.aclose()
        except Exception:  # pragma: no cover — best-effort cleanup
            pass
        logger.info(
            "SwarmListener | shutdown — in-flight messages reclaimed on restart"
        )


# ---------------------------------------------------------------------------
# Core message processor
# ---------------------------------------------------------------------------

async def _process_message(
    redis_client: "aioredis.Redis",
    pheromone: PheromoneService,
    working_memory: WorkingMemoryService,
    graph_service: "GraphService",
    message_id: str,
    fields: dict[str, str],
    agent_id: str,
) -> None:
    """
    Execute the full pipeline for one stream entry.

    XACK is sent ONLY after all of the following succeed in order:
      specialist work → clear pheromone → release lease.

    Failure at any step → no XACK, lease expires, XAUTOCLAIM retries.
    Contention (lease held by another worker) → silent skip, no XACK,
    the lease-holder's worker will ACK when it finishes.

    Telemetry hooks publish to Redis Pub/Sub at 4 lifecycle points so the
    LivePheromoneStream terminal in the frontend receives real-time events.
    """
    # ── 1. Extract fields ─────────────────────────────────────────────────
    node_id    = fields.get("node_id", "")
    agent_type = fields.get("agent_type", "")
    session_id = fields.get("session_id")   # None when field is absent

    if not node_id or not agent_type:
        logger.warning(
            "SwarmListener | malformed entry id=%s fields=%r — discarding",
            message_id, fields,
        )
        await _safe_ack(redis_client, message_id)
        return

    # ── TELEMETRY HOOK 1: Routing / Wakeup ───────────────────────────────
    # Fired immediately after a valid message is extracted from the stream,
    # before any blocking work (context load, lease claim).
    await publish_swarm_log(
        redis_client,
        event_type="routing",
        agent=agent_id,
        message=f"Agent woke up. Evaluating context for node {node_id}...",
        node_id=node_id,
    )

    # ── 2. Context Scent — load working memory ────────────────────────────
    # Always returns a list (empty list when session_id is absent or expired).
    # Loading happens BEFORE the lease claim so we don't hold the lock while
    # waiting on a Redis round-trip.
    context: list[dict] = await working_memory.load_context(session_id)

    if session_id:
        logger.debug(
            "SwarmListener | context loaded session=%s messages=%d",
            session_id, len(context),
        )

    # ── 3. Atomic Lease — prevent Ghost Work ─────────────────────────────
    # claim_node_for_agent returns False if another worker holds the lock.
    # We do NOT send XACK: the message stays in the PEL.  The worker that
    # holds the lease will process it and send XACK when done.
    claimed = await graph_service.claim_node_for_agent(
        node_id=node_id,
        agent_id=_CONSUMER_NAME,
        lease_duration_sec=_LEASE_DURATION_SEC,
    )
    if not claimed:
        logger.info(
            "SwarmListener | [%s] SKIPPED node=[%s] msg=%s — "
            "lease held by another worker",
            agent_type, node_id, message_id,
        )
        # ── TELEMETRY HOOK 4a: Lock denied (contention) ──────────────────
        await publish_swarm_log(
            redis_client,
            event_type="error",
            agent=agent_id,
            message="Lock denied or execution failed. Reason: lease held by another worker",
            node_id=node_id,
        )
        return

    # ── TELEMETRY HOOK 2: Atomic Lease secured ───────────────────────────
    # Fired only when THIS worker won the CAS race in Neo4j.
    await publish_swarm_log(
        redis_client,
        event_type="lock",
        agent=agent_id,
        message=f"Atomic lease secured. Claimed node {node_id}.",
        node_id=node_id,
    )

    logger.info(
        "SwarmListener | [%s] woke up to process node=[%s] "
        "session=%s msg=%s",
        agent_type, node_id, session_id or "—", message_id,
    )

    # ── 4-6. Work → clear pheromone → release lease ───────────────────────
    try:
        # 4. Dispatch specialist (receives working memory for LLM context)
        await _dispatch(node_id, agent_type, context, graph_service)

        # 5. Clear Neo4j pheromone — raises on failure (no XACK if this fails)
        await pheromone.clear_pheromone(node_id)

        # 6. Release the lease — only this worker can release its own lock
        await graph_service.release_node_claim(
            node_id=node_id,
            agent_id=_CONSUMER_NAME,
        )

        # ── TELEMETRY HOOK 3: Task complete ──────────────────────────────
        # Fired after ALL pipeline steps succeed — pheromone cleared, lease
        # released, ACT-R energy boost implicitly triggered by graph write.
        await publish_swarm_log(
            redis_client,
            event_type="success",
            agent=agent_id,
            message="Task resolved. ACT-R energy boost triggered.",
            node_id=node_id,
        )

    except Exception as exc:
        logger.error(
            "SwarmListener | pipeline FAILED node=%s agent_type=%s msg=%s — "
            "NO XACK, lease expires in %ds, XAUTOCLAIM will retry. Error: %s",
            node_id, agent_type, message_id, _LEASE_DURATION_SEC, exc,
        )
        # ── TELEMETRY HOOK 4b: Pipeline exception ────────────────────────
        await publish_swarm_log(
            redis_client,
            event_type="error",
            agent=agent_id,
            message=f"Lock denied or execution failed. Reason: {exc}",
            node_id=node_id,
        )
        return

    # ── 7. XACK — only reached on full success ────────────────────────────
    await _safe_ack(redis_client, message_id)
    logger.info(
        "SwarmListener | [%s] finished — XACK msg=%s node=[%s] session=%s",
        agent_type, message_id, node_id, session_id or "—",
    )


async def _safe_ack(redis_client: "aioredis.Redis", message_id: str) -> None:
    """Send XACK, logging a warning on failure (non-fatal — XAUTOCLAIM handles it)."""
    try:
        await redis_client.xack(PHEROMONE_STREAM, CONSUMER_GROUP, message_id)
    except Exception as exc:
        logger.warning(
            "SwarmListener | XACK failed for msg=%s "
            "(will be reclaimed by XAUTOCLAIM): %s",
            message_id, exc,
        )


# ---------------------------------------------------------------------------
# XAUTOCLAIM recovery
# ---------------------------------------------------------------------------

async def _recover_pending(
    redis_client: "aioredis.Redis",
    pheromone: PheromoneService,
    working_memory: WorkingMemoryService,
    graph_service: "GraphService",
    agent_id: str,
) -> None:
    """
    Reclaim stale PEL entries (idle > _CLAIM_MIN_IDLE_MS) and reprocess them.

    By the time XAUTOCLAIM runs (after 60 s), any Neo4j lease set during the
    original delivery attempt has already expired (lease = 60 s).  The full
    pipeline in _process_message will therefore be able to acquire a fresh
    lease cleanly.

    agent_id is forwarded so telemetry hooks inside _process_message identify
    the correct worker in the LivePheromoneStream terminal.
    """
    cursor = "0-0"
    reclaimed_total = 0

    while True:
        try:
            next_cursor, entries, _ = await redis_client.xautoclaim(
                name=PHEROMONE_STREAM,
                groupname=CONSUMER_GROUP,
                consumername=_CONSUMER_NAME,
                min_idle_time=_CLAIM_MIN_IDLE_MS,
                start_id=cursor,
                count=_READ_COUNT,
            )
        except Exception as exc:
            logger.warning("SwarmListener | XAUTOCLAIM failed: %s", exc)
            return

        if entries:
            reclaimed_total += len(entries)
            logger.info(
                "SwarmListener | XAUTOCLAIM reclaimed %d stale message(s)",
                len(entries),
            )
            for message_id, fields in entries:
                await _process_message(
                    redis_client=redis_client,
                    pheromone=pheromone,
                    working_memory=working_memory,
                    graph_service=graph_service,
                    message_id=message_id,
                    fields=fields,
                    agent_id=agent_id,
                )

        if not next_cursor or next_cursor == "0-0":
            break
        cursor = next_cursor

    if reclaimed_total:
        logger.info(
            "SwarmListener | recovery scan complete — %d message(s) reclaimed",
            reclaimed_total,
        )


# ---------------------------------------------------------------------------
# Specialist dispatcher
# ---------------------------------------------------------------------------

async def _dispatch(
    node_id: str,
    agent_type: str,
    working_memory: list[dict],
    graph_service: "GraphService",
) -> None:
    """
    Route to the correct specialist coroutine with full working memory.

    ``working_memory`` is the chat history loaded from Redis via session_id.
    It is an empty list for background (non-conversational) events.
    Each specialist prepends its system prompt and calls the LLM directly.
    """
    match agent_type:
        case "summariser":
            await _specialist_summariser(node_id, working_memory, graph_service)
        case "enricher":
            await _specialist_enricher(node_id, working_memory, graph_service)
        case "classifier":
            await _specialist_classifier(node_id, working_memory, graph_service)
        case _:
            logger.warning(
                "SwarmListener | unknown agent_type=%r for node=%s — "
                "pheromone will be cleared to prevent graph pollution",
                agent_type, node_id,
            )


# ---------------------------------------------------------------------------
# Specialist LLM helpers
# ---------------------------------------------------------------------------

_SUMMARISER_SYSTEM = (
    "You condense a knowledge-graph node into one or two factual sentences. "
    "Preserve named entities, numbers, and dates. No preamble, no markdown."
)
_CLASSIFIER_SYSTEM = (
    "You classify a knowledge-graph node into exactly one type from a fixed "
    "vocabulary. Reply with the single type token and nothing else."
)
_ENRICHER_SYSTEM = (
    "You extract entities related to a knowledge-graph node, grounded strictly "
    "in the supplied context. Reply with a JSON array and nothing else."
)


async def _llm_complete(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 512,
    temperature: float = 0.0,
) -> str:
    """One LLM completion via the shared retry wrapper. Returns the message text.

    Mirrors the call convention used across the services (model + optional
    api_base / api_key from settings, wrapped in a 120 s timeout).
    """
    call_kwargs: dict = dict(
        model=settings.litellm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if settings.llm_base_url:
        call_kwargs["api_base"] = settings.llm_base_url
    if settings.llm_api_key:
        call_kwargs["api_key"] = settings.llm_api_key

    response = await asyncio.wait_for(
        acompletion_with_retry(**call_kwargs),
        timeout=120,
    )
    return (response.choices[0].message.content or "").strip()


def _node_source_text(node) -> str:
    """Best available text for a node: verbatim source first, then description, then label."""
    props = node.properties or {}
    return (props.get("source_text") or node.description or node.label or "").strip()


def _format_working_memory(working_memory: list[dict]) -> str:
    """Render recent chat turns as a short context preamble (empty string if none)."""
    if not working_memory:
        return ""
    turns = []
    for msg in working_memory[-6:]:
        role = msg.get("role", "user")
        content = str(msg.get("content", "")).strip()
        if content:
            turns.append(f"{role}: {content}")
    if not turns:
        return ""
    return "Recent conversation context:\n" + "\n".join(turns) + "\n\n"


def _parse_json_array(raw: str) -> list[dict]:
    """Extract a JSON array of objects from an LLM reply, tolerating code fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text[3:]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Specialists
# ---------------------------------------------------------------------------

async def _specialist_summariser(
    node_id: str,
    working_memory: list[dict],
    graph_service: "GraphService",
) -> None:
    """Condense a node's source text into a description and write it back.

    working_memory provides prior conversation turns so the summary stays
    coherent with the user's current line of inquiry.
    """
    node = await graph_service.get_node_by_id(node_id)
    if node is None:
        logger.warning("SwarmListener | summariser: node=%s not found", node_id)
        return

    source = _node_source_text(node)
    if not source:
        logger.debug("SwarmListener | summariser: node=%s has no text — skipping", node_id)
        return

    user = (
        f"{_format_working_memory(working_memory)}"
        f"Summarise this knowledge-graph node in one or two sentences:\n\n{source[:4000]}"
    )
    summary = await _llm_complete(_SUMMARISER_SYSTEM, user, max_tokens=256)
    if not summary:
        logger.warning("SwarmListener | summariser: empty summary for node=%s", node_id)
        return

    await graph_service.set_node_description(node_id, summary)
    logger.info(
        "SwarmListener | summariser: node=%s description updated (%d chars)",
        node_id, len(summary),
    )


async def _specialist_classifier(
    node_id: str,
    working_memory: list[dict],
    graph_service: "GraphService",
) -> None:
    """Zero-shot classify a node into the NodeType vocabulary and update n.type."""
    node = await graph_service.get_node_by_id(node_id)
    if node is None:
        logger.warning("SwarmListener | classifier: node=%s not found", node_id)
        return

    valid_types = {t.value for t in NodeType}
    vocab = ", ".join(sorted(valid_types))
    user = (
        f"Node label: {node.label}\n"
        f"Context: {_node_source_text(node)[:1000]}\n\n"
        f"Classify into exactly one of: {vocab}\n"
        f"Reply with the single type token only."
    )
    raw = await _llm_complete(_CLASSIFIER_SYSTEM, user, max_tokens=16)
    predicted = raw.strip().upper().split()[0].strip(".,") if raw.strip() else ""

    if predicted not in valid_types:
        logger.warning(
            "SwarmListener | classifier: node=%s got invalid class %r — type unchanged",
            node_id, raw,
        )
        return

    await graph_service.set_node_type(node_id, predicted)
    logger.info("SwarmListener | classifier: node=%s type=%s", node_id, predicted)


async def _specialist_enricher(
    node_id: str,
    working_memory: list[dict],
    graph_service: "GraphService",
) -> None:
    """Extract context-grounded related entities and MERGE them as new nodes/edges.

    Each related entity becomes a SpaiderNode (with an embedding so it is
    vector-searchable) linked back to the source node by a typed RELATION.
    Entities are capped at 3 per run and constrained to the NodeType /
    RelationType vocabularies to keep the graph clean.
    """
    node = await graph_service.get_node_by_id(node_id)
    if node is None:
        logger.warning("SwarmListener | enricher: node=%s not found", node_id)
        return

    agent_id = node.agent_id
    if not agent_id:
        logger.warning("SwarmListener | enricher: node=%s has no agent_id — skipping", node_id)
        return

    valid_types = {t.value for t in NodeType}
    valid_rels = {r.value for r in RelationType}
    user = (
        f"{_format_working_memory(working_memory)}"
        f"Source entity: {node.label}\n"
        f"Context: {_node_source_text(node)[:1500]}\n\n"
        f"List up to 3 entities related to the source, grounded strictly in the "
        f"context. Return a JSON array of objects with keys label, type, relation, "
        f"where type is one of [{', '.join(sorted(valid_types))}] and relation is one "
        f"of [{', '.join(sorted(valid_rels))}]. Return [] if nothing is well-supported."
    )
    raw = await _llm_complete(_ENRICHER_SYSTEM, user, max_tokens=512)
    items = _parse_json_array(raw)
    if not items:
        logger.debug("SwarmListener | enricher: node=%s found no related entities", node_id)
        return

    embedder = EmbeddingService()
    try:
        new_nodes: list[Node] = []
        new_edges: list[Edge] = []
        for item in items[:3]:
            label = str(item.get("label", "")).strip()
            if not label:
                continue
            node_type = str(item.get("type", "OTHER")).strip().upper()
            relation = str(item.get("relation", "RELATED_TO")).strip().upper()
            if node_type not in valid_types:
                node_type = "OTHER"
            if relation not in valid_rels:
                relation = "RELATED_TO"

            embedding = await embedder.embed(label)
            new_node = Node(
                label=label,
                type=node_type,
                agent_id=agent_id,
                embedding=embedding,
                properties={"source": "swarm_enricher", "origin_node": node_id},
            )
            new_nodes.append(new_node)
            new_edges.append(Edge(
                source_id=node_id,
                target_id=new_node.id,
                relation=relation,
                properties={"source": "swarm_enricher"},
            ))

        if not new_nodes:
            logger.debug("SwarmListener | enricher: node=%s produced no usable entities", node_id)
            return

        await graph_service.write_graph(
            GraphPayload(nodes=new_nodes, edges=new_edges), agent_id,
        )
        logger.info(
            "SwarmListener | enricher: node=%s added %d node(s) / %d edge(s)",
            node_id, len(new_nodes), len(new_edges),
        )
    finally:
        await embedder.close()
