/**
 * "Weitere Modelle": Hugging Face search (results are always "ungeprüft"),
 * repository-ID resolve (GGUF header check by the core, no code execution),
 * download of the resolved dynamic entry, and the catalog refresh button.
 */
import { AlertTriangle, Download, RefreshCw, Search, ShieldCheck, WifiOff } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import { errorMessage } from "../../api/client";
import type { CatalogRefreshResult, ModelSearchResponse, ResolveResponse } from "../../api/types";
import { formatBytes, formatDate, formatDateTime, formatNumber, formatTokens } from "../../lib/format";
import { FIT_LABEL, MODEL_TYPE_LABEL } from "../../lib/labels";
import { resolveErrorInfo, type ResolveErrorInfo } from "../../lib/modelErrors";
import { notify } from "../../lib/notify";
import { useAppStore } from "../../store/useAppStore";
import { Badge, Button, inputClass } from "../ui";
import JobProgress from "./JobProgress";
import { GatedNotice, LicenseBadges, VerificationBadge } from "./ModelBadges";
import type { ModelJobs } from "./useModelJobs";

const FIT_TONE = { recommended: "green", ok: "blue", tight: "yellow", too_large: "red", unknown: "neutral" } as const;
const REPO_RE = /^[\w.-]+\/[\w.-]+$/;

