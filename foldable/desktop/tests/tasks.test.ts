import { describe, expect, it } from "vitest";
import type { Action, ActivityEvent, StepEvent, Task, TaskState, TeachSession, Workflow } from "../src/renderer/api/types";
import {
  ACTION_STATE_LABEL,
  appendStep,
  applyTaskStreamEvent,
  BINDING_KIND_LABEL,
  canCancel,
  canCompileWorkflow,
  canPause,
  canResume,
  canSaveRecipe,
  coerceParameterValue,
  compactArguments,
  countNewSteps,
  DEPENDENCY_KIND_LABEL,
  dependencySummary,
  draftFromScopes,
  draftFromWorkflow,
  draftToRequest,
  draftToRunRequest,
  emptyDraft,
  emptyFeed,
  evidencePath,
  formatCoverage,
  isStaleWorkflow,
  isTerminalTaskState,
  missingWorkflowParameters,
  OUTCOME_LABEL,
  OUTCOME_TONE,
  reduceTaskActivity,
  REUSE_MODE_LABEL,
  scopeSummary,
  sortTasksNewestFirst,
  sortWorkflows,
  STEP_KIND_LABEL,
  stepCountText,
  suggestParameterName,
  TASK_STATE_LABEL,
  TASK_STATE_TONE,
  teachValues,
  upsertAction,
  upsertTask,
  upsertWorkflow,
  validateDraft,
  WORKFLOW_ORIGIN_LABEL,
  WORKFLOW_STATUS_LABEL,
  WORKFLOW_STATUS_TONE,
} from "../src/renderer/store/tasks";

const ALL_STATES: TaskState[] = [
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
];

const task = (id: string, state: TaskState, extra: Partial<Task> = {}): Task => ({
  id,
  goal: `Ziel ${id}`,
  state,
  step: 0,
  max_steps: 20,
  created_at: "2026-11-03T10:00:00Z",
  updated_at: "2026-11-03T10:00:00Z",
  started_at: null,
  finished_at: null,
  summary_de: "",
  result: null,
  scopes: { files: null, browser: null, windows: null, memory: true, external_effects: false },
  pending_question: null,
  conversation_id: null,
  live_session_id: null,
  recipe_id: null,
  llm_calls: 0,
  host_bound: false,
  last_error: null,
  workflow_id: null,
  project_id: null,
  connection_id: null,
  success_criteria: [],
  outcome: null,
  outcome_report: null,
  reuse: null,
  ...extra,
});

const action = (seq: number, state: Action["state"], extra: Partial<Action> = {}): Action => ({
  action_id: `act_${seq}`,
  task_id: "t1",
  seq,
  tool: "files.write",
  arguments: { path: "C:\\x\\y.md", mode: "create" },
  state,
  reason_de: null,
  expected_effect: "Datei schreiben",
  observation_ref: null,
  started_at: null,
  finished_at: null,
  duration_ms: null,
  result: null,
  call_id: null,
  origin: "planner",
  ...extra,
});

const workflow = (id: string, extra: Partial<Workflow> = {}): Workflow => ({
  id,
  version: 1,
  name: `Ablauf ${id}`,
  purpose_de: "Protokoll zusammenfassen",
  status: "candidate",
  parameters: [
    { name: "zielordner", description_de: "Ordner", example: "C:\\out", kind: "path", required: true },
    { name: "anzahl", description_de: "Wie viele", example: "3", kind: "number", required: false },
    { name: "stichtag", description_de: "Datum", example: "2026-11-11", kind: "date", required: false },
  ],
  preconditions: [],
  bindings: [],
  steps: [
    { index: 0, tool: "windows.read", arguments_template: {}, target: null, read_set: [], write_set: [], preconditions: [], postconditions: [], side_effect: "none", depends_on: [], derive: false, derive_from: [], check_de: "" },
    { index: 1, tool: "files.write", arguments_template: {}, target: null, read_set: [], write_set: [], preconditions: [], postconditions: [], side_effect: "local", depends_on: [], derive: true, derive_from: [0], check_de: "" },
  ],
  checks: [],
  dependencies: [],
  scope: { files: { roots: ["C:\\a"], write_dir: "C:\\a\\out" }, browser: null, windows: { source_ids: [], allowed_processes: ["notepad.exe"] }, memory: true, external_effects: false },
  origin: {},
  tool_contract_hash: "abc",
  model_hint: null,
  known_limits_de: [],
  created_at: "2026-11-10T10:00:00Z",
  updated_at: "2026-11-10T10:00:00Z",
  last_verified_at: null,
  stats: {
    success_runs: 0,
    failed_runs: 0,
    distinct_param_sets: 0,
    negative_checked: false,
    last_verified_at: null,
    last_failed_at: null,
    origin_kind: "compiled",
    dependencies: { total: 0, valid: 0, invalid: 0, by_kind: {}, inferred: 0 },
  },
  requires: { tools: ["windows.read", "files.write"], apps: ["notepad.exe"], vision: false },
  derived_steps: [1],
  external_effects: false,
  ...extra,
});

