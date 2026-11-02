/**
 * "Für diesen Computer empfohlen": focus selector, hardware line, up to three
 * recommendation cards and the catalog date (GET /api/models/recommendations).
 */
import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import { errorMessage } from "../../api/client";
import type { RecommendationFocus, Recommendations } from "../../api/types";
import { formatDate } from "../../lib/format";
import { FOCUS_LABEL } from "../../lib/labels";
import { useAppStore } from "../../store/useAppStore";
import { Button } from "../ui";
import HardwareSummary from "./HardwareSummary";
import RecommendationCard from "./RecommendationCard";
import type { ModelJobs } from "./useModelJobs";

const FOCUSES: RecommendationFocus[] = ["alltag", "dokumente", "coding"];

export default function RecommendationsPanel({
  jobs,
  busy,
  showHardware = true,
}: {
  jobs: ModelJobs;
  busy: boolean;
  /** The setup screen shows the hardware line in its header already. */
  showHardware?: boolean;
}) {
  const client = useAppStore((s) => s.client);
  const hardware = useAppStore((s) => s.hardware);
  const loadHardware = useAppStore((s) => s.loadHardware);
  const [focus, setFocus] = useState<RecommendationFocus>("alltag");
  const [data, setData] = useState<Recommendations | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showExcluded, setShowExcluded] = useState(false);

  const load = useCallback(
    async (f: RecommendationFocus) => {
      if (!client) return;
      setLoading(true);
      try {
        setData(await client.recommendations(f));
        setError(null);
      } catch (e) {
        setError(errorMessage(e));
      } finally {
        setLoading(false);
      }
    },
    [client],
  );

  useEffect(() => {
    void load(focus);
  }, [focus, load]);

  useEffect(() => {
    if (!hardware) void loadHardware();
  }, [hardware, loadHardware]);

  // Installed state changes (downloads) should refresh the picks.
  const installedCount = jobs.installed?.length ?? 0;
  useEffect(() => {
    if (data) void load(focus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [installedCount]);

  return (
    <div className="flex flex-col gap-4" data-testid="recommendations">
      {showHardware && <HardwareSummary hardware={data?.hardware ?? hardware} />}

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="inline-flex rounded-lg border border-border bg-bg-primary p-0.5" role="radiogroup" aria-label="Schwerpunkt">
          {FOCUSES.map((f) => (
            <button
              key={f}
              type="button"
              role="radio"
              aria-checked={focus === f}
              onClick={() => setFocus(f)}
              className={cn(
                "px-3 py-1.5 text-xs rounded-md transition-colors",
                focus === f ? "bg-bg-tertiary text-text-primary border border-border" : "text-text-secondary hover:text-text-primary border border-transparent",
              )}
            >
              {FOCUS_LABEL[f]}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 text-xs text-text-secondary">
          {data?.catalog_date && <span data-testid="catalog-date">Katalogstand: {formatDate(data.catalog_date)}</span>}
          <Button size="sm" variant="ghost" icon={<RefreshCw className="w-3.5 h-3.5" />} onClick={() => void load(focus)} disabled={loading}>
            Neu berechnen
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200">
          Empfehlungen nicht verfügbar: {error}
        </div>
      )}
      {!data && loading && (
        <div className="flex justify-center py-8">
          <LoadingSpinner size="sm" />
        </div>
      )}
      {data && data.picks.length === 0 && !error && (
        <div className="text-sm text-text-secondary py-4">
          Für diesen Rechner gibt es im aktuellen Katalog keine passende Empfehlung. Unter „Weitere Modelle“ lässt sich
          ein Modell manuell prüfen.
        </div>
      )}
      {data && data.picks.length > 0 && (
        <div className={cn("grid gap-4", data.picks.length >= 3 ? "xl:grid-cols-3 md:grid-cols-2" : "md:grid-cols-2")}>
          {data.picks.slice(0, 3).map((pick) => (
            <RecommendationCard
              key={pick.slot}
              pick={pick}
              job={jobs.jobsByCatalog[pick.entry.id]}
              busy={busy || loading}
              onDownload={() => void jobs.download(pick.entry)}
              onCancel={() => void jobs.cancelJob(jobs.jobsByCatalog[pick.entry.id])}
              onResume={() => void jobs.resumeJob(jobs.jobsByCatalog[pick.entry.id])}
            />
          ))}
        </div>
      )}
      {data && data.excluded.length > 0 && (
        <div className="text-xs text-text-secondary">
          <button type="button" className="hover:text-text-primary underline underline-offset-2" onClick={() => setShowExcluded((v) => !v)}>
            {showExcluded ? "Ausgeschlossene Modelle ausblenden" : `${data.excluded.length} Modell(e) ausgeschlossen – warum?`}
          </button>
          {showExcluded && (
            <ul className="mt-2 list-disc pl-4 flex flex-col gap-0.5">
              {data.excluded.map((x) => (
                <li key={x.id}>
                  <span className="font-mono text-text-primary">{x.id}</span>: {x.reason_de}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
