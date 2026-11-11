"""T11 canary: what leaves the machine when a chat is bound to a (fake) cloud
connection. Memories from a „Nur lokal“ project never appear in the request,
memories from the shared project do; a chat inside the local-only project is
blocked before any request. No local model is needed for connection-bound chats."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from spaider_foldable.api.app import create_app
from spaider_foldable.context import AppContext
from spaider_foldable.memory.store import Origin, new_id
from tests.providers.fakeserver import FakeProvider, Scripted, cc_chunk, sse

TOKEN = "t" * 32
CANARY = "CANARY-7731"


@pytest.fixture
def client(paths, tmp_path):
    runtime_dir = tmp_path / "no-runtime"
    runtime_dir.mkdir()
    ctx = AppContext(paths, runtime_dir)
    ctx.startup_sync()
    app = create_app(ctx, TOKEN)
    with TestClient(app, headers={"X-SpAIder-Token": TOKEN}) as c:
        c.ctx = ctx  # type: ignore[attr-attr-defined]
        yield c
    asyncio.run(ctx.shutdown())


@pytest.fixture
def cloud():
    f = FakeProvider().start()
    f.on("GET", "/v1/models", Scripted(200, "application/json", json.dumps({"data": [{"id": "fake-cloud-1"}]}).encode()))
    f.on("POST", "/v1/chat/completions", Scripted(200, "text/event-stream", sse(cc_chunk("Antwort aus der Wolke."), cc_chunk(finish="stop"))))
    yield f
    f.stop()


def _events(resp):
    out = []
    event, data = None, []
    for line in resp.iter_lines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "" and event:
            out.append((event, json.loads("\n".join(data) or "{}")))
            event, data = None, []
    return out


def test_local_only_memories_never_reach_a_cloud_connection(client, cloud):
    ctx = client.ctx
    # a cloud connection (fake, loopback) with sharing allowed for every project
    r = client.post("/api/connections", json={"provider": "openai_compatible", "name": "Fake Cloud", "base_url": cloud.base_url + "/v1", "auth_method": "none", "credential_storage": "session", "execution": "cloud", "data_sharing": "allow", "extra": {"allow_insecure_http": True}})
    assert r.status_code == 201, r.text
    conn_id = r.json()["connection"]["id"]
    # a local-only project with a canary fact, and a shared fact in the default project
    pr = client.post("/api/projects", json={"name": "Vertraulich", "local_only": True}).json()
    secret_pid = pr["project"]["id"] if "project" in pr else pr["id"]
    secret_conv = client.post("/api/conversations", json={"title": "Geheim"}).json()
    ctx.conversations.set_project(secret_conv["id"], secret_pid)
    ctx.store.write_raw_fact(new_id(), f"Das Projektwort lautet {CANARY}.", Origin("conversation", secret_conv["id"], "Geheim"), source="test")
    public_conv = client.post("/api/conversations", json={"title": "Offen"}).json()
    ctx.store.write_raw_fact(new_id(), "Die Lieblingsfarbe ist Türkis.", Origin("conversation", public_conv["id"], "Offen"), source="test")
    # sanity: both facts are visible to the local UI search
    res = client.get("/api/memory/search", params={"q": "Projektwort"}).json()["results"]
    assert any(CANARY in (x.get("excerpt") or "") for x in res)

    # chat in the default project bound to the cloud connection (no local model running)
    chat = client.post("/api/conversations", json={"title": "Wolke"}).json()
    b = client.put(f"/api/conversations/{chat['id']}/binding", json={"kind": "connection", "connection_id": conn_id, "provider_model_id": "fake-cloud-1"})
    assert b.status_code == 200, b.text
    with client.stream("POST", f"/api/conversations/{chat['id']}/messages", json={"content": "Wie lautet das Projektwort und welche Lieblingsfarbe habe ich?"}) as resp:
        assert resp.status_code == 200
        evs = _events(resp)
    kinds = [e for e, _ in evs]
    assert "sharing" in kinds and "done" in kinds and "error" not in kinds, kinds
    done = next(d for e, d in evs if e == "done")["message"]
    assert done["content"].startswith("Antwort aus der Wolke") and done["connection_id"] == conn_id and done["provider_model"] == "fake-cloud-1"
    sent = [q for q in cloud.requests if q.path == "/v1/chat/completions"]
    assert len(sent) == 1
    body = json.dumps(sent[0].body, ensure_ascii=False)
    assert "Türkis" in body, "shared-project memory should be included"
    assert CANARY not in body, "local-only project memory leaked into the cloud request"
    # the sharing event reports counts only
    sharing = next(d for e, d in evs if e == "sharing")
    assert sharing["connection_id"] == conn_id and sharing["execution"] == "cloud" and "memory_chars" in sharing and CANARY not in json.dumps(sharing)

    # a chat inside the local-only project is blocked before any request leaves
    secret_chat = client.post("/api/conversations", json={"title": "Geheim 2"}).json()
    ctx.conversations.set_project(secret_chat["id"], secret_pid)
    client.put(f"/api/conversations/{secret_chat['id']}/binding", json={"kind": "connection", "connection_id": conn_id, "provider_model_id": "fake-cloud-1"})
    with client.stream("POST", f"/api/conversations/{secret_chat['id']}/messages", json={"content": f"Sag {CANARY}"}) as resp:
        evs2 = _events(resp)
    err = next(d for e, d in evs2 if e == "error")
    assert err["code"] == "local_only" and err["sharing"]["estimate"] is True and "done" not in [e for e, _ in evs2]
    assert len([q for q in cloud.requests if q.path == "/v1/chat/completions"]) == 1
    # and the canary never appeared in any request to the fake provider at all
    assert CANARY not in json.dumps([q.body for q in cloud.requests], ensure_ascii=False)
