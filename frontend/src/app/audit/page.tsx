"use client";

import { useEffect } from "react";
import { History } from "lucide-react";
import Sidebar from "@/components/layout/Sidebar";
import Header from "@/components/layout/Header";
import WorkflowSelector from "@/components/audit/WorkflowSelector";
import ReplayTimeline from "@/components/audit/ReplayTimeline";
import EventDetail from "@/components/audit/EventDetail";
import { useReplay } from "@/hooks/useReplay";

export default function AuditPage() {
  const {
    runs,
    events,
    selectedRun,
    selectedEvent,
    loadingRuns,
    loadingEvents,
    error,
    fetchRuns,
    selectRun,
    selectEvent,
  } = useReplay();

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  return (
    <div className="flex h-screen overflow-hidden bg-[#0A0A0F]">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Header />

        {/* Page header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-[#2A2A35]">
          <div className="w-8 h-8 rounded-lg bg-[#F59E0B]/10 border border-[#F59E0B]/20 flex items-center justify-center">
            <History className="w-4 h-4 text-[#F59E0B]" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-[#E4E4E7]">
              Audit Log
            </h1>
            <p className="text-xs text-[#6B7280]">
              Durable event log of every ingest and query — ClickHouse-backed
            </p>
          </div>
          {selectedRun && (
            <div className="ml-auto flex items-center gap-2">
              <span className="text-[10px] text-[#6B7280] px-2 py-1 bg-white/[0.03] rounded-lg border border-[#2A2A35]">
                {selectedRun.workflow_id}
              </span>
              <span className="text-[10px] text-[#A1A1AA]">
                {events.length} events
              </span>
            </div>
          )}
        </div>

        {/* Error banner */}
        {error && (
          <div className="mx-6 mt-3 px-4 py-2.5 bg-[#EF4444]/10 border border-[#EF4444]/20 rounded-lg text-xs text-[#EF4444]">
            {error}
          </div>
        )}

        {/* Three-panel layout */}
        <div className="flex-1 flex overflow-hidden">
          {/* Left: Workflow selector */}
          <div className="w-72 flex-shrink-0 border-r border-[#2A2A35] bg-[#12121A]/50 overflow-hidden">
            <WorkflowSelector
              runs={runs}
              selectedRun={selectedRun}
              loading={loadingRuns}
              onSelectRun={selectRun}
              onFilterChange={fetchRuns}
            />
          </div>

          {/* Center: Timeline */}
          <div className="w-80 flex-shrink-0 border-r border-[#2A2A35] overflow-hidden">
            <ReplayTimeline
              events={events}
              selectedEvent={selectedEvent}
              loading={loadingEvents}
              onSelectEvent={selectEvent}
            />
          </div>

          {/* Right: Event detail */}
          <div className="flex-1 overflow-hidden">
            <EventDetail event={selectedEvent} />
          </div>
        </div>
      </div>
    </div>
  );
}
