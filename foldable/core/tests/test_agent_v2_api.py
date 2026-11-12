"""API level of the execution core: workflows endpoints, local-only lock for cloud planners,
bindings, stop, recipe compatibility, schema v6 migration on an existing v5 database."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from spaider_foldable.api.app import create_app
from spaider_foldable.context import AppContext
from spaider_foldable.db import Database
from spaider_foldable.db.migrations import MIGRATIONS, run_schema, _sql

TOKEN = "t" * 32


@pytest.fixture
def client(paths, tmp_path):
    runtime_dir = tmp_path / "no-runtime"
    runtime_dir.mkdir()
    ctx = AppContext(paths, runtime_dir)
    ctx.startup_sync()
    app = create_app(ctx, TOKEN)
    with TestClient(app, headers={"X-SpAIder-Token": TOKEN}) as c:
        c.ctx = ctx  # type: ignore[attr-defined]
        yield c
    asyncio.run(ctx.shutdown())


def test_workflows_bindings_and_stop_endpoints(client, tmp_path):
    assert client.get("/api/agent/workflows").json()["workflows"] == []
    caps = client.get("/api/agent/capabilities").json()
    assert caps["execution_core"]["version"] == "1.5" and caps["execution_core"]["input_guard"]["enabled"] is True
    b = client.put("/api/agent/bindings", json={"role": "output_dir", "kind": "dir", "target": str(tmp_path)}).json()["binding"]
    assert b["role"] == "output_dir"
    assert [x["role"] for x in client.get("/api/agent/bindings").json()["bindings"]] == ["output_dir"]
    assert client.delete("/api/agent/bindings/output_dir").json()["ok"] is True
    assert client.get("/api/agent/workflows/wf_nope").status_code == 404
    stop = client.post("/api/agent/stop").json()
    assert stop["stopped_tasks"] == 0 and stop["lock_ms"] < 100 and "released_keys" in stop
    assert client.get("/api/agent/teach").json()["active"] == 0


def test_cloud_planner_respects_local_only_and_consent(client):
    pr = client.post("/api/projects", json={"name": "Vertraulich", "local_only": True}).json()
    project_id = pr["project"]["id"] if "project" in pr else pr["id"]
    conn = client.post("/api/connections", json={"provider": "openai", "name": "Cloud", "auth_method": "api_key", "credential_storage": "session", "api_key": "sk-test-0000000000"}).json()
    conn_id = conn["connection"]["id"] if "connection" in conn else conn["id"]
    body = {"goal": "Lies alles", "scopes": {"memory": True}, "connection_id": conn_id, "project_id": project_id}
    r = client.post("/api/agent/tasks", json=body)
    assert r.status_code == 403 and r.json()["code"] == "local_only"
    pr2 = client.post("/api/projects", json={"name": "Offen", "local_only": False}).json()
    open_id = pr2["project"]["id"] if "project" in pr2 else pr2["id"]
    r = client.post("/api/agent/tasks", json={**body, "project_id": open_id})
    assert r.status_code == 409 and r.json()["code"] == "consent_required"


def test_schema_v6_migrates_v5_database_with_recipes(tmp_path):
    """A v5 profile database (0.4) with a stored 0.3 recipe opens on the 0.5 core: v6 applied,
    agent tables extended, the recipe visible as a migrated candidate workflow, nothing deleted."""
    path = tmp_path / "spaider.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
    for version, name, filename in MIGRATIONS:
        if version > 5:
            break
        conn.execute("BEGIN")
        run_schema(conn, _sql(filename))
        conn.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (?,?,'2026-11-01T00:00:00Z')", (version, name))
        conn.execute("COMMIT")
    conn.execute("INSERT INTO agent_recipes(id, version, name, recipe_json, created_at, updated_at) VALUES ('rcp_1', 1, 'Alt', ?, 'x', 'x')", ('{"id":"rcp_1","version":1,"name":"Alt","purpose_de":"p","parameters":[],"origin":{},"requires":{"tools":["files.read"]},"preconditions":[],"scope":{"files":null,"browser":null,"windows":null,"memory":true,"external_effects":false},"steps":[{"tool":"files.read","arguments_template":{"path":"C:/x/a.txt"},"check":""}],"final_checks":[],"last_verified_at":null,"known_limits_de":[]}',))
    conn.execute("INSERT INTO agent_tasks(id, goal, state, step, max_steps, max_seconds, scopes_json, summary_de, host_id, created_at, updated_at) VALUES ('task_old', 'g', 'completed', 1, 5, 60, '{}', '', 'h', 'x', 'x')")
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    conn.close()
    db = Database(path)
    db.open()
    try:
        assert db.scalar("PRAGMA user_version") == 6
        cols = {r[1] for r in db.query("PRAGMA table_info(agent_tasks)")}
        assert {"success_json", "outcome", "workflow_id", "reuse_json"} <= cols
        assert db.scalar("SELECT count(*) FROM agent_recipes") == 1 and db.scalar("SELECT count(*) FROM agent_tasks") == 1
        from spaider_foldable.agent.deps import DependencyStore
        from spaider_foldable.agent.store import TaskStore
        from spaider_foldable.agent.workflows import WorkflowStore

        wfs = WorkflowStore(db, DependencyStore(db, "h"), "h")
        assert wfs.migrate_recipes(TaskStore(db, "h").list_recipes()) == 1
        assert wfs.get("rcp_1").status == "candidate"
        assert db.scalar("PRAGMA quick_check") == "ok"
    finally:
        db.close()
