/**
 * SpAIder Foldable - Electron main process.
 *
 * Security: contextIsolation, sandbox, no nodeIntegration, no remote content,
 * window.open denied, in-page navigation blocked, strict CSP in index.html.
 * Portability: every Electron path is redirected below <appRoot>/data before
 * app.whenReady(); the core is started/stopped by CoreManager.
 *
 * v1.2: global hotkeys (overlay / stop), the live overlay window, the tray
 * icon (tray_mode hides the main window instead of quitting; no autostart)
 * and the measured hotkey -> overlay latencies (perf.ts).
 */
import { app, BrowserWindow, dialog, ipcMain, Menu, session, shell, type Tray } from "electron";
import fs from "node:fs";
import path from "node:path";
import { validateAccelerator } from "../shared-types/accelerator";
import {
  DEFAULT_HOTKEYS,
  IPC,
  isAllowedExternalUrl,
  type AppInfo,
  type BackendStatus,
  type HotkeyConfig,
  type HotkeyEvent,
  type HotkeyResult,
  type OverlayShowOptions,
  type TaskFormPrefill,
  type WindowSource,
} from "../shared-types/bridge";
import { CoreManager } from "./core";
import { HotkeyManager } from "./hotkeys";
import { initLauncherLog, log } from "./log";
import { OverlayManager } from "./overlay";
import { applyPortablePaths, resolveAppPaths } from "./paths";
import { getPerfSamples, recordSample } from "./perf";
import { createTray } from "./tray";
import { loadWindowState, MIN_HEIGHT, MIN_WIDTH, saveWindowState, trackWindowState } from "./window-state";

// ---------------------------------------------------------------------------
// Paths and single instance (before app.whenReady())
// ---------------------------------------------------------------------------
const paths = resolveAppPaths();
applyPortablePaths(paths);
initLauncherLog(paths.logsDir);
log(`starting (packaged=${paths.isPackaged}) appRoot=${paths.appRoot}`);

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  log("another instance is running, exiting");
  app.exit(0);
}

Menu.setApplicationMenu(null);

const core = new CoreManager(paths);
let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let quitting = false;
let quitPromise: Promise<void> | null = null;
let trayMode = false;

const DEV_SERVER_URL = app.isPackaged ? undefined : process.env.VITE_DEV_SERVER_URL;
const SCREENSHOT_PATH = process.env.SPAIDER_FOLDABLE_SCREENSHOT;
/** Verification only: loads the renderer with ?perf=1 so window.__spaiderPerf exists. */
const PERF_QUERY = process.env.SPAIDER_FOLDABLE_PERF === "1";
const PRELOAD = path.join(__dirname, "..", "preload", "index.js");
const RENDERER_HTML = path.join(__dirname, "..", "renderer", "index.html");
const ICON_PNG = path.resolve(__dirname, "..", "..", "assets", "icon.png");
/** The contract aims at <= 50 ms for POST /api/live/foreground; never block the overlay longer than this. */
const FOREGROUND_TIMEOUT_MS = 400;
const STOP_TIMEOUT_MS = 2000;

/** Conversations whose answers are streaming, reported per renderer (webContents id). */
const activeConversations = new Map<number, string[]>();

function loadRenderer(target: BrowserWindow, hash: string): void {
  if (DEV_SERVER_URL) {
    const url = `${DEV_SERVER_URL}${PERF_QUERY ? "?perf=1" : ""}${hash ? `#${hash}` : ""}`;
    void target.loadURL(url);
    target.webContents.on("did-fail-load", () => {
      setTimeout(() => {
        if (!target.isDestroyed()) void target.loadURL(url);
      }, 700);
    });
  } else {
    void target.loadFile(RENDERER_HTML, { ...(PERF_QUERY ? { query: { perf: "1" } } : {}), ...(hash ? { hash } : {}) });
  }
}

function sendToAll(channel: string, payload: unknown): void {
  for (const w of BrowserWindow.getAllWindows()) {
    if (!w.isDestroyed()) w.webContents.send(channel, payload);
  }
}

// ---------------------------------------------------------------------------
// Overlay + hotkeys
// ---------------------------------------------------------------------------
const overlay = new OverlayManager({
  electronDir: paths.electronDir,
  preload: PRELOAD,
  load: loadRenderer,
  devTools: !app.isPackaged,
});

function isWindowSource(v: unknown): v is WindowSource {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  return typeof o.id === "string" && typeof o.title === "string" && typeof o.process === "string" && o.is_self !== true;
}