const step = (kind: StepEvent["kind"], message_de: string, at = "2026-11-11T10:00:00Z", extra: Partial<StepEvent> = {}): StepEvent => ({ kind, message_de, at, ...extra });

describe("task state labels", () => {
  it("has a German label and a tone for every TaskState", () => {
    for (const s of ALL_STATES) {
      expect(TASK_STATE_LABEL[s]).toBeTruthy();
      expect(TASK_STATE_LABEL[s]).not.toBe(s);
      expect(TASK_STATE_TONE[s]).toBeTruthy();
    }
    expect(Object.keys(TASK_STATE_LABEL).sort()).toEqual([...ALL_STATES].sort());
  });

  it("labels every action state", () => {
    for (const s of ["proposed", "validated", "rejected", "executing", "done", "failed", "unknown_outcome", "cancelled"] as const) {
      expect(ACTION_STATE_LABEL[s]).toBeTruthy();
    }
  });
});

describe("task transitions and buttons", () => {
  it("knows terminal states", () => {
    expect(ALL_STATES.filter(isTerminalTaskState)).toEqual(["completed", "failed", "cancelled"]);
  });

  it("offers Pause only while running, Weiter when paused/interrupted, Stopp until terminal", () => {
    expect(canPause(task("a", "executing"))).toBe(true);
    expect(canPause(task("a", "paused"))).toBe(false);
    expect(canPause(task("a", "waiting_for_user"))).toBe(false);
    expect(canResume(task("a", "paused"))).toBe(true);
    expect(canResume(task("a", "interrupted"))).toBe(true);
    expect(canResume(task("a", "executing"))).toBe(false);
    expect(canCancel(task("a", "waiting_for_user"))).toBe(true);
    expect(canCancel(task("a", "completed"))).toBe(false);
    expect(canSaveRecipe(task("a", "completed"))).toBe(true);
    expect(canSaveRecipe(task("a", "failed"))).toBe(false);
  });

  it("upserts tasks newest first and never applies an older snapshot", () => {
    let list = sortTasksNewestFirst([task("old", "completed", { created_at: "2026-11-01T00:00:00Z" }), task("new", "created", { created_at: "2026-11-03T00:00:00Z" })]);
    expect(list.map((t) => t.id)).toEqual(["new", "old"]);
    list = upsertTask(list, task("mid", "planning", { created_at: "2026-11-02T00:00:00Z" }));
    expect(list.map((t) => t.id)).toEqual(["new", "mid", "old"]);
    list = upsertTask(list, task("mid", "executing", { created_at: "2026-11-02T00:00:00Z", updated_at: "2026-11-03T10:05:00Z" }));
    expect(list.find((t) => t.id === "mid")?.state).toBe("executing");
    const stale = upsertTask(list, task("mid", "planning", { created_at: "2026-11-02T00:00:00Z", updated_at: "2026-11-03T10:01:00Z" }));
    expect(stale.find((t) => t.id === "mid")?.state).toBe("executing");
  });

  it("walks a task through the event stream: task -> action -> question -> done", () => {
    let feed = emptyFeed();
    let r = applyTaskStreamEvent(feed, { type: "task", data: task("t1", "observing") });
    expect(r.task?.state).toBe("observing");
    feed = r.feed;
    r = applyTaskStreamEvent(feed, { type: "action", data: action(1, "proposed") });
    feed = r.feed;
    r = applyTaskStreamEvent(feed, { type: "action", data: action(1, "done", { result: { ok: true, summary_de: "fertig", evidence: null, snapshot_id: null } }) });
    feed = r.feed;
    expect(feed.actions).toHaveLength(1);
    expect(feed.actions[0].state).toBe("done");
    r = applyTaskStreamEvent(feed, { type: "token", data: { text: "Ich lese " } });
    r = applyTaskStreamEvent(r.feed, { type: "token", data: { text: "zuerst." } });
    feed = r.feed;
    expect(feed.notes).toBe("Ich lese zuerst.");
    const q = { id: "q1", text_de: "Darf ich?", kind: "approval" as const, action: action(2, "proposed"), binding: null };
    r = applyTaskStreamEvent(feed, { type: "question", data: q });
    feed = r.feed;
    expect(feed.question?.id).toBe("q1");
    r = applyTaskStreamEvent(feed, { type: "task", data: task("t1", "executing", { pending_question: null }) });
    feed = r.feed;
    expect(feed.question).toBeNull();
    r = applyTaskStreamEvent(feed, { type: "done", data: task("t1", "completed") });
    expect(r.feed.done).toBe(true);
    expect(r.task?.state).toBe("completed");
  });

  it("keeps actions sorted by seq when they arrive out of order", () => {
    const list = upsertAction(upsertAction([], action(3, "proposed")), action(1, "done"));
    expect(list.map((a) => a.seq)).toEqual([1, 3]);
    expect(upsertAction(list, action(3, "executing")).map((a) => a.state)).toEqual(["done", "executing"]);
  });
});

