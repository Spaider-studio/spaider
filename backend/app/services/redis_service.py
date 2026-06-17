"""
Pheromone Service — Enterprise Stigmergic Swarm Routing Layer.

Architecture (v2 — Redis Streams)
-----------------------------------
Implements "Event-Driven Graph Routing" with at-least-once delivery
guarantees via Redis Streams + Consumer Groups.

  1. A graph node receives a pheromone marker (Neo4j property `needs_agent`)
     that signals which specialist worker should process it next.

  2. An event is appended to the Redis Stream `pheromone_stream` via XADD.
     The stream persists messages durably — no subscriber needs to be online
     at publish time (unlike Pub/Sub).

  3. Consumer Group `swarm_workers` reads events with XREADGROUP.  Each event
     moves into the Pending Entry List (PEL) for that consumer.

  4. ONLY after Neo4j is successfully updated does the worker call XACK.
     Until then the message stays in the PEL and will be re-delivered on
     restart or reclaimed via XAUTOCLAIM after a visibility timeout.

Reliability contract
--------------------
  • At-Least-Once Delivery  — a message is never dropped due to a worker crash.
  • Idempotent init          — XGROUP CREATE is wrapped in a ResponseError guard
                               so restarting the app never breaks an existing group.
  • Back-pressure            — XADD uses MAXLEN ~ 10 000 (approximate trim) to
                               prevent unbounded stream growth in high-throughput
                               scenarios while keeping recent history available.
  • Neo4j-first ordering     — XADD is only called after the Neo4j SET succeeds,
                               so the stream never contains events for markers that
                               were never written to the graph.

Design decisions
----------------
  • PheromoneService is injected (not a singleton) — testable, no competing pools.
  • initialize_stream() is a standalone coroutine called once from main.py lifespan,
    decoupled from the PheromoneService instance lifecycle.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from neo4j import AsyncDriver

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stream / Consumer Group constants
# ---------------------------------------------------------------------------

PHEROMONE_STREAM   = "pheromone_stream"   # Redis Stream key
CONSUMER_GROUP     = "swarm_workers"      # Consumer Group name
STREAM_MAXLEN      = 10_000               # Approximate max entries (~ trim)


# ---------------------------------------------------------------------------
# One-time stream initialisation (called from main.py lifespan)
# ---------------------------------------------------------------------------

async def initialize_stream(redis_client: "aioredis.Redis") -> None:
    """
    Ensure the stream and consumer group exist before any worker starts.

    Uses ``XGROUP CREATE … MKSTREAM`` which atomically creates the stream
    (if absent) and the consumer group in a single round-trip.

    Idempotency
    -----------
    Redis raises ``BUSYGROUP Consumer Group name already exists`` as a
    ``ResponseError`` when the group already exists.  We catch and swallow
    that specific error so that restarting the application is always safe.

    Called from
    -----------
    ``main.py`` lifespan, step 2b (immediately after Redis ping succeeds).
    Must complete before the swarm_listener task is created.
    """
    try:
        # "$" means "deliver only messages added from NOW on" to new consumers.
        # MKSTREAM creates the stream key if it does not yet exist.
        await redis_client.xgroup_create(
            name=PHEROMONE_STREAM,
            groupname=CONSUMER_GROUP,
            id="$",
            mkstream=True,
        )
        logger.info(
            "Redis Stream '%s' + Consumer Group '%s' created",
            PHEROMONE_STREAM, CONSUMER_GROUP,
        )
    except Exception as exc:
        # redis.asyncio raises ResponseError for BUSYGROUP.
        # Any other exception is unexpected — re-raise so lifespan logs it.
        if "BUSYGROUP" in str(exc):
            logger.info(
                "Consumer Group '%s' already exists on stream '%s' — skipping create",
                CONSUMER_GROUP, PHEROMONE_STREAM,
            )
        else:
            logger.error(
                "Failed to initialise stream '%s': %s", PHEROMONE_STREAM, exc
            )
            raise


# ---------------------------------------------------------------------------
# PheromoneService
# ---------------------------------------------------------------------------

class PheromoneService:
    """
    Marks graph nodes with pheromones and appends events to the Redis Stream.

    Usage — hot path (fire-and-forget, zero latency):
    -------------------------------------------------
        import asyncio
        pheromone = PheromoneService(redis_client, graph_service._driver)
        asyncio.create_task(
            pheromone.mark_node_and_notify(node_id, "summariser")
        )

    Usage — awaited (when you need to confirm the event was appended):
    ------------------------------------------------------------------
        await pheromone.mark_node_and_notify(node_id, "enricher")
    """

    # Cypher: stamp the pheromone onto the node.
    _MARK_CYPHER = """
    MATCH (n:SpaiderNode {id: $node_id})
    SET n.needs_agent = $agent_type
    """

    # Cypher: remove the pheromone after successful processing.
    # Also imported directly by swarm_listener without needing an instance.
    REMOVE_CYPHER = """
    MATCH (n:SpaiderNode {id: $node_id})
    REMOVE n.needs_agent
    """

    def __init__(
        self,
        redis_client: "aioredis.Redis",
        neo4j_driver: "AsyncDriver",
    ) -> None:
        self._redis  = redis_client
        self._driver = neo4j_driver

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def mark_node_and_notify(
        self,
        node_id: str,
        agent_type_needed: str,
        session_id: str | None = None,
    ) -> str | None:
        """
        Stamp a pheromone on a graph node and append an event to the stream.

        Steps
        -----
        A) Neo4j write — ``SET n.needs_agent = agent_type_needed``.
           Aborts the whole operation on failure (no phantom stream events).

        B) Redis XADD — appends ``{node_id, agent_type[, session_id]}`` to
           ``pheromone_stream`` with approximate MAXLEN trimming.
           Returns the stream entry ID on success, ``None`` on Redis failure.

        Parameters
        ----------
        node_id:
            UUID of the SpaiderNode to mark (``n.id`` in Neo4j).
        agent_type_needed:
            Specialist worker token, e.g. ``"summariser"``, ``"enricher"``.
        session_id:
            Optional chat session identifier (Context Scent).  When provided,
            the worker that receives this event will load the corresponding
            chat history from Redis before dispatching to the specialist, so
            the specialist has full conversational context for its LLM call.
            Pass ``None`` for background / non-conversational events.

        Returns
        -------
        str | None
            The Redis Stream entry ID (e.g. ``"1701234567890-0"``) on success,
            ``None`` if the Redis append failed (Neo4j marker still committed).
        """
        # ── Step A: Neo4j pheromone write ─────────────────────────────────
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    self._MARK_CYPHER,
                    node_id=node_id,
                    agent_type=agent_type_needed,
                )
                summary = await result.consume()
                logger.debug(
                    "PheromoneService | node=%s stamped needs_agent=%s "
                    "(properties_set=%d)",
                    node_id, agent_type_needed, summary.counters.properties_set,
                )
        except Exception as exc:
            logger.error(
                "PheromoneService | Neo4j write failed for node=%s: %s — "
                "aborting stream append",
                node_id, exc,
            )
            # Do not append to the stream: a stream event without a graph
            # marker causes the worker to REMOVE a non-existent property,
            # which is harmless but produces confusing log noise.
            return None

        # ── Step B: Redis XADD ────────────────────────────────────────────
        # Fields are individual string key-value pairs — no JSON needed.
        # session_id is omitted entirely when not provided so XLEN stays small
        # and workers can cheaply check ``fields.get("session_id")`` for None.
        fields: dict[str, str] = {
            "node_id":    node_id,
            "agent_type": agent_type_needed,
        }
        if session_id is not None:
            fields["session_id"] = session_id

        try:
            entry_id = await self._redis.xadd(
                name=PHEROMONE_STREAM,
                fields=fields,
                maxlen=STREAM_MAXLEN,
                approximate=True,   # ~ trim: O(1) amortised, avoids blocking
            )
            logger.info(
                "PheromoneService | XADD %s entry_id=%s node=%s "
                "agent_type=%s session_id=%s",
                PHEROMONE_STREAM, entry_id, node_id,
                agent_type_needed, session_id or "—",
            )
            return entry_id
        except Exception as exc:
            # Redis failure is non-fatal: the Neo4j marker is committed and
            # XAUTOCLAIM / a scan-on-boot can recover pending entries.
            logger.warning(
                "PheromoneService | XADD failed for node=%s "
                "(Neo4j marker persisted — worker can recover on restart): %s",
                node_id, exc,
            )
            return None

    # ------------------------------------------------------------------
    # Pheromone removal (called by swarm_listener after successful work)
    # ------------------------------------------------------------------

    async def clear_pheromone(self, node_id: str) -> None:
        """
        Remove the ``needs_agent`` pheromone from a node after processing.

        Completes the stigmergic feedback loop: the graph is returned to a
        neutral state so other workers do not re-process the same node.

        The caller (swarm_listener) is responsible for calling ``XACK``
        AFTER this method returns successfully.
        """
        try:
            async with self._driver.session() as session:
                await session.run(self.REMOVE_CYPHER, node_id=node_id)
            logger.debug(
                "PheromoneService | pheromone cleared on node=%s", node_id
            )
        except Exception as exc:
            logger.error(
                "PheromoneService | failed to clear pheromone on node=%s: %s",
                node_id, exc,
            )
            # Re-raise so the caller knows the graph update failed and must
            # NOT send XACK — the message stays in the PEL for retry.
            raise


# ---------------------------------------------------------------------------
# WorkingMemoryService — Context Scent
# ---------------------------------------------------------------------------

# Redis key template for a session's working memory.
_SESSION_KEY = "spaider:session:{session_id}:context"

# How long (seconds) session context survives in Redis after last write.
# 1 hour covers even slow multi-turn conversations without leaking stale data.
_SESSION_TTL_SEC = 3600


class WorkingMemoryService:
    """
    Stores and retrieves per-session chat history ("working memory") in Redis.

    Context Scent
    -------------
    When a pheromone event carries a ``session_id``, the swarm worker loads
    the associated chat history before dispatching to a specialist.  This
    gives the specialist the full conversational context it needs to produce
    a coherent LLM response — without any round-trip to the original HTTP
    request that started the conversation.

    Storage format
    --------------
    Messages are stored as a JSON-serialised list of OpenAI-compatible dicts:

        [
            {"role": "user",      "content": "What is SpAIder?"},
            {"role": "assistant", "content": "SpAIder is a knowledge graph…"},
            ...
        ]

    The same format is consumed directly by litellm / OpenAI clients, so
    specialists can prepend their system prompt and call the LLM immediately.

    Usage
    -----
        wm = WorkingMemoryService(redis_client)

        # In the request handler — persist context after each turn:
        await wm.store_context(session_id, messages)

        # In the swarm worker — load context before specialist dispatch:
        messages = await wm.load_context(session_id)
    """

    def __init__(self, redis_client: "aioredis.Redis") -> None:
        self._redis = redis_client

    def _key(self, session_id: str) -> str:
        return _SESSION_KEY.format(session_id=session_id)

    async def store_context(
        self,
        session_id: str,
        messages: list[dict],
        ttl_sec: int = _SESSION_TTL_SEC,
    ) -> None:
        """
        Persist the chat history for ``session_id``.

        Overwrites any existing context for this session.  The TTL is
        refreshed on every write, so active sessions never expire mid-flow.

        Parameters
        ----------
        session_id:
            Unique identifier for the conversation (e.g. WebSocket client ID,
            HTTP request correlation ID, or user session token).
        messages:
            Full message list in OpenAI format
            ``[{"role": "user"|"assistant"|"system", "content": "…"}, …]``.
        ttl_sec:
            Time-to-live in seconds.  Defaults to ``_SESSION_TTL_SEC`` (1 h).
        """
        try:
            import json as _json
            await self._redis.setex(
                name=self._key(session_id),
                time=ttl_sec,
                value=_json.dumps(messages),
            )
            logger.debug(
                "WorkingMemory | stored %d message(s) for session=%s (ttl=%ds)",
                len(messages), session_id, ttl_sec,
            )
        except Exception as exc:
            # Non-fatal: the specialist will work with an empty context.
            logger.warning(
                "WorkingMemory | store_context failed for session=%s: %s",
                session_id, exc,
            )

    async def load_context(
        self,
        session_id: str | None,
    ) -> list[dict]:
        """
        Load the chat history for ``session_id``.

        Always returns a list — callers never need to guard against ``None``.

        Parameters
        ----------
        session_id:
            Session identifier from the pheromone stream event field.
            Passing ``None`` (event had no session) returns ``[]`` immediately
            without a Redis round-trip.

        Returns
        -------
        list[dict]
            Full message history, or ``[]`` when:
            • ``session_id`` is ``None`` (background event, no conversation).
            • Key not found (session expired or was never stored).
            • Redis is unavailable or returns malformed data.
        """
        if not session_id:
            return []

        try:
            import json as _json
            raw = await self._redis.get(self._key(session_id))
            if raw is None:
                logger.debug(
                    "WorkingMemory | session=%s not found (expired or new)",
                    session_id,
                )
                return []
            messages: list[dict] = _json.loads(raw)
            logger.debug(
                "WorkingMemory | loaded %d message(s) for session=%s",
                len(messages), session_id,
            )
            return messages
        except Exception as exc:
            logger.warning(
                "WorkingMemory | load_context failed for session=%s: %s — "
                "specialist will proceed with empty context",
                session_id, exc,
            )
            return []


# ---------------------------------------------------------------------------
# Swarm Log — Redis Pub/Sub Fan-Out for Frontend Live-Logs
# ---------------------------------------------------------------------------
#
# Architecture rationale
# ----------------------
# The existing ``pheromone_stream`` is a Redis Stream with Consumer Groups,
# meaning each message is delivered to exactly ONE worker (competing-consumer
# pattern).  Reading it from multiple browser tabs would cause them to share
# messages round-robin — each tab seeing only a fraction of the log.
#
# Pub/Sub is a broadcast primitive: every message published to
# ``SWARM_LOG_CHANNEL`` is delivered to ALL active subscribers simultaneously,
# regardless of how many frontend clients are connected.  It is therefore the
# correct mechanism for live-log fan-out.
#
# Message schema (JSON, all fields mandatory)
# -------------------------------------------
#   {
#     "type":      str,   # e.g. "pheromone", "lease", "dispatch", "ack"
#     "agent":     str,   # worker agent_id or specialist name
#     "message":   str,   # human-readable log line
#     "timestamp": str,   # ISO-8601 UTC  (datetime.utcnow().isoformat() + "Z")
#     **kwargs            # optional extra fields (node_id, session_id, etc.)
#   }

import json as _json_module  # noqa: E402  (deferred to avoid a circular import)
from datetime import datetime, timezone  # noqa: E402

# Redis Pub/Sub channel name — shared between publisher and all subscribers.
SWARM_LOG_CHANNEL = "swarm_log_channel"


async def publish_swarm_log(
    redis_client: "aioredis.Redis",
    event_type: str,
    agent: str,
    message: str,
    **kwargs: object,
) -> None:
    """
    Publish a structured log event to the Swarm Log Pub/Sub channel.

    All connected SSE clients (browser tabs) receive the event simultaneously
    via the fan-out pattern.  Publication is fire-and-forget: a Redis failure
    is logged as a warning and does NOT raise, so callers (worker pipeline,
    pheromone service) are never interrupted by a logging hiccup.

    Parameters
    ----------
    redis_client:
        Async Redis client (shared application singleton from main.py).
    event_type:
        Short token identifying the log category, e.g. ``"pheromone"``,
        ``"lease"``, ``"dispatch"``, ``"ack"``, ``"error"``.
    agent:
        Worker or specialist identity string (``_CONSUMER_NAME``, specialist
        name, or ``"system"`` for infrastructure events).
    message:
        Human-readable log line shown in the frontend Live-Log panel.
    **kwargs:
        Optional structured fields attached verbatim to the JSON payload,
        e.g. ``node_id="abc"``, ``session_id="xyz"``.

    Examples
    --------
    >>> await publish_swarm_log(redis, "pheromone", "swarm_worker_3f9a", "Node stamped", node_id="abc123")
    >>> await publish_swarm_log(redis, "ack",       "swarm_worker_3f9a", "XACK sent",   msg_id="1701-0")
    """
    payload: dict[str, object] = {
        "type":      event_type,
        "agent":     agent,
        "message":   message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    try:
        await redis_client.publish(
            SWARM_LOG_CHANNEL,
            _json_module.dumps(payload, ensure_ascii=False),
        )
        logger.debug(
            "SwarmLog | published event_type=%s agent=%s",
            event_type, agent,
        )
    except Exception as exc:
        logger.warning(
            "SwarmLog | publish failed (event_type=%s agent=%s): %s — "
            "log event dropped, pipeline unaffected",
            event_type, agent, exc,
        )


async def subscribe_to_swarm_logs(
    redis_client: "aioredis.Redis",
):
    """
    Async generator: subscribe to the Swarm Log Pub/Sub channel and yield
    raw JSON strings as they arrive.

    Yields
    ------
    str
        The raw JSON string of each incoming Pub/Sub message.  Callers are
        responsible for further parsing (e.g. the SSE endpoint wraps each
        yield in ``data: {raw}\\n\\n``).

    Lifecycle
    ---------
    The generator holds an open Pub/Sub connection for its entire lifetime.
    The ``finally`` block guarantees ``unsubscribe()`` + ``aclose()`` are
    called even if the generator is closed mid-iteration by:
      • the SSE client disconnecting (``request.is_disconnected()``),
      • the server shutting down (``CancelledError``),
      • any unhandled exception in the consumer loop.

    Leaving subscriptions open leaks Redis connections and memory — the
    finally block is therefore not optional.

    Usage
    -----
    ::

        async for raw_json in subscribe_to_swarm_logs(redis_client):
            if await request.is_disconnected():
                break
            yield f"data: {raw_json}\\n\\n"
    """
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(SWARM_LOG_CHANNEL)
    logger.info(
        "SwarmLog | subscriber opened on channel '%s'", SWARM_LOG_CHANNEL
    )
    try:
        while True:
            # Poll with a bounded timeout instead of blocking on pubsub.listen().
            # listen() does a BLOCKING socket read that raises
            # "Timeout reading from redis" on a quiet channel (the connection's
            # read timeout / idle close), which tore the subscription — and the
            # SSE stream on top of it — down every few seconds. get_message()
            # returns None when no message arrives within the window, so the
            # subscription stays alive indefinitely on an idle channel.
            try:
                raw_message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
            except (TimeoutError, ConnectionError) as exc:
                # Transient read timeout: the connection is still usable; keep
                # polling rather than terminating the subscriber.
                logger.debug("SwarmLog | idle poll (%s), continuing", type(exc).__name__)
                continue

            if raw_message is None or raw_message.get("type") != "message":
                continue

            data = raw_message.get("data")
            if not isinstance(data, (str, bytes)):
                continue

            yield data if isinstance(data, str) else data.decode("utf-8")

    finally:
        # Guaranteed cleanup regardless of how the generator exits.
        # Unsubscribing before closing prevents a Redis server-side warning
        # about abruptly closed subscriber connections.
        try:
            await pubsub.unsubscribe(SWARM_LOG_CHANNEL)
            await pubsub.aclose()
        except Exception as exc:
            logger.warning(
                "SwarmLog | cleanup error on subscriber close: %s", exc
            )
        logger.info(
            "SwarmLog | subscriber closed for channel '%s'", SWARM_LOG_CHANNEL
        )
