import { AlertTriangle } from "lucide-react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import type { BackendStatus } from "../../shared-types/bridge";
import Logo from "../components/Logo";
import { Button } from "../components/ui";

export default function BootScreen({ status, onQuit }: { status: BackendStatus; onQuit: () => void }) {
  const isError = status.state === "error";
  return (
    <div className="h-full w-full flex items-center justify-center grid-bg">
      <div className="panel w-full max-w-lg mx-4 p-8 flex flex-col items-center text-center animate-fade-in">
        <Logo className="w-12 h-12 mb-4" />
        <h1 className="text-lg font-semibold tracking-tight">SpAIder Foldable</h1>
        {!isError ? (
          <>
            <div className="mt-6 flex items-center gap-3 text-sm text-text-secondary">
              <LoadingSpinner size="sm" />
              <span>{status.message || "SpAIder startet …"}</span>
            </div>
            <p className="mt-4 text-xs text-text-secondary/70">
              Der lokale Kern wird gestartet. Es werden keine Daten ins Internet gesendet.
            </p>
          </>
        ) : (
          <>
            <div className="mt-6 w-full rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-left">
              <div className="flex items-center gap-2 text-sm font-medium text-red-300">
                <AlertTriangle className="w-4 h-4" />
                Der SpAIder-Kern konnte nicht gestartet werden
              </div>
              <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-red-200/90">
                {status.message || "Unbekannter Fehler."}
              </pre>
            </div>
            <p className="mt-3 text-xs text-text-secondary">
              Details stehen in <span className="font-mono">data/logs/core-stderr.log</span> auf dem Datenträger.
            </p>
            <Button variant="danger" className="mt-5" onClick={onQuit}>
              Beenden
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
