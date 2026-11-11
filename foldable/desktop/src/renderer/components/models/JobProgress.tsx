import { Pause, Play, X } from "lucide-react";
import type { Job } from "../../api/types";
import { formatBytes, formatEta, formatSpeed } from "../../lib/format";
import { JOB_KIND_LABEL, JOB_STATE_LABEL, jobPhaseText } from "../../lib/labels";
import { Badge, Button, ProgressBar } from "../ui";

export default function JobProgress({
  job,
  onCancel,
  onResume,
  title,
}: {
  job: Job;
  onCancel?: () => void;
  onResume?: () => void;
  title?: string;
}) {
  const { done_bytes, total_bytes, speed_bps, eta_seconds } = job.progress;
  const running = job.state === "queued" || job.state === "running";
  const canResume = job.kind === "download" && (job.state === "paused" || job.state === "failed");
  const pct = total_bytes > 0 ? (done_bytes / total_bytes) * 100 : job.state === "done" ? 100 : 0;
  const tone =
    job.state === "failed" || job.state === "cancelled" ? "red" : job.state === "done" ? "green" : job.state === "paused" ? "yellow" : "blue";
  const phaseText = running ? jobPhaseText(job.phase, job.kind) : job.message_de || JOB_STATE_LABEL[job.state];

  return (
    <div className="rounded-lg border border-border bg-bg-primary/70 p-3 text-xs" data-testid="job-progress">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-text-primary truncate">{title ?? JOB_KIND_LABEL[job.kind]}</span>
        <Badge tone={tone}>{JOB_STATE_LABEL[job.state]}</Badge>
      </div>
      <div className="mt-1.5 text-text-secondary">{phaseText}</div>
      {running && job.message_de && job.message_de !== phaseText && (
        <div className="mt-0.5 text-text-secondary/80">{job.message_de}</div>
      )}

      {total_bytes > 0 ? (
        <>
          <ProgressBar value={pct} tone={tone === "red" ? "red" : tone === "green" ? "green" : "blue"} className="mt-2.5" />
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-text-secondary tabular-nums">
            <span className="text-text-primary">
              {formatBytes(done_bytes)} von {formatBytes(total_bytes)}
            </span>
            <span>{Math.floor(pct)} %</span>
            {running && speed_bps > 0 && <span>{formatSpeed(speed_bps)}</span>}
            {running && eta_seconds !== null && <span>Restzeit {formatEta(eta_seconds)}</span>}
          </div>
        </>
      ) : (
        running && (
          <div className="mt-2.5 h-1.5 w-full rounded-full bg-white/5 overflow-hidden">
            <div className="h-full w-1/3 rounded-full bg-accent-blue/70 animate-pulse" />
          </div>
        )
      )}

      {job.error && (
        <div className="mt-2 rounded-md border border-red-500/30 bg-red-500/10 px-2.5 py-2 text-red-200">
          {job.error.detail}
          {job.kind === "activate" && <div className="mt-1 text-red-200/80">Vorheriges Modell bleibt aktiv.</div>}
        </div>
      )}

      {(running || canResume) && (onCancel || onResume) && (
        <div className="mt-2.5 flex items-center gap-2">
          {running && onCancel && (
            <Button size="sm" variant="secondary" icon={<X className="w-3 h-3" />} onClick={onCancel}>
              Abbrechen
            </Button>
          )}
          {canResume && onResume && (
            <Button size="sm" variant="primary" icon={<Play className="w-3 h-3" />} onClick={onResume}>
              Fortsetzen
            </Button>
          )}
          {job.state === "paused" && <Pause className="w-3 h-3 text-text-secondary" />}
        </div>
      )}
    </div>
  );
}
