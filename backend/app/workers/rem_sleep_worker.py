"""
REM-Sleep Worker — per-agent, frequency-driven consolidation ("hibernation").

Why this exists
---------------
The default consolidation surface is the weekly Airflow DAG
(``airflow/dags/graph_maintenance_dag.py``, cron ``"0 3 * * 0"``) that runs
over the whole store at once. That cadence is one-size-fits-all: a heavy
personal agent may want to consolidate daily while an archival agent is fine
untouched for weeks.

This worker gives every agent its OWN cadence. Each ``SystemAgent`` node
carries ``consolidation_interval_hours`` (0 = off) and ``last_consolidated_at``.
A background loop wakes every ``consolidation_scheduler_interval_s`` seconds,
finds the agents whose interval has elapsed, and runs a per-agent sleep pass
(prune orphans, fuse duplicates, decay unused synapses, optionally propose
inverse edges), then stamps ``last_consolidated_at``.

Safe on by default: every agent defaults to interval 0 (off), so the loop is a
no-op until an agent opts into a cadence (or a "consolidate now" is triggered).

Wiring
------
Started from the FastAPI startup lifespan in ``backend/app/main.py`` when
``settings.consolidation_scheduler_enabled`` is true::

    worker = REMSleepWorker(graph_service._driver)
    asyncio.create_task(worker.run())

The same class backs the manual per-agent trigger
(``POST /api/v1/agents/{id}/consolidate-now``) via ``consolidate_agent_now``.
"""
from __future__ import annotations

import asyncio
import logging

import numpy as np

from app.config import settings
from app.lib.consolidation import _propose_relations
from app.services.sleep_service import SleepBusy, SleepOrchestrator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-agent passes (agent-scoped; the consolidation.py variants that carry an
# agent_id are reused directly, and the two below add the scoping the global
# passes lack).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# REMSleepWorker
# ---------------------------------------------------------------------------


class REMSleepWorker:
    """Frequency-driven, per-agent consolidation loop.

    Lifecycle::

        worker = REMSleepWorker(driver)
        task = asyncio.create_task(worker.run())   # fire-and-forget
        ...
        worker.stop()                              # graceful shutdown
        await task
    """

    def __init__(self, driver) -> None:
        self._driver = driver
        self._running: bool = False

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the hibernation loop until ``stop()`` is called.

        The outer try/except guarantees a failed sweep (a bad agent, an
        unreachable Neo4j) never crashes the loop.
        """
        if not settings.consolidation_scheduler_enabled:
            logger.info(
                "REMSleepWorker: consolidation_scheduler_enabled is false — "
                "hibernation scheduler is a no-op."
            )
            return

        self._running = True
        interval = settings.consolidation_scheduler_interval_s
        logger.info(
            "REMSleepWorker: hibernation scheduler started | tick=%ds max_concurrent=%d",
            interval, settings.consolidation_max_concurrent_agents,
        )
        while self._running:
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.exception("REMSleepWorker: tick failed — continuing | error=%s", exc)
            await asyncio.sleep(interval)

        logger.info("REMSleepWorker: stopped")

    def stop(self) -> None:
        """Request graceful shutdown. The current tick completes first."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal: one sweep
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        """Find agents whose cadence has elapsed and consolidate each one."""
        due = await self._find_due_agents()
        if not due:
            logger.debug("REMSleepWorker: no agents due this tick")
            return

        logger.info("REMSleepWorker: %d agent(s) due for consolidation: %s", len(due), due)

        sem = asyncio.Semaphore(settings.consolidation_max_concurrent_agents)

        async def _run_with_sem(agent_id: str) -> None:
            async with sem:
                await self._consolidate_agent(agent_id)

        await asyncio.gather(*[_run_with_sem(aid) for aid in due], return_exceptions=True)

    async def _find_due_agents(self) -> list[str]:
        """Agents with a cadence set whose interval has elapsed since last run.

        ``last_consolidated_at`` NULL (never consolidated) always qualifies once
        a cadence is set, so opting in triggers a first pass on the next tick.
        """
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (a:SystemAgent)
                WHERE a.agent_id IS NOT NULL
                  AND coalesce(a.consolidation_interval_hours, 0) > 0
                WITH a.agent_id AS agent_id,
                     coalesce(a.consolidation_interval_hours, 0) AS interval_h,
                     a.last_consolidated_at AS last_at
                WHERE last_at IS NULL
                   OR datetime(last_at) < datetime() - duration({hours: interval_h})
                RETURN agent_id
                """
            )
            return [r["agent_id"] async for r in result]

    async def _consolidate_agent(self, agent_id: str) -> dict:
        """Run the full sleep cycle for one KB. Another worker holding it is not an error."""
        from app.services.graph_service import get_graph_service

        logger.info("REMSleepWorker: consolidating agent=%s", agent_id)
        try:
            report = await SleepOrchestrator(get_graph_service()).run(agent_id)
        except SleepBusy:
            logger.info("REMSleepWorker: agent=%s is already sleeping elsewhere", agent_id)
            return {"status": "busy"}

        proposed = 0
        if settings.consolidation_propose_edges:
            try:
                proposed = await _propose_relations(self._driver, agent_id, np)
            except Exception as exc:  # noqa: BLE001
                logger.exception("REMSleepWorker: alchemist failed | agent=%s err=%s", agent_id, exc)

        passes = report["passes"]
        return {
            "pruned": passes.get("prune", {}).get("pruned", 0),
            "fused": passes.get("fusion", {}).get("merged", 0),
            "decayed": passes.get("decay", {}).get("edges", 0),
            "proposed": proposed,
            "report": report,
        }

    async def consolidate_agent_now(self, agent_id: str) -> dict:
        """Run a consolidation pass for one agent immediately (manual trigger)."""
        return await self._consolidate_agent(agent_id)
