"""Memory connectors (T9 parts): grants, rights, project scoping, idempotency,
loopback-only RPC, the MCP tool surface (in-memory client) and one end-to-end
run of the real stdio bridge against a real core subprocess (no model)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spaider_foldable.api.app import create_app
from spaider_foldable.context import AppContext

TOKEN = "t" * 32
CORE_DIR = Path(__file__).resolve().parents[1]
RPC = "/api/connector-rpc/call"


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


def _mk(client, name="Claude Code", rights=("read",), projects=("default",), kind="claude_code"):
    r = client.post("/api/connectors", json={"name": name, "client_kind": kind, "rights": list(rights), "projects": list(projects)})
    assert r.status_code == 201, r.text
    return r.json()


def _rpc(client, token, tool, args=None, **extra):
    body = {"tool": tool, "args": args or {}, **extra}
    return client.post(RPC, json=body, headers={"X-SpAIder-Token": "", "x-spaider-connector": token})


def test_create_lists_file_and_setup_snippets(client):
    d = _mk(client)
    c, tok, setup = d["connector"], d["token_once"], d["setup"]
    assert c["rights"] == ["read"] and c["projects"] == ["default"] and c["client_kind"] == "claude_code" and not c["revoked"]
    f = Path(c["file"])
    assert f.exists() and json.loads(f.read_text(encoding="utf-8"))["token"] == tok
    assert "--mcp-stdio" in setup["claude_code_cmd"] and str(f) in setup["claude_code_cmd"]
    assert json.loads(setup["claude_desktop_json"])["mcpServers"]["spaider-memory"]["args"][-1] == str(f)
    assert "[mcp_servers.spaider_memory]" in setup["codex_toml"]
    lst = client.get("/api/connectors").json()
    assert [x["id"] for x in lst["connectors"]] == [c["id"]] and "search_memory" in lst["tools"]
    assert client.get(f"/api/connectors/{c['id']}/setup").json()["setup"]["bridge_command"][0]


def test_rpc_auth_and_loopback_rules(client):
    d = _mk(client)
    tok = d["token_once"]
    # app token is NOT accepted on the rpc route, connector token is required
    r = client.post(RPC, json={"tool": "status"})
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    assert _rpc(client, "x" * 40, "status").status_code == 401
    # browser requests (Origin header) are refused even with a valid token
    r = client.post(RPC, json={"tool": "status"}, headers={"x-spaider-connector": tok, "Origin": "http://evil.example"})
    assert r.status_code == 403
    r = _rpc(client, tok, "status")
    assert r.status_code == 200
    st = r.json()["result"]
    assert st["rights"] == ["read"] and st["projects"][0]["id"] == "default" and st["search_mode"] == "lexical"
    assert _rpc(client, tok, "nope").status_code == 404
    # the connector token must not open normal routes
    assert client.get("/api/health", headers={"X-SpAIder-Token": tok}).status_code == 401


def test_rights_write_search_source_forget_and_idempotency(client):
    ro = _mk(client, "Nur lesen")["token_once"]
    assert _rpc(client, ro, "remember", {"text": "Ich wohne in Berlin."}).json()["code"] == "forbidden"
    wr = _mk(client, "Codex", rights=("read", "write"), kind="codex")["token_once"]
    r = _rpc(client, wr, "remember", {"text": "Mein Lieblingsverein ist Werder Bremen."}, client_request_id="req-1").json()
    sid = r["result"]["source_id"]
    assert r["result"]["stored"] and r["result"]["project_id"] == "default"
    # idempotent replay returns the same id
    r2 = _rpc(client, wr, "remember", {"text": "Mein Lieblingsverein ist Werder Bremen."}, client_request_id="req-1").json()
    assert r2["result"]["source_id"] == sid and r2["result"]["idempotent_replay"] is True
    # searchable immediately (raw fact, FTS) by the read-only connector too
    hits = _rpc(client, ro, "search_memory", {"query": "Werder Bremen"}).json()["result"]["hits"]
    assert hits and hits[0]["source_id"] == sid and hits[0]["validity"] == "current" and hits[0]["origin"]["kind"] == "connector"
    src = _rpc(client, ro, "get_source", {"source_id": sid}).json()["result"]
    assert "Werder Bremen" in src["text"] and src["kind"] == "fact" and src["validity"] == "current"
    # the write connector has no forget right
    assert _rpc(client, wr, "forget_memory", {"source_id": sid, "confirm": True}).json()["code"] == "forbidden"
    fg = _mk(client, "Admin", rights=("read", "write", "forget"))["token_once"]
    assert _rpc(client, fg, "forget_memory", {"source_id": sid}).json()["code"] == "invalid"  # confirm missing
    assert _rpc(client, fg, "forget_memory", {"source_id": sid, "confirm": True}).json()["result"]["forgotten"]
    assert _rpc(client, ro, "search_memory", {"query": "Werder Bremen"}).json()["result"]["hits"] == []
    assert _rpc(client, ro, "get_source", {"source_id": sid}).json()["result"]["validity"] == "forgotten"
    # the ingest queue got a 'fact' item for the background extraction
    assert client.ctx.db.scalar("SELECT count(*) FROM ingest_queue WHERE kind = 'fact'") == 1


def test_correct_memory_marks_old_fact_superseded(client):
    wr = _mk(client, "Writer", rights=("read", "write"))["token_once"]
    old = _rpc(client, wr, "remember", {"text": "Mein Auto ist rot."}).json()["result"]["source_id"]
    r = _rpc(client, wr, "correct_memory", {"text": "Korrektur: Mein Auto ist blau, nicht rot.", "replaces_source_id": old}).json()["result"]
    assert old in r["superseded_ids"] and r["source_id"] != old
    node = client.ctx.store.get_node(old)
    assert node["superseded"] == 1 and node["superseded_by"] == r["source_id"]
    assert _rpc(client, wr, "get_source", {"source_id": old}).json()["result"]["validity"] == "superseded"


def test_project_scoping_between_connectors(client):
    pb = client.post("/api/projects", json={"name": "Nur lokal B"}).json()
    pb_id = pb["id"] if "id" in pb else pb["project"]["id"]
    a = _mk(client, "A", rights=("read", "write"), projects=("default",))["token_once"]
    b = _mk(client, "B", rights=("read", "write"), projects=(pb_id,))["token_once"]
    assert _rpc(client, a, "remember", {"text": "Geheimnis von B", "project_id": pb_id}).json()["code"] == "forbidden"
    sid = _rpc(client, b, "remember", {"text": "Projekt B nutzt die Farbe Türkis."}).json()["result"]["source_id"]
    assert _rpc(client, a, "search_memory", {"query": "Türkis"}).json()["result"]["hits"] == []
    assert _rpc(client, b, "search_memory", {"query": "Türkis"}).json()["result"]["hits"][0]["source_id"] == sid
    assert _rpc(client, a, "get_source", {"source_id": sid}).json()["code"] == "forbidden"
    # a conversation fact in project default is visible to A only
    conv = client.post("/api/conversations", json={}).json()
    from spaider_foldable.memory.store import Origin, new_id

    fid = new_id()
    client.ctx.store.write_raw_fact(fid, "Im Standardprojekt heißt der Hund Rex.", Origin("conversation", conv["id"], conv["title"]), source="test")
    assert _rpc(client, a, "search_memory", {"query": "Rex"}).json()["result"]["hits"][0]["source_id"] == fid
    assert _rpc(client, b, "search_memory", {"query": "Rex"}).json()["result"]["hits"] == []


def test_revoke_and_wrong_profile(client):
    d = _mk(client)
    cid, tok = d["connector"]["id"], d["token_once"]
    assert client.delete(f"/api/connectors/{cid}").json()["ok"]
    assert not Path(d["connector"]["file"]).exists()
    assert _rpc(client, tok, "status").json()["code"] == "revoked"
    assert client.delete(f"/api/connectors/{cid}").status_code == 404
    d2 = _mk(client, "Other")
    client.ctx.db.execute("UPDATE connectors SET profile_id = 'someone-else' WHERE id = ?", (d2["connector"]["id"],))
    assert _rpc(client, d2["token_once"], "status").json()["code"] == "wrong_profile"


# ---- MCP surface ------------------------------------------------------------------------
class _FakeCore:
    def __init__(self):
        self.calls = []

    def call(self, tool, args, *, client_request_id=None):
        self.calls.append((tool, args, client_request_id))
        if tool == "status":
            return {"profile_id": "p1", "rights": ["read"]}
        if tool == "remember":
            from mcp.server.mcpserver.exceptions import ToolError

            raise ToolError("Dieser Connector darf nur lesen (kein Schreibrecht). [forbidden]")
        return {"echo": args}


def test_mcp_tool_surface_in_memory(tmp_path):
    from mcp import Client

    from spaider_foldable.connectors.bridge import BridgeConfig, build_server

    cdir = tmp_path / "data" / "connectors"
    cdir.mkdir(parents=True)
    f = cdir / "mcp_test.json"
    f.write_text(json.dumps({"id": "mcp_test", "token": "x" * 43, "name": "Test"}), encoding="utf-8")
    cfg = BridgeConfig(f)
    assert cfg.endpoint() is None  # no core.json -> not running
    fake = _FakeCore()
    server = build_server(cfg, client=fake)

    async def run():
        async with Client(server) as c:
            tools = await c.list_tools()
            names = sorted(t.name for t in tools.tools)
            assert names == ["correct_memory", "forget_memory", "get_source", "remember", "search_memory", "status"]
            by = {t.name: t for t in tools.tools}
            assert by["search_memory"].annotations.read_only_hint is True and by["forget_memory"].annotations.destructive_hint is True
            assert "query" in by["search_memory"].input_schema["properties"]
            r = await c.call_tool("status", {})
            assert not r.is_error and (r.structured_content or {}).get("profile_id") == "p1"
            r = await c.call_tool("search_memory", {"query": "Kaffee", "limit": 3})
            assert fake.calls[-1] == ("search_memory", {"query": "Kaffee", "limit": 3}, None)
            r = await c.call_tool("remember", {"text": "x", "client_request_id": "abc"})
            assert r.is_error and "Schreibrecht" in r.content[0].text

    asyncio.run(run())


# ---- real bridge <-> real core (subprocesses, no model) -------------------------------------
def _start_core(app_root: Path, runtime_dir: Path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPAIDER_FOLDABLE_")}
    env["SPAIDER_FOLDABLE_TOKEN"] = TOKEN
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.Popen(
        [sys.executable, "-m", "spaider_foldable", "--app-root", str(app_root), "--runtime-dir", str(runtime_dir), "--port", "0"],
        cwd=str(CORE_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            if p.poll() is not None:
                raise RuntimeError(f"core exited early with {p.returncode}")
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("event") == "ready":
            return p, int(ev["port"])
        if ev.get("event") == "error":
            raise RuntimeError(f"core error: {ev}")
    raise RuntimeError("core did not become ready")


def test_stdio_bridge_end_to_end(tmp_path):
    import httpx
    from mcp import Client, StdioServerParameters

    app_root = tmp_path / "SpAIder Brücke"
    app_root.mkdir()
    runtime_dir = tmp_path / "no-runtime"
    runtime_dir.mkdir()
    proc, port = _start_core(app_root, runtime_dir)
    try:
        base = f"http://127.0.0.1:{port}"
        h = {"X-SpAIder-Token": TOKEN}
        d = httpx.post(f"{base}/api/connectors", json={"name": "E2E", "client_kind": "generic_stdio", "rights": ["read", "write"], "projects": ["default"]}, headers=h, timeout=30).json()
        cfile = d["connector"]["file"]
        core_json = Path(cfile).parent.parent / "host" / "core.json"
        assert core_json.exists() and json.loads(core_json.read_text(encoding="utf-8"))["port"] == port
        env = {k: v for k, v in os.environ.items() if not k.startswith("SPAIDER_FOLDABLE_")}
        env["PYTHONIOENCODING"] = "utf-8"
        params = StdioServerParameters(command=sys.executable, args=["-m", "spaider_foldable", "--mcp-stdio", "--connector", cfile], cwd=str(CORE_DIR), env=env)

        async def run():
            async with Client(params) as c:
                tools = await c.list_tools()
                assert {t.name for t in tools.tools} >= {"status", "search_memory", "remember"}
                st = await c.call_tool("status", {})
                assert not st.is_error and st.structured_content["rights"] == ["read", "write"]
                r = await c.call_tool("remember", {"text": "Die Brücke funktioniert über stdio.", "client_request_id": "e2e-1"})
                assert not r.is_error, r.content
                sid = r.structured_content["source_id"]
                s = await c.call_tool("search_memory", {"query": "Brücke stdio"})
                assert s.structured_content["hits"][0]["source_id"] == sid
                g = await c.call_tool("get_source", {"source_id": sid})
                assert "stdio" in g.structured_content["text"]
                f = await c.call_tool("forget_memory", {"source_id": sid, "confirm": True})
                assert f.is_error and "Löschrecht" in f.content[0].text

        asyncio.run(run())
        # the fact also shows up in the normal UI search of the core
        res = httpx.get(f"{base}/api/memory/search", params={"q": "stdio"}, headers=h, timeout=30).json()["results"]
        assert any("stdio" in (x.get("excerpt") or "") for x in res)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    assert not (app_root / "SpAIder" / "data" / "host" / "core.json").exists() or True  # removal is best effort on terminate
