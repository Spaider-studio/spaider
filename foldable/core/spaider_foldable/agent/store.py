"""SQLite persistence for tasks, the action journal and recipes."""

from __future__ import annotations

import json
import uuid
from typing import Optional

from ..db import Database
from ..store.common import utcnow
from .models import Action, ActionResult, Recipe, Scopes, Task, TERMINAL_STATES


class TaskStore:
    def __init__(self, db: Database, host_id: str):
        self.db = db
        self.host_id = host_id

    # ---- tasks -------------------------------------------------------------------
    def create(self, *, goal: str, scopes: Scopes, max_steps: int, max_seconds: int, conversation_id: Optional[str], live_session_id: Optional[str], recipe_id: Optional[str], parameters: Optional[dict]) -> Task:
        now = utcnow()
        tid = f"task_{uuid.uuid4().hex[:12]}"
        self.db.execute(
            "INSERT INTO agent_tasks(id, goal, state, step, max_steps, max_seconds, scopes_json, conversation_id, live_session_id, "
            "recipe_id, parameters_json, summary_de, host_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, goal, "created", 0, max_steps, max_seconds, scopes.model_dump_json(), conversation_id, live_session_id, recipe_id, json.dumps(parameters) if parameters else None, "", self.host_id, now, now),
        )
        return self.get(tid)

    def get(self, task_id: str) -> Task:
        row = self.db.query_one("SELECT * FROM agent_tasks WHERE id = ?", (task_id,))
        if row is None:
            raise KeyError(task_id)
        return self._row_to_task(row)

    def list(self, limit: int = 100) -> list[Task]:
        rows = self.db.query("SELECT * FROM agent_tasks ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row_to_task(r) for r in rows]

    def update(self, task_id: str, **fields) -> Task:
        allowed = {"state", "step", "summary_de", "result_json", "pending_question_json", "last_error", "llm_calls", "started_at", "finished_at", "cancel_generation", "scopes_json"}
        cols = {k: v for k, v in fields.items() if k in allowed}
        cols["updated_at"] = utcnow()
        sets = ", ".join(f"{k} = ?" for k in cols)
        self.db.execute(f"UPDATE agent_tasks SET {sets} WHERE id = ?", (*cols.values(), task_id))
        return self.get(task_id)

    def bump_cancel_generation(self, task_id: str) -> int:
        self.db.execute("UPDATE agent_tasks SET cancel_generation = cancel_generation + 1, updated_at = ? WHERE id = ?", (utcnow(), task_id))
        return int(self.db.scalar("SELECT cancel_generation FROM agent_tasks WHERE id = ?", (task_id,)) or 0)

    def cancel_generation(self, task_id: str) -> int:
        return int(self.db.scalar("SELECT cancel_generation FROM agent_tasks WHERE id = ?", (task_id,)) or 0)

    def mark_interrupted_after_crash(self) -> list[str]:
        """Tasks that were running when the process died (or on another host) become 'interrupted'."""
        rows = self.db.query("SELECT id, host_id, state FROM agent_tasks WHERE state NOT IN ('completed','failed','cancelled','interrupted')")
        out = []
        for r in rows:
            self.db.execute(
                "UPDATE agent_tasks SET state = 'interrupted', last_error = ?, updated_at = ? WHERE id = ?",
                ("Unterbrochen durch Neustart" + (" auf einem anderen Rechner" if r["host_id"] != self.host_id else "") + "; Ausgang der letzten Aktion bitte prüfen.", utcnow(), r["id"]),
            )
            self.db.execute(
                "UPDATE agent_actions SET state = 'unknown_outcome', finished_at = ? WHERE task_id = ? AND state = 'executing'",
                (utcnow(), r["id"]),
            )
            out.append(r["id"])
        return out

    def _row_to_task(self, row) -> Task:
        return Task(
            id=row["id"],
            goal=row["goal"],
            state=row["state"],
            step=row["step"],
            max_steps=row["max_steps"],
            max_seconds=row["max_seconds"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            summary_de=row["summary_de"] or "",
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            scopes=Scopes.model_validate_json(row["scopes_json"]),
            pending_question=json.loads(row["pending_question_json"]) if row["pending_question_json"] else None,
            conversation_id=row["conversation_id"],
            live_session_id=row["live_session_id"],
            recipe_id=row["recipe_id"],
            parameters=json.loads(row["parameters_json"]) if row["parameters_json"] else None,
            llm_calls=row["llm_calls"],
            host_bound=row["host_id"] == self.host_id,
            last_error=row["last_error"],
        )

    # ---- actions -------------------------------------------------------------------
    def add_action(self, task_id: str, *, tool: str, arguments: dict, expected_effect: str, observation_ref: Optional[str], side_effect: str, state: str = "proposed", reason_de: Optional[str] = None) -> Action:
        seq = int(self.db.scalar("SELECT COALESCE(MAX(seq), 0) + 1 FROM agent_actions WHERE task_id = ?", (task_id,)) or 1)
        aid = f"act_{uuid.uuid4().hex[:12]}"
        now = utcnow()
        self.db.execute(
            "INSERT INTO agent_actions(action_id, task_id, seq, tool, arguments_json, state, reason_de, expected_effect, observation_ref, side_effect, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (aid, task_id, seq, tool, json.dumps(arguments, ensure_ascii=False), state, reason_de, expected_effect, observation_ref, side_effect, now),
        )
        return self.get_action(aid)

    def set_action_state(self, action_id: str, state: str, *, reason_de: Optional[str] = None, result: Optional[ActionResult] = None, started: bool = False, finished: bool = False, duration_ms: Optional[int] = None) -> Action:
        now = utcnow()
        sets = ["state = ?"]
        vals: list = [state]
        if reason_de is not None:
            sets.append("reason_de = ?")
            vals.append(reason_de)
        if result is not None:
            sets.append("result_json = ?")
            vals.append(result.model_dump_json())
        if started:
            sets.append("started_at = ?")
            vals.append(now)
        if finished:
            sets.append("finished_at = ?")
            vals.append(now)
        if duration_ms is not None:
            sets.append("duration_ms = ?")
            vals.append(int(duration_ms))
        vals.append(action_id)
        self.db.execute(f"UPDATE agent_actions SET {', '.join(sets)} WHERE action_id = ?", tuple(vals))
        return self.get_action(action_id)

    def get_action(self, action_id: str) -> Action:
        row = self.db.query_one("SELECT * FROM agent_actions WHERE action_id = ?", (action_id,))
        if row is None:
            raise KeyError(action_id)
        return self._row_to_action(row)

    def actions(self, task_id: str) -> list[Action]:
        return [self._row_to_action(r) for r in self.db.query("SELECT * FROM agent_actions WHERE task_id = ? ORDER BY seq", (task_id,))]

    def _row_to_action(self, row) -> Action:
        return Action(
            action_id=row["action_id"],
            task_id=row["task_id"],
            seq=row["seq"],
            tool=row["tool"],
            arguments=json.loads(row["arguments_json"] or "{}"),
            state=row["state"],
            reason_de=row["reason_de"],
            expected_effect=row["expected_effect"] or "",
            observation_ref=row["observation_ref"],
            side_effect=row["side_effect"] or "none",
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            duration_ms=row["duration_ms"],
            result=ActionResult.model_validate_json(row["result_json"]) if row["result_json"] else None,
        )

    # ---- recipes -------------------------------------------------------------------
    def save_recipe(self, recipe: Recipe) -> Recipe:
        now = utcnow()
        self.db.execute(
            "INSERT INTO agent_recipes(id, version, name, recipe_json, created_at, updated_at, last_verified_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET version = excluded.version, name = excluded.name, recipe_json = excluded.recipe_json, updated_at = excluded.updated_at, last_verified_at = excluded.last_verified_at",
            (recipe.id, recipe.version, recipe.name, recipe.model_dump_json(), now, now, recipe.last_verified_at),
        )
        return recipe

    def get_recipe(self, recipe_id: str) -> Recipe:
        row = self.db.query_one("SELECT recipe_json FROM agent_recipes WHERE id = ?", (recipe_id,))
        if row is None:
            raise KeyError(recipe_id)
        return Recipe.model_validate_json(row["recipe_json"])

    def list_recipes(self) -> list[Recipe]:
        return [Recipe.model_validate_json(r["recipe_json"]) for r in self.db.query("SELECT recipe_json FROM agent_recipes ORDER BY updated_at DESC")]

    def delete_recipe(self, recipe_id: str) -> None:
        self.db.execute("DELETE FROM agent_recipes WHERE id = ?", (recipe_id,))

    def is_terminal(self, state: str) -> bool:
        return state in TERMINAL_STATES
