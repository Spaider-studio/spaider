/**
 * Pure state of the overlay's target selection (API_CONTRACT.md v1.2,
 * "Quellen und Live-Sitzungen") plus the German texts for snapshot quality.
 *
 * Rules enforced here:
 * - a session is never started without a confirmed, visible source;
 * - the first time, the picker must be confirmed explicitly;
 * - later shows preselect the foreground hint, which one click / Enter
 *   confirms (`hintPending`), a silent switch never happens;
 * - SpAIder's own windows (`is_self`) are never offered.
 */
import {
  isWindowSource,
  type LiveSessionState,
  type LiveSource,
  type LiveSources,
  type ScreenSource,
  type SnapshotQuality,
  type WindowSource,
} from "../api/types";

export interface OverlaySourceState {
  /** The source the user confirmed (what a session targets). */
  confirmed: LiveSource | null;
  /** Foreground hint of the latest show (preselected, highlighted in the picker). */
  hint: WindowSource | null;
  /** The hint differs from the confirmed source and still needs one click / Enter. */
  hintPending: boolean;
  pickerOpen: boolean;
  /** A source was confirmed at least once in this overlay session. */
  everConfirmed: boolean;
}

export function initialSourceState(): OverlaySourceState {
  return { confirmed: null, hint: null, hintPending: false, pickerOpen: false, everConfirmed: false };
}

function usableHint(hint: WindowSource | null | undefined): WindowSource | null {
  if (!hint || hint.is_self) return null;
  return hint;
}

/** The overlay was shown with a foreground hint (may be null). */
export function applyShown(state: OverlaySourceState, rawHint: WindowSource | null): OverlaySourceState {
  const hint = usableHint(rawHint);
  if (!state.everConfirmed) {
    // First time: the picker must be confirmed, the hint is only highlighted.
    return { ...state, hint, hintPending: false, pickerOpen: true };
  }
  if (!hint) return { ...state, hint: null, hintPending: false };
  if (state.confirmed && state.confirmed.id === hint.id) {
    // Same window as before: refresh title/rect, nothing to confirm.
    return { ...state, confirmed: hint, hint, hintPending: false };
  }
  return { ...state, hint, hintPending: true };
}

/** User confirmed a source (picker click, "Bestätigen" or Enter on a pending hint). */
export function confirmSource(state: OverlaySourceState, source: LiveSource): OverlaySourceState {
  if (isWindowSource(source) && source.is_self) return state;
  return { ...state, confirmed: source, hintPending: false, pickerOpen: false, everConfirmed: true, hint: isWindowSource(source) ? source : null };
}

export function openPicker(state: OverlaySourceState): OverlaySourceState {
  return { ...state, pickerOpen: true };
}

export function closePicker(state: OverlaySourceState): OverlaySourceState {
  return { ...state, pickerOpen: false };
}

/** Source the next question would target: the pending hint or the confirmed source. */
export function effectiveTarget(state: OverlaySourceState): LiveSource | null {
  if (state.hintPending && state.hint) return state.hint;
  return state.confirmed;
}

/** The id the picker should highlight. */
export function preselectedId(state: OverlaySourceState): string | null {
  return state.hint?.id ?? state.confirmed?.id ?? null;
}

/** Windows and screens the picker may offer (never SpAIder itself). */
export function selectableSources(sources: LiveSources | null): { windows: WindowSource[]; screens: ScreenSource[] } {
  if (!sources) return { windows: [], screens: [] };
  return { windows: sources.windows.filter((w) => !w.is_self), screens: sources.screens };
}

export function sourceTitle(source: LiveSource | null): string {
  if (!source) return "Keine Quelle gewählt";
  if (isWindowSource(source)) return source.title || source.process;
  return source.name;
}

export function sourceProcess(source: LiveSource | null): string | null {
  if (!source) return null;
  return isWindowSource(source) ? source.process : `Bildschirm${source.primary ? " (primär)" : ""}`;
}

/** "Ziel: <Titel> – <Prozess>" */
export function targetLine(source: LiveSource | null): string {
  if (!source) return "Ziel: nicht gewählt";
  const proc = sourceProcess(source);
  return `Ziel: ${sourceTitle(source)}${proc ? ` – ${proc}` : ""}`;
}

export const QUALITY_TEXT: Record<SnapshotQuality, string | null> = {
  ok: null,
  black: "Bild ist schwarz (geschützter Inhalt oder Vollbild?)",
  minimized: "Fenster ist minimiert",
  occluded: "Fenster ist teilweise verdeckt",
  lost: "Quelle verloren – Fenster nicht mehr vorhanden",
  error: "Aufnahme fehlgeschlagen",
};

/** Short German text for a non-ok quality, null when everything is fine. */
export function qualityText(quality: SnapshotQuality | null | undefined): string | null {
  if (!quality) return null;
  return QUALITY_TEXT[quality] ?? null;
}

/** Qualities that make a new source selection sensible. */
export function needsNewSource(quality: SnapshotQuality | null | undefined, sessionState: LiveSessionState | null | undefined): boolean {
  if (sessionState === "lost" || sessionState === "ended") return true;
  return quality === "lost" || quality === "minimized" || quality === "black" || quality === "error";
}

export function sessionStateText(state: LiveSessionState | null | undefined): string | null {
  if (state === "lost") return "Quelle verloren – bitte neu wählen";
  if (state === "ended") return "Sitzung beendet";
  return null;
}
