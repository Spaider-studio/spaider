/**
 * Pure state logic for agent tasks (API_CONTRACT.md, Addendum v1.2 "Agent"):
 * German labels for every TaskState / ActionState, list upserts, the reducer
 * for `GET /api/agent/tasks/{id}/events`, the task form draft and its
 * conversion into the request body. Nothing here talks to the network.
 */
import type {
  Action,
  ActionState,
  ActivityEvent,
  BindingKind,
  CreateTaskRequest,
  DependencyKind,
  PendingQuestion,
  ReuseMode,
  RunWorkflowRequest,
  Scopes,
  StepEvent,
  StepKind,
  Task,
  TaskOutcome,
  TaskState,
  TaskStreamEvent,
  TeachSession,
  Workflow,
  WorkflowDependencyStats,
  WorkflowOriginKind,
  WorkflowParameter,
  WorkflowStatus,
} from "../api/types";
import { createTrack, reduceActivity, type ActivityTrack } from "./activity";

export const TASK_STATE_LABEL: Record<TaskState, string> = {
  created: "Angelegt",
  observing: "Beobachtet",
  planning: "Plant",
  validating: "Prüft Plan",
  executing: "Führt aus",
  verifying: "Verifiziert",
  completed: "Abgeschlossen",
  failed: "Fehlgeschlagen",
  cancelled: "Abgebrochen",
  paused: "Pausiert",
  waiting_for_user: "Wartet auf Antwort",
  interrupted: "Unterbrochen",
};

export type TaskTone = "neutral" | "blue" | "green" | "yellow" | "red" | "purple" | "cyan";

export const TASK_STATE_TONE: Record<TaskState, TaskTone> = {
  created: "neutral",
  observing: "cyan",
  planning: "blue",
  validating: "blue",
  executing: "blue",
  verifying: "cyan",
  completed: "green",
  failed: "red",
  cancelled: "neutral",
  paused: "yellow",
  waiting_for_user: "purple",
  interrupted: "yellow",
};

export const ACTION_STATE_LABEL: Record<ActionState, string> = {
  proposed: "vorgeschlagen",
  validated: "geprüft",
  rejected: "abgelehnt",
  executing: "läuft",
  done: "fertig",
  failed: "fehlgeschlagen",
  unknown_outcome: "Ergebnis unklar",
  cancelled: "abgebrochen",
};

export const TERMINAL_TASK_STATES: readonly TaskState[] = ["completed", "failed", "cancelled"];
const RUNNING_TASK_STATES: readonly TaskState[] = ["created", "observing", "planning", "validating", "executing", "verifying"];

export function isTerminalTaskState(state: TaskState): boolean {
  return TERMINAL_TASK_STATES.includes(state);
}

/** The core is actively working on the task (not paused / waiting / finished). */
export function isRunningTaskState(state: TaskState): boolean {
  return RUNNING_TASK_STATES.includes(state);
}

export function canPause(task: Task): boolean {
  return isRunningTaskState(task.state);
}

export function canResume(task: Task): boolean {
  return task.state === "paused" || task.state === "interrupted";
}

export function canCancel(task: Task): boolean {
  return !isTerminalTaskState(task.state);
}

export function canSaveRecipe(task: Task): boolean {
  return task.state === "completed";
}

/** v1.5: only completed, successful tasks whose outcome is not "nicht erfüllt" compile into a workflow (else 409 `not_completed`). */
export function canCompileWorkflow(task: Task): boolean {
  return task.state === "completed" && Boolean(task.result?.ok) && (task.outcome ?? null) !== "not_fulfilled";
}

function stamp(t: Task): number {
  const c = Date.parse(t.created_at);
  return Number.isNaN(c) ? 0 : c;
}

export function sortTasksNewestFirst(list: Task[]): Task[] {
  return [...list].sort((a, b) => stamp(b) - stamp(a));
}

export function upsertTask(list: Task[], task: Task): Task[] {
  const idx = list.findIndex((t) => t.id === task.id);
  if (idx === -1) return sortTasksNewestFirst([task, ...list]);
  const current = list[idx];
  // Never let an older snapshot overwrite a newer one (events and polls may race).
  if (Date.parse(task.updated_at) < Date.parse(current.updated_at)) return list;
  return list.map((t) => (t.id === task.id ? task : t));
}

/** Inserts or replaces an action, keeping the list sorted by seq. */
export function upsertAction(list: Action[], action: Action): Action[] {
  const without = list.filter((a) => a.action_id !== action.action_id);
  return [...without, action].sort((a, b) => a.seq - b.seq);
}

