/**
 * Preload script: exposes exactly the `window.spaider` bridge from the API
 * contract (v1 + Addendum v1.2) plus a few shell-internal helpers. Runs
 * sandboxed (contextIsolation: true, sandbox: true), so it may only require
 * `electron` and must stay a single file.
 */
import { contextBridge, ipcRenderer } from "electron";
import type {
  AppInfo,
  BackendInfo,
  BackendStatus,
  HotkeyConfig,
  HotkeyEvent,
  HotkeyResult,
  OverlayShowOptions,
  OverlayShownEvent,
  OverlayState,
  PerfSample,
  SpaiderBridge,
  TaskFormPrefill,
} from "../shared-types/bridge";

// Channel names are duplicated here on purpose: a sandboxed preload cannot
// require sibling modules at runtime, only type imports are allowed.
const CH = {
  getBackend: "spaider:get-backend",
  getBackendStatus: "spaider:get-backend-status",
  backendStatusEvent: "spaider:backend-status",
  getAppInfo: "spaider:get-app-info",
  pickModelFile: "spaider:pick-model-file",
  openExternal: "spaider:open-external",
  requestQuit: "spaider:request-quit",
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

function subscribe<T>(channel: string, cb: (payload: T) => void): () => void {
  const handler = (_event: Electron.IpcRendererEvent, payload: T) => cb(payload);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

const bridge: SpaiderBridge = {
  getBackend(): Promise<BackendInfo> {
    return ipcRenderer.invoke(CH.getBackend);
  },
  onBackendStatus(cb: (s: BackendStatus) => void): () => void {
    const handler = (_event: Electron.IpcRendererEvent, status: BackendStatus) => cb(status);
    ipcRenderer.on(CH.backendStatusEvent, handler);
    // Deliver the current state right away so late subscribers never miss
    // the initial transition.
    let active = true;
    void ipcRenderer.invoke(CH.getBackendStatus).then((status: BackendStatus) => {
      if (active && status) cb(status);
    });
    return () => {
      active = false;
      ipcRenderer.removeListener(CH.backendStatusEvent, handler);
    };
  },
  getAppInfo(): Promise<AppInfo> {
    return ipcRenderer.invoke(CH.getAppInfo);
  },
  pickModelFile(): Promise<string | null> {
    return ipcRenderer.invoke(CH.pickModelFile);
  },
  openExternal(url: string): Promise<boolean> {
    return ipcRenderer.invoke(CH.openExternal, String(url));
  },
  requestQuit(): Promise<void> {
    return ipcRenderer.invoke(CH.requestQuit);
  },

  // ----- v1.2 -----------------------------------------------------------------
  onHotkey(cb: (e: HotkeyEvent) => void): () => void {
    return subscribe<HotkeyEvent>(CH.hotkeyEvent, cb);
  },
  overlay: {
    show(opts?: OverlayShowOptions): Promise<void> {
      return ipcRenderer.invoke(CH.overlayShow, opts ?? {});
    },
    hide(): Promise<void> {
      return ipcRenderer.invoke(CH.overlayHide);
    },
    toggle(): Promise<void> {
      return ipcRenderer.invoke(CH.overlayToggle);
    },
  },
  getOverlayState(): Promise<OverlayState> {
    return ipcRenderer.invoke(CH.getOverlayState);
  },
  pickFolder(): Promise<string | null> {
    return ipcRenderer.invoke(CH.pickFolder);
  },
  pickFiles(): Promise<string[]> {
    return ipcRenderer.invoke(CH.pickFiles);
  },
  registerHotkeys(cfg: HotkeyConfig): Promise<HotkeyResult> {
    return ipcRenderer.invoke(CH.registerHotkeys, { overlay: String(cfg.overlay), stop: String(cfg.stop) });
  },
  setTrayMode(enabled: boolean): Promise<void> {
    return ipcRenderer.invoke(CH.setTrayMode, Boolean(enabled));
  },
  getPerfSamples(): Promise<PerfSample[]> {
    return ipcRenderer.invoke(CH.getPerfSamples);
  },

  // ----- shell-internal ---------------------------------------------------------
  onOverlayShown(cb: (e: OverlayShownEvent) => void): () => void {
    return subscribe<OverlayShownEvent>(CH.overlayShownEvent, cb);
  },
  reportOverlayPainted(showId: number): void {
    ipcRenderer.send(CH.overlayPainted, Number(showId));
  },
  onOverlayHidden(cb: () => void): () => void {
    return subscribe<void>(CH.overlayHiddenEvent, () => cb());
  },
  reportActiveConversations(ids: string[]): void {
    ipcRenderer.send(CH.activeConversations, Array.isArray(ids) ? ids.map(String) : []);
  },
  focusMain(conversationId: string | null): Promise<void> {
    return ipcRenderer.invoke(CH.focusMain, conversationId === null ? null : String(conversationId));
  },
  requestTaskForm(prefill: TaskFormPrefill): Promise<void> {
    return ipcRenderer.invoke(CH.requestTaskForm, prefill);
  },
  onOpenConversation(cb: (conversationId: string) => void): () => void {
    return subscribe<string>(CH.openConversationEvent, cb);
  },
  onOpenTaskForm(cb: (prefill: TaskFormPrefill) => void): () => void {
    return subscribe<TaskFormPrefill>(CH.openTaskFormEvent, cb);
  },
};

contextBridge.exposeInMainWorld("spaider", bridge);
