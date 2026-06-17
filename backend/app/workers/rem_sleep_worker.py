"""
REM-Sleep Worker — per-agent idle-triggered consolidation.

Why this exists
---------------
The default consolidation surface is the weekly Airflow DAG
(``airflow/dags/graph_maintenance_dag.py``, cron ``"0 3 * * 0"``).
That cadence wastes ~6.86 days out of every 7 — a personal agent
that ingests heavily on Monday morning does not benefit from
duplicate fusion or orphan pruning until the following Sunday at
03:00 UTC, and all agents share the same window so heavy agents
starve light ones.

This worker fixes both: per-agent scheduling, idle-triggered
(not cron-triggered). When an agent has been quiet for
``_IDLE_THRESHOLD_HOURS`` it gets a sleep pass that prunes its
orphans, fuses near-duplicates, and (optionally) runs the
alchemist inverse pass.

Status: V1 implementation. Gated off by default behind
``REM_SLEEP_WORKER_ENABLED`` — see "Wiring" below. Designed to
be invoked from the FastAPI startup lifespan or as a standalone
process; this file defines the worker class only.

Wiring (out of scope for this commit)
-------------------------------------
To enable, add to ``backend/app/main.py`` lifespan (or run as a
sidecar process)::

    if os.environ.get("REM_SLEEP_WORKER_ENABLED", "false").lower() == "true":
        worker = REMSleepWorker(graph_service._driver)
        asyncio.create_task(worker.run())

Idle detection
--------------
Today there is no ``last_query_at`` field per agent. As a proxy,
this worker reads ``max(n.last_activation)`` across each agent's
SpaiderNodes — that field is refreshed on every retrieval by
``CognitiveGraphService.boost_nodes`` (see
``backend/app/services/cognitive_engine.py``). An agent whose
nodes have all been quiet for ``_IDLE_THRESHOLD_HOURS`` is
treated as sleeping.

Caveats:
  • Agents that ingested heavily but never queried have stale
    ``last_activation`` values — they'll trigger sleep early.
    Acceptable for V1; a dedicated ``last_query_at`` Redis key
    is in scope for Phase A.2 of the architecture proposal.
  • An agent with zero nodes has ``last_activation = NULL`` and
    is treated as "permanently sleeping" — the cypher below
    excludes those via NOT NULL guard.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from app.lib.consolidation import _fuse_agent_duplicates, _propose_relations

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables (env-overridable; same pattern as ``consolidation.py:52-53``)
# ---------------------------------------------------------------------------

# Feature flag. Default off — the worker is a no-op unless explicitly
# enabled. See module docstring "Wiring" section for activation steps.
_REM_SLEEP_WORKER_ENABLED = (
    os.environ.get("REM_SLEEP_WORKER_ENABLED", "false").lower() == "true"
)

# How often the worker wakes to scan for idle agents (seconds).
_LOOP_INTERVAL_SECONDS = int(os.environ.get("REM_SLEEP_LOOP_INTERVAL_S", "1800"))

# An agent is "sleeping" when its most-recent ``n.last_activation`` is
# older than this. Default: 4 hours.
_IDLE_THRESHOLD_HOURS = int(os.environ.get("REM_SLEEP_IDLE_HOURS", "4"))

# Minimum age (days) for an orphan node before it qualifies for pruning
# inside a sleep pass. Mirrors ``ORPHAN_MIN_AGE_DAYS`` in
# ``consolidation.py:52`` but is intentionally separate so operators can
# tune sleep-time pruning more aggressively than the weekly DAG without
# affecting the DAG's defaults.
_SLEEP_ORPHAN_MIN_AGE_DAYS = int(
    os.environ.get("REM_SLEEP_ORPHAN_MIN_AGE_DAYS", "7")
)

# Cap on concurrent agent sleep passes. Prevents a "thundering herd" if
# 100 agents go idle simultaneously (e.g., after a Friday-evening lull).
_MAX_CONCURRENT_AGENTS = int(os.environ.get("REM_SLEEP_MAX_CONCURRENCY", "4"))


# ---------------------------------------------------------------------------
# Per-agent orphan-prune (inline; ``consolidation.py:_prune_orphans`` is
# global-scoped, no agent_id parameter). Mirrors that function's Cypher
# shape exactly, with the agent_id WHERE clause added.
# ---------------------------------------------------------------------------


async def _prune_agent_orphans(driver, agent_id: str) -> int:
    """Delete isolated SpaiderNodes for one agent older than the threshold.

    Returns the number of nodes deleted. Safe to call concurrently with
    other agents — every Cypher pattern is fully agent-scoped.
    """
    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=_SLEEP_ORPHAN_MIN_AGE_DAYS))
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


# ---------------------------------------------------------------------------
# REMSleepWorker
# ---------------------------------------------------------------------------


class REMSleepWorker:
    """Idle-triggered, per-agent consolidation loop.

    Lifecycle::

        worker = REMSleepWorker(driver)
        task = asyncio.create_task(worker.run())   # fire-and-forget
        ...
        worker.stop()                              # graceful shutdown
        await task                                 # await loop exit

    The ``run()`` coroutine is the long-lived loop. It iterates forever
    (until ``stop()`` is called) and uses ``asyncio.sleep`` between
    sweeps; it never blocks the event loop.
    """

    def __init__(self, driver) -> None:
        self._driver = driver
        self._running: bool = False
        # Tracks the last sleep-event timestamp per agent so we don't
        # re-consolidate the same idle agent every loop tick. An agent
        # gets at most one sleep pass per
        # ``2 * _IDLE_THRESHOLD_HOURS`` window.
        self._last_sleep_at: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the consolidation loop until ``stop()`` is called.

        Outermost try/except guarantees that a failure in any sweep
        (one bad agent, an unreachable Neo4j, anything) cannot crash
        the loop. The worker will log the exception and continue
        sleeping until the next tick.
        """
        if not _REM_SLEEP_WORKER_ENABLED:
            logger.info(
                "REMSleepWorker: REM_SLEEP_WORKER_ENABLED is false — "
                "worker is a no-op. Set the env var to 'true' to enable."
            )
            return

        self._running = True
        logger.info(
            "REMSleepWorker: starting | interval=%ds idle_hours=%d "
            "orphan_age_days=%d max_concurrent=%d",
            _LOOP_INTERVAL_SECONDS,
            _IDLE_THRESHOLD_HOURS,
            _SLEEP_ORPHAN_MIN_AGE_DAYS,
            _MAX_CONCURRENT_AGENTS,
        )

        while self._running:
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                # A failure during the tick (e.g. Neo4j outage) must NOT
                # crash the loop. Log with stack trace and continue.
                logger.exception(
                    "REMSleepWorker: tick failed — continuing | error=%s", exc,
                )
            await asyncio.sleep(_LOOP_INTERVAL_SECONDS)

        logger.info("REMSleepWorker: stopped")

    def stop(self) -> None:
        """Request graceful shutdown. The current tick completes first."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal: one sweep
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        """Find idle agents and consolidate each one, bounded by a semaphore."""
        idle_agent_ids = await self._find_idle_agents()
        if not idle_agent_ids:
            logger.debug("REMSleepWorker: no idle agents this tick")
            return

        logger.info(
            "REMSleepWorker: %d idle agent(s) qualify for consolidation: %s",
            len(idle_agent_ids), idle_agent_ids,
        )

        # Bound concurrency so a burst of idle agents doesn't saturate the
        # Neo4j connection pool. Each ``_consolidate_agent`` call holds a
        # session for the duration of its 3 sub-passes.
        sem = asyncio.Semaphore(_MAX_CONCURRENT_AGENTS)

        async def _run_with_sem(agent_id: str) -> None:
            async with sem:
                await self._consolidate_agent(agent_id)

        await asyncio.gather(
            *[_run_with_sem(aid) for aid in idle_agent_ids],
            return_exceptions=True,
        )

    async def _find_idle_agents(self) -> list[str]:
        """Identify agents whose most-recent node activation is older than
        the idle threshold AND who haven't been consolidated in the
        current window.

        Returns a list of agent_id strings.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_IDLE_THRESHOLD_HOURS)
        # In-memory dedupe window: skip agents we just consolidated.
        dedupe_cutoff = datetime.now(timezone.utc) - timedelta(
            hours=2 * _IDLE_THRESHOLD_HOURS,
        )

        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (a:SystemAgent)
                WHERE a.agent_id IS NOT NULL
                OPTIONAL MATCH (n:SpaiderNode {agent_id: a.agent_id})
                WHERE n.last_activation IS NOT NULL AND NOT n:SystemAgent
                WITH a.agent_id AS agent_id,
                     max(n.last_activation) AS most_recent
                WHERE most_recent IS NOT NULL
                  AND datetime(most_recent) < datetime($cutoff)
                RETURN agent_id
                """,
                cutoff=cutoff.isoformat(),
            )
            candidates: list[str] = [r["agent_id"] async for r in result]

        # In-memory dedupe: drop agents we already consolidated recently.
        filtered = [
            aid for aid in candidates
            if self._last_sleep_at.get(aid, datetime.min.replace(tzinfo=timezone.utc))
            < dedupe_cutoff
        ]
        return filtered

    async def _consolidate_agent(self, agent_id: str) -> None:
        """Run prune → fuse → (optional) propose for one agent.

        A failure in any sub-pass is logged and swallowed so the
        remaining passes (and remaining agents) still complete. The
        worker loop guarantees this method never raises.
        """
        logger.info("REMSleepWorker: consolidating agent=%s", agent_id)
        sleep_started = datetime.now(timezone.utc)

        pruned = fused = proposed = 0

        # ── Pass 1: orphan prune (agent-scoped) ─────────────────────────
        try:
            pruned = await _prune_agent_orphans(self._driver, agent_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "REMSleepWorker: orphan prune failed | agent=%s err=%s",
                agent_id, exc,
            )

        # ── Pass 2: duplicate fusion (agent-scoped, from consolidation.py) ─
        try:
            # numpy is imported lazily so a missing dependency degrades
            # this pass to a no-op rather than crashing the worker.
            import numpy as np
            fused = await _fuse_agent_duplicates(self._driver, agent_id, np)
        except ImportError:
            logger.warning(
                "REMSleepWorker: numpy unavailable — skipping fuse pass for "
                "agent=%s",
                agent_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "REMSleepWorker: duplicate fuse failed | agent=%s err=%s",
                agent_id, exc,
            )

        # ── Pass 3: alchemist edge proposal (optional, respects the
        # existing global ``CONSOLIDATION_PROPOSE_EDGES`` flag — we do
        # NOT introduce a separate sleep-only flag for this).
        try:
            from app.config import settings as _settings
            if _settings.consolidation_propose_edges:
                import numpy as np
                proposed = await _propose_relations(self._driver, agent_id, np)
        except ImportError:
            pass  # numpy already warned above
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "REMSleepWorker: edge proposal failed | agent=%s err=%s",
                agent_id, exc,
            )

        # ── Mark this agent as consolidated this window ─────────────────
        self._last_sleep_at[agent_id] = sleep_started
        duration = (datetime.now(timezone.utc) - sleep_started).total_seconds()
        logger.info(
            "REMSleepWorker: agent=%s done in %.1fs | "
            "orphans_pruned=%d duplicates_fused=%d edges_proposed=%d",
            agent_id, duration, pruned, fused, proposed,
        )
