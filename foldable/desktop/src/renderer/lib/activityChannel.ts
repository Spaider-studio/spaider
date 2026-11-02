/**
 * Keeps one `GET /api/activity` SSE connection alive for the whole app
 * session (ACTIVITY_EVENTS.md, "Globaler Kanal"): connects, forwards events,
 * and reconnects with exponential backoff (1 s … 30 s) when the stream ends
 * or fails. A connection that stayed open for a while resets the backoff.
 */
import type { ApiClient } from "../api/client";
import type { ActivityEvent, RuntimeEvent, SchedulerEvent } from "../api/types";

export type ChannelState = "connecting" | "connected" | "reconnecting" | "closed";

export interface ActivityChannelHandlers {
  onActivity?: (ev: ActivityEvent) => void;
  onScheduler?: (ev: SchedulerEvent) => void;
  onRuntime?: (ev: RuntimeEvent) => void;
  onState?: (state: ChannelState, attempt: number) => void;
}

export interface ActivityChannelOptions {
  minBackoffMs?: number;
  maxBackoffMs?: number;
  /** A connection open longer than this resets the backoff. */
  stableAfterMs?: number;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
}

function defaultSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const t = setTimeout(done, ms);
    function done() {
      signal.removeEventListener("abort", done);
      clearTimeout(t);
      resolve();
    }
    signal.addEventListener("abort", done, { once: true });
  });
}

export function nextBackoff(attempt: number, min = 1000, max = 30000): number {
  return Math.min(max, min * 2 ** Math.max(0, attempt - 1));
}

/** Starts the channel; returns a function that closes it. */
export function startActivityChannel(
  client: ApiClient,
  handlers: ActivityChannelHandlers,
  options: ActivityChannelOptions = {},
): () => void {
  const controller = new AbortController();
  const { signal } = controller;
  const min = options.minBackoffMs ?? 1000;
  const max = options.maxBackoffMs ?? 30000;
  const stableAfter = options.stableAfterMs ?? 10000;
  const sleep = options.sleep ?? defaultSleep;

  void (async () => {
    let attempt = 0;
    while (!signal.aborted) {
      handlers.onState?.(attempt === 0 ? "connecting" : "reconnecting", attempt);
      const openedAt = Date.now();
      let opened = false;
      try {
        await client.activityStream(
          {
            onOpen: () => {
              opened = true;
              handlers.onState?.("connected", attempt);
            },
            onActivity: handlers.onActivity,
            onScheduler: handlers.onScheduler,
            onRuntime: handlers.onRuntime,
          },
          signal,
        );
      } catch {
        /* dropped or refused: fall through to the backoff */
      }
      if (signal.aborted) break;
      attempt = opened && Date.now() - openedAt >= stableAfter ? 1 : attempt + 1;
      await sleep(nextBackoff(attempt, min, max), signal);
    }
    handlers.onState?.("closed", 0);
  })();

  return () => controller.abort();
}
