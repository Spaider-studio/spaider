"""
Connector Framework — base classes and shared utilities.

Vocabulary
----------
ConnectorRecord
    One successfully parsed document yielded by a connector.  Contains
    the extracted ``text``, its MIME type, format hints from the parser,
    and enough provenance metadata for the ingest pipeline to attach
    correct Neo4j properties.

RunState
    Per-connector mutable state bag that survives across incremental sync
    runs.  Each connector stores whatever it needs inside
    ``source_states[source_uri]`` (e.g. ETags, Last-Modified headers,
    cursor tokens).  Callers are responsible for persisting the mutated
    object after a run completes (e.g. to Redis or a DB row).

BaseConnector
    Abstract base that every connector subclasses.  Declares the
    ``connector_id`` class variable and the ``run()`` async generator
    contract.

send_to_dlq()
    Best-effort fire-and-forget helper.  Sends a failed record to the
    shared Kafka DLQ topic (``spaider.ingest.dlq``) with standardised
    headers.  Swallows all exceptions so a DLQ failure never aborts the
    connector run.
"""
from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, ClassVar, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connector run statistics (written by route handlers, read by status endpoint)
# ---------------------------------------------------------------------------

class ConnectorStats(BaseModel):
    """Last-run statistics for one connector — updated after every completed run."""

    connector_id: str
    status: str = "idle"                  # "idle" | "done" | "error"
    last_run_at: Optional[str] = None     # ISO-8601 UTC timestamp
    records_processed: int = 0
    last_error: Optional[str] = None


# Module-level store — single-process, no lock needed (asyncio event loop is
# single-threaded).  Replace with a Redis hash for multi-process deployments.
_connector_stats: dict[str, ConnectorStats] = {}


def record_connector_run(
    connector_id: str,
    records_processed: int,
    error: Optional[str] = None,
) -> None:
    """
    Called by route handlers at the end of every connector run to update
    the in-process stats store.  Safe to call from any async context.
    """
    from datetime import datetime, timezone

    _connector_stats[connector_id] = ConnectorStats(
        connector_id=connector_id,
        status="error" if error else "done",
        last_run_at=datetime.now(timezone.utc).isoformat(),
        records_processed=records_processed,
        last_error=error,
    )


def get_connector_stats(connector_id: str) -> ConnectorStats:
    """
    Return stats for *connector_id*.  Returns a zeroed idle record if no
    run has been recorded yet (i.e. server just started).
    """
    return _connector_stats.get(
        connector_id,
        ConnectorStats(connector_id=connector_id),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Global registry reference — set by the ingest route module at startup so
# the status endpoint can validate connector IDs without importing ingest.py.
_global_registry: Optional["ConnectorRegistry"] = None


def set_global_registry(registry: "ConnectorRegistry") -> None:
    global _global_registry
    _global_registry = registry


def get_global_registry() -> Optional["ConnectorRegistry"]:
    return _global_registry


class ConnectorRegistry:
    """
    Central catalogue of registered connector instances.

    Connectors are registered once at application startup.  The registry is
    intentionally simple — it does not manage lifecycle (start/stop) because
    connectors are stateless objects whose only stateful surface is RunState.

    Usage::

        registry = ConnectorRegistry()
        registry.register(UploadConnector())
        registry.register(URLConnector())

        connector = registry.get("upload")       # → UploadConnector instance
        print(registry.connector_ids)            # → ["upload", "url"]
    """

    def __init__(self) -> None:
        self._connectors: dict[str, "BaseConnector"] = {}

    def register(self, connector: "BaseConnector") -> None:
        """Register a connector instance.  Replaces any existing entry with the same id."""
        self._connectors[connector.connector_id] = connector
        logger.debug("ConnectorRegistry: registered connector_id=%r", connector.connector_id)

    def get(self, connector_id: str) -> Optional["BaseConnector"]:
        """Return the connector with the given id, or None if not registered."""
        return self._connectors.get(connector_id)

    @property
    def connector_ids(self) -> list[str]:
        """Sorted list of registered connector IDs."""
        return sorted(self._connectors.keys())


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ConnectorRecord(BaseModel):
    """One successfully parsed document yielded by a connector run."""

    connector_id: str = Field(..., description="ID of the connector that produced this record.")
    source_uri: str = Field(..., description="Filename, URL, or other stable identifier.")
    text: str = Field(..., description="Extracted plain text — fed directly to SemanticCompressor.")
    mime_type: str = Field(..., description="Original MIME type of the source content.")
    hints: dict[str, Any] = Field(
        default_factory=dict,
        description="Parser hints (page_count, title, etc.) — surfaced as Neo4j node properties.",
    )
    agent_id: str = Field(..., description="Owning agent namespace.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary extra metadata attached by the connector (e.g. HTTP status, filename).",
    )
    record_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Stable UUID assigned at yield time — used as DLQ message_id if later routed.",
    )


