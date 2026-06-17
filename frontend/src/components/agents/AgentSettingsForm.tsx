"use client";

import { useState } from "react";
import { Brain, Loader2, Trash2 } from "lucide-react";
import { updateAgent, deleteAgentInteractions } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { useToast } from "@/components/shared/Toast";
import ConfirmDialog from "@/components/shared/ConfirmDialog";

interface Props {
  agent: Agent;
  /** Called after a successful update so the parent can refresh its local state. */
  onAgentUpdated?: (updated: Agent) => void;
}

export default function AgentSettingsForm({ agent, onAgentUpdated }: Props) {
  const { toast } = useToast();

  // ── Interaction Memory toggle ──────────────────────────────────────────────
  const [memoryEnabled, setMemoryEnabled] = useState(agent.interaction_memory);
  const [toggling, setToggling] = useState(false);

  async function handleMemoryToggle() {
    if (toggling) return;
    const next = !memoryEnabled;
    setToggling(true);
    try {
      const updated = await updateAgent(agent.id, {
        name: agent.name,
        description: agent.description,
        permissions: agent.permissions,
        clearance_level: agent.clearance_level,
        interaction_memory: next,
      });
      // Pessimistic update — only flip UI after server confirms
      setMemoryEnabled(updated.interaction_memory);
      onAgentUpdated?.(updated);
      toast(
        next ? "Interaction Memory enabled." : "Interaction Memory disabled.",
        "success"
      );
    } catch (err) {
      toast(
        err instanceof Error ? err.message : "Failed to update agent.",
        "error"
      );
    } finally {
      setToggling(false);
    }
  }

  // ── Clear interactions ─────────────────────────────────────────────────────
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [clearing, setClearing] = useState(false);

  async function handleClearInteractions() {
    setClearing(true);
    try {
      const result = await deleteAgentInteractions(agent.id);
      setConfirmOpen(false);
      toast(
        `Cleared ${result.deleted_count} interaction${result.deleted_count !== 1 ? "s" : ""}.`,
        "success"
      );
    } catch (err) {
      toast(
        err instanceof Error ? err.message : "Failed to clear interaction memory.",
        "error"
      );
    } finally {
      setClearing(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* ── Interaction Memory toggle ──────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <div
            className={[
              "w-8 h-8 rounded-lg border flex items-center justify-center flex-shrink-0 transition-all duration-300",
              memoryEnabled
                ? "bg-purple-500/10 border-purple-500/30 shadow-[0_0_12px_2px_rgba(168,85,247,0.15)]"
                : "bg-[#1A1A25] border-[#2A2A35]",
            ].join(" ")}
          >
            <Brain
              className={[
                "w-4 h-4 transition-colors duration-300",
                memoryEnabled ? "text-purple-400" : "text-[#6B7280]",
              ].join(" ")}
            />
          </div>

          <div className="flex flex-col gap-0.5 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-[#E4E4E7]">
                Interaction Memory
              </span>
              {memoryEnabled && (
                <span className="flex items-center gap-1 text-[10px] text-purple-400 animate-pulse">
                  <span className="w-1.5 h-1.5 rounded-full bg-purple-400 inline-block" />
                  Recording
                </span>
              )}
            </div>
            <p className="text-[11px] text-[#6B7280] leading-tight">
              {memoryEnabled
                ? "Every query is stored as an episodic memory node linked to its source facts."
                : "Enable to record query/response pairs as InteractionNodes in the knowledge graph."}
            </p>
          </div>
        </div>

        {/* Pill toggle */}
        <button
          role="switch"
          aria-checked={memoryEnabled}
          onClick={handleMemoryToggle}
          disabled={toggling}
          className={[
            "relative flex-shrink-0 w-11 h-6 rounded-full transition-all duration-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-[#12121A]",
            memoryEnabled
              ? "bg-purple-500 focus-visible:ring-purple-500"
              : "bg-[#2A2A35] focus-visible:ring-[#6B7280]",
            toggling ? "opacity-60 cursor-not-allowed" : "cursor-pointer",
          ].join(" ")}
          title={memoryEnabled ? "Disable Interaction Memory" : "Enable Interaction Memory"}
        >
          {memoryEnabled && (
            <span className="absolute inset-0 rounded-full shadow-[0_0_10px_2px_rgba(168,85,247,0.4)] pointer-events-none" />
          )}
          <span
            className={[
              "absolute top-0.5 left-0.5 w-5 h-5 rounded-full flex items-center justify-center",
              "bg-white shadow-sm transition-transform duration-300",
              memoryEnabled ? "translate-x-5" : "translate-x-0",
            ].join(" ")}
          >
            {toggling && (
              <Loader2 className="w-3 h-3 text-[#6B7280] animate-spin" />
            )}
          </span>
        </button>
      </div>

      {/* ── Danger Zone ───────────────────────────────────────────────────────── */}
      <div className="border border-red-500/20 rounded-lg p-4 bg-red-500/5">
        <p className="text-xs font-semibold text-red-400 uppercase tracking-wider mb-3">
          Danger Zone
        </p>
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="text-sm font-medium text-[#E4E4E7]">
              Clear Interaction Memory
            </p>
            <p className="text-[11px] text-[#6B7280] leading-tight mt-0.5">
              Permanently delete all episodic memory records for this agent.
              Knowledge graph nodes are never affected.
            </p>
          </div>
          <button
            onClick={() => setConfirmOpen(true)}
            className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-red-400 border border-red-500/30 rounded-lg hover:bg-red-500/10 transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" />
            Clear
          </button>
        </div>
      </div>

      {/* Confirm dialog */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Clear Interaction Memory"
        description={`This will permanently delete all InteractionNodes for "${agent.name}". SpaiderNodes and knowledge edges are not affected. This action cannot be undone.`}
        confirmLabel="Clear Memory"
        cancelLabel="Cancel"
        onConfirm={handleClearInteractions}
        destructive
        loading={clearing}
      />
    </div>
  );
}
