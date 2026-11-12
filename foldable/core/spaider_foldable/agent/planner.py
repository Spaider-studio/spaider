"""Planner: one decision per step, from either model path, normalised into
the same dictionary the loop validates and executes::

    {"call_id": "...", "thought": "...", "decision": "action" | "ask_user" | "done",
     "action": {"tool": "files.read", "arguments": {...}}, "expected_effect": "...",
     "ask_user": {...}, "done": {...}}

* Local runtime: grammar-constrained JSON (``response_format: json_schema``)
  exactly as in 0.3; the JSON is parsed only after the stream ended - a
  partially streamed object is never executed.
* Provider connection with native tool calls: every broker tool plus
  ``ask_user`` and ``done`` are offered as functions; the adapter emits a
  ``tool_call`` event only when the call is complete. Tool results are fed
  back under the same call id so the model round really continues.
* Both paths: complete arguments are validated by the broker afterwards; a
  provider error is surfaced as a task error, never as an empty "done".
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from ..providers.events import GenerationError, GenerationRequest
from ..scheduler import LLMScheduler
from .models import Task
from .prompts import build_action_schema

logger = logging.getLogger(__name__)

MAX_REPAIRS = 2
NATIVE_HISTORY = 16
_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


def native_name(tool: str) -> str:
    return _NAME_RE.sub("_", tool.replace(".", "__"))


def from_native_name(name: str, known: dict[str, str]) -> str:
    return known.get(name, name.replace("__", "."))


def native_tool_specs(tool_infos: list[dict]) -> list[dict]:
    specs = []
    for t in tool_infos:
        specs.append({"type": "function", "function": {"name": native_name(t["name"]), "description": (t.get("description_de") or "")[:1000], "parameters": t.get("schema") or {"type": "object", "properties": {}}}})
    specs.append({"type": "function", "function": {"name": "ask_user", "description": "Rückfrage oder Freigabe beim Nutzer einholen (nur wenn eine nötige Information fehlt oder eine folgenreiche Wirkung bevorsteht).", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": ["approval", "input"]}}, "required": ["text", "kind"]}}})
    specs.append({"type": "function", "function": {"name": "done", "description": "Aufgabe abschließen, nachdem Werkzeugergebnisse das Ziel belegen (ok=false, wenn das Ziel nicht erreichbar ist).", "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}, "summary": {"type": "string"}, "verified_by": {"type": "string"}}, "required": ["ok", "summary", "verified_by"]}}})
    return specs


class PlannerError(Exception):
    def __init__(self, code: str, message_de: str):
        super().__init__(message_de)
        self.code = code
        self.message_de = message_de


class Planner:
    def __init__(
        self,
        *,
        scheduler: LLMScheduler,
        get_client: Callable[[], Any],
        reasoning_toggle: Callable[[], Optional[str]] = lambda: None,
        adapter_for_task: Optional[Callable[[Task], Awaitable[Any]]] = None,
    ):
        self.scheduler = scheduler
        self.get_client = get_client
        self.reasoning_toggle = reasoning_toggle
        self.adapter_for_task = adapter_for_task
        self._native: dict[str, list[dict]] = {}
        self._pending_calls: dict[str, dict[str, str]] = {}  # task_id -> {call_id: tool}
        self.timings: dict[str, dict[str, int]] = {}

    def forget(self, task_id: str) -> None:
        self._native.pop(task_id, None)
        self._pending_calls.pop(task_id, None)
        self.timings.pop(task_id, None)

    def _timing(self, task_id: str, key: str, ms: int) -> None:
        t = self.timings.setdefault(task_id, {})
        t[key] = t.get(key, 0) + int(ms)

    # ---- public ---------------------------------------------------------------------------
    async def decide(self, task: Task, *, system: str, user: str, tool_infos: list[dict], is_cancelled: Callable[[], bool], count_call: Callable[[], None]) -> Optional[dict]:
        adapter = None
        if self.adapter_for_task is not None and task.connection_id:
            adapter = await self.adapter_for_task(task)
        if adapter is not None:
            return await self._decide_native(task, adapter, system=system, user=user, tool_infos=tool_infos, is_cancelled=is_cancelled, count_call=count_call)
        return await self._decide_grammar(task, system=system, user=user, tool_infos=tool_infos, is_cancelled=is_cancelled, count_call=count_call)

    def record_tool_result(self, task_id: str, call_id: Optional[str], text: str) -> None:
        """Feed an executed call's result back into the native conversation (no-op for grammar)."""
        if not call_id or task_id not in self._native:
            return
        pend = self._pending_calls.get(task_id, {})
        if call_id not in pend:
            return
        pend.pop(call_id, None)
        self._native[task_id].append({"role": "tool", "tool_call_id": call_id, "content": text[:6000]})
        del self._native[task_id][:-NATIVE_HISTORY]

    # ---- grammar JSON (local runtime) --------------------------------------------------------
    async def _decide_grammar(self, task: Task, *, system: str, user: str, tool_infos: list[dict], is_cancelled, count_call) -> Optional[dict]:
        schema = build_action_schema(tool_infos)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        toggle = self.reasoning_toggle()
        for attempt in range(MAX_REPAIRS + 1):
            client = self.get_client()
            if client is None:
                raise PlannerError("runtime_not_ready", "Kein Chatmodell bereit.")
            text = ""
            t_wait = time.monotonic()
            async with self.scheduler.agent():
                self._timing(task.id, "wait_ms", int((time.monotonic() - t_wait) * 1000))
                t_llm = time.monotonic()
                count_call()
                try:
                    async for chunk in client.stream_chat(messages, temperature=0.1, max_tokens=700, enable_thinking=(False if toggle else None), reasoning_toggle=toggle, json_schema=schema, timeout_s=240.0):
                        if is_cancelled():
                            return None
                        if chunk.text:
                            text += chunk.text
                except RuntimeError as exc:
                    raise PlannerError("runtime_error", f"Die lokale Modell-Laufzeit meldete einen Fehler: {str(exc)[:200]}")
            self._timing(task.id, "planning_ms", int((time.monotonic() - t_llm) * 1000))
            try:
                decision = _parse_decision(text)
                decision["call_id"] = f"call_{uuid.uuid4().hex[:10]}"
                return decision
            except ValueError as exc:
                logger.warning("agent decision invalid (attempt %s): %s | %s", attempt, exc, text[:200])
                messages.append({"role": "assistant", "content": text[:2000]})
                messages.append({"role": "user", "content": f"Die Antwort war kein gültiges Entscheidungs-JSON ({exc}). Antworte erneut nur mit dem JSON-Objekt."})
        return None

    # ---- native tool calls (provider connection) ---------------------------------------------
    async def _decide_native(self, task: Task, adapter, *, system: str, user: str, tool_infos: list[dict], is_cancelled, count_call) -> Optional[dict]:
        known = {native_name(t["name"]): t["name"] for t in tool_infos}
        history = self._native.setdefault(task.id, [])
        pending = self._pending_calls.setdefault(task.id, {})
        # calls the model made that never got a result (rejected/cancelled) are answered explicitly
        for cid, tool in list(pending.items()):
            history.append({"role": "tool", "tool_call_id": cid, "content": f"{tool}: nicht ausgeführt (abgelehnt oder abgebrochen)."})
            pending.pop(cid, None)
        messages = [{"role": "system", "content": system}] + history[-NATIVE_HISTORY:] + [{"role": "user", "content": user}]
        tools = native_tool_specs(tool_infos)
        model = getattr(task, "provider_model", None) or getattr(adapter, "default_model", None) or getattr(getattr(adapter, "cfg", None), "default_model", None) or "default"
        for attempt in range(MAX_REPAIRS + 1):
            req = GenerationRequest(messages=messages, model=model, temperature=0.1, max_tokens=900, tools=tools, timeout_s=240.0, run_id=f"agent-{task.id}-{uuid.uuid4().hex[:6]}")
            text = ""
            calls: list = []
            t_llm = time.monotonic()
            count_call()
            async with self.scheduler.agent():
                try:
                    async for ev in adapter.generate(req):
                        if is_cancelled():
                            try:
                                await adapter.cancel()
                            except Exception:  # noqa: BLE001
                                pass
                            return None
                        if ev.kind == "text_delta":
                            text += ev.text
                        elif ev.kind == "tool_call" and ev.tool_call is not None:
                            calls.append(ev.tool_call)  # complete calls only (adapters emit at the end)
                        elif ev.kind == "error":
                            err = ev.error or GenerationError("server_error", "Unbekannter Anbieterfehler")
                            raise PlannerError(err.code, err.message_de)
                except GenerationError as exc:
                    raise PlannerError(exc.code, exc.message_de)
            self._timing(task.id, "planning_ms", int((time.monotonic() - t_llm) * 1000))
            if calls:
                tc = calls[0]
                if len(calls) > 1:
                    logger.info("planner: %d tool calls in one turn; only the first is executed, the rest are answered as not executed", len(calls))
                try:
                    args = json.loads(tc.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be an object")
                except ValueError as exc:
                    messages.append({"role": "assistant", "content": text[:500] or None, "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}]})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": f"Argumente waren kein gültiges JSON ({exc}); bitte den Aufruf vollständig wiederholen."})
                    continue
                name = from_native_name(tc.name, known)
                assistant = {"role": "assistant", "content": text[:500] or None, "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}] if False else [{"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}]}
                history.append(assistant)
                for extra in calls[1:]:
                    history.append({"role": "tool", "tool_call_id": extra.id, "content": "Nicht ausgeführt: nur ein Werkzeugaufruf je Schritt."})
                del history[:-NATIVE_HISTORY]
                if name == "ask_user":
                    pending[tc.id] = name
                    return {"call_id": tc.id, "thought": text[:400], "decision": "ask_user", "ask_user": {"text": str(args.get("text") or ""), "kind": args.get("kind") or "input"}}
                if name == "done":
                    pending[tc.id] = name
                    return {"call_id": tc.id, "thought": text[:400], "decision": "done", "done": {"ok": bool(args.get("ok")), "summary": str(args.get("summary") or ""), "verified_by": str(args.get("verified_by") or "")}}
                pending[tc.id] = name
                return {"call_id": tc.id, "thought": text[:400], "decision": "action", "action": {"tool": name, "arguments": args}, "expected_effect": ""}
            # text only: not a decision
            logger.warning("planner: provider answered without a tool call (attempt %s): %s", attempt, text[:160])
            messages.append({"role": "assistant", "content": text[:1500]})
            messages.append({"role": "user", "content": "Bitte entscheide mit genau einem Werkzeugaufruf (ein Werkzeug, ask_user oder done). Reiner Text ist keine Entscheidung."})
        return None

    def answer_pending(self, task_id: str, call_id: Optional[str], text: str) -> None:
        """ask_user/done calls get their 'result' so the next native turn is well-formed."""
        self.record_tool_result(task_id, call_id, text)


def _parse_decision(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("leere Antwort")
    decision = json.loads(raw)
    if not isinstance(decision, dict) or decision.get("decision") not in ("action", "ask_user", "done"):
        raise ValueError("decision fehlt")
    if decision["decision"] == "action" and not isinstance(decision.get("action"), dict):
        raise ValueError("action fehlt")
    return decision
