"""
SpAIder Graph Maintenance DAG.

Performs housekeeping on all agent knowledge graphs: removes orphan
nodes, merges near-duplicate nodes via embedding cosine similarity,
and logs per-agent statistics.

The actual logic lives in ``backend/app/lib/consolidation.py`` — the
canonical home shared with the ``python -m app.scripts.run_consolidation``
CLI. Bind-mount that directory into the Airflow container at
``/opt/airflow/spaider_lib`` (see ``docker-compose.airflow.yml`` overlay
in the repo root). The DAG adds the parent directory to ``sys.path`` at
task-execution time so ``from spaider_lib.consolidation import …``
resolves cleanly without polluting Airflow's DAG discovery.

Schedule
--------
``MAINTENANCE_DAG_SCHEDULE`` (default ``"0 3 * * 0"`` — Sunday 03:00 UTC).

Tunables (read by the shared lib)
---------------------------------
``ORPHAN_MIN_AGE_DAYS``        default ``7``
``MERGE_SIMILARITY_THRESHOLD`` default ``0.95``

History
-------
Replaces the previous in-DAG copy that (a) duplicated logic now in the
shared lib, and (b) silently no-op'd because it queried the wrong node
labels (``:Agent`` / ``:Node`` instead of ``:SystemAgent`` / ``:SpaiderNode``).
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any

from airflow import DAG
from airflow.operators.python import PythonOperator

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

default_args = {
    "owner": "spaider",
    "depends_on_past": False,
    "start_date": datetime(2024, 1, 1),
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "email_on_failure": False,
    "email_on_retry": False,
}

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "spaider-dev-2024")

# Cron schedule (read at DAG parse time). Examples:
#   "0 3 * * 0"   — 03:00 UTC every Sunday   (default)
#   "0 3 * * *"   — 03:00 UTC daily
#   "0 * * * *"   — top of every hour (heavy-ingest deployments)
MAINTENANCE_DAG_SCHEDULE = os.environ.get("MAINTENANCE_DAG_SCHEDULE", "0 3 * * 0")

# Where the shared lib (``backend/app/lib/``) is mounted in the Airflow
# container. Override via env when the deploy uses a different mount path.
SPAIDER_LIB_PARENT = os.environ.get("SPAIDER_LIB_PARENT", "/opt/airflow")


# ---------------------------------------------------------------------------
# Task — runs all three passes via the shared lib
# ---------------------------------------------------------------------------


def run_full_consolidation(**context: Any) -> dict:
    """
    Single Airflow task that delegates to the shared
    ``run_consolidation(driver)`` coroutine.

    Three previous Operators (cleanup_orphans, merge_duplicates,
    compute_stats) are collapsed into one because the shared lib runs
    them as a single transactional cycle and returns one
    ``ConsolidationReport``. Per-pass progress is still visible in the
    Airflow task log via the lib's structured ``logger.info`` calls.
    """
    import asyncio

    if SPAIDER_LIB_PARENT not in sys.path:
        sys.path.insert(0, SPAIDER_LIB_PARENT)

    try:
        from spaider_lib.consolidation import run_consolidation
    except ImportError as exc:
        raise RuntimeError(
            f"Could not import spaider_lib.consolidation from {SPAIDER_LIB_PARENT}. "
            "Bind-mount backend/app/lib/ into the Airflow container at "
            f"{SPAIDER_LIB_PARENT}/spaider_lib (see docker-compose.airflow.yml)."
        ) from exc

    from neo4j import AsyncGraphDatabase

    async def _run() -> Any:
        driver = AsyncGraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
        try:
            return await run_consolidation(driver)
        finally:
            await driver.close()

    report = asyncio.run(_run())

    if report.error is not None:
        raise RuntimeError(f"Consolidation failed: {report.error}")

    summary = {
        "deleted_orphans": report.deleted_orphans,
        "merged_duplicates": report.merged_duplicates,
        "agents_scanned": report.agents_scanned,
        "duration_s": round(report.duration_s, 2),
    }
    log.info(
        "Maintenance complete in %.1fs — orphans_removed=%d  "
        "duplicates_merged=%d  agents_scanned=%d",
        report.duration_s,
        report.deleted_orphans,
        report.merged_duplicates,
        report.agents_scanned,
    )
    context["ti"].xcom_push(key="consolidation_summary", value=summary)
    return summary


# ---------------------------------------------------------------------------
# DAG
# ---------------------------------------------------------------------------


with DAG(
    dag_id="spaider_graph_maintenance",
    default_args=default_args,
    description="Graph housekeeping: orphan removal, duplicate merging, stats",
    schedule_interval=MAINTENANCE_DAG_SCHEDULE,
    catchup=False,
    tags=["spaider", "maintenance"],
) as dag:
    PythonOperator(
        task_id="run_consolidation",
        python_callable=run_full_consolidation,
    )
