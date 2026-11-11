import { AlertTriangle, HardDrive, X } from "lucide-react";
import { useEffect, useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import type { SystemInfo } from "../api/types";
import Logo from "../components/Logo";
import HardwareSummary from "../components/models/HardwareSummary";
import JobProgress from "../components/models/JobProgress";
import ModelPaths from "../components/models/ModelPaths";
import { useModelJobs } from "../components/models/useModelJobs";
import { Button } from "../components/ui";
import { formatBytes } from "../lib/format";
import { useAppStore } from "../store/useAppStore";

export default function SetupScreen() {
  const client = useAppStore((s) => s.client);
  const hardware = useAppStore((s) => s.hardware);
  const loadHardware = useAppStore((s) => s.loadHardware);
  const requestQuit = useAppStore((s) => s.requestQuit);
  const jobs = useModelJobs({ autoActivate: true });
  const [system, setSystem] = useState<SystemInfo | null>(null);

  useEffect(() => {
    if (!client) return;
    client
      .systemInfo()
      .then(setSystem)
      .catch(() => setSystem(null));
    void loadHardware();
  }, [client, loadHardware]);

  const busy = Boolean(jobs.activation && (jobs.activation.state === "running" || jobs.activation.state === "queued"));

  return (
    <div className="h-full w-full overflow-y-auto grid-bg">
      <div className="max-w-5xl mx-auto px-6 py-10">
        <header className="flex items-start gap-4 mb-8">
          <Logo className="w-10 h-10 mt-0.5" />
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Willkommen bei SpAIder Foldable</h1>
            <p className="mt-1 text-sm text-text-secondary max-w-2xl leading-relaxed">
              Für den Chat wird einmalig ein Sprachmodell auf diesen Datenträger geladen. Danach arbeitet SpAIder
              vollständig lokal und offline. Modelle werden von Hugging Face geladen und per Prüfsumme verifiziert.
            </p>
          </div>
        </header>

        <div className="mb-6 flex flex-col gap-3">
          <HardwareSummary hardware={hardware} fallback={system} />
          {system && (
            <div className="panel px-4 py-3 flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-text-secondary">
              <span className="inline-flex items-center gap-1.5">
                <HardDrive className="w-3.5 h-3.5" />
                Freier Speicher: <span className="text-text-primary">{formatBytes(system.disk_free_bytes)}</span> von{" "}
                {formatBytes(system.disk_total_bytes)} ({system.filesystem})
              </span>
              {system.max_file_bytes !== null && (
                <span className="text-amber-300">
                  FAT32: Dateien größer als {formatBytes(system.max_file_bytes)} können nicht gespeichert werden.
                </span>
              )}
            </div>
          )}
        </div>

        {jobs.spaceError && (
          <div className="mb-6 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm flex items-start gap-3">
            <AlertTriangle className="w-4 h-4 text-amber-300 mt-0.5 flex-shrink-0" />
            <div className="flex-1">
              <div className="font-medium text-amber-200">Nicht genug Speicherplatz</div>
              <div className="text-amber-100/90 mt-1">{jobs.spaceError.detail}</div>
              {(jobs.spaceError.requiredBytes !== null || jobs.spaceError.freeBytes !== null) && (
                <div className="text-xs text-amber-100/80 mt-1">
                  Benötigt: {formatBytes(jobs.spaceError.requiredBytes)} · Frei: {formatBytes(jobs.spaceError.freeBytes)}
                </div>
              )}
            </div>
            <button type="button" onClick={jobs.dismissSpaceError} aria-label="Hinweis schließen" className="text-amber-200/70 hover:text-amber-100">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {jobs.activation && (
          <div className="mb-6">
            <JobProgress job={jobs.activation} title="Modell wird aktiviert" />
          </div>
        )}

        {jobs.loadError && (
          <div className="mb-6 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
            Der Modellkatalog konnte nicht geladen werden: {jobs.loadError}
            <div className="mt-2">
              <Button size="sm" onClick={() => void jobs.reload()}>
                Erneut versuchen
              </Button>
            </div>
          </div>
        )}

        {!jobs.entries && !jobs.loadError ? (
          <div className="flex items-center gap-3 text-sm text-text-secondary py-10 justify-center">
            <LoadingSpinner size="sm" /> Modellkatalog wird geladen …
          </div>
        ) : (
          <ModelPaths jobs={jobs} busy={busy} showHardware={false} />
        )}

        <div className="mt-8 flex justify-end">
          <Button variant="ghost" size="sm" onClick={() => void requestQuit()}>
            Sicher beenden
          </Button>
        </div>
      </div>
    </div>
  );
}
