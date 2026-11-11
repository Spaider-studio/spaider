/**
 * Mock SpAIder core (no dependencies). Implements enough of
 * docs/portable/API_CONTRACT.md (v1 + Addendum v1.1) and
 * docs/portable/ACTIVITY_EVENTS.md to drive every renderer screen.
 *
 *   node mock/server.mjs [--port 8765] [--token mock-token] [--state needs_model|ready]
 *                        [--exit-on-shutdown] [--offline] [--no-gpu] [--fat32]
 *
 * Prints {"event":"ready","port":N} on stdout when listening (like the real core).
 * Behaviour hooks for testing the UI:
 *   - catalog entry "qwen3.8-27b-community": first download -> 409 insufficient_space,
 *     second download fails at ~30 % (resumable with /resume), resume completes.
 *   - import path containing "invalid" -> gguf_invalid, "split" -> split_incomplete,
 *     "embed"/"nochat" -> not_chat_model, "unsupported" -> arch_unsupported,
 *     "rolemismatch" -> role_mismatch.
 *   - document name containing "scan" -> warning no_text_layer.
 *   - message text containing "fehler" -> activity `generating failed` + error event.
 *   - settings.thinking_enabled (and a model with reasoning_toggle) -> `thinking` events.
 *   - search: `?offline=1`, `--offline`, or a query containing "offline" -> offline:true.
 *   - resolve: repo containing "gated"/"meta-llama"/"google/gemma" -> 409 gated;
 *     "split" -> two-part model (second part without checksum); "base"/"nochat" ->
 *     incompatible (type base); "vision" -> type vision; "missing" -> 404.
 *   - "--state ready" installs and activates the first catalog model.
 */
import http from "node:http";
import { randomUUID } from "node:crypto";
import { registerLiveAgent } from "./live-agent.mjs";
import { registerModelHub } from "./modelhub.mjs";

const args = process.argv.slice(2);
const argValue = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i !== -1 && args[i + 1] ? args[i + 1] : fallback;
};
const PORT = Number(argValue("port", 8765));
const TOKEN = process.env.SPAIDER_FOLDABLE_TOKEN ?? argValue("token", "mock-token");
const INITIAL_STATE = argValue("state", "needs_model");
const EXIT_ON_SHUTDOWN = args.includes("--exit-on-shutdown");
const OFFLINE = args.includes("--offline");
const NO_GPU = args.includes("--no-gpu");

const now = () => new Date().toISOString();
const GB = 1024 * 1024 * 1024;
const MB = 1024 * 1024;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  settings: {
    thinking_enabled: false,
    context_tokens: 8192,
    max_answer_tokens: 1024,
    memory_auto_extract: true,
    embedding_enabled: true,
    threads: null,
    temperature: 0.7,
    system_prompt_extra: "",
    performance_mode: "auto",
    gpu_mode: "auto",
    // v1.2 (the defaults can be overridden for test machines where a
    // combination is already taken: --hotkey-overlay "Ctrl+Alt+F9")
    hotkey_overlay: argValue("hotkey-overlay", "Ctrl+Alt+Space"),
    hotkey_stop: argValue("hotkey-stop", "Ctrl+Alt+Backspace"),
    tray_mode: false,
    live_preview: true,
    agent_max_steps: 20,
    agent_max_seconds: 600,
  },
  installed: new Map(),
  activeChat: null,
  activeEmbedding: null,
  runtime: "stopped",
  jobs: new Map(),
  conversations: new Map(),
  messages: new Map(), // conversationId -> Message[]
  documents: new Map(),
  notes: new Map(),
  generation: null, // { conversationId, stopped, closed }
  spaceErrorShown: false,
  failedOnce: false,
  diskFree: 120 * GB,
  // v1.1
  dynamicEntries: new Map(), // "hf:<repo>:<file>" -> ModelCatalogEntry
  requests: new Map(), // assistant message id -> { seq, events, conversationId }
  postprocessQueue: [], // { conversationId, messageId }
  postprocessRunning: null,
  catalogDate: "2026-10-30T09:00:00Z",
};

const FINGERPRINT = "mock-268v-arc140v-32gb";

function hardwareProfile() {
  const models = [...state.installed.values()].reduce((a, m) => a + m.size_bytes, 0);
  const gpus = NO_GPU
    ? []
    : [{ name: "Intel(R) Arc(TM) 140V GPU", backend: "vulkan", vram_total_bytes: 16 * GB, vram_free_bytes: 13.5 * GB, driver: "32.0.101.8860", usable: true }];
  const gpuUsable = gpus.some((g) => g.usable) && state.settings.gpu_mode !== "off";
  return {
    cpu: { name: "Intel Core Ultra 7 268V", cores_physical: 8, threads: 8, features: ["avx2", "avx512", "fma", "f16c"] },
    ram_total_bytes: 32 * GB,
    ram_available_bytes: 19.4 * GB,
    gpus,
    disk_free_bytes: state.diskFree - models,
    power: { on_battery: false, power_saver: false },
    fingerprint: FINGERPRINT,
    runtime_variants: NO_GPU ? ["cpu"] : ["cpu", "vulkan"],
    active_backend: gpuUsable ? "vulkan" : "cpu",
    detected_at: now(),
  };
}

const LICENSE_APACHE = (url) => ({ id: "apache-2.0", name: "Apache License 2.0", url, kind: "open-source", gated: false, requires_account: false });

