/**
 * Live overlay window: a small frameless, always-on-top BrowserWindow that
 * loads the same renderer bundle with the hash route `#/overlay`.
 *
 * - created hidden at startup so a hotkey only has to show it,
 * - `setContentProtection(true)`: never part of screen captures,
 * - skipTaskbar, not minimizable; Alt+F4 / close just hides it,
 * - remembers its bounds per monitor in <dataDir>/electron/overlay.json,
 * - measures hotkey -> 'show' event -> first paint (reported by the renderer).
 */
import { BrowserWindow, screen, type Rectangle } from "electron";
import fs from "node:fs";
import path from "node:path";
import { IPC, type OverlayShownEvent, type OverlayState, type WindowSource } from "../shared-types/bridge";
import { log } from "./log";
import { recordSample } from "./perf";

export const OVERLAY_WIDTH = 520;
export const OVERLAY_HEIGHT = 360;
export const OVERLAY_MIN_WIDTH = 360;
export const OVERLAY_MIN_HEIGHT = 240;
const MARGIN = 24;

type BoundsByDisplay = Record<string, Rectangle>;

function clampToArea(bounds: Rectangle, area: Rectangle): Rectangle {
  const width = Math.max(OVERLAY_MIN_WIDTH, Math.min(bounds.width, area.width));
  const height = Math.max(OVERLAY_MIN_HEIGHT, Math.min(bounds.height, area.height));
  const x = Math.min(Math.max(bounds.x, area.x), area.x + area.width - width);
  const y = Math.min(Math.max(bounds.y, area.y), area.y + area.height - height);
  return { x, y, width, height };
}

function defaultBounds(area: Rectangle): Rectangle {
  return clampToArea(
    {
      x: area.x + area.width - OVERLAY_WIDTH - MARGIN,
      y: area.y + area.height - OVERLAY_HEIGHT - MARGIN,
      width: OVERLAY_WIDTH,
      height: OVERLAY_HEIGHT,
    },
    area,
  );
}

export interface OverlayOptions {
  electronDir: string;
  preload: string;
  /** Loads the renderer into the window (dev server or dist) with the given hash route. */
  load: (win: BrowserWindow, hash: string) => void;
  devTools: boolean;
  /** Called with every event the overlay renderer should receive right after a show. */
  onShown?: (e: OverlayShownEvent) => void;
}

export class OverlayManager {
  private win: BrowserWindow | null = null;
  private sourceHint: WindowSource | null = null;
  private shownAt: number | null = null;
  private showId = 0;
  private pending: { showId: number; hotkeyAt: number | null; shownAt: number } | null = null;
  private saveTimer: NodeJS.Timeout | null = null;
  private destroyed = false;

  constructor(private readonly opts: OverlayOptions) {}

  private get stateFile(): string {
    return path.join(this.opts.electronDir, "overlay.json");
  }

  private loadBounds(): BoundsByDisplay {
    try {
      const raw = JSON.parse(fs.readFileSync(this.stateFile, "utf8")) as BoundsByDisplay;
      return raw && typeof raw === "object" ? raw : {};
    } catch {
      return {};
    }
  }

  private saveBounds(): void {
    const win = this.win;
    if (!win || win.isDestroyed() || !win.isVisible()) return;
    try {
      const bounds = win.getBounds();
      const display = screen.getDisplayMatching(bounds);
      const all = this.loadBounds();
      all[String(display.id)] = bounds;
      fs.mkdirSync(this.opts.electronDir, { recursive: true });
      fs.writeFileSync(this.stateFile, JSON.stringify(all, null, 2));
    } catch {
      /* ignore */
    }
  }

