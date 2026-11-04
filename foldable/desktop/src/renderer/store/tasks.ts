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
  CreateTaskRequest,
  PendingQuestion,
  Scopes,
  Task,
  TaskState,
  TaskStreamEvent,
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
}

export function emptyFeed(actions: Action[] = []): TaskFeed {
  return { actions: [...actions].sort((a, b) => a.seq - b.seq), notes: "", question: null, done: false, activity: null };
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

/** German validation error, or null when the draft can be sent. */
export function validateDraft(draft: TaskDraft): string | null {
  if (!draft.goal.trim()) return "Bitte ein Ziel beschreiben.";
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
  };
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
