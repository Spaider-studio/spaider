"use client";

import { useState, useCallback } from "react";
import { getWorkflowRuns, getReplayEvents } from "@/lib/api";
import type { WorkflowRun, ReplayEvent } from "@/lib/types";
import { useLatestRequest } from "./useLatestRequest";

interface UseReplayReturn {
  runs: WorkflowRun[];
  events: ReplayEvent[];
  selectedRun: WorkflowRun | null;
  selectedEvent: ReplayEvent | null;
  loadingRuns: boolean;
  loadingEvents: boolean;
  /** The audit store did not answer, so the list holds only what Kafka still has. */
  partial: boolean;
  error: string | null;
  fetchRuns: (agentId?: string, workflowId?: string) => Promise<void>;
  selectRun: (run: WorkflowRun) => Promise<void>;
  selectEvent: (event: ReplayEvent | null) => void;
  clearSelection: () => void;
}

export function useReplay(): UseReplayReturn {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [events, setEvents] = useState<ReplayEvent[]>([]);
  const [selectedRun, setSelectedRun] = useState<WorkflowRun | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<ReplayEvent | null>(null);
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [loadingEvents, setLoadingEvents] = useState(false);
  const [partial, setPartial] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Whichever answer arrives last must not win: only the newest request counts.
  const startRunsRequest = useLatestRequest();
  const startEventsRequest = useLatestRequest();

  const fetchRuns = useCallback(
    async (agentId?: string, workflowId?: string) => {
      const current = startRunsRequest();
      setLoadingRuns(true);
      setError(null);
      try {
        const data = await getWorkflowRuns(agentId, workflowId);
        if (!current()) return;
        setRuns(data.runs);
        setPartial(!data.complete);
      } catch (err) {
        if (!current()) return;
        setError((err as Error).message || "Failed to fetch workflow runs");
        setRuns([]);
        setPartial(false);
      } finally {
        if (current()) setLoadingRuns(false);
      }
    },
    [startRunsRequest]
  );

  const selectRun = useCallback(
    async (run: WorkflowRun) => {
      const current = startEventsRequest();
      setSelectedRun(run);
      setSelectedEvent(null);
      setEvents([]); // clear immediately so stale events are never visible
      setLoadingEvents(true);
      setError(null);
      try {
        const data = await getReplayEvents(run.id);
        if (current()) setEvents(data.events);
      } catch (err) {
        if (current()) {
          setError((err as Error).message || "Failed to fetch replay events");
          setEvents([]);
        }
      } finally {
        if (current()) setLoadingEvents(false);
      }
    },
    [startEventsRequest]
  );

  const selectEvent = useCallback((event: ReplayEvent | null) => {
    setSelectedEvent(event);
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedRun(null);
    setSelectedEvent(null);
    setEvents([]);
  }, []);

  return {
    runs,
    events,
    selectedRun,
    selectedEvent,
    loadingRuns,
    loadingEvents,
    partial,
    error,
    fetchRuns,
    selectRun,
    selectEvent,
    clearSelection,
  };
}