  private scheduleSave(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.saveBounds(), 400);
  }

  /** Creates the hidden window (idempotent). */
  ensure(): BrowserWindow {
    if (this.win && !this.win.isDestroyed()) return this.win;
    const win = new BrowserWindow({
      width: OVERLAY_WIDTH,
      height: OVERLAY_HEIGHT,
      minWidth: OVERLAY_MIN_WIDTH,
      minHeight: OVERLAY_MIN_HEIGHT,
      show: false,
      frame: false,
      alwaysOnTop: true,
      skipTaskbar: true,
      resizable: true,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      hasShadow: true,
      title: "SpAIder Live",
      backgroundColor: "#12121A",
      autoHideMenuBar: true,
      webPreferences: {
        preload: this.opts.preload,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        webSecurity: true,
        spellcheck: false,
        devTools: this.opts.devTools,
      },
    });
    win.setContentProtection(true);
    win.setMenuBarVisibility(false);
    // Keep "SpAIder Live" instead of the document title of the shared bundle.
    win.on("page-title-updated", (event) => event.preventDefault());
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    win.webContents.on("will-attach-webview", (event) => event.preventDefault());
    win.on("show", () => {
      const p = this.pending;
      if (p && p.hotkeyAt !== null) recordSample("overlay_hotkey_to_show_ms", performance.now() - p.hotkeyAt);
    });
    win.on("close", (event) => {
      if (!this.destroyed) {
        event.preventDefault();
        this.hide();
      }
    });
    win.on("move", () => this.scheduleSave());
    win.on("resize", () => this.scheduleSave());
    if (this.opts.devTools) {
      win.webContents.on("before-input-event", (_event, input) => {
        if (input.type === "keyDown" && input.key === "F12") win.webContents.toggleDevTools();
      });
    }
    this.opts.load(win, "/overlay");
    this.win = win;
    return win;
  }

  isVisible(): boolean {
    return Boolean(this.win && !this.win.isDestroyed() && this.win.isVisible());
  }

  webContentsId(): number | null {
    return this.win && !this.win.isDestroyed() ? this.win.webContents.id : null;
  }

  window(): BrowserWindow | null {
    return this.win && !this.win.isDestroyed() ? this.win : null;
  }

  /** Shows the overlay on the monitor of the hinted window (else the cursor's). */
  show(sourceHint: WindowSource | null, hotkeyAt: number | null): void {
    const win = this.ensure();
    this.sourceHint = sourceHint;
    const area = this.pickDisplay(sourceHint).workArea;
    const displayId = String(screen.getDisplayMatching(area).id);
    const saved = this.loadBounds()[displayId];
    const bounds = saved ? clampToArea(saved, area) : defaultBounds(area);
    if (!win.isVisible()) win.setBounds(bounds);
    this.showId += 1;
    const shownAt = performance.now();
    this.shownAt = shownAt;
    this.pending = { showId: this.showId, hotkeyAt, shownAt };
    const event: OverlayShownEvent = { sourceHint, shownAt, hotkeyAt, showId: this.showId };
    win.webContents.send(IPC.overlayShownEvent, event);
    this.opts.onShown?.(event);
    if (win.isVisible()) {
      win.focus();
      if (hotkeyAt !== null) recordSample("overlay_hotkey_to_show_ms", shownAt - hotkeyAt);
    } else {
      win.show();
    }
    log(`overlay shown (hint=${sourceHint ? "window" : "none"}, source=${hotkeyAt !== null ? "hotkey" : "ui"})`);
  }

  hide(): void {
    const win = this.win;
    if (!win || win.isDestroyed() || !win.isVisible()) return;
    this.saveBounds();
    win.hide();
    this.shownAt = null;
    log("overlay hidden");
  }

  toggle(sourceHint: WindowSource | null): void {
    if (this.isVisible()) this.hide();
    else this.show(sourceHint, null);
  }

  /** Renderer reports first paint (two animation frames after the show event). */
  onPainted(showId: number): void {
    const p = this.pending;
    if (!p || p.showId !== showId) return;
    this.pending = null;
    const now = performance.now();
    recordSample("overlay_show_to_painted_ms", now - p.shownAt);
    if (p.hotkeyAt !== null) recordSample("overlay_hotkey_to_painted_ms", now - p.hotkeyAt);
  }

  getState(): OverlayState {
    return { visible: this.isVisible(), sourceHint: this.sourceHint, shownAt: this.shownAt };
  }

  private pickDisplay(hint: WindowSource | null) {
    if (hint && hint.rect && !hint.minimized && hint.rect.w > 0 && hint.rect.h > 0) {
      return screen.getDisplayMatching({ x: hint.rect.x, y: hint.rect.y, width: hint.rect.w, height: hint.rect.h });
    }
    return screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
  }

  destroy(): void {
    this.destroyed = true;
    if (this.saveTimer) clearTimeout(this.saveTimer);
    if (this.win && !this.win.isDestroyed()) {
      if (this.win.isVisible()) this.saveBounds();
      this.win.destroy();
    }
    this.win = null;
  }
}
