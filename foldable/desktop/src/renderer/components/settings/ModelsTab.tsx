/**
 * Settings -> Modelle (0.4 "Modellbibliothek"), four sections:
 *   1. Für diesen Computer empfohlen   – recommendations with reasons
 *   2. Alle Modelle entdecken          – paginated hub search + repository detail
 *   3. Meine Modelle                   – installed models, GGUF import, repository-ID resolve
 *   4. Anbieter & Verbindungen         – models of enabled connections, link to "Verbindungen"
 */
import { FolderOpen, Plug, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import type { Connection, ModelInstalled } from "../../api/types";
import { formatBytes } from "../../lib/format";
import { CAPABILITY_LABEL, CAPABILITY_TONE, capabilityChips, EXECUTION_LABEL, EXECUTION_TONE, selectableConnections } from "../../store/hub";
import { useAppStore } from "../../store/useAppStore";
import { useHubStore } from "../../store/useHubStore";
import DiscoverPanel from "../models/DiscoverPanel";
import InstalledModelRow from "../models/InstalledModelRow";
import JobProgress from "../models/JobProgress";
import ModelSearchPanel from "../models/ModelSearchPanel";
import RecommendationsPanel from "../models/RecommendationsPanel";
import SpaceErrorBanner from "../models/SpaceErrorBanner";
import { useModelJobs } from "../models/useModelJobs";
import { Badge, Button, EmptyState, SectionTitle } from "../ui";

function ConnectionModelsSummary({ connection }: { connection: Connection }) {
  const models = useHubStore((s) => s.modelsByConnection[connection.id]);
  const loading = useHubStore((s) => Boolean(s.modelsLoading[connection.id]));
  const error = useHubStore((s) => s.modelsError[connection.id] ?? "");
  const loadConnectionModels = useHubStore((s) => s.loadConnectionModels);
  useEffect(() => {
    if (!models) void loadConnectionModels(connection.id);
  }, [models, connection.id, loadConnectionModels]);
  return (
    <li className="panel p-3" data-testid="connection-models-summary" data-connection-id={connection.id}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm font-medium">{connection.name}</span>
        <Badge tone={EXECUTION_TONE[connection.execution]}>{EXECUTION_LABEL[connection.execution]}</Badge>
        {connection.default_model && <span className="text-[11px] text-text-secondary">Standard: <span className="font-mono">{connection.default_model}</span></span>}
      </div>
      {loading && !models ? (
        <div className="mt-2 text-xs text-text-secondary">Modelle werden geladen …</div>
      ) : error ? (
        <div className="mt-2 text-xs text-red-300">{error}</div>
      ) : !models || models.length === 0 ? (
        <div className="mt-2 text-xs text-text-secondary">Keine Modellliste – Modell-ID im Chat von Hand eingeben.</div>
      ) : (
        <ul className="mt-2 flex flex-col gap-1">
          {models.slice(0, 8).map((m) => (
            <li key={m.id} className="flex items-center gap-2 flex-wrap text-xs">
              <span className="font-mono text-text-primary">{m.id}</span>
              {capabilityChips(m)
                .filter((c) => c.value !== "unknown")
                .map((c) => (
                  <Badge key={c.key} tone={CAPABILITY_TONE[c.value]}>
                    {c.label}: {CAPABILITY_LABEL[c.value]}
                  </Badge>
                ))}
            </li>
          ))}
          {models.length > 8 && <li className="text-[11px] text-text-secondary">… {models.length - 8} weitere</li>}
        </ul>
      )}
    </li>
  );
}

export default function ModelsTab() {
  const setup = useAppStore((s) => s.setup);
  const openSettings = useAppStore((s) => s.openSettings);
  const client = useAppStore((s) => s.client);
  const connections = useHubStore((s) => s.connections);
  const connectionsLoaded = useHubStore((s) => s.connectionsLoaded);
  const loadConnections = useHubStore((s) => s.loadConnections);
  const jobs = useModelJobs({ autoActivate: false });
  const [deleteTarget, setDeleteTarget] = useState<ModelInstalled | null>(null);
  const activationRunning = Boolean(jobs.activation && (jobs.activation.state === "running" || jobs.activation.state === "queued"));

  useEffect(() => {
    if (client && !connectionsLoaded) void loadConnections();
  }, [client, connectionsLoaded, loadConnections]);

  const enabled = selectableConnections(connections);

  return (
    <div className="flex flex-col gap-10" data-testid="models-tab">
      {setup?.runtime_state === "error" && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200">
          Die Laufzeit meldet einen Fehler. Ein Modell erneut aktivieren oder die Logs unter <span className="font-mono">data/logs</span> prüfen.
        </div>
      )}
      {jobs.activation && <JobProgress job={jobs.activation} title="Modell wird aktiviert" />}
      {jobs.spaceError && <SpaceErrorBanner error={jobs.spaceError} onDismiss={jobs.dismissSpaceError} />}

      <section data-testid="section-recommended">
        <SectionTitle>Für diesen Computer empfohlen</SectionTitle>
        <RecommendationsPanel jobs={jobs} busy={activationRunning} showHardware />
      </section>

      <section data-testid="section-discover">
        <SectionTitle>Alle Modelle entdecken</SectionTitle>
        <DiscoverPanel jobs={jobs} busy={activationRunning} />
        <details className="mt-3 text-xs text-text-secondary">
          <summary className="cursor-pointer hover:text-text-primary">Direkt per Repository-ID oder Hugging-Face-Suche (bisheriger Weg)</summary>
          <div className="mt-3">
            <ModelSearchPanel jobs={jobs} busy={activationRunning} />
          </div>
        </details>
      </section>

      <section data-testid="section-mine">
        <SectionTitle
          action={
            <div className="flex items-center gap-2">
              <Button size="sm" variant="ghost" icon={<RefreshCw className="w-3.5 h-3.5" />} onClick={() => void jobs.reload()}>
                Aktualisieren
              </Button>
              <Button size="sm" icon={<FolderOpen className="w-3.5 h-3.5" />} onClick={() => void jobs.importFile()} disabled={activationRunning} data-testid="import-model">
                GGUF-Datei importieren …
              </Button>
            </div>
          }
        >
          Meine Modelle
        </SectionTitle>
        {jobs.importJob && <div className="mb-3"><JobProgress job={jobs.importJob} title="GGUF-Import" onCancel={() => void jobs.cancelJob(jobs.importJob as NonNullable<typeof jobs.importJob>)} /></div>}
        {jobs.installed === null ? (
          <div className="flex justify-center py-6">
            <LoadingSpinner size="sm" />
          </div>
        ) : jobs.installed.length === 0 ? (
          <EmptyState title="Keine Modelle installiert" description="Oben ein empfohlenes Modell laden, ein Modell entdecken oder eine GGUF-Datei importieren." className="py-6" />
        ) : (
          <ul className="flex flex-col gap-2">
            {jobs.installed.map((m) => (
              <InstalledModelRow
                key={m.id}
                model={m}
                busy={activationRunning}
                deleting={jobs.deleting === m.id}
                calibration={jobs.calibrations[m.id]}
                onActivate={() => void jobs.activate(m.id)}
                onDelete={() => setDeleteTarget(m)}
                onCalibrate={() => void jobs.calibrate(m.id)}
                onCancelCalibration={() => {
                  const job = jobs.calibrations[m.id];
                  if (job) void jobs.cancelJob(job);
                }}
              />
            ))}
          </ul>
        )}
      </section>

      <section data-testid="section-connections">
        <SectionTitle
          action={
            <Button size="sm" icon={<Plug className="w-3.5 h-3.5" />} onClick={() => openSettings("connections")} data-testid="goto-connections">
              Verbindungen verwalten
            </Button>
          }
        >
          Anbieter & Verbindungen
        </SectionTitle>
        {enabled.length === 0 ? (
          <div className="text-xs text-text-secondary px-1">Keine aktive Verbindung. Eigene Server oder Anbieter mit eigenem Schlüssel lassen sich unter Einstellungen → Verbindungen einrichten; das lokale Modell braucht keine.</div>
        ) : (
          <ul className="flex flex-col gap-2">
            {enabled.map((c) => (
              <ConnectionModelsSummary key={c.id} connection={c} />
            ))}
          </ul>
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
