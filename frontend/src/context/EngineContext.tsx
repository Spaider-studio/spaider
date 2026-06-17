"use client";

/**
 * EngineContext — global state for the V1 / V2 engine mode toggle.
 *
 * - Fetches the persisted value from the backend on first mount (GET /system/settings).
 * - setEngineVersion() fires the POST immediately (pessimistic update: state only
 *   changes on confirmed server response), keeping the UI and DB in sync.
 * - Consumed by Header (toggle UI) and MultiverseCanvas / NeuralGraph (WebGL rendering).
 */

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import { getSystemSettings, setEngineVersion as apiSetEngineVersion } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type EngineVersion = "v1" | "v2";

interface EngineContextValue {
  engineVersion: EngineVersion;
  setEngineVersion: (v: EngineVersion) => Promise<void>;
  isLoading: boolean;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const EngineContext = createContext<EngineContextValue | null>(null);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function EngineProvider({ children }: { children: ReactNode }) {
  const [engineVersion, setEngineVersionState] = useState<EngineVersion>("v1");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch persisted value from Neo4j on mount
  useEffect(() => {
    let cancelled = false;
    getSystemSettings()
      .then((settings) => {
        if (!cancelled) {
          setEngineVersionState(settings.engine_version ?? "v1");
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError((err as Error).message ?? "Failed to load engine settings");
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  // Pessimistic update: POST to backend, then update local state on success
  const setEngineVersion = useCallback(async (version: EngineVersion) => {
    setIsLoading(true);
    setError(null);
    try {
      const updated = await apiSetEngineVersion(version);
      setEngineVersionState(updated.engine_version ?? version);
    } catch (err) {
      setError((err as Error).message ?? "Failed to update engine version");
    } finally {
      setIsLoading(false);
    }
  }, []);

  return (
    <EngineContext.Provider value={{ engineVersion, setEngineVersion, isLoading, error }}>
      {children}
    </EngineContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useEngine(): EngineContextValue {
  const ctx = useContext(EngineContext);
  if (!ctx) {
    throw new Error("useEngine() must be used inside <EngineProvider>");
  }
  return ctx;
}
