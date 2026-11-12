"""WorkflowStore: persistence of verified workflows, their runs and the
candidate -> verified -> demoted life cycle; migration of 0.3 recipes.

Promotion rule (0.5): a candidate becomes ``verified`` only after at least
two successful runs with two distinct parameter sets *and* a recorded
negative check (the gate refused reuse when dependencies were made invalid).
A failed reuse run demotes the workflow (``demoted``): it is still visible,
can be inspected and re-compiled from a new successful task, but it is never
reused automatically again. Learned workflows never widen rights: the scope
stored with them is only informational, the task's own scopes decide.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from ..db import Database
from ..store.common import utcnow
from .contracts import Dependency, StepCheck, SuccessCriterion, VerifiedWorkflow, WorkflowBinding, WorkflowParameter, WorkflowStep, params_hash
from .deps import DependencyStore
from .models import Recipe, Scopes

PROMOTE_MIN_RUNS = 2
PROMOTE_MIN_PARAM_SETS = 2


class WorkflowStore:
    def __init__(self, db: Database, deps: DependencyStore, host_id: str):
        self.db = db
        self.deps = deps
        self.host_id = host_id

    # ---- CRUD -----------------------------------------------------------------------------
    def save(self, wf: VerifiedWorkflow, *, origin_kind: str = "compiled", origin_task_id: Optional[str] = None) -> VerifiedWorkflow:
        now = utcnow()
        wf.updated_at = now
        if not wf.created_at:
            wf.created_at = now
        self.db.execute(
            "INSERT INTO agent_workflows(id, version, name, status, workflow_json, origin_task_id, origin_kind, created_at, updated_at, last_verified_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET version = excluded.version, name = excluded.name, status = excluded.status, workflow_json = excluded.workflow_json, updated_at = excluded.updated_at, last_verified_at = excluded.last_verified_at",
            (wf.id, wf.version, wf.name, wf.status, wf.model_dump_json(), origin_task_id or wf.origin.get("task_id"), origin_kind, wf.created_at, now, wf.last_verified_at),
        )
        wf.dependencies = self.deps.replace(wf.id, wf.dependencies)
        self.db.execute("UPDATE agent_workflows SET workflow_json = ? WHERE id = ?", (wf.model_dump_json(), wf.id))
        return wf

    def get(self, workflow_id: str) -> VerifiedWorkflow:
        row = self.db.query_one("SELECT * FROM agent_workflows WHERE id = ?", (workflow_id,))
        if row is None:
            raise KeyError(workflow_id)
        return self._row(row)

    def _row(self, row) -> VerifiedWorkflow:
        wf = VerifiedWorkflow.model_validate_json(row["workflow_json"])
        wf.status = row["status"]
        wf.stats = {
            "success_runs": row["success_runs"], "failed_runs": row["failed_runs"], "distinct_param_sets": row["distinct_param_sets"],
            "negative_checked": bool(row["negative_checked"]), "last_verified_at": row["last_verified_at"], "last_failed_at": row["last_failed_at"],
            "origin_kind": row["origin_kind"], "dependencies": self.deps.summary(wf.id),
        }
        return wf

    def list(self, *, include_retired: bool = False) -> list[VerifiedWorkflow]:
        sql = "SELECT * FROM agent_workflows" + ("" if include_retired else " WHERE status <> 'retired'") + " ORDER BY updated_at DESC"
        return [self._row(r) for r in self.db.query(sql)]

    def status_of(self, workflow_id: str) -> Optional[str]:
        return self.db.scalar("SELECT status FROM agent_workflows WHERE id = ?", (workflow_id,))

    def set_status(self, workflow_id: str, status: str) -> VerifiedWorkflow:
        self.db.execute("UPDATE agent_workflows SET status = ?, updated_at = ? WHERE id = ?", (status, utcnow(), workflow_id))
        return self.get(workflow_id)

    def delete(self, workflow_id: str) -> None:
        self.db.execute("DELETE FROM agent_workflows WHERE id = ?", (workflow_id,))

    def update_fields(self, workflow_id: str, *, name: Optional[str] = None, parameters: Optional[list[dict]] = None, known_limits_de: Optional[list[str]] = None, steps: Optional[list[dict]] = None) -> VerifiedWorkflow:
        wf = self.get(workflow_id)
        if name:
            wf.name = name[:120]
        if parameters is not None:
            wf.parameters = [WorkflowParameter.model_validate(p) for p in parameters]
        if known_limits_de is not None:
            wf.known_limits_de = [str(x)[:300] for x in known_limits_de][:10]
        if steps is not None:
            # only argument templates and checks of existing steps may be edited; tools and side effects are fixed
            by_index = {int(s.get("index", -1)): s for s in steps if isinstance(s, dict)}
            for st in wf.steps:
                patch = by_index.get(st.index)
                if not patch:
                    continue
                if isinstance(patch.get("arguments_template"), dict):
                    st.arguments_template = patch["arguments_template"]
                if isinstance(patch.get("postconditions"), list):
                    st.postconditions = [StepCheck.model_validate(c) for c in patch["postconditions"]]
                if isinstance(patch.get("check_de"), str):
                    st.check_de = patch["check_de"][:160]
        wf.version += 1
        return self.save(wf, origin_kind=wf.stats.get("origin_kind", "compiled") if wf.stats else "compiled")

    # ---- runs ----------------------------------------------------------------------------
    def start_run(self, workflow_id: str, task_id: str, *, decision: str, reason: str, params: dict, gate_ms: int) -> str:
        rid = f"run_{uuid.uuid4().hex[:10]}"
        self.db.execute(
            "INSERT INTO agent_workflow_runs(id, workflow_id, task_id, host_id, decision, reason, params_hash, gate_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, workflow_id, task_id, self.host_id, decision, reason[:400], params_hash(params), gate_ms, utcnow()),
        )
        return rid

    def finish_run(self, run_id: str, *, outcome: str, llm_calls: int, reused_steps: int, repaired_steps: list[int], skipped_steps: list[int]) -> None:
        self.db.execute(
            "UPDATE agent_workflow_runs SET outcome = ?, llm_calls = ?, reused_steps = ?, repaired_steps = ?, skipped_steps = ?, finished_at = ? WHERE id = ?",
            (outcome, llm_calls, reused_steps, json.dumps(repaired_steps), json.dumps(skipped_steps), utcnow(), run_id),
        )

    def runs(self, workflow_id: str, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM agent_workflow_runs WHERE workflow_id = ? ORDER BY created_at DESC LIMIT ?", (workflow_id, limit))]

    def record_outcome(self, workflow_id: str, *, success: bool, params: dict, decision: str) -> VerifiedWorkflow:
        """Statistics + life cycle after a run that used the workflow (reuse or repair)."""
        now = utcnow()
        if success:
            distinct = self.db.scalar("SELECT count(DISTINCT params_hash) FROM agent_workflow_runs WHERE workflow_id = ? AND outcome = 'fulfilled'", (workflow_id,)) or 0
            self.db.execute("UPDATE agent_workflows SET success_runs = success_runs + 1, distinct_param_sets = ?, last_verified_at = ?, updated_at = ? WHERE id = ?", (int(distinct), now, now, workflow_id))
            self._maybe_promote(workflow_id)
        else:
            self.db.execute("UPDATE agent_workflows SET failed_runs = failed_runs + 1, last_failed_at = ?, updated_at = ? WHERE id = ?", (now, now, workflow_id))
            if decision in ("reuse", "repair"):
                self.db.execute("UPDATE agent_workflows SET status = 'demoted' WHERE id = ? AND status IN ('candidate','verified')", (workflow_id,))
        return self.get(workflow_id)

    def record_negative_check(self, workflow_id: str, ok: bool) -> VerifiedWorkflow:
        self.db.execute("UPDATE agent_workflows SET negative_checked = ?, updated_at = ? WHERE id = ?", (1 if ok else 0, utcnow(), workflow_id))
        if ok:
            self._maybe_promote(workflow_id)
        return self.get(workflow_id)

    def _maybe_promote(self, workflow_id: str) -> None:
        row = self.db.query_one("SELECT status, success_runs, distinct_param_sets, negative_checked FROM agent_workflows WHERE id = ?", (workflow_id,))
        if row and row["status"] == "candidate" and row["success_runs"] >= PROMOTE_MIN_RUNS and row["distinct_param_sets"] >= PROMOTE_MIN_PARAM_SETS and row["negative_checked"]:
            self.db.execute("UPDATE agent_workflows SET status = 'verified', updated_at = ? WHERE id = ?", (utcnow(), workflow_id))

    # ---- 0.3 recipes -> 0.5 workflows -------------------------------------------------------
    def migrate_recipes(self, recipes: list[Recipe]) -> int:
        """Idempotent: every 0.3 recipe becomes a candidate workflow with the same id (origin
        'migrated'); the recipe table is left untouched so an older core still works."""
        n = 0
        for r in recipes:
            if self.db.query_one("SELECT 1 FROM agent_workflows WHERE id = ?", (r.id,)):
                continue
            wf = recipe_to_workflow(r)
            self.save(wf, origin_kind="migrated", origin_task_id=(r.origin or {}).get("task_id"))
            n += 1
        return n

    def as_recipe(self, wf: VerifiedWorkflow) -> Recipe:
        """0.3 wire format for the compatibility endpoints."""
        from .models import RecipeStep

        return Recipe(
            id=wf.id, version=wf.version, name=wf.name, purpose_de=wf.purpose_de,
            parameters=[{"name": p.name, "description_de": p.description_de, "example": p.example} for p in wf.parameters],
            origin={**wf.origin, "status": wf.status}, requires={"tools": wf.requires_tools(), "apps": [b.hint for b in wf.bindings if b.kind == "app"], "vision": any(s.tool.startswith("vision.") for s in wf.steps)},
            preconditions=wf.preconditions, scope=wf.scope,
            steps=[RecipeStep(tool=s.tool, arguments_template=s.arguments_template, check=s.check_de) for s in wf.steps],
            final_checks=[c.description_de or c.kind for c in wf.checks], last_verified_at=wf.last_verified_at, known_limits_de=wf.known_limits_de,
        )


def recipe_to_workflow(r: Recipe) -> VerifiedWorkflow:
    steps = []
    for i, s in enumerate(r.steps):
        side = "local" if s.tool.split(".")[-1] in ("write", "click", "fill", "select", "press", "invoke", "set_value", "type_text", "send_keys", "click_at", "launch", "open", "back", "focus") else "none"
        steps.append(WorkflowStep(index=i, tool=s.tool, arguments_template=s.arguments_template, side_effect=side, check_de=s.check or ""))
    deps = [Dependency(kind="tool_contract", key=t, revision="", origin="declared") for t in sorted({s.tool for s in steps})]
    bindings: list[WorkflowBinding] = []
    sc: Scopes = r.scope
    if sc.files and sc.files.write_dir:
        bindings.append(WorkflowBinding(role="output_dir", kind="dir", hint="", required=False, original=sc.files.write_dir))
    return VerifiedWorkflow(
        id=r.id, version=r.version, name=r.name, purpose_de=r.purpose_de, status="candidate",
        parameters=[WorkflowParameter(name=str(p.get("name")), description_de=str(p.get("description_de") or ""), example=str(p.get("example") or "")) for p in r.parameters if p.get("name")],
        preconditions=list(r.preconditions), bindings=bindings, steps=steps,
        checks=[SuccessCriterion(kind="action_evidence", origin="workflow", description_de=c) for c in r.final_checks[:1]],
        dependencies=deps, scope=sc, origin={**(r.origin or {}), "kind": "migrated"},
        known_limits_de=list(r.known_limits_de) + ["Aus einem 0.3-Rezept übernommen: ohne Lese-/Schreibmengen und Faktenabhängigkeiten; wird streng geprüft."],
        created_at=utcnow(), updated_at=utcnow(), last_verified_at=r.last_verified_at,
    )


def workflow_api(wf: VerifiedWorkflow) -> dict[str, Any]:
    d = wf.model_dump()
    d["requires"] = {"tools": wf.requires_tools(), "apps": [b.hint for b in wf.bindings if b.kind == "app"], "vision": any(s.tool.startswith("vision.") for s in wf.steps)}
    d["derived_steps"] = [s.index for s in wf.steps if s.derive]
    d["external_effects"] = wf.has_external_effects()
    return d
