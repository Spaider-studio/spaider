"""
ConnectorScheduler — polling orchestrator for the Connector Framework.

Responsibilities
----------------
1. **Polling loop** (``run_connector_loop``): wakes every
   ``POLL_INTERVAL_SECONDS``, queries Postgres for ``ConnectorConfig`` rows
   whose last run is overdue, and spawns one background ``asyncio.Task`` per
   due connector so slow connectors never block each other.

2. **Per-connector pipeline** (``process_connector``): for each due
   connector, streams ``ConnectorRecord`` objects from ``ConnectorRunner``,
   offloads parsing to a thread via ``parser_service.parse`` (``to_thread``),
   and publishes the enriched payload to Kafka via ``KafkaProducerService``.

Isolation guarantees
--------------------
- **Connector-level isolation**: the entire ``process_connector`` coroutine
  is wrapped in ``try/except`` — one crashing connector never kills the
  scheduler loop or any sibling task.
- **Record-level isolation**: each record's parse + publish step is wrapped
  in its own ``try/except`` inside the async-for loop — one bad document
  never aborts the remaining stream for that connector.
- **Parsing is CPU-bound**: ``parser_service.parse`` uses
  ``asyncio.to_thread`` internally so Docling / Trafilatura never block
  the event loop.

Configuration
-------------
``POLL_INTERVAL_SECONDS``  How often the loop wakes (default: 300 s / 5 min).
``SYNC_INTERVAL_SECONDS``  Minimum gap between two runs of the same connector
                           (default: 3600 s / 1 hour).  A connector is
                           considered "due" if ``last_run_at IS NULL`` or
                           ``now - last_run_at >= SYNC_INTERVAL_SECONDS``.
``MAX_CONCURRENT_CONNECTORS``
                           Cap on simultaneously running connector tasks
                           (default: 10).  A semaphore prevents bursts of
                           newly-registered connectors from exhausting the
                           thread pool or Kafka producer connections.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import ConnectorRegistry, get_global_registry
from app.connectors.runner import ConnectorRunner
from app.db.postgres import async_session_factory
from app.models.connector import ConnectorConfig, ConnectorRunState
from app.services import parser_service
from app.services.kafka_producer import KafkaProducerService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS: int = 300       # wake every 5 minutes
SYNC_INTERVAL_SECONDS: int = 3_600     # connectors run at most once per hour
MAX_CONCURRENT_CONNECTORS: int = 10    # semaphore cap


# ---------------------------------------------------------------------------
# ConnectorScheduler
# ---------------------------------------------------------------------------


class ConnectorScheduler:
    """
    Long-running scheduler that polls Postgres and drives connector runs.

    Lifecycle
    ---------
    Call ``start()`` once (e.g. from ``worker.py``) and ``stop()`` on
    graceful shutdown.  ``start()`` blocks until ``stop()`` is called.

    Parameters
    ----------
    registry:
        ``ConnectorRegistry`` holding all registered connector classes.
        Defaults to the process-level global registry.
    kafka_producer:
        A started ``KafkaProducerService`` instance.  The scheduler does NOT
        call ``start()`` / ``stop()`` on it — the caller owns the lifecycle.
    poll_interval:
        Seconds between polling cycles.  Override in tests.
    sync_interval:
        Seconds a connector must wait between runs.  Override in tests.
    """

    def __init__(
        self,
        registry: Optional[ConnectorRegistry] = None,
        kafka_producer: Optional[KafkaProducerService] = None,
        poll_interval: int = POLL_INTERVAL_SECONDS,
        sync_interval: int = SYNC_INTERVAL_SECONDS,
    ) -> None:
        self._registry: ConnectorRegistry = registry or get_global_registry() or ConnectorRegistry()
        self._kafka: KafkaProducerService = kafka_producer or KafkaProducerService()
        self._poll_interval = poll_interval
        self._sync_interval = sync_interval
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONNECTORS)
        self._stop_event = asyncio.Event()
        self._active_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the polling loop.  Blocks until ``stop()`` is called."""
        logger.info(
            "ConnectorScheduler starting — poll_interval=%ds sync_interval=%ds",
            self._poll_interval,
            self._sync_interval,
        )
        await self._run_loop()

    async def stop(self) -> None:
        """Signal the loop to exit and await any in-flight tasks."""
        logger.info("ConnectorScheduler stop requested.")
        self._stop_event.set()
        if self._active_tasks:
            logger.info(
                "ConnectorScheduler: waiting for %d active task(s) to finish.",
                len(self._active_tasks),
            )
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
        logger.info("ConnectorScheduler stopped cleanly.")

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """
        Main polling loop.

        Each cycle:
        1. Query Postgres for due ``ConnectorConfig`` rows.
        2. Spawn one ``asyncio.Task`` per due connector (semaphore-guarded).
        3. Sleep for ``_poll_interval`` seconds (or until ``stop()``).
        """
        while not self._stop_event.is_set():
            try:
                await self._poll_and_dispatch()
            except Exception as exc:
                # A DB failure in the polling query itself must not crash the
                # loop — log and wait for the next cycle.
                logger.error(
                    "ConnectorScheduler: polling cycle failed: %s", exc, exc_info=True
                )

            # Sleep in short ticks so stop() is acknowledged promptly
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=float(self._poll_interval),
                )
            except asyncio.TimeoutError:
                pass  # normal path — sleep elapsed, run next cycle

    async def _poll_and_dispatch(self) -> None:
        """Query for due connectors and spawn a task for each."""
        async with async_session_factory() as db:
            due_configs = await self._fetch_due_connectors(db)

        if not due_configs:
            logger.debug("ConnectorScheduler: no connectors due this cycle.")
            return

        logger.info(
            "ConnectorScheduler: %d connector(s) due this cycle.", len(due_configs)
        )

        for config_id, agent_id, type_id in due_configs:
            task = asyncio.create_task(
                self._process_connector_guarded(config_id, agent_id, type_id),
                name=f"connector-{type_id}-{config_id[:8]}",
            )
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)

    async def _fetch_due_connectors(
        self, db: AsyncSession
    ) -> list[tuple[str, str, str]]:
        """
        Return ``(connector_config_id, agent_id, type_id)`` tuples for every
        ``ConnectorConfig`` that is due for a run.

        A connector is due when:
        - It has no ``ConnectorRunState`` row  (never run), OR
        - ``last_run_at IS NULL``, OR
        - ``now - last_run_at >= sync_interval``

        Uses a LEFT OUTER JOIN so connectors with no run-state row are
        included without a second query.
        """
        now = datetime.now(timezone.utc)

        result = await db.execute(
            select(
                ConnectorConfig.id,
                ConnectorConfig.agent_id,
                ConnectorConfig.type_id,
                ConnectorRunState.last_run_at,
            ).outerjoin(
                ConnectorRunState,
                ConnectorRunState.connector_id == ConnectorConfig.id,
            )
        )
        rows = result.all()

        due: list[tuple[str, str, str]] = []
        for config_id, agent_id, type_id, last_run_at in rows:
            if last_run_at is None:
                due.append((config_id, agent_id, type_id))
                continue
            elapsed = (now - last_run_at).total_seconds()
            if elapsed >= self._sync_interval:
                due.append((config_id, agent_id, type_id))

        return due

    # ------------------------------------------------------------------
    # Per-connector orchestration — connector-level isolation
    # ------------------------------------------------------------------

    async def _process_connector_guarded(
        self,
        connector_config_id: str,
        agent_id: str,
        type_id: str,
    ) -> None:
        """
        Connector-level isolation wrapper.

        Acquires the semaphore before entering ``_process_connector`` so at
        most ``MAX_CONCURRENT_CONNECTORS`` connectors run simultaneously.
        Any exception from ``_process_connector`` is caught here and logged —
        it NEVER propagates to the scheduler loop.
        """
        async with self._semaphore:
            try:
                await self._process_connector(
                    connector_config_id, agent_id, type_id
                )
            except Exception as exc:
                logger.error(
                    "ConnectorScheduler: connector type_id=%s config_id=%s "
                    "crashed — isolated from scheduler loop: %s",
                    type_id,
                    connector_config_id,
                    exc,
                    exc_info=True,
                )

    async def _process_connector(
        self,
        connector_config_id: str,
        agent_id: str,
        type_id: str,
    ) -> None:
        """
        Full pipeline for one connector run:

        1. Instantiate ``ConnectorRunner`` and open a DB session.
        2. Stream ``ConnectorRecord`` objects from the runner.
        3. For each record: parse (CPU offloaded) → build payload → publish
           to Kafka.  Record-level errors are caught and skipped.

        The ``ConnectorRunner`` owns ``RunState`` persistence (commits to
        Postgres in its own ``finally`` block) so we do not need to manage
        that here.
        """
        logger.info(
            "ConnectorScheduler: starting run type_id=%s config_id=%s agent_id=%s",
            type_id, connector_config_id, agent_id,
        )

        records_published = 0
        records_failed = 0

        async with async_session_factory() as db:
            runner = ConnectorRunner(
                registry=self._registry,
                # SecretsService singleton used internally by runner
            )

            record_stream = await runner.run_connector(connector_config_id, db)

            async for record in record_stream:

                # ── Record-level isolation ─────────────────────────────────
                try:
                    # Parse: CPU-bound — offloaded to thread via to_thread
                    parse_result = await parser_service.parse(
                        record.content,
                        record.content_type,
                    )

                    if not parse_result.text.strip():
                        logger.debug(
                            "scheduler: empty parse result for source=%r — skipping.",
                            record.source_uri,
                        )
                        continue

                    # Build Kafka payload — merge parser hints into metadata
                    # so the SemanticCompressor LLM receives structural context
                    # (heading_count, page_count, title, etc.) alongside the text.
                    enriched_metadata = {
                        **record.metadata,
                        "parser_hints": parse_result.hints,
                        "external_id": record.external_id,
                        "source_uri": record.source_uri,
                        "content_type": record.content_type,
                        "title": record.title,
                        "connector_type": type_id,
                        "connector_config_id": connector_config_id,
                    }

                    await self._kafka.produce_ingest_event(
                        text=parse_result.text,
                        agent_id=agent_id,
                        source=record.source_uri,
                        metadata=enriched_metadata,
                    )
                    records_published += 1

                    logger.debug(
                        "scheduler: published source=%r agent_id=%s "
                        "chars=%d",
                        record.source_uri,
                        agent_id,
                        len(parse_result.text),
                    )

                except Exception as rec_exc:
                    records_failed += 1
                    logger.error(
                        "scheduler: record-level error type_id=%s "
                        "source=%r — skipping record: %s",
                        type_id,
                        record.source_uri,
                        rec_exc,
                        exc_info=True,
                    )
                    continue  # next record — do not abort the connector run

        logger.info(
            "ConnectorScheduler: run complete type_id=%s config_id=%s "
            "agent_id=%s published=%d failed=%d",
            type_id,
            connector_config_id,
            agent_id,
            records_published,
            records_failed,
        )


