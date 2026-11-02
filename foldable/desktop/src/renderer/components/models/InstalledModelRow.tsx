/**
 * Installed model with v1.1 details: capabilities summary, verification,
 * measurement on this machine and the "Kurz messen" calibration job.
 */
import { CheckCircle2, Timer } from "lucide-react";
import type { Job, ModelInstalled } from "../../api/types";
import { formatBytes, formatDateTime, formatTokens } from "../../lib/format";
import { backendLabel } from "../../lib/labels";
import { Badge, Button } from "../ui";
import JobProgress from "./JobProgress";
import { MeasuredLine, VerificationBadge } from "./ModelBadges";

export function capabilitiesText(model: ModelInstalled): string | null {
  const cap = model.capabilities;
  if (!cap) return null;
  const parts = [
    `Kontext ${formatTokens(cap.context_length || model.context_length)} Token`,
    `Denken: ${cap.reasoning_toggle ? "ja" : "nein"}`,
    `Backends: ${cap.backends.length ? cap.backends.map(backendLabel).join(", ") : "–"}`,
  ];
  if (!cap.chat_template && model.role === "chat") parts.push("keine Chat-Vorlage");
  if (!cap.system_role) parts.push("keine Systemrolle");
  return parts.join(" · ");
}

export default function InstalledModelRow({
  model,
  busy,
  deleting,
  calibration,
  onActivate,
  onDelete,
  onCalibrate,
  onCancelCalibration,
}: {
  model: ModelInstalled;
  busy: boolean;
  deleting: boolean;
  calibration?: Job | null;
  onActivate: () => void;
  onDelete: () => void;
  onCalibrate: () => void;
  onCancelCalibration: () => void;
}) {
  const caps = capabilitiesText(model);
  const calibrating = Boolean(calibration && (calibration.state === "queued" || calibration.state === "running"));
  return (
    <li className="panel p-3 flex flex-col gap-2" data-testid="installed-model">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium truncate">{model.name}</span>
            <Badge tone={model.role === "chat" ? "blue" : "purple"}>{model.role === "chat" ? "Chat" : "Embedding"}</Badge>
            {model.active && (
              <Badge tone="green">
                <CheckCircle2 className="w-3 h-3" /> Aktiv
              </Badge>
            )}
            {model.state === "broken" && <Badge tone="red">Defekt</Badge>}
            {model.source === "import" && <Badge tone="neutral">Import</Badge>}
            {(model.verification || model.capabilities) && <VerificationBadge verification={model.verification} />}
          </div>
          <div className="text-[11px] text-text-secondary mt-0.5 flex flex-wrap gap-x-3">
            <span className="font-mono">{model.quantization}</span>
            <span>{formatBytes(model.size_bytes)}</span>
            {!caps && <span>{formatTokens(model.context_length)} Token Kontext</span>}
            <span>{model.sha256_verified ? "Prüfsumme geprüft" : "Prüfsumme nicht geprüft"}</span>
            <span>{formatDateTime(model.created_at)}</span>
          </div>
          {caps && (
            <div className="text-[11px] text-text-secondary mt-0.5" data-testid="capabilities">
              {caps}
            </div>
          )}
          {model.role === "chat" && (
            <div className="mt-1 flex items-center gap-3 flex-wrap">
              <MeasuredLine measured={model.measured} className="text-[11px]" />
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {model.role === "chat" && model.state === "ready" && !calibrating && (
            <Button size="sm" variant="ghost" icon={<Timer className="w-3.5 h-3.5" />} onClick={onCalibrate} disabled={busy} title="Misst Token/s mit einem synthetischen Prompt auf diesem Rechner">
              Kurz messen
            </Button>
          )}
          {!model.active && model.state === "ready" && (
            <Button size="sm" variant="primary" onClick={onActivate} disabled={busy || calibrating}>
              Aktivieren
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={onDelete}
            disabled={model.active || busy || calibrating}
            loading={deleting}
            title={model.active ? "Aktive Modelle können nicht gelöscht werden" : undefined}
          >
            Löschen
          </Button>
        </div>
      </div>
      {calibration && (
        <JobProgress job={calibration} title="Kurze Messung" onCancel={calibrating ? onCancelCalibration : undefined} />
      )}
    </li>
  );
}
