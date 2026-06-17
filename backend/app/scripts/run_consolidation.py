"""
One-shot graph consolidation CLI — for deployments without Airflow.

Runs the same three passes as the ``graph_maintenance`` Airflow DAG (orphan
prune → cosine-similarity duplicate fusion → stats logging), reading the
same env-var tunables (``ORPHAN_MIN_AGE_DAYS``, ``MERGE_SIMILARITY_THRESHOLD``).

Wire this into cron / k8s ``CronJob`` / systemd timer / your scheduler of
choice. Exits 0 on success, 1 on consolidation error.

Usage
-----
    # Inside the backend container:
    python -m app.scripts.run_consolidation

    # k8s CronJob skeleton:
    apiVersion: batch/v1
    kind: CronJob
    metadata:
      name: spaider-consolidation
    spec:
      schedule: "0 3 * * *"
      jobTemplate:
        spec:
          template:
            spec:
              containers:
              - name: consolidation
                image: spaider-backend:latest
                command: ["python", "-m", "app.scripts.run_consolidation"]
                envFrom:
                  - configMapRef:
                      name: spaider-env
"""
from __future__ import annotations

import asyncio
import logging
import sys

from app.lib.consolidation import run_consolidation
from app.services.graph_service import GraphService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _main() -> int:
    graph = GraphService()
    await graph.initialize()
    try:
        report = await run_consolidation(graph._driver)
    finally:
        # Best-effort close — graph_service doesn't expose an explicit close
        # but the driver does.
        try:
            await graph._driver.close()
        except Exception:  # noqa: BLE001
            pass

    if report.error is not None:
        logger.error("Consolidation finished with error: %s", report.error)
        return 1

    logger.info(
        "Consolidation complete in %.1fs — orphans_removed=%d  "
        "duplicates_merged=%d  agents_scanned=%d",
        report.duration_s,
        report.deleted_orphans,
        report.merged_duplicates,
        report.agents_scanned,
    )
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
