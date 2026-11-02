import { describe, expect, it } from "vitest";
import { readSseStream, SseParser } from "../src/renderer/api/sse";

describe("SseParser", () => {
  it("parses a single event with name and data", () => {
    const p = new SseParser();
    const events = p.feed('event: token\ndata: {"text":"Hallo"}\n\n');
    expect(events).toEqual([{ event: "token", data: '{"text":"Hallo"}' }]);
  });

  it("joins multi-line data with newlines and defaults the event name to message", () => {
    const p = new SseParser();
    const events = p.feed("data: erste Zeile\ndata: zweite Zeile\n\n");
    expect(events).toEqual([{ event: "message", data: "erste Zeile\nzweite Zeile" }]);
  });

  it("handles CRLF line endings and chunks split at arbitrary positions", () => {
    const p = new SseParser();
    const raw = 'event: job\r\ndata: {"a":1}\r\n\r\nevent: job\r\ndata: {"a":2}\r\n\r\n';
    const events = [];
    for (let i = 0; i < raw.length; i += 3) events.push(...p.feed(raw.slice(i, i + 3)));
    expect(events).toEqual([
      { event: "job", data: '{"a":1}' },
      { event: "job", data: '{"a":2}' },
    ]);
  });

  it("ignores comments and unknown fields, keeps ids", () => {
    const p = new SseParser();
    const events = p.feed(": keep-alive\nretry: 1000\nid: 7\nevent: done\ndata: x\n\n");
    expect(events).toEqual([{ event: "done", data: "x", id: "7" }]);
  });

  it("strips exactly one leading space from values", () => {
    const p = new SseParser();
    expect(p.feed("data:  two spaces\n\n")[0].data).toBe(" two spaces");
    expect(p.feed("data:nospace\n\n")[0].data).toBe("nospace");
  });

  it("flushes a trailing event at end of stream", () => {
    const p = new SseParser();
    expect(p.feed("event: done\ndata: 1")).toEqual([]);
    expect(p.end()).toEqual([{ event: "done", data: "1" }]);
  });
});

describe("readSseStream", () => {
  it("reads events from a Response body stream", async () => {
    const encoder = new TextEncoder();
    const chunks = ['event: status\ndata: {"phase":"gen', 'erating"}\n\nevent: token\ndata: {"text":"Hi"}\n\n'];
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(encoder.encode(c));
        controller.close();
      },
    });
    const response = new Response(stream, { headers: { "Content-Type": "text/event-stream" } });
    const seen: string[] = [];
    await readSseStream(response, (ev) => seen.push(`${ev.event}:${ev.data}`));
    expect(seen).toEqual(['status:{"phase":"generating"}', 'token:{"text":"Hi"}']);
  });
});
