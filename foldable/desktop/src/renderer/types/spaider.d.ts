import type { SpaiderBridge } from "../../shared-types/bridge";

declare global {
  interface Window {
    /** Preload bridge, see docs/portable/API_CONTRACT.md. */
    spaider: SpaiderBridge;
  }
}

export {};
