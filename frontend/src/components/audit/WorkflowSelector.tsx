"use client";

import { useEffect, useState } from "react";
import { getAgents } from "@/lib/api";
import type { Agent, WorkflowRun } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  Search,
  ChevronDown,
  CheckCircle2,
  XCircle,
  Clock,
  Bot,
} from "lucide-react";

interface WorkflowSelectorProps {
  runs: WorkflowRun[];
  selectedRun: WorkflowRun | null;
  loading: boolean;
  onSelectRun: (run: WorkflowRun) => void;
  onFilterChange: (agentId?: string, workflowId?: string) => void;
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
  onSelectRun,
  onFilterChange,
}: WorkflowSelectorProps) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentFilter, setAgentFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    getAgents()
      .then(setAgents)
      .catch((error) => {
        console.error("Failed to load agents:", error);
      });
  }, []);

  useEffect(() => {
    onFilterChange(agentFilter || undefined, undefined);
  }, [agentFilter, onFilterChange]);

  const filtered = runs.filter((run) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      run.workflow_id.toLowerCase().includes(q) ||
      run.agent_name?.toLowerCase().includes(q) ||
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
        <div className="relative">
          <Bot className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#6B7280]" />
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="w-full pl-8 pr-7 py-1.5 bg-[#0A0A0F] border border-[#2A2A35] rounded-lg text-xs text-[#E4E4E7] focus:outline-none focus:border-[#3B82F6]/50 transition-colors appearance-none"
          >
            <option value="">All Agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-[#6B7280] pointer-events-none" />
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
              {runs.length === 0
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
                      {run.agent_name || run.agent_id.slice(0, 8)}
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