export interface TaskFeed {
  actions: Action[];
  /** Explanation text of the agent (token events) - no evidence. */
  notes: string;
  question: PendingQuestion | null;
  /** The stream ended with `done`. */
  done: boolean;
  /** Agent activity (`event: activity`, request_id = task id): observe / planning / executing phases. */
  activity: ActivityTrack | null;
  /** v1.5: steps that really happened (`event: step`) - the "Tatsächliche Schritte" timeline. */
  steps: StepEvent[];
}

export function emptyFeed(actions: Action[] = []): TaskFeed {
  return { actions: [...actions].sort((a, b) => a.seq - b.seq), notes: "", question: null, done: false, activity: null, steps: [] };
}

function stepKey(ev: StepEvent): string {
  return `${ev.at}\u0000${ev.message_de}`;
}

/** Appends a step event; a replay after a reconnect must not duplicate steps (same `at` + `message_de`). */
export function appendStep(steps: StepEvent[], ev: StepEvent): StepEvent[] {
  const key = stepKey(ev);
  if (steps.some((s) => stepKey(s) === key)) return steps;
  return [...steps, ev];
}

function isStepEvent(data: unknown): data is StepEvent {
  if (!data || typeof data !== "object") return false;
  const d = data as Record<string, unknown>;
  return typeof d.kind === "string" && typeof d.message_de === "string" && typeof d.at === "string";
}

/**
 * Agent activity of a task. The core numbers the events per run; a run that
 * starts again (resume after an interruption) begins at seq 0 again, so a
 * lower or equal seq starts a fresh track instead of being discarded.
 */
export function reduceTaskActivity(track: ActivityTrack | null, ev: ActivityEvent, now: number): ActivityTrack {
  if (!track || track.requestId !== ev.request_id || ev.seq <= track.lastSeq) return createTrack(ev.request_id, ev, now);
  return reduceActivity(track, ev);
}

/** Applies one event of the task stream; returns the new feed and the task snapshot if the event carried one. */
export function applyTaskStreamEvent(feed: TaskFeed, ev: TaskStreamEvent, now: number = Date.now()): { feed: TaskFeed; task: Task | null } {
  switch (ev.type) {
    case "task":
      return { feed: { ...feed, question: ev.data.pending_question ?? null }, task: ev.data };
    case "action":
      return { feed: { ...feed, actions: upsertAction(feed.actions, ev.data) }, task: null };
    case "token":
      return { feed: { ...feed, notes: feed.notes + (typeof ev.data.text === "string" ? ev.data.text : "") }, task: null };
    case "question":
      return { feed: { ...feed, question: ev.data }, task: null };
    case "done":
      return { feed: { ...feed, done: true, question: ev.data.pending_question ?? null }, task: ev.data };
    case "activity":
      if (!ev.data || typeof ev.data.request_id !== "string" || typeof ev.data.seq !== "number") return { feed, task: null };
      return { feed: { ...feed, activity: reduceTaskActivity(feed.activity, ev.data, now) }, task: null };
    case "step":
      if (!isStepEvent(ev.data)) return { feed, task: null };
      return { feed: { ...feed, steps: appendStep(feed.steps, ev.data) }, task: null };
    default:
      return { feed, task: null };
  }
}

/** One-line rendering of tool arguments, e.g. `path="C:\\x\\y.txt", mode="append"`. */
export function compactArguments(args: Record<string, unknown> | null | undefined, max = 90): string {
  if (!args) return "";
  const parts: string[] = [];
  for (const [k, v] of Object.entries(args)) {
    let text: string;
    if (typeof v === "string") text = `"${v}"`;
    else if (v === null || typeof v === "number" || typeof v === "boolean") text = String(v);
    else if (Array.isArray(v)) text = `[${v.length}]`;
    else text = "{…}";
    parts.push(`${k}=${text}`);
  }
  const joined = parts.join(", ");
  return joined.length > max ? `${joined.slice(0, max - 1).trimEnd()}…` : joined;
}

const PATH_RE = /^(?:[A-Za-z]:\\|\\\\|\/)[^\n\r]+$/;

/** A file path mentioned as evidence of an action (only absolute paths). */
export function evidencePath(action: Action): string | null {
  const ev = action.result?.evidence;
  if (!ev) return null;
  for (const key of ["path", "file", "file_path", "output_path", "screenshot_path"]) {
    const v = ev[key];
    if (typeof v === "string" && PATH_RE.test(v)) return v;
  }
  return null;
}

// ----- task form draft ------------------------------------------------------------------