describe("action rendering helpers", () => {
  it("renders compact arguments and truncates", () => {
    expect(compactArguments({ path: "C:\\x\\y.md", mode: "create", n: 3, ok: true, list: [1, 2], obj: { a: 1 } })).toBe('path="C:\\x\\y.md", mode="create", n=3, ok=true, list=[2], obj={…}');
    expect(compactArguments({ text: "x".repeat(200) }, 20).length).toBe(20);
    expect(compactArguments(null)).toBe("");
  });

  it("extracts only absolute evidence paths", () => {
    expect(evidencePath(action(1, "done", { result: { ok: true, summary_de: "", evidence: { path: "C:\\out\\report.md" }, snapshot_id: null } }))).toBe("C:\\out\\report.md");
    expect(evidencePath(action(1, "done", { result: { ok: true, summary_de: "", evidence: { path: "relative/x.md" }, snapshot_id: null } }))).toBeNull();
    expect(evidencePath(action(1, "done", { result: { ok: true, summary_de: "", evidence: { url: "https://x" }, snapshot_id: null } }))).toBeNull();
    expect(evidencePath(action(1, "done"))).toBeNull();
  });
});

describe("task draft", () => {
  it("validates the draft with German messages", () => {
    const d = emptyDraft({ max_steps: 12, max_seconds: 300 });
    expect(d.external_effects).toBe(false);
    expect(d.max_steps).toBe(12);
    expect(validateDraft(d)).toMatch(/Ziel/);
    expect(validateDraft({ ...d, goal: "x", filesEnabled: true })).toMatch(/Ordner/);
    expect(validateDraft({ ...d, goal: "x", windowsEnabled: true })).toMatch(/Fenster/);
    expect(validateDraft({ ...d, goal: "x", max_steps: 0 })).toMatch(/Schritte/);
    expect(validateDraft({ ...d, goal: "x" })).toBeNull();
  });

  it("builds the request body with scopes only from the form", () => {
    const d = { ...emptyDraft(), goal: " Bericht  ", filesEnabled: true, fileRoots: ["C:\\a"], writeDir: "C:\\a\\out", windowsEnabled: true, windowSourceIds: ["win:1:2"], allowedProcesses: ["notepad.exe"], browser: true, recipe_id: "rcp_1" };
    const req = draftToRequest(d);
    expect(req.goal).toBe("Bericht");
    expect(req.recipe_id).toBe("rcp_1");
    expect(req.scopes).toEqual({
      files: { roots: ["C:\\a"], write_dir: "C:\\a\\out" },
      browser: { enabled: true, allowed_origins: null },
      windows: { source_ids: ["win:1:2"], allowed_processes: ["notepad.exe"] },
      memory: true,
      external_effects: false,
    });
    expect(req.budget).toEqual({ max_steps: 20, max_seconds: 600 });
    expect(draftToRequest(emptyDraft()).scopes.files).toBeNull();
  });

  it("prefills a draft from recipe scopes and summarises scopes", () => {
    const scopes = { files: { roots: ["C:\\a"], write_dir: null }, browser: null, windows: { source_ids: [], allowed_processes: ["notepad.exe"] }, memory: false, external_effects: true };
    const d = draftFromScopes({ ...emptyDraft(), goal: "g", recipe_id: "r" }, scopes);
    expect(d.filesEnabled).toBe(true);
    expect(d.windowsEnabled).toBe(true);
    expect(d.allowedProcesses).toEqual(["notepad.exe"]);
    expect(d.memory).toBe(false);
    expect(d.external_effects).toBe(true);
    expect(scopeSummary(scopes)).toBe("Dateien (1) · Fenster (1) · externe Wirkung");
    expect(scopeSummary({ files: null, browser: null, windows: null, memory: false, external_effects: false })).toBe("ohne Zugriffe");
  });
});

