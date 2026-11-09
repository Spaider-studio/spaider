"""CLI commands against a fake HTTP layer."""
from __future__ import annotations

import json
import os
import stat

import httpx
import pytest
from typer.testing import CliRunner

from spaider_cli.lib import clients, config, stack
from spaider_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SPAIDER_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("SPAIDER_API_KEY", raising=False)
    monkeypatch.delenv("SPAIDER_API_BASE", raising=False)
    monkeypatch.delenv("SPAIDER_AGENT_ID", raising=False)
    monkeypatch.chdir(tmp_path)


class Api:
    def __init__(self, monkeypatch):
        self.calls = []
        self.responses = {}
        monkeypatch.setattr(httpx, "request", self.handle)

    def handle(self, method, url, json=None, params=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "params": params, "headers": headers})
        path = url.split("/api/v1", 1)[1]
        status, body = self.responses.get((method, path), (200, {}))
        return httpx.Response(status, json=body)


@pytest.fixture
def api(monkeypatch):
    config.save(api_key="sk-test", agent_id="kb1")
    return Api(monkeypatch)


def test_every_documented_command_exists():
    output = runner.invoke(app, ["--help"], env={"COLUMNS": "200"}).output
    for command in "init up down status logs doctor login ingest recall query sleep grant kpr finetune export".split():
        assert command in output, command


def test_login_saves_a_private_file(monkeypatch, tmp_path):
    api = Api(monkeypatch)
    api.responses[("GET", "/auth/whoami")] = (200, {"id": "u1", "name": "Ann", "default_kb": "kb9"})
    result = runner.invoke(app, ["login", "--api-key", "sk-abc"])
    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(os.stat(config.config_path()).st_mode) == 0o600
    assert config.api_key() == "sk-abc" and config.default_agent() == "kb9"
    assert "sk-abc" not in result.output


def test_a_bad_key_is_not_saved(monkeypatch):
    api = Api(monkeypatch)
    api.responses[("GET", "/auth/whoami")] = (401, {"detail": "Missing or invalid API key."})
    result = runner.invoke(app, ["login", "--api-key", "bad"])
    assert result.exit_code == 1 and "invalid" in result.output
    assert not config.config_path().exists()


def test_requests_carry_the_key(api):
    api.responses[("POST", "/recall")] = (200, {"results": [{"type": "episode", "text": "found it"}]})
    result = runner.invoke(app, ["recall", "something"])
    assert result.exit_code == 0 and "found it" in result.output
    call = api.calls[0]
    assert call["headers"]["X-Api-Key"] == "sk-test" and call["json"]["agent_id"] == "kb1"


def test_no_knowledge_base_is_a_clear_error(monkeypatch):
    config.save(api_key="sk")
    Api(monkeypatch)
    result = runner.invoke(app, ["recall", "x"])
    assert result.exit_code == 1 and "knowledge base" in result.output


def test_ingest_from_a_file(api, tmp_path):
    (tmp_path / "note.txt").write_text("from file", encoding="utf-8")
    api.responses[("POST", "/ingest/sync")] = (200, {"nodes_created": 1, "nodes_merged": 0, "edges_created": 0})
    result = runner.invoke(app, ["ingest", "--file", "note.txt"])
    assert result.exit_code == 0, result.output
    assert api.calls[0]["json"]["text"] == "from file"


def test_query_shows_citations(api):
    citations = [{"ref": "n1", "label": "Acme"}]
    api.responses[("POST", "/query")] = (200, {"answer": "Berlin [n1].", "citations": citations, "query_id": "q"})
    output = runner.invoke(app, ["query", "where?"]).output
    assert "Berlin" in output and "Acme" in output and "q" in output


def test_sleep_report(api):
    api.responses[("GET", "/agents/kb1/sleep/report")] = (200, {"status": "completed"})
    assert "completed" in runner.invoke(app, ["sleep", "--report"]).output


@pytest.mark.parametrize(
    ("arguments", "method", "path"),
    [
        (["grant", "approve", "g1"], "POST", "/federation/grants/g1/approve"),
        (["grant", "deny", "g1"], "POST", "/federation/grants/g1/deny"),
        (["grant", "revoke", "g1"], "DELETE", "/federation/grants/g1"),
        (["kpr", "approve", "k1"], "POST", "/federation/kpr/k1/approve"),
        (["kpr", "reject", "k1"], "POST", "/federation/kpr/k1/reject"),
        (["kpr", "revert", "k1"], "POST", "/federation/kpr/k1/revert"),
        (["kpr", "show", "k1"], "GET", "/federation/kpr/k1"),
        (["finetune", "status", "v1"], "GET", "/finetune/jobs/v1"),
        (["finetune", "promote", "v1"], "POST", "/finetune/jobs/v1/promote"),
        (["finetune", "rollback", "kb1"], "POST", "/finetune/rollback"),
    ],
)
def test_governance_commands_reach_their_routes(api, arguments, method, path):
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0, result.output
    assert api.calls[0]["method"] == method and api.calls[0]["url"].endswith(path)