export interface TaskDraft {
  goal: string;
  recipe_id: string | null;
  /** v1.5: start from a workflow (POST /workflows/{id}/run instead of POST /tasks). */
  workflow_id: string | null;
  /** v1.5: parameter values as typed (converted by kind in draftToRunRequest). */
  workflowParameters: Record<string, string>;
  /** v1.5: "Ohne Wiederverwendung (Vergleichslauf)" -> mode "replan". */
  replan: boolean;
  live_session_id: string | null;
  conversation_id: string | null;
  filesEnabled: boolean;
  fileRoots: string[];
  writeDir: string | null;
  browser: boolean;
  windowsEnabled: boolean;
  windowSourceIds: string[];
  allowedProcesses: string[];
  memory: boolean;
  external_effects: boolean;
  max_steps: number;
  max_seconds: number;
}

export function emptyDraft(defaults: { max_steps?: number; max_seconds?: number } = {}): TaskDraft {
  return {
    goal: "",
    recipe_id: null,
    workflow_id: null,
    workflowParameters: {},
    replan: false,
    live_session_id: null,
    conversation_id: null,
    filesEnabled: false,
    fileRoots: [],
    writeDir: null,
    browser: false,
    windowsEnabled: false,
    windowSourceIds: [],
    allowedProcesses: [],
    memory: true,
    external_effects: false,
    max_steps: defaults.max_steps ?? 20,
    max_seconds: defaults.max_seconds ?? 600,
  };
}

/** Required workflow parameters without a value (names), in workflow order. */
export function missingWorkflowParameters(workflow: Workflow | null | undefined, values: Record<string, string>): string[] {
  if (!workflow) return [];
  return workflow.parameters.filter((p) => p.required && !(values[p.name] ?? "").trim()).map((p) => p.name);
}

/** German validation error, or null when the draft can be sent. With a workflow, its required parameters must be filled. */
export function validateDraft(draft: TaskDraft, workflow: Workflow | null = null): string | null {
  if (draft.workflow_id) {
    const missing = missingWorkflowParameters(workflow, draft.workflowParameters);
    if (missing.length > 0) return `Bitte ausfüllen: ${missing.join(", ")}.`;
    for (const p of workflow?.parameters ?? []) {
      const raw = (draft.workflowParameters[p.name] ?? "").trim();
      if (raw && p.kind === "number" && !Number.isFinite(Number(raw.replace(",", ".")))) return `${p.name}: bitte eine Zahl angeben.`;
    }
  } else if (!draft.goal.trim()) {
    return "Bitte ein Ziel beschreiben.";
  }
  if (draft.filesEnabled && draft.fileRoots.length === 0) return "Für den Dateizugriff mindestens einen Ordner wählen.";
  if (draft.windowsEnabled && draft.windowSourceIds.length === 0 && draft.allowedProcesses.length === 0) {
    return "Für den Fensterzugriff ein Fenster oder mindestens einen erlaubten Prozess angeben.";
  }
  if (!Number.isInteger(draft.max_steps) || draft.max_steps < 1 || draft.max_steps > 500) return "Schritte: 1 bis 500.";
  if (!Number.isInteger(draft.max_seconds) || draft.max_seconds < 10 || draft.max_seconds > 86400) return "Zeitlimit: 10 bis 86400 Sekunden.";
  return null;
}

export function draftScopes(draft: TaskDraft): Scopes {
  return {
    files: draft.filesEnabled ? { roots: draft.fileRoots, write_dir: draft.writeDir } : null,
    browser: draft.browser ? { enabled: true, allowed_origins: null } : null,
    windows: draft.windowsEnabled ? { source_ids: draft.windowSourceIds, allowed_processes: draft.allowedProcesses } : null,
    memory: draft.memory,
    external_effects: draft.external_effects,
  };
}

/** Scopes come exclusively from this request (the UI), never from model text. */
export function draftToRequest(draft: TaskDraft): CreateTaskRequest {
  return {
    goal: draft.goal.trim(),
    conversation_id: draft.conversation_id,
    live_session_id: draft.live_session_id,
    recipe_id: draft.recipe_id,
    scopes: draftScopes(draft),
    budget: { max_steps: draft.max_steps, max_seconds: draft.max_seconds },
    parameters: null,
    workflow_id: draft.workflow_id,
  };
}

/** Converts one typed parameter value by its kind (number -> number, everything else trimmed text). */
export function coerceParameterValue(kind: WorkflowParameter["kind"], raw: string): unknown {
  const text = raw.trim();
  if (kind === "number") {
    const n = Number(text.replace(",", "."));
    return Number.isFinite(n) ? n : text;
  }
  return text;
}

