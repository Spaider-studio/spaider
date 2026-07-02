"use client";

/**
 * MemoryModeContext — per-agent memory mode (off | on).
 *
 * Replaces the old global V1/V2 engine flag. The mode belongs to the agent
 * currently selected in the graph store, so this provider tracks that agent
 * and reads/writes its mode via /api/v1/agents/{id}/memory-mode.
 *
 *   off  classic retrieval, no synaptic scoring or reinforcement
 *   on   synaptic retrieval that learns from usage (edge width reflects
 *        utility_weight) and accepts explicit feedback
 *
 * Consumed by Header (toggle), GraphCanvas3D + QueryPanel (rendering cues).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { getMemoryMode, setMemoryMode as apiSetMemoryMode } from "@/lib/api";
import { useGraph } from "@/hooks/useGraph";

export type MemoryMode = "off" | "on";

interface MemoryModeContextValue {
  memoryMode: MemoryMode;
  setMemoryMode: (m: MemoryMode) => Promise<void>;
  isLoading: boolean;
  error: string | null;
}

const MemoryModeContext = createContext<MemoryModeContextValue | null>(null);

export function MemoryModeProvider({ children }: { children: ReactNode }) {
  const agentId = useGraph((s) => s.agentId);
  const [memoryMode, setMemoryModeState] = useState<MemoryMode>("on");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Read the selected agent's mode whenever the selection changes.
  useEffect(() => {
    if (!agentId) {
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    getMemoryMode(agentId)
      .then((mode) => {
        if (!cancelled) setMemoryModeState(mode);
      })
      .catch((err) => {
        if (!cancelled) setError((err as Error).message ?? "Failed to load memory mode");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  // Pessimistic update: POST, then reflect the confirmed value.
  const setMemoryMode = useCallback(
    async (mode: MemoryMode) => {
      if (!agentId) return;
      setIsLoading(true);
      setError(null);
      try {
        const confirmed = await apiSetMemoryMode(agentId, mode);
        setMemoryModeState(confirmed);
      } catch (err) {
        setError((err as Error).message ?? "Failed to update memory mode");
      } finally {
        setIsLoading(false);
      }
    },
    [agentId]
  );

  return (
    <MemoryModeContext.Provider value={{ memoryMode, setMemoryMode, isLoading, error }}>
      {children}
    </MemoryModeContext.Provider>
  );
}

export function useMemoryMode(): MemoryModeContextValue {
  const ctx = useContext(MemoryModeContext);
  if (!ctx) {
    throw new Error("useMemoryMode() must be used inside <MemoryModeProvider>");
  }
  return ctx;
}