describe("agent activity in the task feed", () => {
  const act = (seq: number, phase: string, state: ActivityEvent["state"], message_de: string, request_id = "t1"): ActivityEvent => ({
    request_id,
    seq,
    phase,
    state,
    t_ms: seq * 100,
    at: "2026-11-04T10:00:00Z",
    message_de,
    counters: null,
    source_ids: [],
  });

  it("collects observe / planning / executing phases into one track", () => {
    let feed = emptyFeed();
    expect(feed.activity).toBeNull();
    feed = applyTaskStreamEvent(feed, { type: "activity", data: act(0, "observe", "started", "Schritt 1: Beobachtung") }, 5000).feed;
    expect(feed.activity?.requestId).toBe("t1");
    expect(feed.activity?.startedAt).toBe(5000);
    expect(feed.activity?.current.message_de).toBe("Schritt 1: Beobachtung");
    feed = applyTaskStreamEvent(feed, { type: "activity", data: act(1, "observe", "done", "Beobachtung fertig") }, 5100).feed;
    feed = applyTaskStreamEvent(feed, { type: "activity", data: act(2, "planning", "started", "Schritt 1: Planung") }, 5200).feed;
    feed = applyTaskStreamEvent(feed, { type: "activity", data: act(3, "executing", "started", "files.read") }, 5300).feed;
    expect(feed.activity?.events.map((e) => e.phase)).toEqual(["observe", "observe", "planning", "executing"]);
    expect(feed.activity?.lastSeq).toBe(3);
    expect(feed.activity?.outcome).toBe("live");
    // other feed fields untouched
    expect(feed.actions).toEqual([]);
    expect(feed.done).toBe(false);
  });

  it("starts a fresh track when the run restarts (seq from 0 again) or the request id changes", () => {
    let track = reduceTaskActivity(null, act(0, "observe", "started", "a"), 1);
    track = reduceTaskActivity(track, act(1, "planning", "started", "b"), 2);
    expect(track.events).toHaveLength(2);
    const restarted = reduceTaskActivity(track, act(0, "observe", "started", "c"), 3);
    expect(restarted.events).toHaveLength(1);
    expect(restarted.startedAt).toBe(3);
    const other = reduceTaskActivity(restarted, act(5, "executing", "started", "d", "t2"), 4);
    expect(other.requestId).toBe("t2");
    expect(other.events).toHaveLength(1);
  });

  it("ignores malformed activity events", () => {
    const feed = emptyFeed();
    const broken = applyTaskStreamEvent(feed, { type: "activity", data: { seq: "x" } as unknown as ActivityEvent }).feed;
    expect(broken).toBe(feed);
  });
});

