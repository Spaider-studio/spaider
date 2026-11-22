"""Sends parallel ingests and queries to the rig and checks the audit store kept up.

Every ingest and query should leave one analytics row and one audit workflow.
This counts what ClickHouse actually holds before and after, so a silent
insert failure shows up as a number instead of staying a debug log line.

    backend/.venv/bin/python backend/tests/rig/load.py [--n 40]

The rig ships a limit of 60 requests a minute per key. For a load test raise it
first:  python backend/tests/rig/rig.py env RATE_LIMIT_REQUESTS_PER_MINUTE=2000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
KEYS = HERE / ".rig-keys.json"
API = "http://localhost:18000/api/v1"
TABLES = {
    "ingest_events": "SELECT count() FROM spaider.ingest_events",
    "query_events": "SELECT count() FROM spaider.query_events",
    "workflows": "SELECT uniq(workflow_id) FROM spaider.workflow_events",
    "workflow_events": "SELECT count() FROM spaider.workflow_events",
}


def counts() -> dict[str, int]:
    out = {}
    for name, sql in TABLES.items():
        # The password is read from the rig's own .env, in the rig's shell.
        command = (
            "docker exec spaider-clickhouse clickhouse-client "
            f"--password \"$(grep ^CLICKHOUSE_PASSWORD= /root/s/.env | cut -d= -f2)\" -q '{sql}'"
        )
        result = subprocess.run(
            ["docker", "exec", "spaider-rig", "sh", "-c", command], capture_output=True, text=True, check=False
        )
        out[name] = int(result.stdout.strip() or -1)
    return out


async def main(n: int) -> int:
    keys = json.loads(KEYS.read_text())
    key, kb = keys["keys"]["owner"], keys["kbs"]["rig-alpha"]
    headers = {"X-Api-Key": key}
    before = counts()
    stamp = int(time.time())
    async with httpx.AsyncClient(timeout=300, limits=httpx.Limits(max_connections=n * 2)) as client:
        async def ingest(i: int):
            text = f"Load test fact {stamp}-{i}: Robot R{i} was assigned to bay {i} in run {stamp}."
            body = {"text": text, "agent_id": kb}
            return await client.post(f"{API}/ingest/sync", headers=headers, json=body)

        async def query(i: int):
            body = {"question": f"Which bay is Robot R{i} assigned to?", "agent_id": kb}
            return await client.post(f"{API}/query", headers=headers, json=body)

        started = time.time()
        ingests = await asyncio.gather(*[ingest(i) for i in range(n)])
        queries = await asyncio.gather(*[query(i) for i in range(n)])
        elapsed = time.time() - started
    bad = [r.status_code for r in ingests + queries if r.status_code >= 400]
    await asyncio.sleep(8)  # inserts are scheduled in the background
    after = counts()
    delta = {name: after[name] - before[name] for name in before}
    print(f"{n} ingests and {n} queries in {elapsed:.1f}s, failed requests: {len(bad)} {sorted(set(bad))}")
    expected = {"ingest_events": n, "query_events": n, "workflows": 2 * n}
    for name, want in expected.items():
        status = "ok" if delta[name] == want else f"LOST {want - delta[name]}"
        print(f"  {name:16s} +{delta[name]:4d} (expected {want})  {status}")
    print(f"  {'workflow_events':16s} +{delta['workflow_events']:4d}")
    return 0 if all(delta[k] == v for k, v in expected.items()) and not bad else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=40)
    sys.exit(asyncio.run(main(parser.parse_args().n)))
