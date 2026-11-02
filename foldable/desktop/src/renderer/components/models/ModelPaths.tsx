/**
 * The three ways to get a model (setup screen and Settings -> Modelle):
 *   1. "Für diesen Computer empfohlen"  – recommendations with reasons
 *   2. "Weitere Modelle"                – search / resolve / built-in catalog
 *   3. "Lokales Modell importieren"     – GGUF file import
 */
import { FolderOpen } from "lucide-react";
import { useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import { formatDate } from "../../lib/format";
import { IMPORT_ERROR_LABEL } from "../../lib/modelErrors";
import { Button } from "../ui";
import CatalogCard from "./CatalogCard";
import JobProgress from "./JobProgress";
import ModelSearchPanel from "./ModelSearchPanel";
import RecommendationsPanel from "./RecommendationsPanel";
import type { ModelJobs } from "./useModelJobs";

export type ModelPath = "recommended" | "more" | "import";

const PATHS: { id: ModelPath; label: string }[] = [
  { id: "recommended", label: "Für diesen Computer empfohlen" },
  { id: "more", label: "Weitere Modelle" },
  { id: "import", label: "Lokales Modell importieren" },
];

const IMPORT_HINTS: { code: string; text: string }[] = [
  { code: "split_incomplete", text: IMPORT_ERROR_LABEL.split_incomplete },
  { code: "not_chat_model", text: IMPORT_ERROR_LABEL.not_chat_model },
  { code: "arch_unsupported", text: IMPORT_ERROR_LABEL.arch_unsupported },
  { code: "role_mismatch", text: IMPORT_ERROR_LABEL.role_mismatch },
];

export default function ModelPaths({
  jobs,
  busy,
  initial = "recommended",
  showCatalog = true,
}: {
  jobs: ModelJobs;
  busy: boolean;
  initial?: ModelPath;
  /** Show the built-in catalog cards under "Weitere Modelle". */
  showCatalog?: boolean;
}) {
  const [path, setPath] = useState<ModelPath>(initial);
  const chatEntries = jobs.entries?.filter((e) => e.role === "chat") ?? [];
  const embeddingEntries = jobs.entries?.filter((e) => e.role === "embedding") ?? [];

  return (
    <div className="flex flex-col gap-5" data-testid="model-paths">
      <div className="flex gap-1 border-b border-border" role="tablist" aria-label="Modellquellen">
        {PATHS.map((p) => (
          <button
            key={p.id}
            type="button"
            role="tab"
            aria-selected={path === p.id}
            onClick={() => setPath(p.id)}
            className={cn(
              "px-3 py-2 text-sm -mb-px border-b-2 transition-colors",
              path === p.id ? "border-accent-blue text-text-primary" : "border-transparent text-text-secondary hover:text-text-primary",
            )}
          >
            {p.label}
          </button>
        ))}
      </div>

      {path === "recommended" && <RecommendationsPanel jobs={jobs} busy={busy} />}

      {path === "more" && (
        <div className="flex flex-col gap-8">
          <ModelSearchPanel jobs={jobs} busy={busy} />
          {showCatalog && (
            <section>
              <div className="flex items-center justify-between gap-3 mb-3">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-text-secondary">Eingebauter Katalog</h3>
                {jobs.catalogDate && <span className="text-xs text-text-secondary">Katalogstand: {formatDate(jobs.catalogDate)}</span>}
              </div>
              {jobs.loadError && <div className="text-xs text-red-300 mb-3">Katalog nicht verfügbar: {jobs.loadError}</div>}
              {jobs.entries === null && !jobs.loadError ? (
                <div className="flex justify-center py-6">
                  <LoadingSpinner size="sm" />
                </div>
              ) : (
                <div className="grid gap-4 xl:grid-cols-2">
                  {[...chatEntries, ...embeddingEntries].map((entry) => (
                    <CatalogCard
                      key={entry.id}
                      entry={entry}
                      job={jobs.jobsByCatalog[entry.id]}
                      busy={busy}
                      onDownload={() => void jobs.download(entry)}
                      onCancel={() => void jobs.cancelJob(jobs.jobsByCatalog[entry.id])}
                      onResume={() => void jobs.resumeJob(jobs.jobsByCatalog[entry.id])}
                    />
                  ))}
                </div>
              )}
            </section>
          )}
        </div>
      )}

      {path === "import" && (
        <section className="flex flex-col gap-4" data-testid="import-path">
          <div className="panel p-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium">Eigene GGUF-Datei importieren</div>
              <div className="text-xs text-text-secondary mt-0.5">
                Eine vorhandene GGUF-Datei wird geprüft (Header, Architektur, Rolle) und nach{" "}
                <span className="font-mono">data/models</span> kopiert. Mehrteilige Modelle: die erste Datei
                (…-00001-of-0000N.gguf) auswählen, alle Teile müssen im selben Ordner liegen.
              </div>
            </div>
            <Button icon={<FolderOpen className="w-4 h-4" />} onClick={() => void jobs.importFile()} disabled={busy}>
              Datei auswählen …
            </Button>
          </div>
          {jobs.importJob && (
            <JobProgress
              job={jobs.importJob}
              title="GGUF-Import"
              onCancel={() => void jobs.cancelJob(jobs.importJob as NonNullable<typeof jobs.importJob>)}
            />
          )}
          <details className="text-xs text-text-secondary">
            <summary className="cursor-pointer hover:text-text-primary">Mögliche Prüfergebnisse</summary>
            <ul className="mt-2 flex flex-col gap-1 pl-4 list-disc">
              {IMPORT_HINTS.map((h) => (
                <li key={h.code}>
                  <span className="font-mono text-text-primary">{h.code}</span>: {h.text}
                </li>
              ))}
            </ul>
          </details>
        </section>
      )}
    </div>
  );
}
