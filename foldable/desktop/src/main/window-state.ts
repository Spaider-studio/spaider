/**
 * Remembers the window bounds in <dataDir>/electron/window.json.
 */
import { screen, type BrowserWindow, type Rectangle } from "electron";
import fs from "node:fs";
import path from "node:path";

export interface WindowState {
  x?: number;
  y?: number;
  width: number;
  height: number;
  maximized?: boolean;
}

export const DEFAULT_WINDOW: WindowState = { width: 1280, height: 800 };
export const MIN_WIDTH = 960;
export const MIN_HEIGHT = 600;

function stateFile(electronDir: string): string {
  return path.join(electronDir, "window.json");
}

export function loadWindowState(electronDir: string): WindowState {
  try {
    const raw = JSON.parse(fs.readFileSync(stateFile(electronDir), "utf8")) as Partial<WindowState>;
    const width = Math.max(MIN_WIDTH, Number(raw.width) || DEFAULT_WINDOW.width);
    const height = Math.max(MIN_HEIGHT, Number(raw.height) || DEFAULT_WINDOW.height);
    const state: WindowState = { width, height, maximized: raw.maximized === true };
    if (typeof raw.x === "number" && typeof raw.y === "number") {
      // Only restore the position if it is still (mostly) on a connected display.
      const rect: Rectangle = { x: raw.x, y: raw.y, width, height };
      const display = screen.getDisplayMatching(rect);
      const area = display.workArea;
      const visible =
        raw.x + width > area.x + 40 &&
        raw.x < area.x + area.width - 40 &&
        raw.y >= area.y - 10 &&
        raw.y < area.y + area.height - 40;
      if (visible) {
        state.x = raw.x;
        state.y = raw.y;
      }
    }
    return state;
  } catch {
    return { ...DEFAULT_WINDOW };
  }
}

export function saveWindowState(electronDir: string, win: BrowserWindow): void {
  try {
    if (win.isDestroyed()) return;
    const maximized = win.isMaximized();
    const bounds = maximized ? win.getNormalBounds() : win.getBounds();
    const state: WindowState = { ...bounds, maximized };
    fs.mkdirSync(electronDir, { recursive: true });
    fs.writeFileSync(stateFile(electronDir), JSON.stringify(state, null, 2));
  } catch {
    /* ignore */
  }
}

/** Attaches debounced persistence to resize/move events. */
export function trackWindowState(electronDir: string, win: BrowserWindow): void {
  let timer: NodeJS.Timeout | null = null;
  const schedule = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => saveWindowState(electronDir, win), 400);
  };
  win.on("resize", schedule);
  win.on("move", schedule);
  win.on("maximize", schedule);
  win.on("unmaximize", schedule);
}
