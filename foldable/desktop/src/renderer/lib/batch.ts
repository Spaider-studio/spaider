/**
 * Collects streamed text chunks and hands them to `flush` at most once per
 * `intervalMs` (default 40 ms, see ACTIVITY_EVENTS.md: "Textstücke sammelt die
 * UI alle 30–50 ms vor dem Rendern"). Keeps React state updates per stream
 * bounded no matter how fast tokens arrive.
 */
export interface TokenBatcher {
  /** Queues a chunk; schedules a flush if none is pending. */
  push(text: string): void;
  /** Delivers everything queued right now (synchronously). */
  flush(): void;
  /** Flushes and stops; later pushes are delivered immediately. */
  dispose(): void;
  readonly pending: number;
}

export function createTokenBatcher(flush: (text: string) => void, intervalMs = 40): TokenBatcher {
  let buffer: string[] = [];
  let size = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let disposed = false;

  const deliver = () => {
    timer = null;
    if (size === 0) return;
    const text = buffer.join("");
    buffer = [];
    size = 0;
    flush(text);
  };

  return {
    push(text) {
      if (!text) return;
      if (disposed) {
        flush(text);
        return;
      }
      buffer.push(text);
      size += text.length;
      if (timer === null) timer = setTimeout(deliver, intervalMs);
    },
    flush() {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      deliver();
    },
    dispose() {
      this.flush();
      disposed = true;
    },
    get pending() {
      return size;
    },
  };
}
