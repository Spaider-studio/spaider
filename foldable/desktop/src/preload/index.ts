/**
 * Preload script: exposes exactly the `window.spaider` bridge from the API
 * contract. Runs sandboxed (contextIsolation: true, sandbox: true), so it may
 * only require `electron` and must stay a single file.
 */
import { contextBridge, ipcRenderer } from "electron";
import type { AppInfo, BackendInfo, BackendStatus, SpaiderBridge } from "../shared-types/bridge";

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
} as const;

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
};

contextBridge.exposeInMainWorld("spaider", bridge);