const catalog = [
  {
    id: "qwen3-1.7b-q8_0",
    name: "Qwen3 1.7B (Q8_0)",
    role: "chat",
    family: "Qwen3",
    publisher: "Qwen",
    source: "official",
    repo: "Qwen/Qwen3-1.7B-GGUF",
    revision: "0123456789abcdef0123456789abcdef01234567",
    repo_url: "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF",
    files: [{ path: "Qwen3-1.7B-Q8_0.gguf", size: 1834426016, sha256: "ab".repeat(32) }],
    total_bytes: 1834426016,
    license: LICENSE_APACHE("https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/blob/main/LICENSE"),
    architecture: "qwen3",
    parameters: "1.7B",
    quantization: "Q8_0",
    context_length: 32768,
    embedding_dimensions: null,
    min_ram_bytes: 4 * GB,
    recommended_ram_bytes: 8 * GB,
    support: "executed",
    description_de: "Kompaktes Allround-Modell für Chat und Zusammenfassungen. Läuft flüssig auf aktuellen Laptops ohne Grafikkarte.",
    fit: { level: "recommended", message_de: "Passt gut zu 32 GB Arbeitsspeicher." },
    installed_model_id: null,
    original_model: "Qwen/Qwen3-1.7B",
    converter: "Qwen (offiziell)",
    verification: { status: "executed", date: "2026-10-28T14:00:00Z", scope: "llama.cpp b12032, CPU und Vulkan" },
    evidence: [
      { kind: "local_measurement", note_de: "Auf dem Referenz-Laptop mit 24 Token/s gemessen.", scope: "Intel Core Ultra 7 268V, Vulkan" },
      { kind: "model_card", note_de: "Laut Model Card für Chat und Reasoning trainiert.", scope: "Hugging Face" },
    ],
    resources: { weights_bytes: 1834426016, kv_bytes_per_token_est: 28672, est_ram_bytes_8k: 2.3 * GB, est_vram_bytes_8k: 2.1 * GB },
    template: { reasoning_toggle: "enable_thinking", system_role: true },
    type: "chat",
    families: ["Qwen3"],
    published: "2025-04-29",
    checked_at: "2026-10-30T09:00:00Z",
    source_kind: "builtin",
  },
  {
    id: "qwen3-0.6b-q8_0",
    name: "Qwen3 0.6B (Q8_0)",
    role: "chat",
    family: "Qwen3",
    publisher: "Qwen",
    source: "official",
    repo: "Qwen/Qwen3-0.6B-GGUF",
    revision: "2222222222222222222222222222222222222222",
    repo_url: "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF",
    files: [{ path: "Qwen3-0.6B-Q8_0.gguf", size: 639_000_000, sha256: "a1".repeat(32) }],
    total_bytes: 639_000_000,
    license: LICENSE_APACHE("https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/blob/main/LICENSE"),
    architecture: "qwen3",
    parameters: "0.6B",
    quantization: "Q8_0",
    context_length: 32768,
    embedding_dimensions: null,
    min_ram_bytes: 2 * GB,
    recommended_ram_bytes: 4 * GB,
    support: "documented",
    description_de: "Sehr kleines, schnelles Modell für einfache Fragen und knappe Antworten.",
    fit: { level: "ok", message_de: "Passt problemlos." },
    installed_model_id: null,
    original_model: "Qwen/Qwen3-0.6B",
    converter: "Qwen (offiziell)",
    verification: { status: "metadata", date: "2026-10-30T09:00:00Z", scope: "GGUF-Header geprüft" },
    evidence: [{ kind: "model_card", note_de: "Laut Model Card für einfache Chat-Aufgaben gedacht.", scope: "Hugging Face" }],
    resources: { weights_bytes: 639_000_000, kv_bytes_per_token_est: 14336, est_ram_bytes_8k: 0.9 * GB, est_vram_bytes_8k: 0.8 * GB },
    template: { reasoning_toggle: "enable_thinking", system_role: true },
    type: "chat",
    families: ["Qwen3"],
    published: "2025-04-29",
    checked_at: "2026-10-30T09:00:00Z",
    source_kind: "builtin",
  },
  {
    id: "qwen3.8-27b-community",
    name: "Qwen3.8 27B (Q4_K_M, Community)",
    role: "chat",
    family: "Qwen3.8",
    publisher: "unsloth",
    source: "community",
    repo: "unsloth/Qwen3.8-27B-GGUF",
    revision: "fedcba9876543210fedcba9876543210fedcba98",
    repo_url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF",
    files: [{ path: "Qwen3.8-27B-Q4_K_M.gguf", size: 17_200_000_000, sha256: "cd".repeat(32) }],
    total_bytes: 17_200_000_000,
    license: LICENSE_APACHE("https://huggingface.co/unsloth/Qwen3.8-27B-GGUF"),
    architecture: "qwen35",
    parameters: "27B",
    quantization: "Q4_K_M",
    context_length: 32768,
    embedding_dimensions: null,
    min_ram_bytes: 20 * GB,
    recommended_ram_bytes: 32 * GB,
    support: "documented",
    description_de: "Großes Modell mit deutlich besserer Qualität. Community-Konvertierung, laut llama.cpp-Dokumentation unterstützt, hier nicht ausgeführt.",
    fit: { level: "tight", message_de: "Knapp: benötigt etwa 20 GB freien Arbeitsspeicher, Antworten sind langsam." },
    installed_model_id: null,
    original_model: "Qwen/Qwen3.8-27B",
    converter: "unsloth (Community)",
    verification: { status: "metadata", date: "2026-10-30T09:00:00Z", scope: "GGUF-Header geprüft" },
    evidence: [{ kind: "benchmark", note_de: "Laut Hersteller-Benchmarks deutlich stärker als die 1,7B-Variante.", scope: "Qwen Blog" }],
    resources: { weights_bytes: 17_200_000_000, kv_bytes_per_token_est: 65536, est_ram_bytes_8k: 20.5 * GB, est_vram_bytes_8k: 18.9 * GB },
    template: { reasoning_toggle: "enable_thinking", system_role: true },
    type: "chat",
    families: ["Qwen3.8"],
    published: "2026-09-15",
    checked_at: "2026-10-30T09:00:00Z",
    source_kind: "builtin",
  },
  {
    id: "gemma-3-1b-it-qat",
    name: "Gemma 3 1B (Q4_0, QAT)",
    role: "chat",
    family: "Gemma 3",
    publisher: "Google",
    source: "official",
    repo: "google/gemma-3-1b-it-qat-q4_0-gguf",
    revision: "3333333333333333333333333333333333333333",
    repo_url: "https://huggingface.co/google/gemma-3-1b-it-qat-q4_0-gguf",
    files: [{ path: "gemma-3-1b-it-q4_0.gguf", size: 720_000_000, sha256: "b2".repeat(32) }],
    total_bytes: 720_000_000,
    license: { id: "gemma", name: "Gemma Terms of Use", url: "https://ai.google.dev/gemma/terms", kind: "other", gated: true, requires_account: true },
    architecture: "gemma3",
    parameters: "1B",
    quantization: "Q4_0",
    context_length: 32768,
    embedding_dimensions: null,
    min_ram_bytes: 2 * GB,
    recommended_ram_bytes: 4 * GB,
    support: "documented",
    description_de: "Kleines Google-Modell. Der Download setzt ein Hugging-Face-Konto und die Zustimmung zu den Gemma-Bedingungen voraus.",
    fit: { level: "ok", message_de: "Passt problemlos." },
    installed_model_id: null,
    original_model: "google/gemma-3-1b-it",
    converter: "Google (offiziell)",
    verification: { status: "unverified", date: null, scope: null },
    evidence: [],
    resources: { weights_bytes: 720_000_000, kv_bytes_per_token_est: 16384, est_ram_bytes_8k: 1.1 * GB, est_vram_bytes_8k: 1.0 * GB },
    template: { reasoning_toggle: null, system_role: false },
    type: "chat",
    families: ["Gemma 3"],
    published: "2025-04-03",
    checked_at: "2026-10-30T09:00:00Z",
    source_kind: "builtin",
  },
  {
    id: "qwen3-embedding-0.6b-q8_0",
    name: "Qwen3 Embedding 0.6B (Q8_0)",
    role: "embedding",
    family: "Qwen3",
    publisher: "Qwen",
    source: "official",
    repo: "Qwen/Qwen3-Embedding-0.6B-GGUF",
    revision: "1111111111111111111111111111111111111111",
    repo_url: "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF",
    files: [{ path: "Qwen3-Embedding-0.6B-Q8_0.gguf", size: 639_000_000, sha256: "ef".repeat(32) }],
    total_bytes: 639_000_000,
    license: LICENSE_APACHE("https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF"),
    architecture: "qwen3",
    parameters: "0.6B",
    quantization: "Q8_0",
    context_length: 32768,
    embedding_dimensions: 1024,
    min_ram_bytes: 1 * GB,
    recommended_ram_bytes: 2 * GB,
    support: "executed",
    description_de: "Embedding-Modell für die semantische Gedächtnis-Suche.",
    fit: { level: "ok", message_de: "Passt." },
    installed_model_id: null,
    original_model: "Qwen/Qwen3-Embedding-0.6B",
    converter: "Qwen (offiziell)",
    verification: { status: "executed", date: "2026-10-28T14:00:00Z", scope: "llama.cpp b12032, Embeddings" },
    evidence: [],
    resources: { weights_bytes: 639_000_000, kv_bytes_per_token_est: 0, est_ram_bytes_8k: 0.8 * GB, est_vram_bytes_8k: 0.7 * GB },
    template: { reasoning_toggle: null, system_role: false },
    type: "embedding",
    families: ["Qwen3"],
    published: "2025-06-05",
    checked_at: "2026-10-30T09:00:00Z",
    source_kind: "builtin",
  },
];

const SEARCH_INDEX = [
  { repo: "Qwen/Qwen3-4B-GGUF", author: "Qwen", name: "Qwen3-4B-GGUF", likes: 412, downloads: 184_000, updated: "2026-10-12T10:00:00Z", gguf: true },
  { repo: "unsloth/Qwen3-4B-GGUF", author: "unsloth", name: "Qwen3-4B-GGUF", likes: 238, downloads: 96_000, updated: "2026-10-20T10:00:00Z", gguf: true },
  { repo: "unsloth/Qwen3-8B-GGUF", author: "unsloth", name: "Qwen3-8B-GGUF", likes: 301, downloads: 120_500, updated: "2026-10-18T10:00:00Z", gguf: true },
  { repo: "bartowski/Llama-3.2-1B-Instruct-GGUF", author: "bartowski", name: "Llama-3.2-1B-Instruct-GGUF", likes: 150, downloads: 420_000, updated: "2026-09-01T10:00:00Z", gguf: true },
  { repo: "meta-llama/Llama-3.2-1B-Instruct", author: "meta-llama", name: "Llama-3.2-1B-Instruct", likes: 1200, downloads: 2_400_000, updated: "2026-08-10T10:00:00Z", gguf: false },
  { repo: "ggml-org/SmolLM3-3B-GGUF", author: "ggml-org", name: "SmolLM3-3B-GGUF", likes: 88, downloads: 33_000, updated: "2026-10-25T10:00:00Z", gguf: true },
  { repo: "mistralai/Ministral-3-3B-Instruct-2512-GGUF", author: "mistralai", name: "Ministral-3-3B-Instruct-2512-GGUF", likes: 210, downloads: 51_000, updated: "2026-10-28T10:00:00Z", gguf: true },
  { repo: "unsloth/Qwen3-30B-A3B-GGUF-split", author: "unsloth", name: "Qwen3-30B-A3B-GGUF (mehrteilig)", likes: 64, downloads: 12_000, updated: "2026-10-22T10:00:00Z", gguf: true },
];

const facts = [
  {
    kind: "fact",
    id: "fact-1",
    label: "Projekt Foldable läuft auf einem USB-SSD",
    excerpt: "SpAIder Foldable wird von einem USB-SSD gestartet und speichert alle Daten dort.",
    status: "current",
    origin: { kind: "conversation", id: "conv-seed", title: "Erstes Gespräch", at: now() },
    superseded_by: null,
  },
  {
    kind: "fact",
    id: "fact-2",
    label: "Standard-Kontextlänge 4096",
    excerpt: "Die Kontextlänge war ursprünglich auf 4096 Token eingestellt.",
    status: "superseded",
    origin: { kind: "conversation", id: "conv-seed", title: "Erstes Gespräch", at: now() },
    superseded_by: { id: "fact-3", excerpt: "Die Kontextlänge beträgt jetzt 8192 Token." },
  },
  {
    kind: "document",
    id: "doc-chunk-1",
    label: "bericht.md, Abschnitt 2",
    excerpt: "Der Bericht nennt drei Meilensteine für das portable Setup.",
    status: "uncertain",
    origin: { kind: "document", id: "doc-seed", title: "bericht.md", at: now() },
    superseded_by: null,
  },
];

function seedNote(text) {
  const note = { id: randomUUID(), text, created_at: now(), updated_at: now() };
  state.notes.set(note.id, note);
}
seedNote("Musa bevorzugt kurze, sachliche Antworten auf Deutsch.");

function capabilitiesFor(entry, role) {
  return {
    chat_template: role === "chat",
    reasoning_toggle: entry?.template?.reasoning_toggle ?? null,
    system_role: entry?.template?.system_role ?? role === "chat",
    context_length: entry?.context_length ?? 8192,
    modalities: ["text"],
    structured_output: role === "chat",
    backends: NO_GPU ? ["cpu"] : ["cpu", "vulkan"],
  };
}

function installModel(entry, overrides = {}) {
  const model = {
    id: randomUUID(),
    name: entry.name,
    role: entry.role,
    catalog_id: entry.id,
    source: "catalog",
    path: `D:\\SpAIder\\data\\models\\${entry.files[0].path}`,
    size_bytes: entry.total_bytes,
    sha256: entry.files[0].sha256,
    sha256_verified: Boolean(entry.files[0].sha256),
    architecture: entry.architecture,
    quantization: entry.quantization,
    context_length: entry.context_length,
    embedding_dimensions: entry.embedding_dimensions,
    license: entry.license,
    state: "ready",
    active: false,
    created_at: now(),
    capabilities: capabilitiesFor(entry, entry.role),
    verification: entry.verification ?? { status: "metadata", date: now(), scope: "GGUF-Header geprüft" },
    measured: null,
    ...overrides,
  };
  state.installed.set(model.id, model);
  if (entry.id) {
    const c = catalog.find((e) => e.id === entry.id) ?? state.dynamicEntries.get(entry.id);
    if (c) c.installed_model_id = model.id;
  }
  return model;
}

