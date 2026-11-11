import { describe, expect, it } from "vitest";
import type {
  BindingResponse,
  ChatErrorEvent,
  Connection,
  ConnectionModel,
  ConnectionTestResult,
  DiscoverItem,
  DiscoverResponse,
  ProviderPreset,
  RepoVariant,
  SharingEvent,
} from "../src/renderer/api/types";
import {
  bindingLabel,
  capabilityChips,
  consentFromError,
  DEFAULT_DISCOVER_QUERY,
  draftForPreset,
  draftToRequest,
  groupVariants,
  mergeDiscoverPage,
  needsBaseUrl,
  parseRepo,
  sameDiscoverQuery,
  sharingSummary,
  testStages,
  validateConnectionDraft,
  variantDownloadState,
  variantPrimaryFile,
  visibleAuthMethods,
} from "../src/renderer/store/hub";

const preset = (over: Partial<ProviderPreset> = {}): ProviderPreset => ({
  id: "deepseek",
  name: "DeepSeek API",
  protocol: "chat_completions",
  base_url: "https://api.deepseek.com",
  base_url_editable: false,
  auth_methods: ["api_key"],
  execution: "cloud",
  capabilities: { text: "declared", vision: "no", tools: "declared", structured_output: "declared", streaming: "declared", reasoning: "declared", notes_de: "" },
  key_url: "https://platform.deepseek.com/api_keys",
  docs_url: "",
  subscription_note_de: "",
  data_note_de: "Anfragen gehen an DeepSeek.",
  free_access_check: true,
  connections: [],
  ...over,
});
const ownServer = preset({ id: "openai_compatible", name: "Eigener Server (OpenAI-kompatibel)", base_url: "", base_url_editable: true, auth_methods: ["none", "api_key"], execution: "unknown" });
const store = { host_available: true, vault: { exists: false, unlocked: false, path: "x", kdf: null }, session_items: 0 };

const connection = (over: Partial<Connection> = {}): Connection => ({
  id: "conn_1",
  provider: "deepseek",
  protocol: "chat_completions",
  name: "DeepSeek",
  base_url: "https://api.deepseek.com",
  auth_method: "api_key",
  has_credential: true,
  credential_storage: "host",
  account_label: null,
  workspace: null,
  execution: "cloud",
  data_sharing: "ask",
  default_model: "deepseek-chat",
  extra: {},
  enabled: true,
  last_test: null,
  created_at: "2026-11-11T10:00:00Z",
  updated_at: "2026-11-11T10:00:00Z",
  ...over,
});

