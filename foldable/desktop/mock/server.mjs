/**
 * Mock SpAIder core (no dependencies). Implements enough of
 * docs/portable/API_CONTRACT.md to drive every renderer screen.
 *
 *   node mock/server.mjs [--port 8765] [--token mock-token] [--state needs_model|ready] [--exit-on-shutdown]
 *
 * Prints {"event":"ready","port":N} on stdout when listening (like the real core).
 * Behaviour hooks for testing the UI:
 *   - catalog entry "qwen3.8-27b-community": first download -> 409 insufficient_space,
 *     second download fails at ~30 % (resumable with /resume), resume completes.
 *   - import path containing "invalid" -> job fails with gguf_invalid.
 *   - document name containing "scan" -> warning no_text_layer.
 *   - message text containing "fehler" -> stream ends with an error event.
 */
import http from "node:http";
import { randomUUID } from "node:crypto";

const args = process.argv.slice(2);
const argValue = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i !== -1 && args[i + 1] ? args[i + 1] : fallback;
};
const PORT = Number(argValue("port", 8765));
const TOKEN = process.env.SPAIDER_FOLDABLE_TOKEN ?? argValue("token", "mock-token");
const INITIAL_STATE = argValue("state", "needs_model");
const EXIT_ON_SHUTDOWN = args.includes("--exit-on-shutdown");

const now = () => new Date().toISOString();
const GB = 1024 * 1024 * 1024;

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
  generation: null, // { conversationId, stop }
  spaceErrorShown: false,
  failedOnce: false,
  diskFree: 120 * GB,
};

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
    license: { id: "apache-2.0", name: "Apache License 2.0", url: "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/blob/main/LICENSE" },
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
    license: { id: "apache-2.0", name: "Apache License 2.0", url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF" },
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
    license: { id: "apache-2.0", name: "Apache License 2.0", url: "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF" },
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
  },
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

if (INITIAL_STATE === "ready") {
  const m = installModel(catalog[0]);
  state.activeChat = m.id;
  state.runtime = "ready";
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
    sha256_verified: true,
    architecture: entry.architecture,
    quantization: entry.quantization,
    context_length: entry.context_length,
    embedding_dimensions: entry.embedding_dimensions,
    license: entry.license,
    state: "ready",
    active: false,
    created_at: now(),
    ...overrides,
  };
  state.installed.set(model.id, model);
  if (entry.id) {
    const c = catalog.find((e) => e.id === entry.id);
    if (c) c.installed_model_id = model.id;
  }
  return model;
}

function modelView(m) {
  if (!m) return null;
  return { ...m, active: m.id === state.activeChat || m.id === state.activeEmbedding };
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
      updateJob(job, { phase: "download", message_de: "Lädt von Hugging Face" });
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
  state.runtime = "starting";
  const tick = () => {
    if (job.state !== "running") return;
    i += 1;
    if (i >= phases.length) {
      if (model.role === "chat") state.activeChat = model.id;
      else state.activeEmbedding = model.id;
      state.runtime = "ready";
      updateJob(job, { state: "done", phase: null, message_de: "Modell aktiv", result_model_id: model.id });
      return;
    }
    updateJob(job, { phase: phases[i], message_de: phases[i] === "probe" ? "Testantwort wird erzeugt" : phases[i] === "commit" ? "Wird übernommen" : "Modell wird geladen" });
    timers.set(job.id, setTimeout(tick, 600));
  };
  timers.set(job.id, setTimeout(tick, 600));
}

