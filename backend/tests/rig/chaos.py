"""Breaks the rig's services on purpose and measures how it recovers.

Two kinds of scenario:

* ``stop``: stop one service, watch what /health says, start it again and time
  how long until everything is healthy and works.
* ``daemon``: restart the whole Docker daemon of the rig. Docker then starts
  every container at once and ignores startup order, which is what happens
  after a reboot or a Docker Desktop restart. Repeated, because it is a race.

"Works" means more than /health: an ingest, a cited query, the audit list, the
analytics route, the graph statistics and the Studio's pages must all answer.
A service that is up but was never reconnected shows here and nowhere else.

    backend/.venv/bin/python backend/tests/rig/chaos.py stop
    backend/.venv/bin/python backend/tests/rig/chaos.py daemon --rounds 5

The rig keeps the shipped rate limit of 60 a minute, which these checks would
hit; raise it first:  rig.py env RATE_LIMIT_REQUESTS_PER_MINUTE=3000
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
KEYS = HERE / ".rig-keys.json"
API = "http://localhost:18000"
STUDIO = "http://localhost:13000"

# (container, what to expect /health to say while it is down)
SERVICES = [
    ("spaider-kafka", "kafka"),
    ("spaider-zookeeper", None),
    ("spaider-redis", "redis"),
    ("spaider-clickhouse", "clickhouse"),
    ("spaider-postgres", "postgres"),
    ("spaider-neo4j", "neo4j"),
    ("s-backend-worker-1", None),
    ("spaider-kong", None),
    ("spaider-backend-api", None),
    ("spaider-frontend", None),
]


def inner(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", "spaider-rig", "docker", *args], capture_output=True, text=True, check=False
    )


def health() -> dict | None:
    try:
        with urllib.request.urlopen(f"{API}/health", timeout=5) as response:
            return json.load(response)
    except Exception:  # noqa: BLE001
        return None


def all_good(report: dict | None) -> bool:
    if report is None:
        return False
    states = report.get("services", {}).values()
    return report.get("status") == "ok" and all(state in ("ok", "disabled") for state in states)


def wait_good(timeout: float) -> float | None:
    start = time.time()
    while time.time() - start < timeout:
        if all_good(health()):
            return time.time() - start
        time.sleep(3)
    return None


def functional() -> list[str]:
    """What still does not work, as short messages. Empty means everything works."""
    keys = json.loads(KEYS.read_text())
    key, kb = keys["keys"]["owner"], keys["kbs"]["rig-alpha"]
    headers = {"X-Api-Key": key}
    base = f"{API}/api/v1"
    problems: list[str] = []
    stamp = int(time.time())
    with httpx.Client(timeout=90) as client:

        def check(name: str, method: str, path: str, **kwargs):
            try:
                response = client.request(method, base + path, headers=headers, **kwargs)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{name}: {type(exc).__name__}")
                return None
            if response.status_code >= 400:
                problems.append(f"{name}: {response.status_code}")
                return None
            return response.json()

        fact = f"Chaos fact {stamp}: Unit U{stamp} is in bay 7."
        check("ingest", "POST", "/ingest/sync", json={"text": fact, "agent_id": kb})
        answer = check("query", "POST", "/query", json={"question": f"Where is unit U{stamp}?", "agent_id": kb})
        if answer is not None and not answer.get("answer"):
            problems.append("query: empty answer")
        runs = check("audit list", "GET", "/replay/workflows", params={"agent_id": kb})
        if runs is not None and runs.get("complete") is False:
            problems.append("audit list: only the Kafka fallback answered")
        check("analytics", "GET", "/analytics/overview", params={"agent_id": kb})
        check("graph stats", "GET", "/graph/stats", params={"agent_id": kb})
        check("vector search", "POST", "/recall", json={"query": "bay 7", "agent_id": kb})
        try:
            if client.get(f"{STUDIO}/audit").status_code != 200:
                problems.append("studio: /audit not 200")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"studio: {type(exc).__name__}")
    return problems


def stop_scenarios(only: list[str] | None) -> list[dict]:
    rows = []
    for container, expected_key in SERVICES:
        if only and container not in only:
            continue
        wait_good(120)
        inner("stop", container)
        time.sleep(18)
        while_down = health()
        seen = None
        if while_down is not None:
            seen = while_down["services"].get(expected_key) if expected_key else while_down.get("status")
        started = time.time()
        inner("start", container)
        recovered = wait_good(240)
        after = functional() if recovered is not None else ["never became healthy"]
        rows.append(
            {
                "scenario": f"stop {container}",
                "health while down": "no answer" if while_down is None else f"{expected_key or 'status'}={seen}",
                "recovery s": None if recovered is None else round(time.time() - started),
                "still broken": after,
            }
        )
        print(json.dumps(rows[-1]), flush=True)
    return rows


def daemon_scenarios(rounds: int) -> list[dict]:
    rows = []
    for number in range(1, rounds + 1):
        wait_good(120)
        started = time.time()
        subprocess.run(["docker", "restart", "spaider-rig"], capture_output=True, check=False)
        recovered = wait_good(300)
        after = functional() if recovered is not None else ["never became healthy"]
        report = health()
        restarts = {}
        for container, _ in SERVICES:
            result = inner("inspect", container, "--format", "{{.RestartCount}}")
            restarts[container] = result.stdout.strip()
        rows.append(
            {
                "scenario": f"daemon restart {number}",
                "recovery s": None if recovered is None else round(time.time() - started),
                "health": None if report is None else {k: v for k, v in report["services"].items() if v != "ok"},
                "still broken": after,
                "restart counts": {k: v for k, v in restarts.items() if v not in ("", "0")},
            }
        )
        print(json.dumps(rows[-1]), flush=True)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", choices=["stop", "daemon"])
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--only", nargs="*", help="container names, for the stop scenario")
    args = parser.parse_args()
    rows = stop_scenarios(args.only) if args.scenario == "stop" else daemon_scenarios(args.rounds)
    bad = [row for row in rows if row["still broken"] or row["recovery s"] is None]
    print(f"\n{len(rows)} scenarios, {len(bad)} left something broken")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