describe("connection form", () => {
  it("offers only the preset's auth methods and asks for a base URL only for own servers", () => {
    expect(visibleAuthMethods(preset())).toEqual(["api_key"]);
    expect(visibleAuthMethods(ownServer)).toEqual(["none", "api_key"]);
    expect(needsBaseUrl(preset())).toBe(false);
    expect(needsBaseUrl(ownServer)).toBe(true);
    expect(needsBaseUrl(preset({ id: "ollama", base_url_editable: true }))).toBe(true);
  });

  it("builds a draft with safe defaults (ask for cloud, allow for local, vault when DPAPI is missing)", () => {
    const d = draftForPreset(preset(), store);
    expect(d.data_sharing).toBe("ask");
    expect(d.credential_storage).toBe("host");
    expect(d.auth_method).toBe("api_key");
    expect(d.name).toBe("DeepSeek API");
    const local = draftForPreset(preset({ id: "ollama", name: "Ollama (bereits laufend)", execution: "local", auth_methods: ["none", "api_key"] }), { ...store, host_available: false });
    expect(local.data_sharing).toBe("allow");
    expect(local.credential_storage).toBe("vault");
    expect(local.auth_method).toBe("none");
    expect(local.name).toBe("Ollama");
  });

  it("validates with German messages", () => {
    const d = draftForPreset(preset(), store);
    expect(validateConnectionDraft(d, null, store)).toBe("Bitte einen Anbieter wählen.");
    expect(validateConnectionDraft({ ...d, name: " " }, preset(), store)).toBe("Bitte einen Namen vergeben.");
    expect(validateConnectionDraft(d, preset(), store)).toBe("API-Schlüssel fehlt.");
    expect(validateConnectionDraft({ ...d, api_key: "sk-1", credential_storage: "vault" }, preset(), store)).toMatch(/Tresor ist gesperrt/);
    expect(validateConnectionDraft({ ...d, api_key: "sk-1", credential_storage: "host" }, preset(), { ...store, host_available: false })).toMatch(/nicht verfügbar/);
    expect(validateConnectionDraft({ ...d, api_key: "sk-1" }, preset(), store)).toBeNull();
    expect(validateConnectionDraft({ ...d, auth_method: "none" }, preset(), store)).toMatch(/nicht vorgesehen/);
    const own = draftForPreset(ownServer, store);
    expect(validateConnectionDraft(own, ownServer, store)).toBe("Serveradresse fehlt.");
    expect(validateConnectionDraft({ ...own, base_url: "localhost:8080" }, ownServer, store)).toMatch(/http/);
    expect(validateConnectionDraft({ ...own, base_url: "http://srv/v1", execution: "cloud" }, ownServer, store)).toMatch(/https/);
    expect(validateConnectionDraft({ ...own, base_url: "http://192.168.1.10:8080/v1", execution: "lan" }, ownServer, store)).toBeNull();
  });

  it("sends only the fields the preset allows (no key when none, base URL only when editable)", () => {
    const d = { ...draftForPreset(preset(), store), api_key: "sk-1", default_model: " deepseek-chat " };
    const body = draftToRequest(d, preset());
    expect(body).toEqual({ provider: "deepseek", name: "DeepSeek API", auth_method: "api_key", credential_storage: "host", data_sharing: "ask", default_model: "deepseek-chat", api_key: "sk-1" });
    const own = { ...draftForPreset(ownServer, store), base_url: "http://10.0.0.5:8080/v1 ", execution: "lan" as const, auth_method: "none" as const };
    const ownBody = draftToRequest(own, ownServer);
    expect(ownBody.base_url).toBe("http://10.0.0.5:8080/v1");
    expect(ownBody.execution).toBe("lan");
    expect(ownBody.api_key).toBeUndefined();
  });
});

describe("test stages", () => {
  const result = (over: Partial<ConnectionTestResult> = {}): ConnectionTestResult => ({
    server_reached: true,
    access_ok: true,
    model_listed: true,
    answer_ok: null,
    models: ["a", "b"],
    message_de: "ok",
    error: null,
    latency_ms: 120,
    details: {},
    ...over,
  });

  it("shows four pending stages before a test", () => {
    expect(testStages(null, false).map((s) => s.state)).toEqual(["pending", "pending", "pending", "pending"]);
  });

  it("renders network failure, 401 and success distinctly", () => {
    const down = testStages(result({ server_reached: false, access_ok: null, model_listed: null, models: [], error: { code: "network", message_de: "Verbindung fehlgeschlagen", status: null, retryable: true, retry_after_s: null } }), false);
    expect(down.map((s) => s.state)).toEqual(["failed", "skipped", "skipped", "skipped"]);
    expect(down[0].note).toBe("Verbindung fehlgeschlagen");
    const denied = testStages(result({ access_ok: false, model_listed: null, models: [], error: { code: "unauthorized", message_de: "401 Unauthorized", status: 401, retryable: false, retry_after_s: null } }), false);
    expect(denied.map((s) => s.state)).toEqual(["ok", "failed", "skipped", "skipped"]);
    expect(denied[1].note).toBe("401 Unauthorized (HTTP 401)");
    const ok = testStages(result({ answer_ok: true, details: { probe_answer: "OK" } }), true);
    expect(ok.map((s) => s.state)).toEqual(["ok", "ok", "ok", "ok"]);
    expect(ok[2].note).toBe("2 Modell(e)");
    expect(ok[3].note).toBe("„OK“");
    expect(testStages(result(), false)[3]).toMatchObject({ state: "skipped", note: "nicht angefordert" });
  });
});