export function ResolveResult({
  result,
  job,
  busy,
  onDownload,
  onCancel,
  onResume,
}: {
  result: ResolveResponse;
  job?: Job | null;
  busy?: boolean;
  onDownload: () => void;
  onCancel?: () => void;
  onResume?: () => void;
}) {
  const { entry, files, compatibility, fit } = result;
  const gated = Boolean(entry.license.gated || entry.license.requires_account);
  const canDownload = compatibility.ok && fit.level !== "too_large" && !gated && !entry.installed_model_id;
  return (
    <div className="panel p-4 flex flex-col gap-3" data-testid="resolve-result">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="text-sm font-semibold text-text-primary">{entry.name}</h4>
          <div className="text-xs text-text-secondary mt-0.5 truncate">
            {entry.repo}
            {entry.revision && <span className="font-mono"> @ {entry.revision.slice(0, 8)}</span>}
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-1 flex-shrink-0">
          <VerificationBadge verification={entry.verification} support={entry.support} />
          <LicenseBadges license={entry.license} />
        </div>
      </div>

      <div
        className={cn(
          "rounded-md border px-3 py-2 text-xs",
          compatibility.ok ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200" : "border-red-500/30 bg-red-500/10 text-red-200",
        )}
        data-testid="compatibility"
      >
        <div className="flex items-center gap-1.5 font-medium">
          {compatibility.ok ? <ShieldCheck className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
          {compatibility.ok ? "Kompatibel mit der mitgelieferten Laufzeit" : "Nicht kompatibel"}
          <span className="font-normal opacity-80">
            · {compatibility.architecture} · {MODEL_TYPE_LABEL[compatibility.type] ?? compatibility.type} · {formatTokens(compatibility.context_length)} Token
          </span>
        </div>
        {compatibility.reasons_de.length > 0 && (
          <ul className="mt-1 list-disc pl-4 opacity-90">
            {compatibility.reasons_de.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex items-center gap-2 text-xs">
        <Badge tone={FIT_TONE[fit.level]}>{FIT_LABEL[fit.level]}</Badge>
        <span className="text-text-secondary">{fit.message_de}</span>
      </div>

      <div>
        <div className="text-xs font-medium text-text-secondary mb-1">
          Dateien ({files.length}) · gesamt {formatBytes(entry.total_bytes)}
        </div>
        <ul className="rounded-md border border-border divide-y divide-border/60 text-xs" data-testid="resolve-files">
          {files.map((f) => (
            <li key={f.path} className="flex items-center justify-between gap-3 px-3 py-1.5">
              <span className="font-mono truncate">{f.path}</span>
              <span className="flex items-center gap-3 flex-shrink-0 text-text-secondary tabular-nums">
                {formatBytes(f.size)}
                {f.sha256 ? (
                  <Badge tone="green" title={f.sha256}>
                    Prüfsumme vorhanden
                  </Badge>
                ) : (
                  <Badge tone="yellow">keine Prüfsumme</Badge>
                )}
              </span>
            </li>
          ))}
        </ul>
        {files.length > 1 && <div className="mt-1 text-[11px] text-text-secondary">Mehrteiliges Modell – alle Teile werden geladen.</div>}
      </div>

      <GatedNotice license={entry.license} />

      {job ? (
        <JobProgress job={job} onCancel={onCancel} onResume={onResume} />
      ) : (
        <div className="flex items-center justify-between gap-3 pt-1">
          <span className="text-[11px] text-text-secondary font-mono truncate">{entry.id}</span>
          {entry.installed_model_id ? (
            <span className="text-xs text-emerald-300">Installiert</span>
          ) : (
            <Button variant="primary" size="sm" icon={<Download className="w-3.5 h-3.5" />} onClick={onDownload} disabled={busy || !canDownload}>
              Herunterladen
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

type Job = NonNullable<ModelJobs["importJob"]>;

export default function ModelSearchPanel({ jobs, busy }: { jobs: ModelJobs; busy: boolean }) {
  const client = useAppStore((s) => s.client);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<ModelSearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [repo, setRepo] = useState("");
  const [file, setFile] = useState("");
  const [resolving, setResolving] = useState(false);
  const [resolved, setResolved] = useState<ResolveResponse | null>(null);
  const [resolveError, setResolveError] = useState<ResolveErrorInfo | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshResult, setRefreshResult] = useState<CatalogRefreshResult | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const repoInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const runSearch = async (e?: FormEvent) => {
    e?.preventDefault();
    const q = query.trim();
    if (!client || !q) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setSearching(true);
    setSearchError(null);
    try {
      setSearch(await client.searchModels(q, 20, controller.signal));
    } catch (err) {
      if (!controller.signal.aborted) setSearchError(errorMessage(err));
    } finally {
      if (!controller.signal.aborted) setSearching(false);
    }
  };

  const runResolve = async (repoId: string, fileName: string) => {
    if (!client) return;
    const r = repoId.trim();
    if (!REPO_RE.test(r)) {
      setResolved(null);
      setResolveError({ code: "invalid_repo", title: "Prüfung nicht möglich", text: "Bitte eine Repository-ID im Format „Autor/Name“ eingeben.", gated: false });
      return;
    }
    setResolving(true);
    setResolveError(null);
    setResolved(null);
    try {
      setResolved(await client.resolveModel(r, fileName.trim() || null));
    } catch (err) {
      setResolveError(resolveErrorInfo(err));
    } finally {
      setResolving(false);
    }
  };

  const pickResult = (repoId: string) => {
    setRepo(repoId);
    setFile("");
    repoInputRef.current?.focus();
    void runResolve(repoId, "");
  };

  const refresh = async () => {
    if (!client) return;
    setRefreshing(true);
    try {
      const result = await client.refreshCatalog();
      setRefreshResult(result);
      await jobs.reload();
      const errors = result.errors.length ? ` ${result.errors.length} Fehler.` : "";
      notify(
        `Katalog aktualisiert (Stand ${formatDate(result.catalog_date)}): ${result.checked} Einträge geprüft, ${result.changed} geändert.${errors}`,
        result.errors.length ? "error" : "success",
      );
    } catch (err) {
      notify(`Katalog konnte nicht aktualisiert werden: ${errorMessage(err)}`, "error");
    } finally {
      setRefreshing(false);
    }
  };

  const resolvedJob = resolved ? jobs.jobsByCatalog[resolved.entry.id] : undefined;

  return (
    <div className="flex flex-col gap-6" data-testid="model-search">
      <section>
        <form onSubmit={(e) => void runSearch(e)} className="flex items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Auf Hugging Face suchen, z. B. „Qwen3 GGUF“"
            aria-label="Modellsuche"
            className={inputClass}
          />
          <Button type="submit" size="md" icon={<Search className="w-4 h-4" />} disabled={!query.trim() || searching} loading={searching}>
            Suchen
          </Button>
        </form>
        <p className="mt-1.5 text-[11px] text-text-secondary">
          Suchergebnisse sind ungeprüft. Erst die Prüfung der Repository-ID zeigt, ob ein Modell mit der mitgelieferten
          Laufzeit läuft.
        </p>
        {searchError && <div className="mt-2 text-xs text-red-300">Suche nicht möglich: {searchError}</div>}
        {search?.offline && (
          <div className="mt-3 flex items-start gap-2 rounded-md border border-border bg-bg-primary/60 px-3 py-2 text-xs text-text-secondary" data-testid="offline-hint">
            <WifiOff className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
            <span>
              Gerade offline – die Suche ist nicht möglich.
              {search.cached_at ? ` Angezeigt werden zwischengespeicherte Ergebnisse vom ${formatDateTime(search.cached_at)}.` : " Sobald eine Verbindung besteht, einfach erneut suchen."}
            </span>
          </div>
        )}
        {search && search.results.length === 0 && !search.offline && (
          <div className="mt-3 text-xs text-text-secondary">Keine GGUF-Repositories für „{query}“ gefunden.</div>
        )}
        {search && search.results.length > 0 && (
          <ul className="mt-3 rounded-lg border border-border divide-y divide-border/60" data-testid="search-results">
            {search.results.map((r) => (
              <li key={r.repo} className="flex items-center gap-3 px-3 py-2 text-xs">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-mono text-text-primary truncate">{r.repo}</span>
                    <Badge tone="neutral">ungeprüft</Badge>
                    {!r.gguf && <Badge tone="yellow">kein GGUF</Badge>}
                  </div>
                  <div className="text-text-secondary mt-0.5 tabular-nums">
                    {r.author} · {formatNumber(r.downloads)} Downloads · {formatNumber(r.likes)} Likes
                    {r.updated && ` · ${formatDate(r.updated)}`}
                  </div>
                </div>
                <Button size="sm" onClick={() => pickResult(r.repo)} disabled={resolving}>
                  Prüfen
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <div className="text-xs font-medium text-text-secondary mb-1.5">Repository-ID prüfen</div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void runResolve(repo, file);
          }}
          className="flex items-center gap-2 flex-wrap"
        >
          <input
            ref={repoInputRef}
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="Autor/Repository, z. B. unsloth/Qwen3-4B-GGUF"
            aria-label="Repository-ID"
            className={cn(inputClass, "flex-1 min-w-[220px] font-mono")}
          />
          <input
            value={file}
            onChange={(e) => setFile(e.target.value)}
            placeholder="Datei (optional)"
            aria-label="Dateiname"
            className={cn(inputClass, "w-56 font-mono")}
          />
          <Button type="submit" size="md" disabled={!repo.trim() || resolving} loading={resolving}>
            Prüfen
          </Button>
        </form>
        <p className="mt-1.5 text-[11px] text-text-secondary">
          Der Kern liest nur den GGUF-Header (Bereichsabfrage), führt nichts aus und prüft Architektur, Typ und
          Speicherbedarf gegen diesen Rechner.
        </p>
        {resolving && (
          <div className="mt-3 flex items-center gap-2 text-xs text-text-secondary">
            <LoadingSpinner size="sm" /> Wird geprüft …
          </div>
        )}
        {resolveError && (
          <div
            className={cn(
              "mt-3 rounded-lg border p-3 text-xs flex items-start gap-2",
              resolveError.gated ? "border-amber-500/30 bg-amber-500/10 text-amber-100" : "border-red-500/30 bg-red-500/10 text-red-200",
            )}
            data-testid="resolve-error"
            data-code={resolveError.code}
          >
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
            <div>
              <div className="font-medium">{resolveError.title}</div>
              <div className="mt-0.5 opacity-90">{resolveError.text}</div>
            </div>
          </div>
        )}
        {resolved && (
          <div className="mt-3">
            <ResolveResult
              result={resolved}
              job={resolvedJob}
              busy={busy}
              onDownload={() => void jobs.download(resolved.entry)}
              onCancel={() => resolvedJob && void jobs.cancelJob(resolvedJob)}
              onResume={() => resolvedJob && void jobs.resumeJob(resolvedJob)}
            />
          </div>
        )}
      </section>

      <section className="flex items-center justify-between gap-3 flex-wrap rounded-lg border border-border bg-bg-primary/40 px-4 py-3">
        <div className="text-xs text-text-secondary">
          <div className="font-medium text-text-primary">Eingebauten Katalog aktualisieren</div>
          Prüft Revisionen und Dateien der eingebauten Einträge auf Hugging Face (ausdrücklicher Netzwerkzugriff).
          {jobs.catalogDate && <span> Katalogstand: {formatDate(jobs.catalogDate)}.</span>}
          {refreshResult && refreshResult.errors.length > 0 && (
            <ul className="mt-1 list-disc pl-4 text-amber-200">
              {refreshResult.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
        </div>
        <Button size="sm" icon={<RefreshCw className="w-3.5 h-3.5" />} onClick={() => void refresh()} disabled={refreshing} loading={refreshing}>
          Katalog aktualisieren
        </Button>
      </section>
    </div>
  );
}
