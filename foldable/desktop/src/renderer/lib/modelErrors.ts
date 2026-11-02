/**
 * German texts for model-related error codes (import jobs, resolve, download).
 * `detail` from the core always wins when present; the mapping adds context
 * the user can act on. No workaround is ever suggested for gated models.
 */
import { isApiError } from "../api/client";

export const IMPORT_ERROR_LABEL: Record<string, string> = {
  gguf_invalid: "Die Datei ist keine gültige GGUF-Datei.",
  arch_unsupported: "Diese Modellarchitektur wird von der mitgelieferten llama.cpp-Laufzeit nicht unterstützt.",
  role_mismatch: "Die Datei passt nicht zur erwarteten Rolle (Chat- oder Embedding-Modell).",
  copy_failed: "Die Datei konnte nicht nach data/models kopiert werden.",
  split_incomplete:
    "Die GGUF-Datei ist Teil eines mehrteiligen Modells; es fehlen Teile. Bitte alle Teile (…-00001-of-0000N.gguf) in denselben Ordner legen.",
  not_chat_model: "Diese Datei ist kein Chat-Modell (z. B. Basis-, Embedding- oder Vision-Modell) und kann hier nicht als Chat-Modell verwendet werden.",
};

/** Text for a failed import/download/activate job. */
export function jobErrorText(error: { code: string; detail?: string | null } | null | undefined, fallback = "Vorgang fehlgeschlagen."): string {
  if (!error) return fallback;
  const label = IMPORT_ERROR_LABEL[error.code];
  if (label && error.detail && error.detail !== label) return `${error.detail} ${label}`;
  return error.detail || label || fallback;
}

export interface ResolveErrorInfo {
  code: string;
  /** Headline for the error box. */
  title: string;
  /** Explanation from the core (detail) or local fallback. */
  text: string;
  /** Account / approval required on Hugging Face (409 gated). */
  gated: boolean;
}

export const RESOLVE_ERROR_LABEL: Record<string, string> = {
  gated:
    "Der Zugriff auf dieses Modell erfordert ein Hugging-Face-Konto und die Zustimmung zu den Nutzungsbedingungen des Herstellers. SpAIder lädt nur frei zugängliche Dateien.",
  not_found: "Das Repository oder die Datei wurde auf Hugging Face nicht gefunden.",
  no_gguf: "Dieses Repository enthält keine GGUF-Dateien.",
  split_incomplete: "Das mehrteilige Modell ist unvollständig – nicht alle Teile sind im Repository vorhanden.",
  offline: "Keine Netzwerkverbindung – die Prüfung ist gerade nicht möglich.",
  network_error: "Keine Netzwerkverbindung – die Prüfung ist gerade nicht möglich.",
  invalid_repo: "Bitte eine Repository-ID im Format „Autor/Name“ eingeben.",
};

export function resolveErrorInfo(e: unknown): ResolveErrorInfo {
  if (isApiError(e)) {
    const gated = e.code === "gated" || e.extra.requires_account === true;
    const label = RESOLVE_ERROR_LABEL[e.code];
    const text = e.detail && e.detail !== `HTTP ${e.status}` ? (label && gated ? `${e.detail} ${label}` : e.detail) : (label ?? e.detail);
    return {
      code: e.code,
      title: gated ? "Modell erfordert ein Konto" : "Prüfung nicht möglich",
      text,
      gated,
    };
  }
  const message = e instanceof Error ? e.message : String(e);
  return { code: "unknown", title: "Prüfung nicht möglich", text: message, gated: false };
}