describe("capability chips and binding label", () => {
  it("prefers the verified profile over the provider's declaration and maps booleans", () => {
    const model: ConnectionModel = { id: "m", display_name: "m", owned_by: null, context_length: null, capabilities: null, declared: { text: true, vision: false, tools: "declared" } };
    expect(capabilityChips(model).map((c) => c.value)).toEqual(["declared", "no", "declared", "unknown"]);
    const verified: ConnectionModel = { ...model, capabilities: { scope: "connection:c", model: "m", text: "verified", vision: "verified", tools: "no", structured_output: "unknown", streaming: "verified", reasoning: "unknown", context_length: 8192, parameters: {}, notes_de: "", checked_at: null } };
    expect(capabilityChips(verified).map((c) => c.value)).toEqual(["verified", "verified", "no", "unknown"]);
  });

  it("labels local and connection bindings", () => {
    expect(bindingLabel(null, "Qwen3 4B")).toMatchObject({ kind: "local", title: "Qwen3 4B", execution: "local" });
    const res: BindingResponse = {
      binding: { kind: "connection", connection_id: "conn_1", provider_model_id: "deepseek-chat", local_model_id: null },
      project: { id: "default", name: "Allgemein", local_only: false, conversations: 1, documents: 0, created_at: "", updated_at: "" },
      policy: { allowed: false, reason_de: "Freigabe nötig", needs_consent: true, code: "consent_required" },
      connection: connection(),
    };
    expect(bindingLabel(res, "Qwen3 4B")).toMatchObject({ kind: "connection", title: "DeepSeek · deepseek-chat", execution: "cloud", connectionName: "DeepSeek" });
    expect(bindingLabel({ ...res, connection: null }, null).execution).toBe("unknown");
  });
});

describe("consent flow", () => {
  const sharing: SharingEvent = { connection_id: "conn_1", connection_name: "DeepSeek", execution: "cloud", project_id: "default", history_messages: 3, system_prompt: true, memory_chars: 120, sources: 1, images: 0 };
  const error: ChatErrorEvent = { detail: "Freigabe nötig", code: "consent_required", needs_consent: true, connection_id: "conn_1", project_id: "default", retryable: true, next_action_de: "Freigabe erteilen" };

  it("derives a consent request only from consent_required with needs_consent", () => {
    const req = consentFromError("c1", "Hallo", error, sharing, null);
    expect(req).toMatchObject({ conversationId: "c1", text: "Hallo", connectionId: "conn_1", connectionName: "DeepSeek", projectId: "default", execution: "cloud" });
    expect(consentFromError("c1", "Hallo", { ...error, code: "empty_answer" }, sharing, null)).toBeNull();
    expect(consentFromError("c1", "Hallo", { ...error, needs_consent: false }, sharing, null)).toBeNull();
    expect(consentFromError("c1", "Hallo", { detail: "x", code: "consent_required", needs_consent: true }, null, null)).toBeNull();
  });

  it("falls back to the binding for names and summarises what is sent", () => {
    const res: BindingResponse = {
      binding: { kind: "connection", connection_id: "conn_1", provider_model_id: "deepseek-chat", local_model_id: null },
      project: { id: "p2", name: "Nordlicht", local_only: false, conversations: 0, documents: 0, created_at: "", updated_at: "" },
      policy: { allowed: false, reason_de: "", needs_consent: true, code: "consent_required" },
      connection: connection({ name: "Mein DeepSeek" }),
    };
    const req = consentFromError("c1", "Hallo", { detail: "x", code: "consent_required", needs_consent: true }, null, res);
    expect(req).toMatchObject({ connectionId: "conn_1", connectionName: "Mein DeepSeek", projectId: "p2", projectName: "Nordlicht", execution: "cloud" });
    expect(sharingSummary(sharing)).toEqual(["3 Nachricht(en) des Gesprächsverlaufs", "die Systemanweisung von SpAIder", "120 Zeichen aus dem Gedächtnis", "1 Quelle(n) aus Dokumenten"]);
    expect(sharingSummary(null)).toHaveLength(1);
  });
});