if (INITIAL_STATE === "ready") {
  const m = installModel(catalog[0]);
  state.activeChat = m.id;
  state.runtime = "ready";
}

function modelView(m) {
  if (!m) return null;
  return { ...m, active: m.id === state.activeChat || m.id === state.activeEmbedding };
}

// ---------------------------------------------------------------------------
// Global activity channel (GET /api/activity)
// ---------------------------------------------------------------------------
const activityListeners = new Set();

function schedulerSnapshot() {
  const pending = state.postprocessQueue.length + (state.postprocessRunning ? 1 : 0);
  const chatActive = Boolean(state.generation);
  return {
    chat_active: chatActive,
    background: { kind: state.postprocessRunning ? "message" : pending > 0 ? "message" : null, pending },
    paused: chatActive && pending > 0,
  };
}
function runtimeSnapshot() {
  return { state: state.runtime, model_id: state.activeChat, at: now() };
}
function broadcast(event, data) {
  for (const res of activityListeners) sseSend(res, event, data);
}
function broadcastScheduler() {
  broadcast("scheduler", schedulerSnapshot());
}
function setRuntime(next) {
  if (state.runtime === next) return;
  state.runtime = next;
  broadcast("runtime", runtimeSnapshot());
}

/** Per-request activity bookkeeping (seq continues on the global channel). */
function requestFor(messageId, conversationId) {
  let req = state.requests.get(messageId);
  if (!req) {
    req = { id: messageId, conversationId, seq: 0, events: [], started: Date.now() };
    state.requests.set(messageId, req);
  }
  return req;
}
function makeEvent(req, phase, st, message_de, extra = {}) {
  const ev = {
    request_id: req.id,
    seq: req.seq++,
    phase,
    state: st,
    t_ms: Date.now() - req.started,
    at: now(),
    message_de,
    counters: extra.counters ?? null,
    source_ids: extra.source_ids ?? [],
  };
  req.events.push(ev);
  if (req.events.length > 200) req.events.shift();
  return ev;
}

function pumpPostprocess() {
  if (state.postprocessRunning || state.generation || state.postprocessQueue.length === 0) return;
  const item = state.postprocessQueue.shift();
  state.postprocessRunning = item;
  const req = requestFor(item.messageId, item.conversationId);
  broadcast("activity", { ...makeEvent(req, "postprocess_running", "started", "Gedächtnis wird aktualisiert", { counters: { facts_pending: 1 } }), conversation_id: item.conversationId });
  broadcastScheduler();
  setTimeout(() => {
    state.postprocessRunning = null;
    broadcast("activity", { ...makeEvent(req, "postprocess_done", "done", "Gedächtnis aktualisiert", { counters: { facts_pending: 0, facts: 2 } }), conversation_id: item.conversationId });
    broadcastScheduler();
    setTimeout(pumpPostprocess, 200);
  }, 2500);
}
function enqueuePostprocess(conversationId, messageId) {
  state.postprocessQueue.push({ conversationId, messageId });
  broadcastScheduler();
  setTimeout(pumpPostprocess, 1000);
}
function pendingFor(conversationId) {
  return state.postprocessQueue.filter((q) => q.conversationId === conversationId).length + (state.postprocessRunning?.conversationId === conversationId ? 1 : 0);
}
function convView(conv) {
  return { ...conv, project_id: conv.project_id ?? "default", binding: conv.binding ?? null, postprocessing_pending: pendingFor(conv.id) };
}

// ---------------------------------------------------------------------------
// Jobs with SSE listeners
// ---------------------------------------------------------------------------
const jobListeners = new Map(); // jobId -> Set<res>
const docListeners = new Map();

function createJob(kind, extra = {}) {
  const job = {
    id: randomUUID(),
    kind,
    state: "queued",
    catalog_id: null,
    model_id: null,
    phase: null,
    progress: { done_bytes: 0, total_bytes: 0, speed_bps: 0, eta_seconds: null },
    message_de: "In Warteschlange",
    error: null,
    result_model_id: null,
    created_at: now(),
    updated_at: now(),
    finished_at: null,
    ...extra,
  };
  state.jobs.set(job.id, job);
  return job;
}

function updateJob(job, patch) {
  Object.assign(job, patch, { updated_at: now() });
  if (["done", "failed", "cancelled"].includes(job.state)) job.finished_at = now();
  const listeners = jobListeners.get(job.id);
  if (listeners) {
    for (const res of listeners) sseSend(res, "job", { ...job });
    if (job.finished_at) {
      for (const res of listeners) res.end();
      jobListeners.delete(job.id);
    }
  }
}

const timers = new Map();
function runDownload(job, entry, failAt = null) {
  const total = entry.total_bytes;
  const step = Math.ceil(total / 40);
  const speed = step * 4; // 250 ms ticks
  updateJob(job, { state: "running", phase: "check_space", message_de: "Speicherplatz wird geprüft", progress: { ...job.progress, total_bytes: total } });
  const tick = () => {
    if (job.state !== "running") return;
    if (job.phase === "check_space") {
      updateJob(job, { phase: "download", message_de: entry.files.length > 1 ? `Lädt von Hugging Face (${entry.files.length} Teile)` : "Lädt von Hugging Face" });
    } else if (job.phase === "download") {
      const done = Math.min(total, job.progress.done_bytes + step);
      if (failAt !== null && done / total >= failAt && !state.failedOnce) {
        state.failedOnce = true;
        updateJob(job, { state: "failed", message_de: "Verbindung unterbrochen", error: { detail: "Die Verbindung zu Hugging Face wurde unterbrochen. Der Download kann fortgesetzt werden.", code: "download_failed" } });
        return;
      }
      updateJob(job, { progress: { done_bytes: done, total_bytes: total, speed_bps: speed, eta_seconds: Math.round((total - done) / speed) } });
      if (done >= total) updateJob(job, { phase: "verify", message_de: "Prüfsumme wird berechnet", progress: { ...job.progress, speed_bps: 0, eta_seconds: null } });
    } else if (job.phase === "verify") {
      const model = installModel(entry);
      updateJob(job, { state: "done", phase: null, message_de: "Download abgeschlossen", result_model_id: model.id });
      return;
    }
    timers.set(job.id, setTimeout(tick, 250));
  };
  timers.set(job.id, setTimeout(tick, 250));
}

function runActivate(job, model) {
  const phases = ["load", "load", "load", "probe", "probe", "commit"];
  let i = 0;
  updateJob(job, { state: "running", phase: "load", message_de: "llama-server wird gestartet" });
  setRuntime("starting");
  const tick = () => {
    if (job.state !== "running") return;
    i += 1;
    if (i >= phases.length) {
      if (model.role === "chat") state.activeChat = model.id;
      else state.activeEmbedding = model.id;
      setRuntime("ready");
      updateJob(job, { state: "done", phase: null, message_de: "Modell aktiv", result_model_id: model.id });
      return;
    }
    updateJob(job, { phase: phases[i], message_de: phases[i] === "probe" ? "Testantwort wird erzeugt" : phases[i] === "commit" ? "Wird übernommen" : "Modell wird geladen" });
    timers.set(job.id, setTimeout(tick, 600));
  };
  timers.set(job.id, setTimeout(tick, 600));
}

const IMPORT_FAILURES = [
  [/invalid/i, "gguf_invalid", "Die Datei ist keine gültige GGUF-Datei."],
  [/split/i, "split_incomplete", "Von diesem mehrteiligen Modell fehlen Teile (gefunden: 1 von 3)."],
  [/embed|nochat/i, "not_chat_model", "Die Datei ist ein Embedding-Modell, kein Chat-Modell."],
  [/unsupported/i, "arch_unsupported", "Architektur „mamba2“ wird von dieser llama.cpp-Laufzeit nicht unterstützt."],
  [/rolemismatch/i, "role_mismatch", "Die Datei passt nicht zur erwarteten Rolle."],
];

function runImport(job, path) {
  const failure = IMPORT_FAILURES.find(([re]) => re.test(path));
  const phases = ["copy", "copy", "verify", "probe"];
  let i = 0;
  const total = 900_000_000;
  updateJob(job, { state: "running", phase: "copy", message_de: "Datei wird kopiert", progress: { done_bytes: 0, total_bytes: total, speed_bps: 300_000_000, eta_seconds: 3 } });
  const tick = () => {
    if (job.state !== "running") return;
    i += 1;
    if (i >= phases.length) {
      if (failure) {
        updateJob(job, { state: "failed", phase: null, message_de: "Import fehlgeschlagen", error: { detail: failure[2], code: failure[1] } });
        return;
      }
      const name = path.split(/[\\/]/).pop().replace(/\.gguf$/i, "");
      const model = installModel(
        { id: null, name, role: "chat", files: [{ path: `${name}.gguf`, sha256: null }], total_bytes: total, architecture: "qwen3", quantization: "Q4_K_M", context_length: 8192, embedding_dimensions: null, license: null, template: { reasoning_toggle: null, system_role: true } },
        { source: "import", catalog_id: null, sha256: null, sha256_verified: false, verification: { status: "metadata", date: now(), scope: "GGUF-Header beim Import geprüft" } },
      );
      updateJob(job, { state: "done", phase: null, message_de: "Import abgeschlossen", result_model_id: model.id, progress: { ...job.progress, done_bytes: total } });
      return;
    }
    updateJob(job, { phase: phases[i], message_de: phases[i] === "verify" ? "GGUF-Header wird geprüft" : phases[i] === "probe" ? "Modell wird geprüft" : "Datei wird kopiert", progress: { ...job.progress, done_bytes: Math.min(total, Math.round((total * i) / 2)) } });
    timers.set(job.id, setTimeout(tick, 700));
  };
  timers.set(job.id, setTimeout(tick, 700));
}