def test_grant_request_sends_its_scope(api):
    runner.invoke(app, ["grant", "request", "src", "tgt", "--node-type", "PERSON", "--max-clearance", "2"])
    assert api.calls[0]["json"] == {
        "source_kb": "src", "target_kb": "tgt", "node_types": ["PERSON"],
        "relation_types": None, "max_clearance": 2, "expires_at": None,
    }


def test_api_errors_exit_nonzero_with_the_reason(api):
    api.responses[("POST", "/federation/kpr/k1/approve")] = (403, {"detail": "needs a second person"})
    result = runner.invoke(app, ["kpr", "approve", "k1"])
    assert result.exit_code == 1 and "second person" in result.output


@pytest.mark.parametrize("name", clients.SUPPORTED)
def test_every_client_installs_with_a_private_config(name, tmp_path):
    path = clients.install(name, "http://h/mcp", "ss-key", cwd=tmp_path)
    assert "ss-key" in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_there_are_eight_clients():
    assert len(clients.SUPPORTED) == 8


def test_installing_keeps_other_servers(tmp_path):
    target = tmp_path / ".cursor" / "mcp.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"mcpServers": {"other": {"url": "x"}}}), encoding="utf-8")
    clients.install("cursor", "http://h/mcp", "ss-key", cwd=tmp_path)
    assert set(json.loads(target.read_text())["mcpServers"]) == {"other", "spaider"}


def test_codex_replaces_its_block_only(tmp_path):
    target = tmp_path / ".codex" / "config.toml"
    target.parent.mkdir()
    target.write_text('model = "x"\n', encoding="utf-8")
    clients.install("codex", "http://h/mcp", "key1")
    clients.install("codex", "http://h/mcp", "key2")
    body = target.read_text()
    assert 'model = "x"' in body and "key2" in body and "key1" not in body and body.count("spaider:start") == 1


def test_mcp_add_makes_one_key_per_client(api, tmp_path):
    api.responses[("POST", "/agents/kb1/service-accounts")] = (201, {"api_key": "ss-new"})
    result = runner.invoke(app, ["mcp", "add", "cursor"])
    assert result.exit_code == 0, result.output
    assert api.calls[0]["json"]["name"] == "mcp-cursor"
    assert "ss-new" in (tmp_path / ".cursor/mcp.json").read_text() and "ss-new" not in result.output


def test_mcp_add_rejects_unknown_clients(api):
    assert runner.invoke(app, ["mcp", "add", "emacs"]).exit_code == 1


def test_bundled_stack_unpacks(tmp_path):
    root = stack.locate()
    assert (root / "docker-compose.yml").exists() and (root / "gateway/kong.yml").exists()
    assert (root / ".env").exists()


def test_up_passes_the_profile(monkeypatch, tmp_path):
    seen = {}

    class Done:
        returncode = 0

    def run(command, cwd, check, text, capture_output):
        seen["command"] = command
        return Done()

    monkeypatch.setattr("subprocess.run", run)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    result = runner.invoke(app, ["up", "--profile", "lite"])
    assert result.exit_code == 0, result.output
    assert seen["command"][:4] == ["docker", "compose", "--profile", "lite"] and "--wait" in seen["command"]


def test_bootstrap_network_failure_is_a_clean_error(monkeypatch):
    import importlib

    from spaider_cli.lib import api

    importlib.reload(api)
    monkeypatch.setenv("SPAIDER_API_BASE", "http://127.0.0.1:9/api/v1")
    with pytest.raises(api.SpaiderApiError):
        api.bootstrap("t", "o", "n")


def test_doctor_warns_when_not_logged_in():
    from spaider_cli.commands import doctor

    result = doctor.check_login()
    assert result.status == "warn" and "spaider login" in result.detail


def test_doctor_fails_on_a_rejected_key(monkeypatch):
    from spaider_cli.commands import doctor

    config.save(api_key="sk-old")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(401, json={}))
    assert doctor.check_login().status == "fail"


def test_doctor_names_the_logged_in_principal(monkeypatch):
    from spaider_cli.commands import doctor

    config.save(api_key="sk-good")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json={"name": "Ann", "org_role": "owner"}))
    result = doctor.check_login()
    assert result.status == "ok" and "Ann" in result.detail


def test_doctor_flags_a_stale_skill(tmp_path):
    from spaider_cli.commands import doctor

    path = tmp_path / ".claude/skills/spaider.md"
    path.parent.mkdir(parents=True)
    path.write_text("spaider.query spaider.feedback", encoding="utf-8")
    result = doctor.check_skill_file(home=tmp_path)
    assert result.status == "warn" and "spaider mcp skill" in result.detail


def test_mcp_skill_refreshes_without_touching_keys(tmp_path):
    config_file = tmp_path / ".claude/.mcp.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('{"mcpServers": {"spaider": {"url": "x"}}}', encoding="utf-8")
    result = runner.invoke(app, ["mcp", "skill"])
    assert result.exit_code == 0, result.output
    assert "spaider.recall" in (tmp_path / ".claude/skills/spaider.md").read_text(encoding="utf-8")
    assert config_file.read_text(encoding="utf-8") == '{"mcpServers": {"spaider": {"url": "x"}}}'
