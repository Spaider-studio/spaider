/**
 * Global hotkeys (Electron globalShortcut). Registration failures (invalid
 * accelerator, combination already taken by another application or by the
 * other hotkey) are reported in `HotkeyResult.errors`, never swallowed.
 */
import { globalShortcut } from "electron";
import { sameAccelerator, validateAccelerator } from "../shared-types/accelerator";
import type { HotkeyConfig, HotkeyKind, HotkeyResult } from "../shared-types/bridge";
import { log } from "./log";

export type HotkeyHandlers = Record<HotkeyKind, () => void>;

const LABEL: Record<HotkeyKind, string> = { overlay: "Live-Fenster", stop: "Stopp" };

export class HotkeyManager {
  private readonly active: Partial<Record<HotkeyKind, string>> = {};

  constructor(private readonly handlers: HotkeyHandlers) {}

  /** Replaces the current registrations with `cfg`; returns what actually worked. */
  register(cfg: HotkeyConfig): HotkeyResult {
    this.unregisterAll();
    const result: HotkeyResult = { overlay: false, stop: false, errors: [] };
    const kinds: HotkeyKind[] = ["overlay", "stop"];

    if (sameAccelerator(cfg.overlay, cfg.stop)) {
      result.errors.push("Beide Hotkeys sind identisch – bitte unterschiedliche Kombinationen wählen.");
    }

    for (const kind of kinds) {
      const raw = cfg[kind];
      const check = validateAccelerator(raw);
      if (!check.ok) {
        result.errors.push(`${LABEL[kind]}: ${check.error}`);
        continue;
      }
      if (kind === "stop" && result.overlay && sameAccelerator(cfg.overlay, raw)) continue;
      let ok: boolean;
      try {
        ok = globalShortcut.register(check.normalized, () => this.handlers[kind]());
      } catch (e) {
        result.errors.push(`${LABEL[kind]}: ${check.normalized} wurde abgelehnt (${e instanceof Error ? e.message : String(e)}).`);
        continue;
      }
      // register() can return true without the shortcut being usable; verify.
      if (ok && !globalShortcut.isRegistered(check.normalized)) ok = false;
      if (!ok) {
        result.errors.push(
          `${LABEL[kind]}: ${check.normalized} konnte nicht registriert werden – vermutlich belegt eine andere Anwendung diese Kombination.`,
        );
        continue;
      }
      this.active[kind] = check.normalized;
      result[kind] = true;
    }
    log(`hotkeys registered: overlay=${result.overlay ? this.active.overlay : "-"} stop=${result.stop ? this.active.stop : "-"} errors=${result.errors.length}`);
    return result;
  }

  current(): Partial<Record<HotkeyKind, string>> {
    return { ...this.active };
  }

  unregisterAll(): void {
    for (const kind of Object.keys(this.active) as HotkeyKind[]) {
      const acc = this.active[kind];
      if (acc) {
        try {
          globalShortcut.unregister(acc);
        } catch {
          /* ignore */
        }
      }
      delete this.active[kind];
    }
  }
}
