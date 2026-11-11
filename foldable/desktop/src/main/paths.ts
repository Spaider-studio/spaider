/**
 * App-root / data-dir resolution for the portable layout.
 *
 * packaged:  <appRoot>/SpAIder.exe      -> appRoot = dirname(process.execPath)
 * dev:       --app-root=<dir> | SPAIDER_FOLDABLE_ROOT | <desktop>/dev-data
 * data dir:  <appRoot>/data
 */
import { app } from "electron";
import fs from "node:fs";
import path from "node:path";

export interface AppPaths {
  appRoot: string;
  dataDir: string;
  electronDir: string;
  logsDir: string;
  isPackaged: boolean;
}

function argValue(name: string): string | undefined {
  const prefix = `--${name}=`;
  for (let i = 0; i < process.argv.length; i++) {
    const a = process.argv[i];
    if (a.startsWith(prefix)) return a.slice(prefix.length);
    if (a === `--${name}` && i + 1 < process.argv.length) return process.argv[i + 1];
  }
  return undefined;
}

export function resolveAppPaths(): AppPaths {
  const isPackaged = app.isPackaged;
  let appRoot: string;
  if (isPackaged) {
    // Packaged: the directory of SpAIder.exe. SPAIDER_FOLDABLE_ROOT is an
    // explicit developer override (used to test the exe against the mock core).
    const override = process.env.SPAIDER_FOLDABLE_ROOT;
    appRoot = override ? path.resolve(override) : path.dirname(process.execPath);
  } else {
    const fromArg = argValue("app-root");
    const fromEnv = process.env.SPAIDER_FOLDABLE_ROOT;
    const desktopDir = path.resolve(__dirname, "..", "..");
    appRoot = path.resolve(fromArg ?? fromEnv ?? path.join(desktopDir, "dev-data"));
  }
  const dataDir = path.join(appRoot, "data");
  const electronDir = path.join(dataDir, "electron");
  const logsDir = path.join(dataDir, "logs");
  return { appRoot, dataDir, electronDir, logsDir, isPackaged };
}

/** Must run BEFORE app.whenReady(): redirects every Electron cache to the stick. */
export function applyPortablePaths(p: AppPaths): void {
  const sessionDir = path.join(p.electronDir, "session");
  const tempDir = path.join(p.electronDir, "temp");
  for (const d of [p.dataDir, p.electronDir, sessionDir, tempDir, p.logsDir]) {
    fs.mkdirSync(d, { recursive: true });
  }
  app.setPath("userData", p.electronDir);
  app.setPath("sessionData", sessionDir);
  app.setPath("temp", tempDir);
  app.setPath("logs", p.logsDir);
}
