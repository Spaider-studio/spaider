"""Fills a running test rig with a small, repeatable world.

Run it once after ``rig.py up``. It only ever talks to the rig's API on
``localhost:18000`` and refuses any other address, so it cannot touch the real
stack. Keys go to ``.rig-keys.json`` (mode 600) and are never printed.

    backend/.venv/bin/python backend/tests/rig/seed.py

What it creates:

* an owner and three KBs: ``rig-alpha``, ``rig-beta`` and one with a very long
  name for layout tests;
* one user per KB role in ``rig-alpha`` (reader, contributor, editor, steward,
  auditor), a steward of ``rig-beta``, and a service account whose default KB is
  ``rig-alpha`` (the kind of key that exposed the empty audit log);
* about 30 facts in ``rig-alpha`` and a few private ones in ``rig-beta``;
* queries with feedback, so the audit log and the learning page have content;
* an approved grant that lets ``rig-alpha`` read ``rig-beta``;
* one sleep cycle on ``rig-alpha``.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STATE = HERE / ".rig-state.json"
KEYS = HERE / ".rig-keys.json"
CORPUS = ROOT / "benchmarks" / "corpus" / "acmeai_30d.yaml"

ROLES = ["reader", "contributor", "editor", "steward", "auditor"]
QUESTIONS = [
    "When does the acmeai.com domain expire?",
    "What is AcmeAI's Atlas Q2 launch target date?",
    "Who decided to delay the Beacon analytics rollout?",
    "Which customer asked about data residency in the EU?",
    "What did Sara merge into main?",
]
BETA_FACTS = [
    "The Beta Vault holds the Zephyr contract. It is owned by Priya Nair.",
    "Priya Nair is the CEO of Zephyr Robotics.",
    "Zephyr Robotics ships the Atlas arm to three customers in Munich.",
]


def fail(message: str) -> None:
    sys.exit(f"seed: {message}")


class Rig:
    def __init__(self) -> None:
        if not STATE.exists():
            fail("no rig state found. Run rig.py create, sync and up first.")
        state = json.loads(STATE.read_text())
        self.api = state["api"].rstrip("/") + "/api/v1"
        if "localhost:18000" not in self.api:
            fail(f"refusing to seed {self.api}: it is not the rig")
        self.bootstrap_token = state["bootstrap_token"]
        self.http = httpx.Client(timeout=120)

    def call(self, method: str, path: str, key: str | None = None, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if key:
            headers["X-Api-Key"] = key
        # The rig keeps the shipped limit of 60 requests a minute per key, so wait
        # for the window instead of raising it.
        for _ in range(6):
            response = self.http.request(method, self.api + path, headers=headers, **kwargs)
            if response.status_code != 429:
                break
            reset_at = response.json().get("reset_at", time.time() + 30)
            time.sleep(min(max(reset_at - time.time(), 1) + 1, 65))
        if response.status_code >= 400:
            fail(f"{method} {path} -> {response.status_code}: {response.text[:200]}")
        return response.json() if response.content else {}


def main() -> None:
    if KEYS.exists():
        fail(f"{KEYS.name} exists. Recreate the rig before seeding again.")
    rig = Rig()
    health = httpx.get(rig.api.replace("/api/v1", "/health"), timeout=10).json()
    if not all(state == "ok" for state in health["services"].values()):
        fail(f"the rig is not healthy: {health['services']}")

    boot = rig.call(
        "POST", "/auth/bootstrap",
        json={"org_name": "Rig", "owner_name": "rig-owner"},
        headers={"X-Bootstrap-Token": rig.bootstrap_token},
    )
    owner = boot["api_key"]
    keys: dict[str, str] = {"owner": owner}

    kbs: dict[str, str] = {}
    for name, clearance in [
        ("rig-alpha", 5),
        ("rig-beta", 5),
        ("rig-with-a-very-long-name-that-should-wrap-or-truncate-in-every-table-and-dropdown", 3),
    ]:
        created = rig.call("POST", "/agents", owner, json={"name": name, "clearance_level": clearance})
        kbs[name] = created["agent"]["id"]
    alpha, beta = kbs["rig-alpha"], kbs["rig-beta"]

    def add_member(kb: str, role: str, label: str) -> None:
        user = rig.call("POST", "/users", owner, json={"name": f"rig-{label}", "clearance_level": 3})
        rig.call("PUT", f"/agents/{kb}/members/{user['id']}", owner, json={"role": role})
        keys[label] = user["api_key"]

    for role in ROLES:
        add_member(alpha, role, role)
    add_member(beta, "steward", "beta-steward")
    service = rig.call(
        "POST", f"/agents/{alpha}/service-accounts", owner, json={"name": "rig-service", "role": "editor"}
    )
    keys["service-account"] = service["api_key"]

    facts = yaml.safe_load(CORPUS.read_text())["facts"][:30]
    for fact in facts:
        body = {"text": fact["text"], "agent_id": alpha, "source": fact["source"]}
        rig.call("POST", "/ingest/sync", owner, json=body)
    for text in BETA_FACTS:
        rig.call("POST", "/ingest/sync", owner, json={"text": text, "agent_id": beta})

    for question in QUESTIONS:
        answer = rig.call("POST", "/query", owner, json={"question": question, "agent_id": alpha})
        if answer.get("query_id"):
            used = [item["node_id"] for item in answer.get("citations", []) if item.get("node_id")][:2]
            if not used:
                used = [node["id"] for node in answer.get("subgraph", {}).get("nodes", [])][:2]
            if not used:
                continue
            rig.call(
                "POST", "/system/feedback", owner,
                json={"query_id": answer["query_id"], "used_node_ids": used, "success": True, "agent_id": alpha},
            )

    grant = rig.call("POST", "/federation/grants", owner, json={"source_kb": beta, "target_kb": alpha, "reason": "rig"})
    rig.call("POST", f"/federation/grants/{grant['id']}/approve", keys["beta-steward"], json={})

    rig.call("POST", f"/agents/{alpha}/sleep", owner)
    time.sleep(2)

    fd = os.open(KEYS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"keys": keys, "kbs": kbs}, handle, indent=2)
    print(f"seeded {len(facts)} facts, {len(kbs)} KBs, {len(keys)} keys -> {KEYS.name}")


if __name__ == "__main__":
    main()
