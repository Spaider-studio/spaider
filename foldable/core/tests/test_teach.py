"""Teach by demonstration: window capture diff with fake snapshots (no desktop), and - when the
Chromium package is available (SPAIDER_FOLDABLE_BROWSER_DIR) - the real browser recorder: the
own Chromium page records clicks/inputs/selections of a local test page, the session compiles
into a candidate workflow with role+name targets."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from spaider_foldable.agent.models import BrowserScope, Scopes, WindowsScope
from spaider_foldable.agent.teach import TeachError, TeachManager, TeachSession
from spaider_foldable.observe.uia import UiElement, UiTree
from spaider_foldable.observe.windows import Rect


def _el(eid: str, role: str, name: str, value: str | None) -> UiElement:
    return UiElement(id=eid, role=role, name=name, value=value, rect=Rect(0, 0, 10, 10), enabled=True, focused=False, path=eid)


def _snap(elements, text=""):
    tree = UiTree(elements=elements, total_seen=len(elements), text_excerpt=text)
    return SimpleNamespace(tree=tree, context_text=lambda max_elements=80, max_chars=3500: text)


class _Bus:
    def __init__(self):
        self.events = []

    def publish(self, kind, payload):
        self.events.append((kind, payload))


@pytest.mark.asyncio
async def test_window_capture_records_changed_values_and_named_buttons():
    snaps = [
        _snap([_el("e1", "Edit", "Name", ""), _el("e2", "Edit", "Notizen", ""), _el("e3", "Button", "Speichern", None)], "Bereit"),
        _snap([_el("e1", "Edit", "Name", "Mara Muster"), _el("e2", "Edit", "Notizen", ""), _el("e3", "Button", "Speichern", None)], "Bereit"),
        _snap([_el("e1", "Edit", "Name", "Mara Muster"), _el("e2", "Edit", "Notizen", "Kickoff 03.12."), _el("e3", "Button", "Speichern", None)], "Gespeichert: x"),
    ]
    taker = SimpleNamespace(take=lambda sid, src, want_image=False, max_elements=200: (snaps.pop(0) if len(snaps) > 1 else snaps[0]))
    ctx = SimpleNamespace(live=SimpleNamespace(resolve_source=lambda sid: SimpleNamespace(id=sid, process="powershell.exe", title="Test"), taker=taker), bus=_Bus(), agent=SimpleNamespace(running=lambda: []))
    tm = TeachManager(ctx)
    scopes = Scopes(windows=WindowsScope(source_ids=["win:1:1"], allowed_processes=["powershell.exe"]))
    s = await tm.start(name="Formular", source="window", scopes=scopes, url=None, source_id="win:1:1")
    assert s.state == "recording" and s.steps[0]["tool"] == "windows.inspect"
    new = await tm.capture(s.id)
    assert [x["tool"] for x in new] == ["windows.set_value", "windows.read"]
    assert new[0]["arguments"] == {"source_id": "win:1:1", "element_id": "e1", "text": "Mara Muster"} and new[0]["target"] == {"role": "Edit", "name": "Name"}
    new = await tm.capture(s.id, invoke_name="Speichern")
    assert [x["tool"] for x in new] == ["windows.set_value", "windows.invoke", "windows.read"]
    assert new[1]["target"]["name"] == "Speichern" and new[0]["target"]["name"] == "Notizen"
    with pytest.raises(TeachError):
        await tm.capture(s.id, invoke_name="Gibt es nicht")
    # outside the granted scope: refused
    with pytest.raises(TeachError):
        await tm.start(name="x", source="window", scopes=Scopes(windows=WindowsScope(source_ids=[], allowed_processes=[])), url=None, source_id="win:1:1")
    assert tm.status()["active"] == 1
    await tm.cancel(s.id)
    assert tm.status()["active"] == 0


def test_teach_session_api_shape():
    s = TeachSession(name="n", source="browser", scopes=Scopes(browser=BrowserScope(enabled=True)), url="file:///x.html", source_id=None)
    d = s.to_api()
    assert d["state"] == "recording" and d["steps"] == [] and d["scopes"]["browser"]["enabled"] is True


BROWSER_DIR = os.environ.get("SPAIDER_FOLDABLE_BROWSER_DIR")


@pytest.mark.skipif(not BROWSER_DIR, reason="set SPAIDER_FOLDABLE_BROWSER_DIR to the folder with the Chromium package")
@pytest.mark.asyncio
async def test_real_browser_recorder_compiles_a_candidate(db, paths, tmp_path):
    """Real Chromium: the recorder script on the own page reports a fill, a select and a click
    (Playwright dispatches real input events), the session finishes into a candidate workflow
    whose steps target elements by role + name."""
    from spaider_foldable.agent.bindings import BindingResolver
    from spaider_foldable.agent.compiler import WorkflowCompiler
    from spaider_foldable.agent.deps import DependencyStore
    from spaider_foldable.agent.store import TaskStore
    from spaider_foldable.agent.tools import browser as browser_tools
    from spaider_foldable.agent.workflows import WorkflowStore

    chrome = browser_tools.find_chrome(Path(BROWSER_DIR))
    if chrome is None:
        pytest.skip("no chromium in SPAIDER_FOLDABLE_BROWSER_DIR")
    web = tmp_path / "web"
    web.mkdir()
    page = web / "seite.html"
    page.write_text("""<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Projektanfrage</title></head><body>
