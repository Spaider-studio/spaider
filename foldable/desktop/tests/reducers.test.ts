import { describe, expect, it } from "vitest";
import type { Conversation, Message } from "../src/renderer/api/types";
import {
  applyChatEvent,
  finalizeDanglingStream,
  nextActiveAfterDelete,
  removeConversation,
  renameConversation,
  sortNewestFirst,
  upsertConversation,
} from "../src/renderer/store/reducers";

const conv = (id: string, updated: string, title = id): Conversation => ({
  id,
  title,
  created_at: updated,
  updated_at: updated,
  message_count: 0,
  last_model_id: null,
});

const msg = (id: string, role: Message["role"], content = "", status: Message["status"] = "complete"): Message => ({
  id,
  conversation_id: "c1",
  role,
  content,
  created_at: "2026-11-02T00:00:00Z",
  status,
  model_id: null,
  sources: [],
  error: null,
  usage: null,
});

describe("conversation list reducers", () => {
  const list = [conv("a", "2026-11-01T10:00:00Z"), conv("b", "2026-11-02T10:00:00Z"), conv("c", "2026-10-30T10:00:00Z")];

  it("sorts newest first", () => {
    expect(sortNewestFirst(list).map((c) => c.id)).toEqual(["b", "a", "c"]);
  });

  it("upserts and re-sorts", () => {
    const next = upsertConversation(list, conv("d", "2026-11-03T00:00:00Z"));
    expect(next.map((c) => c.id)).toEqual(["d", "b", "a", "c"]);
    const replaced = upsertConversation(next, conv("a", "2026-11-04T00:00:00Z", "neu"));
    expect(replaced[0]).toMatchObject({ id: "a", title: "neu" });
    expect(replaced).toHaveLength(4);
  });

  it("removes and renames", () => {
    expect(removeConversation(list, "a").map((c) => c.id)).toEqual(["b", "c"]);
    expect(renameConversation(list, "c", "Titel").find((c) => c.id === "c")?.title).toBe("Titel");
  });

  it("picks the next active conversation after a delete", () => {
    const sorted = sortNewestFirst(list); // b, a, c
    expect(nextActiveAfterDelete(sorted, "a", "a")).toBe("c");
    expect(nextActiveAfterDelete(sorted, "c", "c")).toBe("a");
    expect(nextActiveAfterDelete(sorted, "b", "a")).toBe("a");
    expect(nextActiveAfterDelete([sorted[0]], "b", "b")).toBeNull();
  });
});

describe("applyChatEvent", () => {
  it("appends user and assistant messages, streams tokens, attaches sources and finalises", () => {
    let messages: Message[] = [msg("old", "assistant", "früher")];
    messages = applyChatEvent(messages, {
      type: "message",
      data: { user_message: msg("u1", "user", "Frage"), assistant_message: msg("a1", "assistant", "", "streaming") },
    });
    expect(messages.map((m) => m.id)).toEqual(["old", "u1", "a1"]);

    messages = applyChatEvent(messages, { type: "token", data: { text: "Hal" } });
    messages = applyChatEvent(messages, { type: "token", data: { text: "lo" } });
    expect(messages[2].content).toBe("Hallo");
    expect(messages[2].status).toBe("streaming");

    messages = applyChatEvent(messages, { type: "status", data: { phase: "generating", message_de: "…" } });
    expect(messages[2].content).toBe("Hallo");

    const source = {
      kind: "fact" as const,
      id: "f1",
      label: "L",
      excerpt: "E",
      status: "current" as const,
      origin: { kind: "conversation" as const, id: "c", title: "T", at: "2026-01-01T00:00:00Z" },
      superseded_by: null,
    };
    messages = applyChatEvent(messages, { type: "sources", data: { sources: [source] } });
    expect(messages[2].sources).toHaveLength(1);

    const final = { ...msg("a1", "assistant", "Hallo Welt"), sources: [source] };
    messages = applyChatEvent(messages, { type: "done", data: { message: final } });
    expect(messages[2]).toEqual(final);
  });

  it("marks the streaming message as error on error events", () => {
    let messages = applyChatEvent([], {
      type: "message",
      data: { user_message: msg("u", "user", "x"), assistant_message: msg("a", "assistant", "", "streaming") },
    });
    messages = applyChatEvent(messages, { type: "token", data: { text: "teil" } });
    messages = applyChatEvent(messages, { type: "error", data: { detail: "Kaputt", code: "runtime_error" } });
    expect(messages[1]).toMatchObject({ status: "error", error: "Kaputt", content: "teil" });
  });

  it("ignores tokens without a streaming message", () => {
    const messages = [msg("a", "assistant", "fertig")];
    expect(applyChatEvent(messages, { type: "token", data: { text: "x" } })).toBe(messages);
  });

  it("finalises dangling streams as stopped or errored", () => {
    const streaming = [msg("a", "assistant", "halb", "streaming")];
    expect(finalizeDanglingStream(streaming, null)[0].status).toBe("stopped");
    expect(finalizeDanglingStream(streaming, "weg")[0]).toMatchObject({ status: "error", error: "weg" });
    const done = [msg("a", "assistant", "fertig")];
    expect(finalizeDanglingStream(done, null)).toBe(done);
  });
});

describe("v1.3 chat events", () => {
  const streaming = [msg("u1", "user", "Hallo"), msg("a1", "assistant", "Teil", "streaming")];

  it("keeps partial text on an error, marks it and stores the error fields", () => {
    const next = applyChatEvent(streaming, {
      type: "error",
      data: { detail: "Die Antwort war leer.", code: "empty_answer", retryable: true, retry_after_s: null, needs_consent: false, partial_text: true, next_action_de: "Erneut senden." },
    });
    const a = next[1];
    expect(a.status).toBe("error");
    expect(a.content).toBe("Teil");
    expect(a.error).toBe("Die Antwort war leer.");
    expect(a.error_info).toMatchObject({ code: "empty_answer", retryable: true, partial_text: true, next_action_de: "Erneut senden." });
  });

  it("infers partial_text from the streamed content when the core omits it (older core)", () => {
    const next = applyChatEvent(streaming, { type: "error", data: { detail: "x", code: "generation_failed" } });
    expect(next[1].error_info?.partial_text).toBe(true);
    const empty = applyChatEvent([msg("u1", "user", "Hallo"), msg("a1", "assistant", "", "streaming")], { type: "error", data: { detail: "x", code: "consent_required", needs_consent: true } });
    expect(empty[1].error_info?.partial_text).toBe(false);
    expect(empty[1].error_info?.needs_consent).toBe(true);
  });

  it("ignores the sharing event for the message list", () => {
    const next = applyChatEvent(streaming, {
      type: "sharing",
      data: { connection_id: "c", connection_name: "DeepSeek", execution: "cloud", project_id: "default", history_messages: 1, system_prompt: true, memory_chars: 0, sources: 0, images: 0 },
    });
    expect(next).toBe(streaming);
  });
});
