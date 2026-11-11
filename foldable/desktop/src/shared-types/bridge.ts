/**
 * Types shared between the Electron main process, the preload script and the
 * renderer. Mirrors the `window.spaider` bridge from
 * docs/portable/API_CONTRACT.md ("Electron preload bridge", v1 + Addendum v1.2).
 *
 * Only types and constants live here. The sandboxed preload imports this file
 * with `import type` because it cannot require local modules at runtime.
 */

export type BackendState = "starting" | "ready" | "error" | "stopping";

export interface BackendStatus {
  state: BackendState;
  message?: string;
}

export interface BackendInfo {
  baseUrl: string;
  token: string;
}

export interface AppInfo {
  version: string;
  appRoot: string;
  dataDir: string;
  platform: string;
}

// ----- v1.2: live sources, overlay, hotkeys ------------------------------------

/** `WindowSource` of the core (GET /api/live/sources, POST /api/live/foreground). */
export interface WindowSource {
  id: string;
  hwnd: number;
  pid: number;
  process: string;
  exe: string | null;
  title: string;
  class: string;
  rect: { x: number; y: number; w: number; h: number };
  monitor_id: string;
  minimized: boolean;
  is_self: boolean;
}

export type HotkeyKind = "overlay" | "stop";

export interface HotkeyEvent {
  kind: HotkeyKind;
  /** performance.now() of the main process at the global shortcut callback. */
  at: number;
}

export interface OverlayShowOptions {
  sourceHint?: WindowSource | null;
}

export interface OverlayState {
  visible: boolean;
  sourceHint: WindowSource | null;
  shownAt: number | null;
}

/** Sent to the overlay renderer every time the window is shown. */
export interface OverlayShownEvent {
  sourceHint: WindowSource | null;
  /** performance.now() (main) of the show call. */
  shownAt: number;
  /** performance.now() (main) of the hotkey, null when shown from the UI/tray. */
  hotkeyAt: number | null;
  /** Monotonic counter so the renderer reports the paint of exactly this show. */
  showId: number;
}

export interface HotkeyConfig {
  overlay: string;
  stop: string;
}

export interface HotkeyResult {
  overlay: boolean;
  stop: boolean;
  /** German messages for every failed registration (conflict / invalid). */
  errors: string[];
}

/** One measured latency sample of the main process (never synthetic). */
export interface PerfSample {
  name: string;
  ms: number;
  /** Date.now() when the sample was recorded. */
  at: number;
}

/** Prefill for the task form ("Als Aufgabe ausführen" from the overlay). */
export interface TaskFormPrefill {
  goal: string;
  source_id: string | null;
  process: string | null;
  live_session_id: string | null;
  conversation_id: string | null;
}

export interface SpaiderBridge {
  /** Resolves once the core is ready; rejects if the core failed to start. */
  getBackend(): Promise<BackendInfo>;
  onBackendStatus(cb: (s: BackendStatus) => void): () => void;
  getAppInfo(): Promise<AppInfo>;
  /** Native open dialog filtered to *.gguf, returns absolute path or null. */
  pickModelFile(): Promise<string | null>;
  /** Only https://huggingface.co/* and https://github.com/ggml-org/llama.cpp* are allowed. */
  openExternal(url: string): Promise<boolean>;
  /** Graceful shutdown of the core, then the window closes. */
  requestQuit(): Promise<void>;

  // ----- v1.2 -----------------------------------------------------------------
  onHotkey(cb: (e: HotkeyEvent) => void): () => void;
  overlay: {
    show(opts?: OverlayShowOptions): Promise<void>;
    hide(): Promise<void>;
    toggle(): Promise<void>;
  };
  getOverlayState(): Promise<OverlayState>;
  pickFolder(): Promise<string | null>;
  pickFiles(): Promise<string[]>;
  registerHotkeys(cfg: HotkeyConfig): Promise<HotkeyResult>;
  setTrayMode(enabled: boolean): Promise<void>;
  /** Last 20 measured main-process samples (overlay hotkey -> show / painted). */
  getPerfSamples(): Promise<PerfSample[]>;

