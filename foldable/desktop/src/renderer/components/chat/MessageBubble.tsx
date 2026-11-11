import { AlertCircle, Check, Copy, RotateCcw, Square } from "lucide-react";
import { memo, useState } from "react";
import { cn } from "@/lib/utils";
import type { Message, MessageTimings } from "../../api/types";
import { formatSeconds } from "../../lib/format";
import type { ActivityTrack } from "../../store/activity";
import Logo from "../Logo";
import ActivityLine from "./ActivityLine";
import { copyText } from "./CodeBlock";
import Markdown from "./Markdown";
import SourcesBlock from "./SourcesBlock";
import ThinkingBlock from "./ThinkingBlock";

function timingParts(t: MessageTimings | null | undefined): string[] {
  if (!t) return [];
  const parts: string[] = [];
  if (typeof t.memory_search_ms === "number") parts.push(`Suche ${formatSeconds(t.memory_search_ms)}`);
  if (typeof t.prompt_processing_ms === "number") parts.push(`Kontext ${formatSeconds(t.prompt_processing_ms)}`);
  if (typeof t.generation_ms === "number") parts.push(`Antwort ${formatSeconds(t.generation_ms)}`);
  if (typeof t.total_ms === "number") parts.push(`gesamt ${formatSeconds(t.total_ms)}`);
  return parts;
}

function UsageLine({ usage, timings }: { usage: Message["usage"]; timings: MessageTimings | null | undefined }) {
  const parts: string[] = [];
  if (usage) {
    if (usage.tokens_per_second > 0) parts.push(`${usage.tokens_per_second.toLocaleString("de-DE", { maximumFractionDigits: 1 })} Token/s`);
    if (usage.time_to_first_token_ms > 0) parts.push(`erstes Token nach ${formatSeconds(usage.time_to_first_token_ms)}`);
    if (usage.completion_tokens > 0) parts.push(`${usage.completion_tokens} Token`);
  }
  parts.push(...timingParts(timings));
  if (parts.length === 0) return null;
  return <div className="text-[11px] text-text-secondary/60 mt-1.5 tabular-nums">{parts.join(" · ")}</div>;
}

/**
 * Lightweight renderer while tokens arrive: plain text with preserved line
 * breaks and a cursor. Full Markdown (+ highlighting) is rendered only once
 * the message is complete, so no per-token Markdown parsing happens.
 */
export function StreamingText({ content }: { content: string }) {
  return (
    <div className="streaming-text whitespace-pre-wrap break-words" data-testid="streaming-text">
      {content}
      <span className="streaming-cursor" aria-hidden="true">
        ▍
      </span>
    </div>
  );
}

function MessageBubbleInner({
  message,
  streaming,
  activity,
  stopping,
  thinking,
  showThinking,
  onStop,
  onRetry,
}: {
  message: Message;
  streaming: boolean;
  /** Activity track of this message (chat stream + global channel). */
  activity?: ActivityTrack;
  stopping?: boolean;
  /** Thinking text received through `thinking` events. */
  thinking?: string;
  /** Active model exposes a reasoning toggle. */
  showThinking?: boolean;
  onStop?: () => void;
  /** Sends the preceding user text again (only offered for retryable errors). */
  onRetry?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    if (await copyText(message.content)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  if (message.role === "user") {
    return (
      <div className="flex justify-end animate-fade-in msg-complete" data-role="user">
        <div className="max-w-[78%] rounded-2xl rounded-br-md bg-bg-tertiary border border-border px-4 py-2.5 text-sm whitespace-pre-wrap break-words leading-relaxed">
          {message.content}
        </div>
      </div>
    );
  }

  const empty = message.content.length === 0;
  return (
    <div className={cn("flex items-start gap-3 animate-fade-in", !streaming && "msg-complete")} data-role="assistant" data-message-id={message.id}>
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-bg-secondary border border-border flex items-center justify-center mt-0.5">
        <Logo className="w-5 h-5" />
      </div>
      <div className="min-w-0 flex-1 max-w-[calc(100%-3rem)]">
        {showThinking && thinking && <ThinkingBlock text={thinking} streaming={streaming && empty} />}
        <div className="text-sm leading-relaxed">
          {empty && streaming ? (
            <span className="inline-flex items-center gap-1 text-text-secondary py-1">
              <span className="w-1.5 h-1.5 rounded-full bg-text-secondary/70 animate-pulse" />
              <span className="w-1.5 h-1.5 rounded-full bg-text-secondary/70 animate-pulse [animation-delay:150ms]" />
              <span className="w-1.5 h-1.5 rounded-full bg-text-secondary/70 animate-pulse [animation-delay:300ms]" />
            </span>
          ) : streaming ? (
            <StreamingText content={message.content} />
          ) : (
            <Markdown content={message.content} />
          )}
        </div>

        {activity && (
          <ActivityLine
            track={activity}
            live={streaming}
            stopping={Boolean(stopping)}
            onStop={onStop}
            errorText={message.status === "error" ? `Fehlgeschlagen: ${message.error || "Bei der Antwort ist ein Fehler aufgetreten."}` : undefined}
          />
        )}

        {message.status === "error" && (
          <div className="mt-2 flex flex-col gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200 max-w-xl" role="alert" data-testid="message-error" data-error-code={message.error_info?.code ?? undefined}>
            <div className="flex items-start gap-2">
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
              <span className="min-w-0">
                <span>{message.error || "Bei der Antwort ist ein Fehler aufgetreten."}</span>
                {message.error_info?.code && <span className="ml-1.5 font-mono text-red-200/70">({message.error_info.code})</span>}
              </span>
            </div>
            {message.error_info?.next_action_de && (
              <div className="text-red-100/90 pl-5" data-testid="message-next-action">
                {message.error_info.next_action_de}
              </div>
            )}
            <div className="flex items-center gap-3 pl-5 flex-wrap">
              {message.error_info?.partial_text && message.content && (
                <span className="text-amber-200" data-testid="message-partial">
                  Antwort unvollständig – der Text oben ist nur ein Teil.
                </span>
              )}
              {onRetry && (
                <button type="button" onClick={onRetry} className="inline-flex items-center gap-1 rounded-md border border-red-400/40 px-2 py-0.5 text-red-100 hover:bg-red-500/20" data-testid="message-retry">
                  <RotateCcw className="w-3 h-3" /> Erneut senden
                </button>
              )}
              {typeof message.error_info?.retry_after_s === "number" && message.error_info.retry_after_s > 0 && <span className="text-red-200/70">frühestens in {message.error_info.retry_after_s} s</span>}
            </div>
          </div>
        )}

        {!streaming && (
          <div className="mt-1.5 flex items-center gap-2 flex-wrap">
            {message.status === "stopped" && !activity && (
              <span className="inline-flex items-center gap-1 text-[11px] text-text-secondary">
                <Square className="w-3 h-3" /> Abgebrochen
              </span>
            )}
            {message.content && (
              <button
                type="button"
                onClick={() => void onCopy()}
                className={cn(
                  "inline-flex items-center gap-1 text-[11px] text-text-secondary hover:text-text-primary transition-colors",
                )}
                aria-label="Antwort kopieren"
              >
                {copied ? <Check className="w-3 h-3 text-accent-green" /> : <Copy className="w-3 h-3" />}
                {copied ? "Kopiert" : "Antwort kopieren"}
              </button>
            )}
          </div>
        )}

        <SourcesBlock sources={message.sources ?? []} />
        {!streaming && <UsageLine usage={message.usage} timings={message.timings} />}
      </div>
    </div>
  );
}

const MessageBubble = memo(MessageBubbleInner);
export default MessageBubble;
