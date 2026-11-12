/**
 * Mock implementation of API_CONTRACT.md Addendum v1.2 (live sources and
 * sessions, agent tasks, recipes, global stop). Registered by server.mjs.
 *
 * Synthetic data: 3 fake windows (+ SpAIder itself, which is never offered)
 * and 2 screens; snapshots carry a tiny generated PNG preview; the ask stream
 * reuses the chat generation with the live phases capture / observe /
 * image_encode and an `observation` event; tasks walk through the state
 * machine with one action every ~800 ms.
 *
 * Test hooks:
 *   - live question containing "verloren" -> 409 source_lost (session state lost)
 *   - live question containing "schwarz" -> observation/snapshot quality black
 *   - session on the minimized Explorer window -> quality minimized
 *   - task goal containing "mail", "send", "bestell", "veröffentl", "freigabe",
 *     "frage" or "bestätig" -> approval question at step 3 (waiting_for_user)
 *   - task goal containing "eingabe" -> input question at step 2
 *   - task goal containing "fehler" -> task fails at step 2
 */
import { randomUUID } from "node:crypto";
import zlib from "node:zlib";

const ACTION_INTERVAL_MS = 800;

// ---------------------------------------------------------------------------
// Tiny PNG encoder (RGB, no dependencies) for the preview image
// ---------------------------------------------------------------------------
const CRC_TABLE = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}
/** Renders a small "window" (title bar, text lines) as PNG; black variant for quality=black. */
export function previewPng(width, height, variant = "ok") {
  const raw = Buffer.alloc((width * 3 + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (width * 3 + 1)] = 0; // filter: none
    for (let x = 0; x < width; x++) {
      let r = 24;
      let g = 24;
      let b = 32;
      if (variant !== "black") {
        if (y < Math.round(height * 0.12)) {
          r = 42;
          g = 46;
          b = 64; // title bar
        } else {
          r = 240;
          g = 240;
          b = 244; // document background
          const line = Math.floor((y - height * 0.12) / (height * 0.11));
          const inLine = (y - height * 0.12) % (height * 0.11) < height * 0.04;
          const lineWidth = width * (0.55 + ((line * 7) % 4) * 0.1);
          if (inLine && x > width * 0.08 && x < lineWidth) {
            r = 70;
            g = 70;
            b = 80; // text lines
          }
        }
      }
      const o = y * (width * 3 + 1) + 1 + x * 3;
      raw[o] = r;
      raw[o + 1] = g;
      raw[o + 2] = b;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // colour type RGB
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ---------------------------------------------------------------------------
// Sources
// ---------------------------------------------------------------------------
const WINDOWS = [
  {
    id: "win:132456:4120",
    hwnd: 132456,
    pid: 4120,
    process: "notepad.exe",
    exe: "C:\\Windows\\System32\\notepad.exe",
    title: "Protokoll.txt – Editor",
    class: "Notepad",
    rect: { x: 120, y: 80, w: 1100, h: 700 },
    monitor_id: "screen:1",
    minimized: false,
    is_self: false,
  },
  {
    id: "win:198822:7788",
    hwnd: 198822,
    pid: 7788,
    process: "msedge.exe",
    exe: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    title: "Quartalsbericht Q3 – Microsoft Edge",
    class: "Chrome_WidgetWin_1",
    rect: { x: 300, y: 140, w: 1400, h: 900 },
    monitor_id: "screen:1",
    minimized: false,
    is_self: false,
  },
  {
    id: "win:220011:3344",
    hwnd: 220011,
    pid: 3344,
    process: "explorer.exe",
    exe: "C:\\Windows\\explorer.exe",
    title: "Downloads",
    class: "CabinetWClass",
    rect: { x: 2560, y: 0, w: 900, h: 600 },
    monitor_id: "screen:2",
    minimized: true,
    is_self: false,
  },
  {
    id: "win:900001:5150",
    hwnd: 900001,
    pid: 5150,
    process: "SpAIder.exe",
    exe: "D:\\SpAIder\\SpAIder.exe",
    title: "SpAIder Foldable",
    class: "Chrome_WidgetWin_1",
    rect: { x: 200, y: 200, w: 1280, h: 800 },
    monitor_id: "screen:1",
    minimized: false,
    is_self: true,
  },
];
const SCREENS = [
  { id: "screen:1", name: "Bildschirm 1 (2560×1600)", rect: { x: 0, y: 0, w: 2560, h: 1600 }, scale: 1.5, primary: true },
  { id: "screen:2", name: "Bildschirm 2 (1920×1080)", rect: { x: 2560, y: 0, w: 1920, h: 1080 }, scale: 1, primary: false },
];
const FOREGROUND_ID = WINDOWS[0].id;

const TEXT_EXCERPT = {
  "notepad.exe": "Protokoll Team-Meeting 03.11.2026\n- Portable Build 0.3 bis Freitag\n- Live-Overlay: Hotkey Ctrl+Alt+Space\n- Offene Punkte: Tray-Modus, Agent-Budget",
  "msedge.exe": "Quartalsbericht Q3: Umsatz +12 % gegenüber Vorjahr, Betriebsergebnis 4,2 Mio. EUR, drei neue Standorte.",
  "explorer.exe": "Downloads – 14 Elemente",
};
const ELEMENTS = {
  "notepad.exe": [
    { role: "document", name: "Textbearbeitung", value: null, rect: { x: 128, y: 140, w: 1080, h: 600 }, path: "Window/Document" },
    { role: "menuitem", name: "Datei", value: null, rect: { x: 128, y: 112, w: 48, h: 24 }, path: "Window/MenuBar/Datei" },
    { role: "menuitem", name: "Bearbeiten", value: null, rect: { x: 180, y: 112, w: 80, h: 24 }, path: "Window/MenuBar/Bearbeiten" },
  ],
  "msedge.exe": [
    { role: "document", name: "Quartalsbericht Q3", value: null, rect: { x: 300, y: 220, w: 1400, h: 820 }, path: "Window/Document" },
    { role: "edit", name: "Adresse", value: "https://intranet.example/q3", rect: { x: 400, y: 160, w: 900, h: 32 }, path: "Window/Toolbar/Adresse" },
    { role: "button", name: "Herunterladen", value: null, rect: { x: 1500, y: 300, w: 120, h: 32 }, path: "Window/Document/Herunterladen" },
    { role: "heading", name: "Zusammenfassung", value: null, rect: { x: 340, y: 260, w: 400, h: 32 }, path: "Window/Document/H1" },
  ],
  "explorer.exe": [{ role: "list", name: "Elemente", value: null, rect: { x: 0, y: 60, w: 900, h: 540 }, path: "Window/List" }],
};

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------
export function registerLiveAgent({ route, json, error, sseStart, sseSend, state, broadcast, now, startGeneration, createConversation, createJob, updateJob, timers, args }) {
  const sessions = new Map();

  // ----- on-demand browser package (--browser-installed starts installed) ----------
  const BROWSER_PACKAGE = {
    name: "chromium-headless-shell",
    revision: "1194",
    file: "chromium-headless-shell-win64-1194.zip",
    size: 217_055_232,
    sha256: "9f".repeat(32),
    license: "BSD-3-Clause",
    playwright: true,
  };
  const browser = { installed: args.includes("--browser-installed"), version: null, path: null };
  const browserStatus = () => ({
    installed: browser.installed,
    version: browser.installed ? "131.0.6778.33" : null,
    path: browser.installed ? "D:\\SpAIder\\data\\browser\\chromium-headless-shell-win64-1194\\headless_shell.exe" : null,
    playwright: browser.installed,
    reason_de: browser.installed ? null : "Browser-Paket noch nicht geladen (einmaliger Download, ≈ 207 MB).",
    package: BROWSER_PACKAGE,
  });
  route("GET", /^\/api\/agent\/browser$/, (ctx) => json(ctx.res, 200, browserStatus()));
  route("POST", /^\/api\/agent\/browser\/install$/, (ctx) => {
    if (browser.installed) return json(ctx.res, 200, { job: null, installed: true });
    const running = [...state.jobs.values()].find((j) => j.kind === "browser" && ["queued", "running"].includes(j.state));
    if (running) return json(ctx.res, 202, { job: running });
    const job = createJob("browser", { progress: { done_bytes: 0, total_bytes: BROWSER_PACKAGE.size, speed_bps: 0, eta_seconds: null } });
    const total = BROWSER_PACKAGE.size;
    const step = Math.ceil(total / 24);
    updateJob(job, { state: "running", phase: "check_space", message_de: "Speicherplatz wird geprüft" });
    const tick = () => {
      if (job.state !== "running") return;
      if (job.phase === "check_space") {
        updateJob(job, { phase: "download", message_de: `Lädt ${BROWSER_PACKAGE.file}` });
      } else if (job.phase === "download") {
        const done = Math.min(total, job.progress.done_bytes + step);
        updateJob(job, { progress: { done_bytes: done, total_bytes: total, speed_bps: step * 4, eta_seconds: Math.round((total - done) / (step * 4)) } });
        if (done >= total) updateJob(job, { phase: "verify", message_de: "Prüfsumme wird berechnet", progress: { ...job.progress, speed_bps: 0, eta_seconds: null } });
      } else if (job.phase === "verify") {
        updateJob(job, { phase: "copy", message_de: "Paket wird entpackt" });
      } else if (job.phase === "copy") {
        browser.installed = true;
        updateJob(job, { state: "done", phase: null, message_de: "Browser installiert" });
        return;
      }
      timers.set(job.id, setTimeout(tick, 250));
    };
    timers.set(job.id, setTimeout(tick, 250));
    json(ctx.res, 202, { job });
  });
  const snapshots = new Map();
  const tasks = new Map();
  const taskActions = new Map(); // taskId -> Action[]
  const taskSteps = new Map(); // taskId -> StepEvent[] (v1.5, replayed on reconnect)
  const taskTimers = new Map();
  const taskListeners = new Map(); // taskId -> Set<res>
  const recipes = new Map();
  const workflows = new Map(); // v1.5
  const workflowRuns = new Map(); // workflowId -> WorkflowRun[]
  const hostBindings = new Map(); // role -> binding
  const teachSessions = new Map(); // v1.5

  const findSource = (id) => WINDOWS.find((w) => w.id === id) ?? SCREENS.find((s) => s.id === id) ?? null;
  const isWindow = (s) => typeof s?.hwnd === "number";

  // ----- sources ---------------------------------------------------------------
  route("GET", /^\/api\/live\/sources$/, (ctx) => json(ctx.res, 200, { windows: WINDOWS, screens: SCREENS, foreground_id: FOREGROUND_ID }));
  route("POST", /^\/api\/live\/foreground$/, (ctx) => json(ctx.res, 200, { source: WINDOWS[0], captured_at: now() }));

  // ----- sessions ---------------------------------------------------------------
  function sessionView(s) {
    return { ...s };
  }
  function qualityFor(session, content = "") {
    if (session.state === "lost") return "lost";
    if (/schwarz/i.test(content)) return "black";
    if (isWindow(session.source) && session.source.minimized) return "minimized";
    return "ok";
  }
  function makeSnapshot(session, { includeImage, preview, maxElements, quality }) {
    const src = session.source;
    const proc = isWindow(src) ? src.process : null;
    const elements = (proc ? ELEMENTS[proc] : []) ?? [];
    const text = proc ? (TEXT_EXCERPT[proc] ?? "") : "Bildschirm";
    const shown = elements.slice(0, maxElements ?? 200).map((e, i) => ({ id: `el_${i + 1}`, enabled: true, focused: i === 0, ...e }));
    const snap = {
      snapshot_id: `snap_${randomUUID().slice(0, 8)}`,
      session_id: session.id,
      captured_at: now(),
      age_ms: 12,
      source_kind: session.capture_mode,
      target: isWindow(src)
        ? { hwnd: src.hwnd, pid: src.pid, process: src.process, title: src.title, rect: src.rect, monitor_id: src.monitor_id, scale: 1.5 }
        : { hwnd: null, pid: null, process: null, title: src.name, rect: src.rect, monitor_id: src.id, scale: src.scale },
      quality,
      error: quality === "error" ? "Aufnahme fehlgeschlagen (Mock)." : null,
      text_excerpt: quality === "ok" || quality === "occluded" ? text : "",
      text_chars: quality === "ok" || quality === "occluded" ? text.length : 0,
      elements: quality === "lost" ? [] : shown,
      elements_total: quality === "lost" ? 0 : elements.length,
      truncated: shown.length < elements.length,
      image: null,
      transform: { origin_x: src.rect.x, origin_y: src.rect.y, scale: 1 },
    };
    if (quality !== "lost" && (includeImage || preview)) {
      const w = preview ? 240 : src.rect.w;
      const h = preview ? Math.round((240 * src.rect.h) / src.rect.w) : src.rect.h;
      const png = previewPng(preview ? 96 : 64, preview ? Math.round((96 * src.rect.h) / src.rect.w) : 40, quality === "black" ? "black" : "ok").toString("base64");
      snap.image = { width: w, height: h, bytes: Math.round(w * h * 0.2), png_base64: includeImage ? png : null, preview_png_base64: preview ? png : null };
    }
    snapshots.set(snap.snapshot_id, snap);
    session.last_snapshot = { snapshot_id: snap.snapshot_id, captured_at: snap.captured_at, age_ms: snap.age_ms, quality };
    return snap;
  }

  route("GET", /^\/api\/live\/sessions$/, (ctx) => json(ctx.res, 200, { sessions: [...sessions.values()].map(sessionView) }));
  route("POST", /^\/api\/live\/sessions$/, (ctx) => {
    const source = findSource(ctx.body?.source_id);
    if (!source) return error(ctx.res, 404, "Quelle nicht gefunden.", "source_not_found");
    if (source.is_self) return error(ctx.res, 409, "SpAIder selbst kann nicht beobachtet werden.", "source_is_self");
    let conversationId = ctx.body?.conversation_id ?? null;
    if (!conversationId || !state.conversations.has(conversationId)) {
      conversationId = createConversation(`Live: ${isWindow(source) ? source.title : source.name}`).id;
    }
    const session = {
      id: `live_${randomUUID().slice(0, 8)}`,
      source,
      capture_mode: isWindow(source) ? "window" : "screen",
      state: "active",
      conversation_id: conversationId,
      created_at: now(),
      last_snapshot: null,
      vision_available: false,
      notes_de: ["Mock-Sitzung: Struktur und Text sind synthetisch.", "Kein Bildmodell aktiv – Antworten nutzen nur Text und Struktur."],
    };
    sessions.set(session.id, session);
    broadcast("live", sessionView(session));
    json(ctx.res, 201, { session: sessionView(session) });
  });
  route("GET", /^\/api\/live\/sessions\/([^/]+)$/, (ctx) => {
    const s = sessions.get(ctx.params[0]);
    return s ? json(ctx.res, 200, { session: sessionView(s) }) : error(ctx.res, 404, "Sitzung nicht gefunden.", "not_found");
  });
  route("PATCH", /^\/api\/live\/sessions\/([^/]+)$/, (ctx) => {
    const s = sessions.get(ctx.params[0]);
    if (!s) return error(ctx.res, 404, "Sitzung nicht gefunden.", "not_found");
    const source = findSource(ctx.body?.source_id);
    if (!source) return error(ctx.res, 404, "Quelle nicht gefunden.", "source_not_found");
    if (source.is_self) return error(ctx.res, 409, "SpAIder selbst kann nicht beobachtet werden.", "source_is_self");
    s.source = source;
    s.capture_mode = isWindow(source) ? "window" : "screen";
    s.state = "active";
    s.last_snapshot = null;
    broadcast("live", sessionView(s));
    json(ctx.res, 200, { session: sessionView(s) });
  });
  route("DELETE", /^\/api\/live\/sessions\/([^/]+)$/, (ctx) => {
    const s = sessions.get(ctx.params[0]);
    if (!s) return error(ctx.res, 404, "Sitzung nicht gefunden.", "not_found");
    s.state = "ended";
    sessions.delete(s.id);
    broadcast("live", sessionView(s));
    json(ctx.res, 200, { ok: true });
  });
  route("POST", /^\/api\/live\/sessions\/([^/]+)\/snapshot$/, (ctx) => {
    const s = sessions.get(ctx.params[0]);
    if (!s) return error(ctx.res, 404, "Sitzung nicht gefunden.", "not_found");
    const snap = makeSnapshot(s, {
      includeImage: Boolean(ctx.body?.include_image),
      preview: Boolean(ctx.body?.preview),
      maxElements: Number(ctx.body?.max_elements ?? 200),
      quality: qualityFor(s),
    });
    json(ctx.res, 200, { snapshot: snap });
  });
  route("POST", /^\/api\/live\/sessions\/([^/]+)\/ask$/, (ctx) => {
    const s = sessions.get(ctx.params[0]);
    if (!s) return error(ctx.res, 404, "Sitzung nicht gefunden.", "not_found");
    if (!state.activeChat) return error(ctx.res, 409, "Kein Chat-Modell aktiv.", "no_model");
    if (state.runtime !== "ready") return error(ctx.res, 409, "Die Laufzeit ist noch nicht bereit.", "runtime_not_ready");
    if (state.generation) return error(ctx.res, 409, "Es läuft bereits eine Antwort.", "generation_running");
    const content = String(ctx.body?.content ?? "").trim();
    if (!content) return error(ctx.res, 422, "Leere Frage.", "empty_message");
    if (/verloren/i.test(content) || s.state === "lost") {
      s.state = "lost";
      broadcast("live", sessionView(s));
      return error(ctx.res, 409, "Quelle verloren – das Fenster ist nicht mehr vorhanden.", "source_lost");
    }
    if (ctx.body?.use_image === "always" && !s.vision_available) {
      return error(ctx.res, 409, "Kein Bildmodell aktiv.", "vision_unavailable");
    }
    if (!state.conversations.has(s.conversation_id)) s.conversation_id = createConversation(`Live: ${isWindow(s.source) ? s.source.title : s.source.name}`).id;
    const quality = qualityFor(s, content);
    const snap = makeSnapshot(s, { includeImage: false, preview: false, maxElements: 200, quality });
    startGeneration(s.conversation_id, content, ctx.res, {
      session: s,
      snapshot: snap,
      useImage: ctx.body?.use_image !== "never" && s.vision_available && quality === "ok",
    });
  });
  route("POST", /^\/api\/live\/sessions\/([^/]+)\/stop$/, (ctx) => {
    const s = sessions.get(ctx.params[0]);
    if (!s) return error(ctx.res, 404, "Sitzung nicht gefunden.", "not_found");
    const gen = state.generation;
    if (gen && gen.conversationId === s.conversation_id) {
      gen.stopped = true;
      return json(ctx.res, 200, { ok: true, stopped: true });
    }
    json(ctx.res, 200, { ok: true, stopped: false });
  });

  // ----- agent: capabilities ------------------------------------------------------
  const TOOLS = [
    { name: "files.read", description_de: "Dateien in den freigegebenen Ordnern lesen", scope: "files", side_effect: "none", available: true, reason_de: null },
    { name: "files.write", description_de: "Dateien im Schreibordner anlegen oder ändern", scope: "files", side_effect: "local", available: true, reason_de: null },
    { name: "browser.navigate", description_de: "Seite im kontrollierten Browser öffnen und lesen", scope: "browser", side_effect: "none", available: true, reason_de: null },
    { name: "browser.click", description_de: "Element im Browser anklicken", scope: "browser", side_effect: "external", available: true, reason_de: null },
    { name: "windows.read", description_de: "Struktur und Text eines Fensters lesen (UIA)", scope: "windows", side_effect: "none", available: true, reason_de: null },
    { name: "windows.click", description_de: "Element in einem erlaubten Fenster anklicken", scope: "windows", side_effect: "local", available: true, reason_de: null },
    { name: "windows.type", description_de: "Text in ein erlaubtes Fenster eingeben", scope: "windows", side_effect: "local", available: true, reason_de: null },
    { name: "vision.locate", description_de: "Element per Bild finden", scope: "vision", side_effect: "none", available: false, reason_de: "Kein Bildmodell aktiv." },
    { name: "memory.search", description_de: "Gedächtnis durchsuchen", scope: "memory", side_effect: "none", available: true, reason_de: null },
  ];
  const HOST_ID = "host_mock_7f3a9c21d4e5";
  route("GET", /^\/api\/agent\/capabilities$/, (ctx) =>
    json(ctx.res, 200, {
      tools: TOOLS,
      model: { id: state.activeChat ?? "none", vision_input: false, image_grounding: "none", tool_call_format: "json_schema", structured_output: true, native_tool_calls: "untested" },
      browser: { installed: browser.installed, version: browserStatus().version, path: browserStatus().path },
      windows_automation: { available: true, reason_de: "UI Automation verfügbar." },
      desktop_interactive: true,
      execution_core: {
        version: "1.5",
        workflows: workflows.size,
        stale_workflows: [...workflows.values()].filter((w) => w.stats.dependencies.invalid > 0).length,
        input_guard: { enabled: true, paused_by_user: 0, user_idle_ms: 4200 },
        host_id: HOST_ID,
        teach: { active: [...teachSessions.values()].filter((s) => s.state === "recording").length, sessions: [...teachSessions.values()].filter((s) => s.state === "recording") },
      },
    }),
  );

  // ----- agent: tasks ----------------------------------------------------------------
  function taskView(t) {
    return { ...t, scopes: t.scopes, pending_question: t.pending_question ? { ...t.pending_question } : null };
  }
  function emitTask(t, event = "task") {
    t.updated_at = now();
    const view = taskView(t);
    const listeners = taskListeners.get(t.id);
    if (listeners) for (const res of listeners) sseSend(res, event, view);
    broadcast("task", view);
    if (["completed", "failed", "cancelled"].includes(t.state)) {
      if (listeners) {
        for (const res of listeners) {
          sseSend(res, "done", view);
          res.end();
        }
        taskListeners.delete(t.id);
      }
    }
  }
  function emitAction(t, a) {
    const listeners = taskListeners.get(t.id);
    if (listeners) for (const res of listeners) sseSend(res, "action", { ...a });
  }
  function emitToken(t, text) {
    const listeners = taskListeners.get(t.id);
    if (listeners) for (const res of listeners) sseSend(res, "token", { text });
  }
  function emitQuestion(t) {
    const listeners = taskListeners.get(t.id);
    if (listeners) for (const res of listeners) sseSend(res, "question", { ...t.pending_question });
  }
  function emitActivity(t, phase, st, message_de, counters = null) {
    const ev = { request_id: t.id, seq: t._seq++, phase, state: st, t_ms: Date.now() - t._startedMs, at: now(), message_de, counters, source_ids: [] };
    const listeners = taskListeners.get(t.id);
    if (listeners) for (const res of listeners) sseSend(res, "activity", ev);
  }
  /** v1.5: a step that really happened (never a thought); kept for replay. */
  function emitStep(t, kind, message_de, extra = {}) {
    const ev = { kind, message_de, at: now(), ...extra };
    if (!taskSteps.has(t.id)) taskSteps.set(t.id, []);
    taskSteps.get(t.id).push(ev);
    const listeners = taskListeners.get(t.id);
    if (listeners) for (const res of listeners) sseSend(res, "step", ev);
  }
  function setState(t, st, summary) {
    t.state = st;
    if (summary !== undefined) t.summary_de = summary;
    if (st === "observing" && !t.started_at) t.started_at = now();
    if (["completed", "failed", "cancelled"].includes(st)) t.finished_at = now();
    emitTask(t);
  }
  function planFor(t) {
    const goal = t.goal.toLowerCase();
    const external = /mail|send|versend|bestell|veröffentl|freigabe|frage|bestätig/.test(goal);
    const input = /eingabe/.test(goal);
    const failing = /fehler/.test(goal);
    const win = t.scopes.windows?.source_ids?.[0] ?? null;
    const proc = t.scopes.windows?.allowed_processes?.[0] ?? "notepad.exe";
    const root = t.scopes.files?.roots?.[0] ?? "C:\\Users\\mock\\Dokumente";
    const writeDir = t.scopes.files?.write_dir ?? root;
    const steps = [
      { tool: "memory.search", arguments: { query: t.goal.slice(0, 40) }, expected_effect: "Passende Fakten und Dokumente finden", summary: "2 passende Quellen gefunden", evidence: null },
      { tool: "windows.read", arguments: { source_id: win ?? "win:132456:4120", max_elements: 50 }, expected_effect: `Struktur von ${proc} lesen`, summary: "3 Elemente, 142 Zeichen gelesen", evidence: { snapshot: "snap_mock_1" } },
      external
        ? { tool: "browser.click", arguments: { selector: "button#send", origin: "https://intranet.example" }, expected_effect: "Formular absenden (externe Wirkung)", summary: "Formular abgesendet", evidence: { url: "https://intranet.example/sent/42" }, question: "approval" }
        : input
          ? { tool: "windows.type", arguments: { source_id: win ?? "win:132456:4120", text: "{antwort}" }, expected_effect: "Text in das Zielfenster eingeben", summary: "Text eingegeben", evidence: null, question: "input" }
          : { tool: "files.write", arguments: { path: `${writeDir}\\zusammenfassung.md`, mode: "create" }, expected_effect: "Zusammenfassung als Datei ablegen", summary: "Datei geschrieben (1,2 KB)", evidence: { path: `${writeDir}\\zusammenfassung.md`, bytes: 1228 } },
      { tool: "windows.read", arguments: { source_id: win ?? "win:132456:4120", max_elements: 20 }, expected_effect: "Ergebnis im Zielfenster prüfen", summary: "Erwarteter Zustand bestätigt", evidence: { snapshot: "snap_mock_2" } },
    ];
    return { steps, failing, external, input, writeDir };
  }
  function schedule(t, fn, ms = ACTION_INTERVAL_MS) {
    clearTimeout(taskTimers.get(t.id));
    taskTimers.set(t.id, setTimeout(fn, ms));
  }
  function runTask(t) {
    const plan = t._plan;
    const actions = taskActions.get(t.id);
    const next = () => {
      if (["paused", "waiting_for_user", "cancelled", "failed", "completed", "interrupted"].includes(t.state)) return;
      const idx = actions.length;
      if (t.state === "created") {
        setState(t, "observing", "Zielfenster und Gedächtnis werden gelesen");
        emitActivity(t, "capture", "started", "Quelle wird aufgenommen");
        if (t.reuse) {
          emitStep(t, "gate", `Wiederverwendung geprüft: ${t.reuse.mode === "reuse" ? "Ablauf wird wiederverwendet" : t.reuse.mode === "repair" ? "Ablauf wird repariert" : "Ablauf wird neu geplant"}`, {
            mode: t.reuse.mode,
            coverage: t.reuse.coverage,
            repair_steps: t.reuse.repair_steps,
          });
        }
        return schedule(t, next);
      }
      if (t.state === "observing") {
        emitActivity(t, "capture", "done", "Quelle aufgenommen", { elements: 3 });
        emitStep(t, "read", "Fenster gelesen: Protokoll.txt – Editor (3 Elemente, 142 Zeichen)");
        setState(t, "planning", "Plan wird erstellt");
        emitActivity(t, "generating", "started", "Plan wird geschrieben");
        emitToken(t, `Ich lese zuerst das Zielfenster und das Gedächtnis, dann ${plan.external ? "frage ich vor der externen Aktion nach" : "lege ich das Ergebnis ab"}. `);
        return schedule(t, next);
      }
      if (t.state === "planning") {
        emitActivity(t, "generating", "done", "Plan fertig", { tokens: 84, tokens_per_second: 21.5 });
        setState(t, "validating", `Plan mit ${plan.steps.length} Schritten wird gegen die Freigaben geprüft`);
        return schedule(t, next, 500);
      }
      if (t.state === "validating" || (t.state === "executing" && idx < plan.steps.length)) {
        if (t.state === "validating") setState(t, "executing", "Aktionen werden ausgeführt");
        if (idx >= plan.steps.length) return schedule(t, next, 10);
        if (t.step >= t.max_steps) {
          setState(t, "failed", "Schrittbudget aufgebraucht");
          t.last_error = `Maximal ${t.max_steps} Schritte erlaubt.`;
          emitTask(t);
          return;
        }
        const def = plan.steps[idx];
        const action = {
          action_id: `act_${randomUUID().slice(0, 8)}`,
          task_id: t.id,
          seq: idx + 1,
          tool: def.tool,
          arguments: def.arguments,
          state: "proposed",
          reason_de: null,
          expected_effect: def.expected_effect,
          observation_ref: null,
          started_at: null,
          finished_at: null,
          duration_ms: null,
          result: null,
          call_id: null,
          origin: t.reuse ? (t.reuse.repair_steps.includes(idx) ? "repair" : t.reuse.mode === "reuse" || t.reuse.mode === "repair" ? "workflow" : "planner") : "planner",
        };
        actions.push(action);
        emitAction(t, action);
        if (def.question && !t._answered) {
          t.pending_question = {
            id: `q_${randomUUID().slice(0, 6)}`,
            text_de:
              def.question === "approval"
                ? `Die nächste Aktion hat Wirkung nach außen (${def.expected_effect}). Soll sie ausgeführt werden?`
                : "Welcher Text soll in das Zielfenster eingegeben werden?",
            kind: def.question,
            action: { ...action },
          };
          setState(t, "waiting_for_user", "Wartet auf eine Antwort");
          emitQuestion(t);
          return;
        }
        return schedule(t, () => executeAction(t, action, def, idx), 300);
      }
      if (t.state === "executing" && idx >= plan.steps.length) {
        setState(t, "verifying", "Ergebnis wird geprüft");
        return schedule(t, () => {
          if (t.state !== "verifying") return;
          const file = `${plan.writeDir}\\zusammenfassung.md`;
          const unclear = /ungeklärt|unklar/i.test(t.goal);
          const outcome = unclear ? "unclear" : "fulfilled";
          const criteria = t.success_criteria.length
            ? t.success_criteria
            : plan.external
              ? [{ kind: "page_text", target: "https://intranet.example/sent/42", expect: ["Gesendet"], origin: "planner", description_de: "Bestätigungsseite zeigt „Gesendet“" }]
              : [
                  { kind: "file_exists", target: file, expect: [], origin: "planner", description_de: "zusammenfassung.md ist vorhanden" },
                  { kind: "file_contains", target: file, expect: ["Zusammenfassung"], origin: "planner", description_de: "Datei enthält eine Überschrift „Zusammenfassung“" },
                ];
          t.outcome = outcome;
          t.outcome_report = {
            status: outcome,
            checks: criteria.map((c, i) => ({
              criterion: c,
              ok: unclear && i === criteria.length - 1 ? null : true,
              detail_de: unclear && i === criteria.length - 1 ? "Konnte nicht geprüft werden (Mock)." : c.kind === "file_exists" ? "Datei vorhanden (1,2 KB)" : "Text gefunden",
              evidence: c.kind.startsWith("file") ? { path: c.target } : { url: c.target },
            })),
            causes_de: unclear ? ["Die letzte Prüfung lieferte kein eindeutiges Ergebnis."] : [],
            evidence_paths: plan.external ? [] : [file],
            verified_at: now(),
            verified_by: "verifier",
          };
          emitStep(t, "verify", `Ergebnis geprüft: ${outcome === "fulfilled" ? "erfüllt" : "ungeklärt"}`, { ok: outcome === "fulfilled", status: outcome });
          t.result = {
            ok: true,
            summary_de: plan.external ? "Formular gesendet und Bestätigung im Zielfenster gesehen." : `Zusammenfassung unter ${file} abgelegt und geprüft.`,
            artifacts: plan.external
              ? [{ kind: "url", path: null, url: "https://intranet.example/sent/42", label: "Bestätigungsseite" }]
              : [{ kind: "file", path: file, url: null, label: "zusammenfassung.md" }],
            verified: true,
            outcome,
            evidence_paths: plan.external ? [] : [file],
            verified_by: "verifier",
          };
          if (t.workflow_id) recordRun(t);
          setState(t, "completed", "Aufgabe abgeschlossen");
        });
      }
    };
    const executeAction = (task, action, def, idx) => {
      if (task.state !== "executing") return;
      action.state = "validated";
      emitAction(task, action);
      action.state = "executing";
      action.started_at = now();
      emitAction(task, action);
      emitActivity(task, "observe", "started", `${def.tool} läuft`);
      schedule(task, () => {
        if (task.state !== "executing") {
          action.state = "cancelled";
          action.finished_at = now();
          emitAction(task, action);
          return;
        }
        action.finished_at = now();
        action.duration_ms = 420 + idx * 60;
        if (plan.failing && idx === 1) {
          action.state = "failed";
          action.result = { ok: false, summary_de: "Das Zielfenster hat nicht reagiert (Testfehler).", evidence: null, snapshot_id: null };
          emitAction(task, action);
          task.last_error = "Das Zielfenster hat nicht reagiert (Testfehler).";
          setState(task, "failed", "Aktion fehlgeschlagen");
          return;
        }
        action.state = "done";
        action.result = { ok: true, summary_de: def.summary, evidence: def.evidence, snapshot_id: def.evidence?.snapshot ?? null };
        action.observation_ref = def.evidence?.snapshot ?? null;
        task.step += 1;
        if (action.origin === "workflow") emitStep(task, "skip", `Schritt ${idx + 1} aus Ablauf übernommen (kein Modellaufruf): ${def.tool}`, { action_id: action.action_id, step: idx + 1 });
        else task.llm_calls += 1;
        if (action.origin === "repair") emitStep(task, "repaired", `Schritt ${idx + 1} repariert: ${def.tool}`, { action_id: action.action_id, step: idx + 1, ok: true });
        if (def.tool === "files.write") emitStep(task, "write", `Datei geschrieben: ${def.arguments.path}`, { action_id: action.action_id, step: idx + 1, ok: true });
        else if (def.tool === "windows.read") emitStep(task, "read", `Fenster gelesen: ${def.expected_effect}`, { action_id: action.action_id, step: idx + 1, ok: true });
        else if (def.tool === "memory.search") emitStep(task, "read", "Gedächtnis durchsucht: 2 Quellen", { action_id: action.action_id, step: idx + 1, ok: true });
        else emitStep(task, "check", `${def.tool}: ${def.summary}`, { action_id: action.action_id, step: idx + 1, ok: true });
        emitAction(task, action);
        emitActivity(task, "observe", "done", `${def.tool} fertig`);
        emitTask(task);
        schedule(task, next, 400);
      }, 500);
    };
    t._next = next;
    schedule(t, next, 300);
  }

  route("GET", /^\/api\/agent\/tasks$/, (ctx) => json(ctx.res, 200, { tasks: [...tasks.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)).map(taskView) }));
  /** Creates a task record (POST /tasks and POST /workflows/{id}/run) and starts it. */
  function makeTask({ goal, scopes, budget, body = {}, workflow = null, reuse = null, parameters = null }) {
    const t = {
      id: `task_${randomUUID().slice(0, 8)}`,
      goal,
      state: "created",
      step: 0,
      max_steps: budget?.max_steps ?? state.settings.agent_max_steps ?? 20,
      created_at: now(),
      updated_at: now(),
      started_at: null,
      finished_at: null,
      summary_de: "Aufgabe angelegt",
      result: null,
      scopes,
      pending_question: null,
      conversation_id: body.conversation_id ?? null,
      live_session_id: body.live_session_id ?? null,
      recipe_id: body.recipe_id ?? null,
      llm_calls: 0,
      host_bound: Boolean(scopes.windows),
      last_error: null,
      // v1.5
      workflow_id: workflow?.id ?? body.workflow_id ?? null,
      project_id: body.project_id ?? null,
      connection_id: body.connection_id ?? null,
      success_criteria: Array.isArray(body.success_criteria) ? body.success_criteria : (workflow?.checks ?? []),
      outcome: null,
      outcome_report: null,
      reuse,
      _parameters: parameters,
      _seq: 0,
      _startedMs: Date.now(),
      _answered: false,
    };
    t._plan = planFor(t);
    tasks.set(t.id, t);
    taskActions.set(t.id, []);
    taskSteps.set(t.id, []);
    broadcast("task", taskView(t));
    runTask(t);
    return t;
  }
  route("POST", /^\/api\/agent\/tasks$/, (ctx) => {
    const goal = String(ctx.body?.goal ?? "").trim();
    if (!goal) return error(ctx.res, 422, "Ziel fehlt.", "invalid_goal");
    const scopes = ctx.body?.scopes ?? { files: null, browser: null, windows: null, memory: true, external_effects: false };
    const budget = ctx.body?.budget ?? null;
    const t = makeTask({ goal, scopes, budget, body: ctx.body ?? {} });
    json(ctx.res, 201, { task: privateView(t) });
  });
  const privateView = (t) => {
    const v = taskView(t);
    for (const k of Object.keys(v)) if (k.startsWith("_")) delete v[k];
    return v;
  };
  route("GET", /^\/api\/agent\/tasks\/([^/]+)$/, (ctx) => {
    const t = tasks.get(ctx.params[0]);
    return t ? json(ctx.res, 200, { task: privateView(t), actions: taskActions.get(t.id) ?? [] }) : error(ctx.res, 404, "Aufgabe nicht gefunden.", "not_found");
  });
  route("GET", /^\/api\/agent\/tasks\/([^/]+)\/events$/, (ctx) => {
    const t = tasks.get(ctx.params[0]);
    if (!t) return error(ctx.res, 404, "Aufgabe nicht gefunden.", "not_found");
    sseStart(ctx.res);
    sseSend(ctx.res, "task", privateView(t));
    for (const a of taskActions.get(t.id) ?? []) sseSend(ctx.res, "action", a);
    for (const s of taskSteps.get(t.id) ?? []) sseSend(ctx.res, "step", s);
    if (t.pending_question) sseSend(ctx.res, "question", t.pending_question);
    if (["completed", "failed", "cancelled"].includes(t.state)) {
      sseSend(ctx.res, "done", privateView(t));
      return ctx.res.end();
    }
    if (!taskListeners.has(t.id)) taskListeners.set(t.id, new Set());
    taskListeners.get(t.id).add(ctx.res);
    const keepAlive = setInterval(() => {
      if (ctx.res.writableEnded || ctx.res.destroyed) return clearInterval(keepAlive);
      ctx.res.write(": keep-alive\n\n");
    }, 15000);
    ctx.req.on("close", () => {
      clearInterval(keepAlive);
      taskListeners.get(t.id)?.delete(ctx.res);
    });
  });
  route("POST", /^\/api\/agent\/tasks\/([^/]+)\/cancel$/, (ctx) => {
    const t = tasks.get(ctx.params[0]);
    if (!t) return error(ctx.res, 404, "Aufgabe nicht gefunden.", "not_found");
    if (!["completed", "failed", "cancelled"].includes(t.state)) {
      clearTimeout(taskTimers.get(t.id));
      t.pending_question = null;
      for (const a of taskActions.get(t.id) ?? []) {
        if (["proposed", "validated", "executing"].includes(a.state)) {
          a.state = "cancelled";
          a.finished_at = now();
          emitAction(t, a);
        }
      }
      setState(t, "cancelled", "Vom Nutzer gestoppt");
    }
    json(ctx.res, 200, { task: privateView(t) });
  });
  route("POST", /^\/api\/agent\/tasks\/([^/]+)\/pause$/, (ctx) => {
    const t = tasks.get(ctx.params[0]);
    if (!t) return error(ctx.res, 404, "Aufgabe nicht gefunden.", "not_found");
    if (["created", "observing", "planning", "validating", "executing", "verifying"].includes(t.state)) {
      clearTimeout(taskTimers.get(t.id));
      t._resumeState = t.state;
      setState(t, "paused", "Pausiert");
    }
    json(ctx.res, 200, { task: privateView(t) });
  });
  route("POST", /^\/api\/agent\/tasks\/([^/]+)\/resume$/, (ctx) => {
    const t = tasks.get(ctx.params[0]);
    if (!t) return error(ctx.res, 404, "Aufgabe nicht gefunden.", "not_found");
    if (t.state === "paused" || t.state === "interrupted") {
      setState(t, t._resumeState && t._resumeState !== "paused" ? t._resumeState : "executing", "Fortgesetzt");
      schedule(t, t._next, 300);
    }
    json(ctx.res, 200, { task: privateView(t) });
  });
  route("POST", /^\/api\/agent\/tasks\/([^/]+)\/answer$/, (ctx) => {
    const t = tasks.get(ctx.params[0]);
    if (!t) return error(ctx.res, 404, "Aufgabe nicht gefunden.", "not_found");
    const q = t.pending_question;
    if (!q || q.id !== ctx.body?.question_id) return error(ctx.res, 409, "Keine offene Rückfrage mit dieser Id.", "no_pending_question");
    const actions = taskActions.get(t.id);
    const action = actions.find((a) => a.action_id === q.action?.action_id) ?? actions[actions.length - 1];
    t.pending_question = null;
    t._answered = true;
    const approved = q.kind === "approval" ? ctx.body?.approve === true : typeof ctx.body?.text === "string" && ctx.body.text.trim().length > 0;
    if (!approved) {
      if (action) {
        action.state = "rejected";
        action.reason_de = "Vom Nutzer abgelehnt";
        action.finished_at = now();
        emitAction(t, action);
      }
      setState(t, "cancelled", "Vom Nutzer abgelehnt");
      return json(ctx.res, 200, { task: privateView(t) });
    }
    if (q.kind === "input" && action) action.arguments = { ...action.arguments, text: ctx.body.text };
    setState(t, "executing", "Aktionen werden ausgeführt");
    json(ctx.res, 200, { task: privateView(t) });
    const idx = actions.indexOf(action);
    const def = t._plan.steps[idx];
    schedule(t, () => {
      if (t.state !== "executing") return;
      action.state = "validated";
      emitAction(t, action);
      action.state = "executing";
      action.started_at = now();
      emitAction(t, action);
      schedule(t, () => {
        if (t.state !== "executing") return;
        action.state = "done";
        action.finished_at = now();
        action.duration_ms = 610;
        action.result = { ok: true, summary_de: def.summary, evidence: def.evidence, snapshot_id: null };
        t.step += 1;
        t.llm_calls += 1;
        emitAction(t, action);
        emitTask(t);
        schedule(t, t._next, 400);
      }, 500);
    }, 200);
  });
  route("POST", /^\/api\/agent\/tasks\/([^/]+)\/save-recipe$/, (ctx) => {
    const t = tasks.get(ctx.params[0]);
    if (!t) return error(ctx.res, 404, "Aufgabe nicht gefunden.", "not_found");
    if (t.state !== "completed") return error(ctx.res, 409, "Nur abgeschlossene Aufgaben können als Ablauf gespeichert werden.", "task_not_completed");
    const name = String(ctx.body?.name ?? "").trim();
    if (!name) return error(ctx.res, 422, "Name fehlt.", "invalid_name");
    const recipe = {
      id: `rcp_${randomUUID().slice(0, 8)}`,
      version: 1,
      name,
      purpose_de: t.goal,
      parameters: Array.isArray(ctx.body?.parameters) ? ctx.body.parameters : [],
      origin: { task_id: t.id, created_at: now() },
      requires: { tools: [...new Set((taskActions.get(t.id) ?? []).map((a) => a.tool))], apps: t.scopes.windows?.allowed_processes ?? [], vision: false },
      preconditions: ["Zielfenster ist geöffnet und nicht minimiert."],
      scope: t.scopes,
      steps: (taskActions.get(t.id) ?? []).map((a) => ({ tool: a.tool, arguments_template: a.arguments, check: a.result?.summary_de ?? "" })),
      final_checks: ["Ergebnis im Zielfenster sichtbar"],
      last_verified_at: now(),
      known_limits_de: ["Mock-Ablauf: Schritte sind synthetisch."],
    };
    recipes.set(recipe.id, recipe);
    const workflow = compileTask(t, name, recipe.parameters);
    json(ctx.res, 201, { recipe, workflow });
  });

  // ----- recipes ---------------------------------------------------------------------
  const seeded = {
    id: "rcp_seed0001",
    version: 2,
    name: "Protokoll aus Editor zusammenfassen",
    purpose_de: "Das offene Protokoll im Editor lesen und eine Zusammenfassung als Datei ablegen.",
    parameters: [{ name: "zielordner", description_de: "Ordner für die Zusammenfassung", example: "C:\\Users\\mock\\Dokumente" }],
    origin: { task_id: "task_seed0001", created_at: "2026-11-02T09:30:00Z" },
    requires: { tools: ["windows.read", "memory.search", "files.write"], apps: ["notepad.exe"], vision: false },
    preconditions: ["Editor-Fenster mit dem Protokoll ist geöffnet."],
    scope: { files: { roots: ["C:\\Users\\mock\\Dokumente"], write_dir: "C:\\Users\\mock\\Dokumente" }, browser: null, windows: { source_ids: [], allowed_processes: ["notepad.exe"] }, memory: true, external_effects: false },
    steps: [
      { tool: "windows.read", arguments_template: { process: "notepad.exe" }, check: "Text gelesen" },
      { tool: "memory.search", arguments_template: { query: "{thema}" }, check: "Quellen gefunden" },
      { tool: "files.write", arguments_template: { path: "{zielordner}\\zusammenfassung.md" }, check: "Datei vorhanden" },
    ],
    final_checks: ["Datei zusammenfassung.md existiert und ist nicht leer"],
    last_verified_at: "2026-11-02T09:35:00Z",
    known_limits_de: ["Funktioniert nur mit dem klassischen Editor (notepad.exe)."],
  };
  recipes.set(seeded.id, seeded);
  route("GET", /^\/api\/agent\/recipes$/, (ctx) => json(ctx.res, 200, { recipes: [...recipes.values()] }));
  route("GET", /^\/api\/agent\/recipes\/([^/]+)$/, (ctx) => {
    const r = recipes.get(ctx.params[0]);
    return r ? json(ctx.res, 200, { recipe: r }) : error(ctx.res, 404, "Ablauf nicht gefunden.", "not_found");
  });
  route("DELETE", /^\/api\/agent\/recipes\/([^/]+)$/, (ctx) => {
    if (!recipes.delete(ctx.params[0])) return error(ctx.res, 404, "Ablauf nicht gefunden.", "not_found");
    json(ctx.res, 200, { ok: true });
  });

  // ----- v1.5: workflows ----------------------------------------------------------------
  const dependency = (kind, key, { valid = true, origin = "observed", step_index = null, reason = null } = {}) => ({
    id: `dep_${randomUUID().slice(0, 8)}`,
    kind,
    key,
    revision: valid ? "r1" : "r0",
    origin,
    scope: kind === "file" ? "files" : kind === "app" ? "windows" : null,
    step_index,
    valid,
    reason,
    invalidated_by: valid ? null : "fact_update",
    invalidated_at: valid ? null : now(),
  });
  function depStats(deps) {
    const by_kind = {};
    for (const d of deps) by_kind[d.kind] = (by_kind[d.kind] ?? 0) + 1;
    return { total: deps.length, valid: deps.filter((d) => d.valid).length, invalid: deps.filter((d) => !d.valid).length, by_kind, inferred: deps.filter((d) => d.origin === "inferred").length };
  }
  function makeWorkflow({ id, name, purpose_de, status, parameters, bindings, steps, checks, dependencies, scope, origin, origin_kind, known_limits_de, requires, derived_steps, external_effects, stats = {} }) {
    const wf = {
      id,
      version: 1,
      name,
      purpose_de,
      status,
      parameters,
      preconditions: ["Zielfenster ist geöffnet und nicht minimiert."],
      bindings,
      steps,
      checks,
      dependencies,
      scope,
      origin,
      tool_contract_hash: "tc_9a1b2c",
      model_hint: state.activeChat ?? null,
      known_limits_de,
      created_at: now(),
      updated_at: now(),
      last_verified_at: stats.last_verified_at ?? null,
      stats: {
        success_runs: 0,
        failed_runs: 0,
        distinct_param_sets: 0,
        negative_checked: false,
        last_verified_at: null,
        last_failed_at: null,
        origin_kind,
        ...stats,
        dependencies: depStats(dependencies),
      },
      requires,
      derived_steps,
      external_effects,
    };
    workflows.set(wf.id, wf);
    workflowRuns.set(wf.id, []);
    return wf;
  }
  const wfStep = (index, tool, arguments_template, extra = {}) => ({
    index,
    tool,
    arguments_template,
    target: null,
    read_set: [],
    write_set: [],
    preconditions: [],
    postconditions: [],
    side_effect: tool.includes("write") || tool.includes("click") ? "local" : "none",
    depends_on: [],
    derive: false,
    derive_from: [],
    check_de: "",
    ...extra,
  });
  const seedScope = { files: { roots: ["C:\\Users\\mock\\Dokumente"], write_dir: "C:\\Users\\mock\\Dokumente" }, browser: null, windows: { source_ids: [], allowed_processes: ["notepad.exe"] }, memory: true, external_effects: false };
  makeWorkflow({
    id: "wf_seed0001",
    name: "Protokoll aus Editor zusammenfassen",
    purpose_de: "Das offene Protokoll im Editor lesen und eine Zusammenfassung als Datei ablegen.",
    status: "verified",
    parameters: [{ name: "zielordner", description_de: "Ordner für die Zusammenfassung", example: "C:\\Users\\mock\\Dokumente", kind: "path", required: true }],
    bindings: [{ role: "editor", kind: "window", hint: "Editor-Fenster mit dem Protokoll", required: true, original: "win:132456:4120" }],
    steps: [
      wfStep(0, "windows.read", { process: "notepad.exe" }, { target: { role: "editor", name: "Protokoll.txt – Editor" }, read_set: ["window:editor"], check_de: "Text gelesen" }),
      wfStep(1, "memory.search", { query: "{thema}" }, { read_set: ["memory"], check_de: "Quellen gefunden" }),
      wfStep(2, "files.write", { path: "{zielordner}\\zusammenfassung.md" }, { write_set: ["{zielordner}\\zusammenfassung.md"], depends_on: ["0", "1"], derive: true, derive_from: [0, 1], check_de: "Datei vorhanden", postconditions: [{ kind: "file_exists", target: "{zielordner}\\zusammenfassung.md", expect: [], description_de: "Datei vorhanden" }] }),
    ],
    checks: [{ kind: "file_contains", target: "{zielordner}\\zusammenfassung.md", expect: ["Zusammenfassung"], origin: "user", description_de: "Datei enthält „Zusammenfassung“" }],
    dependencies: [
      dependency("app", "notepad.exe", { step_index: 0 }),
      dependency("binding", "editor", { step_index: 0 }),
      dependency("tool_contract", "tc_9a1b2c"),
      dependency("fact", "protokoll.format", { origin: "inferred", step_index: 1 }),
    ],
    scope: seedScope,
    origin: { task_id: "task_seed0001", created_at: "2026-11-02T09:30:00Z" },
    origin_kind: "migrated",
    known_limits_de: ["Funktioniert nur mit dem klassischen Editor (notepad.exe)."],
    requires: { tools: ["windows.read", "memory.search", "files.write"], apps: ["notepad.exe"], vision: false },
    derived_steps: [2],
    external_effects: false,
    stats: { success_runs: 3, failed_runs: 0, distinct_param_sets: 2, negative_checked: true, last_verified_at: "2026-11-09T09:35:00Z" },
  });
  makeWorkflow({
    id: "wf_seed0002",
    name: "Quartalsbericht herunterladen",
    purpose_de: "Im Intranet den aktuellen Quartalsbericht öffnen und als PDF im Zielordner ablegen.",
    status: "candidate",
    parameters: [
      { name: "quartal", description_de: "Quartal, z. B. Q3", example: "Q3", kind: "text", required: true },
      { name: "stichtag", description_de: "Stichtag des Berichts", example: "2026-09-30", kind: "date", required: false },
    ],
    bindings: [{ role: "ablage", kind: "dir", hint: "Ordner für den Bericht", required: true, original: "C:\\Users\\mock\\Downloads" }],
    steps: [
      wfStep(0, "browser.navigate", { url: "https://intranet.example/berichte/{quartal}" }, { read_set: ["https://intranet.example"], check_de: "Seite geladen" }),
      wfStep(1, "browser.click", { selector: "a.download" }, { depends_on: ["0"], check_de: "Download gestartet" }),
      wfStep(2, "files.write", { path: "{ablage}\\quartalsbericht-{quartal}.pdf" }, { write_set: ["{ablage}\\quartalsbericht-{quartal}.pdf"], depends_on: ["1"], check_de: "PDF vorhanden" }),
    ],
    checks: [{ kind: "file_exists", target: "{ablage}\\quartalsbericht-{quartal}.pdf", expect: [], origin: "user", description_de: "PDF liegt im Zielordner" }],
    dependencies: [
      dependency("file", "https://intranet.example/berichte", { valid: false, step_index: 0, reason: "Seite hat sich seit der Aufnahme geändert" }),
      dependency("binding", "ablage", { step_index: 2 }),
      dependency("tool_contract", "tc_9a1b2c"),
    ],
    scope: { files: { roots: ["C:\\Users\\mock\\Downloads"], write_dir: "C:\\Users\\mock\\Downloads" }, browser: { enabled: true, allowed_origins: ["https://intranet.example"] }, windows: null, memory: false, external_effects: false },
    origin: { teach_session: "teach_seed0001", created_at: "2026-11-10T14:00:00Z" },
    origin_kind: "taught",
    known_limits_de: [],
    requires: { tools: ["browser.navigate", "browser.click", "files.write"], apps: [], vision: false },
    derived_steps: [],
    external_effects: false,
  });

  function checkPlan(wf, { strict = false } = {}) {
    const invalid = wf.dependencies.filter((d) => !d.valid);
    const missing = wf.bindings.filter((b) => b.required && !hostBindings.has(b.role));
    const repair = [...new Set(invalid.map((d) => d.step_index).filter((i) => typeof i === "number"))];
    const coverage = wf.steps.length ? (wf.steps.length - repair.length) / wf.steps.length : 1;
    const mode = strict ? "replan_forced" : invalid.length === 0 ? "reuse" : coverage >= 0.5 ? "repair" : "replan";
    const reasons_de = [];
    if (mode === "reuse") reasons_de.push("Alle Abhängigkeiten sind gültig; die Schritte werden ohne Modellaufruf wiederholt.");
    for (const d of invalid) reasons_de.push(`${d.kind} „${d.key}“ ungültig: ${d.reason ?? "geändert"}`);
    if (missing.length) reasons_de.push(`Fehlende Zuordnungen: ${missing.map((b) => b.role).join(", ")}`);
    if (strict) reasons_de.push("Vergleichslauf ohne Wiederverwendung angefordert.");
    const bindings = {};
    for (const b of wf.bindings) if (hostBindings.has(b.role)) bindings[b.role] = hostBindings.get(b.role).target;
    return {
      plan: {
        mode,
        reasons_de,
        repair_steps: repair,
        strict,
        coverage: strict ? 0 : coverage,
        unknown_dependencies: wf.dependencies.some((d) => d.origin === "inferred"),
        invalid,
        bindings,
        missing_bindings: missing.map((b) => b.role),
        gate_ms: 7 + Math.round(Math.random() * 9),
        workflow_id: wf.id,
        workflow_name: wf.name,
        run_id: null,
      },
      dependencies: wf.dependencies,
      bindings,
      missing_bindings: missing,
    };
  }
  function recordRun(t) {
    const wf = workflows.get(t.workflow_id);
    if (!wf) return;
    const ok = t.outcome !== "not_fulfilled";
    const run = {
      id: `run_${randomUUID().slice(0, 8)}`,
      workflow_id: wf.id,
      task_id: t.id,
      host_id: HOST_ID,
      decision: t.reuse?.mode ?? "replan",
      reason: t.reuse?.reasons_de?.[0] ?? "",
      params_hash: `ph_${JSON.stringify(t._parameters ?? {}).length}`,
      outcome: t.outcome,
      llm_calls: t.llm_calls,
      reused_steps: (taskActions.get(t.id) ?? []).filter((a) => a.origin === "workflow").length,
      repaired_steps: JSON.stringify(t.reuse?.repair_steps ?? []),
      skipped_steps: "[]",
      gate_ms: t.reuse?.gate_ms ?? 0,
      created_at: t.created_at,
      finished_at: now(),
    };
    workflowRuns.get(wf.id).push(run);
    if (ok) {
      wf.stats.success_runs += 1;
      wf.stats.last_verified_at = now();
      wf.last_verified_at = now();
      if (wf.status === "candidate") wf.status = "verified";
      // A successful run re-validated the dependencies.
      for (const d of wf.dependencies) {
        d.valid = true;
        d.reason = null;
        d.invalidated_by = null;
        d.invalidated_at = null;
      }
      wf.stats.dependencies = depStats(wf.dependencies);
    } else {
      wf.stats.failed_runs += 1;
      wf.stats.last_failed_at = now();
    }
    wf.updated_at = now();
  }
  function compileTask(t, name, params) {
    const actions = taskActions.get(t.id) ?? [];
    const steps = actions.map((a, i) => wfStep(i, a.tool, a.arguments, { check_de: a.result?.summary_de ?? "", depends_on: i > 0 ? [String(i - 1)] : [] }));
    const wf = makeWorkflow({
      id: `wf_${randomUUID().slice(0, 8)}`,
      name,
      purpose_de: t.goal,
      status: "candidate",
      parameters: (Array.isArray(params) ? params : []).map((p) => ({ name: String(p.name ?? ""), description_de: String(p.description_de ?? ""), example: String(p.example ?? ""), kind: "text", required: true })),
      bindings: t.scopes.windows?.source_ids?.length ? [{ role: "zielfenster", kind: "window", hint: "Fenster der Aufnahme", required: true, original: t.scopes.windows.source_ids[0] }] : [],
      steps,
      checks: t.success_criteria.length ? t.success_criteria : (t.outcome_report?.checks ?? []).map((c) => c.criterion),
      dependencies: [dependency("tool_contract", "tc_9a1b2c"), ...(t.scopes.windows?.allowed_processes ?? []).map((p) => dependency("app", p, { step_index: 0 }))],
      scope: t.scopes,
      origin: { task_id: t.id, created_at: now() },
      origin_kind: "compiled",
      known_limits_de: ["Aus einer Aufgabe abgeleitet; erst ein Prüflauf bestätigt den Ablauf."],
      requires: { tools: [...new Set(actions.map((a) => a.tool))], apps: t.scopes.windows?.allowed_processes ?? [], vision: false },
      derived_steps: [],
      external_effects: Boolean(t.scopes.external_effects),
    });
    broadcast("workflows", { stale: [], facts: null, by: "compile" });
    return wf;
  }

  route("GET", /^\/api\/agent\/workflows$/, (ctx) => json(ctx.res, 200, { workflows: [...workflows.values()] }));
  route("GET", /^\/api\/agent\/workflows\/([^/]+)$/, (ctx) => {
    const wf = workflows.get(ctx.params[0]);
    if (!wf) return error(ctx.res, 404, "Ablauf nicht gefunden.", "not_found");
    const bindings = {};
    for (const b of wf.bindings) if (hostBindings.has(b.role)) bindings[b.role] = hostBindings.get(b.role);
    json(ctx.res, 200, { workflow: wf, dependencies: wf.dependencies, runs: workflowRuns.get(wf.id) ?? [], bindings });
  });
  route("PATCH", /^\/api\/agent\/workflows\/([^/]+)$/, (ctx) => {
    const wf = workflows.get(ctx.params[0]);
    if (!wf) return error(ctx.res, 404, "Ablauf nicht gefunden.", "not_found");
    const b = ctx.body ?? {};
    if (typeof b.name === "string" && b.name.trim()) wf.name = b.name.trim();
    if (Array.isArray(b.parameters)) wf.parameters = b.parameters;
    if (Array.isArray(b.known_limits_de)) wf.known_limits_de = b.known_limits_de;
    if (Array.isArray(b.steps)) {
      for (const patch of b.steps) {
        const s = wf.steps.find((x) => x.index === patch.index);
        if (!s) continue;
        if (patch.arguments_template) s.arguments_template = patch.arguments_template;
        if (patch.postconditions) s.postconditions = patch.postconditions;
        if (typeof patch.check_de === "string") s.check_de = patch.check_de;
      }
    }
    if (b.status !== undefined) {
      if (!["candidate", "demoted", "retired"].includes(b.status)) return error(ctx.res, 422, "Ungültiger Status.", "invalid_status");
      wf.status = b.status;
    }
    wf.updated_at = now();
    wf.version += 1;
    json(ctx.res, 200, { workflow: wf });
  });
  route("DELETE", /^\/api\/agent\/workflows\/([^/]+)$/, (ctx) => {
    if (!workflows.delete(ctx.params[0])) return error(ctx.res, 404, "Ablauf nicht gefunden.", "not_found");
    workflowRuns.delete(ctx.params[0]);
    json(ctx.res, 200, { ok: true });
  });
  route("GET", /^\/api\/agent\/workflows\/([^/]+)\/check$/, (ctx) => {
    const wf = workflows.get(ctx.params[0]);
    if (!wf) return error(ctx.res, 404, "Ablauf nicht gefunden.", "not_found");
    json(ctx.res, 200, checkPlan(wf));
  });
  route("POST", /^\/api\/agent\/workflows\/([^/]+)\/negative-check$/, (ctx) => {
    const wf = workflows.get(ctx.params[0]);
    if (!wf) return error(ctx.res, 404, "Ablauf nicht gefunden.", "not_found");
    // Without its preconditions the gate must refuse to reuse the workflow.
    const ok = wf.dependencies.length > 0;
    wf.stats.negative_checked = ok;
    wf.updated_at = now();
    json(ctx.res, 200, {
      result: { ok, mode: ok ? "replan" : "reuse", reasons_de: ok ? ["Ohne gültige Abhängigkeiten wählt die Prüfung „neu geplant“ – keine blinde Wiederverwendung."] : ["Der Ablauf hat keine Abhängigkeiten und würde immer wiederverwendet."] },
      workflow: wf,
    });
  });
  route("POST", /^\/api\/agent\/workflows\/([^/]+)\/run$/, (ctx) => {
    const wf = workflows.get(ctx.params[0]);
    if (!wf) return error(ctx.res, 404, "Ablauf nicht gefunden.", "not_found");
    if (wf.status === "retired") return error(ctx.res, 409, "Der Ablauf ist stillgelegt.", "workflow_retired");
    const params = ctx.body?.parameters && typeof ctx.body.parameters === "object" ? ctx.body.parameters : {};
    const missing = wf.parameters.filter((p) => p.required && (params[p.name] === undefined || params[p.name] === null || params[p.name] === "")).map((p) => p.name);
    if (missing.length) return json(ctx.res, 422, { detail: `Parameter fehlen: ${missing.join(", ")}.`, code: "parameters_missing", missing });
    const strict = ctx.body?.mode === "replan";
    const { plan } = checkPlan(wf, { strict });
    plan.run_id = `run_${randomUUID().slice(0, 8)}`;
    const paramText = Object.entries(params)
      .map(([k, v]) => `${k}=${String(v)}`)
      .join(", ");
    const scopes = ctx.body?.scopes ?? wf.scope;
    const t = makeTask({ goal: `${wf.purpose_de}${paramText ? ` (${paramText})` : ""}`, scopes, budget: ctx.body?.budget ?? null, body: ctx.body ?? {}, workflow: wf, reuse: plan, parameters: params });
    wf.stats.distinct_param_sets += 1;
    json(ctx.res, 201, { task: privateView(t) });
  });
  route("POST", /^\/api\/agent\/tasks\/([^/]+)\/compile$/, (ctx) => {
    const t = tasks.get(ctx.params[0]);
    if (!t) return error(ctx.res, 404, "Aufgabe nicht gefunden.", "not_found");
    if (t.state !== "completed" || !t.result?.ok || t.outcome === "not_fulfilled") {
      return error(ctx.res, 409, "Nur erfolgreich abgeschlossene Aufgaben mit erfülltem Ergebnis lassen sich als Ablauf speichern.", "not_completed");
    }
    const name = String(ctx.body?.name ?? "").trim();
    if (!name) return error(ctx.res, 422, "Name fehlt.", "invalid_name");
    json(ctx.res, 201, { workflow: compileTask(t, name, ctx.body?.parameters) });
  });

  // ----- v1.5: host bindings ---------------------------------------------------------------
  route("GET", /^\/api\/agent\/bindings$/, (ctx) => json(ctx.res, 200, { host_id: HOST_ID, bindings: [...hostBindings.values()] }));
  route("PUT", /^\/api\/agent\/bindings$/, (ctx) => {
    const role = String(ctx.body?.role ?? "").trim();
    const kind = String(ctx.body?.kind ?? "");
    const target = String(ctx.body?.target ?? "").trim();
    if (!role || !target) return error(ctx.res, 422, "Rolle und Ziel sind nötig.", "invalid_binding");
    if (!["dir", "file", "app", "window", "url", "account", "model"].includes(kind)) return error(ctx.res, 422, "Unbekannte Art der Zuordnung.", "invalid_binding");
    const binding = { host_id: HOST_ID, role, kind, target, verified_at: now(), created_at: hostBindings.get(role)?.created_at ?? now() };
    hostBindings.set(role, binding);
    json(ctx.res, 200, { binding });
  });
  route("DELETE", /^\/api\/agent\/bindings\/([^/]+)$/, (ctx) => {
    if (!hostBindings.delete(ctx.params[0])) return error(ctx.res, 404, "Zuordnung nicht gefunden.", "not_found");
    json(ctx.res, 200, { ok: true });
  });

  // ----- v1.5: teach ("Ablauf beibringen") ---------------------------------------------------
  const teachView = (s) => {
    const v = { ...s };
    for (const k of Object.keys(v)) if (k.startsWith("_")) delete v[k];
    return v;
  };
  const teachEvent = (s) => broadcast("teach", { id: s.id, state: s.state, steps: s.steps.length });
  const taskRunning = () => [...tasks.values()].some((t) => !["completed", "failed", "cancelled"].includes(t.state));
  route("GET", /^\/api\/agent\/teach$/, (ctx) => {
    const list = [...teachSessions.values()].map(teachView);
    json(ctx.res, 200, { active: list.filter((s) => s.state === "recording").length, sessions: list });
  });
  route("POST", /^\/api\/agent\/teach$/, (ctx) => {
    const b = ctx.body ?? {};
    const name = String(b.name ?? "").trim();
    const source = b.source;
    if (!name) return error(ctx.res, 422, "Name fehlt.", "invalid_name");
    if (!["browser", "window"].includes(source)) return error(ctx.res, 422, "Quelle muss browser oder window sein.", "invalid_source");
    if (source === "browser" && !/^https?:\/\//.test(String(b.url ?? ""))) return error(ctx.res, 422, "Startadresse fehlt.", "invalid_url");
    if (source === "window" && !findSource(b.source_id)) return error(ctx.res, 422, "Fenster nicht gefunden.", "invalid_source_id");
    if (source === "browser" && !b.scopes?.browser?.enabled) return error(ctx.res, 422, "Browser-Zugriff fehlt.", "invalid_scopes");
    if (source === "window" && !b.scopes?.windows) return error(ctx.res, 422, "Fenster-Zugriff fehlt.", "invalid_scopes");
    if (taskRunning() || [...teachSessions.values()].some((s) => s.state === "recording")) return error(ctx.res, 409, "Gerade läuft eine Aufgabe oder eine Aufnahme.", "busy");
    const session = {
      id: `teach_${randomUUID().slice(0, 8)}`,
      name,
      source,
      url: source === "browser" ? String(b.url) : null,
      source_id: source === "window" ? String(b.source_id) : null,
      state: "recording",
      steps: [],
      created_at: now(),
      finished_at: null,
      events: 0,
      workflow_id: null,
      note_de: source === "browser" ? "Mock: Die Seite zeichnet alle 3 s einen synthetischen Schritt auf." : "Mock: Jedes „Schritt erfassen“ liefert einen synthetischen Feldwert.",
      scopes: b.scopes,
      _counter: 0,
    };
    teachSessions.set(session.id, session);
    if (source === "browser") {
      // The own browser page records clicks / inputs by itself: simulate that.
      const tick = () => {
        if (session.state !== "recording") return;
        session._counter += 1;
        const n = session._counter;
        session.steps.push(
          n === 1
            ? { tool: "browser.navigate", arguments: { url: session.url }, check_de: "Seite geladen", evidence: { title: "Intranet" }, target: null, at: now() }
            : n % 2 === 0
              ? { tool: "browser.fill", arguments: { selector: `#feld${n}`, text: n === 2 ? "Müller GmbH" : `Wert ${n}` }, check_de: "Feld ausgefüllt", evidence: null, target: { role: "edit", name: n === 2 ? "Firmenname" : `Feld ${n}` }, at: now() }
              : { tool: "browser.click", arguments: { selector: `button#weiter${n}` }, check_de: "Weiter geklickt", evidence: null, target: { role: "button", name: "Weiter" }, at: now() },
        );
        session.events += 1;
        teachEvent(session);
        if (n < 6) timers.set(`teach_${session.id}`, setTimeout(tick, 3000));
      };
      timers.set(`teach_${session.id}`, setTimeout(tick, 1500));
    }
    teachEvent(session);
    json(ctx.res, 201, { session: teachView(session) });
  });
  route("GET", /^\/api\/agent\/teach\/([^/]+)$/, (ctx) => {
    const s = teachSessions.get(ctx.params[0]);
    return s ? json(ctx.res, 200, { session: teachView(s) }) : error(ctx.res, 404, "Aufnahme nicht gefunden.", "not_found");
  });
  route("POST", /^\/api\/agent\/teach\/([^/]+)\/capture$/, (ctx) => {
    const s = teachSessions.get(ctx.params[0]);
    if (!s) return error(ctx.res, 404, "Aufnahme nicht gefunden.", "not_found");
    if (s.state !== "recording") return error(ctx.res, 409, "Die Aufnahme ist beendet.", "not_recording");
    const invoke = typeof ctx.body?.invoke_name === "string" ? ctx.body.invoke_name.trim() : "";
    const added = [];
    if (s.source === "window") {
      s._counter += 1;
      const src = findSource(s.source_id);
      const fields = ["Betreff", "Betrag", "Empfänger", "Notiz"];
      const field = fields[(s._counter - 1) % fields.length];
      added.push({ tool: "windows.set_value", arguments: { source_id: s.source_id, element: field, value: `Beispielwert ${s._counter}` }, check_de: `Feld „${field}“ hat einen Wert`, evidence: { before: "", after: `Beispielwert ${s._counter}` }, target: { role: "edit", name: field }, at: now() });
      if (invoke) added.push({ tool: "windows.invoke", arguments: { source_id: s.source_id, name: invoke }, check_de: `„${invoke}“ ausgelöst`, evidence: { window: src?.title ?? null }, target: { role: "button", name: invoke }, at: now() });
    } else {
      added.push({ tool: "browser.read", arguments: { url: s.url }, check_de: "Seite erneut gelesen", evidence: { elements: 12 }, target: null, at: now() });
    }
    s.steps.push(...added);
    s.events += 1;
    teachEvent(s);
    json(ctx.res, 200, { new_steps: added.length, session: teachView(s) });
  });
  route("POST", /^\/api\/agent\/teach\/([^/]+)\/check$/, (ctx) => {
    const s = teachSessions.get(ctx.params[0]);
    if (!s) return error(ctx.res, 404, "Aufnahme nicht gefunden.", "not_found");
    if (s.state !== "recording") return error(ctx.res, 409, "Die Aufnahme ist beendet.", "not_recording");
    const check = ctx.body?.check;
    if (!check || typeof check.kind !== "string" || !Array.isArray(check.expect)) return error(ctx.res, 422, "Prüfung unvollständig.", "invalid_check");
    const allowed = s.source === "browser" ? ["page_text"] : ["window_text", "element_value"];
    if (!allowed.includes(check.kind)) return error(ctx.res, 422, `Für diese Quelle sind nur ${allowed.join(", ")} erlaubt.`, "invalid_check");
    const step = { tool: "verify.check", arguments: { check }, check_de: check.description_de || `„${check.expect.join("“, „")}“ sichtbar`, evidence: { found: true }, target: null, at: now() };
    s.steps.push(step);
    s.events += 1;
    teachEvent(s);
    json(ctx.res, 200, { step, session: teachView(s) });
  });
  route("DELETE", /^\/api\/agent\/teach\/([^/]+)\/steps\/([^/]+)$/, (ctx) => {
    const s = teachSessions.get(ctx.params[0]);
    if (!s) return error(ctx.res, 404, "Aufnahme nicht gefunden.", "not_found");
    const index = Number(ctx.params[1]);
    if (!Number.isInteger(index) || index < 0 || index >= s.steps.length) return error(ctx.res, 404, "Schritt nicht gefunden.", "not_found");
    s.steps.splice(index, 1);
    teachEvent(s);
    json(ctx.res, 200, { session: teachView(s) });
  });
  route("POST", /^\/api\/agent\/teach\/([^/]+)\/finish$/, (ctx) => {
    const s = teachSessions.get(ctx.params[0]);
    if (!s) return error(ctx.res, 404, "Aufnahme nicht gefunden.", "not_found");
    if (s.state !== "recording") return error(ctx.res, 409, "Die Aufnahme ist beendet.", "not_recording");
    if (s.steps.length === 0) return error(ctx.res, 422, "Keine Schritte aufgezeichnet.", "no_steps");
    const params = Array.isArray(ctx.body?.parameters) ? ctx.body.parameters : [];
    const checks = Array.isArray(ctx.body?.checks) ? ctx.body.checks : [];
    clearTimeout(timers.get(`teach_${s.id}`));
    const steps = s.steps.map((st, i) => {
      const args = { ...st.arguments };
      for (const p of params) for (const k of Object.keys(args)) if (typeof args[k] === "string" && p.value && args[k] === p.value) args[k] = `{${p.name}}`;
      return wfStep(i, st.tool, args, { target: st.target, check_de: st.check_de, depends_on: i > 0 ? [String(i - 1)] : [] });
    });
    const wf = makeWorkflow({
      id: `wf_${randomUUID().slice(0, 8)}`,
      name: s.name,
      purpose_de: `Beigebracht: ${s.name}`,
      status: "candidate",
      parameters: params.map((p) => ({ name: String(p.name ?? ""), description_de: String(p.description_de ?? ""), example: String(p.example ?? p.value ?? ""), kind: "text", required: true })),
      bindings: s.source === "window" ? [{ role: "zielfenster", kind: "window", hint: "Fenster der Aufnahme", required: true, original: s.source_id }] : [],
      steps,
      checks,
      dependencies: [dependency("tool_contract", "tc_9a1b2c"), s.source === "browser" ? dependency("file", s.url, { step_index: 0 }) : dependency("binding", "zielfenster", { step_index: 0 })],
      scope: s.scopes,
      origin: { teach_session: s.id, created_at: now() },
      origin_kind: "taught",
      known_limits_de: ["Beigebracht; erst ein Prüflauf bestätigt den Ablauf."],
      requires: { tools: [...new Set(steps.map((x) => x.tool))], apps: s.scopes.windows?.allowed_processes ?? [], vision: false },
      derived_steps: [],
      external_effects: false,
    });
    s.state = "finished";
    s.finished_at = now();
    s.workflow_id = wf.id;
    teachEvent(s);
    broadcast("workflows", { stale: [], facts: null, by: "teach" });
    json(ctx.res, 201, { session: teachView(s), workflow: wf, task_id: null });
  });
  route("POST", /^\/api\/agent\/teach\/([^/]+)\/cancel$/, (ctx) => {
    const s = teachSessions.get(ctx.params[0]);
    if (!s) return error(ctx.res, 404, "Aufnahme nicht gefunden.", "not_found");
    clearTimeout(timers.get(`teach_${s.id}`));
    if (s.state === "recording") {
      s.state = "cancelled";
      s.finished_at = now();
      teachEvent(s);
    }
    json(ctx.res, 200, { session: teachView(s) });
  });

  // ----- global stop ------------------------------------------------------------------
  route("POST", /^\/api\/agent\/stop$/, (ctx) => {
    const lockStart = Date.now();
    let stoppedTeach = 0;
    for (const s of teachSessions.values()) {
      if (s.state !== "recording") continue;
      clearTimeout(timers.get(`teach_${s.id}`));
      s.state = "cancelled";
      s.finished_at = now();
      teachEvent(s);
      stoppedTeach += 1;
    }
    let stoppedTasks = 0;
    for (const t of tasks.values()) {
      if (["completed", "failed", "cancelled"].includes(t.state)) continue;
      clearTimeout(taskTimers.get(t.id));
      t.pending_question = null;
      for (const a of taskActions.get(t.id) ?? []) {
        if (["proposed", "validated", "executing"].includes(a.state)) {
          a.state = "cancelled";
          a.finished_at = now();
          emitAction(t, a);
        }
      }
      setState(t, "cancelled", "Globaler Stopp");
      stoppedTasks += 1;
    }
    let stoppedLive = 0;
    const gen = state.generation;
    if (gen && [...sessions.values()].some((s) => s.conversation_id === gen.conversationId)) {
      gen.stopped = true;
      stoppedLive += 1;
    }
    broadcast("stop", { source: "api", at: now() });
    json(ctx.res, 200, { stopped_tasks: stoppedTasks, stopped_live: stoppedLive, ended_sessions: 0, released_keys: 0, stopped_teach: stoppedTeach, lock_ms: Date.now() - lockStart });
  });

  return { sessions, tasks };
}
