import { AlertTriangle, X } from "lucide-react";
import { formatBytes } from "../../lib/format";
import type { SpaceError } from "./useModelJobs";

export default function SpaceErrorBanner({ error, onDismiss }: { error: SpaceError; onDismiss: () => void }) {
  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm flex items-start gap-3">
      <AlertTriangle className="w-4 h-4 text-amber-300 mt-0.5 flex-shrink-0" />
      <div className="flex-1">
        <div className="font-medium text-amber-200">Nicht genug Speicherplatz</div>
        <div className="text-amber-100/90 mt-1">{error.detail}</div>
        {(error.requiredBytes !== null || error.freeBytes !== null) && (
          <div className="text-xs text-amber-100/80 mt-1">
            Benötigt: {formatBytes(error.requiredBytes)} · Frei: {formatBytes(error.freeBytes)}
          </div>
        )}
      </div>
      <button type="button" onClick={onDismiss} aria-label="Hinweis schließen" className="text-amber-200/70 hover:text-amber-100">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