<form onsubmit="event.preventDefault();document.getElementById('o').textContent='Entwurf gespeichert: '+document.getElementById('n').value;">
<p><label>Projektname <input id="n"></label></p>
<p><label>Priorität <select id="p"><option>niedrig</option><option>hoch</option></select></label></p>
<p><button type="submit">Als Entwurf speichern</button></p></form><p id="o"></p></body></html>""", encoding="utf-8")
    adapter = browser_tools.BrowserAdapter(paths.data_dir, chrome)
    store = TaskStore(db, "host-test")
    deps = DependencyStore(db, "host-test")
    wfs = WorkflowStore(db, deps, "host-test")
    broker = SimpleNamespace(all_tools=lambda: [])
    compiler = WorkflowCompiler(tool_infos=lambda: [], host_id="host-test")
    ctx = SimpleNamespace(browser=adapter, bus=_Bus(), agent=SimpleNamespace(running=lambda: []), tasks=store, workflows=wfs, workflow_compiler=compiler, window_hints=lambda sids: {}, live=None, broker=broker)
    tm = TeachManager(ctx)
    scopes = Scopes(browser=BrowserScope(enabled=True, allowed_origins=[str(web)]))
    try:
        s = await tm.start(name="Anfrage", source="browser", scopes=scopes, url="file:///" + str(page).replace("\\", "/"), source_id=None)
        pg = await adapter._ensure()
        await pg.fill("#n", "Nordlicht")
        await pg.select_option("#p", "hoch")
        await pg.click("button[type=submit]")
        for _ in range(40):
            await asyncio.sleep(0.1)
            if sum(1 for st in s.steps if st["tool"] in ("browser.fill", "browser.select", "browser.click")) >= 3:
                break
        tools = [st["tool"] for st in s.steps]
        assert tools[:2] == ["browser.open", "browser.snapshot"] and "browser.fill" in tools and "browser.select" in tools and "browser.click" in tools, tools
        fill = next(st for st in s.steps if st["tool"] == "browser.fill")
        assert fill["target"]["name"].startswith("Projektname") and fill["arguments"]["text"] == "Nordlicht"
        click = next(st for st in s.steps if st["tool"] == "browser.click")
        assert click["target"]["name"] == "Als Entwurf speichern"
        tm.add_check(s.id, {"kind": "page_text", "target": "browser", "expect": ["Entwurf gespeichert"]})
        res = await tm.finish(s.id, parameters=[{"name": "projekt", "value": "Nordlicht", "description_de": "Projektname"}], checks=[{"kind": "page_text", "target": "browser", "expect": ["Entwurf gespeichert: {projekt}"]}])
        wf = wfs.get(res["workflow"]["id"])
        assert wf.status == "candidate" and wf.stats["origin_kind"] == "taught"
        fill_step = next(st for st in wf.steps if st.tool == "browser.fill")
        assert fill_step.target["name"].startswith("Projektname") and fill_step.arguments_template["text"] == "{projekt}" and fill_step.arguments_template["ref"] == "{target}"
        assert wf.checks and wf.checks[0].expect == ["Entwurf gespeichert: {projekt}"]
        assert any(l.startswith("Aus einer Demonstration") for l in wf.known_limits_de)
    finally:
        await adapter.close()
