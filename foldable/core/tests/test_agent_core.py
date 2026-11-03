"""Agent core: broker validation/scopes, file tools inside roots, the runner
with a scripted fake model (no llama-server), cancellation and approvals."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from spaider_foldable.activity import ActivityBus
from spaider_foldable.agent.broker import Broker, ToolContext
from spaider_foldable.agent.loop import AgentManager
from spaider_foldable.agent.models import FilesScope, Scopes
from spaider_foldable.agent.store import TaskStore
from spaider_foldable.agent.tools import files as files_tools
from spaider_foldable.inference.client import ChatChunk
from spaider_foldable.scheduler import LLMScheduler


class _Prefs:
    pass


class _ScriptedClient:
    """Returns the scripted JSON decisions in order; records prompts."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.prompts = []

    async def stream_chat(self, messages, **kwargs):
        self.prompts.append(messages)
        assert kwargs.get("json_schema"), "planner must request a JSON schema"
        d = self.decisions.pop(0) if self.decisions else {"decision": "done", "done": {"ok": False, "summary": "leer", "verified_by": ""}, "thought": ""}
        yield ChatChunk(text=json.dumps(d))
        yield ChatChunk(done=True)


def _manager(db, client, tmp_path):
    broker = Broker()
    files_tools.register(broker)
    store = TaskStore(db, host_id="host-test")
    bus = ActivityBus()

    async def observe(task, latest_refs):
        return "", {}, {}

    mgr = AgentManager(store=store, broker=broker, scheduler=LLMScheduler(cooldown_s=0.0), bus=bus, get_client=lambda: client, observe_sources=observe, settings_loader=lambda: _Prefs())
    return mgr, store, broker


