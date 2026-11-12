"""Teach a workflow by demonstration ("Ablauf beibringen").

A demonstration is started explicitly for ONE source and records only inside
it: the own browser page (a small recorder script reports clicks, inputs and
selections of that page) or one granted window (the user presses "Schritt
erfassen"; the core diffs the UI Automation tree and records changed field
values; invoked buttons are named by the user). There is no global keyboard
hook and no screen recording - nothing outside the chosen source is seen.

Finishing a session creates a *candidate* workflow through the same compiler
as a task journal: the recorded steps become declarative actions with
element targets (role + name), parameters replace the typed values, checks
are declared by the user. The candidate is verified by running it (the
normal task path) before it is ever reused automatically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from ..store.common import utcnow
from .contracts import SuccessCriterion
from .models import ActionResult, Scopes
from .workflows import workflow_api

logger = logging.getLogger(__name__)

RECORDER_JS = r"""
() => {
  if (window.__spaiderTeachInstalled) return true;
  window.__spaiderTeachInstalled = true;
  const nameOf = (el) => {
    try {
      const aria = el.getAttribute && el.getAttribute('aria-label'); if (aria) return aria.trim().slice(0, 80);
      if (el.id) { const l = document.querySelector('label[for="' + el.id + '"]'); if (l) return l.innerText.trim().slice(0, 80); }
      const lab = el.closest && el.closest('label'); if (lab) return lab.innerText.replace(el.value || '', '').trim().slice(0, 80);
      if (el.placeholder) return el.placeholder.slice(0, 80);
      if (el.name) return el.name.slice(0, 80);
      return (el.innerText || el.value || el.title || '').trim().slice(0, 80);
    } catch (e) { return ''; }
  };
  const roleOf = (el) => {
    const tag = el.tagName.toLowerCase();
    return el.getAttribute('role') || ({a: 'link', button: 'button', input: (el.type || 'text'), select: 'combobox', textarea: 'textbox', summary: 'button'}[tag] || tag);
  };
  const send = (ev) => { try { window.__spaiderTeach(JSON.stringify(ev)); } catch (e) {} };
  document.addEventListener('click', (e) => {
    const t = e.target && e.target.closest ? e.target.closest('a[href],button,[role=button],[role=link],[role=tab],[role=menuitem],input[type=submit],input[type=button],input[type=checkbox],input[type=radio],summary') : null;
    if (!t) return;
    send({kind: 'click', role: roleOf(t), name: nameOf(t), ref: t.getAttribute('data-spaider-ref') || ''});
  }, true);
  document.addEventListener('change', (e) => {
    const el = e.target; if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') { const o = el.options[el.selectedIndex]; send({kind: 'select', role: 'combobox', name: nameOf(el), value: el.value, label: o ? o.text : '', ref: el.getAttribute('data-spaider-ref') || ''}); }
    else if (tag === 'input' || tag === 'textarea' || el.isContentEditable) {
      if (el.type === 'checkbox' || el.type === 'radio' || el.type === 'password') return;
      send({kind: 'fill', role: roleOf(el), name: nameOf(el), value: el.value, ref: el.getAttribute('data-spaider-ref') || ''});
    }
  }, true);
  return true;
}
"""

VALUE_ROLES = {"Edit", "Document", "ComboBox", "Spinner", "Text"}


class TeachError(Exception):
    def __init__(self, code: str, detail: str, status: int = 409):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


class TeachSession:
    def __init__(self, *, name: str, source: str, scopes: Scopes, url: Optional[str], source_id: Optional[str]):
        self.id = f"teach_{uuid.uuid4().hex[:10]}"
        self.name = name
        self.source = source
        self.scopes = scopes
        self.url = url
        self.source_id = source_id
        self.state = "recording"
        self.steps: list[dict[str, Any]] = []
        self.created_at = utcnow()
        self.finished_at: Optional[str] = None
        self.last_snapshot = None  # window: Snapshot of the last capture
        self.element_names: dict[str, str] = {}
        self.events = 0
        self.workflow_id: Optional[str] = None
        self.note_de = ""

    def to_api(self) -> dict:
        return {
            "id": self.id, "name": self.name, "source": self.source, "url": self.url, "source_id": self.source_id, "state": self.state,
            "steps": [{k: v for k, v in s.items() if k != "evidence"} | {"evidence": s.get("evidence")} for s in self.steps],
            "created_at": self.created_at, "finished_at": self.finished_at, "events": self.events, "workflow_id": self.workflow_id, "note_de": self.note_de,
            "scopes": self.scopes.model_dump(),
        }


class TeachManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.sessions: dict[str, TeachSession] = {}
        self._recorder_bound = False

    def status(self) -> dict:
        active = [s for s in self.sessions.values() if s.state == "recording"]
        return {"active": len(active), "sessions": [s.to_api() for s in self.sessions.values()][-10:]}

    def get(self, sid: str) -> TeachSession:
        s = self.sessions.get(sid)
        if s is None:
            raise TeachError("not_found", "Demonstration nicht gefunden.", 404)
        return s

    # ---- start ------------------------------------------------------------------------------
    async def start(self, *, name: str, source: str, scopes: Scopes, url: Optional[str], source_id: Optional[str]) -> TeachSession:
        if any(s.state == "recording" for s in self.sessions.values()):
            raise TeachError("busy", "Es läuft bereits eine Demonstration; bitte zuerst beenden oder abbrechen.")
        if self.ctx.agent.running():
            raise TeachError("busy", "Während eine Aufgabe läuft, kann keine Demonstration aufgenommen werden.")
        s = TeachSession(name=name, source=source, scopes=scopes, url=url, source_id=source_id)
        if source == "browser":
            if not url:
                raise TeachError("invalid", "Für eine Browser-Demonstration wird eine Startadresse benötigt.", 422)
            if not (scopes.browser and scopes.browser.enabled):
                raise TeachError("scope", "Der Browser ist in den Zugriffen nicht freigegeben.", 422)
            adapter = self.ctx.browser
            ok, reason = adapter.availability()
            if not ok:
                raise TeachError("browser_missing", reason or "Browser nicht verfügbar.")
            info = await adapter.open(url, scopes.browser.allowed_origins, task_id=f"teach:{s.id}")
            page = await adapter._ensure()
            if not self._recorder_bound:
                try:
                    await page.context.expose_binding("__spaiderTeach", lambda source_, payload: self._on_browser_event(payload))
                    self._recorder_bound = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("teach: recorder binding: %s", exc)
            await page.add_init_script("(" + RECORDER_JS.strip() + ")();")
            await page.evaluate(RECORDER_JS)
            snap = await adapter.snapshot(limit=200)
            s.steps.append(self._step("browser.open", {"url": url}, check_de=f"Seite geöffnet: {info.get('title') or url}", evidence={"url": info.get("url"), "title": info.get("title")}))
            s.steps.append(self._step("browser.snapshot", {}, check_de="Seite gelesen (Elemente)", data={"elements": snap.get("elements"), "url": snap.get("url"), "title": snap.get("title"), "text": (snap.get("text") or "")[:3000]}, evidence={"url": snap.get("url")}))
            self._browser_session = s
        elif source == "window":
            if not source_id or not (scopes.windows and source_id in scopes.windows.source_ids):
                raise TeachError("scope", "Das Fenster muss in den Zugriffen freigegeben sein.", 422)
            src = self.ctx.live.resolve_source(source_id)
            snap = await asyncio.to_thread(self.ctx.live.taker.take, s.id, src, want_image=False, max_elements=200)
            s.last_snapshot = snap
            s.element_names = {e.id: e.name for e in snap.tree.elements}
            s.steps.append(self._step("windows.inspect", {"source_id": source_id}, check_de=f"{len(snap.tree.elements)} Elemente gelesen", data={"structure": snap.context_text(max_elements=80, max_chars=3500)}, evidence={"elements": len(snap.tree.elements)}))
        else:
            raise TeachError("invalid", "Quelle muss browser oder window sein.", 422)
        self.sessions[s.id] = s
        self.ctx.bus.publish("teach", {"id": s.id, "state": s.state, "steps": len(s.steps)})
        return s

    @staticmethod
    def _step(tool: str, arguments: dict, *, check_de: str = "", evidence: Optional[dict] = None, data: Optional[dict] = None, target: Optional[dict] = None) -> dict:
        return {"tool": tool, "arguments": arguments, "check_de": check_de, "evidence": evidence or {}, "data": data or {}, "target": target, "at": utcnow()}

    # ---- browser events (recorder) ---------------------------------------------------------
    def _on_browser_event(self, payload: str) -> None:
        s = getattr(self, "_browser_session", None)
        if s is None or s.state != "recording":
            return
        try:
            ev = json.loads(payload)
        except ValueError:
            return
        s.events += 1
        kind = ev.get("kind")
        name = str(ev.get("name") or "")[:80]
        role = str(ev.get("role") or "")
        ref = str(ev.get("ref") or "")
        target = {"role": role, "name": name}
        if kind == "click":
            s.steps.append(self._step("browser.click", {"ref": ref or "{target}"}, check_de=f"„{name}“ geklickt", evidence={"name": name, "role": role}, target=target))
        elif kind == "fill":
            value = str(ev.get("value") or "")
            # consecutive edits of the same field collapse into the final value
            if s.steps and s.steps[-1]["tool"] == "browser.fill" and (s.steps[-1].get("target") or {}).get("name") == name:
                s.steps[-1]["arguments"]["text"] = value
                s.steps[-1]["evidence"]["value_after"] = value
            else:
                s.steps.append(self._step("browser.fill", {"ref": ref or "{target}", "text": value}, check_de=f"„{name}“ ausgefüllt", evidence={"name": name, "role": role, "value_after": value}, target=target))
        elif kind == "select":
            s.steps.append(self._step("browser.select", {"ref": ref or "{target}", "value": str(ev.get("value") or "")}, check_de=f"„{name}“ gewählt: {ev.get('label') or ev.get('value')}", evidence={"name": name, "role": "combobox", "value_after": str(ev.get("value") or "")}, target=target))
        else:
            return
        self.ctx.bus.publish("teach", {"id": s.id, "state": s.state, "steps": len(s.steps)})

    # ---- window capture (explicit) -----------------------------------------------------------
    async def capture(self, sid: str, *, invoke_name: Optional[str] = None) -> list[dict]:
        s = self.get(sid)
        if s.state != "recording":
            raise TeachError("finished", "Die Demonstration ist beendet.")
        if s.source == "browser":
            snap = await self.ctx.browser.snapshot(limit=200)
            step = self._step("browser.snapshot", {}, check_de="Seite gelesen (Elemente)", data={"elements": snap.get("elements"), "url": snap.get("url"), "title": snap.get("title"), "text": (snap.get("text") or "")[:3000]}, evidence={"url": snap.get("url")})
            s.steps.append(step)
            return [step]
        src = self.ctx.live.resolve_source(s.source_id)
        snap = await asyncio.to_thread(self.ctx.live.taker.take, s.id, src, want_image=False, max_elements=200)
        new: list[dict] = []
        before = {e.path: e for e in (s.last_snapshot.tree.elements if s.last_snapshot else [])}
        for e in snap.tree.elements:
            if e.role not in VALUE_ROLES:
                continue
            old = before.get(e.path)
            old_val = (old.value if old is not None and old.value is not None else "")
            new_val = e.value if e.value is not None else ""
            if new_val != old_val and new_val.strip():
                new.append(self._step("windows.set_value", {"source_id": s.source_id, "element_id": e.id, "text": new_val}, check_de=f"Feld „{e.name}“ gesetzt", evidence={"element": e.name, "value_after": new_val, "method": "demonstration"}, target={"role": e.role, "name": e.name}))
        if invoke_name:
            hits = [e for e in snap.tree.elements if (e.name or "").strip().lower() == invoke_name.strip().lower()]
            if len(hits) != 1:
                raise TeachError("ambiguous" if hits else "not_found", f"Element „{invoke_name}“ ist {'mehrdeutig' if hits else 'nicht sichtbar'}.", 422)
            e = hits[0]
            new.append(self._step("windows.invoke", {"source_id": s.source_id, "element_id": e.id}, check_de=f"„{e.name}“ ausgelöst", evidence={"element": e.name, "method": "demonstration"}, target={"role": e.role, "name": e.name}))
        if new:
            new.append(self._step("windows.read", {"source_id": s.source_id}, check_de="Fenstertext gelesen", data={"text": snap.tree.text_excerpt[:3000]}, evidence={"text_excerpt": snap.tree.text_excerpt[:600]}))
        s.steps.extend(new)
        s.last_snapshot = snap
        s.element_names.update({e.id: e.name for e in snap.tree.elements})
        s.events += 1
        self.ctx.bus.publish("teach", {"id": s.id, "state": s.state, "steps": len(s.steps)})
        return new

    def add_check(self, sid: str, check: dict) -> dict:
        s = self.get(sid)
        c = SuccessCriterion.model_validate(check)
        if s.source == "browser" and c.kind == "page_text":
            s.steps.append(self._step("browser.wait_for_text", {"text": c.expect[0] if c.expect else "", "timeout_ms": 5000}, check_de=f"Text erschien: „{c.expect[0] if c.expect else ''}“", evidence={"text": c.expect[0] if c.expect else ""}))
        elif s.source == "window" and c.kind in ("window_text", "element_value"):
            s.steps.append(self._step("windows.read", {"source_id": s.source_id}, check_de=f"Fenster zeigt: „{c.expect[0] if c.expect else ''}“", evidence={"text_excerpt": c.expect[0] if c.expect else ""}))
        return s.steps[-1]

    def remove_step(self, sid: str, index: int) -> None:
        s = self.get(sid)
        if 0 <= index < len(s.steps):
            del s.steps[index]

    # ---- finish / cancel ---------------------------------------------------------------------
    async def finish(self, sid: str, *, parameters: list[dict], checks: list[dict]) -> dict:
        s = self.get(sid)
        if s.state != "recording":
            raise TeachError("finished", "Die Demonstration ist bereits beendet.")
        actionable = [st for st in s.steps if st["tool"] not in ("browser.snapshot", "windows.inspect")]
        if not actionable:
            raise TeachError("empty", "Es wurden keine Schritte erfasst.", 422)
        s.state = "finished"
        s.finished_at = utcnow()
        if s.source == "browser":
            self._browser_session = None
        ctx = self.ctx
        task = ctx.tasks.create(goal=f"Demonstration „{s.name}“ ({'Browser' if s.source == 'browser' else 'Fenster'})", scopes=s.scopes, max_steps=max(1, len(s.steps)), max_seconds=60, conversation_id=None, live_session_id=None, recipe_id=None, parameters=None)
        action_data: dict[str, dict] = {}
        for st in s.steps:
            a = ctx.tasks.add_action(task.id, tool=st["tool"], arguments=st["arguments"], expected_effect=st.get("check_de", ""), observation_ref=None, side_effect=_side_effect(st["tool"]), state="done", origin="teach")
            a = ctx.tasks.set_action_state(a.action_id, "done", result=ActionResult(ok=True, summary_de=st.get("check_de") or st["tool"], evidence=st.get("evidence") or None, verified=True), finished=True, duration_ms=0)
            if st.get("data"):
                action_data[a.action_id] = st["data"]
        ctx.tasks.update(task.id, state="completed", outcome="unclear", summary_de="Demonstration aufgezeichnet (nicht ausgeführt).", result_json=json.dumps({"ok": True, "summary_de": "Demonstration aufgezeichnet", "artifacts": [], "verified": False, "outcome": "unclear"}, ensure_ascii=False), finished_at=utcnow())
        criteria = [SuccessCriterion.model_validate(c) for c in checks]
        wf = ctx.workflow_compiler.compile(
            task, ctx.tasks.actions(task.id), name=s.name, parameters=parameters, action_data=action_data,
            element_names={s.source_id: s.element_names} if s.source_id else {}, window_hints=ctx.window_hints([s.source_id] if s.source_id else []),
            criteria=criteria, origin_kind="taught",
        )
        wf.known_limits_de = ["Aus einer Demonstration abgeleitet und noch nicht ausgeführt: erst ein Prüflauf (Ablauf starten) belegt, dass die Schritte funktionieren."] + wf.known_limits_de
        wf = ctx.workflows.save(wf, origin_kind="taught", origin_task_id=task.id)
        s.workflow_id = wf.id
        s.note_de = "Kandidat gespeichert; bitte mit „Ablauf starten“ prüfen."
        ctx.bus.publish("teach", {"id": s.id, "state": s.state, "steps": len(s.steps), "workflow_id": wf.id})
        return {"session": s.to_api(), "workflow": workflow_api(wf), "task_id": task.id}

    async def cancel(self, sid: str) -> TeachSession:
        s = self.get(sid)
        if s.state == "recording":
            s.state = "cancelled"
            s.finished_at = utcnow()
            if s.source == "browser":
                self._browser_session = None
        self.ctx.bus.publish("teach", {"id": s.id, "state": s.state, "steps": len(s.steps)})
        return s

    async def stop_all(self) -> int:
        n = 0
        for s in list(self.sessions.values()):
            if s.state == "recording":
                await self.cancel(s.id)
                n += 1
        return n


def _side_effect(tool: str) -> str:
    return "local" if tool.split(".")[-1] in ("open", "click", "fill", "select", "press", "invoke", "set_value", "type_text", "send_keys", "click_at", "launch", "write", "back", "focus") else "none"


def _now_ms() -> int:
    return int(time.time() * 1000)