function runImport(job, path) {
  const invalid = /invalid/i.test(path);
  const phases = ["copy", "copy", "verify", "probe"];
  let i = 0;
  const total = 900_000_000;
  updateJob(job, { state: "running", phase: "copy", message_de: "Datei wird kopiert", progress: { done_bytes: 0, total_bytes: total, speed_bps: 300_000_000, eta_seconds: 3 } });
  const tick = () => {
    if (job.state !== "running") return;
    i += 1;
    if (i >= phases.length) {
      if (invalid) {
        updateJob(job, { state: "failed", phase: null, message_de: "Import fehlgeschlagen", error: { detail: "Die Datei ist keine gültige GGUF-Datei.", code: "gguf_invalid" } });
        return;
      }
      const name = path.split(/[\\/]/).pop().replace(/\.gguf$/i, "");
      const model = installModel({ id: null, name, role: "chat", files: [{ path: `${name}.gguf`, sha256: null }], total_bytes: total, architecture: "qwen3", quantization: "Q4_K_M", context_length: 8192, embedding_dimensions: null, license: null }, { source: "import", catalog_id: null, sha256: null, sha256_verified: false });
      updateJob(job, { state: "done", phase: null, message_de: "Import abgeschlossen", result_model_id: model.id, progress: { ...job.progress, done_bytes: total } });
      return;
    }
    updateJob(job, { phase: phases[i], message_de: phases[i] === "verify" ? "GGUF-Header wird geprüft" : phases[i] === "probe" ? "Modell wird geprüft" : "Datei wird kopiert", progress: { ...job.progress, done_bytes: Math.min(total, Math.round((total * i) / 2)) } });
    timers.set(job.id, setTimeout(tick, 700));
  };
  timers.set(job.id, setTimeout(tick, 700));
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
// Chat streaming
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

function startGeneration(conversationId, content, res) {
  const conv = state.conversations.get(conversationId);
  const list = state.messages.get(conversationId);
  const userMessage = { id: randomUUID(), conversation_id: conversationId, role: "user", content, created_at: now(), status: "complete", model_id: null, sources: [], error: null, usage: null };
  const assistant = { id: randomUUID(), conversation_id: conversationId, role: "assistant", content: "", created_at: now(), status: "streaming", model_id: state.activeChat, sources: [], error: null, usage: null };
  list.push(userMessage, assistant);
  if (!conv.title || conv.title === "Neues Gespräch") conv.title = content.slice(0, 48);
  conv.message_count = list.length;
  conv.updated_at = now();
  conv.last_model_id = state.activeChat;

  sseStart(res);
  sseSend(res, "message", { user_message: userMessage, assistant_message: assistant });
  const gen = { conversationId, stopped: false, closed: false };
  state.generation = gen;
  res.on("close", () => {
    gen.closed = true;
  });

  const words = (/fehler/i.test(content) ? "Diese Antwort bricht gleich mit einem Fehler ab. " : MOCK_ANSWER).split(/(?<=\s)/);
  const started = Date.now();
  let i = 0;
  const finish = (status) => {
    assistant.status = status;
    assistant.usage = { prompt_tokens: 120, completion_tokens: i, time_to_first_token_ms: 310, tokens_per_second: Math.round((i / Math.max(1, (Date.now() - started) / 1000)) * 10) / 10 };
    conv.updated_at = now();
    state.generation = null;
    if (!gen.closed) {
      sseSend(res, "done", { message: assistant });
      res.end();
    }
  };
  setTimeout(() => sseSend(res, "status", { phase: "retrieving", message_de: "Gedächtnis wird durchsucht …" }), 50);
  setTimeout(() => {
    assistant.sources = facts.slice(0, 3);
    sseSend(res, "sources", { sources: assistant.sources });
    sseSend(res, "status", { phase: "generating", message_de: "Antwort wird erzeugt …" });
    const tick = () => {
      if (gen.stopped) return finish("stopped");
      if (i >= words.length) {
        if (/fehler/i.test(content)) {
          assistant.status = "error";
          assistant.error = "Die Laufzeit hat die Verbindung beendet (Testfehler).";
          state.generation = null;
          if (!gen.closed) {
            sseSend(res, "error", { detail: assistant.error, code: "runtime_error" });
            res.end();
          }
          return;
        }
        sseSend(res, "status", { phase: "memorizing", message_de: "Gedächtnis wird aktualisiert …" });
        setTimeout(() => finish("complete"), 600);
        return;
      }
      assistant.content += words[i];
      sseSend(res, "token", { text: words[i] });
      i += 1;
      setTimeout(tick, 35);
    };
    tick();
  }, 700);
}

// ---------------------------------------------------------------------------
// HTTP plumbing
// ---------------------------------------------------------------------------
function cors(req, res) {
  res.setHeader("Access-Control-Allow-Origin", req.headers.origin ?? "*");
  res.setHeader("Access-Control-Allow-Headers", "X-SpAIder-Token, Content-Type");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS");
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
route("GET", /^\/api\/health$/, (ctx) => json(ctx.res, 200, { status: "ok", version: "0.1.0-mock", app_root: "D:\\SpAIder", data_dir: "D:\\SpAIder\\data" }));
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
    usage: { models_bytes: models, db_bytes: 48 * 1024 * 1024, imports_bytes: 3 * 1024 * 1024, logs_bytes: 512 * 1024, downloads_bytes: 0 },
    runtime: { name: "llama.cpp", build: "b12032", variant: "cpu", path: "D:\\SpAIder\\resources\\runtime\\llama\\llama-server.exe" },
  });
});
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