function runCalibrate(job, model) {
  const totalTokens = 64;
  let done = 0;
  updateJob(job, { state: "running", phase: "load", message_de: "Modell wird für die Messung geladen" });
  const tick = () => {
    if (job.state !== "running") return;
    if (job.phase === "load") {
      updateJob(job, { phase: "probe", message_de: `Messung läuft: 0 von ${totalTokens} Token` });
    } else {
      done += 16;
      if (done >= totalTokens) {
        const hw = hardwareProfile();
        model.measured = {
          backend: hw.active_backend,
          tokens_per_second: hw.active_backend === "vulkan" ? 23.6 : 11.2,
          time_to_first_token_ms: 290,
          prompt_tokens_per_second: hw.active_backend === "vulkan" ? 410 : 160,
          measured_at: now(),
          fingerprint: FINGERPRINT,
        };
        updateJob(job, { state: "done", phase: null, message_de: `Gemessen: ${model.measured.tokens_per_second} Token/s`, result_model_id: model.id });
        return;
      }
      updateJob(job, { message_de: `Messung läuft: ${done} von ${totalTokens} Token` });
    }
    timers.set(job.id, setTimeout(tick, 450));
  };
  timers.set(job.id, setTimeout(tick, 600));
}

// ---------------------------------------------------------------------------
// Documents
// ---------------------------------------------------------------------------
function updateDoc(doc, patch) {
  Object.assign(doc, patch);
  const listeners = docListeners.get(doc.id);
  if (listeners) {
    for (const res of listeners) sseSend(res, "document", { ...doc });
    if (doc.status === "indexed" || doc.status === "failed") {
      for (const res of listeners) res.end();
      docListeners.delete(doc.id);
    }
  }
}

function runIndexing(doc) {
  const total = 6;
  let done = 0;
  updateDoc(doc, { status: "indexing", progress: { done: 0, total } });
  const tick = () => {
    done += 1;
    if (done >= total) {
      const noText = doc.warnings.some((w) => w.code === "no_text_layer");
      updateDoc(doc, { status: "indexed", progress: { done: total, total }, facts_count: noText ? 0 : 7 + Math.floor(Math.random() * 5) });
      return;
    }
    updateDoc(doc, { progress: { done, total } });
    setTimeout(tick, 500);
  };
  setTimeout(tick, 500);
}

// ---------------------------------------------------------------------------
// Chat streaming (v1 events + v1.1 activity / thinking)
// ---------------------------------------------------------------------------
const MOCK_ANSWER = `Dies ist eine **Testantwort des Mock-Kerns** (keine echte Modellantwort).

Sie dient nur dazu, die Darstellung zu prüfen:

1. Markdown-Listen und *Hervorhebungen*
2. Links, z. B. [Qwen auf Hugging Face](https://huggingface.co/Qwen) und ein [nicht erlaubter Link](https://example.com/x)
3. Tabellen

| Phase | Status |
| --- | --- |
| retrieving | fertig |
| generating | läuft |

\`\`\`python
def begruessung(name: str) -> str:
    return f"Hallo, {name}!"
\`\`\`

Inline-Code wie \`SPAIDER_FOLDABLE_TOKEN\` wird ebenfalls dargestellt. <script>alert("x")</script>`;

const LONG_ANSWER = Array.from({ length: 12 }, (_, i) => `Absatz ${i + 1}: ${MOCK_ANSWER.split("\n")[0]} Dieser Abschnitt ist bewusst lang, damit die Oberfläche bei vielen Token flüssig bleibt und das Scrollen sowie die Eingabe währenddessen reagieren.`).join("\n\n");

const MOCK_THINKING =
  "Der Nutzer fragt nach dem Projekt. Ich sollte zuerst die gespeicherten Fakten durchgehen: Das Projekt läuft von einem USB-SSD, die Kontextlänge wurde auf 8192 erhöht. Die Antwort sollte kurz und sachlich auf Deutsch sein, mit einer Liste und einem kleinen Codebeispiel zur Darstellung. ";

/**
 * Streams one answer. `live` (v1.2) = { session, snapshot, useImage }: adds the
 * phases capture / observe / image_encode and the `observation` event before
 * the regular timeline.
 */
function startGeneration(conversationId, content, res, live = null) {
  const conv = state.conversations.get(conversationId);
  const list = state.messages.get(conversationId);
  const model = state.installed.get(state.activeChat);
  const userMessage = { id: randomUUID(), conversation_id: conversationId, role: "user", content, created_at: now(), status: "complete", model_id: null, sources: [], error: null, usage: null, timings: null };
  const pol = hub.decide(conv);
  const viaConnection = pol.binding.kind === "connection";
  const assistant = { id: randomUUID(), conversation_id: conversationId, role: "assistant", content: "", created_at: now(), status: "streaming", model_id: viaConnection ? null : state.activeChat, sources: [], error: null, usage: null, timings: null, connection_id: pol.connection?.id ?? null, provider_model: viaConnection ? pol.binding.provider_model_id : null };
  list.push(userMessage, assistant);
  if (!conv.title || conv.title === "Neues Gespräch") conv.title = content.slice(0, 48);
  conv.message_count = list.length;
  conv.updated_at = now();
  conv.last_model_id = state.activeChat;

  sseStart(res);
  sseSend(res, "message", { user_message: userMessage, assistant_message: assistant, binding: pol.binding, project: pol.project });
  const gen = { conversationId, stopped: false, closed: false };
  state.generation = gen;
  broadcastScheduler();
  res.on("close", () => {
    gen.closed = true;
  });

  const req = requestFor(assistant.id, conversationId);
  const act = (phase, st, message_de, extra) => {
    const ev = makeEvent(req, phase, st, message_de, extra);
    if (!gen.closed) sseSend(res, "activity", ev);
    return ev;
  };
  const errorCase = /fehler/i.test(content);
  const longCase = /lang|viele token/i.test(content);
  const answer = errorCase ? "Diese Antwort bricht gleich mit einem Fehler ab. " : longCase ? LONG_ANSWER : MOCK_ANSWER;
  const words = answer.split(/(?<=\s)/);
  const thinkingOn = Boolean(state.settings.thinking_enabled && model?.capabilities?.reasoning_toggle);
  const thinkWords = thinkingOn ? MOCK_THINKING.split(/(?<=\s)/) : [];
  const timings = {};
  const started = req.started;
  let i = 0;
  let firstTokenAt = null;
  let generationStartedAt = null;
  let lastProgressAt = 0;

  const tps = () => Math.round((i / Math.max(0.05, (Date.now() - (generationStartedAt ?? started)) / 1000)) * 10) / 10;

  const finish = (status) => {
    assistant.status = status;
    const endedAt = Date.now();
    timings.generation_ms = generationStartedAt ? endedAt - generationStartedAt : null;
    timings.total_ms = endedAt - started;
    assistant.timings = timings;
    assistant.usage = { prompt_tokens: 412, completion_tokens: i, time_to_first_token_ms: firstTokenAt ? firstTokenAt - started : 0, tokens_per_second: tps() };
    conv.updated_at = now();
    act("answer_done", status === "stopped" ? "cancelled" : "done", status === "stopped" ? "Abgebrochen" : "Antwort fertig", { counters: { tokens: i, tokens_per_second: tps() } });
    if (!gen.closed) sseSend(res, "status", { phase: "memorizing", message_de: "Gedächtnis wird aktualisiert …" });
    setTimeout(() => {
      act("saved", "done", "Gespräch gespeichert");
      act("postprocess_pending", "started", "Gedächtnis-Nachverarbeitung ausstehend", { counters: { facts_pending: 1 } });
      state.generation = null;
      if (!gen.closed) {
        sseSend(res, "done", { message: assistant });
        res.end();
      }
      enqueuePostprocess(conversationId, assistant.id);
    }, 80);
  };

  // v1.3: every error carries code/retryable/next_action_de (+ consent fields); never followed by `done`.
  const fail = (detail, code = "generation_failed", extra = {}, phase = "generating") => {
    assistant.status = "error";
    assistant.error = detail;
    assistant.timings = { ...timings, total_ms: Date.now() - started };
    state.generation = null;
    act(phase, "failed", `Fehlgeschlagen: ${detail}`, { counters: { tokens: i } });
    if (!gen.closed) {
      sseSend(res, "error", {
        detail,
        code,
        retryable: extra.retryable ?? true,
        retry_after_s: extra.retry_after_s ?? null,
        needs_consent: Boolean(extra.needs_consent),
        connection_id: pol.connection?.id ?? null,
        project_id: pol.project?.id ?? null,
        partial_text: assistant.content.length > 0,
        next_action_de: hub.NEXT_ACTION_DE[code] ?? hub.NEXT_ACTION_DE.generation_failed,
      });
      res.end();
    }
    broadcastScheduler();
  };
  const emptyCase = /^leer$/i.test(content.trim());
  if (viaConnection && !pol.decision.allowed) {
    // Policy blocks the connection (consent_required / local_only / denied / disabled): error, no generation.
    setTimeout(() => fail(pol.decision.reason_de, pol.decision.code, { needs_consent: pol.decision.needs_consent, retryable: true }, "queued"), 30);
    return;
  }
  if (viaConnection) {
    setTimeout(() => {
      if (!gen.closed) sseSend(res, "sharing", hub.sharingPreview(conv, list));
    }, 15);
  }

  const generate = () => {
    generationStartedAt = Date.now();
    act("generating", "started", "Antwort wird geschrieben");
    if (!gen.closed) sseSend(res, "status", { phase: "generating", message_de: "Antwort wird erzeugt …" });
    if (emptyCase) {
      // An empty answer ends with `error` (empty_answer), never with `done`.
      setTimeout(() => fail("Das Modell hat eine leere Antwort geliefert.", "empty_answer", { retryable: true }), 120);
      return;
    }
    let t = 0;
    const tick = () => {
      if (gen.stopped) return finish("stopped");
      if (t < thinkWords.length) {
        if (!gen.closed) sseSend(res, "thinking", { text: thinkWords[t] });
        t += 1;
        return setTimeout(tick, 30);
      }
      if (i >= words.length) {
        if (errorCase) return fail("Die Laufzeit hat die Verbindung beendet (Testfehler).");
        return finish("complete");
      }
      if (firstTokenAt === null) {
        firstTokenAt = Date.now();
        timings.first_token_ms = firstTokenAt - started;
      }
      assistant.content += words[i];
      if (!gen.closed) sseSend(res, "token", { text: words[i] });
      i += 1;
      const nowMs = Date.now();
      if (nowMs - lastProgressAt >= 250) {
        lastProgressAt = nowMs;
        act("generating", "progress", "Antwort wird geschrieben", { counters: { tokens: i, tokens_per_second: tps() } });
      }
      setTimeout(tick, longCase ? 12 : 35);
    };
    tick();
  };

  // v1.2 live phases before the regular timeline (only for /live/sessions/{id}/ask).
  let base = 0;
  if (live) {
    const snap = live.snapshot;
    base = live.useImage ? 420 : 260;
    setTimeout(() => act("capture", "started", "Quelle wird aufgenommen"), 10);
    setTimeout(() => {
      if (gen.stopped) return finish("stopped");
      act("capture", "done", snap.quality === "ok" ? "Quelle aufgenommen" : `Quelle aufgenommen (${snap.quality})`, { counters: { width: snap.target.rect.w, height: snap.target.rect.h } });
      act("observe", "started", "Struktur wird gelesen");
    }, 70);
    setTimeout(() => {
      if (gen.stopped) return finish("stopped");
      act("observe", "done", `${snap.elements_total} Elemente, ${snap.text_chars} Zeichen gelesen`, { counters: { elements: snap.elements_total, text_chars: snap.text_chars } });
      if (live.useImage) {
        act("image_encode", "started", "Bild wird vorbereitet", { counters: { image_total: 100, image_done: 0 } });
        setTimeout(() => act("image_encode", "progress", "Bild wird vorbereitet", { counters: { image_total: 100, image_done: 55 } }), 80);
        setTimeout(() => act("image_encode", "done", "Bild vorbereitet", { counters: { image_total: 100, image_done: 100 } }), 150);
      }
    }, 190);
    setTimeout(() => {
      if (gen.closed) return;
      sseSend(res, "observation", {
        snapshot_id: snap.snapshot_id,
        source_kind: snap.source_kind,
        quality: snap.quality,
        used_image: Boolean(live.useImage),
        used_structure: snap.elements_total > 0,
        elements: snap.elements_total,
        text_chars: snap.text_chars,
        age_ms: Date.now() - started,
      });
    }, base - 10);
  }

  // Phase timeline (measured against the wall clock of this mock, nothing invented beyond that).
  setTimeout(() => {
    act("memory_search", "started", "Gespeicherte Informationen werden gesucht");
    if (!gen.closed) sseSend(res, "status", { phase: "retrieving", message_de: "Gedächtnis wird durchsucht …" });
  }, base + 20);
  setTimeout(() => {
    if (gen.stopped) return finish("stopped");
    assistant.sources = facts.slice(0, 3);
    if (!gen.closed) sseSend(res, "sources", { sources: assistant.sources });
    timings.memory_search_ms = Date.now() - started - base - 20;
    act("memory_search", "done", `${assistant.sources.length} passende Quellen gefunden`, { counters: { sources: assistant.sources.length }, source_ids: assistant.sources.map((s) => s.id) });
    act("prompt_build", "started", "Kontext wird vorbereitet");
  }, base + 180);
  setTimeout(() => {
    if (gen.stopped) return finish("stopped");
    act("prompt_build", "done", "Kontext vorbereitet", { counters: { prompt_total: 412, prompt_cached: 64 } });
    const ppStart = Date.now();
    let processed = 0;
    act("prompt_processing", "started", "Kontext wird verarbeitet", { counters: { prompt_total: 412, prompt_processed: 0, prompt_cached: 64 } });
    const pp = () => {
      if (gen.stopped) return finish("stopped");
      processed = Math.min(412, processed + 96);
      if (processed >= 412) {
        timings.prompt_processing_ms = Date.now() - ppStart;
        act("prompt_processing", "done", "Kontext verarbeitet", { counters: { prompt_total: 412, prompt_processed: 412, prompt_cached: 64 } });
        return generate();
      }
      act("prompt_processing", "progress", "Kontext wird verarbeitet", { counters: { prompt_total: 412, prompt_processed: processed, prompt_cached: 64 } });
      setTimeout(pp, 110);
    };
    setTimeout(pp, 110);
  }, base + 260);
}

