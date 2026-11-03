"""Agent endpoints (contract v1.2): tasks, events, control, recipes, capabilities."""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..agent.models import AnswerBody, CreateTaskBody, Recipe, RecipeStep, SaveRecipeBody
from ..context import AppContext
from .deps import get_ctx
from .errors import ApiError

router = APIRouter(prefix="/agent")


def _task_or_404(ctx: AppContext, task_id: str):
    try:
        return ctx.tasks.get(task_id)
    except KeyError:
        raise ApiError(404, "not_found", "Aufgabe nicht gefunden.")


@router.get("/capabilities")
async def capabilities(ctx: AppContext = Depends(get_ctx)):
    return ctx.agent_capabilities()


@router.get("/tasks")
async def list_tasks(ctx: AppContext = Depends(get_ctx)):
    return {"tasks": [t.model_dump() for t in ctx.tasks.list()]}


@router.post("/tasks", status_code=201)
async def create_task(body: CreateTaskBody, ctx: AppContext = Depends(get_ctx)):
    if ctx.inference.chat.model_id is None:
        raise ApiError(409, "no_model", "Es ist kein Chatmodell aktiv.")
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
    if body.recipe_id:
        try:
            ctx.tasks.get_recipe(body.recipe_id)
        except KeyError:
            raise ApiError(404, "not_found", "Ablauf nicht gefunden.")
    task = ctx.tasks.create(
        goal=body.goal,
        scopes=body.scopes,
        max_steps=max_steps,
        max_seconds=max_seconds,
        conversation_id=body.conversation_id,
        live_session_id=body.live_session_id,
        recipe_id=body.recipe_id,
        parameters=body.parameters,
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


@router.post("/tasks/{task_id}/save-recipe", status_code=201)
async def save_recipe(task_id: str, body: SaveRecipeBody, ctx: AppContext = Depends(get_ctx)):
    task = _task_or_404(ctx, task_id)
    if task.state != "completed" or not (task.result and task.result.ok):
        raise ApiError(409, "not_completed", "Nur erfolgreich abgeschlossene Aufgaben können als Ablauf gespeichert werden.")
    actions = [a for a in ctx.tasks.actions(task_id) if a.state == "done"]
    tools_used = sorted({a.tool for a in actions})
    recipe = Recipe(
        id=f"rcp_{uuid.uuid4().hex[:10]}",
        version=1,
        name=body.name,
        purpose_de=task.goal[:300],
        parameters=body.parameters,
        origin={"task_id": task_id, "created_at": task.finished_at or task.updated_at, "host_bound": True},
        requires={"tools": tools_used, "apps": sorted({a.arguments.get("process") for a in actions if a.arguments.get("process")}), "vision": any(t.startswith("vision.") for t in tools_used)},
        preconditions=[f"Werkzeug verfügbar: {t}" for t in tools_used] + ([f"Ordner freigegeben: {r}" for r in (task.scopes.files.roots if task.scopes.files else [])]),
        scope=task.scopes,
        steps=[RecipeStep(tool=a.tool, arguments_template=a.arguments, check=(a.result.summary_de if a.result else "")) for a in actions],
        final_checks=[task.result.verified_by or "Ergebnis der letzten Aktion"],
        last_verified_at=task.finished_at,
        known_limits_de=["Aus genau einem belegten Lauf abgeleitet; bei geänderter Anwendung/Quelle wird neu geplant."],
    )
    ctx.tasks.save_recipe(recipe)
    return {"recipe": recipe.model_dump()}


@router.get("/recipes")
async def list_recipes(ctx: AppContext = Depends(get_ctx)):
    return {"recipes": [r.model_dump() for r in ctx.tasks.list_recipes()]}


@router.get("/recipes/{recipe_id}")
async def get_recipe(recipe_id: str, ctx: AppContext = Depends(get_ctx)):
    try:
        return {"recipe": ctx.tasks.get_recipe(recipe_id).model_dump()}
    except KeyError:
        raise ApiError(404, "not_found", "Ablauf nicht gefunden.")


@router.delete("/recipes/{recipe_id}")
async def delete_recipe(recipe_id: str, ctx: AppContext = Depends(get_ctx)):
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
    stopped_tasks = ctx.agent.cancel_all()
    stopped_live = 0
    for s in list(ctx.live.sessions.values()):
        if await ctx.chat.stop(s.conversation_id):
            stopped_live += 1
    ctx.bus.publish("stop", {"source": "api", "at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())})
    return {"stopped_tasks": stopped_tasks, "stopped_live": stopped_live}
