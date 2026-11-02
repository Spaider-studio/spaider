/**
 * SpAIder Foldable - Electron main process.
 *
 * Security: contextIsolation, sandbox, no nodeIntegration, no remote content,
 * window.open denied, in-page navigation blocked, strict CSP in index.html.
 * Portability: every Electron path is redirected below <appRoot>/data before
 * app.whenReady(); the core is started/stopped by CoreManager.
 */
import { app, BrowserWindow, dialog, ipcMain, Menu, session, shell } from "electron";
import fs from "node:fs";
import path from "node:path";
import { IPC, isAllowedExternalUrl, type AppInfo, type BackendStatus } from "../shared-types/bridge";
import { CoreManager } from "./core";
import { initLauncherLog, log } from "./log";
import { applyPortablePaths, resolveAppPaths } from "./paths";
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
let quitting = false;
let quitPromise: Promise<void> | null = null;

const DEV_SERVER_URL = app.isPackaged ? undefined : process.env.VITE_DEV_SERVER_URL;
const SCREENSHOT_PATH = process.env.SPAIDER_FOLDABLE_SCREENSHOT;

// ---------------------------------------------------------------------------
// Quit orchestration: shutdown the core first, only then close the window.
// ---------------------------------------------------------------------------
function performQuit(reason: string): Promise<void> {
  if (quitPromise) return quitPromise;
  log(`quit requested (${reason})`);
  quitPromise = (async () => {
    if (win && !win.isDestroyed()) saveWindowState(paths.electronDir, win);
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

app.on("second-instance", () => {
  if (win && !win.isDestroyed()) {
    if (win.isMinimized()) win.restore();
    win.focus();
  }
});

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

core.onStatus((status: BackendStatus) => {
  if (win && !win.isDestroyed()) win.webContents.send(IPC.backendStatusEvent, status);
});

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------
function createWindow(): BrowserWindow {
  const state = loadWindowState(paths.electronDir);
  const iconPng = path.resolve(__dirname, "..", "..", "assets", "icon.png");
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
    ...(!app.isPackaged && fs.existsSync(iconPng) ? { icon: iconPng } : {}),
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "index.js"),
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
    if (!quitting) {
      event.preventDefault();
      void performQuit("window-close");
    }
  });
  browserWindow.on("closed", () => {
    win = null;
  });
  trackWindowState(paths.electronDir, browserWindow);

  if (DEV_SERVER_URL) {
    void browserWindow.loadURL(DEV_SERVER_URL);
    browserWindow.webContents.on("did-fail-load", () => {
      setTimeout(() => {
        if (!browserWindow.isDestroyed()) void browserWindow.loadURL(DEV_SERVER_URL);
      }, 700);
    });
  } else {
    void browserWindow.loadFile(path.join(__dirname, "..", "renderer", "index.html"));
  }
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

process.on("uncaughtException", (err) => {
  log(`uncaught exception: ${err.stack ?? err.message}`);
});
