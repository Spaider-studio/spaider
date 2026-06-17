"use client";

import { useState, useCallback, useRef } from "react";
import { getWorkflowRuns, getReplayEvents } from "@/lib/api";
import type { WorkflowRun, ReplayEvent } from "@/lib/types";

interface UseReplayReturn {
  runs: WorkflowRun[];
  events: ReplayEvent[];
  selectedRun: WorkflowRun | null;
  selectedEvent: ReplayEvent | null;
  loadingRuns: boolean;
  loadingEvents: boolean;
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
  const [error, setError] = useState<string | null>(null);
  // Track the latest selected run id to discard stale in-flight responses
  const latestRunIdRef = useRef<string | null>(null);

  const fetchRuns = useCallback(
    async (agentId?: string, workflowId?: string) => {
      setLoadingRuns(true);
      setError(null);
      try {
        const data = await getWorkflowRuns(agentId, workflowId);
        setRuns(data);
      } catch (err) {
        setError((err as Error).message || "Failed to fetch workflow runs");
        setRuns([]);
      } finally {
        setLoadingRuns(false);
      }
    },
    []
  );

  const selectRun = useCallback(async (run: WorkflowRun) => {
    latestRunIdRef.current = run.id;
    setSelectedRun(run);
    setSelectedEvent(null);
    setEvents([]); // clear immediately so stale events are never visible
    setLoadingEvents(true);
    setError(null);
    try {
      const data = await getReplayEvents(run.id);
      // Discard the response if a newer selection was made while this was in flight
      if (latestRunIdRef.current === run.id) {
        setEvents(data);
      }
    } catch (err) {
      if (latestRunIdRef.current === run.id) {
        setError((err as Error).message || "Failed to fetch replay events");
        setEvents([]);
      }
    } finally {
      if (latestRunIdRef.current === run.id) {
        setLoadingEvents(false);
      }
    }
  }, []);

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
    error,
    fetchRuns,
    selectRun,
    selectEvent,
    clearSelection,
  };
}
