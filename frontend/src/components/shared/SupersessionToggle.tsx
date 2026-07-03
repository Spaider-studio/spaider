"use client";

/**
 * SupersessionToggle — per-agent contradiction/update resolution.
 *
 * On = working memory: a fact that updates a functional attribute (new CEO,
 * moved HQ) supersedes the prior one, so retrieval returns the current value.
 * Off = archive memory: keep the full history; nothing is superseded.
 * Bound to the agentId prop; owns its own fetch/set lifecycle.
 */

import { useEffect, useState } from "react";
import { History, Loader2, Zap } from "lucide-react";
import { getSupersession, setSupersession as apiSetSupersession } from "@/lib/api";

export default function SupersessionToggle({ agentId }: { agentId: string }) {
  const [on, setOn] = useState<boolean | null>(null); // null = loading
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSupersession(agentId)
      .then((v) => {
        if (!cancelled) setOn(v);
      })
      .catch(() => {
        if (!cancelled) setOn(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  async function toggle(next: boolean) {
    if (busy || next === on) return;
    setBusy(true);
    try {
      const confirmed = await apiSetSupersession(agentId, next);
      setOn(confirmed);
    } catch {
      // leave the switch where it was on failure
    } finally {
      setBusy(false);
    }
  }

  const ready = on !== null;

  return (
    <div
      className="flex items-center gap-2"
      title="On (working memory): an update supersedes the prior fact, so retrieval returns the current value. Off (archive): keep the full history."
    >
      {on ? (
        <Zap className="w-3.5 h-3.5 flex-shrink-0 text-amber-400" />
      ) : (
        <History className="w-3.5 h-3.5 flex-shrink-0 text-[#6B7280]" />
      )}
      <span className="text-[11px] text-[#A1A1AA]">Supersede updates</span>

      <div
        className={`flex items-center gap-0.5 rounded-lg border p-0.5 transition-all duration-300 ${
          on
            ? "border-amber-500/50 bg-amber-950/30 shadow-[0_0_10px_rgba(245,158,11,0.2)]"
            : "border-[#2A2A35] bg-[#1A1A25]"
        }`}
      >
        <button
          onClick={() => toggle(false)}
          disabled={busy || !ready}
          className={`px-2 py-0.5 rounded-md text-[11px] font-medium transition-all duration-200 ${
            ready && !on
              ? "bg-[#2A2A35] text-[#E4E4E7]"
              : "text-[#6B7280] hover:text-[#A1A1AA]"
          }`}
        >
          Archive
        </button>
        <button
          onClick={() => toggle(true)}
          disabled={busy || !ready}
          className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium transition-all duration-200 ${
            on
              ? "bg-amber-600 text-white shadow-[0_0_8px_rgba(245,158,11,0.5)]"
              : "text-[#6B7280] hover:text-[#A1A1AA]"
          }`}
        >
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
          Working
        </button>
      </div>
    </div>
  );
}