// ----- v1.5 (0.5): execution core ---------------------------------------------------------

describe("step events (Tatsächliche Schritte)", () => {
  it("appends step events to the feed in arrival order and ignores malformed ones", () => {
    let feed = emptyFeed();
    expect(feed.steps).toEqual([]);
    feed = applyTaskStreamEvent(feed, { type: "step", data: step("read", "Datei gelesen: x.txt", "2026-11-11T10:00:00Z") }).feed;
    feed = applyTaskStreamEvent(feed, { type: "step", data: step("verify", "Ergebnis geprüft: erfüllt", "2026-11-11T10:00:05Z", { ok: true, status: "fulfilled" }) }).feed;
    expect(feed.steps.map((s) => s.kind)).toEqual(["read", "verify"]);
    expect(feed.steps[1].ok).toBe(true);
    const broken = applyTaskStreamEvent(feed, { type: "step", data: { kind: "read" } as unknown as StepEvent }).feed;
    expect(broken).toBe(feed);
    // other feed fields untouched
    expect(feed.actions).toEqual([]);
    expect(feed.notes).toBe("");
  });

  it("de-duplicates replayed steps by at + message_de after a reconnect", () => {
    const a = step("read", "Datei gelesen: x.txt", "2026-11-11T10:00:00Z");
    const b = step("write", "Datei geschrieben: y.md", "2026-11-11T10:00:01Z");
    let steps = appendStep(appendStep([], a), b);
    expect(steps).toHaveLength(2);
    steps = appendStep(steps, { ...a, ok: true });
    expect(steps).toHaveLength(2);
    // same text at a different time is a new step
    steps = appendStep(steps, step("read", "Datei gelesen: x.txt", "2026-11-11T10:00:09Z"));
    expect(steps).toHaveLength(3);
    let feed = emptyFeed();
    for (const s of [a, b, a, b]) feed = applyTaskStreamEvent(feed, { type: "step", data: s }).feed;
    expect(feed.steps).toHaveLength(2);
  });

  it("labels every step kind in German", () => {
    for (const k of ["read", "write", "check", "verify", "skip", "repair", "repaired", "replan", "gate", "derive", "pause"] as const) {
      expect(STEP_KIND_LABEL[k]).toBeTruthy();
      expect(STEP_KIND_LABEL[k]).not.toBe(k);
    }
  });
});

