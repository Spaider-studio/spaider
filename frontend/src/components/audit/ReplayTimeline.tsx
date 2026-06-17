"use client";

import type { ReplayEvent } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  MessageSquare,
  Search,
  Inbox,
  Layers,
  CheckCircle2,
  FileOutput,
  AlertTriangle,
  Database,
  CircleDot,
} from "lucide-react";

interface ReplayTimelineProps {
  events: ReplayEvent[];
  selectedEvent: ReplayEvent | null;
  loading: boolean;
  onSelectEvent: (event: ReplayEvent) => void;
}

// One entry per event type the backend actually emits (see ReplayEventType).
const EVENT_CONFIG: Record<
  string,
  { icon: typeof MessageSquare; color: string; label: string }
> = {
  ingest_received: { icon: Inbox, color: "#10B981", label: "Ingest Received" },
  ingest_queued: { icon: Layers, color: "#10B981", label: "Ingest Queued" },
  graph_mutation: { icon: Database, color: "#F59E0B", label: "Graph Mutation" },
  ingest_completed: { icon: CheckCircle2, color: "#10B981", label: "Ingest Completed" },
  ingest_failed: { icon: AlertTriangle, color: "#EF4444", label: "Ingest Failed" },
  query_received: { icon: Search, color: "#3B82F6", label: "Query Received" },
  query_answered: { icon: FileOutput, color: "#06B6D4", label: "Query Answered" },
  query_failed: { icon: AlertTriangle, color: "#EF4444", label: "Query Failed" },
  other: { icon: CircleDot, color: "#6B7280", label: "Event" },
};

export default function ReplayTimeline({
  events,
  selectedEvent,
  loading,
  onSelectEvent,
}: ReplayTimelineProps) {
  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="w-5 h-5 rounded-full border-2 border-[#3B82F6]/30 border-t-[#3B82F6] animate-spin" />
      </div>
    );
  }

  if (events.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-xs text-[#6B7280]">
          Select a workflow run to view its timeline.
        </p>
      </div>
    );
  }

  function formatTimestamp(ts: string) {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto p-4">
      <div className="relative">
        {/* Vertical line */}
        <div className="absolute left-[15px] top-2 bottom-2 w-px bg-[#2A2A35]" />

        <div className="flex flex-col gap-1">
          {events.map((event, idx) => {
            const config = EVENT_CONFIG[event.type] || EVENT_CONFIG.other;
            const Icon = config.icon;
            const active = selectedEvent?.id === event.id;

            return (
              <button
                key={event.id}
                onClick={() => onSelectEvent(event)}
                className={cn(
                  "relative flex items-start gap-3 text-left pl-0 pr-3 py-2 rounded-lg transition-all group",
                  active
                    ? "bg-white/[0.04]"
                    : "hover:bg-white/[0.02]"
                )}
              >
                {/* Timeline dot */}
                <div
                  className={cn(
                    "relative z-10 flex-shrink-0 w-[31px] h-[31px] rounded-full flex items-center justify-center border-2 transition-all",
                    active
                      ? "border-current bg-current/10"
                      : "border-[#2A2A35] bg-[#12121A] group-hover:border-current/50"
                  )}
                  style={{ color: config.color }}
                >
                  <Icon className="w-3.5 h-3.5" style={{ color: config.color }} />
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0 pt-0.5">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span
                      className="text-xs font-medium"
                      style={{ color: config.color }}
                    >
                      {config.label}
                    </span>
                    <span className="text-[10px] text-[#6B7280]">
                      {formatTimestamp(event.timestamp)}
                    </span>
                    {event.agent_name && (
                      <span className="text-[10px] text-[#A1A1AA] px-1.5 py-0.5 bg-white/[0.03] rounded">
                        {event.agent_name}
                      </span>
                    )}
                  </div>
                  {typeof event.metadata?.answer === "string" && (
                    <p className="text-[11px] text-[#A1A1AA] leading-relaxed truncate">
                      {(event.metadata.answer as string).slice(0, 120)}
                      {(event.metadata.answer as string).length > 120 ? "..." : ""}
                    </p>
                  )}
                </div>

                {/* Step number */}
                <span className="text-[10px] text-[#6B7280] font-mono flex-shrink-0 pt-1">
                  #{idx + 1}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
