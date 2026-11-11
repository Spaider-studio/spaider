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
  const taskTimers = new Map();
  const taskListeners = new Map(); // taskId -> Set<res>
  const recipes = new Map();

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
  route("GET", /^\/api\/agent\/capabilities$/, (ctx) =>
    json(ctx.res, 200, {
      tools: TOOLS,
      model: { id: state.activeChat ?? "none", vision_input: false, image_grounding: "none", tool_call_format: "json_schema", structured_output: true },
      browser: { installed: browser.installed, version: browserStatus().version, path: browserStatus().path },
      windows_automation: { available: true, reason_de: "UI Automation verfügbar." },
      desktop_interactive: true,
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
        return schedule(t, next);
      }
      if (t.state === "observing") {
        emitActivity(t, "capture", "done", "Quelle aufgenommen", { elements: 3 });
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
          t.result = {
            ok: true,
            summary_de: plan.external ? "Formular gesendet und Bestätigung im Zielfenster gesehen." : `Zusammenfassung unter ${plan.writeDir}\\zusammenfassung.md abgelegt und geprüft.`,
            artifacts: plan.external
              ? [{ kind: "url", path: null, url: "https://intranet.example/sent/42", label: "Bestätigungsseite" }]
              : [{ kind: "file", path: `${plan.writeDir}\\zusammenfassung.md`, url: null, label: "zusammenfassung.md" }],
            verified: true,
          };
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
        task.llm_calls += 1;
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
  route("POST", /^\/api\/agent\/tasks$/, (ctx) => {
    const goal = String(ctx.body?.goal ?? "").trim();
    if (!goal) return error(ctx.res, 422, "Ziel fehlt.", "invalid_goal");
    const scopes = ctx.body?.scopes ?? { files: null, browser: null, windows: null, memory: true, external_effects: false };
    const budget = ctx.body?.budget ?? null;
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
      conversation_id: ctx.body?.conversation_id ?? null,
      live_session_id: ctx.body?.live_session_id ?? null,
      recipe_id: ctx.body?.recipe_id ?? null,
      llm_calls: 0,
      host_bound: Boolean(scopes.windows),
      last_error: null,
      _seq: 0,
      _startedMs: Date.now(),
      _answered: false,
    };
    t._plan = planFor(t);
    tasks.set(t.id, t);
    taskActions.set(t.id, []);
    json(ctx.res, 201, { task: taskView(t) });
    broadcast("task", taskView(t));
    runTask(t);
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
    json(ctx.res, 201, { recipe });
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

  // ----- global stop ------------------------------------------------------------------
  route("POST", /^\/api\/agent\/stop$/, (ctx) => {
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
    json(ctx.res, 200, { stopped_tasks: stoppedTasks, stopped_live: stoppedLive });
  });

  return { sessions, tasks };
}
