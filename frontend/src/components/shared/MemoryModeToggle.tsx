"use client";

/**
 * MemoryModeToggle — a self-contained per-agent memory switch.
 *
 * Fetches and sets one specific agent's memory_mode (off | on) via
 * /api/v1/agents/{id}/memory-mode. Unlike the graph-scoped MemoryModeContext,
 * this is bound to the agentId prop, so it is safe to render one per agent
 * card. Owns its own fetch + POST lifecycle; no required wiring.
 */

import { useEffect, useState } from "react";
import { Brain, Loader2 } from "lucide-react";
import { getMemoryMode, setMemoryMode as apiSetMemoryMode } from "@/lib/api";

export default function MemoryModeToggle({ agentId }: { agentId: string }) {
  // null = still loading the current value
  const [mode, setMode] = useState<"off" | "on" | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getMemoryMode(agentId)
      .then((m) => {
        if (!cancelled) setMode(m);
      })
      .catch(() => {
        if (!cancelled) setMode("on"); // default assumption on read failure
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  async function toggle(next: "off" | "on") {
    if (busy || next === mode) return;
    setBusy(true);
    try {
      const confirmed = await apiSetMemoryMode(agentId, next);
      setMode(confirmed);
    } catch {
      // leave the switch where it was on failure
    } finally {
      setBusy(false);
    }
  }

  const isOn = mode === "on";
  const ready = mode !== null;

  return (
    <div
      className="flex items-center gap-2"
      title="Synaptic memory for this agent. On: retrieval learns from usage (reinforce on use, decay on disuse). Off: classic retrieval."
    >
      <Brain
        className={`w-3.5 h-3.5 flex-shrink-0 transition-colors ${
          isOn ? "text-purple-400" : "text-[#6B7280]"
        }`}
      />
      <span className="text-[11px] text-[#A1A1AA]">Synaptic Memory</span>

      <div
        className={`flex items-center gap-0.5 rounded-lg border p-0.5 transition-all duration-300 ${
          isOn
            ? "border-purple-500/50 bg-purple-950/30 shadow-[0_0_10px_rgba(139,92,246,0.25)]"
            : "border-[#2A2A35] bg-[#1A1A25]"
        }`}
      >
        <button
          onClick={() => toggle("off")}
          disabled={busy || !ready}
          className={`px-2 py-0.5 rounded-md text-[11px] font-medium transition-all duration-200 ${
            ready && !isOn
              ? "bg-[#2A2A35] text-[#E4E4E7]"
              : "text-[#6B7280] hover:text-[#A1A1AA]"
          }`}
        >
          Off
        </button>
        <button
          onClick={() => toggle("on")}
          disabled={busy || !ready}
          className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium transition-all duration-200 ${
            isOn
              ? "bg-purple-600 text-white shadow-[0_0_8px_rgba(139,92,246,0.5)]"
              : "text-[#6B7280] hover:text-[#A1A1AA]"
          }`}
        >
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
          On
        </button>
      </div>
    </div>
  );
}
