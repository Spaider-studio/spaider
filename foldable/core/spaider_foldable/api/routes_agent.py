"""Agent endpoints (contract v1.2 + v1.5): tasks, events, control, workflows,
dependencies, bindings, recipes (compatibility), capabilities, stop."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..agent.bindings import BindingResolver
from ..agent.compiler import templatize as _templatize  # noqa: F401  (0.3 tests import it from here)
from ..agent.contracts import SuccessCriterion
from ..agent.models import AnswerBody, BindingBody, CreateTaskBody, RunWorkflowBody, SaveRecipeBody, WorkflowPatchBody
from ..agent.reuse import ReuseGate
from ..agent.verifier import derive_criteria
from ..agent.workflows import workflow_api
from ..context import AppContext
from .deps import get_ctx
from .errors import ApiError

router = APIRouter(prefix="/agent")


def _task_or_404(ctx: AppContext, task_id: str):
    try:
        return ctx.tasks.get(task_id)
    except KeyError:
        raise ApiError(404, "not_found", "Aufgabe nicht gefunden.")


def _workflow_or_404(ctx: AppContext, workflow_id: str):
    try:
        return ctx.workflows.get(workflow_id)
    except KeyError:
        raise ApiError(404, "not_found", "Ablauf nicht gefunden.")


@router.get("/capabilities")
async def capabilities(ctx: AppContext = Depends(get_ctx)):
    return ctx.agent_capabilities()


@router.get("/tasks")
async def list_tasks(ctx: AppContext = Depends(get_ctx)):
    return {"tasks": [t.model_dump() for t in ctx.tasks.list()]}


def _check_connection_policy(ctx: AppContext, connection_id: str | None, project_id: str | None) -> None:
    """A planner through a provider connection needs: the connection, the project not local-only,
    an existing sharing consent. Same rule as chat; background jobs are no exception."""
    if not connection_id:
        return
    conn = ctx.connections.get(connection_id)
    if conn is None or not conn.enabled:
        raise ApiError(404, "not_found", "Verbindung nicht gefunden oder deaktiviert.")
    project = ctx.projects.get_or_default(project_id)
    if conn.execution != "local":
        if project.get("local_only"):
            raise ApiError(403, "local_only", f"Projekt „{project.get('name')}“ ist „Nur lokal“: keine Aufgabenplanung über eine Cloud-Verbindung.")
        if ctx.connections.consent(conn.id, project["id"]) is None:
            raise ApiError(409, "consent_required", "Für diese Verbindung und dieses Projekt liegt keine Freigabe vor (Einstellungen → Verbindungen).")


@router.post("/tasks", status_code=201)
async def create_task(body: CreateTaskBody, ctx: AppContext = Depends(get_ctx)):
    if ctx.inference.chat.model_id is None and not body.connection_id:
        raise ApiError(409, "no_model", "Es ist kein Chatmodell aktiv.")
    _check_connection_policy(ctx, body.connection_id, body.project_id)
    if body.conversation_id:
        ctx.conversations.get(body.conversation_id)
    if body.live_session_id:
        try:
            sess = ctx.live.get(body.live_session_id)
            if body.scopes.windows is None:
                from ..agent.models import WindowsScope

                body.scopes.windows = WindowsScope(source_ids=[sess.source_id], allowed_processes=[])
            elif sess.source_id not in body.scopes.windows.source_ids:
                body.scopes.windows.source_ids.append(sess.source_id)
        except Exception:
            raise ApiError(404, "not_found", "Live-Sitzung nicht gefunden.")
    prefs = ctx.settings.load()
    budget = body.budget
    max_steps = budget.max_steps if budget else prefs.agent_max_steps
    max_seconds = budget.max_seconds if budget else prefs.agent_max_seconds
    workflow_id = body.workflow_id or body.recipe_id
    if workflow_id:
        try:
            ctx.workflows.get(workflow_id)
        except KeyError:
            try:
                ctx.tasks.get_recipe(workflow_id)
            except KeyError:
                raise ApiError(404, "not_found", "Ablauf nicht gefunden.")
    criteria = [SuccessCriterion.model_validate(c).model_dump() for c in (body.success_criteria or [])] or [c.model_dump() for c in derive_criteria(body.goal, body.scopes, body.parameters)]
    task = ctx.tasks.create(
        goal=body.goal,
        scopes=body.scopes,
        max_steps=max_steps,
        max_seconds=max_seconds,
        conversation_id=body.conversation_id,
        live_session_id=body.live_session_id,
        recipe_id=body.recipe_id,
        parameters=body.parameters,
        workflow_id=workflow_id,
        project_id=body.project_id,
        connection_id=body.connection_id,
        success_criteria=criteria,
    )
    ctx.agent.start(task)
    ctx.scheduler.touch_user_activity()
    return {"task": task.model_dump()}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, ctx: AppContext = Depends(get_ctx)):
    task = _task_or_404(ctx, task_id)
    return {"task": task.model_dump(), "actions": [a.model_dump() for a in ctx.tasks.actions(task_id)]}


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, ctx: AppContext = Depends(get_ctx)):
    task = _task_or_404(ctx, task_id)
    events = ctx.agent.events(task_id)

    async def gen():
        q = events.subscribe()
        try:
            yield f"event: task\ndata: {json.dumps(task.model_dump(), ensure_ascii=False)}\n\n"
            for a in ctx.tasks.actions(task_id):
                yield f"event: action\ndata: {json.dumps(a.model_dump(), ensure_ascii=False)}\n\n"
            for kind, payload in events.history:
                if kind == "step":
                    yield f"event: step\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if task.state in ("completed", "failed", "cancelled", "interrupted"):
                yield f"event: done\ndata: {json.dumps(task.model_dump(), ensure_ascii=False)}\n\n"
                return
            while not ctx.shutting_down:
                try:
                    kind, payload = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if kind == "done":
                    return
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: str, ctx: AppContext = Depends(get_ctx)):
    _task_or_404(ctx, task_id)
    return {"task": ctx.agent.cancel(task_id).model_dump()}


@router.post("/tasks/{task_id}/pause")
async def pause(task_id: str, ctx: AppContext = Depends(get_ctx)):
    _task_or_404(ctx, task_id)
    return {"task": ctx.agent.pause(task_id).model_dump()}


@router.post("/tasks/{task_id}/resume")
async def resume(task_id: str, ctx: AppContext = Depends(get_ctx)):
    _task_or_404(ctx, task_id)
    try:
        return {"task": ctx.agent.resume(task_id).model_dump()}
    except ValueError as exc:
        raise ApiError(409, "not_resumable", str(exc))


@router.post("/tasks/{task_id}/answer")
async def answer(task_id: str, body: AnswerBody, ctx: AppContext = Depends(get_ctx)):
    _task_or_404(ctx, task_id)
    try:
        return {"task": ctx.agent.answer(task_id, body.question_id, approve=body.approve, text=body.text).model_dump()}
    except ValueError as exc:
        raise ApiError(409, "no_question", str(exc))


# ---- workflows (0.5) ---------------------------------------------------------------------
def _compile(ctx: AppContext, task_id: str, body: SaveRecipeBody):
    task = _task_or_404(ctx, task_id)
    if task.state != "completed" or not (task.result and task.result.ok) or task.outcome == "not_fulfilled":
        raise ApiError(409, "not_completed", "Nur erfolgreich abgeschlossene Aufgaben mit erfülltem oder ungeklärtem Ergebnis können als Ablauf gespeichert werden.")
    actions = ctx.tasks.actions(task_id)
    criteria = [SuccessCriterion.model_validate(c) for c in (task.success_criteria or []) if c.get("kind") != "action_evidence"]
    sids = task.scopes.windows.source_ids if task.scopes.windows else []
    wf = ctx.workflow_compiler.compile(
        task,
        actions,
        name=body.name,
        parameters=body.parameters,
        action_data=ctx.agent.action_data(task_id),
        element_names={sid: ctx.agent.element_names(task_id) for sid in sids},
        window_hints=ctx.window_hints(sids),
        criteria=criteria,
    )
    return ctx.workflows.save(wf, origin_kind="compiled", origin_task_id=task_id)


@router.post("/tasks/{task_id}/compile", status_code=201)
async def compile_workflow(task_id: str, body: SaveRecipeBody, ctx: AppContext = Depends(get_ctx)):
    wf = _compile(ctx, task_id, body)
    return {"workflow": workflow_api(wf)}


@router.post("/tasks/{task_id}/save-recipe", status_code=201)
async def save_recipe(task_id: str, body: SaveRecipeBody, ctx: AppContext = Depends(get_ctx)):
    """0.3 compatibility: compiles a workflow and returns it in the recipe shape."""
    wf = _compile(ctx, task_id, body)
    return {"recipe": ctx.workflows.as_recipe(wf).model_dump(), "workflow": workflow_api(wf)}


@router.get("/workflows")
async def list_workflows(ctx: AppContext = Depends(get_ctx)):
    return {"workflows": [workflow_api(w) for w in ctx.workflows.list()]}


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, ctx: AppContext = Depends(get_ctx)):
    wf = _workflow_or_404(ctx, workflow_id)
    return {"workflow": workflow_api(wf), "dependencies": [d.model_dump() for d in ctx.deps.for_workflow(workflow_id)], "runs": ctx.workflows.runs(workflow_id), "bindings": ctx.binding_resolver.stored()}


@router.patch("/workflows/{workflow_id}")
async def patch_workflow(workflow_id: str, body: WorkflowPatchBody, ctx: AppContext = Depends(get_ctx)):
    _workflow_or_404(ctx, workflow_id)
    wf = ctx.workflows.update_fields(workflow_id, name=body.name, parameters=body.parameters, known_limits_de=body.known_limits_de, steps=body.steps)
    if body.status:
        wf = ctx.workflows.set_status(workflow_id, body.status)
    return {"workflow": workflow_api(wf)}


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str, ctx: AppContext = Depends(get_ctx)):
    ctx.workflows.delete(workflow_id)
    ctx.tasks.delete_recipe(workflow_id)
    return {"ok": True}


@router.get("/workflows/{workflow_id}/check")
async def check_workflow(workflow_id: str, ctx: AppContext = Depends(get_ctx)):
    """Dry run of the gate on this host: dependencies right now, bindings, decision."""
    wf = _workflow_or_404(ctx, workflow_id)
    t0 = time.monotonic()
    bound, missing = ctx.binding_resolver.resolve(wf, wf.scope, windows=ctx.list_windows())
    deps_now = ctx.check_dependencies(wf, bound)
    plan = ReuseGate().decide(wf, params={p.name: p.example or "" for p in wf.parameters}, deps_now=deps_now, bindings=bound, missing_bindings=missing, available_tools={t.name for t in ctx.broker.all_tools()})
    plan.gate_ms = int((time.monotonic() - t0) * 1000)
    return {"plan": plan.model_dump(), "dependencies": [d.model_dump() for d in deps_now], "bindings": bound, "missing_bindings": [b.model_dump() for b in missing]}


@router.post("/workflows/{workflow_id}/negative-check")
async def negative_check(workflow_id: str, ctx: AppContext = Depends(get_ctx)):
    wf = _workflow_or_404(ctx, workflow_id)
    res = ReuseGate.negative_check(wf)
    wf = ctx.workflows.record_negative_check(workflow_id, bool(res["ok"]))
    return {"result": res, "workflow": workflow_api(wf)}


@router.post("/workflows/{workflow_id}/run", status_code=201)
async def run_workflow(workflow_id: str, body: RunWorkflowBody, ctx: AppContext = Depends(get_ctx)):
    wf = _workflow_or_404(ctx, workflow_id)
    if ctx.inference.chat.model_id is None and not body.connection_id:
        raise ApiError(409, "no_model", "Es ist kein Chatmodell aktiv.")
    _check_connection_policy(ctx, body.connection_id, body.project_id)
    missing = [p.name for p in wf.parameters if p.required and body.parameters.get(p.name) in (None, "")]
    if missing:
        raise ApiError(422, "parameters_missing", f"Parameter fehlen: {', '.join(missing)}")
    scopes = body.scopes or wf.scope
    prefs = ctx.settings.load()
    bound, _missing = ctx.binding_resolver.resolve(wf, scopes, windows=ctx.list_windows())
    goal = BindingResolver.fill(wf.purpose_de or f"Ablauf „{wf.name}“ ausführen", bound, body.parameters)
    criteria = [c.model_copy(update={"target": BindingResolver.fill(c.target, bound, body.parameters), "expect": [BindingResolver.fill(e, bound, body.parameters) for e in c.expect]}).model_dump() for c in wf.checks] + [c.model_dump() for c in derive_criteria(goal, scopes, body.parameters) if c.kind != "action_evidence"]
    task = ctx.tasks.create(
        goal=goal,
        scopes=scopes,
        max_steps=body.budget.max_steps if body.budget else prefs.agent_max_steps,
        max_seconds=body.budget.max_seconds if body.budget else prefs.agent_max_seconds,
        conversation_id=None,
        live_session_id=None,
        recipe_id=None,
        parameters=body.parameters,
        workflow_id=wf.id,
        project_id=body.project_id,
        connection_id=body.connection_id,
        success_criteria=criteria or None,
    )
    if body.mode in ("replan", "simple"):
        ctx.tasks.update(task.id, reuse_json=json.dumps({"mode": f"{body.mode}_forced", "workflow_id": wf.id, "workflow_name": wf.name}, ensure_ascii=False))
        task = ctx.tasks.get(task.id)
    ctx.agent.start(task)
    ctx.scheduler.touch_user_activity()
    return {"task": task.model_dump()}


# ---- bindings --------------------------------------------------------------------------------
@router.get("/bindings")
async def list_bindings(ctx: AppContext = Depends(get_ctx)):
    return {"host_id": ctx.binding_resolver.host_id, "bindings": list(ctx.binding_resolver.stored().values())}


@router.put("/bindings")
async def put_binding(body: BindingBody, ctx: AppContext = Depends(get_ctx)):
    return {"binding": ctx.binding_resolver.remember(body.role, body.kind, body.target)}


@router.delete("/bindings/{role}")
async def delete_binding(role: str, ctx: AppContext = Depends(get_ctx)):
    ctx.binding_resolver.forget(role)
    return {"ok": True}


# ---- recipes (0.3 compatibility: workflows in recipe shape) ---------------------------------------
@router.get("/recipes")
async def list_recipes(ctx: AppContext = Depends(get_ctx)):
    return {"recipes": [ctx.workflows.as_recipe(w).model_dump() for w in ctx.workflows.list()]}


@router.get("/recipes/{recipe_id}")
async def get_recipe(recipe_id: str, ctx: AppContext = Depends(get_ctx)):
    try:
        return {"recipe": ctx.workflows.as_recipe(ctx.workflows.get(recipe_id)).model_dump()}
    except KeyError:
        try:
            return {"recipe": ctx.tasks.get_recipe(recipe_id).model_dump()}
        except KeyError:
            raise ApiError(404, "not_found", "Ablauf nicht gefunden.")


@router.delete("/recipes/{recipe_id}")
async def delete_recipe(recipe_id: str, ctx: AppContext = Depends(get_ctx)):
    ctx.workflows.delete(recipe_id)
    ctx.tasks.delete_recipe(recipe_id)
    return {"ok": True}


@router.get("/browser")
async def browser_status(ctx: AppContext = Depends(get_ctx)):
    from ..agent import browserpkg

    st = ctx.browser.status() if ctx.browser is not None else {"installed": False}
    st["package"] = {k: v for k, v in browserpkg.BROWSER_PACKAGE.items() if k != "urls"}
    return st


@router.post("/browser/install", status_code=202)
async def browser_install(ctx: AppContext = Depends(get_ctx)):
    if ctx.browser is not None and ctx.browser.status().get("installed"):
        return {"job": None, "installed": True}
    job = await ctx.jobs.start_browser_install()
    return {"job": job}


@router.post("/stop")
async def stop_all(ctx: AppContext = Depends(get_ctx)):
    """Global stop, independent of the model: lock follow-up actions (generation), release held
    keys, end live capture, stop chat generations."""
    t0 = time.perf_counter()
    stopped_tasks = ctx.agent.cancel_all()
    released = ctx.input_guard.release_held_keys() if ctx.input_guard is not None else 0
    stopped_live = 0
    for s in list(ctx.live.sessions.values()):
        if await ctx.chat.stop(s.conversation_id):
            stopped_live += 1
    ended = ctx.live.end_all()
    teach_stopped = 0
    if getattr(ctx, "teach", None) is not None:
        teach_stopped = await ctx.teach.stop_all()
    ctx.bus.publish("stop", {"source": "api", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return {"stopped_tasks": stopped_tasks, "stopped_live": stopped_live, "ended_sessions": ended, "released_keys": released, "stopped_teach": teach_stopped, "lock_ms": round((time.perf_counter() - t0) * 1000, 1)}
