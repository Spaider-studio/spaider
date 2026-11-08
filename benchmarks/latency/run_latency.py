"""
Latency of the no-LLM memory paths at a chosen graph size.

Runs in-process against a throwaway Neo4j (default bolt://localhost:17687) and
refuses the real database port. No model is called on these paths, so numbers
reflect Neo4j and the API only. Query and ingest latency with a real provider is
not measured here.

    SPAIDER_TEST_NEO4J_PASSWORD=... PYTHONPATH=backend \\
        backend/.venv/bin/python benchmarks/latency/run_latency.py --nodes 100000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path

os.environ.setdefault("SPAIDER_TEST_NEO4J_URI", "bolt://localhost:17687")
os.environ.setdefault("SPAIDER_TEST_REDIS_URL", "")

from tests import _safety  # noqa: E402  isort: skip

_safety.install_test_environment()
_safety.block_real_services()

_BATCH = 5000


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]


def summarise(samples: list[float]) -> dict:
    return {
        "count": len(samples),
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(percentile(samples, 0.95), 2),
        "p99_ms": round(percentile(samples, 0.99), 2),
    }


async def seed(driver, kb_id: str, nodes: int) -> None:
    async with driver.session() as session:
        for start in range(0, nodes, _BATCH):
            await session.run(
                """
                UNWIND range($start, $end) AS i
                CREATE (:SpaiderNode {
                    id: 'n' + i, agent_id: $kb, type: 'CONCEPT', label: 'entity ' + i,
                    description: 'seeded node number ' + i + ' topic ' + (i % 97), created_at: $now
                })
                """,
                start=start,
                end=min(start + _BATCH, nodes) - 1,
                kb=kb_id,
                now="2026-01-01T00:00:00+00:00",
            )
        await session.run(
            """
            UNWIND range(0, $count) AS i
            CREATE (:Episode {
                id: $kb + '-' + i, agent_id: $kb, idempotency_key: 'seed-' + i, kind: 'observation', status: 'new',
                content: 'seeded episode ' + i, occurred_at: '2026-01-01T00:00:00+00:00', metadata: '{}',
                recorded_at: '2026-01-01T00:00:00+00:00', clearance_level: 1,
                location: point({x: toFloat(i % 1000), y: toFloat(i % 700)})
            })
            """,
            count=min(nodes, 20000),
            kb=kb_id,
        )
        await session.run("CALL db.awaitIndexes(600)")


async def timed(call, requests: int, concurrency: int) -> list[float]:
    gate = asyncio.Semaphore(concurrency)
    samples: list[float] = []

    async def one(index: int) -> None:
        async with gate:
            started = time.perf_counter()
            response = await call(index)
            samples.append((time.perf_counter() - started) * 1000)
            if response.status_code >= 400:
                raise RuntimeError(f"{response.status_code}: {response.text[:200]}")

    await asyncio.gather(*[one(index) for index in range(requests)])
    return samples


async def run(nodes: int, requests: int, output: Path | None) -> dict:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api.router import api_router
    from app.services import graph_service as graph_module
    from app.services.identity_service import get_identity_service
    graph = graph_module.get_graph_service()
    await _safety.assert_test_database(graph._driver)
    await _safety.wipe_test_database(graph._driver)
    await graph.initialize()
    owner = await get_identity_service().bootstrap("Bench", "Owner")

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    timeout = 120
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://bench", timeout=timeout) as client:
        headers = {"X-Api-Key": owner["api_key"]}
        created = await client.post("/api/v1/agents", json={"name": "bench", "clearance_level": 5}, headers=headers)
        created.raise_for_status()
        kb_id = created.json()["agent"]["id"]
        other = await client.post("/api/v1/agents", json={"name": "peer", "clearance_level": 5}, headers=headers)
        other_id = other.json()["agent"]["id"]
        await seed(graph._driver, kb_id, nodes)
        await seed(graph._driver, other_id, min(nodes, 5000))
        grant = await client.post(
            "/api/v1/federation/grants",
            json={"source_kb": other_id, "target_kb": kb_id, "max_clearance": 5},
            headers=headers,
        )
        await client.post(f"/api/v1/federation/grants/{grant.json()['id']}/approve", json={}, headers=headers)

        def recall(**extra):
            async def call(index: int):
                body = {"agent_id": kb_id, "query": f"topic {index % 97} seeded", "limit": 10, **extra}
                return await client.post("/api/v1/recall", json=body, headers=headers)

            return call

        async def write(index: int):
            episode = {"content": f"observation {index}", "idempotency_key": f"w-{index}-{time.time_ns()}"}
            body = {"agent_id": kb_id, "episodes": [episode]}
            return await client.post("/api/v1/episodes", json=body, headers=headers)

        async def near(index: int):
            centre = {"x": float(index % 900), "y": float(index % 600)}
            body = {"agent_id": kb_id, "near": centre, "radius": 25.0, "limit": 10}
            return await client.post("/api/v1/recall", json=body, headers=headers)

        scenarios = {
            "episode_write": write,
            "recall_text": recall(),
            "recall_filtered": recall(kinds=["observation"], session_id=None),
            "recall_near": near,
            "recall_federated": recall(federated=True),
        }
        report: dict = {
            "nodes": nodes,
            "note": "No LLM or embedding call on these paths. Measures Neo4j and API latency only.",
            "cpu_count": os.cpu_count(),
            "results": {},
        }
        for concurrency in (1, 8):
            for name, call in scenarios.items():
                samples = await timed(call, requests, concurrency)
                report["results"][f"{name}@{concurrency}"] = summarise(samples)
                print(f"{name:18s} concurrency={concurrency} {report['results'][f'{name}@{concurrency}']}", flush=True)
    await graph.close()
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=10_000)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    from app.config import settings

    settings.spaider_insecure_dev = False
    asyncio.run(run(args.nodes, args.requests, args.output))
