/**
 * Minimal Server-Sent-Events parser over fetch + ReadableStream.
 * EventSource cannot send the X-SpAIder-Token header, so every SSE endpoint
 * of the core is consumed through this module instead.
 *
 * Handles `event:`/`data:`/`id:` fields, multi-line data (joined with "\n"),
 * comments (`:`), and LF / CRLF / CR line endings.
 */

export interface SseEvent {
  event: string;
  data: string;
  id?: string;
}

export class SseParser {
  private buffer = "";
  private eventName = "";
  private dataLines: string[] = [];
  private lastId: string | undefined;

  /** Feeds a chunk of text and returns every event completed by it. */
  feed(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const events: SseEvent[] = [];
    for (;;) {
      const nl = this.buffer.indexOf("\n");
      const cr = this.buffer.indexOf("\r");
      let end: number;
      let skip: number;
      if (nl === -1 && cr === -1) break;
      if (cr !== -1 && (nl === -1 || cr < nl)) {
        if (cr === this.buffer.length - 1) break; // could be the first half of CRLF
        end = cr;
        skip = this.buffer[cr + 1] === "\n" ? 2 : 1;
      } else {
        end = nl;
        skip = 1;
      }
      const line = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + skip);
      const ev = this.processLine(line);
      if (ev) events.push(ev);
    }
    return events;
  }

  /** Call at end of stream: flushes a trailing event without blank line. */
  end(): SseEvent[] {
    const events: SseEvent[] = [];
    if (this.buffer.length > 0) {
      const ev = this.processLine(this.buffer);
      this.buffer = "";
      if (ev) events.push(ev);
    }
    const pending = this.dispatch();
    if (pending) events.push(pending);
    return events;
  }

  private processLine(line: string): SseEvent | null {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "event":
        this.eventName = value;
        break;
      case "data":
        this.dataLines.push(value);
        break;
      case "id":
        this.lastId = value;
        break;
      default:
        // "retry" and unknown fields are ignored
        break;
    }
    return null;
  }

  private dispatch(): SseEvent | null {
    if (this.dataLines.length === 0 && this.eventName === "") return null;
    const ev: SseEvent = {
      event: this.eventName || "message",
      data: this.dataLines.join("\n"),
    };
    if (this.lastId !== undefined) ev.id = this.lastId;
    this.eventName = "";
    this.dataLines = [];
    return ev;
  }
}

/**
 * Reads an SSE response body to the end, invoking `onEvent` per event.
 * Resolves when the stream ends; rejects on read errors / abort.
 */
export async function readSseStream(
  response: Response,
  onEvent: (ev: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!response.body) throw new Error("SSE response has no body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = new SseParser();
  const onAbort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const ev of parser.feed(decoder.decode(value, { stream: true }))) onEvent(ev);
    }
    for (const ev of parser.feed(decoder.decode())) onEvent(ev);
    for (const ev of parser.end()) onEvent(ev);
  } finally {
    signal?.removeEventListener("abort", onAbort);
  }
}

/** Parses the JSON payload of an event; returns null for invalid JSON. */
export function parseJsonData<T>(ev: SseEvent): T | null {
  try {
    return JSON.parse(ev.data) as T;
  } catch {
    return null;
  }
}