// ---------------------------------------------------------------------------
// HTTP plumbing
// ---------------------------------------------------------------------------
function cors(req, res) {
  res.setHeader("Access-Control-Allow-Origin", req.headers.origin ?? "*");
  res.setHeader("Access-Control-Allow-Headers", "X-SpAIder-Token, Content-Type");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, PATCH, PUT, DELETE, OPTIONS");
  res.setHeader("Access-Control-Max-Age", "600");
}
function json(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(body));
}
function error(res, status, detail, code, extra = {}) {
  json(res, status, { detail, code, ...extra });
}
function sseStart(res) {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
  res.write(": stream open\n\n");
}
function sseSend(res, event, data) {
  if (res.writableEnded || res.destroyed) return;
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}
function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
  });
}
function parseMultipart(buffer, contentType) {
  const m = /boundary=("?)([^";]+)\1/.exec(contentType ?? "");
  if (!m) return [];
  const boundary = Buffer.from(`--${m[2]}`);
  const parts = [];
  let pos = buffer.indexOf(boundary);
  while (pos !== -1) {
    const start = pos + boundary.length;
    if (buffer.slice(start, start + 2).toString() === "--") break;
    const headerEnd = buffer.indexOf("\r\n\r\n", start);
    if (headerEnd === -1) break;
    const headers = buffer.slice(start + 2, headerEnd).toString();
    const next = buffer.indexOf(boundary, headerEnd);
    const body = buffer.slice(headerEnd + 4, next === -1 ? buffer.length : next - 2);
    const name = /name="([^"]*)"/.exec(headers)?.[1];
    const filename = /filename="([^"]*)"/.exec(headers)?.[1];
    const type = /Content-Type:\s*([^\r\n]+)/i.exec(headers)?.[1];
    parts.push({ name, filename, type, body });
    pos = next;
  }
  return parts;
}

const routes = [];
const route = (method, pattern, handler) => routes.push({ method, pattern, handler });

// health / setup / system
// v1.3 (0.4): providers, connections, credentials, projects, bindings, model library
const hub = registerModelHub({ route, json, error, state, now, offline: OFFLINE });

route("GET", /^\/api\/health$/, (ctx) => json(ctx.res, 200, { status: "ok", version: "0.3.0-mock", app_root: "D:\\SpAIder", data_dir: "D:\\SpAIder\\data" }));
route("GET", /^\/api\/setup\/status$/, (ctx) =>
  json(ctx.res, 200, {
    state: state.activeChat ? "ready" : "needs_model",
    active_chat_model: modelView(state.installed.get(state.activeChat)),
    active_embedding_model: modelView(state.installed.get(state.activeEmbedding)),
    runtime_state: state.runtime,
  }),
);
route("GET", /^\/api\/system\/info$/, (ctx) => {
  const models = [...state.installed.values()].reduce((a, m) => a + m.size_bytes, 0);
  json(ctx.res, 200, {
    os: "windows",
    arch: "x64",
    hostname: "LAPTOP-MOCK",
    cpu: "Intel Core Ultra 7 268V",
    threads: 8,
    ram_total_bytes: 32 * GB,
    ram_available_bytes: 19.4 * GB,
    disk_total_bytes: 476 * GB,
    disk_free_bytes: state.diskFree - models,
    filesystem: "exFAT",
    max_file_bytes: args.includes("--fat32") ? 4294967295 : null,
    app_root: "D:\\SpAIder",
    data_dir: "D:\\SpAIder\\data",
    usage: { models_bytes: models, db_bytes: 48 * MB, imports_bytes: 3 * MB, logs_bytes: 512 * 1024, downloads_bytes: 0 },
    runtime: { name: "llama.cpp", build: "b12032", variant: hardwareProfile().active_backend, path: "D:\\SpAIder\\resources\\runtime\\llama\\llama-server.exe" },
  });
});
route("GET", /^\/api\/system\/hardware$/, (ctx) => json(ctx.res, 200, hardwareProfile()));
route("GET", /^\/api\/system\/eject-status$/, (ctx) => {
  const running = [...state.jobs.values()].filter((j) => j.state === "running").length;
  const ready = !state.generation && running === 0;
  json(ctx.res, 200, { ready, active_streams: state.generation ? 1 : 0, runtime_processes: state.runtime === "ready" ? 1 : 0, db_open: true, running_jobs: running, message_de: ready ? "Bereit zum Entfernen" : "Es laufen noch Vorgänge" });
});
route("POST", /^\/api\/system\/shutdown$/, (ctx) => {
  console.error("[mock] shutdown requested");
  json(ctx.res, 200, { ok: true });
  if (EXIT_ON_SHUTDOWN) setTimeout(() => process.exit(0), 300);
});
route("GET", /^\/api\/system\/logs$/, (ctx) => json(ctx.res, 200, { lines: ["[mock] core started", "[mock] ready"] }));

// activity (global channel)
route("GET", /^\/api\/activity$/, (ctx) => {
  sseStart(ctx.res);
  sseSend(ctx.res, "scheduler", schedulerSnapshot());
  sseSend(ctx.res, "runtime", runtimeSnapshot());
  activityListeners.add(ctx.res);
  const keepAlive = setInterval(() => {
    if (ctx.res.writableEnded || ctx.res.destroyed) return clearInterval(keepAlive);
    ctx.res.write(": keep-alive\n\n");
  }, 15000);
  ctx.req.on("close", () => {
    clearInterval(keepAlive);
    activityListeners.delete(ctx.res);
  });
});

