/**
 * Live overlay (frameless, ~520x360): target line, question input, status /
 * activity line, streaming answer (plain text while streaming, Markdown after
 * done), optional small preview from the core, Stopp / "Im Hauptfenster
 * öffnen" / "Als Aufgabe ausführen". Questions only - never an action.
 */
import { AppWindow, ExternalLink, Eye, EyeOff, ListPlus, RefreshCw, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import type { Message } from "../api/types";
import { getPerf } from "../lib/perf";
import ActivityLine from "../components/chat/ActivityLine";
import Markdown from "../components/chat/Markdown";
import { StreamingText } from "../components/chat/MessageBubble";
import OverlayInput from "../components/overlay/OverlayInput";
import SourcePicker from "../components/overlay/SourcePicker";
import Logo from "../components/Logo";
import { Button } from "../components/ui";
import { effectiveTarget, needsNewSource, preselectedId, qualityText, sessionStateText, targetLine } from "../store/overlay";
import { useOverlayStore } from "../store/useOverlayStore";

function Turn({ message, streaming }: { message: Message; streaming: boolean }) {
  if (message.role === "user") {
    return (
      <div className="text-[11px] text-text-secondary border-l-2 border-border pl-2 whitespace-pre-wrap break-words" data-role="user">
        {message.content}
      </div>
    );
  }
  const empty = message.content.length === 0;
  return (
    <div className="text-sm leading-relaxed" data-role="assistant" data-message-id={message.id}>
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
      {message.status === "error" && <div className="mt-1 text-[11px] text-red-300">{message.error || "Fehler bei der Antwort."}</div>}
      {message.status === "stopped" && !streaming && <div className="mt-1 text-[11px] text-text-secondary">Abgebrochen</div>}
    </div>
  );
}

export default function OverlayScreen() {
  const backend = useOverlayStore((s) => s.backend);
  const client = useOverlayStore((s) => s.client);
  const settings = useOverlayStore((s) => s.settings);
  const showId = useOverlayStore((s) => s.showId);
  const sources = useOverlayStore((s) => s.sources);
  const sourcesLoading = useOverlayStore((s) => s.sourcesLoading);
  const sourceState = useOverlayStore((s) => s.sourceState);
  const session = useOverlayStore((s) => s.session);
  const quality = useOverlayStore((s) => s.quality);
  const observation = useOverlayStore((s) => s.observation);
  const preview = useOverlayStore((s) => s.preview);
  const previewLoading = useOverlayStore((s) => s.previewLoading);
  const problem = useOverlayStore((s) => s.problem);
  const messages = useOverlayStore((s) => s.messages);
  const streaming = useOverlayStore((s) => s.streaming);
  const activity = useOverlayStore((s) => s.activity);
  const hide = useOverlayStore((s) => s.hide);
  const openPicker = useOverlayStore((s) => s.openPicker);
  const closePicker = useOverlayStore((s) => s.closePicker);
  const refreshSources = useOverlayStore((s) => s.refreshSources);
  const choose = useOverlayStore((s) => s.choose);
  const ask = useOverlayStore((s) => s.ask);
  const stop = useOverlayStore((s) => s.stop);
  const refreshPreview = useOverlayStore((s) => s.refreshPreview);
  const setPreviewEnabled = useOverlayStore((s) => s.setPreviewEnabled);
  const openInMain = useOverlayStore((s) => s.openInMain);
  const runAsTask = useOverlayStore((s) => s.runAsTask);

  const [lastQuestion, setLastQuestion] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const stopClickedAt = useRef<number | null>(null);

  const target = effectiveTarget(sourceState);
  const previewOn = settings?.live_preview !== false;
  const liveTrack = streaming?.assistantId ? activity[streaming.assistantId] : undefined;
  const qText = qualityText(quality) ?? sessionStateText(session?.state);
  const reselect = needsNewSource(quality, session?.state);
  const ready = backend.state === "ready" && Boolean(client);

  // Keep the newest answer in view while it streams.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, liveTrack]);

  // Stopp click -> status text change ("Wird gestoppt …") latency (dev helper).
  useEffect(() => {
    const at = stopClickedAt.current;
    if (at !== null && streaming?.stopping) {
      stopClickedAt.current = null;
      getPerf()?.addSample("overlay_stop_to_status_ms", performance.now() - at);
    }
  }, [streaming?.stopping]);

  const onStop = () => {
    stopClickedAt.current = performance.now();
    void stop();
  };
  const onSend = (text: string) => {
    setLastQuestion(text);
    void ask(text);
  };

  let placeholder = "Frage zum Zielfenster … (Enter sendet, Shift+Enter neue Zeile, Esc schließt)";
  if (!ready) placeholder = backend.message ?? "Verbindung zum Kern …";
  else if (!target) placeholder = "Zuerst eine Quelle wählen …";
  else if (sourceState.hintPending) placeholder = "Enter bestätigt das Vordergrundfenster und sendet die Frage …";

  return (
    <div className="h-full w-full flex flex-col bg-bg-secondary text-text-primary select-text relative" data-testid="overlay-root">
      {/* Header: drag region + target line */}
      <header className="overlay-drag flex items-center gap-2 px-3 py-1.5 border-b border-border bg-bg-primary/40 min-h-[36px]">
        <Logo className="w-4 h-4 flex-shrink-0" />
        <div className="min-w-0 flex-1 flex items-center gap-1.5 text-[11px]">
          <AppWindow className="w-3 h-3 text-accent-cyan flex-shrink-0" />
          <span className="truncate" data-testid="overlay-target" title={targetLine(target)}>
            {targetLine(target)}
          </span>
          {sourceState.hintPending && target && (
            <button
              type="button"
              onClick={() => void choose(target)}
              className="overlay-no-drag flex-shrink-0 rounded border border-accent-blue/60 px-1.5 py-0.5 text-[10px] text-accent-blue hover:bg-accent-blue/10"
              data-testid="overlay-confirm-hint"
            >
              Bestätigen
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={openPicker}
          className="overlay-no-drag flex-shrink-0 rounded-md border border-border px-1.5 py-0.5 text-[10px] text-text-secondary hover:text-text-primary hover:border-[#3A3A45]"
          data-testid="overlay-pick-source"
        >
          Quelle wählen
        </button>
        <button type="button" onClick={hide} aria-label="Schließen (Esc)" title="Schließen (Esc)" className="overlay-no-drag text-text-secondary hover:text-text-primary">
          <X className="w-3.5 h-3.5" />
        </button>
      </header>

      {/* Problems / quality */}
      {(problem || qText) && (
        <div className="flex items-center gap-2 px-3 py-1 text-[11px] bg-amber-500/10 border-b border-amber-500/20 text-amber-200" role="status" data-testid="overlay-problem">
          <span className="truncate flex-1">{problem ?? qText}</span>
          {(reselect || problem) && (
            <button type="button" onClick={openPicker} className="flex-shrink-0 underline underline-offset-2 hover:text-white" data-testid="overlay-reselect">
              Quelle neu wählen
            </button>
          )}
        </div>
      )}

      {/* Answer area + preview */}
      <div className="flex-1 min-h-0 flex">
        <div ref={scrollRef} className="flex-1 min-w-0 overflow-y-auto px-3 py-2 flex flex-col gap-2" data-testid="overlay-answers">
          {messages.length === 0 ? (
            <div className="h-full flex items-center justify-center text-center text-[11px] text-text-secondary px-4 leading-relaxed">
              {target
                ? "Frag etwas zum Zielfenster. SpAIder liest nur mit und beantwortet Fragen – es führt keine Aktionen aus."
                : "Wähle das Fenster oder den Bildschirm, den SpAIder mitlesen soll."}
            </div>
          ) : (
            messages.map((m) => {
              const isLive = Boolean(streaming) && m.status === "streaming";
              const track = m.role === "assistant" ? activity[m.id] : undefined;
              return (
                <div key={m.id}>
                  <Turn message={m} streaming={isLive} />
                  {track && <ActivityLine track={track} live={isLive} stopping={Boolean(streaming?.stopping)} onStop={isLive ? onStop : undefined} />}
                </div>
              );
            })
          )}
        </div>
        {previewOn && session && (
          <aside className="w-[132px] flex-shrink-0 border-l border-border p-2 flex flex-col gap-1.5" aria-label="Vorschau" data-testid="overlay-preview">
            <div className="flex items-center justify-between text-[10px] text-text-secondary">
              <span>Vorschau</span>
              <button type="button" onClick={() => void refreshPreview()} aria-label="Vorschau aktualisieren" className="hover:text-text-primary" disabled={previewLoading}>
                <RefreshCw className={cn("w-3 h-3", previewLoading && "animate-spin")} />
              </button>
            </div>
            {preview ? (
              <img src={`data:image/png;base64,${preview}`} alt="Vorschau der Quelle (vom Kern)" className="w-full rounded border border-border bg-black object-contain" data-testid="overlay-preview-img" />
            ) : (
              <div className="w-full aspect-[4/3] rounded border border-dashed border-border flex items-center justify-center text-[10px] text-text-secondary/70 text-center px-1">
                {previewLoading ? "…" : "keine Vorschau"}
              </div>
            )}
            {observation && (
              <div className="text-[10px] text-text-secondary leading-snug" data-testid="overlay-observation">
                {observation.used_image ? "mit Bild" : "ohne Bild"} · {observation.elements} Elemente · {observation.text_chars} Zeichen
              </div>
            )}
          </aside>
        )}
      </div>

      {/* Input */}
      <div className="px-3 pt-1 pb-1">
        <OverlayInput
          disabled={!ready}
          streaming={Boolean(streaming)}
          stopping={Boolean(streaming?.stopping)}
          placeholder={placeholder}
          focusKey={showId}
          onSend={onSend}
          onStop={onStop}
        />
      </div>

      {/* Footer actions */}
      <div className="overlay-drag flex items-center gap-1 px-2 pb-1.5 text-[10px]">
        <Button variant="ghost" size="sm" className="overlay-no-drag h-6 px-1.5 text-[10px]" onClick={() => void openInMain()} icon={<ExternalLink className="w-3 h-3" />} title="Dieses Gespräch im Hauptfenster anzeigen">
          Im Hauptfenster öffnen
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="overlay-no-drag h-6 px-1.5 text-[10px]"
          onClick={() => void runAsTask(lastQuestion)}
          disabled={!lastQuestion.trim()}
          icon={<ListPlus className="w-3 h-3" />}
          title="Öffnet das Aufgaben-Formular im Hauptfenster (vorausgefüllt, nichts wird automatisch gestartet)"
          data-testid="overlay-run-as-task"
        >
          Als Aufgabe ausführen
        </Button>
        <span className="flex-1" />
        <Button
          variant="ghost"
          size="sm"
          className="overlay-no-drag h-6 px-1.5 text-[10px]"
          onClick={() => void setPreviewEnabled(!previewOn)}
          icon={previewOn ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
          title="Vorschaubild ein-/ausblenden (Einstellung live_preview)"
          aria-pressed={previewOn}
          data-testid="overlay-preview-toggle"
        >
          Vorschau
        </Button>
      </div>

      {sourceState.pickerOpen && (
        <SourcePicker
          sources={sources}
          loading={sourcesLoading}
          preselectedId={preselectedId(sourceState)}
          canClose={sourceState.everConfirmed}
          onChoose={(s) => void choose(s)}
          onClose={closePicker}
          onRefresh={() => void refreshSources()}
        />
      )}
    </div>
  );
}
