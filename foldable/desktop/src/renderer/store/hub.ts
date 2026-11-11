/**
 * Pure logic for the model library and the provider connections (API contract
 * Addendum v1.3): German labels, the connection form draft and its validation,
 * the four test stages, capability chips, the chat binding label, the consent
 * request derived from a `consent_required` error and discover pagination.
 * Nothing here talks to the network.
 */
import type {
  AuthMethod,
  BindingResponse,
  CapabilityValue,
  ChatErrorEvent,
  Connection,
  ConnectionModel,
  ConnectionTestResult,
  CreateConnectionRequest,
  CredentialStoreStatus,
  DataSharing,
  DiscoverItem,
  DiscoverQuery,
  DiscoverResponse,
  Execution,
  ProviderPreset,
  RepoVariant,
  SharingEvent,
} from "../api/types";

export type Tone = "neutral" | "blue" | "green" | "yellow" | "red" | "purple" | "cyan";

export const EXECUTION_LABEL: Record<Execution, string> = {
  local: "lokal",
  lan: "eigener Server",
  cloud: "Cloud",
  unknown: "unbekannt",
};

export const EXECUTION_TONE: Record<Execution, Tone> = { local: "green", lan: "blue", cloud: "yellow", unknown: "neutral" };

export const EXECUTION_OPTIONS: { value: Execution; label: string; description: string }[] = [
  { value: "local", label: "Lokal (dieser Rechner)", description: "Der Server läuft auf diesem Rechner." },
  { value: "lan", label: "Eigener Server im LAN", description: "Ein Rechner im eigenen Netz; Daten verlassen das Netz nicht." },
  { value: "cloud", label: "Cloud", description: "Ein Dienst außerhalb des eigenen Netzes." },
  { value: "unknown", label: "Unbekannt", description: "Nicht sicher – wird wie Cloud behandelt (Freigabe nötig)." },
];

export const DATA_SHARING_LABEL: Record<DataSharing, string> = { allow: "immer erlauben", ask: "vorher fragen", deny: "nie" };
export const DATA_SHARING_OPTIONS: { value: DataSharing; label: string; description: string }[] = [
  { value: "ask", label: "Vorher fragen", description: "Vor dem ersten Senden je Projekt wird eine Freigabe eingeholt (Standard)." },
  { value: "allow", label: "Immer erlauben", description: "Inhalte dürfen ohne Rückfrage an diese Verbindung gehen." },
  { value: "deny", label: "Nie", description: "Die Verbindung kann eingerichtet, aber nicht für Gespräche genutzt werden." },
];

export type StorageChoice = "host" | "vault" | "session";
export const STORAGE_OPTIONS: { value: StorageChoice; label: string; description: string }[] = [
  {
    value: "host",
    label: "An diesen Rechner gebunden",
    description: "Standard: verschlüsselt mit Windows (DPAPI). Auf einem anderen Rechner ist der Schlüssel nicht lesbar und muss neu eingegeben werden.",
  },
  {
    value: "vault",
    label: "Portabler Tresor",
    description: "Verschlüsselte Datei vault.json auf dem Datenträger (Argon2id + AES-256-GCM); wird pro Sitzung mit einem Passwort entsperrt und wandert mit.",
  },
  {
    value: "session",
    label: "Nur diese Sitzung",
    description: "Bleibt nur im Arbeitsspeicher des Kerns und ist nach dem Beenden weg.",
  },
];

export const AUTH_LABEL: Record<AuthMethod, string> = {
  none: "Ohne Zugangsdaten",
  api_key: "API-Schlüssel",
  oauth_siwc: "Anmeldung mit ChatGPT-Konto",
};

export const CAPABILITY_LABEL: Record<CapabilityValue, string> = { declared: "laut Anbieter", verified: "geprüft", unknown: "unbekannt", no: "nein" };
export const CAPABILITY_TONE: Record<CapabilityValue, Tone> = { declared: "blue", verified: "green", unknown: "neutral", no: "red" };
export const CAPABILITY_KEYS = ["text", "vision", "tools", "structured_output"] as const;
export type CapabilityKey = (typeof CAPABILITY_KEYS)[number];
export const CAPABILITY_KEY_LABEL: Record<CapabilityKey, string> = { text: "Text", vision: "Bild", tools: "Werkzeuge", structured_output: "Strukturierte Ausgabe" };

export const PROTOCOL_LABEL: Record<string, string> = {
  llama_local: "eingebettete Laufzeit",
  chat_completions: "OpenAI-kompatibel (chat/completions)",
  responses: "OpenAI Responses",
  anthropic_messages: "Anthropic Messages",
};