// settings
route("GET", /^\/api\/settings$/, (ctx) => json(ctx.res, 200, state.settings));
route("PATCH", /^\/api\/settings$/, (ctx) => {
  const patch = ctx.body ?? {};
  if (patch.context_tokens !== undefined && ![4096, 8192, 16384, 32768].includes(patch.context_tokens)) return error(ctx.res, 422, "Ungültige Kontextlänge.", "invalid_setting");
  if (patch.temperature !== undefined && (patch.temperature < 0 || patch.temperature > 2)) return error(ctx.res, 422, "Temperatur muss zwischen 0 und 2 liegen.", "invalid_setting");
  if (patch.performance_mode !== undefined && !["auto", "quiet", "fast"].includes(patch.performance_mode)) return error(ctx.res, 422, "Ungültiger Leistungsmodus.", "invalid_setting");
  if (patch.gpu_mode !== undefined && !["auto", "off"].includes(patch.gpu_mode)) return error(ctx.res, 422, "Ungültiger GPU-Modus.", "invalid_setting");
  // v1.2
  for (const key of ["hotkey_overlay", "hotkey_stop"]) {
    if (patch[key] !== undefined && (typeof patch[key] !== "string" || !patch[key].trim() || patch[key].length > 64)) return error(ctx.res, 422, "Ungültige Tastenkombination.", "invalid_setting");
  }
  for (const key of ["tray_mode", "live_preview"]) {
    if (patch[key] !== undefined && typeof patch[key] !== "boolean") return error(ctx.res, 422, `Ungültiger Wert für ${key}.`, "invalid_setting");
  }
  if (patch.agent_max_steps !== undefined && (!Number.isInteger(patch.agent_max_steps) || patch.agent_max_steps < 1 || patch.agent_max_steps > 500)) return error(ctx.res, 422, "Schritte: 1 bis 500.", "invalid_setting");
  if (patch.agent_max_seconds !== undefined && (!Number.isInteger(patch.agent_max_seconds) || patch.agent_max_seconds < 10 || patch.agent_max_seconds > 86400)) return error(ctx.res, 422, "Zeitlimit: 10 bis 86400 Sekunden.", "invalid_setting");
  Object.assign(state.settings, patch);
  json(ctx.res, 200, state.settings);
});

// models
const allEntries = () => [...catalog, ...state.dynamicEntries.values()];
const findEntry = (id) => catalog.find((e) => e.id === id) ?? state.dynamicEntries.get(id) ?? null;

route("GET", /^\/api\/models\/catalog$/, (ctx) => json(ctx.res, 200, { entries: catalog, catalog_date: state.catalogDate, local_extensions: state.dynamicEntries.size }));
route("GET", /^\/api\/models\/installed$/, (ctx) => json(ctx.res, 200, { models: [...state.installed.values()].map(modelView) }));
route("GET", /^\/api\/models\/active$/, (ctx) => json(ctx.res, 200, { chat: modelView(state.installed.get(state.activeChat)), embedding: modelView(state.installed.get(state.activeEmbedding)), runtime_state: state.runtime, error: null }));

