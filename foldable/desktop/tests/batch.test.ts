import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTokenBatcher } from "../src/renderer/lib/batch";
import { nextBackoff, startActivityChannel } from "../src/renderer/lib/activityChannel";
import type { ApiClient } from "../src/renderer/api/client";

describe("createTokenBatcher", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("delivers many pushes as one flush per interval", () => {
    const flushes: string[] = [];
    const b = createTokenBatcher((t) => flushes.push(t), 40);
    b.push("Hal");
    b.push("lo ");
    b.push("Welt");
    expect(flushes).toEqual([]);
    expect(b.pending).toBe(10);
    vi.advanceTimersByTime(39);
    expect(flushes).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(flushes).toEqual(["Hallo Welt"]);
    expect(b.pending).toBe(0);
    b.push("!");
    vi.advanceTimersByTime(40);
    expect(flushes).toEqual(["Hallo Welt", "!"]);
  });

  it("flush() delivers synchronously and cancels the pending timer", () => {
    const flushes: string[] = [];
    const b = createTokenBatcher((t) => flushes.push(t), 40);
    b.push("a");
    b.flush();
    expect(flushes).toEqual(["a"]);
    vi.advanceTimersByTime(100);
    expect(flushes).toEqual(["a"]);
    b.flush(); // nothing queued: no empty flush
    expect(flushes).toEqual(["a"]);
  });

  it("ignores empty chunks and passes through after dispose", () => {
    const flushes: string[] = [];
    const b = createTokenBatcher((t) => flushes.push(t), 40);
    b.push("");
    expect(b.pending).toBe(0);
    b.push("x");
    b.dispose();
    expect(flushes).toEqual(["x"]);
    b.push("late");
    expect(flushes).toEqual(["x", "late"]);
  });

  it("bounds state updates during a fast stream", () => {
    const flushes: string[] = [];
    const b = createTokenBatcher((t) => flushes.push(t), 40);
    for (let i = 0; i < 100; i++) {
      b.push("t");
      vi.advanceTimersByTime(5); // 200 tokens/s
    }
    b.flush();
    expect(flushes.join("")).toHaveLength(100);
    expect(flushes.length).toBeLessThanOrEqual(14);
  });
});

describe("activity channel", () => {
  it("doubles the backoff up to the maximum", () => {
    expect(nextBackoff(1)).toBe(1000);
    expect(nextBackoff(2)).toBe(2000);
    expect(nextBackoff(3)).toBe(4000);
    expect(nextBackoff(10)).toBe(30000);
  });

  it("reconnects after the stream ends and stops when closed", async () => {
    let calls = 0;
    const states: string[] = [];
    const client = {
      activityStream: async (handlers: { onOpen?: () => void; onScheduler?: (e: unknown) => void }) => {
        calls += 1;
        handlers.onOpen?.();
        handlers.onScheduler?.({ chat_active: false, background: { kind: null, pending: 0 }, paused: false });
        if (calls === 1) throw new Error("dropped");
        // second connection stays open until aborted
        await new Promise<void>((resolve) => setTimeout(resolve, 50));
      },
    } as unknown as ApiClient;
    const scheduler: unknown[] = [];
    const stop = startActivityChannel(
      client,
      { onScheduler: (e) => scheduler.push(e), onState: (s) => states.push(s) },
      { minBackoffMs: 1, maxBackoffMs: 2, sleep: (ms) => new Promise((r) => setTimeout(r, ms)) },
    );
    await new Promise((r) => setTimeout(r, 30));
    expect(calls).toBeGreaterThanOrEqual(2);
    expect(scheduler.length).toBeGreaterThanOrEqual(2);
    expect(states).toContain("reconnecting");
    expect(states).toContain("connected");
    stop();
    await new Promise((r) => setTimeout(r, 80));
    const after = calls;
    await new Promise((r) => setTimeout(r, 30));
    expect(calls).toBe(after);
    expect(states[states.length - 1]).toBe("closed");
  });
});
