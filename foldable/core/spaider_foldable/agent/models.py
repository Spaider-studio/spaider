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
    scopes: Scopes = Field(default_factory=Scopes)
    budget: Optional[Budget] = None
    parameters: Optional[dict[str, Any]] = None


class AnswerBody(BaseModel):
    question_id: str
    approve: Optional[bool] = None
    text: Optional[str] = Field(default=None, max_length=4000)


class SaveRecipeBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parameters: list[dict[str, Any]] = Field(default_factory=list)


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


class PendingQuestion(BaseModel):
    id: str
    text_de: str
    kind: Literal["approval", "input"]
    action: Optional[dict[str, Any]] = None


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