/** Hotkey: ask the core for the foreground window first, then show the overlay. */
async function onOverlayHotkey(): Promise<void> {
  const at = performance.now();
  const event: HotkeyEvent = { kind: "overlay", at };
  sendToAll(IPC.hotkeyEvent, event);
  if (overlay.isVisible()) {
    overlay.hide();
    return;
  }
  const hint = await fetchForegroundHint();
  overlay.show(hint, at);
}

async function fetchForegroundHint(): Promise<WindowSource | null> {
  const t = performance.now();
  try {
    const res = await core.apiRequest("POST", "/api/live/foreground", { timeoutMs: FOREGROUND_TIMEOUT_MS });
    recordSample("overlay_foreground_ms", performance.now() - t);
    if (!res) {
      log("foreground: core not ready");
      return null;
    }
    if (!res.ok) {
      log(`foreground: HTTP ${res.status}`);
      return null;
    }
    const body = (await res.json()) as { source?: unknown };
    return isWindowSource(body.source) ? body.source : null;
  } catch (e) {
    recordSample("overlay_foreground_ms", performance.now() - t);
    log(`foreground: failed (${e instanceof Error ? e.name : "error"})`);
    return null;
  }
}

/** Hotkey / tray: stop everything, without waiting for anything slow. */
function onStopHotkey(source: "hotkey" | "tray"): void {
  const at = performance.now();
  const event: HotkeyEvent = { kind: "stop", at };
  sendToAll(IPC.hotkeyEvent, event);
  const ids = new Set<string>();
  for (const list of activeConversations.values()) for (const id of list) ids.add(id);
  log(`stop (${source}): agent/stop + ${ids.size} conversation stop(s)`);
  void core
    .apiRequest("POST", "/api/agent/stop", { timeoutMs: STOP_TIMEOUT_MS })
    .then((res) => log(`stop: agent/stop -> ${res ? `HTTP ${res.status}` : "core not ready"}`))
    .catch((e) => log(`stop: agent/stop failed (${e instanceof Error ? e.name : "error"})`));
  for (const id of ids) {
    void core
      .apiRequest("POST", `/api/conversations/${encodeURIComponent(id)}/stop`, { timeoutMs: STOP_TIMEOUT_MS })
      .then((res) => log(`stop: conversation stop -> ${res ? `HTTP ${res.status}` : "core not ready"}`))
      .catch((e) => log(`stop: conversation stop failed (${e instanceof Error ? e.name : "error"})`));
  }
}

const hotkeys = new HotkeyManager({
  overlay: () => void onOverlayHotkey(),
  stop: () => onStopHotkey("hotkey"),
});

function registerHotkeys(cfg: HotkeyConfig): HotkeyResult {
  return hotkeys.register(cfg);
}

/** Reads hotkey_* / tray_mode from GET /api/settings once the core is ready. */
async function applySettingsFromCore(): Promise<void> {
  let cfg: HotkeyConfig = { ...DEFAULT_HOTKEYS };
  try {
    const res = await core.apiRequest("GET", "/api/settings", { timeoutMs: 5000 });
    if (res?.ok) {
      const s = (await res.json()) as Record<string, unknown>;
      if (typeof s.hotkey_overlay === "string" && validateAccelerator(s.hotkey_overlay).ok) cfg.overlay = s.hotkey_overlay;
      if (typeof s.hotkey_stop === "string" && validateAccelerator(s.hotkey_stop).ok) cfg.stop = s.hotkey_stop;
      if (typeof s.tray_mode === "boolean") trayMode = s.tray_mode;
    } else {
      log(`settings: ${res ? `HTTP ${res.status}` : "core not ready"} - using default hotkeys`);
    }
  } catch (e) {
    log(`settings: failed (${e instanceof Error ? e.name : "error"}) - using default hotkeys`);
    cfg = { ...DEFAULT_HOTKEYS };
  }
  const result = registerHotkeys(cfg);
  for (const err of result.errors) log(`hotkey problem: ${err}`);
  log(`tray mode: ${trayMode}`);
}

// ---------------------------------------------------------------------------
// Quit orchestration: shutdown the core first, only then close the window.
// ---------------------------------------------------------------------------
function performQuit(reason: string): Promise<void> {
  if (quitPromise) return quitPromise;
  log(`quit requested (${reason})`);
  quitPromise = (async () => {
    if (win && !win.isDestroyed()) saveWindowState(paths.electronDir, win);
    hotkeys.unregisterAll();
    overlay.destroy();
    if (tray) {
      tray.destroy();
      tray = null;
    }
    const guard = new Promise<void>((resolve) => setTimeout(resolve, 40000));
    await Promise.race([core.shutdown(), guard]);
    quitting = true;
    if (win && !win.isDestroyed()) win.destroy();
    win = null;
    app.quit();
  })();
  return quitPromise;
}

