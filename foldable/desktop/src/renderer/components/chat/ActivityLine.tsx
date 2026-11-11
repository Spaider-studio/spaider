/**
 * Live activity line under the streaming assistant message (ACTIVITY_EVENTS.md).
 * Shows only what the events say: current step (`message_de`), measured
 * percent for prompt_processing, Token/s while generating, the elapsed time
 * since the first event, and a "Details" list of every accepted event.
 */
import { ChevronDown, ChevronRight, Square } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import type { ActivityEvent } from "../../api/types";
import { formatSeconds } from "../../lib/format";
import {
  ACTIVITY_STATE_LABEL,
  COUNTER_LABEL,
  phaseLabel,
  promptPercent,
  summarizeActivity,
  type ActivityTrack,
} from "../../store/activity";

/** Elapsed ms since `startedAt`, refreshed every 250 ms while `running`. */
function useElapsed(startedAt: number, running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [running, startedAt]);
  return Math.max(0, now - startedAt);
}

function counterText(ev: ActivityEvent): string {
  const c = ev.counters;
  if (!c) return "";
  return Object.entries(c)
    .filter(([, v]) => typeof v === "number")
    .map(([k, v]) => `${COUNTER_LABEL[k] ?? k}: ${k === "tokens_per_second" ? (v as number).toLocaleString("de-DE", { maximumFractionDigits: 1 }) : v}`)
    .join(" · ");
}

export function ActivityDetails({ events }: { events: ActivityEvent[] }) {
  return (
    <ol className="mt-2 rounded-lg border border-border bg-bg-secondary/60 text-[11px] divide-y divide-border/60" data-testid="activity-details">
      {events.map((ev) => {
        const pct = promptPercent(ev);
        const failed = ev.state === "failed" || ev.phase === "failed";
        return (
          <li key={`${ev.seq}`} className="grid grid-cols-[3.5rem_1fr_auto] gap-x-3 px-3 py-1.5 items-baseline">
            <span className="tabular-nums text-text-secondary/70">{formatSeconds(ev.t_ms)}</span>
            <span className="min-w-0">
              <span className={cn("font-medium", failed ? "text-red-300" : "text-text-primary")}>{phaseLabel(ev.phase)}</span>
              <span className="text-text-secondary"> · {ACTIVITY_STATE_LABEL[ev.state] ?? ev.state}</span>
              {ev.message_de && <span className="block text-text-secondary truncate">{ev.message_de}</span>}
              {(ev.counters || pct !== null) && (
                <span className="block text-text-secondary/80 tabular-nums">
                  {counterText(ev)}
                  {pct !== null && ` · ${pct} %`}
                </span>
              )}
              {ev.source_ids && ev.source_ids.length > 0 && (
                <span className="block text-text-secondary/70 truncate">Quellen: {ev.source_ids.join(", ")}</span>
              )}
            </span>
            <span className="text-text-secondary/50 tabular-nums">#{ev.seq}</span>
          </li>
        );
      })}
    </ol>
  );
}

export default function ActivityLine({
  track,
  live,
  stopping,
  onStop,
  errorText,
}: {
  track: ActivityTrack;
  /** The chat stream is still open (shows the elapsed counter and Stopp). */
  live: boolean;
  /** Stopp was clicked locally and the core has not confirmed yet. */
  stopping: boolean;
  onStop?: () => void;
  /** The answer ended with an `error` event: forces the failed presentation (never "Antwort fertig"). */
  errorText?: string;
}) {
  const [open, setOpen] = useState(false);
  const failedByError = Boolean(errorText) && track.outcome !== "cancelled";
  const running = live && track.outcome === "live" && !failedByError;
  const elapsed = useElapsed(track.startedAt, running);
  const base = summarizeActivity(track);
  const summary = failedByError && base.outcome !== "failed" ? { ...base, text: errorText as string, outcome: "failed" as const } : base;

  let text = summary.text;
  if (running && stopping) text = "Wird gestoppt …";

  const tone =
    summary.outcome === "failed"
      ? "text-red-300"
      : summary.outcome === "cancelled"
        ? "text-text-secondary"
        : running
          ? "text-text-secondary"
          : "text-text-secondary/80";

  return (
    <div className="mt-2 text-[11px]" data-testid="activity-line" data-outcome={summary.outcome} aria-live="polite">
      <div className="flex items-center gap-2 flex-wrap min-h-[20px]">
        <span
          className={cn(
            "block w-1.5 h-1.5 rounded-full flex-shrink-0",
            summary.outcome === "failed"
              ? "bg-accent-red"
              : summary.outcome === "cancelled"
                ? "bg-text-secondary/60"
                : running
                  ? "bg-accent-blue activity-dot"
                  : "bg-accent-green",
          )}
          aria-hidden="true"
        />
        <span className={cn("truncate max-w-[60%]", tone)} data-testid="activity-text">
          {text}
        </span>
        {running && (
          <span className="tabular-nums text-text-secondary/70" data-testid="activity-elapsed">
            {formatSeconds(elapsed)}
          </span>
        )}
        {summary.memory && (
          <span className={cn("text-text-secondary/80", track.postprocess === "failed" && "text-amber-300")} data-testid="activity-memory">
            · {summary.memory}
          </span>
        )}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="inline-flex items-center gap-0.5 text-text-secondary hover:text-text-primary transition-colors"
        >
          {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          Details
        </button>
        {running && onStop && (
          <button
            type="button"
            onClick={onStop}
            disabled={stopping}
            title="Stopp (Esc)"
            className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-text-secondary hover:text-red-300 hover:border-red-500/40 transition-colors disabled:opacity-50"
          >
            <Square className="w-2.5 h-2.5" /> Stopp
          </button>
        )}
      </div>
      {open && <ActivityDetails events={track.events} />}
    </div>
  );
}