const REASONS = {
  alltag: {
    balanced: ["Gute Qualität bei 24 Token/s auf dieser Hardware", "Nur 1,7 GB Download, 2,3 GB Arbeitsspeicher bei 8k Kontext", "Offiziell konvertiert und auf dieser Laufzeit getestet"],
    fast: ["Sehr schnell und sparsam (unter 1 GB)", "Für kurze Fragen und Zusammenfassungen ausreichend", "Läuft auch im Akkubetrieb flüssig"],
    quality: ["Deutlich bessere Antworten bei komplexen Fragen", "Passt knapp in 32 GB Arbeitsspeicher (etwa 20 GB bei 8k Kontext)", "Community-Konvertierung, nach Metadaten geprüft"],
  },
  dokumente: {
    balanced: ["Großer Kontext (32k) für lange Dokumente", "Zuverlässig beim Zitieren von Quellen", "Auf dieser Laufzeit getestet"],
    fast: ["Für kurze Dokumentfragen ausreichend", "Sehr geringer Speicherbedarf", "Schnell beim Verarbeiten langer Kontexte"],
    quality: ["Beste Zusammenfassungen langer Berichte", "Großer Kontext, aber langsam auf dieser Hardware", "Nach Metadaten geprüft"],
  },
  coding: {
    balanced: ["Solide bei Code-Erklärungen und kleinen Beispielen", "Reasoning-Modus für schrittweise Lösungen", "Auf dieser Laufzeit getestet"],
    fast: ["Für einfache Syntaxfragen ausreichend", "Sehr schnell, aber begrenzt bei größeren Aufgaben"],
    quality: ["Beste Ergebnisse bei komplexem Code", "Langsam, aber passend für längere Sitzungen", "Nach Metadaten geprüft"],
  },
};
route("GET", /^\/api\/models\/recommendations$/, (ctx) => {
  const focus = ["alltag", "dokumente", "coding"].includes(ctx.url.searchParams.get("focus")) ? ctx.url.searchParams.get("focus") : "alltag";
  const hw = hardwareProfile();
  const pick = (slot, id, measured) => {
    const entry = catalog.find((e) => e.id === id);
    return {
      slot,
      entry,
      reasons_de: REASONS[focus][slot],
      estimate: { ram_bytes: entry.resources.est_ram_bytes_8k, vram_bytes: hw.gpus.length ? entry.resources.est_vram_bytes_8k : 0, download_bytes: entry.total_bytes, context_tokens: 8192 },
      measured,
      installed_model_id: entry.installed_model_id,
    };
  };
  const balancedModel = [...state.installed.values()].find((m) => m.catalog_id === "qwen3-1.7b-q8_0" && m.measured);
  const measuredBalanced = balancedModel?.measured ?? {
    backend: hw.active_backend,
    tokens_per_second: hw.active_backend === "vulkan" ? 24.3 : 11.8,
    time_to_first_token_ms: 310,
    prompt_tokens_per_second: hw.active_backend === "vulkan" ? 420 : 165,
    measured_at: "2026-10-28T14:12:00Z",
    fingerprint: FINGERPRINT,
  };
  json(ctx.res, 200, {
    hardware: hw,
    picks: [pick("balanced", "qwen3-1.7b-q8_0", measuredBalanced), pick("fast", "qwen3-0.6b-q8_0", null), pick("quality", "qwen3.8-27b-community", null)],
    excluded: [
      { id: "gemma-3-1b-it-qat", reason_de: "Zugang nur mit Hugging-Face-Konto und Zustimmung zu den Gemma-Bedingungen." },
      { id: "qwen3-embedding-0.6b-q8_0", reason_de: "Embedding-Modell, kein Chat-Modell." },
    ],
    catalog_date: state.catalogDate,
  });
});
route("GET", /^\/api\/models\/search$/, (ctx) => {
  const q = (ctx.url.searchParams.get("q") ?? "").trim().toLowerCase();
  const limit = Number(ctx.url.searchParams.get("limit") ?? 20);
  const offline = OFFLINE || ctx.url.searchParams.get("offline") === "1" || q.includes("offline");
  if (offline) return json(ctx.res, 200, { results: [], cached_at: null, offline: true });
  const terms = q.split(/\s+/).filter(Boolean);
  const results = SEARCH_INDEX.filter((r) => terms.every((t) => (r.repo + " " + r.name).toLowerCase().includes(t)))
    .slice(0, limit)
    .map((r) => ({ ...r, status: "unverified" }));
  json(ctx.res, 200, { results, cached_at: now(), offline: false });
});
route("POST", /^\/api\/models\/resolve$/, (ctx) => {
  const repo = String(ctx.body?.repo ?? "").trim();
  const file = ctx.body?.file ? String(ctx.body.file).trim() : null;
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) return error(ctx.res, 422, "Ungültige Repository-ID (erwartet „Autor/Name“).", "invalid_repo");
  if (/missing/i.test(repo)) return error(ctx.res, 404, `Repository ${repo} wurde auf Hugging Face nicht gefunden.`, "not_found");
  if (/gated|meta-llama|google\/gemma/i.test(repo)) {
    return error(ctx.res, 409, `Das Repository ${repo} ist zugangsbeschränkt (gated).`, "gated", { requires_account: true });
  }
  if (OFFLINE) return error(ctx.res, 503, "Keine Netzwerkverbindung.", "offline");
  const split = /split|30B-A3B/i.test(repo);
  const base = /base|nochat/i.test(repo);
  const vision = /vision|VL/i.test(repo);
  const name = repo.split("/")[1].replace(/-GGUF.*/i, "");
  const files = split
    ? [
        { path: file ?? `${name}-Q4_K_M-00001-of-00002.gguf`, size: 9_200_000_000, sha256: "11".repeat(32) },
        { path: `${name}-Q4_K_M-00002-of-00002.gguf`, size: 8_900_000_000, sha256: null },
      ]
    : [{ path: file ?? `${name}-Q4_K_M.gguf`, size: /8B/i.test(repo) ? 4_900_000_000 : 2_500_000_000, sha256: /unsloth/i.test(repo) ? null : "22".repeat(32) }];
  const total = files.reduce((a, f) => a + f.size, 0);
  const id = `hf:${repo}:${files[0].path}`;
  const type = base ? "base" : vision ? "vision" : "chat";
  const ok = type === "chat";
  const fit =
    total > 24 * GB
      ? { level: "too_large", message_de: "Zu groß für 32 GB Arbeitsspeicher." }
      : total > 12 * GB
        ? { level: "tight", message_de: "Knapp: benötigt etwa 20 GB freien Arbeitsspeicher." }
        : { level: "ok", message_de: "Passt zu 32 GB Arbeitsspeicher." };
  const existing = state.dynamicEntries.get(id);
  const entry = {
    id,
    name: `${name} (${split ? "Q4_K_M, 2 Teile" : "Q4_K_M"})`,
    role: "chat",
    family: name.split("-")[0],
    publisher: repo.split("/")[0],
    source: /^(Qwen|ggml-org|mistralai)\//.test(repo) ? "official" : "community",
    repo,
    revision: "abcdef0123456789abcdef0123456789abcdef01",
    repo_url: `https://huggingface.co/${repo}`,
    files,
    total_bytes: total,
    license: LICENSE_APACHE(`https://huggingface.co/${repo}`),
    architecture: vision ? "qwen2vl" : "qwen3",
    parameters: /8B/i.test(repo) ? "8B" : split ? "30B-A3B" : "4B",
    quantization: "Q4_K_M",
    context_length: 40960,
    embedding_dimensions: null,
    min_ram_bytes: Math.round(total * 1.1),
    recommended_ram_bytes: Math.round(total * 1.5),
    support: "documented",
    description_de: "Dynamisch aus Hugging Face aufgelöst; GGUF-Header per Bereichsabfrage gelesen.",
    fit,
    installed_model_id: existing?.installed_model_id ?? null,
    original_model: repo.replace(/-GGUF.*/i, ""),
    converter: repo.split("/")[0],
    verification: { status: "metadata", date: now(), scope: "GGUF-Header per Range-Request gelesen" },
    evidence: [],
    resources: { weights_bytes: total, kv_bytes_per_token_est: 36864, est_ram_bytes_8k: Math.round(total * 1.15), est_vram_bytes_8k: Math.round(total * 1.1) },
    template: { reasoning_toggle: base ? null : "enable_thinking", system_role: !base },
    type,
    families: [name.split("-")[0]],
    published: null,
    checked_at: now(),
    source_kind: "search",
  };
  state.dynamicEntries.set(id, entry);
  const reasons = ok
    ? ["Architektur qwen3 wird von llama.cpp b12032 unterstützt", "Chat-Template im GGUF-Header vorhanden", files.some((f) => !f.sha256) ? "Nicht alle Dateien haben eine veröffentlichte Prüfsumme" : "Prüfsummen für alle Dateien vorhanden"]
    : base
      ? ["Kein Chat-Template im GGUF-Header: Basis-Modell, nicht für Chat geeignet"]
      : ["Vision-Modell: benötigt einen Projektor, der von dieser Laufzeit nicht unterstützt wird"];
  json(ctx.res, 200, { entry, files, compatibility: { ok, reasons_de: reasons, architecture: entry.architecture, context_length: entry.context_length, type }, fit });
});
route("POST", /^\/api\/models\/catalog\/refresh$/, (ctx) => {
  if (OFFLINE) return error(ctx.res, 503, "Keine Netzwerkverbindung – der Katalog konnte nicht geprüft werden.", "offline");
  setTimeout(() => {
    state.catalogDate = now();
    for (const e of catalog) e.checked_at = state.catalogDate;
    json(ctx.res, 200, { catalog_date: state.catalogDate, checked: catalog.length, changed: 0, errors: [] });
  }, 800);
});
route("POST", /^\/api\/models\/download$/, (ctx) => {
  const entry = findEntry(ctx.body?.catalog_id);
  if (!entry) return error(ctx.res, 404, "Modell nicht im Katalog.", "not_found");
  if (entry.installed_model_id) return error(ctx.res, 409, "Dieses Modell ist bereits installiert.", "already_installed");
  if (entry.license?.gated || entry.license?.requires_account) return error(ctx.res, 409, "Dieses Modell ist zugangsbeschränkt und erfordert ein Hugging-Face-Konto.", "gated", { requires_account: true });
  const running = [...state.jobs.values()].find((j) => j.kind === "download" && j.catalog_id === entry.id && ["queued", "running"].includes(j.state));
  if (running) return error(ctx.res, 409, "Für dieses Modell läuft bereits ein Download.", "job_running");
  if (entry.id === "qwen3.8-27b-community" && !state.spaceErrorShown) {
    state.spaceErrorShown = true;
    return error(ctx.res, 409, "Nicht genug freier Speicherplatz auf dem Datenträger.", "insufficient_space", { required_bytes: entry.total_bytes + 2 * GB, free_bytes: 12 * GB });
  }
  const job = createJob("download", { catalog_id: entry.id, progress: { done_bytes: 0, total_bytes: entry.total_bytes, speed_bps: 0, eta_seconds: null } });
  runDownload(job, entry, entry.id === "qwen3.8-27b-community" ? 0.3 : null);
  json(ctx.res, 202, { job });
});
route("POST", /^\/api\/models\/import$/, (ctx) => {
  if (!ctx.body?.path) return error(ctx.res, 422, "Pfad fehlt.", "invalid_path");
  const job = createJob("import");
  runImport(job, ctx.body.path);
  json(ctx.res, 202, { job });
});
route("POST", /^\/api\/models\/activate$/, (ctx) => {
  const model = state.installed.get(ctx.body?.model_id);
  if (!model) return error(ctx.res, 404, "Modell nicht gefunden.", "not_found");
  const job = createJob("activate", { model_id: model.id });
  runActivate(job, model);
  json(ctx.res, 202, { job });
});
route("POST", /^\/api\/models\/([^/]+)\/calibrate$/, (ctx) => {
  const model = state.installed.get(ctx.params[0]);
  if (!model) return error(ctx.res, 404, "Modell nicht gefunden.", "not_found");
  if (model.role !== "chat") return error(ctx.res, 409, "Nur Chat-Modelle können gemessen werden.", "not_chat_model");
  const running = [...state.jobs.values()].find((j) => j.kind === "calibrate" && j.model_id === model.id && ["queued", "running"].includes(j.state));
  if (running) return error(ctx.res, 409, "Für dieses Modell läuft bereits eine Messung.", "job_running");
  if (state.generation) return error(ctx.res, 409, "Während einer Antwort kann nicht gemessen werden.", "generation_running");
  const job = createJob("calibrate", { model_id: model.id });
  runCalibrate(job, model);
  json(ctx.res, 202, { job });
});
route("DELETE", /^\/api\/models\/([^/]+)$/, (ctx) => {
  const model = state.installed.get(ctx.params[0]);
  if (!model) return error(ctx.res, 404, "Modell nicht gefunden.", "not_found");
  if (model.id === state.activeChat || model.id === state.activeEmbedding) return error(ctx.res, 409, "Das aktive Modell kann nicht gelöscht werden.", "model_active");
  state.installed.delete(model.id);
  for (const e of allEntries()) if (e.installed_model_id === model.id) e.installed_model_id = null;
  json(ctx.res, 200, { ok: true });
});
route("GET", /^\/api\/models\/jobs$/, (ctx) => json(ctx.res, 200, { jobs: [...state.jobs.values()] }));
route("GET", /^\/api\/models\/jobs\/([^/]+)$/, (ctx) => {
  const job = state.jobs.get(ctx.params[0]);
  return job ? json(ctx.res, 200, { job }) : error(ctx.res, 404, "Job nicht gefunden.", "not_found");
});
route("POST", /^\/api\/models\/jobs\/([^/]+)\/cancel$/, (ctx) => {
  const job = state.jobs.get(ctx.params[0]);
  if (!job) return error(ctx.res, 404, "Job nicht gefunden.", "not_found");
  clearTimeout(timers.get(job.id));
  if (["queued", "running"].includes(job.state)) updateJob(job, { state: "cancelled", message_de: "Abgebrochen", progress: { ...job.progress, speed_bps: 0, eta_seconds: null } });
  json(ctx.res, 200, { job });
});
route("POST", /^\/api\/models\/jobs\/([^/]+)\/resume$/, (ctx) => {
  const job = state.jobs.get(ctx.params[0]);
  if (!job) return error(ctx.res, 404, "Job nicht gefunden.", "not_found");
  if (!["paused", "failed"].includes(job.state) || job.kind !== "download") return error(ctx.res, 409, "Dieser Job kann nicht fortgesetzt werden.", "job_not_resumable");
  const entry = findEntry(job.catalog_id);
  job.error = null;
  updateJob(job, { state: "running", phase: "download", message_de: "Download wird fortgesetzt" });
  const total = entry.total_bytes;
  const step = Math.ceil(total / 40);
  const tick = () => {
    if (job.state !== "running") return;
    const done = Math.min(total, job.progress.done_bytes + step);
    updateJob(job, { progress: { done_bytes: done, total_bytes: total, speed_bps: step * 4, eta_seconds: Math.round((total - done) / (step * 4)) } });
    if (done >= total) {
      updateJob(job, { phase: "verify", message_de: "Prüfsumme wird berechnet" });
      setTimeout(() => {
        const model = installModel(entry);
        updateJob(job, { state: "done", phase: null, message_de: "Download abgeschlossen", result_model_id: model.id });
      }, 500);
      return;
    }
    timers.set(job.id, setTimeout(tick, 250));
  };
  timers.set(job.id, setTimeout(tick, 250));
  json(ctx.res, 200, { job });
});
route("GET", /^\/api\/models\/jobs\/([^/]+)\/events$/, (ctx) => {
  const job = state.jobs.get(ctx.params[0]);
  if (!job) return error(ctx.res, 404, "Job nicht gefunden.", "not_found");
  sseStart(ctx.res);
  sseSend(ctx.res, "job", job);
  if (job.finished_at) return ctx.res.end();
  if (!jobListeners.has(job.id)) jobListeners.set(job.id, new Set());
  jobListeners.get(job.id).add(ctx.res);
  ctx.req.on("close", () => jobListeners.get(job.id)?.delete(ctx.res));
});

