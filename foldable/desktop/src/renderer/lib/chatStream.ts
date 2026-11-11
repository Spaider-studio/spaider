/**
 * Consumes a chat / live-ask SSE response (same event set, see
 * API_CONTRACT.md v1 + v1.2 `observation`) with token batching. Used by the
 * overlay store; the main chat store keeps its original inline loop.
 */
import { parseJsonData, readSseStream } from "../api/sse";
import type { ActivityEvent, ChatEvent, Message, ObservationEvent } from "../api/types";
import { createTokenBatcher } from "./batch";

export interface ChatStreamHandlers {
  /** `event: message` arrived; `assistantId` is the request id of the activity events. */
  onMessage?: (assistantId: string | null) => void;
  onStatus?: (text: string | null) => void;
  onActivity?: (ev: ActivityEvent) => void;
  onObservation?: (ev: ObservationEvent) => void;
  /** Batched token text (at most every ~40 ms). */
  onTokens: (chunk: string) => void;
  onThinking?: (chunk: string) => void;
  /** Every event in order (after queued text was flushed) for the message-list reducer. */
  applyEvent: (event: ChatEvent) => void;
}

export interface ChatStreamResult {
  gotDone: boolean;
  error: string | null;
}

export async function consumeChatStream(response: Response, handlers: ChatStreamHandlers): Promise<ChatStreamResult> {
  const tokens = createTokenBatcher(handlers.onTokens);
  const thinking = createTokenBatcher((chunk) => handlers.onThinking?.(chunk));
  let streamError: string | null = null;
  let gotDone = false;
  try {
    await readSseStream(response, (ev) => {
      const data = parseJsonData<Record<string, unknown>>(ev);
      if (!data) return;
      const type = ev.event as ChatEvent["type"];
      switch (type) {
        case "token":
          if (typeof data.text === "string") tokens.push(data.text);
          return;
        case "thinking":
          if (typeof data.text === "string") thinking.push(data.text);
          return;
        case "activity":
          handlers.onActivity?.(data as unknown as ActivityEvent);
          return;
        case "observation":
          handlers.onObservation?.(data as unknown as ObservationEvent);
          return;
        case "status":
          handlers.onStatus?.(typeof data.message_de === "string" ? data.message_de : null);
          return;
        case "message": {
          const assistant = (data as { assistant_message?: Message }).assistant_message;
          handlers.onMessage?.(assistant?.id ?? null);
          break;
        }
        case "done":
          gotDone = true;
          break;
        case "error":
          if (typeof data.detail === "string") streamError = data.detail;
          break;
        default:
          break;
      }
      tokens.flush();
      thinking.flush();
      handlers.applyEvent({ type, data } as ChatEvent);
    });
  } catch (e) {
    streamError = streamError ?? (e instanceof Error ? e.message : String(e));
  }
  tokens.dispose();
  thinking.dispose();
  return { gotDone, error: streamError };
}
