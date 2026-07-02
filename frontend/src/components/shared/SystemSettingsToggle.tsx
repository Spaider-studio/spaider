"use client";

import { useEffect, useState } from "react";
import { Brain, Loader2, AlertCircle } from "lucide-react";
import { getSystemSettings, setReflectionEnabled } from "@/lib/api";

// ---------------------------------------------------------------------------
// SystemSettingsToggle
// ---------------------------------------------------------------------------
// Isolated component — fetches its own state, owns the POST lifecycle.
// Drop it anywhere in the UI with no required props.
// ---------------------------------------------------------------------------

export default function SystemSettingsToggle() {
  // null = loading, undefined = fetch failed
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [toggling, setToggling] = useState(false);
  const [fetchError, setFetchError] = useState(false);

  // Load initial state on mount
  useEffect(() => {
    getSystemSettings()
      .then((s) => setEnabled(s.auto_reflection))
      .catch(() => setFetchError(true));
  }, []);

  async function handleToggle() {
    if (enabled === null || toggling) return;
    const next = !enabled;
    setToggling(true);
    try {
      const confirmed = await setReflectionEnabled(next);
      // Only update UI on confirmed server response (pessimistic update)
      setEnabled(confirmed.auto_reflection);
    } catch {
      // Leave the switch in its prior position on failure
    } finally {
      setToggling(false);
    }
  }

  // ── Loading skeleton ───────────────────────────────────────────────────────
  if (enabled === null && !fetchError) {
    return (
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[#1A1A25] border border-[#2A2A35] flex items-center justify-center">
            <Brain className="w-4 h-4 text-[#6B7280]" />
          </div>
          <div className="flex flex-col gap-1">
            <div className="h-3 w-40 bg-[#2A2A35] rounded animate-pulse" />
            <div className="h-2.5 w-56 bg-[#1A1A25] rounded animate-pulse" />
          </div>
        </div>
        <div className="w-11 h-6 bg-[#2A2A35] rounded-full animate-pulse" />
      </div>
    );
  }

  // ── Fetch error ────────────────────────────────────────────────────────────
  if (fetchError) {
    return (
      <div className="flex items-center gap-2 text-xs text-[#EF4444]">
        <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
        Could not reach backend — check your connection.
      </div>
    );
  }

  // ── Main render ───────────────────────────────────────────────────────────
  return (
    <div className="flex items-center justify-between gap-4">
      {/* Left: icon + label + status badge */}
      <div className="flex items-center gap-3 min-w-0">
        <div
          className={[
            "w-8 h-8 rounded-lg border flex items-center justify-center flex-shrink-0 transition-all duration-300",
            enabled
              ? "bg-green-500/10 border-green-500/30 shadow-[0_0_12px_2px_rgba(34,197,94,0.15)]"
              : "bg-[#1A1A25] border-[#2A2A35]",
          ].join(" ")}
        >
          <Brain
            className={[
              "w-4 h-4 transition-colors duration-300",
              enabled ? "text-green-400" : "text-[#6B7280]",
            ].join(" ")}
          />
        </div>

        <div className="flex flex-col gap-0.5 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-[#E4E4E7]">
              Autonomous Reflection Engine
            </span>

            {/* Pulsing active badge */}
            {enabled && (
              <span className="flex items-center gap-1 text-[10px] text-green-400 animate-pulse">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 inline-block" />
                🧠 Autonomous Consolidation Active
              </span>
            )}
          </div>
          <p className="text-[11px] text-[#6B7280] leading-tight">
            {enabled
              ? "Scheduled consolidation is active — pruning orphans, fusing duplicates, and decaying unused synapses."
              : "Hippocampus is dormant. Enable to begin scheduled memory consolidation."}
          </p>
        </div>
      </div>

      {/* Right: animated pill toggle */}
      <button
        role="switch"
        aria-checked={enabled ?? false}
        onClick={handleToggle}
        disabled={toggling}
        className={[
          "relative flex-shrink-0 w-11 h-6 rounded-full transition-all duration-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-[#12121A]",
          enabled
            ? "bg-green-500 focus-visible:ring-green-500"
            : "bg-[#2A2A35] focus-visible:ring-[#6B7280]",
          toggling ? "opacity-60 cursor-not-allowed" : "cursor-pointer",
        ].join(" ")}
        title={enabled ? "Disable Reflection Engine" : "Enable Reflection Engine"}
      >
        {/* Track glow when active */}
        {enabled && (
          <span className="absolute inset-0 rounded-full shadow-[0_0_10px_2px_rgba(34,197,94,0.4)] pointer-events-none" />
        )}

        {/* Thumb */}
        <span
          className={[
            "absolute top-0.5 left-0.5 w-5 h-5 rounded-full flex items-center justify-center",
            "bg-white shadow-sm transition-transform duration-300",
            enabled ? "translate-x-5" : "translate-x-0",
          ].join(" ")}
        >
          {toggling && (
            <Loader2 className="w-3 h-3 text-[#6B7280] animate-spin" />
          )}
        </span>
      </button>
    </div>
  );
}
