/**
 * "Anbieter hinzufügen" / "Verbindung bearbeiten": only the auth methods the
 * preset declares, the base URL only for own servers, the key with its
 * storage choice (host / vault / session, each explained), the execution for
 * own servers and the data-sharing rule. Keys are sent once and never shown.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import type { Connection, PatchConnectionRequest, ProviderPreset } from "../../api/types";
import {
  asksExecution,
  AUTH_LABEL,
  DATA_SHARING_OPTIONS,
  draftForPreset,
  draftToRequest,
  EXECUTION_OPTIONS,
  needsBaseUrl,
  STORAGE_OPTIONS,
  validateConnectionDraft,
  visibleAuthMethods,
  type ConnectionDraft,
} from "../../store/hub";
import { useHubStore } from "../../store/useHubStore";
import { Button, inputClass, Select } from "../ui";

function RadioRow({ name, checked, onChange, label, description, disabled }: { name: string; checked: boolean; onChange: () => void; label: string; description?: string; disabled?: boolean }) {
  return (
    <label className={cn("flex items-start gap-2.5 rounded-lg border px-3 py-2 cursor-pointer", checked ? "border-accent-blue/60 bg-accent-blue/5" : "border-border hover:bg-white/5", disabled && "opacity-50 cursor-not-allowed")}>
      <input type="radio" name={name} checked={checked} onChange={onChange} disabled={disabled} className="mt-1" />
      <span className="min-w-0">
        <span className="block text-sm text-text-primary">{label}</span>
        {description && <span className="block text-xs text-text-secondary mt-0.5 leading-relaxed">{description}</span>}
      </span>
    </label>
  );
}

export default function ConnectionForm({ preset, existing, onClose }: { preset: ProviderPreset; existing: Connection | null; onClose: (saved: Connection | null) => void }) {
  const store = useHubStore((s) => s.credentialStore);
  const createConnection = useHubStore((s) => s.createConnection);
  const updateConnection = useHubStore((s) => s.updateConnection);
  const openVault = useHubStore((s) => s.openVault);
  const [draft, setDraft] = useState<ConnectionDraft>(() => {
    const base = draftForPreset(preset, store);
    if (!existing) return base;
    return {
      ...base,
      name: existing.name,
      base_url: existing.base_url,
      auth_method: existing.auth_method,
      credential_storage: existing.credential_storage === "none" ? base.credential_storage : existing.credential_storage,
      execution: existing.execution,
      data_sharing: existing.data_sharing,
      default_model: existing.default_model ?? "",
    };
  });
  const [busy, setBusy] = useState(false);
  const patch = (p: Partial<ConnectionDraft>) => setDraft((d) => ({ ...d, ...p }));

  const methods = visibleAuthMethods(preset);
  const problem = validateConnectionDraft(draft, preset, store, Boolean(existing));
  const vaultLocked = Boolean(store && !store.vault.unlocked);

  const submit = async () => {
    if (problem) return;
    setBusy(true);
    try {
      if (existing) {
        const body: PatchConnectionRequest = {
          name: draft.name.trim(),
          auth_method: draft.auth_method,
          credential_storage: draft.credential_storage,
          data_sharing: draft.data_sharing,
          default_model: draft.default_model.trim() || null,
        };
        if (needsBaseUrl(preset)) body.base_url = draft.base_url.trim();
        if (asksExecution(preset)) body.execution = draft.execution;
        if (draft.auth_method === "api_key" && draft.api_key) body.api_key = draft.api_key;
        const conn = await updateConnection(existing.id, body);
        if (conn) onClose(conn);
      } else {
        const conn = await createConnection(draftToRequest(draft, preset));
        if (conn) onClose(conn);
      }
    } finally {
      setBusy(false);
      setDraft((d) => ({ ...d, api_key: "" }));
    }
  };

  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose(null)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[55] data-[state=open]:animate-fade-in" />
        <Dialog.Content
          className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-[55] w-[min(640px,calc(100vw-2rem))] max-h-[calc(100vh-2rem)] overflow-y-auto bg-bg-secondary border border-border rounded-xl p-5 shadow-2xl focus:outline-none"
          data-testid="connection-form"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <Dialog.Title className="text-sm font-semibold">{existing ? "Verbindung bearbeiten" : "Anbieter hinzufügen"}</Dialog.Title>
              <Dialog.Description className="mt-1 text-xs text-text-secondary">{preset.name}</Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button type="button" aria-label="Schließen" className="text-text-secondary hover:text-text-primary">
                <X className="w-4 h-4" />
              </button>
            </Dialog.Close>
          </div>

          <form
            className="mt-4 flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-secondary">Name</span>
              <input value={draft.name} onChange={(e) => patch({ name: e.target.value })} className={inputClass} aria-label="Name der Verbindung" data-testid="conn-name" />
            </label>

            {needsBaseUrl(preset) && (
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-secondary">Basis-URL (genau wie der Server sie erwartet, kein automatisches „/v1“)</span>
                <input value={draft.base_url} onChange={(e) => patch({ base_url: e.target.value })} placeholder="http://127.0.0.1:8080/v1" className={cn(inputClass, "font-mono")} aria-label="Basis-URL" data-testid="conn-base-url" />
              </label>
            )}
            {!needsBaseUrl(preset) && preset.base_url && (
              <div className="text-xs text-text-secondary">
                Endpunkt: <span className="font-mono text-text-primary">{preset.base_url}</span> (vom Anbieter festgelegt)
              </div>
            )}

            <fieldset className="flex flex-col gap-1.5">
              <legend className="text-xs text-text-secondary mb-1">Zugang</legend>
              {methods.map((m) => (
                <RadioRow
                  key={m}
                  name="auth"
                  checked={draft.auth_method === m}
                  onChange={() => patch({ auth_method: m })}
                  label={AUTH_LABEL[m]}
                  description={m === "oauth_siwc" ? "Anmeldung im Browser; Einrichtung erforderlich (nicht in dieser Version automatisiert)." : m === "none" ? "Für Server ohne Zugangsschutz (nur lokal/LAN sinnvoll)." : undefined}
                />
              ))}
            </fieldset>

            {draft.auth_method === "api_key" && (
              <div className="flex flex-col gap-3">
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-text-secondary">
                    API-Schlüssel{existing?.has_credential ? " (leer lassen = unverändert)" : ""}
                    {preset.key_url && (
                      <>
                        {" "}
                        · beziehen unter <span className="font-mono text-text-primary">{preset.key_url}</span>
                      </>
                    )}
                  </span>
                  <input type="password" value={draft.api_key} onChange={(e) => patch({ api_key: e.target.value })} className={cn(inputClass, "font-mono")} aria-label="API-Schlüssel" autoComplete="off" data-testid="conn-api-key" />
                </label>
                <fieldset className="flex flex-col gap-1.5">
                  <legend className="text-xs text-text-secondary mb-1">Ablage des Schlüssels</legend>
                  {STORAGE_OPTIONS.map((o) => (
                    <RadioRow
                      key={o.value}
                      name="storage"
                      checked={draft.credential_storage === o.value}
                      onChange={() => patch({ credential_storage: o.value })}
                      label={o.label}
                      description={o.description}
                      disabled={o.value === "host" && Boolean(store && !store.host_available)}
                    />
                  ))}
                  {draft.credential_storage === "vault" && vaultLocked && (
                    <div className="flex items-center justify-between gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                      <span>{store?.vault.exists ? "Der Tresor ist gesperrt." : "Es gibt noch keinen Tresor."}</span>
                      <Button size="sm" onClick={() => openVault(store?.vault.exists ? "unlock" : "create")} data-testid="conn-open-vault">
                        {store?.vault.exists ? "Tresor entsperren" : "Tresor anlegen"}
                      </Button>
                    </div>
                  )}
                </fieldset>
              </div>
            )}

            {asksExecution(preset) && (
              <label className="flex items-center justify-between gap-3">
                <span className="min-w-0">
                  <span className="block text-sm">Ausführung</span>
                  <span className="block text-xs text-text-secondary">{EXECUTION_OPTIONS.find((o) => o.value === draft.execution)?.description}</span>
                </span>
                <Select value={draft.execution} onChange={(v) => patch({ execution: v as ConnectionDraft["execution"] })} options={EXECUTION_OPTIONS.map((o) => ({ value: o.value, label: o.label }))} ariaLabel="Ausführung" />
              </label>
            )}

            <fieldset className="flex flex-col gap-1.5">
              <legend className="text-xs text-text-secondary mb-1">Datenfreigabe</legend>
              {DATA_SHARING_OPTIONS.map((o) => (
                <RadioRow key={o.value} name="sharing" checked={draft.data_sharing === o.value} onChange={() => patch({ data_sharing: o.value })} label={o.label} description={o.description} />
              ))}
            </fieldset>

            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-secondary">Standardmodell (optional, kann später aus der Modellliste gewählt werden)</span>
              <input value={draft.default_model} onChange={(e) => patch({ default_model: e.target.value })} className={cn(inputClass, "font-mono")} aria-label="Standardmodell" placeholder="z. B. deepseek-chat" />
            </label>

            {preset.data_note_de && <div className="text-xs text-text-secondary leading-relaxed">{preset.data_note_de}</div>}

            <div className="flex items-center justify-end gap-2 pt-1">
              {problem && (
                <span className="text-xs text-text-secondary mr-auto" data-testid="conn-form-problem">
                  {problem}
                </span>
              )}
              <Button onClick={() => onClose(null)} disabled={busy}>
                Abbrechen
              </Button>
              <Button type="submit" variant="primary" disabled={Boolean(problem) || busy} loading={busy} data-testid="conn-save">
                {existing ? "Speichern" : "Verbindung anlegen"}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