app.on("before-quit", (event) => {
  if (!quitting) {
    event.preventDefault();
    void performQuit("before-quit");
  }
});

app.on("window-all-closed", () => {
  if (quitting) app.quit();
  else void performQuit("window-all-closed");
});

app.on("second-instance", () => showMainWindow());

function showMainWindow(): void {
  if (!win || win.isDestroyed()) return;
  if (!win.isVisible()) win.show();
  if (win.isMinimized()) win.restore();
  win.focus();
}

// ---------------------------------------------------------------------------
// IPC (exactly the channels used by the preload bridge)
// ---------------------------------------------------------------------------
ipcMain.handle(IPC.getBackend, () => core.getBackend());
ipcMain.handle(IPC.getBackendStatus, () => core.getStatus());
ipcMain.handle(IPC.getAppInfo, (): AppInfo => ({
  version: app.getVersion(),
  appRoot: paths.appRoot,
  dataDir: paths.dataDir,
  platform: process.platform,
}));
ipcMain.handle(IPC.pickModelFile, async () => {
  if (!win || win.isDestroyed()) return null;
  const result = await dialog.showOpenDialog(win, {
    title: "GGUF-Modell auswählen",
    properties: ["openFile"],
    filters: [{ name: "GGUF-Modelle", extensions: ["gguf"] }],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});
ipcMain.handle(IPC.openExternal, async (_event, url: unknown) => {
  if (typeof url !== "string" || !isAllowedExternalUrl(url)) {
    log(`openExternal denied: ${String(url).slice(0, 200)}`);
    return false;
  }
  try {
    await shell.openExternal(url);
    return true;
  } catch (e) {
    log(`openExternal failed: ${e instanceof Error ? e.message : String(e)}`);
    return false;
  }
});
ipcMain.handle(IPC.requestQuit, async () => {
  await performQuit("renderer");
});

// ----- v1.2 ------------------------------------------------------------------
ipcMain.handle(IPC.overlayShow, (_event, opts: unknown) => {
  const o = (opts ?? {}) as OverlayShowOptions;
  overlay.show(isWindowSource(o.sourceHint) ? o.sourceHint : null, null);
});
ipcMain.handle(IPC.overlayHide, () => overlay.hide());
ipcMain.handle(IPC.overlayToggle, async () => {
  if (overlay.isVisible()) overlay.hide();
  else overlay.show(await fetchForegroundHint(), null);
});
ipcMain.handle(IPC.getOverlayState, () => overlay.getState());
ipcMain.on(IPC.overlayPainted, (_event, showId: unknown) => {
  if (typeof showId === "number") overlay.onPainted(showId);
});
ipcMain.handle(IPC.pickFolder, async (event) => {
  const parent = BrowserWindow.fromWebContents(event.sender) ?? win ?? undefined;
  const result = await dialog.showOpenDialog(parent as BrowserWindow, {
    title: "Ordner auswählen",
    properties: ["openDirectory"],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});
ipcMain.handle(IPC.pickFiles, async (event) => {
  const parent = BrowserWindow.fromWebContents(event.sender) ?? win ?? undefined;
  const result = await dialog.showOpenDialog(parent as BrowserWindow, {
    title: "Dateien auswählen",
    properties: ["openFile", "multiSelections"],
  });
  return result.canceled ? [] : result.filePaths;
});
ipcMain.handle(IPC.registerHotkeys, (_event, cfg: unknown): HotkeyResult => {
  const c = (cfg ?? {}) as Partial<HotkeyConfig>;
  return registerHotkeys({ overlay: String(c.overlay ?? ""), stop: String(c.stop ?? "") });
});
ipcMain.handle(IPC.setTrayMode, (_event, enabled: unknown) => {
  trayMode = enabled === true;
  log(`tray mode: ${trayMode}`);
});
ipcMain.handle(IPC.getPerfSamples, () => getPerfSamples());
ipcMain.on(IPC.activeConversations, (event, ids: unknown) => {
  const list = Array.isArray(ids) ? ids.filter((x): x is string => typeof x === "string").slice(0, 20) : [];
  if (list.length === 0) activeConversations.delete(event.sender.id);
  else activeConversations.set(event.sender.id, list);
});
ipcMain.handle(IPC.focusMain, (_event, conversationId: unknown) => {
  showMainWindow();
  if (typeof conversationId === "string" && win && !win.isDestroyed()) {
    win.webContents.send(IPC.openConversationEvent, conversationId);
  }
});
ipcMain.handle(IPC.requestTaskForm, (_event, prefill: unknown) => {
  const p = (prefill ?? {}) as Partial<TaskFormPrefill>;
  const safe: TaskFormPrefill = {
    goal: typeof p.goal === "string" ? p.goal.slice(0, 4000) : "",
    source_id: typeof p.source_id === "string" ? p.source_id : null,
    process: typeof p.process === "string" ? p.process : null,
    live_session_id: typeof p.live_session_id === "string" ? p.live_session_id : null,
    conversation_id: typeof p.conversation_id === "string" ? p.conversation_id : null,
  };
  showMainWindow();
  if (win && !win.isDestroyed()) win.webContents.send(IPC.openTaskFormEvent, safe);
});

core.onStatus((status: BackendStatus) => {
  sendToAll(IPC.backendStatusEvent, status);
  if (status.state === "ready") void applySettingsFromCore();
});

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------
function createWindow(): BrowserWindow {
  const state = loadWindowState(paths.electronDir);
  const browserWindow = new BrowserWindow({
    width: state.width,
    height: state.height,
    x: state.x,
    y: state.y,
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
    title: "SpAIder Foldable",
    backgroundColor: "#0A0A0F",
    show: false,
    autoHideMenuBar: true,
    // Packaged builds take the icon from the exe resource (rcedit).
    ...(!app.isPackaged && fs.existsSync(ICON_PNG) ? { icon: ICON_PNG } : {}),
    webPreferences: {
      preload: PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
      devTools: !app.isPackaged,
    },
  });
  if (state.maximized) browserWindow.maximize();

  // No remote content, ever.
  browserWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  browserWindow.webContents.on("will-navigate", (event, url) => {
    if (DEV_SERVER_URL && url.startsWith(DEV_SERVER_URL)) return;
    event.preventDefault();
    log(`navigation blocked: ${url.slice(0, 200)}`);
  });
  browserWindow.webContents.on("will-attach-webview", (event) => event.preventDefault());

  if (!app.isPackaged) {
    browserWindow.webContents.on("before-input-event", (_event, input) => {
      if (input.type === "keyDown" && input.key === "F12") {
        browserWindow.webContents.toggleDevTools();
      }
    });
  }

  browserWindow.once("ready-to-show", () => browserWindow.show());
  browserWindow.on("close", (event) => {
    if (quitting) return;
    event.preventDefault();
    if (trayMode) {
      saveWindowState(paths.electronDir, browserWindow);
      browserWindow.hide();
      log("main window hidden to tray");
      return;
    }
    void performQuit("window-close");
  });
  browserWindow.on("closed", () => {
    win = null;
  });
  trackWindowState(paths.electronDir, browserWindow);
  loadRenderer(browserWindow, "");
  return browserWindow;
}

async function takeScreenshotAndQuit(target: string): Promise<void> {
  try {
    if (!win || win.isDestroyed()) return;
    const image = await win.webContents.capturePage();
    fs.mkdirSync(path.dirname(path.resolve(target)), { recursive: true });
    fs.writeFileSync(target, image.toPNG());
    log(`screenshot written to ${target}`);
  } catch (e) {
    log(`screenshot failed: ${e instanceof Error ? e.message : String(e)}`);
  } finally {
    void performQuit("screenshot");
  }
}

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------
void app.whenReady().then(() => {
  // Deny every permission request except sanitized clipboard writes
  // (needed for the "Kopieren" buttons).
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(permission === "clipboard-sanitized-write");
  });
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => {
    return permission === "clipboard-sanitized-write" || permission === "clipboard-read";
  });

  win = createWindow();
  // The overlay is created hidden right away so a hotkey only has to show it.
  overlay.ensure();
  tray = createTray(ICON_PNG, {
    onOpen: showMainWindow,
    onOverlay: () => void (async () => overlay.show(await fetchForegroundHint(), null))(),
    onStop: () => onStopHotkey("tray"),
    onQuit: () => void performQuit("tray"),
  });
  void core.start();

  if (SCREENSHOT_PATH) {
    // Verification mode: capture once the core reports ready (or after 45 s at
    // the latest), with a short settle time so the first screen has rendered.
    win.webContents.once("did-finish-load", () => {
      const startedAt = Date.now();
      const poll = setInterval(() => {
        const ready = core.currentStatus().state === "ready";
        const timedOut = Date.now() - startedAt > 45_000;
        if (ready || timedOut) {
          clearInterval(poll);
          setTimeout(() => void takeScreenshotAndQuit(SCREENSHOT_PATH), ready ? 1500 : 0);
        }
      }, 250);
    });
  }
});

app.on("will-quit", () => hotkeys.unregisterAll());

process.on("uncaughtException", (err) => {
  log(`uncaught exception: ${err.stack ?? err.message}`);
});
