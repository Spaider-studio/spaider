"use client";

import { useState } from "react";
import {
  Bot,
  Copy,
  CheckCircle2,
  Trash2,
  Key,
  ShieldCheck,
  RefreshCw,
} from "lucide-react";
import { formatDate, maskApiKey } from "@/lib/utils";
import type { Agent, ClearanceLevel } from "@/lib/types";
import { CLEARANCE_LABELS } from "@/lib/types";
import MemoryModeToggle from "@/components/shared/MemoryModeToggle";

// ---------------------------------------------------------------------------
// Clearance badge — style map
//
// Three tiers as specified:
//   Level 1          → slate  (public / no restriction)
//   Level 2–3        → amber  (internal / confidential)
//   Level 4–5        → rose   (secret / top-secret)
//
// Each tier has: container classes + icon color class.
// Kept as a plain function so TypeScript can verify the union is exhaustive.
// ---------------------------------------------------------------------------

interface BadgeStyle {
  container: string;
  icon: string;
  dot: string;
}

function getClearanceStyle(level: ClearanceLevel): BadgeStyle {
  switch (level) {
    case 1:
      return {
        container: "bg-slate-500/10 text-slate-400 border-slate-500/20",
        icon:      "text-slate-500",
        dot:       "bg-slate-500",
      };
    case 2:
    case 3:
      return {
        container: "bg-amber-500/10 text-amber-400 border-amber-500/20",
        icon:      "text-amber-500",
        dot:       "bg-amber-400",
      };
    case 4:
    case 5:
      return {
        container: "bg-rose-500/10 text-rose-400 border-rose-500/20",
        icon:      "text-rose-500",
        dot:       "bg-rose-400",
      };
  }
}

// ---------------------------------------------------------------------------
// ClearanceBadge — self-contained, exported for potential reuse
// ---------------------------------------------------------------------------

interface ClearanceBadgeProps {
  level: ClearanceLevel;
}

export function ClearanceBadge({ level }: ClearanceBadgeProps) {
  const style = getClearanceStyle(level);

  return (
    <span
      className={`
        inline-flex items-center gap-1 px-2 py-0.5
        rounded-full border text-[10px] font-semibold
        tracking-wide select-none whitespace-nowrap
        ${style.container}
      `}
      title={`Clearance Level ${level} — ${CLEARANCE_LABELS[level]}`}
    >
      <ShieldCheck className={`w-2.5 h-2.5 flex-shrink-0 ${style.icon}`} />
      L{level}
      <span className="hidden sm:inline opacity-75">· {CLEARANCE_LABELS[level]}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// AgentCard
// ---------------------------------------------------------------------------

interface Props {
  agent: Agent;
  onDelete: () => void;
  onRotate: () => void;
}

export default function AgentCard({ agent, onDelete, onRotate }: Props) {
  const [copiedId,  setCopiedId]  = useState(false);
  const [copiedKey, setCopiedKey] = useState(false);

  // Defensive fallback — backend guarantees the field, but old cached
  // payloads or partial responses may omit it.
  const clearanceLevel: ClearanceLevel =
    (agent.clearance_level as ClearanceLevel) ?? 1;

  function copyId() {
    navigator.clipboard.writeText(agent.id).then(() => {
      setCopiedId(true);
      setTimeout(() => setCopiedId(false), 2000);
    });
  }

  function copyKey() {
    if (!agent.api_key) return;
    navigator.clipboard.writeText(agent.api_key).then(() => {
      setCopiedKey(true);
      setTimeout(() => setCopiedKey(false), 2000);
    });
  }

  return (
    <div className="bg-[#12121A] border border-[#2A2A35] rounded-xl p-5 hover:border-[#3A3A45] transition-colors group">
      <div className="flex items-start justify-between gap-4">

        {/* ── Left: icon + content ─────────────────────────────────────── */}
        <div className="flex items-center gap-4 min-w-0 flex-1">
          <div className="w-10 h-10 rounded-xl bg-[#1A1A25] border border-[#2A2A35] flex items-center justify-center flex-shrink-0">
            <Bot className="w-5 h-5 text-[#3B82F6]" />
          </div>
          <div className="min-w-0 flex-1">
            {/* Name + permission pills */}
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <span className="text-sm font-semibold text-[#E4E4E7]">{agent.name}</span>
              {agent.permissions?.length > 0 && (
                <div className="flex items-center gap-1">
                  {agent.permissions.slice(0, 3).map((p) => (
                    <span
                      key={p}
                      className="text-[10px] px-1.5 py-0.5 bg-[#1A1A25] border border-[#2A2A35] text-[#A1A1AA] rounded-full"
                    >
                      {p}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {agent.description && (
              <p className="text-xs text-[#A1A1AA] mb-2 truncate">{agent.description}</p>
            )}

            {/* Agent ID + API Key rows */}
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <Key className="w-3 h-3 text-[#6B7280]" />
                <code className="text-[11px] text-[#6B7280] font-mono">{agent.id}</code>
                <button
                  onClick={copyId}
                  className="text-[#6B7280] hover:text-[#A1A1AA] transition-colors"
                  title="Copy agent ID"
                >
                  {copiedId ? (
                    <CheckCircle2 className="w-3 h-3 text-[#10B981]" />
                  ) : (
                    <Copy className="w-3 h-3" />
                  )}
                </button>
              </div>

              {agent.api_key && (
                <div className="flex items-center gap-2">
                  <ShieldCheck className="w-3 h-3 text-[#6B7280]" />
                  <code className="text-[11px] text-[#6B7280] font-mono">
                    {maskApiKey(agent.api_key)}
                  </code>
                  <button
                    onClick={copyKey}
                    className="text-[#6B7280] hover:text-[#A1A1AA] transition-colors"
                    title="Copy API key"
                  >
                    {copiedKey ? (
                      <CheckCircle2 className="w-3 h-3 text-[#10B981]" />
                    ) : (
                      <Copy className="w-3 h-3" />
                    )}
                  </button>
                </div>
              )}
            </div>

            {/* Per-agent synaptic memory switch */}
            <div className="mt-3 pt-3 border-t border-[#2A2A35]/60">
              <MemoryModeToggle agentId={agent.id} />
            </div>
          </div>
        </div>

        {/* ── Right: clearance badge + date + delete ───────────────────── */}
        <div className="flex flex-col items-end gap-2 flex-shrink-0">
          {/* Clearance badge — top-right anchor */}
          <ClearanceBadge level={clearanceLevel} />

          <div className="flex items-center gap-3">
            <span className="text-xs text-[#6B7280]">{formatDate(agent.created_at)}</span>
            <button
              onClick={onRotate}
              className="opacity-0 group-hover:opacity-100 p-1.5 text-[#6B7280] hover:text-amber-400 hover:bg-amber-500/10 rounded-lg transition-all"
              title="Rotate API key"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={onDelete}
              className="opacity-0 group-hover:opacity-100 p-1.5 text-[#6B7280] hover:text-[#EF4444] hover:bg-[#EF4444]/10 rounded-lg transition-all"
              title="Delete agent"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}
