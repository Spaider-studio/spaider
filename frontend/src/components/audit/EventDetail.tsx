"use client";

import type { ReplayEvent } from "@/lib/types";
import { FileText, Hash, Clock, Bot } from "lucide-react";

interface EventDetailProps {
  event: ReplayEvent | null;
}

/** Human labels for well-known payload keys; everything else renders raw. */
const FIELD_LABELS: Record<string, string> = {
  text_length: "Text length",
  question_length: "Question length",
  answer_length: "Answer length",
  answer: "Answer (preview)",
  nodes_created: "Nodes created",
  nodes_merged: "Nodes merged",
  edges_created: "Edges created",
  edges_merged: "Edges merged",
  nodes_in_result: "Nodes in result",
  edges_in_result: "Edges in result",
  sample_node_ids: "Sample node IDs",
  latency_ms: "Latency (ms)",
  source: "Source",
  error: "Error",
};

function formatValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(1);
  }
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export default function EventDetail({ event }: EventDetailProps) {
  if (!event) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-xs text-[#6B7280]">
          Select an event to inspect its recorded payload.
        </p>
      </div>
    );
  }

  const payload = event.metadata ?? {};
  const entries = Object.entries(payload);

  return (
    <div className="flex flex-col h-full overflow-y-auto p-6">
      {/* Event identity */}
      <div className="mb-5">
        <div className="flex items-center gap-2 mb-3">
          <FileText className="w-4 h-4 text-[#F59E0B]" />
          <h2 className="text-sm font-semibold text-[#E4E4E7]">
            {event.type}
          </h2>
        </div>
        <div className="flex flex-col gap-2 text-[11px] text-[#A1A1AA]">
          <span className="flex items-center gap-2">
            <Hash className="w-3 h-3 text-[#6B7280]" />
            <span className="font-mono">{event.id}</span>
          </span>
          <span className="flex items-center gap-2">
            <Clock className="w-3 h-3 text-[#6B7280]" />
            {new Date(event.timestamp).toLocaleString()}
          </span>
          <span className="flex items-center gap-2">
            <Bot className="w-3 h-3 text-[#6B7280]" />
            {event.agent_name || event.agent_id || "—"}
          </span>
        </div>
      </div>

      {/* Recorded payload */}
      <div className="bg-[#12121A] border border-[#2A2A35] rounded-xl p-4">
        <h3 className="text-[11px] font-semibold text-[#A1A1AA] uppercase tracking-wide mb-3">
          Recorded payload
        </h3>
        {entries.length === 0 ? (
          <p className="text-xs text-[#6B7280]">No payload recorded for this event.</p>
        ) : (
          <dl className="flex flex-col gap-2.5">
            {entries.map(([key, value]) => (
              <div key={key} className="flex items-start gap-3">
                <dt className="w-36 flex-shrink-0 text-[11px] text-[#6B7280]">
                  {FIELD_LABELS[key] ?? key}
                </dt>
                <dd className="flex-1 min-w-0 text-[11px] text-[#E4E4E7] whitespace-pre-wrap break-words font-mono">
                  {formatValue(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      <p className="mt-4 text-[10px] text-[#6B7280] leading-relaxed">
        Events are persisted to ClickHouse, so the audit trail survives Kafka
        retention. Re-running a query against the graph state at event time
        (true replay) is on the roadmap.
      </p>
    </div>
  );
}