// conversations
const convList = () => [...state.conversations.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).map(convView);
route("GET", /^\/api\/conversations$/, (ctx) => json(ctx.res, 200, { conversations: convList() }));
route("POST", /^\/api\/conversations$/, (ctx) => {
  const conv = { id: randomUUID(), title: ctx.body?.title || "Neues Gespräch", created_at: now(), updated_at: now(), message_count: 0, last_model_id: null, project_id: "default", binding: null };
  state.conversations.set(conv.id, conv);
  state.messages.set(conv.id, []);
  json(ctx.res, 201, convView(conv));
});
route("GET", /^\/api\/conversations\/([^/]+)$/, (ctx) => {
  const conv = state.conversations.get(ctx.params[0]);
  if (!conv) return error(ctx.res, 404, "Gespräch nicht gefunden.", "not_found");
  json(ctx.res, 200, { conversation: convView(conv), messages: state.messages.get(conv.id) });
});
route("PATCH", /^\/api\/conversations\/([^/]+)$/, (ctx) => {
  const conv = state.conversations.get(ctx.params[0]);
  if (!conv) return error(ctx.res, 404, "Gespräch nicht gefunden.", "not_found");
  conv.title = String(ctx.body?.title ?? conv.title);
  conv.updated_at = now();
  json(ctx.res, 200, convView(conv));
});
route("DELETE", /^\/api\/conversations\/([^/]+)$/, (ctx) => {
  if (!state.conversations.delete(ctx.params[0])) return error(ctx.res, 404, "Gespräch nicht gefunden.", "not_found");
  state.messages.delete(ctx.params[0]);
  json(ctx.res, 200, { ok: true });
});
route("POST", /^\/api\/conversations\/([^/]+)\/messages$/, (ctx) => {
  const conv = state.conversations.get(ctx.params[0]);
  if (!conv) return error(ctx.res, 404, "Gespräch nicht gefunden.", "not_found");
  if (!state.activeChat) return error(ctx.res, 409, "Kein Chat-Modell aktiv.", "no_model");
  if (state.runtime !== "ready") return error(ctx.res, 409, "Die Laufzeit ist noch nicht bereit.", "runtime_not_ready");
  if (state.generation) return error(ctx.res, 409, "Es läuft bereits eine Antwort.", "generation_running");
  const content = String(ctx.body?.content ?? "").trim();
  if (!content) return error(ctx.res, 422, "Leere Nachricht.", "empty_message");
  startGeneration(conv.id, content, ctx.res);
});
route("POST", /^\/api\/conversations\/([^/]+)\/stop$/, (ctx) => {
  const gen = state.generation;
  if (gen && gen.conversationId === ctx.params[0]) {
    gen.stopped = true;
    return json(ctx.res, 200, { ok: true, stopped: true });
  }
  json(ctx.res, 200, { ok: true, stopped: false });
});
route("GET", /^\/api\/conversations\/([^/]+)\/messages\/([^/]+)\/sources$/, (ctx) => {
  const msg = (state.messages.get(ctx.params[0]) ?? []).find((m) => m.id === ctx.params[1]);
  return msg ? json(ctx.res, 200, { sources: msg.sources }) : error(ctx.res, 404, "Nachricht nicht gefunden.", "not_found");
});
route("GET", /^\/api\/conversations\/([^/]+)\/messages\/([^/]+)\/activity$/, (ctx) => {
  const msg = (state.messages.get(ctx.params[0]) ?? []).find((m) => m.id === ctx.params[1]);
  if (!msg) return error(ctx.res, 404, "Nachricht nicht gefunden.", "not_found");
  json(ctx.res, 200, { events: state.requests.get(msg.id)?.events ?? [] });
});

// documents
route("GET", /^\/api\/documents$/, (ctx) => json(ctx.res, 200, { documents: [...state.documents.values()] }));
route("POST", /^\/api\/documents$/, (ctx) => {
  const parts = parseMultipart(ctx.raw, ctx.req.headers["content-type"]);
  const file = parts.find((p) => p.name === "file");
  if (!file || !file.filename) return error(ctx.res, 422, "Keine Datei übermittelt.", "missing_file");
  const ext = (file.filename.split(".").pop() ?? "").toLowerCase();
  if (!["txt", "md", "markdown", "pdf"].includes(ext)) return error(ctx.res, 415, "Dateityp wird nicht unterstützt (.txt, .md, .markdown, .pdf).", "unsupported_type");
  if (file.body.length > 50 * MB) return error(ctx.res, 413, "Datei ist größer als 50 MB.", "file_too_large");
  const conversationId = parts.find((p) => p.name === "conversation_id")?.body.toString() || null;
  const scanned = /scan/i.test(file.filename);
  const doc = {
    id: randomUUID(),
    name: file.filename,
    content_type: ext === "pdf" ? "application/pdf" : ext === "txt" ? "text/plain" : "text/markdown",
    size_bytes: file.body.length,
    text_chars: scanned ? 0 : file.body.length,
    status: "imported",
    warnings: scanned ? [{ code: "no_text_layer", message_de: "Kein Textinhalt erkannt (gescannte PDF?)" }] : [],
    conversation_id: conversationId && state.conversations.has(conversationId) ? conversationId : null,
    created_at: now(),
    facts_count: 0,
    progress: { done: 0, total: 0 },
  };
  state.documents.set(doc.id, doc);
  setTimeout(() => runIndexing(doc), 300);
  json(ctx.res, 201, doc);
});
route("GET", /^\/api\/documents\/([^/]+)$/, (ctx) => {
  const doc = state.documents.get(ctx.params[0]);
  return doc ? json(ctx.res, 200, doc) : error(ctx.res, 404, "Dokument nicht gefunden.", "not_found");
});
route("GET", /^\/api\/documents\/([^/]+)\/text$/, (ctx) => json(ctx.res, 200, { text: "Beispieltext", total_chars: 12 }));
route("DELETE", /^\/api\/documents\/([^/]+)$/, (ctx) => {
  if (!state.documents.delete(ctx.params[0])) return error(ctx.res, 404, "Dokument nicht gefunden.", "not_found");
  json(ctx.res, 200, { ok: true });
});
route("GET", /^\/api\/documents\/([^/]+)\/events$/, (ctx) => {
  const doc = state.documents.get(ctx.params[0]);
  if (!doc) return error(ctx.res, 404, "Dokument nicht gefunden.", "not_found");
  sseStart(ctx.res);
  sseSend(ctx.res, "document", doc);
  if (doc.status === "indexed" || doc.status === "failed") return ctx.res.end();
  if (!docListeners.has(doc.id)) docListeners.set(doc.id, new Set());
  docListeners.get(doc.id).add(ctx.res);
  ctx.req.on("close", () => docListeners.get(doc.id)?.delete(ctx.res));
});

// memory
route("GET", /^\/api\/memory\/overview$/, (ctx) =>
  json(ctx.res, 200, {
    facts: 42,
    entities: 17,
    relations: 23,
    superseded: 3,
    documents: state.documents.size,
    notes: state.notes.size,
    conversations: state.conversations.size,
    messages: [...state.messages.values()].reduce((a, l) => a + l.length, 0),
    index: { fts: true, vectors: { enabled: Boolean(state.activeEmbedding), model_id: state.activeEmbedding, dimensions: state.activeEmbedding ? 1024 : null, count: state.activeEmbedding ? 42 : 0 } },
  }),
);
route("GET", /^\/api\/memory\/search$/, (ctx) => {
  const q = (ctx.url.searchParams.get("q") ?? "").toLowerCase();
  const noteResults = [...state.notes.values()].filter((n) => n.text.toLowerCase().includes(q)).map((n) => ({ kind: "note", id: n.id, label: n.text.slice(0, 40), excerpt: n.text, status: "current", origin: { kind: "note", id: n.id, title: "Notiz", at: n.updated_at }, superseded_by: null, score: 0.9 }));
  const factResults = facts.filter((f) => (f.label + f.excerpt).toLowerCase().includes(q)).map((f, i) => ({ ...f, score: 0.8 - i * 0.1 }));
  json(ctx.res, 200, { results: [...noteResults, ...factResults] });
});
route("GET", /^\/api\/memory\/notes$/, (ctx) => json(ctx.res, 200, { notes: [...state.notes.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at)) }));
route("POST", /^\/api\/memory\/notes$/, (ctx) => {
  const text = String(ctx.body?.text ?? "").trim();
  if (!text) return error(ctx.res, 422, "Leere Notiz.", "invalid_note");
  const note = { id: randomUUID(), text, created_at: now(), updated_at: now() };
  state.notes.set(note.id, note);
  json(ctx.res, 201, note);
});
route("PATCH", /^\/api\/memory\/notes\/([^/]+)$/, (ctx) => {
  const note = state.notes.get(ctx.params[0]);
  if (!note) return error(ctx.res, 404, "Notiz nicht gefunden.", "not_found");
  note.text = String(ctx.body?.text ?? note.text);
  note.updated_at = now();
  json(ctx.res, 200, note);
});
route("DELETE", /^\/api\/memory\/notes\/([^/]+)$/, (ctx) => {
  if (!state.notes.delete(ctx.params[0])) return error(ctx.res, 404, "Notiz nicht gefunden.", "not_found");
  json(ctx.res, 200, { ok: true });
});
route("GET", /^\/api\/memory\/facts$/, (ctx) => json(ctx.res, 200, { facts, total: facts.length }));

// v1.2: live sources / sessions, agent tasks, recipes, global stop
function createConversation(title) {
  const conv = { id: randomUUID(), title, created_at: now(), updated_at: now(), message_count: 0, last_model_id: null };
  state.conversations.set(conv.id, conv);
  state.messages.set(conv.id, []);
  return conv;
}
registerLiveAgent({ route, json, error, sseStart, sseSend, state, broadcast, now, startGeneration, createConversation, createJob, updateJob, timers, args });

const server = http.createServer(async (req, res) => {
  cors(req, res);
  if (req.method === "OPTIONS") {
    res.writeHead(204);
    return res.end();
  }
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  if (req.headers["x-spaider-token"] !== TOKEN) return error(res, 401, "Zugriff verweigert.", "unauthorized");
  const raw = ["POST", "PATCH", "PUT"].includes(req.method) ? await readBody(req) : Buffer.alloc(0);
  let body = null;
  if ((req.headers["content-type"] ?? "").includes("application/json") && raw.length) {
    try {
      body = JSON.parse(raw.toString("utf8"));
    } catch {
      return error(res, 400, "Ungültiges JSON.", "invalid_json");
    }
  }
  for (const r of routes) {
    if (r.method !== req.method) continue;
    const m = r.pattern.exec(url.pathname);
    if (!m) continue;
    try {
      return await r.handler({ req, res, url, body, raw, params: m.slice(1).map(decodeURIComponent) });
    } catch (e) {
      console.error("[mock] handler error", e);
      return error(res, 500, "Interner Fehler im Mock-Kern.", "internal_error");
    }
  }
  error(res, 404, "Unbekannter Endpunkt.", "not_found");
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(JSON.stringify({ event: "ready", port: PORT }));
  console.error(`[mock] SpAIder mock core listening on http://127.0.0.1:${PORT} (token ${TOKEN}, state ${INITIAL_STATE}${OFFLINE ? ", offline" : ""}${NO_GPU ? ", no-gpu" : ""})`);
});
