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
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.lib.consolidation import (
    _ORPHAN_MIN_AGE_DAYS,
    _fuse_agent_duplicates,
    _propose_relations,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-agent passes (agent-scoped; the consolidation.py variants that carry an
# agent_id are reused directly, and the two below add the scoping the global
# passes lack).
# ---------------------------------------------------------------------------


async def _prune_agent_orphans(driver, agent_id: str) -> int:
    """Delete isolated SpaiderNodes for one agent older than the threshold.

    Returns the number of nodes deleted. Fully agent-scoped, so it is safe to
    run concurrently with other agents.
    """
    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=_ORPHAN_MIN_AGE_DAYS))
        .timestamp() * 1000
    )
    async with driver.session() as session:
        count_result = await session.run(
            """
            MATCH (n:SpaiderNode {agent_id: $agent_id})
            WHERE NOT (n)--()
              AND n.created_at IS NOT NULL
              AND n.created_at < $cutoff_ms
              AND NOT n:SystemAgent
            RETURN count(n) AS total
            """,
            agent_id=agent_id,
            cutoff_ms=cutoff_ms,
        )
        record = await count_result.single()
        total = int(record["total"]) if record else 0

        if total > 0:
            await session.run(
                """
                MATCH (n:SpaiderNode {agent_id: $agent_id})
                WHERE NOT (n)--()
                  AND n.created_at IS NOT NULL
                  AND n.created_at < $cutoff_ms
                  AND NOT n:SystemAgent
                DETACH DELETE n
                """,
                agent_id=agent_id,
                cutoff_ms=cutoff_ms,
            )
    return total


async def _decay_agent_edges(driver, agent_id: str) -> int:
    """Multiplicatively decay this agent's RELATION utility_weights (floored 0.1).

    The disuse counterweight to implicit reinforcement, scoped to one agent so
    it runs inside a per-agent sleep pass. No-op when the rate is >= 1.0.
    """
    rate = settings.edge_decay_rate
    if rate >= 1.0:
        return 0
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (:SpaiderNode {agent_id: $agent_id})-[r:RELATION]->(:SpaiderNode {agent_id: $agent_id})
            WITH r, coalesce(r.utility_weight, 1.0) AS w
            WITH r, w, CASE WHEN w * $rate < 0.1 THEN 0.1 ELSE w * $rate END AS new_w
            WHERE new_w <> w
            SET r.utility_weight = new_w
            RETURN count(r) AS decayed
            """,
            agent_id=agent_id,
            rate=rate,
        )
        record = await result.single()
        return int(record["decayed"]) if record else 0


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
        """Run prune → fuse → decay → (optional) propose for one agent, then
        stamp ``last_consolidated_at``.

        A failure in any sub-pass is logged and swallowed so the remaining
        passes still run. Returns a small report dict (used by the manual
        trigger endpoint).
        """
        logger.info("REMSleepWorker: consolidating agent=%s", agent_id)
        pruned = fused = decayed = proposed = 0

        try:
            pruned = await _prune_agent_orphans(self._driver, agent_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("REMSleepWorker: orphan prune failed | agent=%s err=%s", agent_id, exc)

        try:
            import numpy as np
            fused = await _fuse_agent_duplicates(self._driver, agent_id, np)
        except ImportError:
            logger.warning("REMSleepWorker: numpy unavailable — skipping fuse | agent=%s", agent_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("REMSleepWorker: duplicate fuse failed | agent=%s err=%s", agent_id, exc)

        try:
            decayed = await _decay_agent_edges(self._driver, agent_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("REMSleepWorker: edge decay failed | agent=%s err=%s", agent_id, exc)

        try:
            if settings.consolidation_propose_edges:
                import numpy as np
                proposed = await _propose_relations(self._driver, agent_id, np)
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.exception("REMSleepWorker: alchemist failed | agent=%s err=%s", agent_id, exc)

        # Stamp completion so the cadence clock restarts from now.
        try:
            async with self._driver.session() as session:
                await session.run(
                    "MATCH (a:SystemAgent {agent_id: $aid}) SET a.last_consolidated_at = datetime()",
                    aid=agent_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("REMSleepWorker: could not stamp last_consolidated_at | agent=%s err=%s", agent_id, exc)

        report = {"pruned": pruned, "fused": fused, "decayed": decayed, "proposed": proposed}
        logger.info("REMSleepWorker: agent=%s done | %s", agent_id, report)
        return report

    async def consolidate_agent_now(self, agent_id: str) -> dict:
        """Run a consolidation pass for one agent immediately (manual trigger)."""
        return await self._consolidate_agent(agent_id)