// ----- connection form draft ----------------------------------------------------------

export interface ConnectionDraft {
  provider: string;
  name: string;
  base_url: string;
  auth_method: AuthMethod;
  api_key: string;
  credential_storage: StorageChoice;
  execution: Execution;
  data_sharing: DataSharing;
  default_model: string;
}

/** The preset's base URL may be edited ("Eigener Server" always needs one). */
export function needsBaseUrl(preset: ProviderPreset): boolean {
  return preset.base_url_editable || preset.id === "openai_compatible";
}

/** Only the methods the preset declares are offered. */
export function visibleAuthMethods(preset: ProviderPreset): AuthMethod[] {
  return preset.auth_methods.length ? preset.auth_methods : ["api_key"];
}

/** Own servers (editable base URL) ask where they run; cloud presets are fixed. */
export function asksExecution(preset: ProviderPreset): boolean {
  return needsBaseUrl(preset) || preset.execution === "unknown";
}

export function draftForPreset(preset: ProviderPreset, store: CredentialStoreStatus | null): ConnectionDraft {
  const methods = visibleAuthMethods(preset);
  return {
    provider: preset.id,
    name: preset.name.replace(/\s*\(.*\)\s*$/, "").trim() || preset.name,
    base_url: preset.base_url,
    auth_method: methods[0],
    api_key: "",
    credential_storage: store && !store.host_available ? "vault" : "host",
    execution: preset.execution,
    data_sharing: preset.execution === "local" ? "allow" : "ask",
    default_model: "",
  };
}

