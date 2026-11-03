/**
 * Pure reducer for activity events (docs/portable/ACTIVITY_EVENTS.md).
 *
 * One ActivityTrack per request (= assistant message id). Events are accepted
 * only when their request_id matches the track and their seq is higher than
 * the last one seen. Nothing here invents activity: every text shown comes
 * from `message_de` or from measured counters of the events themselves.
 */
import type { ActivityEvent, ActivityPhase } from "../api/types";
import { formatSeconds, formatTps } from "../lib/format";

export type ActivityOutcome = "live" | "done" | "cancelled" | "failed";
export type PostprocessState = "pending" | "running" | "done" | "failed" | null;

export interface ActivityTrack {
  requestId: string;
  /** Local wall-clock time of the first accepted event (for the elapsed counter). */
  startedAt: number;
  lastSeq: number;
  events: ActivityEvent[];
  /** Latest accepted event. */
  current: ActivityEvent;
  /** answer_done (done/cancelled) or the failing event. */
  answer: ActivityEvent | null;
  outcome: ActivityOutcome;
  /** Last `generating` event that carried counters (for Token/s). */
  lastGenerating: ActivityEvent | null;
  saved: boolean;
  postprocess: PostprocessState;
}

export const MAX_EVENTS_PER_TRACK = 200;

export function createTrack(requestId: string, first: ActivityEvent, now: number): ActivityTrack {
  const base: ActivityTrack = {
    requestId,
    startedAt: now,
    lastSeq: -1,
    events: [],
    current: first,
    answer: null,
    outcome: "live",
    lastGenerating: null,
    saved: false,
    postprocess: null,
  };
  return reduceActivity(base, first);
}

/** Returns the same object when the event is discarded (stale seq, foreign request). */
export function reduceActivity(track: ActivityTrack, ev: ActivityEvent): ActivityTrack {
  if (ev.request_id !== track.requestId) return track;
  if (typeof ev.seq !== "number" || ev.seq <= track.lastSeq) return track;

  const events = track.events.length >= MAX_EVENTS_PER_TRACK ? [...track.events.slice(1), ev] : [...track.events, ev];
  const next: ActivityTrack = { ...track, events, lastSeq: ev.seq, current: ev };

  const failed = ev.state === "failed" || ev.phase === "failed";
  if (failed && track.outcome === "live") {
    next.outcome = "failed";
    next.answer = ev;
  }
  switch (ev.phase as ActivityPhase) {
    case "generating":
      if (ev.counters && !failed) next.lastGenerating = ev;
      break;
    case "answer_done":
      if (track.outcome === "live") {
        next.outcome = ev.state === "cancelled" ? "cancelled" : "done";
        next.answer = ev;
      }
      break;
    case "saved":
      if (ev.state === "done") next.saved = true;
      break;
    case "postprocess_pending":
      next.postprocess = ev.state === "failed" ? "failed" : "pending";
      break;
    case "postprocess_running":
      next.postprocess = ev.state === "failed" ? "failed" : "running";
      break;
    case "postprocess_done":
      next.postprocess = ev.state === "failed" ? "failed" : "done";
      break;
    default:
      if (ev.state === "cancelled" && track.outcome === "live") {
        next.outcome = "cancelled";
        next.answer = ev;
      }
      break;
  }
  return next;
}

/** Percent of prompt processing, only for that phase and only with a known total. */
export function promptPercent(ev: ActivityEvent | null): number | null {
  if (!ev || ev.phase !== "prompt_processing" || !ev.counters) return null;
  const total = ev.counters.prompt_total;
  const done = ev.counters.prompt_processed;
  if (typeof total !== "number" || total <= 0 || typeof done !== "number") return null;
  return Math.max(0, Math.min(100, Math.floor((done / total) * 100)));
}

/** Text for the live line: message_de plus measured percent / Token/s. */
export function stepText(ev: ActivityEvent): string {
  let text = ev.message_de || ev.phase;
  const pct = promptPercent(ev);
  if (pct !== null && !/%/.test(text)) text += ` (${pct} %)`;
  if (ev.phase === "generating" && typeof ev.counters?.tokens_per_second === "number" && ev.counters.tokens_per_second > 0 && !/Token\/s/.test(text)) {
    text += ` · ${formatTps(ev.counters.tokens_per_second)}`;
  }
  return text;
}