# ---------------------------------------------------------------------------
# Module-level convenience for worker.py
# ---------------------------------------------------------------------------

async def run_connector_loop(
    registry: Optional[ConnectorRegistry] = None,
    kafka_producer: Optional[KafkaProducerService] = None,
    poll_interval: int = POLL_INTERVAL_SECONDS,
    sync_interval: int = SYNC_INTERVAL_SECONDS,
) -> None:
    """
    Convenience coroutine — constructs a ``ConnectorScheduler`` and starts it.

    Designed for ``asyncio.create_task(run_connector_loop(...))`` in
    ``worker.py`` so it runs alongside the Kafka consumer loop::

        asyncio.create_task(run_connector_loop(
            registry=registry,
            kafka_producer=producer,
        ))

    Parameters
    ----------
    registry:
        Connector plugin catalogue.  Defaults to the global registry.
    kafka_producer:
        A started ``KafkaProducerService``.  The caller owns its lifecycle.
    poll_interval:
        Seconds between polling cycles.
    sync_interval:
        Minimum seconds between two runs of the same connector.
    """
    scheduler = ConnectorScheduler(
        registry=registry,
        kafka_producer=kafka_producer,
        poll_interval=poll_interval,
        sync_interval=sync_interval,
    )
    await scheduler.start()
