"""Agent runner: persistent state machine around one task.

created → observing → planning → validating → executing → verifying → … →
completed | failed | cancelled, plus paused / waiting_for_user / interrupted.

The model only proposes; the broker validates and executes; the runner owns
budgets, cancellation (generation counter), journaling and the step loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Optional

from ..activity import ActivityBus, ActivityTracker
from ..scheduler import LLMScheduler
from .broker import Broker, ToolContext, ToolOutcome
from .models import Action, ActionResult, Artifact, PendingQuestion, Scopes, Task, TaskResult
from .prompts import AGENT_SYSTEM_PROMPT, build_action_schema, step_user_message, tools_block
from .store import TaskStore

logger = logging.getLogger(__name__)

MAX_REPAIRS = 2
RECENT_ACTIONS = 8


class TaskEvents:
    """Per-task fan-out of SSE events (task/action/activity/token/question/done)."""

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []
        self.history: list[tuple[str, dict]] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._queues:
            self._queues.remove(q)

    def publish(self, kind: str, payload: dict) -> None:
        self.history.append((kind, payload))
        del self.history[:-300]
        for q in list(self._queues):
            try:
                q.put_nowait((kind, payload))
            except asyncio.QueueFull:
                pass


class AgentManager:
    def __init__(
        self,
        *,
        store: TaskStore,
        broker: Broker,
        scheduler: LLMScheduler,
        bus: ActivityBus,
        get_client: Callable[[], Any],
        observe_sources: Callable[[Task, dict], Any],  # async (task, latest_refs) -> (text, refs)
        settings_loader: Callable[[], Any],
        on_task_change: Optional[Callable[[Task], None]] = None,
        reasoning_toggle: Callable[[], Optional[str]] = lambda: None,
    ):
        self.reasoning_toggle = reasoning_toggle
        self.store = store
        self.broker = broker
        self.scheduler = scheduler
        self.bus = bus
        self.get_client = get_client
        self.observe_sources = observe_sources
        self.settings_loader = settings_loader
        self.on_task_change = on_task_change
        self._runners: dict[str, asyncio.Task] = {}
        self._events: dict[str, TaskEvents] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}
        self._pause_flags: dict[str, asyncio.Event] = {}
        self._answers: dict[str, asyncio.Future] = {}
        self._latest_refs: dict[str, dict[str, set[str]]] = {}
        self._latest_snapshot_ids: dict[str, dict[str, str]] = {}

    # ---- public API ------------------------------------------------------------------
    def events(self, task_id: str) -> TaskEvents:
        if task_id not in self._events:
            self._events[task_id] = TaskEvents()
        return self._events[task_id]

    def running(self) -> list[str]:
        return [tid for tid, t in self._runners.items() if not t.done()]

    def start(self, task: Task) -> Task:
        if task.id in self._runners and not self._runners[task.id].done():
            return task
        self._cancel_flags[task.id] = asyncio.Event()
        self._pause_flags[task.id] = asyncio.Event()
        self._runners[task.id] = asyncio.create_task(self._run(task.id), name=f"agent-{task.id}")
        return task

    def cancel(self, task_id: str) -> Task:
        """Immediate: bump the generation (no further side effects), flag, cancel the runner."""
        gen = self.store.bump_cancel_generation(task_id)
        flag = self._cancel_flags.get(task_id)
        if flag:
            flag.set()
        fut = self._answers.pop(task_id, None)
        if fut and not fut.done():
            fut.cancel()
        runner = self._runners.get(task_id)
        task = self.store.get(task_id)
        if task.state in ("completed", "failed", "cancelled", "interrupted"):
            return task
        if runner and not runner.done():
            runner.cancel()
        task = self._set_state(task_id, "cancelled", summary_de=f"Abgebrochen (Generation {gen}).", finished=True)
        return task

    def cancel_all(self) -> int:
        n = 0
        for tid in list(self._runners):
            t = self.store.get(tid)
            if t.state not in ("completed", "failed", "cancelled", "interrupted"):
                self.cancel(tid)
                n += 1
        return n

    def pause(self, task_id: str) -> Task:
        flag = self._pause_flags.get(task_id)
        if flag:
            flag.set()
        return self.store.get(task_id)

    def resume(self, task_id: str) -> Task:
        task = self.store.get(task_id)
        flag = self._pause_flags.get(task_id)
        if flag:
            flag.clear()
        if task.state in ("paused", "interrupted") and (task_id not in self._runners or self._runners[task_id].done()):
            if task.state == "interrupted" and not task.host_bound:
                raise ValueError("Die Aufgabe wurde auf einem anderen Rechner gestartet; bitte neu anlegen.")
            self._set_state(task_id, "observing", summary_de="Fortgesetzt; aktueller Zustand wird geprüft.")
            self.start(task)
        return self.store.get(task_id)

    def answer(self, task_id: str, question_id: str, *, approve: Optional[bool], text: Optional[str]) -> Task:
        task = self.store.get(task_id)
        pq = task.pending_question
        if not pq or pq.id != question_id:
            raise ValueError("Keine offene Frage mit dieser Id.")
        fut = self._answers.get(task_id)
        if fut is None or fut.done():
            raise ValueError("Die Aufgabe wartet gerade nicht auf eine Antwort.")
        fut.set_result({"approve": approve, "text": text})
        return task

    # ---- internals -------------------------------------------------------------------
    def _set_state(self, task_id: str, state: str, *, summary_de: Optional[str] = None, finished: bool = False, **fields) -> Task:
        upd: dict = {"state": state}
        if summary_de is not None:
            upd["summary_de"] = summary_de
        if finished:
            upd["finished_at"] = _now()
        upd.update(fields)
        task = self.store.update(task_id, **upd)
        self._publish_task(task)
        return task

    def _publish_task(self, task: Task) -> None:
        payload = task.model_dump()
        self.events(task.id).publish("task", payload)
        self.bus.publish("task", payload)
        if self.on_task_change:
            try:
                self.on_task_change(task)
            except Exception:  # noqa: BLE001
                pass
        if task.state in ("completed", "failed", "cancelled", "interrupted"):
            self.events(task.id).publish("done", payload)

    def _publish_action(self, action: Action) -> None:
        self.events(action.task_id).publish("action", action.model_dump())

    def _is_cancelled(self, task_id: str) -> bool:
        flag = self._cancel_flags.get(task_id)
        return bool(flag and flag.is_set())

    async def _wait_if_paused(self, task_id: str) -> None:
        flag = self._pause_flags.get(task_id)
        if flag and flag.is_set():
            self._set_state(task_id, "paused", summary_de="Pausiert.")
            while flag.is_set():
                if self._is_cancelled(task_id):
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.2)
            self._set_state(task_id, "observing", summary_de="Fortgesetzt.")

    async def _run(self, task_id: str) -> None:
        task = self.store.get(task_id)
        ev = self.events(task_id)
        tracker = ActivityTracker(task_id, self.bus, conversation_id=task.conversation_id, emit=lambda e: ev.publish("activity", _ev_dict(e)))
        t_start = time.monotonic()
        started_at = task.started_at or _now()
        self.store.update(task_id, started_at=started_at)
        repeated_failure: dict[str, int] = {}
        try:
            while True:
                await self._wait_if_paused(task_id)
                if self._is_cancelled(task_id):
                    raise asyncio.CancelledError()
                task = self.store.get(task_id)
                if task.step >= task.max_steps:
                    self._finish(task_id, ok=False, summary="Schrittbudget erschöpft, Ziel nicht belegt erreicht.", verified=False)
                    return
                if time.monotonic() - t_start > task.max_seconds:
                    self._finish(task_id, ok=False, summary="Zeitbudget erschöpft, Ziel nicht belegt erreicht.", verified=False)
                    return
                step = task.step + 1
                self.store.update(task_id, step=step)
                # ---- observing
                self._set_state(task_id, "observing", summary_de=f"Schritt {step}: Umgebung wird beobachtet.")
                tracker.start("observe", f"Schritt {step}: Beobachtung")
                try:
                    observations, refs, snapshot_ids = await self.observe_sources(task, self._latest_refs.get(task_id, {}))
                except Exception as exc:  # noqa: BLE001
                    observations, refs, snapshot_ids = f"Beobachtung fehlgeschlagen: {str(exc)[:200]}", {}, {}
                self._latest_refs[task_id] = refs
                self._latest_snapshot_ids[task_id] = snapshot_ids
                tracker.done("observe", "Beobachtung fertig", {"sources": len(snapshot_ids)})
                # ---- planning
                self._set_state(task_id, "planning", summary_de=f"Schritt {step}: nächster Schritt wird geplant.")
                tracker.start("planning", f"Schritt {step}: Planung")
                decision = await self._plan(task, observations, tracker)
                tracker.done("planning", "Entscheidung erhalten", {"llm_calls": self.store.get(task_id).llm_calls})
                if decision is None:
                    self._finish(task_id, ok=False, summary="Das Modell lieferte keine gültige Entscheidung (zwei Reparaturversuche).", verified=False)
                    return
                thought = (decision.get("thought") or "").strip()
                if thought:
                    ev.publish("token", {"text": thought})
                kind = decision.get("decision")
                if kind == "done":
                    d = decision.get("done") or {}
                    verified = self._verified_claim(task_id, d.get("verified_by") or "")
                    self._finish(task_id, ok=bool(d.get("ok")), summary=str(d.get("summary") or "")[:600], verified=verified, verified_by=d.get("verified_by"))
                    return
                if kind == "ask_user":
                    q = decision.get("ask_user") or {}
                    answer = await self._ask(task_id, text=str(q.get("text") or "Rückfrage")[:400], kind=q.get("kind") or "input", action=None)
                    if answer is None:
                        return
                    self.store.update(task_id, pending_question_json=None)
                    self._note(task_id, f"Antwort des Nutzers: {answer.get('text') or ('Freigabe erteilt' if answer.get('approve') else 'abgelehnt')}")
                    continue
                # ---- validating
                act = decision.get("action") or {}
                tool_name = str(act.get("tool") or "")
                arguments = act.get("arguments") if isinstance(act.get("arguments"), dict) else {}
                self._set_state(task_id, "validating", summary_de=f"Schritt {step}: {tool_name} wird geprüft.")
                tool, err = self.broker.validate(tool_name, arguments, task.scopes, fresh_refs=refs)
                action = self.store.add_action(
                    task_id,
                    tool=tool_name,
                    arguments=arguments,
                    expected_effect=str(decision.get("expected_effect") or "")[:200],
                    observation_ref=json.dumps(snapshot_ids) if snapshot_ids else None,
                    side_effect=tool.side_effect if tool else "none",
                )
                self._publish_action(action)
                if err or tool is None:
                    action = self.store.set_action_state(action.action_id, "rejected", reason_de=err, finished=True)
                    self._publish_action(action)
                    self._note(task_id, f"Aktion abgelehnt: {err}")
                    key = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"
                    repeated_failure[key] = repeated_failure.get(key, 0) + 1
                    if repeated_failure[key] >= 3:
                        self._finish(task_id, ok=False, summary=f"Dieselbe ungültige Aktion wurde dreimal vorgeschlagen ({tool_name}).", verified=False)
                        return
                    continue
                element_name = self._element_name(task_id, arguments)
                why = self.broker.requires_approval(tool, arguments, task.scopes, element_name)
                if why:
                    answer = await self._ask(task_id, text=f"Freigabe nötig: {tool_name} – {why} Ausführen?", kind="approval", action=action.model_dump())
                    if answer is None:
                        return
                    self.store.update(task_id, pending_question_json=None)
                    if not answer.get("approve"):
                        action = self.store.set_action_state(action.action_id, "rejected", reason_de="Vom Nutzer abgelehnt.", finished=True)
                        self._publish_action(action)
                        self._note(task_id, f"Der Nutzer hat {tool_name} abgelehnt.")
                        continue
                action = self.store.set_action_state(action.action_id, "validated")
                # ---- executing (intent persisted before the side effect)
                gen = self.store.cancel_generation(task_id)
                self._set_state(task_id, "executing", summary_de=f"Schritt {step}: {tool_name} läuft.")
                action = self.store.set_action_state(action.action_id, "executing", started=True)
                self._publish_action(action)
                tracker.start("executing", f"{tool_name}")
                ctx = ToolContext(
                    task_id=task_id,
                    scopes=task.scopes,
                    cancel_generation=gen,
                    is_cancelled=lambda: self._is_cancelled(task_id),
                    latest_snapshot_ids=snapshot_ids,
                    emit=lambda k, p: ev.publish(k, p),
                )
                t0 = time.monotonic()
                try:
                    outcome = await self.broker.execute(tool, arguments, ctx, generation_now=lambda: self.store.cancel_generation(task_id))
                except asyncio.CancelledError:
                    self.store.set_action_state(action.action_id, "unknown_outcome", reason_de="Abgebrochen während der Ausführung; Ausgang unklar.", finished=True)
                    raise
                duration = int((time.monotonic() - t0) * 1000)
                result = Broker.to_result(outcome)
                state = "done" if outcome.ok else ("cancelled" if outcome.error == "cancelled" else ("unknown_outcome" if outcome.error == "timeout" else "failed"))
                action = self.store.set_action_state(action.action_id, state, result=result, finished=True, duration_ms=duration)
                self._publish_action(action)
                tracker.done("executing", outcome.summary_de[:120], {"ok": outcome.ok, "duration_ms": duration})
                self._remember_data(task_id, action.action_id, outcome)
                if outcome.artifacts:
                    self._add_artifacts(task_id, outcome.artifacts)
                if self.store.cancel_generation(task_id) != gen or self._is_cancelled(task_id):
                    raise asyncio.CancelledError()
                # ---- verifying
                self._set_state(task_id, "verifying", summary_de=f"Schritt {step}: {outcome.summary_de[:120]}")
                key = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"
                if not outcome.ok:
                    repeated_failure[key] = repeated_failure.get(key, 0) + 1
                    if repeated_failure[key] >= 3:
                        self._finish(task_id, ok=False, summary=f"{tool_name} ist dreimal mit denselben Argumenten gescheitert: {outcome.summary_de[:200]}", verified=False)
                        return
        except asyncio.CancelledError:
            task = self.store.get(task_id)
            if task.state not in ("cancelled", "completed", "failed", "interrupted"):
                self._set_state(task_id, "cancelled", summary_de="Abgebrochen.", finished=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent task %s crashed", task_id)
            self._set_state(task_id, "failed", summary_de=f"Interner Fehler: {str(exc)[:200]}", finished=True, last_error=str(exc)[:500])
        finally:
            self._latest_refs.pop(task_id, None)
            self._latest_snapshot_ids.pop(task_id, None)

    # ---- helpers ---------------------------------------------------------------------
    def _finish(self, task_id: str, *, ok: bool, summary: str, verified: bool, verified_by: Optional[str] = None) -> None:
        task = self.store.get(task_id)
        prev = task.result.model_dump() if task.result else {}
        result = TaskResult(ok=ok, summary_de=summary, artifacts=[Artifact(**a) for a in (prev.get("artifacts") or [])], verified=verified, verified_by=verified_by)
        self._set_state(task_id, "completed" if ok else "failed", summary_de=summary, finished=True, result_json=result.model_dump_json())

    def _note(self, task_id: str, text: str) -> None:
        notes = self._notes.setdefault(task_id, []) if hasattr(self, "_notes") else None
        if notes is None:
            self._notes = {task_id: [text]}
        else:
            notes.append(text)
            del notes[:-4]

    def _remember_data(self, task_id: str, action_id: str, outcome: ToolOutcome) -> None:
        data = getattr(self, "_data", None)
        if data is None:
            self._data = {}
            data = self._data
        data.setdefault(task_id, {})[action_id] = outcome.data

    def _add_artifacts(self, task_id: str, artifacts: list[dict]) -> None:
        task = self.store.get(task_id)
        prev = task.result.model_dump() if task.result else {"ok": False, "summary_de": "", "artifacts": [], "verified": False}
        prev["artifacts"] = (prev.get("artifacts") or []) + artifacts
        self.store.update(task_id, result_json=json.dumps(prev, ensure_ascii=False))

    def _verified_claim(self, task_id: str, verified_by: str) -> bool:
        """A completion counts as verified only when the last successful action was itself verified."""
        acts = self.store.actions(task_id)
        done = [a for a in acts if a.state == "done" and a.result]
        if not done:
            return False
        return bool(done[-1].result.verified)

    def _element_name(self, task_id: str, arguments: dict) -> Optional[str]:
        names = getattr(self, "_element_names", {}).get(task_id, {})
        ref = arguments.get("element_id") or arguments.get("ref")
        return names.get(ref) if ref else None

    def remember_element_names(self, task_id: str, mapping: dict[str, str]) -> None:
        if not hasattr(self, "_element_names"):
            self._element_names = {}
        self._element_names[task_id] = mapping

    async def _ask(self, task_id: str, *, text: str, kind: str, action: Optional[dict]) -> Optional[dict]:
        qid = f"q_{uuid.uuid4().hex[:8]}"
        pq = PendingQuestion(id=qid, text_de=text, kind=kind, action=action)
        # the future exists before the state becomes visible, so an answer can never be lost
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._answers[task_id] = fut
        self._set_state(task_id, "waiting_for_user", summary_de=text[:160], pending_question_json=pq.model_dump_json())
        self.events(task_id).publish("question", pq.model_dump())
        try:
            return await fut
        except asyncio.CancelledError:
            return None
        finally:
            self._answers.pop(task_id, None)

    async def _plan(self, task: Task, observations: str, tracker: ActivityTracker) -> Optional[dict]:
        tools = self.broker.available_tools(task.scopes)
        tool_infos = [{"name": t.name, "description_de": t.description_de, "schema": t.schema} for t in tools]
        schema = build_action_schema(tool_infos)
        system = AGENT_SYSTEM_PROMPT + "\n\n" + tools_block(tool_infos)
        acts = self.store.actions(task.id)[-RECENT_ACTIONS:]
        recent = []
        data = getattr(self, "_data", {}).get(task.id, {})
        for a in acts:
            d = a.model_dump()
            if a.result:
                d["result"] = a.result.model_dump()
                if a.action_id in data:
                    d["result"]["data"] = data[a.action_id]
            recent.append(d)
        notes = list(getattr(self, "_notes", {}).get(task.id, []))
        user = step_user_message(goal=task.goal, parameters=task.parameters, step=task.step, max_steps=task.max_steps, recent_actions=recent, observations=observations, notes=notes)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        toggle = self.reasoning_toggle()
        for attempt in range(MAX_REPAIRS + 1):
            client = self.get_client()
            if client is None:
                raise RuntimeError("Kein Chatmodell bereit.")
            text = ""
            async with self.scheduler.chat():
                self.store.update(task.id, llm_calls=self.store.get(task.id).llm_calls + 1)
                async for chunk in client.stream_chat(
                    messages,
                    temperature=0.1,
                    max_tokens=700,
                    enable_thinking=(False if toggle else None),
                    reasoning_toggle=toggle,
                    json_schema=schema,
                    timeout_s=240.0,
                ):
                    if self._is_cancelled(task.id):
                        return None
                    if chunk.text:
                        text += chunk.text
            try:
                decision = json.loads(text.strip())
                if not isinstance(decision, dict) or decision.get("decision") not in ("action", "ask_user", "done"):
                    raise ValueError("decision fehlt")
                if decision["decision"] == "action" and not isinstance(decision.get("action"), dict):
                    raise ValueError("action fehlt")
                return decision
            except (ValueError, json.JSONDecodeError) as exc:
                logger.warning("agent decision invalid (attempt %s): %s | %s", attempt, exc, text[:200])
                messages.append({"role": "assistant", "content": text[:2000]})
                messages.append({"role": "user", "content": f"Die Antwort war kein gültiges Entscheidungs-JSON ({exc}). Antworte erneut nur mit dem JSON-Objekt."})
        return None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ev_dict(e) -> dict:
    return e.to_dict() if hasattr(e, "to_dict") else dict(e)
