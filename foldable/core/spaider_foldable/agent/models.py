"""Pydantic models of the agent API (contract v1.2)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

TaskState = Literal[
    "created",
    "observing",
    "planning",
    "validating",
    "executing",
    "verifying",
    "completed",
    "failed",
    "cancelled",
    "paused",
    "waiting_for_user",
    "interrupted",
]
ActionState = Literal["proposed", "validated", "rejected", "executing", "done", "failed", "unknown_outcome", "cancelled"]
TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}


class FilesScope(BaseModel):
    roots: list[str] = Field(default_factory=list, max_length=20)
    write_dir: Optional[str] = None


class BrowserScope(BaseModel):
    enabled: bool = False
    allowed_origins: Optional[list[str]] = None  # None = any http(s) origin except private/loopback


class WindowsScope(BaseModel):
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    allowed_processes: list[str] = Field(default_factory=list, max_length=20)


class Scopes(BaseModel):
    files: Optional[FilesScope] = None
    browser: Optional[BrowserScope] = None
    windows: Optional[WindowsScope] = None
    memory: bool = True
    external_effects: bool = False


class Budget(BaseModel):
    max_steps: int = Field(default=20, ge=1, le=100)
    max_seconds: int = Field(default=600, ge=30, le=7200)


class CreateTaskBody(BaseModel):
    goal: str = Field(min_length=3, max_length=4000)
    conversation_id: Optional[str] = None
    live_session_id: Optional[str] = None
    recipe_id: Optional[str] = None
    workflow_id: Optional[str] = None
    project_id: Optional[str] = None
    connection_id: Optional[str] = None  # planner through a provider connection (needs consent)
    scopes: Scopes = Field(default_factory=Scopes)
    budget: Optional[Budget] = None
    parameters: Optional[dict[str, Any]] = None
    success_criteria: Optional[list[dict[str, Any]]] = None  # explicit user criteria (SuccessCriterion dicts)


class AnswerBody(BaseModel):
    question_id: str
    approve: Optional[bool] = None
    text: Optional[str] = Field(default=None, max_length=4000)


class SaveRecipeBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parameters: list[dict[str, Any]] = Field(default_factory=list)


class RunWorkflowBody(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)
    scopes: Optional[Scopes] = None  # defaults to the workflow's recorded scope on this host
    budget: Optional[Budget] = None
    project_id: Optional[str] = None
    connection_id: Optional[str] = None
    mode: Optional[Literal["auto", "replan", "simple"]] = "auto"  # replan = planner only; simple = 0.3-style verbatim replay (A/B arms)


class WorkflowPatchBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    parameters: Optional[list[dict[str, Any]]] = None
    known_limits_de: Optional[list[str]] = None
    steps: Optional[list[dict[str, Any]]] = None
    status: Optional[Literal["candidate", "demoted", "retired"]] = None


class BindingBody(BaseModel):
    role: str = Field(min_length=1, max_length=80)
    kind: Literal["dir", "file", "app", "window", "url", "account", "model"]
    target: str = Field(min_length=1, max_length=600)


class TeachStartBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source: Literal["browser", "window"]
    url: Optional[str] = Field(default=None, max_length=2000)
    source_id: Optional[str] = Field(default=None, max_length=60)
    scopes: Scopes = Field(default_factory=Scopes)


class TeachFinishBody(BaseModel):
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)


class Artifact(BaseModel):
    kind: Literal["file", "url", "text"]
    path: Optional[str] = None
    url: Optional[str] = None
    label: str = ""


class TaskResult(BaseModel):
    ok: bool
    summary_de: str
    artifacts: list[Artifact] = Field(default_factory=list)
    verified: bool = False
    verified_by: Optional[str] = None
    timings: dict[str, int] = Field(default_factory=dict)  # observe_ms / wait_ms / planning_ms / executing_ms (sums)
    outcome: Optional[Literal["fulfilled", "not_fulfilled", "unclear"]] = None
    evidence_paths: list[str] = Field(default_factory=list)


class PendingQuestion(BaseModel):
    id: str
    text_de: str
    kind: Literal["approval", "input"]
    action: Optional[dict[str, Any]] = None
    binding: Optional[dict[str, Any]] = None  # {"role", "kind", "hint"}: the answer is a target on this host


class ActionResult(BaseModel):
    ok: bool
    summary_de: str
    evidence: Optional[dict[str, Any]] = None
    snapshot_id: Optional[str] = None
    error: Optional[str] = None
    verified: bool = False


class Action(BaseModel):
    action_id: str
    task_id: str
    seq: int
    call_id: Optional[str] = None
    origin: Literal["planner", "workflow", "repair", "teach", "verifier"] = "planner"
    tool: str
    arguments: dict[str, Any]
    state: ActionState
    reason_de: Optional[str] = None
    expected_effect: str = ""
    observation_ref: Optional[str] = None
    side_effect: Literal["none", "local", "external"] = "none"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    result: Optional[ActionResult] = None


class Task(BaseModel):
    id: str
    goal: str
    state: TaskState
    step: int
    max_steps: int
    max_seconds: int
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    summary_de: str = ""
    result: Optional[TaskResult] = None
    scopes: Scopes
    pending_question: Optional[PendingQuestion] = None
    conversation_id: Optional[str] = None
    live_session_id: Optional[str] = None
    recipe_id: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None
    llm_calls: int = 0
    host_bound: bool = True
    last_error: Optional[str] = None
    # 0.5
    workflow_id: Optional[str] = None
    project_id: Optional[str] = None
    connection_id: Optional[str] = None
    success_criteria: list[dict[str, Any]] = Field(default_factory=list)
    outcome: Optional[Literal["fulfilled", "not_fulfilled", "unclear"]] = None
    outcome_report: Optional[dict[str, Any]] = None
    reuse: Optional[dict[str, Any]] = None  # ReusePlan + run id when a workflow was used


class RecipeStep(BaseModel):
    tool: str
    arguments_template: dict[str, Any]
    check: str = ""


class Recipe(BaseModel):
    id: str
    version: int = 1
    name: str
    purpose_de: str = ""
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    origin: dict[str, Any] = Field(default_factory=dict)
    requires: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)
    scope: Scopes = Field(default_factory=Scopes)
    steps: list[RecipeStep] = Field(default_factory=list)
    final_checks: list[str] = Field(default_factory=list)
    last_verified_at: Optional[str] = None
    known_limits_de: list[str] = Field(default_factory=list)
