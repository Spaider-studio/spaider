import LoadingSpinner from "@/components/shared/LoadingSpinner";
import Logo from "./Logo";

export default function QuitOverlay() {
  return (
    <div
      className="fixed inset-0 z-[200] bg-bg-primary/95 backdrop-blur-sm flex items-center justify-center animate-fade-in"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="quit-title"
    >
      <div className="flex flex-col items-center text-center px-6 max-w-md">
        <Logo className="w-12 h-12 mb-5" />
        <div className="flex items-center gap-3">
          <LoadingSpinner size="sm" />
          <h2 id="quit-title" className="text-base font-semibold">
            SpAIder wird beendet …
          </h2>
        </div>
        <p className="mt-4 text-sm text-text-secondary leading-relaxed">
          Bitte den Datenträger erst entfernen, wenn dieses Fenster geschlossen ist.
        </p>
      </div>
    </div>
  );
}
