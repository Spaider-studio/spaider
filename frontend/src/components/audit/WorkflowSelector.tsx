"use client";

import { useEffect, useState } from "react";
import { useGraph } from "@/hooks/useGraph";
import type { WorkflowRun } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  Search,
  CheckCircle2,
  XCircle,
  Clock,
} from "lucide-react";

interface WorkflowSelectorProps {
  runs: WorkflowRun[];
  selectedRun: WorkflowRun | null;
  loading: boolean;
  /** Loading the list failed, so an empty list does not mean there are no runs. */
  failed?: boolean;
  onSelectRun: (run: WorkflowRun) => void;
}

const STATUS_CONFIG = {
  completed: { icon: CheckCircle2, color: "text-[#10B981]", bg: "bg-[#10B981]/10" },
  failed: { icon: XCircle, color: "text-[#EF4444]", bg: "bg-[#EF4444]/10" },
  in_progress: { icon: Clock, color: "text-[#F59E0B]", bg: "bg-[#F59E0B]/10" },
};

export default function WorkflowSelector({
  runs,
  selectedRun,
  loading,
  failed = false,
  onSelectRun,
}: WorkflowSelectorProps) {
  // Which knowledge base the list is for is chosen in the header; this component
  // only needs the names, to say whose run each one is.
  const agents = useGraph((s) => s.agents);
  const loadAgents = useGraph((s) => s.loadAgents);
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    void loadAgents();
  }, [loadAgents]);

  const kbName = (run: WorkflowRun) =>
    run.agent_name || agents.find((a) => a.id === run.agent_id)?.name || run.agent_id.slice(0, 8);

  const filtered = runs.filter((run) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      run.workflow_id.toLowerCase().includes(q) ||
      kbName(run).toLowerCase().includes(q) ||
      run.topic?.toLowerCase().includes(q)
    );
  });

  function formatTime(dateStr: string) {
    const d = new Date(dateStr);
    return d.toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  return (
    <div className="flex flex-col h-full">
      {/* Filters */}
      <div className="p-3 border-b border-[#2A2A35] space-y-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#6B7280]" />
          <input
            type="text"
            placeholder="Search workflows..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-8 pr-3 py-1.5 bg-[#0A0A0F] border border-[#2A2A35] rounded-lg text-xs text-[#E4E4E7] placeholder-[#6B7280] focus:outline-none focus:border-[#3B82F6]/50 transition-colors"
          />
        </div>
      </div>

      {/* Run list */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="w-5 h-5 rounded-full border-2 border-[#3B82F6]/30 border-t-[#3B82F6] animate-spin" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-12 px-4">
            <p className="text-xs text-[#6B7280]">
              {failed
                ? "The run list could not be loaded. See the message above."
                : runs.length === 0
                  ? "No workflow runs yet. Events appear here as soon as an ingest or query runs."
                  : "No runs match your filter."}
            </p>
          </div>
        ) : (
          <div className="flex flex-col">
            {filtered.map((run) => {
              const status = STATUS_CONFIG[run.status];
              const StatusIcon = status.icon;
              const active = selectedRun?.id === run.id;

              return (
                <button
                  key={run.id}
                  onClick={() => onSelectRun(run)}
                  className={cn(
                    "text-left px-3 py-2.5 border-b border-[#2A2A35]/50 transition-all",
                    active
                      ? "bg-[#3B82F6]/10 border-l-2 border-l-[#3B82F6]"
                      : "hover:bg-white/[0.02] border-l-2 border-l-transparent"
                  )}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <StatusIcon className={cn("w-3.5 h-3.5 flex-shrink-0", status.color)} />
                    <span className="text-xs font-medium text-[#E4E4E7] truncate">
                      {run.topic || run.workflow_id}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 ml-5.5">
                    <span className="text-[10px] text-[#6B7280]">
                      {kbName(run)}
                    </span>
                    <span className="text-[10px] text-[#6B7280]">
                      {formatTime(run.started_at)}
                    </span>
                    <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full", status.bg, status.color)}>
                      {run.event_count} events
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