class RunState(BaseModel):
    """
    Mutable state for incremental-sync connectors.

    ``source_states`` maps each source URI to a free-form dict that the
    connector reads before making a request (to send conditional headers)
    and updates after a 200 response (to store the new ETag/Last-Modified).

    Example::

        {
            "https://example.com/docs": {
                "etag": '"abc123"',
                "last_modified": "Wed, 21 Oct 2025 07:28:00 GMT",
            }
        }
    """

    connector_id: str
    source_states: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract base connector
# ---------------------------------------------------------------------------

class BaseConnector(ABC):
    """
    All connectors inherit from this class.

    Subclasses must declare a ``connector_id`` class variable and implement
    ``run()``.  The ``run()`` method is an async generator: it ``yield``s
    one ``ConnectorRecord`` per successfully parsed source item.  Failed
    items must be routed to the DLQ via ``send_to_dlq()`` and then
    ``continue``d — they must never propagate an exception out of the
    generator, as that would abort processing for all remaining items.
    """

    connector_id: ClassVar[str]

    @abstractmethod
    async def run(
        self,
        agent_id: str,
        run_state: RunState,
        **kwargs: Any,
    ) -> AsyncGenerator[ConnectorRecord, None]:
        """
        Yield one ConnectorRecord per successfully parsed item.

        Implementations must be async generators (contain at least one
        ``yield`` statement).  They own the RunState mutation for their
        source type.
        """
        # pragma: no cover — abstract; yield needed so type-checkers see
        # return type as AsyncGenerator rather than Coroutine.
        raise NotImplementedError
        yield  # type: ignore[misc]  # makes this an AsyncGenerator at the type level


# ---------------------------------------------------------------------------
# DLQ helper
# ---------------------------------------------------------------------------

# Lazy-singleton DLQ producer — mirrors the pattern used in kafka_producer.py
# and the Kafka consumer's `_dlq_producer`.  Never imported at module level to
# avoid import errors when aiokafka is unavailable in test environments.
_dlq_producer = None
_dlq_producer_starting = False


async def _get_dlq_producer():
    """
    Return a started AIOKafkaProducer for the DLQ topic.

    Returns None if Kafka is unavailable so callers can degrade gracefully.
    Not thread-safe, but the entire service runs on a single asyncio event
    loop, so concurrent initialisations are not possible.
    """
    global _dlq_producer, _dlq_producer_starting

    if _dlq_producer is not None:
        return _dlq_producer

    if _dlq_producer_starting:
        # Another coroutine is already initialising — don't double-start.
        return None

    _dlq_producer_starting = True
    try:
        from aiokafka import AIOKafkaProducer  # type: ignore[import]

        from app.config import settings

        producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            acks="all",
            enable_idempotence=True,
        )
        await producer.start()
        _dlq_producer = producer
        logger.info("Connector DLQ producer started.")
        return _dlq_producer
    except Exception as exc:
        logger.warning("Connector DLQ producer unavailable: %s", exc)
        return None
    finally:
        _dlq_producer_starting = False


async def send_to_dlq(
    *,
    payload: dict[str, Any],
    connector_id: str,
    source_uri: str,
    agent_id: str,
    error: str,
    reason: str = "parse_error",
) -> None:
    """
    Route a failed connector item to the Kafka DLQ topic.

    Never raises — DLQ routing is best-effort.  A failure here is logged at
    ERROR level but the connector run continues uninterrupted.

    Headers written to the DLQ record
    -----------------------------------
    dlq-connector-id  — which connector produced the failure
    dlq-source-uri    — filename, URL, etc.
    dlq-error         — exception message
    dlq-reason        — human tag (e.g. ``parse_error``, ``http_error``)
    dlq-timestamp     — ISO-8601 UTC
    dlq-message-id    — UUID of the attempted record
    dlq-agent-id      — owning agent namespace
    """
    try:
        from app.config import settings

        producer = await _get_dlq_producer()
        if producer is None:
            logger.error(
                "DLQ unavailable — connector_id=%s source=%r error dropped: %s",
                connector_id, source_uri, error,
            )
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        message_id = payload.get("record_id", str(uuid.uuid4()))

        headers = [
            ("dlq-connector-id", connector_id.encode("utf-8")),
            ("dlq-source-uri",   source_uri.encode("utf-8")),
            ("dlq-error",        error[:2000].encode("utf-8")),
            ("dlq-reason",       reason.encode("utf-8")),
            ("dlq-timestamp",    now_iso.encode("utf-8")),
            ("dlq-message-id",   message_id.encode("utf-8")),
            ("dlq-agent-id",     agent_id.encode("utf-8")),
        ]

        await producer.send_and_wait(
            topic=settings.kafka_topic_dlq,
            value=payload,
            key=agent_id or None,
            headers=headers,
        )
        logger.info(
            "Connector DLQ: routed failed item connector_id=%s source=%r",
            connector_id, source_uri,
        )
    except Exception as exc:  # noqa: BLE001
        # A DLQ failure must never propagate — the connector run continues.
        logger.error(
            "Connector DLQ send failed (item will be lost) connector_id=%s source=%r: %s",
            connector_id, source_uri, exc,
        )