  // ----- shell-internal helpers (not part of the HTTP contract) -------------------
  /** Overlay renderer: fired on every show with the source hint of this show. */
  onOverlayShown(cb: (e: OverlayShownEvent) => void): () => void;
  /** Overlay renderer: first paint after a show (two animation frames later). */
  reportOverlayPainted(showId: number): void;
  /** Overlay renderer: the window was hidden (Esc, toggle hotkey, tray, close) - the live session ends. */
  onOverlayHidden(cb: () => void): () => void;
  /** Renderers report the conversations whose answer is currently streaming (for the stop hotkey). */
  reportActiveConversations(ids: string[]): void;
  /** Overlay: focus the main window on a conversation ("Im Hauptfenster öffnen"). */
  focusMain(conversationId: string | null): Promise<void>;
  /** Overlay: open the task form in the main window with a prefill ("Als Aufgabe ausführen"). */
  requestTaskForm(prefill: TaskFormPrefill): Promise<void>;
  /** Main renderer: a conversation should be selected (from the overlay). */
  onOpenConversation(cb: (conversationId: string) => void): () => void;
  /** Main renderer: the task form should open with this prefill. */
  onOpenTaskForm(cb: (prefill: TaskFormPrefill) => void): () => void;
}

/** Fixed IPC channel names (main <-> preload). */
export const IPC = {
  getBackend: "spaider:get-backend",
  getBackendStatus: "spaider:get-backend-status",
  backendStatusEvent: "spaider:backend-status",
  getAppInfo: "spaider:get-app-info",
  pickModelFile: "spaider:pick-model-file",
  openExternal: "spaider:open-external",
  requestQuit: "spaider:request-quit",
  // v1.2
  hotkeyEvent: "spaider:hotkey",
  overlayShow: "spaider:overlay-show",
  overlayHide: "spaider:overlay-hide",
  overlayToggle: "spaider:overlay-toggle",
  getOverlayState: "spaider:get-overlay-state",
  overlayShownEvent: "spaider:overlay-shown",
  overlayPainted: "spaider:overlay-painted",
  overlayHiddenEvent: "spaider:overlay-hidden",
  pickFolder: "spaider:pick-folder",
  pickFiles: "spaider:pick-files",
  registerHotkeys: "spaider:register-hotkeys",
  setTrayMode: "spaider:set-tray-mode",
  getPerfSamples: "spaider:get-perf-samples",
  activeConversations: "spaider:active-conversations",
  focusMain: "spaider:focus-main",
  requestTaskForm: "spaider:request-task-form",
  openConversationEvent: "spaider:open-conversation",
  openTaskFormEvent: "spaider:open-task-form",
} as const;

/** URL prefixes the renderer may open in the system browser. */
export const OPEN_EXTERNAL_ALLOWLIST = [
  "https://huggingface.co/",
  "https://github.com/ggml-org/llama.cpp",
  // 0.4: provider key pages and documentation linked from the presets (no other hosts)
  "https://platform.openai.com/",
  "https://developers.openai.com/",
  "https://console.anthropic.com/",
  "https://platform.claude.com/",
  "https://aistudio.google.com/",
  "https://platform.deepseek.com/",
  "https://api-docs.deepseek.com/",
  "https://z.ai/",
  "https://docs.z.ai/",
  "https://console.mistral.ai/",
  "https://docs.mistral.ai/",
  "https://openrouter.ai/",
  "https://docs.ollama.com/",
  "https://lmstudio.ai/",
  "https://github.com/openai/tunnel-client",
] as const;

export function isAllowedExternalUrl(url: string): boolean {
  return OPEN_EXTERNAL_ALLOWLIST.some((prefix) => url.startsWith(prefix));
}

export const DEFAULT_HOTKEYS: HotkeyConfig = { overlay: "Ctrl+Alt+Space", stop: "Ctrl+Alt+Backspace" };
