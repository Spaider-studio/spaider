/**
 * Tray icon with a small menu (Öffnen, Live-Fenster, Stopp, Beenden).
 * No autostart entry is ever created; `tray_mode` only changes what closing
 * the main window does (hide instead of quit), see index.ts.
 */
import { Menu, nativeImage, Tray } from "electron";
import fs from "node:fs";
import { log } from "./log";

export interface TrayHandlers {
  onOpen: () => void;
  onOverlay: () => void;
  onStop: () => void;
  onQuit: () => void;
}

export function createTray(iconPath: string, handlers: TrayHandlers): Tray | null {
  try {
    let image = fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
    if (!image.isEmpty()) image = image.resize({ width: 16, height: 16 });
    const tray = new Tray(image);
    tray.setToolTip("SpAIder Foldable");
    const menu = Menu.buildFromTemplate([
      { label: "Öffnen", click: handlers.onOpen },
      { label: "Live-Fenster", click: handlers.onOverlay },
      { label: "Stopp", click: handlers.onStop },
      { type: "separator" },
      { label: "Beenden", click: handlers.onQuit },
    ]);
    tray.setContextMenu(menu);
    tray.on("click", handlers.onOpen);
    tray.on("double-click", handlers.onOpen);
    log("tray created");
    return tray;
  } catch (e) {
    log(`tray not available: ${e instanceof Error ? e.message : String(e)}`);
    return null;
  }
}