describe("v1.5 labels", () => {
  it("labels outcomes, reuse modes, workflow status and origin in German", () => {
    expect(OUTCOME_LABEL).toEqual({ fulfilled: "erfüllt", not_fulfilled: "nicht erfüllt", unclear: "ungeklärt" });
    expect(OUTCOME_TONE.fulfilled).toBe("green");
    expect(OUTCOME_TONE.not_fulfilled).toBe("red");
    expect(REUSE_MODE_LABEL).toEqual({ reuse: "wiederverwendet", repair: "repariert", replan: "neu geplant", replan_forced: "ohne Wiederverwendung" });
    expect(WORKFLOW_STATUS_LABEL).toEqual({ candidate: "Kandidat", verified: "geprüft", demoted: "zurückgestuft", retired: "stillgelegt" });
    expect(WORKFLOW_STATUS_TONE.verified).toBe("green");
    expect(WORKFLOW_ORIGIN_LABEL).toEqual({ compiled: "aus Aufgabe", taught: "beigebracht", migrated: "aus 0.3-Rezept" });
    for (const k of ["fact", "file", "binding", "app", "tool_contract", "model", "workflow"] as const) expect(DEPENDENCY_KIND_LABEL[k]).toBeTruthy();
    for (const k of ["dir", "file", "app", "window", "url", "account", "model"] as const) expect(BINDING_KIND_LABEL[k]).toBeTruthy();
  });

  it("summarises dependencies, coverage and step counts", () => {
    expect(dependencySummary({ total: 6, valid: 3, invalid: 1, by_kind: {}, inferred: 2 })).toBe("3 gültig · 1 ungültig · 2 vermutet");
    expect(dependencySummary({ total: 2, valid: 2, invalid: 0, by_kind: {}, inferred: 0 })).toBe("2 gültig");
    expect(dependencySummary({ total: 0, valid: 0, invalid: 0, by_kind: {}, inferred: 0 })).toBe("keine Abhängigkeiten");
    expect(dependencySummary(null)).toBe("keine Abhängigkeiten");
    expect(formatCoverage(0.853)).toBe("85 %");
    expect(formatCoverage(1)).toBe("100 %");
    expect(formatCoverage(null)).toBe("–");
    expect(stepCountText(workflow("w"))).toBe("2 Schritte (1 abgeleitet)");
    expect(stepCountText(workflow("w", { derived_steps: [], steps: workflow("w").steps.slice(0, 1) }))).toBe("1 Schritt");
    expect(countNewSteps(2)).toBe(2);
    expect(countNewSteps([{}, {}, {}])).toBe(3);
    expect(countNewSteps(undefined)).toBe(0);
  });

  it("flags stale workflows and sorts usable ones first", () => {
    const fresh = workflow("a");
    const stale = workflow("b", { stats: { ...fresh.stats, dependencies: { total: 3, valid: 2, invalid: 1, by_kind: { file: 3 }, inferred: 0 } } });
    expect(isStaleWorkflow(fresh)).toBe(false);
    expect(isStaleWorkflow(stale)).toBe(true);
    const sorted = sortWorkflows([workflow("r", { status: "retired", name: "A" }), workflow("c", { status: "candidate", name: "Z" }), workflow("v", { status: "verified", name: "M" }), workflow("c2", { status: "candidate", name: "B" })]);
    expect(sorted.map((w) => w.id)).toEqual(["v", "c2", "c", "r"]);
    const upserted = upsertWorkflow(sorted, { ...fresh, id: "c", status: "verified", name: "Z" });
    expect(upserted.find((w) => w.id === "c")?.status).toBe("verified");
    expect(upserted).toHaveLength(4);
    expect(upsertWorkflow(sorted, workflow("new"))).toHaveLength(5);
  });

  it("allows compiling only completed, successful, not-unfulfilled tasks", () => {
    const ok = { ok: true, summary_de: "", artifacts: [], verified: true, outcome: "fulfilled" as const, evidence_paths: [], verified_by: "verifier" };
    expect(canCompileWorkflow(task("a", "completed", { result: ok }))).toBe(true);
    expect(canCompileWorkflow(task("a", "completed", { result: ok, outcome: "unclear" }))).toBe(true);
    expect(canCompileWorkflow(task("a", "completed", { result: ok, outcome: "not_fulfilled" }))).toBe(false);
    expect(canCompileWorkflow(task("a", "completed", { result: { ...ok, ok: false } }))).toBe(false);
    expect(canCompileWorkflow(task("a", "completed"))).toBe(false);
    expect(canCompileWorkflow(task("a", "failed", { result: ok }))).toBe(false);
  });
});

