import { AlertCircle, Check, Copy, Square } from "lucide-react";
import { memo, useState } from "react";
import { cn } from "@/lib/utils";
import type { Message } from "../../api/types";
import Logo from "../Logo";
import { copyText } from "./CodeBlock";
import Markdown from "./Markdown";
import SourcesBlock from "./SourcesBlock";

function UsageLine({ usage }: { usage: NonNullable<Message["usage"]> }) {
  const parts: string[] = [];
  if (usage.tokens_per_second > 0) parts.push(`${usage.tokens_per_second.toFixed(1)} Token/s`);
  if (usage.time_to_first_token_ms > 0) parts.push(`erstes Token nach ${(usage.time_to_first_token_ms / 1000).toFixed(1)} s`);
  if (usage.completion_tokens > 0) parts.push(`${usage.completion_tokens} Token`);
  if (parts.length === 0) return null;
  return <div className="text-[11px] text-text-secondary/60 mt-1.5 tabular-nums">{parts.join(" · ")}</div>;
}

function MessageBubbleInner({ message, streaming }: { message: Message; streaming: boolean }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    if (await copyText(message.content)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  if (message.role === "user") {
    return (
      <div className="flex justify-end animate-fade-in" data-role="user">
        <div className="max-w-[78%] rounded-2xl rounded-br-md bg-bg-tertiary border border-border px-4 py-2.5 text-sm whitespace-pre-wrap break-words leading-relaxed">
          {message.content}
        </div>
      </div>
    );
  }

  const empty = message.content.length === 0;
  return (
    <div className="flex items-start gap-3 animate-fade-in" data-role="assistant">
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-bg-secondary border border-border flex items-center justify-center mt-0.5">
        <Logo className="w-5 h-5" />
      </div>
      <div className="min-w-0 flex-1 max-w-[calc(100%-3rem)]">
        <div className="text-sm leading-relaxed">
          {empty && streaming ? (
            <span className="inline-flex items-center gap-1 text-text-secondary py-1">
              <span className="w-1.5 h-1.5 rounded-full bg-text-secondary/70 animate-pulse" />
              <span className="w-1.5 h-1.5 rounded-full bg-text-secondary/70 animate-pulse [animation-delay:150ms]" />
              <span className="w-1.5 h-1.5 rounded-full bg-text-secondary/70 animate-pulse [animation-delay:300ms]" />
            </span>
          ) : (
            <Markdown content={message.content} className={streaming ? "is-streaming" : undefined} />
          )}
        </div>

        {message.status === "error" && (
          <div className="mt-2 inline-flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
            <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
            <span>{message.error || "Bei der Antwort ist ein Fehler aufgetreten."}</span>
          </div>
        )}

        {!streaming && (
          <div className="mt-1.5 flex items-center gap-2 flex-wrap">
            {message.status === "stopped" && (
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
        {message.usage && !streaming && <UsageLine usage={message.usage} />}
      </div>
    </div>
  );
}

const MessageBubble = memo(MessageBubbleInner);
export default MessageBubble;