async def _wait_terminal(store, tid, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        t = store.get(tid)
        if t.state in ("completed", "failed", "cancelled", "interrupted", "waiting_for_user", "paused"):
            return t
        await asyncio.sleep(0.05)
    return store.get(tid)


@pytest.mark.asyncio
async def test_file_task_read_combine_write_verified(db, tmp_path):
    root = tmp_path / "Projekt ä"
    root.mkdir()
    (root / "a.txt").write_text("Budget Atlas: 42000 Euro", encoding="utf-8")
    (root / "b.md").write_text("Leitung Atlas: Priya Nair", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    decisions = [
        {"thought": "Dateien lesen", "decision": "action", "action": {"tool": "files.list", "arguments": {"path": str(root)}}, "expected_effect": "Liste"},
        {"thought": "a lesen", "decision": "action", "action": {"tool": "files.read", "arguments": {"path": str(root / "a.txt")}}, "expected_effect": "Inhalt"},
        {"thought": "b lesen", "decision": "action", "action": {"tool": "files.read", "arguments": {"path": str(root / "b.md")}}, "expected_effect": "Inhalt"},
        {"thought": "schreiben", "decision": "action", "action": {"tool": "files.write", "arguments": {"path": "zusammenfassung.md", "content": "# Atlas\n- Budget: 42000 Euro\n- Leitung: Priya Nair\n"}}, "expected_effect": "Datei"},
        {"thought": "fertig", "decision": "done", "done": {"ok": True, "summary": "Zusammenfassung geschrieben", "verified_by": "files.write Rücklesen"}},
    ]
    client = _ScriptedClient(decisions)
    mgr, store, _ = _manager(db, client, tmp_path)
    task = store.create(goal="Fasse a.txt und b.md zusammen", scopes=Scopes(files=FilesScope(roots=[str(root)], write_dir=str(out))), max_steps=10, max_seconds=60, conversation_id=None, live_session_id=None, recipe_id=None, parameters=None)
    mgr.start(task)
    t = await _wait_terminal(store, task.id)
    assert t.state == "completed", (t.state, t.summary_de, t.last_error)
    assert t.result.ok and t.result.verified is True
    assert (out / "zusammenfassung.md").read_text(encoding="utf-8").startswith("# Atlas")
    acts = store.actions(task.id)
    assert [a.tool for a in acts] == ["files.list", "files.read", "files.read", "files.write"]
    assert all(a.state == "done" for a in acts)
    assert t.llm_calls == 5 and t.step == 5
    # the planner saw the file contents (data of previous actions) in its prompt
    assert "Priya Nair" in client.prompts[3][1]["content"]
    assert any(a.path.endswith("zusammenfassung.md") for a in t.result.artifacts)


@pytest.mark.asyncio
async def test_scope_escape_and_invalid_arguments_are_rejected(db, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("geheim", encoding="utf-8")
    decisions = [
        {"thought": "", "decision": "action", "action": {"tool": "files.read", "arguments": {"path": str(secret)}}, "expected_effect": ""},
        {"thought": "", "decision": "action", "action": {"tool": "files.read", "arguments": {"path": str(root / ".." / "secret.txt")}}, "expected_effect": ""},
        {"thought": "", "decision": "action", "action": {"tool": "files.write", "arguments": {"path": str(root / "x.txt"), "content": "x"}}, "expected_effect": ""},
        {"thought": "", "decision": "action", "action": {"tool": "shell.run", "arguments": {"cmd": "format c:"}}, "expected_effect": ""},
        {"thought": "", "decision": "action", "action": {"tool": "files.list", "arguments": {"nope": 1}}, "expected_effect": ""},
        {"thought": "", "decision": "done", "done": {"ok": False, "summary": "nichts erlaubt", "verified_by": ""}},
    ]
    client = _ScriptedClient(decisions)
    mgr, store, _ = _manager(db, client, tmp_path)
    task = store.create(goal="lies geheim", scopes=Scopes(files=FilesScope(roots=[str(root)], write_dir=None)), max_steps=10, max_seconds=60, conversation_id=None, live_session_id=None, recipe_id=None, parameters=None)
    mgr.start(task)
    t = await _wait_terminal(store, task.id)
    assert t.state == "failed"
    acts = store.actions(task.id)
    states = [(a.tool, a.state) for a in acts]
    assert states[0] == ("files.read", "failed") and "außerhalb" in acts[0].result.summary_de
    assert states[1] == ("files.read", "failed")
    assert states[2] == ("files.write", "failed") and "Schreibordner" in acts[2].result.summary_de
    assert states[3] == ("shell.run", "rejected")
    assert states[4] == ("files.list", "rejected")
    assert not (root / "x.txt").exists()


@pytest.mark.asyncio
async def test_approval_required_for_consequential_targets(db, tmp_path):
    broker = Broker()

    async def fake_click(args, ctx):
        from spaider_foldable.agent.broker import ToolOutcome

        return ToolOutcome(ok=True, summary_de="geklickt", verified=True)

    from spaider_foldable.agent.broker import Tool

    broker.register(Tool(name="windows.invoke", description_de="klickt", scope="windows", side_effect="local", schema={"type": "object", "properties": {"source_id": {"type": "string"}, "element_id": {"type": "string"}}, "required": ["source_id", "element_id"]}, run=fake_click))
    store = TaskStore(db, host_id="h")
    decisions = [
        {"thought": "", "decision": "action", "action": {"tool": "windows.invoke", "arguments": {"source_id": "win:1:2", "element_id": "e5"}}, "expected_effect": "sendet"},
        {"thought": "", "decision": "done", "done": {"ok": True, "summary": "gesendet", "verified_by": "windows.invoke"}},
    ]
    client = _ScriptedClient(decisions)

    async def observe(task, latest_refs):
        return "", {"win:1:2": {"e5"}}, {"win:1:2": "snap_1"}

    from spaider_foldable.agent.models import WindowsScope

    mgr = AgentManager(store=store, broker=broker, scheduler=LLMScheduler(cooldown_s=0.0), bus=ActivityBus(), get_client=lambda: client, observe_sources=observe, settings_loader=lambda: _Prefs())
    task = store.create(goal="E-Mail senden", scopes=Scopes(windows=WindowsScope(source_ids=["win:1:2"], allowed_processes=[]), external_effects=False), max_steps=5, max_seconds=60, conversation_id=None, live_session_id=None, recipe_id=None, parameters=None)
    mgr.remember_element_names(task.id, {"e5": "Senden"})
    mgr.start(task)
    t = await _wait_terminal(store, task.id)
    assert t.state == "waiting_for_user" and t.pending_question.kind == "approval"
    # deny -> action rejected, task continues and finishes with the next decision
    mgr.answer(task.id, t.pending_question.id, approve=False, text=None)
    for _ in range(100):  # the runner resumes on the next loop turn
        await asyncio.sleep(0.05)
        if store.get(task.id).state != "waiting_for_user":
            break
    t = await _wait_terminal(store, task.id)
    acts = store.actions(task.id)
    assert acts[0].state == "rejected" and "abgelehnt" in (acts[0].reason_de or "")
    # a success claim without any executed action is refused; the scripted client then runs out of decisions
    assert t.state == "failed" and not t.result.verified


@pytest.mark.asyncio
async def test_cancel_blocks_further_actions(db, tmp_path):
    broker = Broker()
    from spaider_foldable.agent.broker import Tool, ToolOutcome

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(args, ctx):
        started.set()
        await release.wait()
        return ToolOutcome(ok=True, summary_de="fertig", verified=True)

    broker.register(Tool(name="slow.op", description_de="", scope="memory", side_effect="local", schema={"type": "object", "properties": {}}, run=slow, timeout_s=10))
    store = TaskStore(db, host_id="h")
    decisions = [{"thought": "", "decision": "action", "action": {"tool": "slow.op", "arguments": {}}, "expected_effect": ""}] * 3
    client = _ScriptedClient(decisions)

    async def observe(task, latest_refs):
        return "", {}, {}

    mgr = AgentManager(store=store, broker=broker, scheduler=LLMScheduler(cooldown_s=0.0), bus=ActivityBus(), get_client=lambda: client, observe_sources=observe, settings_loader=lambda: _Prefs())
    task = store.create(goal="x", scopes=Scopes(memory=True), max_steps=5, max_seconds=60, conversation_id=None, live_session_id=None, recipe_id=None, parameters=None)
    mgr.start(task)
    await asyncio.wait_for(started.wait(), 5)
    t = mgr.cancel(task.id)
    assert t.state == "cancelled"
    release.set()
    await asyncio.sleep(0.3)
    acts = store.actions(task.id)
    assert len(acts) == 1 and acts[0].state in ("unknown_outcome", "cancelled")
    assert store.get(task.id).state == "cancelled"
    assert len(client.decisions) == 2  # no further planning happened


def test_tool_context_and_broker_schema_checks():
    broker = Broker()
    files_tools.register(broker)
    sc = Scopes(files=FilesScope(roots=["C:/tmp"], write_dir=None))
    tool, err = broker.validate("files.read", {"path": "C:/tmp/a.txt", "max_chars": 50}, sc)
    assert err and "zu klein" in err
    tool, err = broker.validate("files.read", {"path": 5}, sc)
    assert err and "Text" in err
    tool, err = broker.validate("files.read", {"path": "x"}, Scopes())
    assert err and "Umfang" in err
    assert broker.validate("nope", {}, sc)[1].startswith("Unbekannt")
