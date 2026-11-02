/**
 * German labels for technical identifiers of the API.
 */
import type { DocumentStatus, FitLevel, JobKind, JobPhase, JobState, RuntimeState, Source } from "../api/types";

export const JOB_PHASE_LABEL: Record<Exclude<JobPhase, null>, string> = {
  check_space: "Speicherplatz wird geprüft …",
  download: "Wird heruntergeladen …",
  verify: "Prüfsumme wird geprüft …",
  copy: "Datei wird kopiert …",
  load: "Modell wird geladen …",
  probe: "Modell wird geprüft …",
  commit: "Wird übernommen …",
  cleanup: "Aufräumen …",
  rollback: "Wird zurückgesetzt – vorheriges Modell bleibt aktiv …",
};

export const JOB_KIND_LABEL: Record<JobKind, string> = {
  download: "Download",
  import: "Import",
  activate: "Aktivierung",
  delete: "Löschen",
};

export const JOB_STATE_LABEL: Record<JobState, string> = {
  queued: "Wartet",
  running: "Läuft",
  paused: "Pausiert",
  done: "Fertig",
  failed: "Fehlgeschlagen",
  cancelled: "Abgebrochen",
};

export function jobPhaseText(phase: JobPhase, kind: JobKind): string {
  if (phase && JOB_PHASE_LABEL[phase]) {
    if (kind === "activate" && (phase === "load" || phase === "probe")) return "Modell wird geladen und geprüft …";
    return JOB_PHASE_LABEL[phase];
  }
  return `${JOB_KIND_LABEL[kind]} …`;
}

export const RUNTIME_STATE_LABEL: Record<RuntimeState, string> = {
  stopped: "Laufzeit gestoppt",
  starting: "Laufzeit startet …",
  ready: "Bereit",
  error: "Laufzeit-Fehler",
};

export const FIT_LABEL: Record<FitLevel, string> = {
  recommended: "Empfohlen",
  ok: "Passt",
  tight: "Knapp",
  too_large: "Zu groß",
  unknown: "Unbekannt",
};

export const DOCUMENT_STATUS_LABEL: Record<DocumentStatus, string> = {
  imported: "Importiert",
  indexing: "Wird indexiert …",
  indexed: "Indexiert",
  failed: "Fehlgeschlagen",
};

export const SOURCE_STATUS_LABEL: Record<Source["status"], string> = {
  current: "aktuell",
  superseded: "veraltet (ersetzt)",
  uncertain: "unsicher",
};

export const SOURCE_KIND_LABEL: Record<Source["kind"], string> = {
  fact: "Fakt",
  document: "Dokument",
  note: "Notiz",
  message: "Nachricht",
};

/** German fallback texts for document warning codes without message_de. */
export const DOCUMENT_WARNING_LABEL: Record<string, string> = {
  no_text_layer: "Kein Textinhalt erkannt (gescannte PDF?)",
};

export function documentWarningText(w: { code: string; message_de?: string }): string {
  return w.message_de || DOCUMENT_WARNING_LABEL[w.code] || `Hinweis: ${w.code}`;
}

/** German texts for the 409 codes a chat turn can produce. */
export const CHAT_ERROR_LABEL: Record<string, string> = {
  generation_running: "Es läuft bereits eine Antwort. Bitte warten oder stoppen.",
  no_model: "Kein Chat-Modell aktiv. Bitte unter Einstellungen → Modelle ein Modell aktivieren.",
  runtime_not_ready: "Die Laufzeit ist noch nicht bereit. Bitte kurz warten.",
};
