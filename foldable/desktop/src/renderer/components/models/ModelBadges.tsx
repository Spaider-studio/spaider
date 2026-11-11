/**
 * Small shared badges for model cards: verification status, license kind and
 * the gated / account warning. Used by catalog cards, recommendation cards,
 * resolve results and installed rows.
 */
import { Lock } from "lucide-react";
import type { License, Measured, Verification, VerificationStatus } from "../../api/types";
import { formatDate, formatSeconds, formatTps } from "../../lib/format";
import { backendLabel, LICENSE_KIND_LABEL, VERIFICATION_LABEL, VERIFICATION_TITLE } from "../../lib/labels";
import { Badge } from "../ui";

const VERIFICATION_TONE: Record<VerificationStatus, "green" | "blue" | "neutral"> = {
  executed: "green",
  metadata: "blue",
  unverified: "neutral",
};

export function VerificationBadge({ verification, support }: { verification?: Verification | null; support?: "executed" | "documented" }) {
  // v1 cores only have `support`; map it to the closest v1.1 status.
  const status: VerificationStatus = verification?.status ?? (support === "executed" ? "executed" : "unverified");
  const title = [VERIFICATION_TITLE[status], verification?.date ? `am ${formatDate(verification.date)}` : null, verification?.scope]
    .filter(Boolean)
    .join(" · ");
  return (
    <Badge tone={VERIFICATION_TONE[status]} title={title}>
      {VERIFICATION_LABEL[status]}
    </Badge>
  );
}

export function LicenseBadges({ license }: { license: License | null | undefined }) {
  if (!license) return null;
  const gated = Boolean(license.gated || license.requires_account);
  return (
    <>
      {license.kind && (
        <Badge tone={license.kind === "open-source" ? "green" : license.kind === "open-weights" ? "cyan" : "neutral"} title={license.name}>
          {LICENSE_KIND_LABEL[license.kind]}
        </Badge>
      )}
      {gated && (
        <Badge tone="yellow" title="Download nur mit Hugging-Face-Konto und Zustimmung zu den Bedingungen des Herstellers">
          <Lock className="w-3 h-3" /> {license.requires_account ? "Konto erforderlich" : "Zustimmung erforderlich"}
        </Badge>
      )}
    </>
  );
}

/** "gemessen auf diesem Rechner: 24 Token/s" – or the honest "noch nicht gemessen". */
export function MeasuredLine({ measured, className }: { measured: Measured | null | undefined; className?: string }) {
  if (!measured) {
    return (
      <div className={className ?? "text-xs text-text-secondary/70"} data-testid="measured-none">
        noch nicht gemessen
      </div>
    );
  }
  const details: string[] = [];
  if (measured.backend) details.push(backendLabel(measured.backend));
  if (measured.time_to_first_token_ms > 0) details.push(`erstes Token nach ${formatSeconds(measured.time_to_first_token_ms)}`);
  if (measured.prompt_tokens_per_second > 0) details.push(`Kontext ${formatTps(measured.prompt_tokens_per_second)}`);
  return (
    <div className={className ?? "text-xs"} data-testid="measured">
      <span className="text-emerald-300">gemessen auf diesem Rechner: {formatTps(measured.tokens_per_second)}</span>
      {details.length > 0 && <span className="text-text-secondary"> ({details.join(", ")})</span>}
      {measured.measured_at && <span className="text-text-secondary/70"> · {formatDate(measured.measured_at)}</span>}
    </div>
  );
}

export function GatedNotice({ license }: { license: License | null | undefined }) {
  if (!license || !(license.gated || license.requires_account)) return null;
  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200" data-testid="gated-notice">
      Dieses Modell ist auf Hugging Face zugangsbeschränkt: Der Download setzt ein Konto und die Zustimmung zu den
      Bedingungen des Herstellers voraus. SpAIder lädt nur frei zugängliche Dateien.
    </div>
  );
}