describe("discover pagination", () => {
  const item = (repo: string): DiscoverItem => ({ repo, author: repo.split("/")[0], name: repo.split("/")[1], publisher_status: "offiziell", likes: 1, downloads: 2, updated: null, gguf: true, gated: false, private: false, pipeline_tag: "text-generation", library: "gguf", family_hints: [], status: "unverified" });
  const page = (items: DiscoverItem[], next: string | null): DiscoverResponse => ({ items, next_cursor: next, cached_at: null, offline: false });

  it("replaces on a new query and appends without duplicates on 'Mehr laden'", () => {
    const first = mergeDiscoverPage([item("x/old")], page([item("a/1"), item("a/2")], "p2"), false);
    expect(first.map((i) => i.repo)).toEqual(["a/1", "a/2"]);
    const more = mergeDiscoverPage(first, page([item("a/2"), item("a/3")], null), true);
    expect(more.map((i) => i.repo)).toEqual(["a/1", "a/2", "a/3"]);
  });

  it("compares queries ignoring surrounding whitespace", () => {
    expect(sameDiscoverQuery(DEFAULT_DISCOVER_QUERY, { ...DEFAULT_DISCOVER_QUERY, q: " " })).toBe(true);
    expect(sameDiscoverQuery(DEFAULT_DISCOVER_QUERY, { ...DEFAULT_DISCOVER_QUERY, gguf_only: false })).toBe(false);
    expect(parseRepo("Qwen/Qwen3-4B-GGUF")).toEqual({ author: "Qwen", name: "Qwen3-4B-GGUF" });
    expect(parseRepo("nope")).toBeNull();
  });
});

describe("repository variants", () => {
  const variant = (over: Partial<RepoVariant> = {}): RepoVariant => ({
    key: "Q4_K_M",
    quantization: "Q4_K_M",
    kind: "weights",
    files: [
      { path: "m-Q4_K_M-00002-of-00002.gguf", size: 10, sha256: null, split_index: 2, split_total: 2 },
      { path: "m-Q4_K_M-00001-of-00002.gguf", size: 20, sha256: "aa", split_index: 1, split_total: 2 },
    ],
    bytes: 30,
    parts: 2,
    needs_projector: false,
    projector_options: [],
    fit: "ok",
    ...over,
  });

  it("groups by kind, picks the first part and gates the download", () => {
    const g = groupVariants([variant(), variant({ key: "mmproj-F16", kind: "projector" }), variant({ key: "draft", kind: "auxiliary" })]);
    expect(g.weights).toHaveLength(1);
    expect(g.projectors).toHaveLength(1);
    expect(g.auxiliary).toHaveLength(1);
    expect(variantPrimaryFile(variant())).toBe("m-Q4_K_M-00001-of-00002.gguf");
    expect(variantDownloadState(null, null, false).can).toBe(false);
    expect(variantDownloadState(variant(), null, true).reason).toMatch(/bereits installiert/);
    expect(variantDownloadState(variant({ kind: "projector" }), null, false).reason).toMatch(/Nicht unterstützt/);
    expect(variantDownloadState(variant({ fit: "too_large" }), null, false).reason).toMatch(/Zu groß/);
    expect(variantDownloadState(variant({ needs_projector: true, projector_options: ["mmproj-F16"] }), null, false).reason).toMatch(/Projektor/);
    expect(variantDownloadState(variant({ needs_projector: true, projector_options: ["mmproj-F16"] }), "mmproj-F16", false).can).toBe(true);
    expect(variantDownloadState(variant(), null, false)).toEqual({ can: true, reason: null });
  });
});