/**
 * Body of `POST /api/agent/workflows/{id}/run`: parameters from the draft
 * (only the workflow's own, empty optional ones omitted), scopes and budget
 * from the form, `mode: "replan"` for the comparison run.
 */
export function draftToRunRequest(draft: TaskDraft, workflow: Workflow): RunWorkflowRequest {
  const parameters: Record<string, unknown> = {};
  for (const p of workflow.parameters) {
    const raw = draft.workflowParameters[p.name] ?? "";
    if (!raw.trim()) continue;
    parameters[p.name] = coerceParameterValue(p.kind, raw);
  }
  return {
    parameters,
    scopes: draftScopes(draft),
    budget: { max_steps: draft.max_steps, max_seconds: draft.max_seconds },
    mode: draft.replan ? "replan" : "auto",
  };
}

/** Prefills a draft from a workflow ("Starten" in the workflow list): goal, scope, empty parameter values. */
export function draftFromWorkflow(base: TaskDraft, workflow: Workflow): TaskDraft {
  const workflowParameters: Record<string, string> = {};
  for (const p of workflow.parameters) workflowParameters[p.name] = "";
  return { ...draftFromScopes(base, workflow.scope), goal: workflow.purpose_de, recipe_id: null, workflow_id: workflow.id, workflowParameters, replan: false };
}

/** Prefills a draft from a recipe's scope ("Mit diesem Ablauf starten"). */
export function draftFromScopes(base: TaskDraft, scopes: Scopes): TaskDraft {
  return {
    ...base,
    filesEnabled: Boolean(scopes.files),
    fileRoots: scopes.files?.roots ?? [],
    writeDir: scopes.files?.write_dir ?? null,
    browser: Boolean(scopes.browser?.enabled),
    windowsEnabled: Boolean(scopes.windows),
    windowSourceIds: scopes.windows?.source_ids ?? [],
    allowedProcesses: scopes.windows?.allowed_processes ?? [],
    memory: scopes.memory,
    external_effects: scopes.external_effects,
  };
}

/** Short German summary of the scopes for list rows. */
export function scopeSummary(scopes: Scopes): string {
  const parts: string[] = [];
  if (scopes.files) parts.push(`Dateien (${scopes.files.roots.length})`);
  if (scopes.browser?.enabled) parts.push("Browser");
  if (scopes.windows) parts.push(`Fenster (${scopes.windows.source_ids.length + scopes.windows.allowed_processes.length})`);
  if (scopes.memory) parts.push("Gedächtnis");
  if (scopes.external_effects) parts.push("externe Wirkung");
  return parts.length ? parts.join(" · ") : "ohne Zugriffe";
}

// ----- v1.5 (0.5): execution core labels and summaries -----------------------------------

export const OUTCOME_LABEL: Record<TaskOutcome, string> = {
  fulfilled: "erfüllt",
  not_fulfilled: "nicht erfüllt",
  unclear: "ungeklärt",
};

export const OUTCOME_TONE: Record<TaskOutcome, TaskTone> = {
  fulfilled: "green",
  not_fulfilled: "red",
  unclear: "yellow",
};

export const REUSE_MODE_LABEL: Record<ReuseMode, string> = {
  reuse: "wiederverwendet",
  repair: "repariert",
  replan: "neu geplant",
  replan_forced: "ohne Wiederverwendung",
};

export const REUSE_MODE_TONE: Record<ReuseMode, TaskTone> = {
  reuse: "green",
  repair: "yellow",
  replan: "blue",
  replan_forced: "neutral",
};

export const WORKFLOW_STATUS_LABEL: Record<WorkflowStatus, string> = {
  candidate: "Kandidat",
  verified: "geprüft",
  demoted: "zurückgestuft",
  retired: "stillgelegt",
};

export const WORKFLOW_STATUS_TONE: Record<WorkflowStatus, TaskTone> = {
  candidate: "blue",
  verified: "green",
  demoted: "yellow",
  retired: "neutral",
};

export const WORKFLOW_ORIGIN_LABEL: Record<WorkflowOriginKind, string> = {
  compiled: "aus Aufgabe",
  taught: "beigebracht",
  migrated: "aus 0.3-Rezept",
};

export const DEPENDENCY_KIND_LABEL: Record<DependencyKind, string> = {
  fact: "Fakt",
  file: "Datei",
  binding: "Zuordnung",
  app: "Programm",
  tool_contract: "Werkzeugvertrag",
  model: "Modell",
  workflow: "Ablauf",
};

