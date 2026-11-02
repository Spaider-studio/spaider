/**
 * Pure state transitions for conversations and streamed chat events.
 * Kept free of React/zustand so they are trivially unit-testable.
 */
import type { ChatEvent, Conversation, Message } from "../api/types";

export function sortNewestFirst(list: Conversation[]): Conversation[] {
  return [...list].sort((a, b) => {
    const ta = Date.parse(b.updated_at) - Date.parse(a.updated_at);
    if (!Number.isNaN(ta) && ta !== 0) return ta;
    return Date.parse(b.created_at) - Date.parse(a.created_at);
  });
}

export function upsertConversation(list: Conversation[], conv: Conversation): Conversation[] {
  const idx = list.findIndex((c) => c.id === conv.id);
  const next = idx === -1 ? [conv, ...list] : list.map((c) => (c.id === conv.id ? conv : c));
  return sortNewestFirst(next);
}

export function removeConversation(list: Conversation[], id: string): Conversation[] {
  return list.filter((c) => c.id !== id);
}

export function renameConversation(list: Conversation[], id: string, title: string): Conversation[] {
  return list.map((c) => (c.id === id ? { ...c, title } : c));
}

/** Touches updated_at/message_count after a completed turn so the list re-sorts. */
export function touchConversation(list: Conversation[], id: string, patch: Partial<Conversation>): Conversation[] {
  return sortNewestFirst(list.map((c) => (c.id === id ? { ...c, ...patch } : c)));
}

/**
 * Picks the next active conversation after `deletedId` disappears:
 * the one that followed it in the (newest-first) list, else the previous.
 */
export function nextActiveAfterDelete(list: Conversation[], deletedId: string, activeId: string | null): string | null {
  if (activeId !== deletedId) return activeId;
  const idx = list.findIndex((c) => c.id === deletedId);
  const remaining = removeConversation(list, deletedId);
  if (remaining.length === 0) return null;
  return (remaining[idx] ?? remaining[idx - 1] ?? remaining[0]).id;
}

function findStreaming(messages: Message[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant" && messages[i].status === "streaming") return i;
  }
  return -1;
}

/** Applies one SSE chat event to the message list of a conversation. */
export function applyChatEvent(messages: Message[], event: ChatEvent): Message[] {
  switch (event.type) {
    case "message": {
      const { user_message, assistant_message } = event.data;
      const without = messages.filter((m) => m.id !== user_message.id && m.id !== assistant_message.id);
      return [...without, user_message, { ...assistant_message, status: "streaming" }];
    }
    case "token": {
      const idx = findStreaming(messages);
      if (idx === -1) return messages;
      const next = messages.slice();
      next[idx] = { ...next[idx], content: next[idx].content + event.data.text };
      return next;
    }
    case "sources": {
      const idx = findStreaming(messages);
      if (idx === -1) return messages;
      const next = messages.slice();
      next[idx] = { ...next[idx], sources: event.data.sources };
      return next;
    }
    case "done": {
      const final = event.data.message;
      const idx = messages.findIndex((m) => m.id === final.id);
      if (idx === -1) {
        const sIdx = findStreaming(messages);
        if (sIdx === -1) return [...messages, final];
        const next = messages.slice();
        next[sIdx] = final;
        return next;
      }
      const next = messages.slice();
      next[idx] = final;
      return next;
    }
    case "error": {
      const idx = findStreaming(messages);
      if (idx === -1) return messages;
      const next = messages.slice();
      next[idx] = { ...next[idx], status: "error", error: event.data.detail };
      return next;
    }
    case "status":
    case "thinking":
    case "activity":
      // Status text, thinking text and activity live outside the message list.
      return messages;
    default:
      return messages;
  }
}

/** Adjusts the open post-processing count of a conversation (never below 0). */
export function adjustPostprocessing(list: Conversation[], id: string, delta: number): Conversation[] {
  return list.map((c) =>
    c.id === id ? { ...c, postprocessing_pending: Math.max(0, (c.postprocessing_pending ?? 0) + delta) } : c,
  );
}

/** Marks a still-streaming assistant message as stopped/errored when the stream ends abruptly. */
export function finalizeDanglingStream(messages: Message[], error: string | null): Message[] {
  const idx = findStreaming(messages);
  if (idx === -1) return messages;
  const next = messages.slice();
  next[idx] = error
    ? { ...next[idx], status: "error", error }
    : { ...next[idx], status: "stopped" };
  return next;
}
