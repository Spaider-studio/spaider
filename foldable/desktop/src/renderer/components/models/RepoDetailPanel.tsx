/**
 * Repository detail (GET /models/repo + /models/card): variants grouped by
 * kind (weights / projector / auxiliary) with quantization, size, parts and
 * fit, the projector choice when the weights need one, the model card as
 * safe Markdown (no raw HTML) and the download through the existing
 * resolve -> download flow (only the chosen file + its projector).
 */
import { ExternalLink, Lock, WifiOff, X } from "lucide-react";
import { useEffect, useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import { errorMessage } from "../../api/client";
import type { RepoVariant, ResolveResponse } from "../../api/types";
import { formatBytes, formatDate, formatDateTime } from "../../lib/format";
import { notify } from "../../lib/notify";
import { FIT_TEXT, FIT_TONE, groupVariants, variantDownloadState, variantPrimaryFile } from "../../store/hub";
import { useAppStore } from "../../store/useAppStore";
import { useHubStore } from "../../store/useHubStore";
import Markdown from "../chat/Markdown";
import { Badge, Button, Select } from "../ui";
import { ResolveResult } from "./ModelSearchPanel";
import type { ModelJobs } from "./useModelJobs";

function VariantRow({ variant, selected, onSelect, selectable }: { variant: RepoVariant; selected: boolean; onSelect: () => void; selectable: boolean }) {
  return (
    <li>
      <label className={cn("flex items-start gap-2.5 px-3 py-2", selectable && "cursor-pointer hover:bg-white/5", selected && "bg-accent-blue/5")} data-testid="variant-row" data-variant={variant.key} data-kind={variant.kind}>
        {selectable ? <input type="radio" name="variant" checked={selected} onChange={onSelect} className="mt-1" aria-label={variant.key} /> : <span className="w-3" />}
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-mono text-text-primary">{variant.quantization || variant.key}</span>
            <span className="text-xs text-text-secondary tabular-nums">{formatBytes(variant.bytes)}</span>
            {variant.parts > 1 && <Badge tone="neutral">{variant.parts} Teile</Badge>}
            {variant.fit && (
              <Badge tone={FIT_TONE[variant.fit]} title={variant.fit_note_de}>
                {FIT_TEXT[variant.fit]}
              </Badge>
            )}
            {variant.needs_projector && <Badge tone="purple">braucht Projektor</Badge>}
          </span>
          <span className="block text-[11px] text-text-secondary/80 font-mono truncate">{variant.files.map((f) => f.path).join(", ")}</span>
        </span>
      </label>
    </li>
  );
}

export default function RepoDetailPanel({ jobs, busy }: { jobs: ModelJobs; busy: boolean }) {
  const client = useAppStore((s) => s.client);
  const repo = useHubStore((s) => s.repo);
  const closeRepo = useHubStore((s) => s.closeRepo);
  const [variantKey, setVariantKey] = useState<string | null>(null);
  const [projectorKey, setProjectorKey] = useState<string | null>(null);
  const [resolved, setResolved] = useState<ResolveResponse | null>(null);
  const [resolving, setResolving] = useState(false);
  const [cardOpen, setCardOpen] = useState(false);

  useEffect(() => {
    setVariantKey(null);
    setProjectorKey(null);
    setResolved(null);
  }, [repo?.repo]);

  if (!repo) return null;
  const { detail, card } = repo;
  const groups = detail ? groupVariants(detail.variants) : { weights: [], projectors: [], auxiliary: [] };
  const variant = detail?.variants.find((v) => v.key === variantKey) ?? null;
  const state = variantDownloadState(variant, projectorKey, Boolean(detail?.installed));
  const projectorChoices = variant?.projector_options ?? [];

  const resolve = async () => {
    if (!client || !detail || !variant) return;
    const file = variantPrimaryFile(variant);
    setResolving(true);
    try {
      setResolved(await client.resolveModel(detail.repo, file));
    } catch (e) {
      notify(`Auflösen fehlgeschlagen: ${errorMessage(e)}`, "error");
    } finally {
      setResolving(false);
    }
  };
  const resolvedJob = resolved ? jobs.jobsByCatalog[resolved.entry.id] : undefined;

  return (
    <div className="panel p-4 flex flex-col gap-3 self-start" data-testid="repo-detail" data-repo={repo.repo}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="text-sm font-semibold font-mono text-text-primary break-all">{repo.repo}</h4>
          {detail && (
            <div className="mt-1 flex items-center gap-1.5 flex-wrap text-[11px] text-text-secondary">
              <Badge tone={/^offiziell/i.test(detail.publisher_status) ? "green" : "neutral"}>{detail.publisher_status}</Badge>
              {detail.gated && (
                <Badge tone="yellow">
                  <Lock className="w-3 h-3" /> Freischaltung {detail.gated === "manual" ? "manuell" : "automatisch"}
                </Badge>
              )}
              {detail.installed && <Badge tone="green">installiert</Badge>}
              {detail.license && <span>Lizenz: {detail.license}</span>}
              {detail.base_model && <span>· Basis: {detail.base_model}</span>}
              {detail.last_modified && <span>· geändert {formatDate(detail.last_modified)}</span>}
              <span>· {formatBytes(detail.total_gguf_bytes)} GGUF gesamt</span>
            </div>
          )}
        </div>
        <button type="button" onClick={closeRepo} aria-label="Schließen" className="text-text-secondary hover:text-text-primary">
          <X className="w-4 h-4" />
        </button>
      </div>

      {repo.loading ? (
        <div className="flex justify-center py-6">
          <LoadingSpinner size="sm" />
        </div>
      ) : repo.error ? (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200" data-testid="repo-error">
          {repo.error}
          {repo.requiresAccount && <div className="mt-1 text-amber-200">Dieses Repository erfordert eine Freischaltung bei Hugging Face; SpAIder lädt keine zugangsbeschränkten Modelle.</div>}
        </div>
      ) : detail ? (
        <>
          {detail.offline && (
            <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              <WifiOff className="w-3.5 h-3.5" /> Offline – gespeicherte Angaben{detail.cached_at ? ` vom ${formatDateTime(detail.cached_at)}` : ""}
            </div>
          )}
          <section>
            <div className="text-xs font-semibold uppercase tracking-wider text-text-secondary mb-1.5">Gewichte</div>
            {groups.weights.length === 0 ? (
              <div className="text-xs text-text-secondary px-1">Keine GGUF-Gewichte in diesem Repository.</div>
            ) : (
              <ul className="rounded-lg border border-border divide-y divide-border/60">
                {groups.weights.map((v) => (
                  <VariantRow
                    key={v.key}
                    variant={v}
                    selectable
                    selected={variantKey === v.key}
                    onSelect={() => {
                      setVariantKey(v.key);
                      setProjectorKey(v.projector_options[0] ?? null);
                      setResolved(null);
                    }}
                  />
                ))}
              </ul>
            )}
          </section>
          {groups.projectors.length > 0 && (
            <section>
              <div className="text-xs font-semibold uppercase tracking-wider text-text-secondary mb-1.5">Projektor (Bildeingabe)</div>
              <ul className="rounded-lg border border-border divide-y divide-border/60">
                {groups.projectors.map((v) => (
                  <VariantRow key={v.key} variant={v} selectable={false} selected={projectorKey === v.key} onSelect={() => undefined} />
                ))}
              </ul>
              {variant?.needs_projector && (
                <div className="mt-2 flex items-center justify-between gap-3 text-xs text-text-secondary">
                  <span>Projektor für die gewählte Variante</span>
                  <Select value={projectorKey ?? ""} onChange={setProjectorKey} options={projectorChoices.map((k) => ({ value: k, label: k }))} ariaLabel="Projektor" />
                </div>
              )}
            </section>
          )}
          {groups.auxiliary.length > 0 && (
            <section>
              <div className="text-xs font-semibold uppercase tracking-wider text-text-secondary mb-1.5">Hilfsdateien</div>
              <ul className="rounded-lg border border-border divide-y divide-border/60">
                {groups.auxiliary.map((v) => (
                  <VariantRow key={v.key} variant={v} selectable={false} selected={false} onSelect={() => undefined} />
                ))}
              </ul>
            </section>
          )}

          <div className="flex items-center gap-3 flex-wrap" data-testid="repo-actions">
            {state.can ? (
              <Button variant="primary" onClick={() => void resolve()} loading={resolving} disabled={busy || Boolean(resolved)} data-testid="repo-resolve">
                {resolved ? "Geprüft" : "Variante prüfen und laden"}
              </Button>
            ) : (
              <span className="text-xs text-text-secondary" data-testid="repo-download-blocked">
                {state.reason}
              </span>
            )}
            {card?.url && (
              <Button size="sm" variant="ghost" icon={<ExternalLink className="w-3.5 h-3.5" />} onClick={() => void window.spaider?.openExternal(card.url)} title={card.url}>
                Modellseite öffnen
              </Button>
            )}
          </div>
          {resolved && (
            <ResolveResult
              result={resolved}
              job={resolvedJob}
              busy={busy}
              onDownload={() => void jobs.download(resolved.entry)}
              onCancel={resolvedJob ? () => void jobs.cancelJob(resolvedJob) : undefined}
              onResume={resolvedJob ? () => void jobs.resumeJob(resolvedJob) : undefined}
            />
          )}

          {card && (
            <section>
              <button type="button" onClick={() => setCardOpen((v) => !v)} aria-expanded={cardOpen} className="text-xs text-text-secondary hover:text-text-primary">
                {cardOpen ? "Modellkarte ausblenden" : "Modellkarte anzeigen"}
                {card.truncated ? " (gekürzt)" : ""}
              </button>
              {cardOpen && (
                <div className="mt-2 rounded-lg border border-border bg-bg-primary/40 p-3 text-sm max-h-80 overflow-y-auto" data-testid="model-card">
                  {card.excerpt ? <Markdown content={card.excerpt} /> : <span className="text-xs text-text-secondary">Keine Modellkarte vorhanden.</span>}
                </div>
              )}
            </section>
          )}
        </>
      ) : null}
    </div>
  );
}
