"""API tests without a model: auth, error shapes, conversations/notes/documents
CRUD, settings validation, eject status, path traversal."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spaider_foldable.api.app import create_app
from spaider_foldable.context import AppContext

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


def test_requires_token(client):
    r = client.get("/api/health", headers={"X-SpAIder-Token": "wrong"})
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    assert client.get("/api/health").status_code == 200


def test_setup_status_without_model(client):
    s = client.get("/api/setup/status").json()
    assert s["state"] == "needs_model" and s["active_chat_model"] is None
    r = client.post("/api/conversations", json={"title": None})
    cid = r.json()["id"]
    r = client.post(f"/api/conversations/{cid}/messages", json={"content": "Hallo"})
    assert r.status_code == 409 and r.json()["code"] == "no_model"


def test_conversation_crud(client):
    c = client.post("/api/conversations", json={}).json()
    assert c["title"] == "Neues Gespräch"
    c2 = client.patch(f"/api/conversations/{c['id']}", json={"title": "Projekt Orion"}).json()
    assert c2["title"] == "Projekt Orion"
    lst = client.get("/api/conversations").json()["conversations"]
    assert [x["id"] for x in lst] == [c["id"]]
    assert client.delete(f"/api/conversations/{c['id']}").json()["ok"]
    assert client.get(f"/api/conversations/{c['id']}").status_code == 404
    assert client.patch("/api/conversations/nope", json={"title": "x"}).json()["code"] == "not_found"


def test_notes_and_memory_overview(client):
    n = client.post("/api/memory/notes", json={"text": "Ich trinke Kaffee ohne Zucker."}).json()
    assert (
        client.patch(f"/api/memory/notes/{n['id']}", json={"text": "Ich trinke Kaffee mit Milch."})
        .json()["text"]
        .endswith("Milch.")
    )
    ov = client.get("/api/memory/overview").json()
    assert ov["notes"] == 1 and ov["index"]["fts"] is True and ov["index"]["vectors"]["enabled"] is False
    res = client.get("/api/memory/search", params={"q": "Kaffee"}).json()["results"]
    assert res and res[0]["kind"] == "note"
    assert client.delete(f"/api/memory/notes/{n['id']}").json()["ok"]


def test_settings_validation(client):
    s = client.get("/api/settings").json()
    assert s["context_tokens"] == 8192
    r = client.patch("/api/settings", json={"context_tokens": 16384, "thinking_enabled": True})
    assert r.status_code == 200 and r.json()["context_tokens"] == 16384
    r = client.patch("/api/settings", json={"context_tokens": 5})
    assert r.status_code == 422 and r.json()["code"] == "invalid_setting"
    r = client.patch("/api/settings", json={"unknown_key": 1})
    assert r.status_code == 422


def test_document_upload_and_delete(client, paths):
    files = {"file": ("notizen.txt", b"Das Meeting mit Zephyr ist am 3. Dezember in Berlin.", "text/plain")}
    r = client.post("/api/documents", files=files)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["text_chars"] > 0 and doc["status"] in ("indexing", "indexed")
    assert (paths.imports_dir / doc["id"] / "notizen.txt").exists()
    assert client.get(f"/api/documents/{doc['id']}/text").json()["text"].startswith("Das Meeting")
    r = client.post("/api/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415 and r.json()["code"] == "unsupported_type"
    assert client.delete(f"/api/documents/{doc['id']}").json()["ok"]
    assert not (paths.imports_dir / doc["id"]).exists()


def test_model_import_rejects_outside_and_invalid(client, tmp_path):
    r = client.post("/api/models/import", json={"path": "relative.gguf"})
    assert r.status_code == 409 and r.json()["code"] == "gguf_invalid"
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"nope" * 100)
    r = client.post("/api/models/import", json={"path": str(bad)})
    assert r.status_code == 202
    job_id = r.json()["job"]["id"]
    for _ in range(50):
        job = client.get(f"/api/models/jobs/{job_id}").json()["job"]
        if job["state"] in ("done", "failed"):
            break
        import time

        time.sleep(0.05)
    assert job["state"] == "failed" and job["error"]["code"] == "gguf_invalid"
    r = client.post("/api/models/download", json={"catalog_id": "does-not-exist"})
    assert r.status_code == 404
    r = client.post("/api/models/activate", json={"model_id": "nope"})
    assert r.status_code == 404


def test_catalog_has_fit_and_license(client):
    entries = client.get("/api/models/catalog").json()["entries"]
    assert entries and all("fit" in e and e["license"]["id"] for e in entries)
    assert all(e["fit"]["level"] in ("recommended", "ok", "tight", "too_large", "unknown") for e in entries)


def test_eject_status_and_shutdown(client):
    st = client.get("/api/system/eject-status").json()
    assert st["ready"] is False and st["db_open"] is True
    info = client.get("/api/system/info").json()
    assert info["ram_total_bytes"] > 0 and info["usage"]["db_bytes"] > 0 and "runtime" in info
    assert Path(info["data_dir"]).exists()
