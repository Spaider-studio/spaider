import { CheckCircle2, Download, ExternalLink } from "lucide-react";
import { isAllowedExternalUrl } from "../../../shared-types/bridge";
import type { Job, ModelCatalogEntry } from "../../api/types";
import { formatBytes, formatTokens } from "../../lib/format";
import { FIT_LABEL } from "../../lib/labels";
import { Badge, Button } from "../ui";
import JobProgress from "./JobProgress";

const FIT_TONE = { recommended: "green", ok: "blue", tight: "yellow", too_large: "red", unknown: "neutral" } as const;

function ExternalButton({ url, children }: { url: string; children: React.ReactNode }) {
  if (!isAllowedExternalUrl(url)) return <span className="text-text-secondary">{children}</span>;
  return (
    <button
      type="button"
      title={url}
      onClick={() => void window.spaider.openExternal(url)}
      className="inline-flex items-center gap-1 text-accent-blue hover:text-blue-400 transition-colors"
    >
      {children}
      <ExternalLink className="w-3 h-3" />
    </button>
  );
}

export default function CatalogCard({
  entry,
  job,
  busy,
  onDownload,
  onCancel,
  onResume,
}: {
  entry: ModelCatalogEntry;
  job?: Job | null;
  busy?: boolean;
  onDownload: () => void;
  onCancel?: () => void;
  onResume?: () => void;
}) {
  const installed = Boolean(entry.installed_model_id);
  const tooLarge = entry.fit.level === "too_large";

  return (
    <div className="panel p-4 flex flex-col gap-3 animate-fade-in" data-testid="catalog-card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="text-sm font-semibold text-text-primary truncate">{entry.name}</h4>
          <div className="text-xs text-text-secondary mt-0.5 truncate">
            {entry.publisher} · {entry.repo}
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-1 flex-shrink-0">
          {entry.source === "official" ? (
            <Badge tone="blue">Offiziell</Badge>
          ) : (
            <Badge tone="yellow" title="Von der Community konvertiertes GGUF, nicht vom Hersteller">
              Community-Konvertierung
            </Badge>
          )}
          {entry.support === "executed" ? (
            <Badge tone="green" title="Auf dieser Laufzeit ausgeführt und geprüft">
              getestet
            </Badge>
          ) : (
            <Badge tone="neutral" title="Laut Dokumentation unterstützt, nicht ausgeführt">
              dokumentiert
            </Badge>
          )}
        </div>
      </div>

      {entry.description_de && <p className="text-xs text-text-secondary leading-relaxed">{entry.description_de}</p>}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <dt className="text-text-secondary">Parameter</dt>
        <dd className="text-text-primary">{entry.parameters}</dd>
        <dt className="text-text-secondary">Quantisierung</dt>
        <dd className="text-text-primary font-mono">{entry.quantization}</dd>
        <dt className="text-text-secondary">Kontextlänge</dt>
        <dd className="text-text-primary">{formatTokens(entry.context_length)} Token</dd>
        <dt className="text-text-secondary">Größe</dt>
        <dd className="text-text-primary">{formatBytes(entry.total_bytes)}</dd>
        {entry.embedding_dimensions !== null && (
          <>
            <dt className="text-text-secondary">Dimensionen</dt>
            <dd className="text-text-primary">{entry.embedding_dimensions}</dd>
          </>
        )}
        <dt className="text-text-secondary">Lizenz</dt>
        <dd>
          <ExternalButton url={entry.license.url}>{entry.license.name}</ExternalButton>
        </dd>
      </dl>

      <div className="flex items-center gap-2 text-xs">
        <Badge tone={FIT_TONE[entry.fit.level]}>{FIT_LABEL[entry.fit.level]}</Badge>
        <span className="text-text-secondary">{entry.fit.message_de}</span>
      </div>

      {job ? (
        <JobProgress job={job} onCancel={onCancel} onResume={onResume} />
      ) : (
        <div className="flex items-center justify-between gap-3 pt-1">
          <ExternalButton url={entry.repo_url}>Auf Hugging Face ansehen</ExternalButton>
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
              disabled={busy || tooLarge}
              title={tooLarge ? entry.fit.message_de : undefined}
            >
              Herunterladen
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
