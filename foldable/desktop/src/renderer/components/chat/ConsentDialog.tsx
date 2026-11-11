/**
 * "Freigabe für <Verbindung> im Projekt <Name>": shown when a send ended with
 * `consent_required`. Lists what would be sent (counts from the `sharing`
 * event), then either grants the consent and resends the same text or binds
 * the conversation to the local model and resends.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { ShieldAlert } from "lucide-react";
import { EXECUTION_LABEL, EXECUTION_TONE, sharingSummary } from "../../store/hub";
import { useAppStore } from "../../store/useAppStore";
import { Badge, Button } from "../ui";

export default function ConsentDialog() {
  const consent = useAppStore((s) => s.pendingConsent);
  const dismiss = useAppStore((s) => s.dismissConsent);
  const grant = useAppStore((s) => s.grantConsentAndResend);
  const switchToLocal = useAppStore((s) => s.useLocalAndResend);
  if (!consent) return null;
  return (
    <Dialog.Root open onOpenChange={(o) => !o && dismiss()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 data-[state=open]:animate-fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[min(520px,calc(100vw-2rem))] bg-bg-secondary border border-amber-500/40 rounded-xl p-5 shadow-2xl focus:outline-none" data-testid="consent-dialog">
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-amber-300" />
            <Dialog.Title className="text-sm font-semibold">
              Freigabe für {consent.connectionName} im Projekt {consent.projectName}
            </Dialog.Title>
          </div>
          <Dialog.Description className="mt-2 text-xs text-text-secondary leading-relaxed">{consent.error.detail}</Dialog.Description>
          <div className="mt-3 rounded-lg border border-border bg-bg-primary/60 px-3 py-2 text-xs">
            <div className="flex items-center gap-2 text-text-primary">
              Gesendet wird an <span className="font-medium">{consent.connectionName}</span>
              <Badge tone={EXECUTION_TONE[consent.execution]}>{EXECUTION_LABEL[consent.execution]}</Badge>
            </div>
            <ul className="mt-1.5 list-disc pl-4 text-text-secondary" data-testid="consent-sharing">
              {sharingSummary(consent.sharing).map((line) => (
                <li key={line}>{line}</li>
              ))}
              <li>deine neue Nachricht</li>
            </ul>
          </div>
          <p className="mt-3 text-[11px] text-text-secondary">Die Freigabe gilt für dieses Projekt und diese Verbindung, bis du sie unter Einstellungen → Verbindungen widerrufst.</p>
          <div className="mt-4 flex justify-end gap-2 flex-wrap">
            <Button onClick={dismiss}>Abbrechen</Button>
            <Button onClick={() => void switchToLocal()} data-testid="consent-local">
              Lokales Modell verwenden
            </Button>
            <Button variant="primary" onClick={() => void grant()} data-testid="consent-grant">
              Einmal freigeben und senden
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