export const BINDING_KIND_LABEL: Record<BindingKind, string> = {
  dir: "Ordner",
  file: "Datei",
  app: "Programm",
  window: "Fenster",
  url: "Adresse",
  account: "Konto",
  model: "Modell",
};

export const STEP_KIND_LABEL: Record<StepKind, string> = {
  read: "gelesen",
  write: "geschrieben",
  check: "geprüft",
  verify: "Ergebnis geprüft",
  skip: "übersprungen",
  repair: "Reparatur",
  repaired: "repariert",
  replan: "neu geplant",
  gate: "Wiederverwendung geprüft",
  derive: "abgeleitet",
  pause: "pausiert",
};

/** "3 gültig · 1 ungültig · 2 vermutet" (zero parts omitted; "keine" without dependencies). */
export function dependencySummary(stats: WorkflowDependencyStats | null | undefined): string {
  if (!stats || stats.total === 0) return "keine Abhängigkeiten";
  const parts = [`${stats.valid} gültig`];
  if (stats.invalid > 0) parts.push(`${stats.invalid} ungültig`);
  if (stats.inferred > 0) parts.push(`${stats.inferred} vermutet`);
  return parts.join(" · ");
}

/** Dependencies changed since the last run: the gate will check / repair on the next start. */
export function isStaleWorkflow(workflow: Workflow): boolean {
  return (workflow.stats?.dependencies?.invalid ?? 0) > 0;
}

/** 0.853 -> "85 %" */
export function formatCoverage(coverage: number | null | undefined): string {
  if (coverage === null || coverage === undefined || !Number.isFinite(coverage)) return "–";
  return `${Math.round(Math.max(0, Math.min(1, coverage)) * 100)} %`;
}

/** "4 Schritte" / "4 Schritte (1 abgeleitet)" */
export function stepCountText(workflow: Workflow): string {
  const n = workflow.steps.length;
  const derived = workflow.derived_steps?.length ?? 0;
  const base = n === 1 ? "1 Schritt" : `${n} Schritte`;
  return derived > 0 ? `${base} (${derived} abgeleitet)` : base;
}

/** Workflows the list shows first: usable ones by name, retired ones last. */
export function sortWorkflows(list: Workflow[]): Workflow[] {
  const rank: Record<WorkflowStatus, number> = { verified: 0, candidate: 1, demoted: 2, retired: 3 };
  return [...list].sort((a, b) => rank[a.status] - rank[b.status] || a.name.localeCompare(b.name, "de"));
}

export function upsertWorkflow(list: Workflow[], workflow: Workflow): Workflow[] {
  const idx = list.findIndex((w) => w.id === workflow.id);
  return sortWorkflows(idx === -1 ? [workflow, ...list] : list.map((w) => (w.id === workflow.id ? workflow : w)));
}

/** Number of steps a capture added (cores answer with a count or with the list). */
export function countNewSteps(value: unknown): number {
  if (typeof value === "number") return value;
  if (Array.isArray(value)) return value.length;
  return 0;
}

// ----- teach: recorded values that may become parameters ----------------------------------

export interface TeachValue {
  /** Index of the recorded step. */
  step: number;
  tool: string;
  /** Argument key holding the value (`text`, `value`, `option`). */
  key: string;
  value: string;
  /** Element the value went into (for the label). */
  target: string | null;
}

const VALUE_TOOL_RE = /(^|[._])(fill|select|set_value|type|input)($|[._])/i;
const VALUE_KEYS = ["text", "value", "option", "label"] as const;

/** Values the user typed or chose during a demonstration - each can be marked "als Parameter". */
export function teachValues(session: TeachSession | null | undefined): TeachValue[] {
  if (!session) return [];
  const out: TeachValue[] = [];
  session.steps.forEach((s, step) => {
    if (!VALUE_TOOL_RE.test(s.tool)) return;
    for (const key of VALUE_KEYS) {
      const v = s.arguments?.[key];
      if (typeof v === "string" && v.trim()) {
        out.push({ step, tool: s.tool, key, value: v, target: s.target?.name ?? null });
        break;
      }
    }
  });
  return out;
}

/** Suggests a parameter name from the element name ("E-Mail Adresse" -> "e_mail_adresse"). */
export function suggestParameterName(label: string | null, fallback: string): string {
  const base = (label ?? "")
    .toLowerCase()
    .replace(/ä/g, "ae")
    .replace(/ö/g, "oe")
    .replace(/ü/g, "ue")
    .replace(/ß/g, "ss")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return base || fallback;
}
