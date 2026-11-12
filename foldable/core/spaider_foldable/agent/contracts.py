"""Typed contracts of the execution core (0.5, contract v1.5).

The five records every model path (grammar JSON of the local runtime, native
tool calls of a provider connection, a stored workflow, a taught
demonstration) is translated into::

    TaskSpec          what the user asked for: goal, profile/project, resources
                      (scopes), approvals, budget, success criteria
    Observation       what the core saw before planning: host/session, window or
                      tab, time, state version, element ids, image reference
    ActionProposal    one call the planner (model, workflow cursor or repair
                      step) wants to make; carries the call id and the rights
                      it needs - the broker decides, never the proposer
    ActionReceipt     what actually happened: evidence, errors, side effects
    VerifiedWorkflow  a compiled, parametrised, declarative plan with read/write
                      sets, pre-/postconditions, bindings, fact dependencies and
                      a version - no executable code

The existing API models (``Task``, ``Action``, ``Recipe``) stay the wire
format of the 0.3 endpoints; these contracts are the internal, typed layer
that the loop, compiler, gate, resolver and verifier exchange.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .models import Budget, Scopes

CriterionKind = Literal[
    "file_exists",  # target path exists and is not empty
    "file_contains",  # target path contains every token of ``expect``
    "file_not_contains",  # target path contains none of ``expect``
    "page_text",  # the own browser page shows every token of ``expect``
    "window_text",  # a window (target = source_id) shows every token of ``expect``
    "element_value",  # an element (target = "source_id|element name") carries the value
    "action_evidence",  # the last successful action of the task was itself verified
]
OutcomeStatus = Literal["fulfilled", "not_fulfilled", "unclear"]
DependencyKind = Literal["fact", "file", "binding", "app", "tool_contract", "model", "workflow"]
DependencyOrigin = Literal["observed", "declared", "inferred"]
BindingKind = Literal["dir", "file", "app", "window", "url", "account", "model"]


class SuccessCriterion(BaseModel):
    kind: CriterionKind
    target: str = ""
    expect: list[str] = Field(default_factory=list)
    origin: Literal["goal", "parameters", "workflow", "user", "planner"] = "goal"
    description_de: str = ""


class TaskSpec(BaseModel):
    goal: str
    profile_id: str = ""
    project_id: Optional[str] = None
    resources: Scopes
    approvals: dict[str, Any] = Field(default_factory=dict)  # {"external_effects": bool, "approved_tools": [...]}
    budget: Budget = Field(default_factory=Budget)
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    workflow_id: Optional[str] = None
    connection_id: Optional[str] = None  # planner through a provider connection (cloud) - needs consent


class Observation(BaseModel):
    host_id: str
    session_id: str  # task id or live session id
    source_id: str = ""  # window/screen id, "browser" or "" (files only)
    title: str = ""  # window title or tab url
    captured_at: str
    state_version: str  # hash of the observed structure; a different version = stale references
    element_ids: list[str] = Field(default_factory=list)
    element_names: dict[str, str] = Field(default_factory=dict)
    image_ref: Optional[str] = None  # snapshot id when an image was taken
    text_excerpt: str = ""


class ActionProposal(BaseModel):
    call_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    target: str = ""  # element name, path, url - for the approval heuristics and the journal
    expected_state: str = ""
    required_rights: list[str] = Field(default_factory=list)  # scope names + "external_effects"
    origin: Literal["planner", "workflow", "repair", "teach", "verifier"] = "planner"
    step_index: Optional[int] = None
    thought: str = ""


class ActionReceipt(BaseModel):
    call_id: str
    action_id: str
    tool: str
    arguments: dict[str, Any]
    ok: bool
    state: str
    evidence: Optional[dict[str, Any]] = None
    data: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    side_effect: Literal["none", "local", "external"] = "none"
    verified: bool = False
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: int = 0
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowParameter(BaseModel):
    name: str
    description_de: str = ""
    example: str = ""
    kind: Literal["text", "path", "number", "date", "choice"] = "text"
    required: bool = True


class WorkflowBinding(BaseModel):
    role: str  # e.g. "output_dir", "root_projekt", "app_notepad", "window_testapp"
    kind: BindingKind
    hint: str = ""  # basename / process name / title fragment used to re-bind on another PC
    required: bool = True
    original: str = ""  # target on the host the workflow was compiled on (informational)


class StepCheck(BaseModel):
    kind: CriterionKind
    target: str = ""
    expect: list[str] = Field(default_factory=list)
    description_de: str = ""


class WorkflowStep(BaseModel):
    index: int
    tool: str
    arguments_template: dict[str, Any] = Field(default_factory=dict)
    target: Optional[dict[str, str]] = None  # {"role": "Edit", "name": "Name"} for UI elements (resolved per fresh observation)
    read_set: list[str] = Field(default_factory=list)  # "{role}/rel/path", "fact:<id>", "page", "window:<role>"
    write_set: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)  # declarative: "exists:{root}/a.txt", "tool:files.read", "binding:output_dir"
    postconditions: list[StepCheck] = Field(default_factory=list)
    side_effect: Literal["none", "local", "external"] = "none"
    depends_on: list[str] = Field(default_factory=list)  # dependency keys ("fact:<id>", "file:{root}/x", "binding:output_dir")
    derive: bool = False  # the arguments were derived from data read earlier: needs one model call per run
    derive_from: list[int] = Field(default_factory=list)  # step indices whose results the derivation used
    check_de: str = ""


class Dependency(BaseModel):
    id: str = ""
    kind: DependencyKind
    key: str
    revision: str = ""
    origin: DependencyOrigin = "declared"
    scope: str = "profile"
    step_index: Optional[int] = None
    valid: bool = True
    reason: str = ""
    invalidated_by: Optional[str] = None
    invalidated_at: Optional[str] = None


class VerifiedWorkflow(BaseModel):
    id: str
    version: int = 1
    name: str
    purpose_de: str = ""
    status: Literal["candidate", "verified", "demoted", "retired"] = "candidate"
    parameters: list[WorkflowParameter] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    bindings: list[WorkflowBinding] = Field(default_factory=list)
    steps: list[WorkflowStep] = Field(default_factory=list)
    checks: list[SuccessCriterion] = Field(default_factory=list)  # final outcome checks
    dependencies: list[Dependency] = Field(default_factory=list)
    scope: Scopes = Field(default_factory=Scopes)
    origin: dict[str, Any] = Field(default_factory=dict)
    tool_contract_hash: str = ""
    model_hint: str = ""
    known_limits_de: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    last_verified_at: Optional[str] = None
    stats: dict[str, Any] = Field(default_factory=dict)

    def requires_tools(self) -> list[str]:
        return sorted({s.tool for s in self.steps})

    def has_external_effects(self) -> bool:
        return any(s.side_effect == "external" for s in self.steps)


class CheckResult(BaseModel):
    criterion: SuccessCriterion
    ok: Optional[bool]  # None = could not be checked
    detail_de: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class OutcomeReport(BaseModel):
    status: OutcomeStatus
    checks: list[CheckResult] = Field(default_factory=list)
    causes_de: list[str] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)
    verified_at: str = ""
    verified_by: str = ""  # "verifier" | "action_evidence" | ""


class ReusePlan(BaseModel):
    mode: Literal["reuse", "repair", "replan"]
    reasons_de: list[str] = Field(default_factory=list)
    repair_steps: list[int] = Field(default_factory=list)
    strict: bool = False  # verify every step's postconditions, re-observe before UI steps
    coverage: float = 0.0  # observed+declared dependencies / all dependencies (0..1)
    unknown_dependencies: bool = False
    invalid: list[Dependency] = Field(default_factory=list)
    bindings: dict[str, str] = Field(default_factory=dict)  # role -> target on this host
    missing_bindings: list[str] = Field(default_factory=list)
    gate_ms: int = 0


def tool_contract_hash(tools: list[dict]) -> str:
    """Stable hash of the tool names and argument schemas available to a task."""
    canon = json.dumps([{"name": t.get("name"), "schema": t.get("schema")} for t in sorted(tools, key=lambda t: t.get("name") or "")], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def params_hash(params: dict[str, Any] | None) -> str:
    return hashlib.sha256(json.dumps(params or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def state_version(structure_text: str) -> str:
    return hashlib.sha256((structure_text or "").encode("utf-8", errors="replace")).hexdigest()[:12]
