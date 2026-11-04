import { describe, expect, it } from "vitest";
import type { Action, ActivityEvent, Task, TaskState } from "../src/renderer/api/types";
import {
  ACTION_STATE_LABEL,
  applyTaskStreamEvent,
  canCancel,
  canPause,
  canResume,
  canSaveRecipe,
  compactArguments,
  draftFromScopes,
  draftToRequest,
  emptyDraft,
  emptyFeed,
  evidencePath,
  isTerminalTaskState,
  reduceTaskActivity,
  scopeSummary,
  sortTasksNewestFirst,
  TASK_STATE_LABEL,
  TASK_STATE_TONE,
  upsertAction,
  upsertTask,
  validateDraft,
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
  ...extra,
});

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
    const q = { id: "q1", text_de: "Darf ich?", kind: "approval" as const, action: action(2, "proposed") };
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