export const POSTPROCESS_LABEL: Record<NonNullable<PostprocessState>, string> = {
  pending: "Gedächtnis-Nachverarbeitung ausstehend",
  running: "Gedächtnis wird aktualisiert",
  done: "Gedächtnis aktualisiert",
  failed: "Gedächtnis-Nachverarbeitung fehlgeschlagen",
};

export interface ActivitySummary {
  /** Main text, e.g. "Antwort fertig · 3,8 s · 24 Token/s". */
  text: string;
  /** Memory state shown after the main text (saved / post-processing). */
  memory: string | null;
  outcome: ActivityOutcome;
}

function tokensPerSecond(track: ActivityTrack): number | null {
  const fromAnswer = track.answer?.counters?.tokens_per_second;
  if (typeof fromAnswer === "number" && fromAnswer > 0) return fromAnswer;
  const fromGen = track.lastGenerating?.counters?.tokens_per_second;
  if (typeof fromGen === "number" && fromGen > 0) return fromGen;
  return null;
}

/** Collapsed one-line summary once the answer is done, cancelled or failed. */
export function summarizeActivity(track: ActivityTrack): ActivitySummary {
  const memoryParts: string[] = [];
  if (track.postprocess) memoryParts.push(POSTPROCESS_LABEL[track.postprocess]);
  else if (track.saved) memoryParts.push("Gespräch gespeichert");
  const memory = memoryParts.length ? memoryParts.join(" · ") : null;

  if (track.outcome === "live") return { text: stepText(track.current), memory, outcome: "live" };
  if (track.outcome === "failed") {
    const msg = track.answer?.message_de || "Unbekannter Fehler";
    return { text: /^Fehlgeschlagen/i.test(msg) ? msg : `Fehlgeschlagen: ${msg}`, memory, outcome: "failed" };
  }
  const parts: string[] = [track.outcome === "cancelled" ? "Abgebrochen" : "Antwort fertig"];
  const t = track.answer?.t_ms;
  if (typeof t === "number" && t >= 0) parts.push(formatSeconds(t));
  const tps = tokensPerSecond(track);
  if (tps !== null) parts.push(formatTps(tps));
  return { text: parts.join(" · "), memory, outcome: track.outcome };
}

/** German labels for the phase column of the details list. */
export const ACTIVITY_PHASE_LABEL: Record<ActivityPhase, string> = {
  queued: "Wartet auf das Modell",
  model_loading: "Modell wird geladen",
  memory_search: "Gedächtnissuche",
  memory_reconcile: "Angaben abgleichen",
  prompt_build: "Kontext vorbereiten",
  prompt_processing: "Kontext verarbeiten",
  generating: "Antwort schreiben",
  answer_done: "Antwort",
  saved: "Speichern",
  postprocess_pending: "Nachverarbeitung ausstehend",
  postprocess_running: "Nachverarbeitung läuft",
  postprocess_done: "Nachverarbeitung fertig",
  failed: "Fehler",
  // v1.2 (live sessions)
  capture: "Quelle aufnehmen",
  observe: "Struktur lesen",
  image_encode: "Bild vorbereiten",
};

export const ACTIVITY_STATE_LABEL: Record<ActivityEvent["state"], string> = {
  started: "gestartet",
  progress: "läuft",
  done: "fertig",
  failed: "fehlgeschlagen",
  cancelled: "abgebrochen",
};

export const COUNTER_LABEL: Record<string, string> = {
  sources: "Quellen",
  facts_pending: "offene Fakten",
  prompt_total: "Kontext-Token",
  prompt_processed: "verarbeitet",
  prompt_cached: "aus Cache",
  tokens: "Token",
  tokens_per_second: "Token/s",
};

export function phaseLabel(phase: string): string {
  return (ACTIVITY_PHASE_LABEL as Record<string, string>)[phase] ?? phase;
}
