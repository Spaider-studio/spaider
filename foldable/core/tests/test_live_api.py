"""Live/agent API without a model (Windows desktop of the test machine): sources,
sessions on a screen, preview snapshot, self-window exclusion, error shapes,
agent capabilities/browser status and task creation refusal without a model."""

from __future__ import annotations

import asyncio
import sys

import pytest
from fastapi.testclient import TestClient

from spaider_foldable.api.app import create_app
from spaider_foldable.context import AppContext
from spaider_foldable.observe import windows as W

TOKEN = "t" * 32
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not W.desktop_interactive(), reason="needs an interactive Windows desktop")


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


def test_sources_and_screen_session(client):
    r = client.get("/api/live/sources")
    assert r.status_code == 200
    d = r.json()
    assert d["screens"] and all(not w["is_self"] for w in d["windows"])
    assert d["desktop_interactive"] is True
    fg = client.post("/api/live/foreground").json()
    assert "source" in fg and "captured_at" in fg
    assert client.post("/api/live/sessions", json={"source_id": "win:1:1"}).status_code == 404
    assert client.post("/api/live/sessions", json={"source_id": "nonsense"}).status_code == 404
    screen = d["screens"][0]["id"]
    r = client.post("/api/live/sessions", json={"source_id": screen})
    assert r.status_code == 201, r.text
    s = r.json()["session"]
    assert s["capture_mode"] == "screen" and s["vision_available"] is False and s["notes_de"]
    conv = client.get(f"/api/conversations/{s['conversation_id']}").json()["conversation"]
    assert conv["title"].startswith("Live:")
    snap = client.post(f"/api/live/sessions/{s['id']}/snapshot", json={"preview": True, "max_elements": 20}).json()["snapshot"]
    assert snap["source_kind"] == "screen" and snap["quality"] in ("ok", "black")
    assert snap["image"] is None or snap["image"]["preview_png_base64"]
    assert snap["age_ms"] >= 0 and "transform" in snap
    # asking without a model is refused cleanly
    r = client.post(f"/api/live/sessions/{s['id']}/ask", json={"content": "Was ist zu sehen?", "use_image": "always"})
    assert r.status_code == 409 and r.json()["code"] in ("vision_unavailable", "no_model")
    assert client.delete(f"/api/live/sessions/{s['id']}").json()["ok"] is True
    assert client.get(f"/api/live/sessions/{s['id']}").status_code == 404


def test_agent_capabilities_and_task_refusal_without_model(client):
    caps = client.get("/api/agent/capabilities").json()
    names = {t["name"] for t in caps["tools"]}
    assert {"files.read", "files.write", "memory.search", "windows.inspect", "browser.open", "vision.locate"} <= names
    assert caps["windows_automation"]["available"] is True
    assert caps["model"]["vision_input"] is False and caps["model"]["tool_call_format"] == "none"
    assert client.get("/api/agent/browser").json()["package"]["revision"] == "1248"
    r = client.post("/api/agent/tasks", json={"goal": "Lies alles", "scopes": {"files": {"roots": ["C:/"], "write_dir": None}}})
    assert r.status_code == 409 and r.json()["code"] == "no_model"
    assert client.get("/api/agent/tasks").json()["tasks"] == []
    assert client.post("/api/agent/stop").json() == {"stopped_tasks": 0, "stopped_live": 0}
    assert client.get("/api/agent/recipes").json()["recipes"] == []
