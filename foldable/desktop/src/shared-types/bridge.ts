/**
 * Types shared between the Electron main process, the preload script and the
 * renderer. Mirrors the `window.spaider` bridge from
 * docs/portable/API_CONTRACT.md ("Electron preload bridge").
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
} as const;

/** URL prefixes the renderer may open in the system browser. */
export const OPEN_EXTERNAL_ALLOWLIST = [
  "https://huggingface.co/",
  "https://github.com/ggml-org/llama.cpp",
] as const;

export function isAllowedExternalUrl(url: string): boolean {
  return OPEN_EXTERNAL_ALLOWLIST.some((prefix) => url.startsWith(prefix));
}