describe("workflow draft (POST /workflows/{id}/run)", () => {
  it("prefills the draft from a workflow with scope, goal and empty parameters", () => {
    const w = workflow("w1");
    const d = draftFromWorkflow(emptyDraft({ max_steps: 7 }), w);
    expect(d.workflow_id).toBe("w1");
    expect(d.goal).toBe("Protokoll zusammenfassen");
    expect(d.recipe_id).toBeNull();
    expect(d.filesEnabled).toBe(true);
    expect(d.fileRoots).toEqual(["C:\\a"]);
    expect(d.windowsEnabled).toBe(true);
    expect(d.allowedProcesses).toEqual(["notepad.exe"]);
    expect(d.workflowParameters).toEqual({ zielordner: "", anzahl: "", stichtag: "" });
    expect(d.replan).toBe(false);
    expect(d.max_steps).toBe(7);
  });

  it("requires the required parameters and validates numbers", () => {
    const w = workflow("w1");
    const d = draftFromWorkflow(emptyDraft(), w);
    expect(missingWorkflowParameters(w, d.workflowParameters)).toEqual(["zielordner"]);
    expect(validateDraft(d, w)).toMatch(/zielordner/);
    const filled = { ...d, workflowParameters: { ...d.workflowParameters, zielordner: "C:\\out" } };
    expect(validateDraft(filled, w)).toBeNull();
    expect(validateDraft({ ...filled, workflowParameters: { ...filled.workflowParameters, anzahl: "drei" } }, w)).toMatch(/anzahl/);
    // without a workflow the goal is still required
    expect(validateDraft({ ...emptyDraft(), workflow_id: null }, null)).toMatch(/Ziel/);
    // a workflow run does not need a goal text
    expect(validateDraft({ ...filled, goal: "" }, w)).toBeNull();
  });

  it("builds the run request with typed parameters, scopes, budget and the replan mode", () => {
    const w = workflow("w1");
    const d = { ...draftFromWorkflow(emptyDraft(), w), workflowParameters: { zielordner: " C:\\out ", anzahl: "3,5", stichtag: "" }, replan: true };
    const req = draftToRunRequest(d, w);
    expect(req.parameters).toEqual({ zielordner: "C:\\out", anzahl: 3.5 });
    expect(req.mode).toBe("replan");
    expect(req.budget).toEqual({ max_steps: 20, max_seconds: 600 });
    expect(req.scopes?.files).toEqual({ roots: ["C:\\a"], write_dir: "C:\\a\\out" });
    expect(draftToRunRequest({ ...d, replan: false }, w).mode).toBe("auto");
    expect(coerceParameterValue("number", "12")).toBe(12);
    expect(coerceParameterValue("number", "x")).toBe("x");
    expect(coerceParameterValue("date", " 2026-11-11 ")).toBe("2026-11-11");
    // the plain task request carries the workflow id as well
    expect(draftToRequest(d).workflow_id).toBe("w1");
  });
});

describe("teach helpers", () => {
  const session = (steps: TeachSession["steps"]): TeachSession => ({
    id: "teach_1",
    name: "Demo",
    source: "browser",
    url: "https://example.test/form",
    source_id: null,
    state: "recording",
    steps,
    created_at: "2026-11-11T10:00:00Z",
    finished_at: null,
    events: [],
    workflow_id: null,
    note_de: "",
    scopes: { files: null, browser: { enabled: true, allowed_origins: null }, windows: null, memory: false, external_effects: false },
  });
  const tstep = (tool: string, args: Record<string, unknown>, target: string | null = null): TeachSession["steps"][number] => ({
    tool,
    arguments: args,
    check_de: "",
    evidence: null,
    target: target ? { role: "edit", name: target } : null,
    at: "2026-11-11T10:00:01Z",
  });

  it("extracts typed / chosen values as parameter candidates", () => {
    const s = session([
      tstep("browser.navigate", { url: "https://example.test" }),
      tstep("browser.fill", { selector: "#name", text: "Müller GmbH" }, "Firmenname"),
      tstep("browser.click", { selector: "#next" }, "Weiter"),
      tstep("browser.select", { selector: "#land", option: "DE" }, "Land"),
      tstep("windows.set_value", { element: "Betrag", value: "42,00" }, "Betrag"),
      tstep("browser.fill", { selector: "#empty", text: "   " }),
    ]);
    const values = teachValues(s);
    expect(values.map((v) => [v.step, v.key, v.value, v.target])).toEqual([
      [1, "text", "Müller GmbH", "Firmenname"],
      [3, "option", "DE", "Land"],
      [4, "value", "42,00", "Betrag"],
    ]);
    expect(teachValues(null)).toEqual([]);
  });

  it("suggests parameter names from element labels", () => {
    expect(suggestParameterName("E-Mail Adresse", "wert_1")).toBe("e_mail_adresse");
    expect(suggestParameterName("Straße / Nr.", "wert_2")).toBe("strasse_nr");
    expect(suggestParameterName(null, "wert_3")).toBe("wert_3");
    expect(suggestParameterName("###", "wert_4")).toBe("wert_4");
  });
});
