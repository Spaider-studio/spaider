/**
 * Portable key vault: create (new password, twice) or unlock (password).
 * Driven by `useHubStore.vaultDialog`; the password never leaves this dialog
 * except in the request body.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useHubStore } from "../../store/useHubStore";
import { Button, inputClass } from "../ui";

export default function VaultDialog() {
  const mode = useHubStore((s) => s.vaultDialog);
  const busy = useHubStore((s) => s.vaultBusy);
  const error = useHubStore((s) => s.vaultError);
  const store = useHubStore((s) => s.credentialStore);
  const closeVault = useHubStore((s) => s.closeVault);
  const vaultCreate = useHubStore((s) => s.vaultCreate);
  const vaultUnlock = useHubStore((s) => s.vaultUnlock);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  useEffect(() => {
    if (!mode) {
      setPassword("");
      setConfirm("");
    }
  }, [mode]);

  const create = mode === "create";
  const mismatch = create && confirm.length > 0 && confirm !== password;
  const tooShort = create && password.length > 0 && password.length < 8;
  const canSubmit = password.length > 0 && !busy && (!create || (password === confirm && password.length >= 8));

  return (
    <Dialog.Root open={mode !== null} onOpenChange={(o) => !o && closeVault()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[60] data-[state=open]:animate-fade-in" />
        <Dialog.Content
          className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-[60] w-[min(460px,calc(100vw-2rem))] bg-bg-secondary border border-border rounded-xl p-5 shadow-2xl focus:outline-none"
          data-testid="vault-dialog"
          data-mode={mode ?? undefined}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2">
              <KeyRound className="w-4 h-4 text-accent-blue" />
              <Dialog.Title className="text-sm font-semibold">{create ? "Tresor anlegen" : "Tresor entsperren"}</Dialog.Title>
            </div>
            <Dialog.Close asChild>
              <button type="button" aria-label="Schließen" className="text-text-secondary hover:text-text-primary">
                <X className="w-4 h-4" />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="mt-2 text-xs text-text-secondary leading-relaxed">
            {create
              ? "Der portable Tresor speichert API-Schlüssel verschlüsselt (Argon2id + AES-256-GCM) in einer Datei auf dem Datenträger. Ohne Passwort sind die Schlüssel nicht lesbar; es gibt keine Wiederherstellung."
              : `Der Tresor${store?.vault.path ? ` (${store.vault.path})` : ""} ist gesperrt. Das Passwort gilt für diese Sitzung.`}
          </Dialog.Description>
          <form
            className="mt-4 flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!canSubmit) return;
              void (create ? vaultCreate(password) : vaultUnlock(password));
            }}
          >
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-label="Passwort"
              placeholder={create ? "Neues Passwort (mindestens 8 Zeichen)" : "Passwort"}
              className={inputClass}
              autoComplete={create ? "new-password" : "current-password"}
              autoFocus
              data-testid="vault-password"
            />
            {create && (
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                aria-label="Passwort wiederholen"
                placeholder="Passwort wiederholen"
                className={inputClass}
                autoComplete="new-password"
              />
            )}
            {(mismatch || tooShort || error) && (
              <div className="text-xs text-red-300" role="alert" data-testid="vault-error">
                {error ?? (tooShort ? "Mindestens 8 Zeichen." : "Die Passwörter stimmen nicht überein.")}
              </div>
            )}
            <div className="flex justify-end gap-2 mt-1">
              <Button onClick={closeVault} disabled={busy}>
                Abbrechen
              </Button>
              <Button type="submit" variant="primary" disabled={!canSubmit} loading={busy} data-testid="vault-submit">
                {create ? "Anlegen und entsperren" : "Entsperren"}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
