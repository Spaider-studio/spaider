"use client";

/**
 * HibernationCadence — per-agent autonomous consolidation control.
 *
 * A cadence selector (Off / Hourly / Daily / Weekly) plus a "Consolidate now"
 * button. Bound to the agentId prop; owns its own fetch/set/trigger lifecycle
 * via /api/v1/agents/{id}/consolidation and /consolidate-now.
 */

import { useEffect, useState } from "react";
import { Moon, Loader2, Zap } from "lucide-react";
import {
  getConsolidationConfig,
  setConsolidationConfig,
  consolidateNow,
} from "@/lib/api";
import { useToast } from "@/components/shared/Toast";

const OPTIONS: { label: string; hours: number }[] = [
  { label: "Off", hours: 0 },
  { label: "Hourly", hours: 1 },
  { label: "Daily", hours: 24 },
  { label: "Weekly", hours: 168 },
];

export default function HibernationCadence({ agentId }: { agentId: string }) {
  const { toast } = useToast();
  const [hours, setHours] = useState<number | null>(null); // null = loading
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getConsolidationConfig(agentId)
      .then((c) => {
        if (!cancelled) setHours(c.interval_hours);
      })
      .catch(() => {
        if (!cancelled) setHours(0);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  async function onChange(next: number) {
    setSaving(true);
    try {
      const confirmed = await setConsolidationConfig(agentId, next);
      setHours(confirmed);
    } catch {
      toast("Could not update consolidation cadence.", "error");
    } finally {
      setSaving(false);
    }
  }

  async function onConsolidateNow() {
    setRunning(true);
    try {
      const r = await consolidateNow(agentId);
      const parts = [`pruned ${r.pruned}`, `fused ${r.fused}`, `decayed ${r.decayed}`];
      if (r.proposed) parts.push(`proposed ${r.proposed}`);
      toast(`Consolidation complete — ${parts.join(", ")}.`, "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Consolidation failed.", "error");
    } finally {
      setRunning(false);
    }
  }

  const ready = hours !== null;

  return (
    <div className="flex items-center gap-2">
      <Moon className="w-3 h-3 text-[#6B7280] flex-shrink-0" />
      <span className="text-[11px] text-[#6B7280]">Consolidate</span>
      <select
        value={ready ? String(hours) : "0"}
        disabled={!ready || saving}
        onChange={(e) => onChange(Number(e.target.value))}
        className="bg-[#12121A] border border-[#2A2A35] text-[#E4E4E7] text-[11px] rounded-lg px-2 py-1 focus:outline-none focus:border-[#3B82F6] cursor-pointer disabled:opacity-40 transition-colors"
        title="How often this agent autonomously consolidates (prune, fuse, decay)."
      >
        {OPTIONS.map((o) => (
          <option key={o.hours} value={o.hours}>
            {o.label}
          </option>
        ))}
      </select>

      <button
        onClick={onConsolidateNow}
        disabled={running || !ready}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium
          bg-[#1A1A25] border border-[#2A2A35] text-[#A1A1AA]
          hover:text-[#E4E4E7] hover:border-[#3A3A45]
          disabled:opacity-40 disabled:cursor-not-allowed transition-all"
        title="Run a consolidation pass for this agent right now"
      >
        {running ? <Loader2 className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}
        {running ? "Consolidating…" : "Consolidate now"}
      </button>
    </div>
  );
}
