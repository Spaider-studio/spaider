"""
SpAIder Synthesizer DAG

Runs daily at 02:00 UTC. For each active agent with auto_synthesis=True,
generates a fine-tuning dataset in JSONL format and stores it persistently.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from airflow import DAG
from airflow.operators.python import PythonOperator

log = logging.getLogger(__name__)

default_args = {
    "owner": "spaider",
    "depends_on_past": False,
    "start_date": datetime(2024, 1, 1),
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
    "email_on_retry": False,
}

STORAGE_BASE = os.environ.get("SYNTHESIS_STORAGE_PATH", "/tmp/spaider/datasets")
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "spaider-dev-2024")


def get_active_agents(**context: Any) -> list[dict]:
    """Query Neo4j for agents with auto_synthesis=True."""
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with driver.session() as session:
            result = session.run(
                """
                MATCH (a:Agent {auto_synthesis: true})
                RETURN a.id AS id, a.name AS name, a.synthesis_strategy AS strategy,
                       a.max_samples AS max_samples
                """
            )
            agents = [dict(record) for record in result]
        driver.close()

        log.info("Found %d active agents for synthesis", len(agents))
        context["ti"].xcom_push(key="agents", value=agents)
        return agents

    except ImportError:
        log.warning("neo4j package not available — returning empty agent list")
        context["ti"].xcom_push(key="agents", value=[])
        return []
    except Exception as exc:
        log.error("Failed to fetch active agents: %s", exc)
        raise


def run_synthesis(**context: Any) -> list[str]:
    """For each agent, call synthesizer service to generate JSONL dataset."""
    import httpx

    agents: list[dict] = context["ti"].xcom_pull(key="agents", task_ids="get_active_agents") or []
    backend_url = os.environ.get("BACKEND_URL", "http://backend-api:8000")
    output_paths: list[str] = []

    for agent in agents:
        agent_id = agent["id"]
        strategy = agent.get("strategy", "reasoning")
        max_samples = agent.get("max_samples", 1000)

        try:
            log.info("Running synthesis for agent %s (strategy=%s)", agent_id, strategy)
            response = httpx.post(
                f"{backend_url}/api/v1/synthesize",
                json={"agent_id": agent_id, "strategy": strategy, "max_samples": max_samples},
                timeout=300.0,
            )
            response.raise_for_status()
            data = response.json()

            tmp_path = f"/tmp/spaider/synthesis_{agent_id}_{context['ds_nodash']}.jsonl"
            Path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as fh:
                for sample in data.get("samples", []):
                    fh.write(json.dumps(sample, ensure_ascii=False) + "\n")

            output_paths.append(tmp_path)
            log.info("Wrote %d samples for agent %s to %s", len(data.get("samples", [])), agent_id, tmp_path)

        except Exception as exc:
            log.error("Synthesis failed for agent %s: %s", agent_id, exc)
            # Continue with remaining agents rather than failing entire task

    context["ti"].xcom_push(key="output_paths", value=output_paths)
    return output_paths


def validate_dataset(**context: Any) -> list[str]:
    """Check .jsonl validity — all lines must parse as JSON."""
    output_paths: list[str] = (
        context["ti"].xcom_pull(key="output_paths", task_ids="run_synthesis") or []
    )
    valid_paths: list[str] = []

    for path in output_paths:
        if not Path(path).exists():
            log.warning("Dataset file not found: %s", path)
            continue

        errors = 0
        total = 0
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    log.error("Invalid JSON on line %d of %s: %s", lineno, path, exc)
                    errors += 1

        if errors == 0 and total > 0:
            log.info("Validated %s: %d records, 0 errors", path, total)
            valid_paths.append(path)
        elif total == 0:
            log.warning("Empty dataset file: %s", path)
        else:
            log.error("Dataset %s has %d invalid lines — skipping", path, errors)

    context["ti"].xcom_push(key="valid_paths", value=valid_paths)
    return valid_paths


def store_dataset(**context: Any) -> list[str]:
    """Move validated datasets to persistent storage path."""
    import shutil

    valid_paths: list[str] = (
        context["ti"].xcom_pull(key="valid_paths", task_ids="validate_dataset") or []
    )
    ds = context["ds"]  # YYYY-MM-DD
    stored_paths: list[str] = []

    for tmp_path in valid_paths:
        fname = Path(tmp_path).name
        dest_dir = Path(STORAGE_BASE) / ds
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = str(dest_dir / fname)
        shutil.move(tmp_path, dest_path)
        log.info("Stored dataset: %s -> %s", tmp_path, dest_path)
        stored_paths.append(dest_path)

    context["ti"].xcom_push(key="stored_paths", value=stored_paths)
    return stored_paths


def notify(**context: Any) -> None:
    """Log completion statistics."""
    stored_paths: list[str] = (
        context["ti"].xcom_pull(key="stored_paths", task_ids="store_dataset") or []
    )
    agents: list[dict] = context["ti"].xcom_pull(key="agents", task_ids="get_active_agents") or []

    total_records = 0
    for path in stored_paths:
        if Path(path).exists():
            with open(path, encoding="utf-8") as fh:
                total_records += sum(1 for line in fh if line.strip())

    log.info(
        "Synthesis complete for %s: %d agents processed, %d datasets stored, %d total records",
        context["ds"],
        len(agents),
        len(stored_paths),
        total_records,
    )


with DAG(
    dag_id="spaider_synthesizer",
    default_args=default_args,
    description="Daily synthesis of fine-tuning datasets from SpAIder knowledge graphs",
    schedule_interval="0 2 * * *",  # 02:00 UTC daily
    catchup=False,
    tags=["spaider", "synthesis"],
) as dag:
    t1 = PythonOperator(
        task_id="get_active_agents",
        python_callable=get_active_agents,
    )

    t2 = PythonOperator(
        task_id="run_synthesis",
        python_callable=run_synthesis,
    )

    t3 = PythonOperator(
        task_id="validate_dataset",
        python_callable=validate_dataset,
    )

    t4 = PythonOperator(
        task_id="store_dataset",
        python_callable=store_dataset,
    )

    t5 = PythonOperator(
        task_id="notify",
        python_callable=notify,
    )

    t1 >> t2 >> t3 >> t4 >> t5
