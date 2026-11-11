/**
 * Picker for the live target: windows and screens from GET /api/live/sources
 * (SpAIder's own windows are filtered out). The foreground hint is highlighted
 * and focused; Enter / click confirms, Esc closes (or hides the overlay the
 * very first time, when nothing was confirmed yet).
 */
import { AppWindow, Monitor, RefreshCw, X } from "lucide-react";
import { useEffect, useRef, type KeyboardEvent } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import type { LiveSource, LiveSources, ScreenSource, WindowSource } from "../../api/types";
import { selectableSources } from "../../store/overlay";

export default function SourcePicker({
  sources,
  loading,
  preselectedId,
  canClose,
  onChoose,
  onClose,
  onRefresh,
}: {
  sources: LiveSources | null;
  loading: boolean;
  preselectedId: string | null;
  /** False the very first time (a source must be confirmed before anything else). */
  canClose: boolean;
  onChoose: (source: LiveSource) => void;
  onClose: () => void;
  onRefresh: () => void;
}) {
  const { windows, screens } = selectableSources(sources);
  const listRef = useRef<HTMLDivElement>(null);
  const preselected = windows.find((w) => w.id === preselectedId) ?? screens.find((s) => s.id === preselectedId) ?? null;

  // Focus the preselected entry (or the first) so Enter confirms it right away.
  useEffect(() => {
    const root = listRef.current;
    if (!root) return;
    const target = (root.querySelector('[data-preselected="true"]') ?? root.querySelector("button[data-source]")) as HTMLButtonElement | null;
    target?.focus();
  }, [sources, preselectedId]);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (canClose) onClose();
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const items = [...(listRef.current?.querySelectorAll<HTMLButtonElement>("button[data-source]") ?? [])];
      const idx = items.findIndex((el) => el === document.activeElement);
      const next = e.key === "ArrowDown" ? Math.min(items.length - 1, idx + 1) : Math.max(0, idx - 1);
      items[next]?.focus();
      e.preventDefault();
    }
  };

  const total = windows.length + screens.length;
  return (
    <div
      className="absolute inset-0 z-20 bg-bg-secondary/95 backdrop-blur-sm flex flex-col overlay-no-drag"
      role="dialog"
      aria-modal="true"
      aria-label="Quelle wählen"
      data-testid="source-picker"
      onKeyDown={onKey}
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <span className="text-xs font-semibold flex-1">Quelle wählen</span>
        {preselected && (
          <span className="text-[10px] text-text-secondary truncate max-w-[200px]" data-testid="picker-hint">
            Vordergrund: {"title" in preselected ? preselected.title : preselected.name}
          </span>
        )}
        <button type="button" onClick={onRefresh} aria-label="Quellen neu laden" className="text-text-secondary hover:text-text-primary" disabled={loading}>
          <RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />
        </button>
        {canClose && (
          <button type="button" onClick={onClose} aria-label="Schließen" className="text-text-secondary hover:text-text-primary">
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      <div ref={listRef} className="flex-1 min-h-0 overflow-y-auto px-2 py-2 flex flex-col gap-0.5">
        {loading && total === 0 ? (
          <div className="flex justify-center py-6">
            <LoadingSpinner size="sm" />
          </div>
        ) : total === 0 ? (
          <div className="text-xs text-text-secondary px-2 py-4 text-center">Keine Fenster oder Bildschirme gefunden.</div>
        ) : (
          <>
            {windows.length > 0 && <div className="px-2 pt-1 pb-0.5 text-[10px] uppercase tracking-wider text-text-secondary/70">Fenster</div>}
            {windows.map((w) => (
              <SourceRow key={w.id} source={w} preselected={w.id === preselectedId} onChoose={onChoose} />
            ))}
            {screens.length > 0 && <div className="px-2 pt-2 pb-0.5 text-[10px] uppercase tracking-wider text-text-secondary/70">Bildschirme</div>}
            {screens.map((s) => (
              <SourceRow key={s.id} source={s} preselected={s.id === preselectedId} onChoose={onChoose} />
            ))}
          </>
        )}
      </div>
      <div className="px-3 py-1.5 border-t border-border text-[10px] text-text-secondary">
        Enter bestätigt die markierte Quelle · Pfeiltasten wechseln{canClose ? " · Esc schließt" : ""}
      </div>
    </div>
  );
}

function SourceRow({ source, preselected, onChoose }: { source: WindowSource | ScreenSource; preselected: boolean; onChoose: (s: LiveSource) => void }) {
  const isWindow = "process" in source;
  const title = isWindow ? source.title || source.process : source.name;
  const sub = isWindow ? `${source.process}${source.minimized ? " · minimiert" : ""}` : `${source.rect.w}×${source.rect.h}${source.primary ? " · primär" : ""}`;
  return (
    <button
      type="button"
      data-source={source.id}
      data-preselected={preselected ? "true" : undefined}
      onClick={() => onChoose(source)}
      className={cn(
        "w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left border transition-colors",
        preselected ? "border-accent-blue bg-accent-blue/10" : "border-transparent hover:bg-white/5 focus:bg-white/5",
      )}
    >
      {isWindow ? <AppWindow className="w-3.5 h-3.5 text-accent-cyan flex-shrink-0" /> : <Monitor className="w-3.5 h-3.5 text-accent-purple flex-shrink-0" />}
      <span className="min-w-0 flex-1">
        <span className="block text-xs truncate text-text-primary">{title}</span>
        <span className="block text-[10px] text-text-secondary truncate">{sub}</span>
      </span>
      {preselected && <span className="text-[10px] text-accent-blue flex-shrink-0">Vordergrund</span>}
    </button>
  );
}
