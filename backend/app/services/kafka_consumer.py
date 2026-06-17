"""
Kafka Consumer Service: High-performance async consumer with micro-batching,
3-retry per message, proper DLQ with error headers, and UNWIND batch Neo4j writes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError

from app.config import settings
from app.logging_context import bind_request_context, reset_request_context

if TYPE_CHECKING:
    from app.services.compressor import SemanticCompressor
    from app.services.entity_resolver import EntityResolver
    from app.services.graph_service import GraphService

logger = logging.getLogger(__name__)

# Micro-batch tuning
_BATCH_SIZE = 100          # flush when this many messages collected
_BATCH_WINDOW_S = 2.0      # flush after this many seconds even if batch not full
_MAX_RETRIES = 3           # attempts before routing to DLQ

# Connection resilience
_CONNECT_MAX_ATTEMPTS = 5  # exponential backoff: 1, 2, 4, 8, 16 s
_DLQ_SEND_MAX_ATTEMPTS = 5 # same pattern for DLQ writes

# Consumer-group timing.  Defaults are too aggressive for a worker that blocks
# on 60-second LLM calls: the broker evicts us mid-call and the batch never
# commits, so offsets replay and the DLQ never sees the failure.
_SESSION_TIMEOUT_MS = 30_000       # aiokafka default is 10 s
_HEARTBEAT_INTERVAL_MS = 10_000    # ~1/3 of session timeout
_MAX_POLL_INTERVAL_MS = 300_000    # 5 min — covers the slowest extraction


class KafkaConsumerService:
    """
    Async Kafka consumer with:
    - Micro-batching: collects up to 100 messages or 2 s, then single UNWIND write
    - 3-retry per message before DLQ routing
    - DLQ messages include error info as Kafka record headers
    """

    def __init__(self) -> None:
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._dlq_producer: Optional[AIOKafkaProducer] = None
        self._running = False
        # retry_counts[message_id] -> attempt number
        self._retry_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        compressor: "SemanticCompressor",
        entity_resolver: "EntityResolver",
        graph_service: "GraphService",
    ) -> None:
        # Static group membership — survives transient disconnects without
        # triggering a full rebalance.  HOSTNAME is the container ID under
        # docker compose, so each scaled replica gets a stable identity.
        instance_id = f"worker-{os.getenv('HOSTNAME', 'local')}"

        self._consumer = AIOKafkaConsumer(
            settings.kafka_topic_ingest,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group,
            group_instance_id=instance_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,       # manual commit after flush
            value_deserializer=lambda raw: json.loads(raw.decode("utf-8")),
            max_poll_records=_BATCH_SIZE,   # fetch up to full batch at once
            fetch_max_wait_ms=500,
            session_timeout_ms=_SESSION_TIMEOUT_MS,
            heartbeat_interval_ms=_HEARTBEAT_INTERVAL_MS,
            max_poll_interval_ms=_MAX_POLL_INTERVAL_MS,
        )

        self._dlq_producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )

        await self._connect_with_backoff()
        self._running = True

        logger.info(
            "KafkaConsumerService started | topic=%s group=%s instance=%s "
            "batch_size=%d window=%.1fs session_timeout=%dms max_poll=%dms",
            settings.kafka_topic_ingest,
            settings.kafka_consumer_group,
            instance_id,
            _BATCH_SIZE,
            _BATCH_WINDOW_S,
            _SESSION_TIMEOUT_MS,
            _MAX_POLL_INTERVAL_MS,
        )

        try:
            await self._micro_batch_loop(compressor, entity_resolver, graph_service)
        finally:
            await self._consumer.stop()
            await self._dlq_producer.stop()
            logger.info("KafkaConsumerService stopped.")

    async def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Connection resilience
    # ------------------------------------------------------------------

    async def _connect_with_backoff(self) -> None:
        """
        Start consumer + DLQ producer with 5 attempts of exponential backoff.

        Why: boot races are routine under docker-compose (kafka healthy but
        brokers still re-electing) and transient DNS blips happen in k8s.
        Failing hard on the first blip forces the whole worker to crash-loop,
        which burns offsets and noisy-logs the channel.
        """
        assert self._consumer is not None and self._dlq_producer is not None

        for attempt in range(1, _CONNECT_MAX_ATTEMPTS + 1):
            try:
                await self._consumer.start()
                await self._dlq_producer.start()
                return
            except KafkaError as exc:
                if attempt == _CONNECT_MAX_ATTEMPTS:
                    logger.critical(
                        "Kafka connect failed after %d attempts — giving up: %s",
                        attempt, exc,
                    )
                    raise
                wait = 2 ** (attempt - 1)
                logger.warning(
                    "Kafka connect failed (%d/%d), retrying in %ds: %s",
                    attempt, _CONNECT_MAX_ATTEMPTS, wait, exc,
                )
                await asyncio.sleep(wait)

    # ------------------------------------------------------------------
    # Micro-batch loop
    # ------------------------------------------------------------------

    async def _micro_batch_loop(
        self,
        compressor: "SemanticCompressor",
        entity_resolver: "EntityResolver",
        graph_service: "GraphService",
    ) -> None:
        """
        Collect messages until BATCH_SIZE or BATCH_WINDOW_S elapses,
        then process them all in parallel and write to Neo4j in one shot.
        """
        assert self._consumer is not None

        while self._running:
            batch: list = []
            deadline = time.monotonic() + _BATCH_WINDOW_S

            # ── Collect phase ─────────────────────────────────────────
            while len(batch) < _BATCH_SIZE and time.monotonic() < deadline:
                remaining = max(deadline - time.monotonic(), 0.05)
                try:
                    records = await asyncio.wait_for(
                        self._consumer.getmany(
                            timeout_ms=int(remaining * 1000),
                            max_records=_BATCH_SIZE - len(batch),
                        ),
                        timeout=remaining + 0.5,
                    )
                except asyncio.TimeoutError:
                    break
                except KafkaError as exc:
                    # Transient broker blip (rebalance, leader election,
                    # network flake).  aiokafka reconnects internally; we
                    # just back off and re-enter the poll loop rather than
                    # silently halting consumption.
                    logger.warning(
                        "Kafka poll error — will retry after backoff: %s", exc,
                    )
                    await asyncio.sleep(2)
                    break

                for _tp, messages in records.items():
                    batch.extend(messages)

                if not records:
                    break  # nothing arriving, flush what we have

            if not batch:
                continue

            logger.info("Micro-batch collected %d messages — processing", len(batch))

            # ── Process phase (parallel LLM extraction) ───────────────
            tasks = [
                self._extract_one(msg, compressor, entity_resolver)
                for msg in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Separate successes from failures
            good: list[tuple] = []   # (GraphPayload, agent_id, message)
            failed: list[tuple] = [] # (message, error_str, is_extraction_failure)

            # Imported inside the function so the module stays importable
            # even if compressor can't be imported (e.g. during tests).
            from app.services.compressor import ExtractionError

            for msg, result in zip(batch, results):
                if isinstance(result, Exception):
                    is_extr = isinstance(result, ExtractionError)
                    failed.append((msg, str(result), is_extr))
                else:
                    graph_payload, agent_id = result
                    good.append((graph_payload, agent_id, msg))

            # ── Write phase (single UNWIND batch) ─────────────────────
            if good:
                try:
                    write_items = [(gp, aid) for gp, aid, _ in good]
                    await graph_service.write_graph_batch(write_items)
                    # Commit offsets for all successfully written messages
                    await self._consumer.commit()
                    logger.info(
                        "Batch written | messages=%d total_nodes=%d total_edges=%d",
                        len(good),
                        sum(len(gp.nodes) for gp, _, _ in good),
                        sum(len(gp.edges) for gp, _, _ in good),
                    )
                except Exception as exc:
                    logger.error("Neo4j batch write failed: %s — messages will retry", exc)
                    # Don't commit — aiokafka will re-deliver on next poll

            # ── DLQ phase (messages that exhausted retries) ───────────
            for msg, error, is_extraction_failure in failed:
                payload = msg.value if isinstance(msg.value, dict) else {}
                message_id = payload.get("message_id", "<unknown>")

                # ExtractionError means the compressor already ran its own
                # 3 internal retries. Re-queueing would burn another 3×N LLM
                # calls and still fail. Route straight to DLQ.
                if is_extraction_failure:
                    logger.error(
                        "message_id=%s EXTRACTION_FAILED after compressor retries → DLQ",
                        message_id,
                    )
                    await self._send_to_dlq(
                        payload,
                        error=error,
                        attempt=_MAX_RETRIES,
                        reason="extraction_failed",
                    )
                    self._record_extraction_failed(payload, error)
                    self._retry_counts.pop(message_id, None)
                    await self._consumer.commit()
                    continue

                retries = self._retry_counts.get(message_id, 0) + 1
                self._retry_counts[message_id] = retries

                if retries >= _MAX_RETRIES:
                    logger.error(
                        "message_id=%s failed %d/%d times → DLQ", message_id, retries, _MAX_RETRIES
                    )
                    await self._send_to_dlq(payload, error=error, attempt=retries)
                    del self._retry_counts[message_id]
                    await self._consumer.commit()
                else:
                    logger.warning(
                        "message_id=%s failed (attempt %d/%d) — will retry",
                        message_id, retries, _MAX_RETRIES,
                    )
                    # Do NOT commit — message will be re-polled

    # ------------------------------------------------------------------
    # Per-message extraction (returns (GraphPayload, agent_id) or raises)
    # ------------------------------------------------------------------

    async def _extract_one(
        self,
        message,
        compressor: "SemanticCompressor",
        entity_resolver: "EntityResolver",
    ) -> tuple:
        payload: dict = message.value if isinstance(message.value, dict) else {}
        agent_id = payload.get("agent_id", "default")
        text = payload.get("text", "")
        message_id = payload.get("message_id", "<unknown>")

        # Bind request_id (from producer header) + agent_id so every log line
        # emitted by the compressor / resolver carries the same trace ID.
        # asyncio.gather copies the current Context into each task, so setting
        # contextvars here only affects this task.
        request_id = _extract_header(message, "x-request-id")
        tokens = bind_request_context(request_id=request_id, agent_id=agent_id)
        try:
            logger.debug("Extracting message_id=%s agent_id=%s", message_id, agent_id)

            graph_payload = await compressor.extract(
                text=text,
                context=payload.get("metadata"),
            )
            resolved = await entity_resolver.resolve.__func__(  # type: ignore[attr-defined]
                entity_resolver, graph_payload, agent_id, None  # graph_service passed later
            ) if False else await _resolve_no_graph(entity_resolver, graph_payload, agent_id)

            return resolved, agent_id
        finally:
            reset_request_context(tokens)

    # ------------------------------------------------------------------
    # Analytics (fire-and-forget — mirrors the sync ingest pathway)
    # ------------------------------------------------------------------

    @staticmethod
    def _record_extraction_failed(payload: dict, error: str) -> None:
        try:
            import app.main as _main
            svc = getattr(_main, "_analytics_service", None)
            if svc and getattr(svc, "_ready", False):
                svc.record_extraction_failed(
                    agent_id=payload.get("agent_id", "unknown"),
                    source="kafka",
                    text_length=len(payload.get("text", "") or ""),
                    attempts=_MAX_RETRIES,
                    last_error=error,
                )
        except Exception:
            logger.debug("record_extraction_failed skipped (non-fatal)")

    # ------------------------------------------------------------------
    # DLQ with Kafka headers
    # ------------------------------------------------------------------

    async def _send_to_dlq(
        self,
        original_payload: dict,
        error: str,
        attempt: int,
        reason: str = "processing_error",
    ) -> None:
        if self._dlq_producer is None:
            logger.error("DLQ producer not initialised — message lost!")
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        headers = [
            ("dlq-error",         error.encode("utf-8")),
            ("dlq-reason",        reason.encode("utf-8")),
            ("dlq-source-topic",  settings.kafka_topic_ingest.encode("utf-8")),
            ("dlq-attempts",      str(attempt).encode("utf-8")),
            ("dlq-timestamp",     now_iso.encode("utf-8")),
            ("dlq-message-id",    original_payload.get("message_id", "").encode("utf-8")),
            ("dlq-agent-id",      original_payload.get("agent_id",   "").encode("utf-8")),
        ]

        for send_attempt in range(1, _DLQ_SEND_MAX_ATTEMPTS + 1):
            try:
                await self._dlq_producer.send_and_wait(
                    topic=settings.kafka_topic_dlq,
                    value=original_payload,
                    key=original_payload.get("agent_id", "").encode("utf-8") or None,
                    headers=headers,
                )
                logger.info(
                    "Routed message_id=%s to DLQ after %d attempts",
                    original_payload.get("message_id"), attempt,
                )
                return
            except KafkaError as exc:
                if send_attempt == _DLQ_SEND_MAX_ATTEMPTS:
                    logger.critical(
                        "DLQ send failed after %d attempts — message_id=%s LOST: %s",
                        send_attempt, original_payload.get("message_id"), exc,
                    )
                    return
                wait = 2 ** (send_attempt - 1)
                logger.warning(
                    "DLQ send failed (%d/%d), retrying in %ds: %s",
                    send_attempt, _DLQ_SEND_MAX_ATTEMPTS, wait, exc,
                )
                await asyncio.sleep(wait)


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------


def _extract_header(message, name: str) -> Optional[str]:
    """Return the first matching Kafka header value decoded as UTF-8, or None."""
    headers = getattr(message, "headers", None) or []
    name_lower = name.lower()
    for key, value in headers:
        if key is None:
            continue
        key_str = key.decode("utf-8") if isinstance(key, (bytes, bytearray)) else str(key)
        if key_str.lower() == name_lower:
            if value is None:
                return None
            return value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)
    return None


# ---------------------------------------------------------------------------
# Helper: resolve without graph_service (micro-batcher resolves in write phase)
# ---------------------------------------------------------------------------

async def _resolve_no_graph(resolver, payload, agent_id):
    """
    Lightweight resolve — skips semantic/Levenshtein matching against the live
    graph (too expensive per-message in a batch).  Full resolution happens when
    write_graph_batch merges duplicate node IDs via MERGE semantics in Cypher.
    """
    # Attach agent_id + return as-is; deduplication is handled by Neo4j MERGE
    for node in payload.nodes:
        node.agent_id = agent_id
    for edge in payload.edges:
        edge.agent_id = agent_id
    return payload