/** German validation error for the draft, or null when it can be sent. */
export function validateConnectionDraft(draft: ConnectionDraft, preset: ProviderPreset | null, store: CredentialStoreStatus | null, editing = false): string | null {
  if (!preset) return "Bitte einen Anbieter wählen.";
  if (preset.id === "llama_local") return "Die eingebettete Laufzeit braucht keine Verbindung.";
  if (!draft.name.trim()) return "Bitte einen Namen vergeben.";
  if (!visibleAuthMethods(preset).includes(draft.auth_method)) return `Zugangsmethode „${AUTH_LABEL[draft.auth_method]}“ ist für ${preset.name} nicht vorgesehen.`;
  if (needsBaseUrl(preset)) {
    const url = draft.base_url.trim();
    if (!url) return "Serveradresse fehlt.";
    if (!/^https?:\/\/\S+$/i.test(url)) return "Serveradresse muss mit http:// oder https:// beginnen.";
    if (/^http:\/\//i.test(url) && draft.execution === "cloud") return "Cloud-Server nur über https://.";
  }
  if (draft.auth_method === "api_key" && (draft.api_key.trim() || !editing)) {
    if (!draft.api_key.trim()) return "API-Schlüssel fehlt.";
    if (draft.credential_storage === "host" && store && !store.host_available) return "Host-gebundene Ablage ist auf diesem System nicht verfügbar – bitte Tresor oder Sitzung wählen.";
    if (draft.credential_storage === "vault" && store && !store.vault.unlocked) return "Der Tresor ist gesperrt – zuerst anlegen bzw. entsperren.";
  }
  return null;
}

/** The request body for `POST /connections`; the key is never kept in the draft afterwards. */
export function draftToRequest(draft: ConnectionDraft, preset: ProviderPreset): CreateConnectionRequest {
  const body: CreateConnectionRequest = {
    provider: draft.provider,
    name: draft.name.trim(),
    auth_method: draft.auth_method,
    credential_storage: draft.credential_storage,
    data_sharing: draft.data_sharing,
    default_model: draft.default_model.trim() || null,
  };
  if (needsBaseUrl(preset)) body.base_url = draft.base_url.trim();
  if (draft.auth_method === "api_key") body.api_key = draft.api_key;
  if (asksExecution(preset)) body.execution = draft.execution;
  return body;
}

// ----- connection test stages ----------------------------------------------------------

export type StageState = "ok" | "failed" | "skipped" | "pending";
export type StageId = "server_reached" | "access_ok" | "model_listed" | "answer_ok";
export interface TestStage {
  id: StageId;
  label: string;
  state: StageState;
  note: string | null;
}

export const STAGE_LABEL: Record<StageId, string> = {
  server_reached: "Server erreicht",
  access_ok: "Zugang geprüft",
  model_listed: "Modell gelistet",
  answer_ok: "Probe-Antwort",
};

/** The four stages as shown in the UI; `null` results are "übersprungen", never "ok". */
export function testStages(result: ConnectionTestResult | null, allowGeneration: boolean): TestStage[] {
  const stage = (id: StageId, value: boolean | null | undefined, note: string | null = null): TestStage => ({
    id,
    label: STAGE_LABEL[id],
    state: result === null ? "pending" : value === true ? "ok" : value === false ? "failed" : "skipped",
    note,
  });
  if (!result) return (["server_reached", "access_ok", "model_listed", "answer_ok"] as StageId[]).map((id) => stage(id, null));
  const err = result.error;
  const failedNote = err ? `${err.message_de}${err.status ? ` (HTTP ${err.status})` : ""}` : null;
  return [
    stage("server_reached", result.server_reached, result.server_reached ? `${result.latency_ms} ms` : failedNote),
    stage("access_ok", result.access_ok, result.access_ok === false ? failedNote : result.access_ok === null ? "nicht prüfbar" : null),
    stage("model_listed", result.model_listed, result.model_listed ? `${result.models.length} Modell(e)` : result.model_listed === null ? "übersprungen" : "keine Modelle"),
    stage(
      "answer_ok",
      result.answer_ok,
      result.answer_ok === null
        ? allowGeneration
          ? "nicht ausgeführt"
          : "nicht angefordert"
        : typeof result.details.probe_answer === "string"
          ? `„${result.details.probe_answer}“`
          : null,
    ),
  ];
}

// ----- capability chips -------------------------------------------------------------------

export interface CapabilityChip {
  key: CapabilityKey;
  label: string;
  value: CapabilityValue;
}

function declaredValue(v: unknown): CapabilityValue {
  if (v === true || v === "declared") return "declared";
  if (v === "verified") return "verified";
  if (v === false || v === "no") return "no";
  return "unknown";
}

/** Verified/declared/unknown/no per capability; a profile wins over the provider's declaration. */
export function capabilityChips(model: ConnectionModel): CapabilityChip[] {
  return CAPABILITY_KEYS.map((key) => {
    const profile = model.capabilities?.[key];
    const value: CapabilityValue = profile ?? declaredValue(model.declared?.[key]);
    return { key, label: CAPABILITY_KEY_LABEL[key], value };
  });
}

// ----- chat binding -----------------------------------------------------------------------

export interface BindingLabel {
  kind: "local" | "connection";
  title: string;
  execution: Execution;
  modelId: string | null;
  connectionName: string | null;
}

/** What the chat header shows for the active conversation. */
export function bindingLabel(res: BindingResponse | null, localModelName: string | null): BindingLabel {
  if (!res || res.binding.kind === "local") {
    return { kind: "local", title: localModelName ?? "Lokales Modell", execution: "local", modelId: res?.binding.local_model_id ?? null, connectionName: null };
  }
  const name = res.connection?.name ?? "Verbindung";
  const model = res.binding.provider_model_id ?? res.connection?.default_model ?? null;
  return {
    kind: "connection",
    title: model ? `${name} · ${model}` : name,
    execution: res.connection?.execution ?? "unknown",
    modelId: model,
    connectionName: name,
  };
}

/** Connections offered in the binding picker (enabled ones only). */
export function selectableConnections(list: Connection[]): Connection[] {
  return list.filter((c) => c.enabled);
}

// ----- consent flow -------------------------------------------------------------------------

export interface ConsentRequest {
  conversationId: string;
  /** The text to send again after the consent was granted. */
  text: string;
  connectionId: string;
  connectionName: string;
  projectId: string;
  projectName: string;
  execution: Execution;
  sharing: SharingEvent | null;
  error: ChatErrorEvent;
}

/** A `consent_required` error becomes a consent dialog; any other error does not. */
export function consentFromError(
  conversationId: string,
  text: string,
  error: ChatErrorEvent,
  sharing: SharingEvent | null,
  binding: BindingResponse | null,
): ConsentRequest | null {
  if (error.code !== "consent_required" || error.needs_consent !== true) return null;
  const connectionId = error.connection_id ?? sharing?.connection_id ?? binding?.connection?.id ?? binding?.binding.connection_id ?? null;
  const projectId = error.project_id ?? sharing?.project_id ?? binding?.project?.id ?? "default";
  if (!connectionId) return null;
  return {
    conversationId,
    text,
    connectionId,
    connectionName: sharing?.connection_name ?? binding?.connection?.name ?? "Verbindung",
    projectId,
    projectName: binding?.project?.name ?? "Allgemein",
    execution: sharing?.execution ?? binding?.connection?.execution ?? "unknown",
    sharing,
    error,
  };
}

/** Lines of "was gesendet wird" for the consent dialog (counts only, no content). */
export function sharingSummary(sharing: SharingEvent | null): string[] {
  if (!sharing) return ["Die Nachricht und der bisherige Gesprächsverlauf dieses Gesprächs."];
  const lines = [`${sharing.history_messages} Nachricht(en) des Gesprächsverlaufs`];
  if (sharing.system_prompt) lines.push("die Systemanweisung von SpAIder");
  if (sharing.memory_chars > 0) lines.push(`${sharing.memory_chars.toLocaleString("de-DE")} Zeichen aus dem Gedächtnis`);
  if (sharing.sources > 0) lines.push(`${sharing.sources} Quelle(n) aus Dokumenten`);
  if (sharing.images > 0) lines.push(`${sharing.images} Bild(er)`);
  return lines;
}

// ----- discover pagination --------------------------------------------------------------------

export const DEFAULT_DISCOVER_QUERY: DiscoverQuery = { q: "", author: "", task: "any", gguf_only: true, sort: "downloads" };

export const DISCOVER_TASK_OPTIONS: { value: DiscoverQuery["task"]; label: string }[] = [
  { value: "any", label: "Alle Aufgaben" },
  { value: "text-generation", label: "Text" },
  { value: "image-text-to-text", label: "Text + Bild" },
];
export const DISCOVER_SORT_OPTIONS: { value: DiscoverQuery["sort"]; label: string }[] = [
  { value: "downloads", label: "Downloads" },
  { value: "likes", label: "Likes" },
  { value: "updated", label: "Zuletzt geändert" },
  { value: "trending", label: "Im Trend" },
];

/** Appends a page without duplicates (by repo); a fresh query replaces the list. */
export function mergeDiscoverPage(prev: DiscoverItem[], page: DiscoverResponse, append: boolean): DiscoverItem[] {
  if (!append) return page.items;
  const seen = new Set(prev.map((i) => i.repo));
  return [...prev, ...page.items.filter((i) => !seen.has(i.repo))];
}

export function sameDiscoverQuery(a: DiscoverQuery, b: DiscoverQuery): boolean {
  return a.q.trim() === b.q.trim() && a.author.trim() === b.author.trim() && a.task === b.task && a.gguf_only === b.gguf_only && a.sort === b.sort;
}

export function parseRepo(repo: string): { author: string; name: string } | null {
  const m = /^([\w.-]+)\/([\w.-]+)$/.exec(repo.trim());
  return m ? { author: m[1], name: m[2] } : null;
}

// ----- repository variants ----------------------------------------------------------------------

export function groupVariants(variants: RepoVariant[]): { weights: RepoVariant[]; projectors: RepoVariant[]; auxiliary: RepoVariant[] } {
  return {
    weights: variants.filter((v) => v.kind === "weights"),
    projectors: variants.filter((v) => v.kind === "projector"),
    auxiliary: variants.filter((v) => v.kind === "auxiliary"),
  };
}

export const FIT_TEXT: Record<Exclude<RepoVariant["fit"], null>, string> = { ok: "passt", tight: "knapp", too_large: "zu groß" };
export const FIT_TONE: Record<Exclude<RepoVariant["fit"], null>, Tone> = { ok: "green", tight: "yellow", too_large: "red" };

/** Whether the chosen variant can be downloaded; otherwise the German reason. */
export function variantDownloadState(variant: RepoVariant | null, projectorKey: string | null, installed: boolean): { can: boolean; reason: string | null } {
  if (!variant) return { can: false, reason: "Bitte eine Variante wählen." };
  if (installed) return { can: false, reason: "Dieses Repository ist bereits installiert." };
  if (variant.kind !== "weights") return { can: false, reason: "Nicht unterstützt: nur Modellgewichte können als Chatmodell geladen werden (Projektor/Hilfsdateien gehören zu einer Gewichtsdatei)." };
  if (variant.fit === "too_large") return { can: false, reason: "Zu groß für den Arbeitsspeicher dieses Rechners." };
  if (variant.needs_projector && !projectorKey) return { can: false, reason: "Dieses Modell braucht einen Projektor – bitte einen wählen." };
  return { can: true, reason: null };
}

/** The file of a variant that `POST /models/resolve` gets (the first part of split files). */
export function variantPrimaryFile(variant: RepoVariant): string | null {
  const sorted = [...variant.files].sort((a, b) => (a.split_index ?? 0) - (b.split_index ?? 0));
  return sorted[0]?.path ?? null;
}
