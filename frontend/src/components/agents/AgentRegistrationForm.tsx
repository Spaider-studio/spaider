"use client";

import { useState } from "react";
import { Plus, ShieldCheck, X } from "lucide-react";
import { createAgent } from "@/lib/api";
import type { AgentCreateRequest } from "@/lib/api";
import type { Agent, ClearanceLevel } from "@/lib/types";
import { CLEARANCE_LABELS } from "@/lib/types";
import LoadingSpinner from "@/components/shared/LoadingSpinner";

const DEFAULT_PERMISSIONS = ["read", "write", "delete"];

/** Tailwind classes for each clearance level — active (selected) state. */
const LEVEL_ACTIVE: Record<ClearanceLevel, string> = {
  1: "bg-slate-500/15  border-slate-500/40  text-slate-300",
  2: "bg-slate-400/15  border-slate-400/40  text-slate-200",
  3: "bg-amber-500/15  border-amber-500/40  text-amber-300",
  4: "bg-orange-500/15 border-orange-500/40 text-orange-300",
  5: "bg-rose-500/15   border-rose-500/40   text-rose-300",
};

/** Tailwind classes for the inline label strip below the selector. */
const LEVEL_LABEL_COLOR: Record<ClearanceLevel, string> = {
  1: "text-slate-400",
  2: "text-slate-300",
  3: "text-amber-400",
  4: "text-orange-400",
  5: "text-rose-400",
};

/** Dot color shown next to the label for quick visual scanning. */
const LEVEL_DOT: Record<ClearanceLevel, string> = {
  1: "bg-slate-500",
  2: "bg-slate-400",
  3: "bg-amber-400",
  4: "bg-orange-400",
  5: "bg-rose-500",
};

const CLEARANCE_LEVELS: ClearanceLevel[] = [1, 2, 3, 4, 5];

interface Props {
  onSuccess: (agent: Agent) => void;
  onClose: () => void;
}

export default function AgentRegistrationForm({ onSuccess, onClose }: Props) {
  const [name, setName]               = useState("");
  const [description, setDescription] = useState("");
  const [permissions, setPermissions] = useState<Set<string>>(new Set(["read", "write"]));
  const [clearanceLevel, setClearanceLevel] = useState<ClearanceLevel>(1);
  const [creating, setCreating]       = useState(false);
  const [error, setError]             = useState<string | null>(null);

  function togglePermission(p: string) {
    const next = new Set(permissions);
    if (next.has(p)) next.delete(p);
    else next.add(p);
    setPermissions(next);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const payload: AgentCreateRequest = {
        name:            name.trim(),
        description:     description.trim() || undefined,
        permissions:     Array.from(permissions),
        clearance_level: clearanceLevel,
      };
      const agent = await createAgent(payload);
      onSuccess(agent);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create agent");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="mb-6 bg-[#12121A] border border-[#2A2A35] rounded-xl overflow-hidden">
      {/* ── Header ───────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-[#2A2A35] bg-[#0D0D15]">
        <h2 className="text-sm font-semibold text-[#E4E4E7]">Register New Agent</h2>
        <button
          onClick={onClose}
          className="text-[#6B7280] hover:text-[#A1A1AA] transition-colors"
          aria-label="Close registration form"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <form onSubmit={handleSubmit} className="p-5 flex flex-col gap-4">
        {/* ── Agent Name ─────────────────────────────────────────────────── */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs text-[#A1A1AA] font-medium">
            Agent Name <span className="text-[#EF4444]">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. research-agent-01"
            className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-[#E4E4E7] placeholder-[#6B7280] focus:outline-none focus:border-[#3B82F6]/50 transition-colors"
            required
            autoFocus
          />
        </div>

        {/* ── Description ────────────────────────────────────────────────── */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs text-[#A1A1AA] font-medium">Description</label>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional description"
            className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-[#E4E4E7] placeholder-[#6B7280] focus:outline-none focus:border-[#3B82F6]/50 transition-colors"
          />
        </div>

        {/* ── Permissions ────────────────────────────────────────────────── */}
        <div className="flex flex-col gap-2">
          <label className="text-xs text-[#A1A1AA] font-medium">Permissions</label>
          <div className="flex items-center gap-2">
            {DEFAULT_PERMISSIONS.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => togglePermission(p)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all border ${
                  permissions.has(p)
                    ? "bg-[#3B82F6]/15 border-[#3B82F6]/30 text-[#3B82F6]"
                    : "bg-[#0A0A0F] border-[#2A2A35] text-[#6B7280] hover:border-[#3A3A45]"
                }`}
              >
                {p}
              </button>
            ))}
          </div>
        </div>

        {/* ── Security Clearance Level ────────────────────────────────────── */}
        <div className="flex flex-col gap-2">
          <label className="text-xs text-[#A1A1AA] font-medium flex items-center gap-1.5">
            <ShieldCheck className="w-3.5 h-3.5" />
            Security Clearance Level
          </label>

          {/* Segmented selector — 5 buttons, one per level */}
          <div
            role="radiogroup"
            aria-label="Security clearance level"
            className="grid grid-cols-5 gap-1.5"
          >
            {CLEARANCE_LEVELS.map((lvl) => {
              const isActive = clearanceLevel === lvl;
              return (
                <button
                  key={lvl}
                  type="button"
                  role="radio"
                  aria-checked={isActive}
                  onClick={() => setClearanceLevel(lvl)}
                  className={`
                    relative flex flex-col items-center justify-center
                    py-2.5 rounded-lg border text-xs font-semibold
                    transition-all duration-150 select-none
                    focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3B82F6]/50
                    ${isActive
                      ? LEVEL_ACTIVE[lvl]
                      : "bg-[#0A0A0F] border-[#2A2A35] text-[#6B7280] hover:border-[#3A3A45] hover:text-[#A1A1AA]"
                    }
                  `}
                >
                  <span className="text-sm leading-none">{lvl}</span>
                  {/* Active dot indicator */}
                  {isActive && (
                    <span
                      className={`mt-1 w-1.5 h-1.5 rounded-full ${LEVEL_DOT[lvl]}`}
                    />
                  )}
                </button>
              );
            })}
          </div>

          {/* Inline label strip — shows full name of selected level */}
          <div className="flex items-center gap-2 px-1">
            <span
              className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${LEVEL_DOT[clearanceLevel]}`}
            />
            <span className={`text-xs font-medium ${LEVEL_LABEL_COLOR[clearanceLevel]}`}>
              Level {clearanceLevel} — {CLEARANCE_LABELS[clearanceLevel]}
            </span>
            <span className="ml-auto text-[10px] text-[#4B5563]">
              {clearanceLevel <= 2
                ? "Nodes up to this level visible"
                : clearanceLevel <= 3
                ? "Restricted access granted"
                : clearanceLevel === 4
                ? "Sensitive data access"
                : "Full classified access"}
            </span>
          </div>
        </div>

        {/* ── Error ──────────────────────────────────────────────────────── */}
        {error && (
          <div className="text-xs text-[#EF4444] bg-[#EF4444]/10 border border-[#EF4444]/20 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        {/* ── Actions ────────────────────────────────────────────────────── */}
        <div className="flex items-center gap-3 pt-1">
          <button
            type="submit"
            disabled={creating || !name.trim()}
            className="flex items-center gap-2 px-4 py-2 bg-[#3B82F6] hover:bg-[#2563EB] text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50"
          >
            {creating ? <LoadingSpinner size="sm" /> : <Plus className="w-3.5 h-3.5" />}
            {creating ? "Creating..." : "Create Agent"}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm text-[#A1A1AA] hover:text-[#E4E4E7] transition-colors"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
