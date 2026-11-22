import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkflowRun } from "@/lib/types";

vi.mock("@/lib/api", () => ({ getWorkflowRuns: vi.fn(), getReplayEvents: vi.fn() }));

import { getReplayEvents, getWorkflowRuns } from "@/lib/api";
import { useReplay } from "./useReplay";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function run(id: string): WorkflowRun {
  return { id, workflow_id: id, agent_id: "kb", status: "completed", started_at: "", event_count: 1 };
}

beforeEach(() => {
  vi.mocked(getWorkflowRuns).mockReset();
  vi.mocked(getReplayEvents).mockReset();
});

describe("useReplay", () => {
  it("shows the newest request's runs even when an older one answers last", async () => {
    const slow = deferred<{ runs: WorkflowRun[]; complete: boolean }>();
    const fast = deferred<{ runs: WorkflowRun[]; complete: boolean }>();
    vi.mocked(getWorkflowRuns).mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise);
    const { result } = renderHook(() => useReplay());

    act(() => {
      void result.current.fetchRuns("kb-a");
      void result.current.fetchRuns("kb-b");
    });
    await act(async () => fast.resolve({ runs: [run("from-b")], complete: true }));
    await act(async () => slow.resolve({ runs: [run("from-a")], complete: true }));

    expect(result.current.runs.map((r) => r.id)).toEqual(["from-b"]);
    expect(result.current.loadingRuns).toBe(false);
  });

  it("an older request failing late does not replace the newer result with an error", async () => {
    const slow = deferred<{ runs: WorkflowRun[]; complete: boolean }>();
    const fast = deferred<{ runs: WorkflowRun[]; complete: boolean }>();
    vi.mocked(getWorkflowRuns).mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise);
    const { result } = renderHook(() => useReplay());

    act(() => {
      void result.current.fetchRuns("kb-a");
      void result.current.fetchRuns("kb-b");
    });
    await act(async () => fast.resolve({ runs: [run("from-b")], complete: true }));
    await act(async () => slow.reject(new Error("timed out")));

    expect(result.current.runs.map((r) => r.id)).toEqual(["from-b"]);
    expect(result.current.error).toBeNull();
  });

  it("says when the list may be partial", async () => {
    vi.mocked(getWorkflowRuns).mockResolvedValueOnce({ runs: [run("recent")], complete: false });
    const { result } = renderHook(() => useReplay());

    await act(async () => result.current.fetchRuns());

    expect(result.current.partial).toBe(true);
  });

  it("a complete list is not marked partial", async () => {
    vi.mocked(getWorkflowRuns).mockResolvedValueOnce({ runs: [run("all")], complete: true });
    const { result } = renderHook(() => useReplay());

    await act(async () => result.current.fetchRuns());

    expect(result.current.partial).toBe(false);
  });

  it("shows an error, and no stale list, when the audit store is unavailable", async () => {
    vi.mocked(getWorkflowRuns).mockRejectedValueOnce(new Error("The audit store is unavailable. Try again shortly."));
    const { result } = renderHook(() => useReplay());

    await act(async () => result.current.fetchRuns());

    expect(result.current.error).toBe("The audit store is unavailable. Try again shortly.");
    expect(result.current.runs).toEqual([]);
    expect(result.current.loadingRuns).toBe(false);
  });
});
