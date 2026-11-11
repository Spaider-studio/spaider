import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { errorMessage } from "../../api/client";
import type { SystemInfo } from "../../api/types";
import { formatBytes } from "../../lib/format";
import { useAppStore } from "../../store/useAppStore";
import { Button, KeyValue, ProgressBar, SectionTitle } from "../ui";

const USAGE_LABELS: { key: keyof SystemInfo["usage"]; label: string }[] = [
  { key: "models_bytes", label: "Modelle" },
  { key: "db_bytes", label: "Datenbank (Gedächtnis)" },
  { key: "imports_bytes", label: "Importierte Dokumente" },
  { key: "downloads_bytes", label: "Downloads (unvollständig)" },
  { key: "logs_bytes", label: "Logs" },
];

export default function StorageTab() {
  const client = useAppStore((s) => s.client);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!client) return;
    setLoading(true);
    try {
      setInfo(await client.systemInfo());
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!info) {
    return error ? (
      <div className="text-sm text-red-300">
        Systeminformationen nicht verfügbar: {error}
        <div className="mt-3">
          <Button size="sm" onClick={() => void load()}>
            Erneut versuchen
          </Button>
        </div>
      </div>
    ) : (
      <div className="flex justify-center py-10">
        <LoadingSpinner />
      </div>
    );
  }

  const usedTotal = Object.values(info.usage).reduce((a, b) => a + b, 0);
  const diskUsedPct = info.disk_total_bytes > 0 ? ((info.disk_total_bytes - info.disk_free_bytes) / info.disk_total_bytes) * 100 : 0;
  const ramUsedPct = info.ram_total_bytes > 0 ? ((info.ram_total_bytes - info.ram_available_bytes) / info.ram_total_bytes) * 100 : 0;

  return (
    <div className="flex flex-col gap-8">
      <section>
        <SectionTitle
          action={
            <Button size="sm" variant="ghost" icon={<RefreshCw className="w-3.5 h-3.5" />} onClick={() => void load()} loading={loading}>
              Aktualisieren
            </Button>
          }
        >
          Datenträger
        </SectionTitle>
        <div className="panel p-4">
          <div className="flex items-center justify-between text-sm">
            <span>
              Frei: <span className="font-medium">{formatBytes(info.disk_free_bytes)}</span> von {formatBytes(info.disk_total_bytes)}
            </span>
            <span className="text-xs text-text-secondary">{info.filesystem}</span>
          </div>
          <ProgressBar value={diskUsedPct} tone={diskUsedPct > 90 ? "red" : "blue"} className="mt-2" />
          {info.max_file_bytes !== null && (
            <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              Dateisystem {info.filesystem}: Einzelne Dateien dürfen höchstens {formatBytes(info.max_file_bytes)} groß sein. Größere Modelle
              können auf diesem Datenträger nicht gespeichert werden (NTFS oder exFAT empfohlen).
            </div>
          )}
          <div className="mt-4">
            {USAGE_LABELS.map(({ key, label }) => (
              <KeyValue key={key} label={label} value={formatBytes(info.usage[key])} />
            ))}
            <KeyValue label="Gesamt belegt durch SpAIder" value={<span className="font-medium">{formatBytes(usedTotal)}</span>} />
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>System</SectionTitle>
        <div className="panel p-4">
          <div className="flex items-center justify-between text-sm">
            <span>
              Arbeitsspeicher verfügbar: <span className="font-medium">{formatBytes(info.ram_available_bytes)}</span> von{" "}
              {formatBytes(info.ram_total_bytes)}
            </span>
          </div>
          <ProgressBar value={ramUsedPct} tone={ramUsedPct > 90 ? "red" : "blue"} className="mt-2 mb-3" />
          <KeyValue label="Prozessor" value={`${info.cpu} (${info.threads} Threads)`} />
          <KeyValue label="System" value={`${info.os} / ${info.arch} · ${info.hostname}`} />
          <KeyValue label="Laufzeit" value={`${info.runtime.name} ${info.runtime.build} (${info.runtime.variant})`} />
          <KeyValue label="Laufzeit-Pfad" value={info.runtime.path} mono />
        </div>
      </section>

      <section>
        <SectionTitle>Pfade</SectionTitle>
        <div className="panel p-4">
          <KeyValue label="App-Verzeichnis" value={info.app_root} mono />
          <KeyValue label="Datenverzeichnis" value={info.data_dir} mono />
        </div>
      </section>
    </div>
  );
}
