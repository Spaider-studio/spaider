import { ArrowDown, FileText } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import { DOCUMENT_STATUS_LABEL } from "../../lib/labels";
import { useAppStore } from "../../store/useAppStore";
import Logo from "../Logo";
import { Badge } from "../ui";
import Composer from "./Composer";
import MessageBubble from "./MessageBubble";

const EMPTY: never[] = [];

function disabledReasonFor(setup: ReturnType<typeof useAppStore.getState>["setup"]): string | null {
  if (!setup) return "Verbindung zum Kern wird hergestellt …";
  if (!setup.active_chat_model) return "Kein Chat-Modell aktiv – unter Einstellungen → Modelle ein Modell aktivieren.";
  switch (setup.runtime_state) {
    case "starting":
      return "Die Laufzeit startet … bitte kurz warten.";
    case "stopped":
      return "Die Laufzeit ist gestoppt – unter Einstellungen → Modelle das Modell erneut aktivieren.";
    case "error":
      return "Laufzeit-Fehler – Details unter Einstellungen → Modelle.";
    default:
      return null;
  }
}

export default function ChatArea() {
  const activeId = useAppStore((s) => s.activeId);
  const messages = useAppStore((s) => (s.activeId ? (s.messages[s.activeId] ?? EMPTY) : EMPTY));
  const conversation = useAppStore((s) => s.conversations.find((c) => c.id === s.activeId) ?? null);
  const streaming = useAppStore((s) => s.streaming);
  const activity = useAppStore((s) => s.activity);
  const thinking = useAppStore((s) => s.thinking);
  const loading = useAppStore((s) => s.loadingConversation);
  const setup = useAppStore((s) => s.setup);
  const documents = useAppStore((s) => s.documents);
  const sendMessage = useAppStore((s) => s.sendMessage);
  const stopGeneration = useAppStore((s) => s.stopGeneration);
  const uploadDocument = useAppStore((s) => s.uploadDocument);
  const openSettings = useAppStore((s) => s.openSettings);

  const [importing, setImporting] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true);

  const isStreamingHere = Boolean(streaming && streaming.conversationId === activeId);
  const disabledReason = disabledReasonFor(setup);
  const conversationDocs = documents.filter((d) => d.conversation_id && d.conversation_id === activeId);
  // Thinking text is only shown when the active model exposes a reasoning toggle.
  const showThinking = Boolean(setup?.active_chat_model?.capabilities?.reasoning_toggle);
  const liveTrack = streaming?.assistantId ? activity[streaming.assistantId] : undefined;
  const liveThinking = streaming?.assistantId ? thinking[streaming.assistantId] : undefined;
  const onStop = useCallback(() => void stopGeneration(), [stopGeneration]);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 48);
  }, []);

  const scrollToBottom = useCallback((smooth = false) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, []);

  // Stick to the bottom while tokens / activity arrive, unless the user scrolled up.
  useLayoutEffect(() => {
    if (stick) scrollToBottom(false);
  }, [messages, liveTrack, liveThinking, stick, scrollToBottom]);

  // New conversation selected: start at the bottom.
  useEffect(() => {
    setStick(true);
    scrollToBottom(false);
  }, [activeId, scrollToBottom]);

  const onImport = async (file: File) => {
    setImporting(true);
    try {
      await uploadDocument(file);
    } finally {
      setImporting(false);
    }
  };

  return (
    <section className="flex-1 min-w-0 flex flex-col h-full bg-bg-primary" aria-label="Chat">
      <header className="flex items-center gap-3 px-5 py-3 border-b border-border min-h-[52px]">
        <h2 className="text-sm font-medium truncate flex-1">
          {conversation ? conversation.title || "Neues Gespräch" : "SpAIder"}
        </h2>
        {conversationDocs.length > 0 && (
          <div className="flex items-center gap-1.5 overflow-x-auto">
            {conversationDocs.map((d) => (
              <button
                key={d.id}
                type="button"
                onClick={() => openSettings("documents")}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-bg-secondary px-2 py-1 text-[11px] text-text-secondary hover:text-text-primary transition-colors"
                title={`${d.name} · ${DOCUMENT_STATUS_LABEL[d.status]}`}
              >
                <FileText className="w-3 h-3 text-accent-cyan" />
                <span className="max-w-[140px] truncate">{d.name}</span>
                {d.status !== "indexed" && (
                  <Badge tone={d.status === "failed" ? "red" : "yellow"}>{DOCUMENT_STATUS_LABEL[d.status]}</Badge>
                )}
              </button>
            ))}
          </div>
        )}
      </header>

      <div className="relative flex-1 min-h-0">
        <div ref={scrollRef} onScroll={onScroll} className="absolute inset-0 overflow-y-auto">
          {loading ? (
            <div className="h-full flex items-center justify-center">
              <LoadingSpinner />
            </div>
          ) : messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center px-6 animate-fade-in">
              <Logo className="w-12 h-12 mb-4 opacity-80" />
              <h3 className="text-base font-semibold">Wie kann ich helfen?</h3>
              <p className="mt-2 text-sm text-text-secondary max-w-md leading-relaxed">
                Stelle eine Frage oder importiere eine Datei. SpAIder merkt sich wichtige Fakten aus euren Gesprächen
                und zeigt dir bei jeder Antwort die verwendeten Quellen.
              </p>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto px-5 py-6 flex flex-col gap-6">
              {messages.map((m) => {
                const isLive = isStreamingHere && m.status === "streaming";
                const track = m.role === "assistant" ? activity[m.id] : undefined;
                return (
                  <MessageBubble
                    key={m.id}
                    message={m}
                    streaming={isLive}
                    activity={track}
                    stopping={isLive ? streaming?.stopping : false}
                    thinking={m.role === "assistant" ? thinking[m.id] : undefined}
                    showThinking={showThinking}
                    onStop={isLive ? onStop : undefined}
                  />
                );
              })}
            </div>
          )}
        </div>
        {!stick && messages.length > 0 && (
          <button
            type="button"
            onClick={() => {
              setStick(true);
              scrollToBottom(true);
            }}
            aria-label="Nach unten"
            className={cn(
              "absolute bottom-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1 rounded-full border border-border bg-bg-secondary/95 px-3 py-1 text-xs text-text-secondary hover:text-text-primary shadow-lg",
            )}
          >
            <ArrowDown className="w-3.5 h-3.5" /> Nach unten
          </button>
        )}
      </div>

      <Composer
        disabled={disabledReason !== null}
        disabledReason={disabledReason}
        streaming={Boolean(streaming)}
        statusText={
          isStreamingHere
            ? liveTrack
              ? null // the activity line under the message carries the status
              : streaming?.stopping
                ? "Wird gestoppt …"
                : (streaming?.statusText ?? "Antwort wird erzeugt …")
            : streaming
              ? "Antwort in einem anderen Gespräch läuft …"
              : null
        }
        onSend={(text) => void sendMessage(text)}
        onStop={onStop}
        onImport={(file) => void onImport(file)}
        importing={importing}
      />
    </section>
  );
}
