import { CheckCircle2, FolderOpen, RefreshCw } from "lucide-react";
import { useState } from "react";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import type { ModelInstalled } from "../../api/types";
import { formatBytes, formatDateTime, formatTokens } from "../../lib/format";
import { useAppStore } from "../../store/useAppStore";
import CatalogCard from "../models/CatalogCard";
import JobProgress from "../models/JobProgress";
import SpaceErrorBanner from "../models/SpaceErrorBanner";
import { useModelJobs } from "../models/useModelJobs";
import { Badge, Button, EmptyState, SectionTitle } from "../ui";

function InstalledRow({
  model,
  busy,
  deleting,
  onActivate,
  onDelete,
}: {
  model: ModelInstalled;
  busy: boolean;
  deleting: boolean;
  onActivate: () => void;
  onDelete: () => void;
}) {
  return (
    <li className="panel p-3 flex items-center gap-3" data-testid="installed-model">
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
        </div>
        <div className="text-[11px] text-text-secondary mt-0.5 flex flex-wrap gap-x-3">
          <span className="font-mono">{model.quantization}</span>
          <span>{formatBytes(model.size_bytes)}</span>
          <span>{formatTokens(model.context_length)} Token Kontext</span>
          <span>{model.sha256_verified ? "Prüfsumme geprüft" : "Prüfsumme nicht geprüft"}</span>
          <span>{formatDateTime(model.created_at)}</span>
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {!model.active && model.state === "ready" && (
          <Button size="sm" variant="primary" onClick={onActivate} disabled={busy}>
            Aktivieren
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onDelete} disabled={model.active || busy} loading={deleting} title={model.active ? "Aktive Modelle können nicht gelöscht werden" : undefined}>
          Löschen
        </Button>
      </div>
    </li>
  );
}

export default function ModelsTab() {
  const setup = useAppStore((s) => s.setup);
  const jobs = useModelJobs({ autoActivate: false });
  const [deleteTarget, setDeleteTarget] = useState<ModelInstalled | null>(null);
  const activationRunning = Boolean(jobs.activation && (jobs.activation.state === "running" || jobs.activation.state === "queued"));

  return (
    <div className="flex flex-col gap-8">
      {setup?.runtime_state === "error" && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200">
          Die Laufzeit meldet einen Fehler. Ein Modell erneut aktivieren oder die Logs unter{" "}
          <span className="font-mono">data/logs</span> prüfen.
        </div>
      )}

      {jobs.activation && (
        <JobProgress job={jobs.activation} title="Modell wird aktiviert" />
      )}

      <section>
        <SectionTitle
          action={
            <Button size="sm" variant="ghost" icon={<RefreshCw className="w-3.5 h-3.5" />} onClick={() => void jobs.reload()}>
              Aktualisieren
            </Button>
          }
        >
          Installierte Modelle
        </SectionTitle>
        {jobs.installed === null ? (
          <div className="flex justify-center py-6">
            <LoadingSpinner size="sm" />
          </div>
        ) : jobs.installed.length === 0 ? (
          <EmptyState title="Keine Modelle installiert" description="Unten ein Modell aus dem Katalog laden oder eine GGUF-Datei importieren." className="py-6" />
        ) : (
          <ul className="flex flex-col gap-2">
            {jobs.installed.map((m) => (
              <InstalledRow
                key={m.id}
                model={m}
                busy={activationRunning}
                deleting={jobs.deleting === m.id}
                onActivate={() => void jobs.activate(m.id)}
                onDelete={() => setDeleteTarget(m)}
              />
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionTitle
          action={
            <Button size="sm" icon={<FolderOpen className="w-3.5 h-3.5" />} onClick={() => void jobs.importFile()} disabled={activationRunning}>
              Eigene GGUF-Datei importieren
            </Button>
          }
        >
          Weitere Modelle
        </SectionTitle>
        {jobs.importJob && (
          <div className="mb-4">
            <JobProgress
              job={jobs.importJob}
              title="GGUF-Import"
              onCancel={() => void jobs.cancelJob(jobs.importJob as NonNullable<typeof jobs.importJob>)}
            />
          </div>
        )}
        {jobs.spaceError && (
          <div className="mb-4">
            <SpaceErrorBanner error={jobs.spaceError} onDismiss={jobs.dismissSpaceError} />
          </div>
        )}
        {jobs.loadError && <div className="text-xs text-red-300 mb-3">Katalog nicht verfügbar: {jobs.loadError}</div>}
        {jobs.entries === null && !jobs.loadError ? (
          <div className="flex justify-center py-6">
            <LoadingSpinner size="sm" />
          </div>
        ) : (
          <div className="grid gap-4 xl:grid-cols-2">
            {(jobs.entries ?? []).map((entry) => (
              <CatalogCard
                key={entry.id}
                entry={entry}
                job={jobs.jobsByCatalog[entry.id]}
                busy={activationRunning}
                onDownload={() => void jobs.download(entry)}
                onCancel={() => void jobs.cancelJob(jobs.jobsByCatalog[entry.id])}
                onResume={() => void jobs.resumeJob(jobs.jobsByCatalog[entry.id])}
              />
            ))}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Modell löschen?"
        description={`„${deleteTarget?.name ?? ""}“ (${formatBytes(deleteTarget?.size_bytes)}) wird vom Datenträger entfernt. Der Download kann später wiederholt werden.`}
        confirmLabel="Löschen"
        cancelLabel="Abbrechen"
        variant="destructive"
        onConfirm={() => {
          const target = deleteTarget;
          setDeleteTarget(null);
          if (target) void jobs.deleteModel(target);
        }}
      />
    </div>
  );
}