// settings
route("GET", /^\/api\/settings$/, (ctx) => json(ctx.res, 200, state.settings));
route("PATCH", /^\/api\/settings$/, (ctx) => {
  const patch = ctx.body ?? {};
  if (patch.context_tokens !== undefined && ![4096, 8192, 16384, 32768].includes(patch.context_tokens)) return error(ctx.res, 422, "Ungültige Kontextlänge.", "invalid_setting");
  if (patch.temperature !== undefined && (patch.temperature < 0 || patch.temperature > 2)) return error(ctx.res, 422, "Temperatur muss zwischen 0 und 2 liegen.", "invalid_setting");
  Object.assign(state.settings, patch);
  json(ctx.res, 200, state.settings);
});

// models
route("GET", /^\/api\/models\/catalog$/, (ctx) => json(ctx.res, 200, { entries: catalog }));
route("GET", /^\/api\/models\/installed$/, (ctx) => json(ctx.res, 200, { models: [...state.installed.values()].map(modelView) }));
route("GET", /^\/api\/models\/active$/, (ctx) => json(ctx.res, 200, { chat: modelView(state.installed.get(state.activeChat)), embedding: modelView(state.installed.get(state.activeEmbedding)), runtime_state: state.runtime, error: null }));
route("POST", /^\/api\/models\/download$/, (ctx) => {
  const entry = catalog.find((e) => e.id === ctx.body?.catalog_id);
  if (!entry) return error(ctx.res, 404, "Modell nicht im Katalog.", "not_found");
  if (entry.installed_model_id) return error(ctx.res, 409, "Dieses Modell ist bereits installiert.", "already_installed");
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
route("DELETE", /^\/api\/models\/([^/]+)$/, (ctx) => {
  const model = state.installed.get(ctx.params[0]);
  if (!model) return error(ctx.res, 404, "Modell nicht gefunden.", "not_found");
  if (model.id === state.activeChat || model.id === state.activeEmbedding) return error(ctx.res, 409, "Das aktive Modell kann nicht gelöscht werden.", "model_active");
  state.installed.delete(model.id);
  for (const e of catalog) if (e.installed_model_id === model.id) e.installed_model_id = null;
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
  const entry = catalog.find((e) => e.id === job.catalog_id);
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
const convList = () => [...state.conversations.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
route("GET", /^\/api\/conversations$/, (ctx) => json(ctx.res, 200, { conversations: convList() }));
route("POST", /^\/api\/conversations$/, (ctx) => {
  const conv = { id: randomUUID(), title: ctx.body?.title || "Neues Gespräch", created_at: now(), updated_at: now(), message_count: 0, last_model_id: null };
  state.conversations.set(conv.id, conv);
  state.messages.set(conv.id, []);
  json(ctx.res, 201, conv);
});
route("GET", /^\/api\/conversations\/([^/]+)$/, (ctx) => {
  const conv = state.conversations.get(ctx.params[0]);
  if (!conv) return error(ctx.res, 404, "Gespräch nicht gefunden.", "not_found");
  json(ctx.res, 200, { conversation: conv, messages: state.messages.get(conv.id) });
});
route("PATCH", /^\/api\/conversations\/([^/]+)$/, (ctx) => {
  const conv = state.conversations.get(ctx.params[0]);
  if (!conv) return error(ctx.res, 404, "Gespräch nicht gefunden.", "not_found");
  conv.title = String(ctx.body?.title ?? conv.title);
  conv.updated_at = now();
  json(ctx.res, 200, conv);
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

// documents
route("GET", /^\/api\/documents$/, (ctx) => json(ctx.res, 200, { documents: [...state.documents.values()] }));
route("POST", /^\/api\/documents$/, (ctx) => {
  const parts = parseMultipart(ctx.raw, ctx.req.headers["content-type"]);
  const file = parts.find((p) => p.name === "file");
  if (!file || !file.filename) return error(ctx.res, 422, "Keine Datei übermittelt.", "missing_file");
  const ext = (file.filename.split(".").pop() ?? "").toLowerCase();
  if (!["txt", "md", "markdown", "pdf"].includes(ext)) return error(ctx.res, 415, "Dateityp wird nicht unterstützt (.txt, .md, .markdown, .pdf).", "unsupported_type");
  if (file.body.length > 50 * 1024 * 1024) return error(ctx.res, 413, "Datei ist größer als 50 MB.", "file_too_large");
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

const server = http.createServer(async (req, res) => {
  cors(req, res);
  if (req.method === "OPTIONS") {
    res.writeHead(204);
    return res.end();
  }
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  if (req.headers["x-spaider-token"] !== TOKEN) return error(res, 401, "Zugriff verweigert.", "unauthorized");
  const raw = ["POST", "PATCH"].includes(req.method) ? await readBody(req) : Buffer.alloc(0);
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
  console.error(`[mock] SpAIder mock core listening on http://127.0.0.1:${PORT} (token ${TOKEN}, state ${INITIAL_STATE})`);
});
