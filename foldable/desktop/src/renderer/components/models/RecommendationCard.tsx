/**
 * One recommendation pick (slot Ausgewogen / Schnell und sparsam / Mehr
 * Qualität). Everything shown comes from the core's answer: reasons, size,
 * RAM/VRAM estimate, verification, license, and a measurement only when the
 * core actually measured this model on this machine.
 */
import { CheckCircle2, Download, Gauge, Scale, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import type { Job, RecommendationPick, RecommendationSlot } from "../../api/types";
import { formatBytes, formatTokens } from "../../lib/format";
import { FIT_LABEL, SLOT_LABEL } from "../../lib/labels";
import { Badge, Button } from "../ui";
import JobProgress from "./JobProgress";
import { GatedNotice, LicenseBadges, MeasuredLine, VerificationBadge } from "./ModelBadges";

const SLOT_ICON: Record<RecommendationSlot, ReactNode> = {
  balanced: <Scale className="w-3.5 h-3.5 text-accent-blue" />,
  fast: <Gauge className="w-3.5 h-3.5 text-accent-green" />,
  quality: <Sparkles className="w-3.5 h-3.5 text-accent-purple" />,
};
const FIT_TONE = { recommended: "green", ok: "blue", tight: "yellow", too_large: "red", unknown: "neutral" } as const;

export default function RecommendationCard({
  pick,
  job,
  busy,
  onDownload,
  onCancel,
  onResume,
}: {
  pick: RecommendationPick;
  job?: Job | null;
  busy?: boolean;
  onDownload: () => void;
  onCancel?: () => void;
  onResume?: () => void;
}) {
  const { entry, estimate } = pick;
  const installed = Boolean(pick.installed_model_id ?? entry.installed_model_id);
  const gated = Boolean(entry.license.gated || entry.license.requires_account);
  const tooLarge = entry.fit.level === "too_large";

  return (
    <div className="panel p-4 flex flex-col gap-3 animate-fade-in" data-testid="recommendation-card" data-slot={pick.slot}>
      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-text-secondary">
        {SLOT_ICON[pick.slot]}
        {SLOT_LABEL[pick.slot]}
      </div>
      <div>
        <h4 className="text-sm font-semibold text-text-primary">{entry.name}</h4>
        <div className="text-xs text-text-secondary mt-0.5 truncate">
          {entry.publisher} · {entry.repo}
        </div>
      </div>
      <div className="flex flex-wrap gap-1">
        <VerificationBadge verification={entry.verification} support={entry.support} />
        <LicenseBadges license={entry.license} />
        <Badge tone={FIT_TONE[entry.fit.level]} title={entry.fit.message_de}>
          {FIT_LABEL[entry.fit.level]}
        </Badge>
      </div>

      {pick.reasons_de.length > 0 && (
        <ul className="text-xs text-text-secondary leading-relaxed list-disc pl-4 flex flex-col gap-0.5" data-testid="reasons">
          {pick.reasons_de.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <dt className="text-text-secondary">Download</dt>
        <dd className="text-text-primary">{formatBytes(estimate.download_bytes)}</dd>
        <dt className="text-text-secondary">Arbeitsspeicher (geschätzt)</dt>
        <dd className="text-text-primary">{formatBytes(estimate.ram_bytes)}</dd>
        {estimate.vram_bytes > 0 && (
          <>
            <dt className="text-text-secondary">Grafikspeicher (geschätzt)</dt>
            <dd className="text-text-primary">{formatBytes(estimate.vram_bytes)}</dd>
          </>
        )}
        <dt className="text-text-secondary">Kontext</dt>
        <dd className="text-text-primary">{formatTokens(estimate.context_tokens)} Token</dd>
        <dt className="text-text-secondary">Quantisierung</dt>
        <dd className="text-text-primary font-mono">{entry.quantization}</dd>
      </dl>

      <MeasuredLine measured={pick.measured} />
      <GatedNotice license={entry.license} />

      {job ? (
        <JobProgress job={job} onCancel={onCancel} onResume={onResume} />
      ) : (
        <div className="flex items-center justify-between gap-3 pt-1">
          <span className="text-xs text-text-secondary">{entry.fit.message_de}</span>
          {installed ? (
            <span className="inline-flex items-center gap-1.5 text-xs text-emerald-300">
              <CheckCircle2 className="w-3.5 h-3.5" /> Installiert
            </span>
          ) : (
            <Button
              variant="primary"
              size="sm"
              icon={<Download className="w-3.5 h-3.5" />}
              onClick={onDownload}
              disabled={busy || tooLarge || gated}
              title={tooLarge ? entry.fit.message_de : gated ? "Zugangsbeschränktes Modell – kein Download ohne Konto" : undefined}
            >
              Herunterladen
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
