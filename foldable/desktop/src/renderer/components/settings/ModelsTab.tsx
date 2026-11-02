import { RefreshCw } from "lucide-react";
import { useState } from "react";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import type { ModelInstalled } from "../../api/types";
import { formatBytes } from "../../lib/format";
import { useAppStore } from "../../store/useAppStore";
import InstalledModelRow from "../models/InstalledModelRow";
import JobProgress from "../models/JobProgress";
import ModelPaths from "../models/ModelPaths";
import SpaceErrorBanner from "../models/SpaceErrorBanner";
import { useModelJobs } from "../models/useModelJobs";
import { Button, EmptyState, SectionTitle } from "../ui";

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

      {jobs.activation && <JobProgress job={jobs.activation} title="Modell wird aktiviert" />}

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
          <EmptyState title="Keine Modelle installiert" description="Unten ein empfohlenes Modell laden, ein Modell suchen oder eine GGUF-Datei importieren." className="py-6" />
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

      <section>
        <SectionTitle>Modelle hinzufügen</SectionTitle>
        {jobs.spaceError && (
          <div className="mb-4">
            <SpaceErrorBanner error={jobs.spaceError} onDismiss={jobs.dismissSpaceError} />
          </div>
        )}
        <ModelPaths jobs={jobs} busy={activationRunning} />
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
