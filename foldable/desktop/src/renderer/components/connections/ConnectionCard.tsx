/**
 * One connection: identity line (preset, execution, access, storage), enable
 * toggle, last test, and two expandable panels: the four-stage connection
 * test (probe answer only on explicit request) and the provider's model list
 * with capability chips and the "Standardmodell" choice.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { Check, CircleDashed, Minus, Pencil, Play, RefreshCw, Trash2, X } from "lucide-react";
import { useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import type { Connection, ConnectionModel, ProviderPreset } from "../../api/types";
import { formatDateTime, formatTokens } from "../../lib/format";
import {
  AUTH_LABEL,
  CAPABILITY_LABEL,
  CAPABILITY_TONE,
  capabilityChips,
  DATA_SHARING_LABEL,
  EXECUTION_LABEL,
  EXECUTION_TONE,
  STORAGE_OPTIONS,
  testStages,
  type StageState,
} from "../../store/hub";
import { useHubStore } from "../../store/useHubStore";
import { Badge, Button, Toggle } from "../ui";

const STAGE_ICON: Record<StageState, { icon: typeof Check; className: string }> = {
  ok: { icon: Check, className: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10" },
  failed: { icon: X, className: "text-red-300 border-red-500/40 bg-red-500/10" },
  skipped: { icon: Minus, className: "text-text-secondary border-border bg-white/5" },
  pending: { icon: CircleDashed, className: "text-text-secondary/60 border-border bg-transparent" },
};

export function TestStages({ connection, allowGeneration }: { connection: Connection; allowGeneration: boolean }) {
  const result = useHubStore((s) => s.testResults[connection.id] ?? null);
  const stages = testStages(result, allowGeneration);
  return (
    <ol className="grid grid-cols-2 gap-2" data-testid="test-stages">
      {stages.map((st) => {
        const { icon: Icon, className } = STAGE_ICON[st.state];
        return (
          <li key={st.id} className="flex items-start gap-2 rounded-lg border border-border/60 bg-bg-primary/40 px-2.5 py-2" data-stage={st.id} data-state={st.state}>
            <span className={cn("mt-0.5 inline-flex w-5 h-5 items-center justify-center rounded-full border flex-shrink-0", className)} aria-hidden="true">
              <Icon className="w-3 h-3" />
            </span>
            <span className="min-w-0">
              <span className="block text-xs font-medium text-text-primary">{st.label}</span>
              {st.note && <span className="block text-[11px] text-text-secondary truncate" title={st.note}>{st.note}</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function ModelRow({ model, isDefault, onDefault, busy }: { model: ConnectionModel; isDefault: boolean; onDefault: () => void; busy: boolean }) {
  return (
    <li className="flex items-start gap-3 px-3 py-2" data-testid="connection-model" data-model-id={model.id}>
      <input type="radio" name="default-model" checked={isDefault} onChange={onDefault} disabled={busy} aria-label={`Standardmodell ${model.id}`} className="mt-1" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-mono text-text-primary">{model.id}</span>
          {model.display_name && model.display_name !== model.id && <span className="text-xs text-text-secondary">{model.display_name}</span>}
          {model.owned_by && <span className="text-[11px] text-text-secondary/70">{model.owned_by}</span>}
          {typeof model.context_length === "number" && model.context_length > 0 && <span className="text-[11px] text-text-secondary/70">{formatTokens(model.context_length)} Kontext</span>}
          {isDefault && <Badge tone="blue">Standardmodell</Badge>}
        </div>
        <div className="mt-1 flex items-center gap-1 flex-wrap">
          {capabilityChips(model).map((c) => (
            <Badge key={c.key} tone={CAPABILITY_TONE[c.value]} title={`${c.label}: ${CAPABILITY_LABEL[c.value]}`}>
              {c.label}: {CAPABILITY_LABEL[c.value]}
            </Badge>
          ))}
        </div>
      </div>
    </li>
  );
}

export default function ConnectionCard({ connection, preset, onEdit }: { connection: Connection; preset: ProviderPreset | null; onEdit: () => void }) {
  const models = useHubStore((s) => s.modelsByConnection[connection.id]);
  const modelsLoading = useHubStore((s) => Boolean(s.modelsLoading[connection.id]));
  const modelsError = useHubStore((s) => s.modelsError[connection.id] ?? "");
  const testing = useHubStore((s) => Boolean(s.testing[connection.id]));
  const result = useHubStore((s) => s.testResults[connection.id] ?? null);
  const testConnection = useHubStore((s) => s.testConnection);
  const loadConnectionModels = useHubStore((s) => s.loadConnectionModels);
  const updateConnection = useHubStore((s) => s.updateConnection);
  const deleteConnection = useHubStore((s) => s.deleteConnection);
  const [panel, setPanel] = useState<"test" | "models" | null>(null);
  const [allowGeneration, setAllowGeneration] = useState(false);
  const [probeModel, setProbeModel] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);

  const storage = STORAGE_OPTIONS.find((o) => o.value === connection.credential_storage)?.label ?? (connection.credential_storage === "none" ? "ohne Schlüssel" : connection.credential_storage);
  const last = connection.last_test;
  const lastAt = last?.at ?? last?.tested_at ?? null;
  const lastOk = last ? Boolean(last.server_reached && last.access_ok !== false && last.model_listed !== false && last.answer_ok !== false) : null;

  const openModels = async () => {
    setPanel((p) => (p === "models" ? null : "models"));
    await loadConnectionModels(connection.id);
  };
  const setDefault = async (id: string) => {
    setBusy(true);
    await updateConnection(connection.id, { default_model: id });
    setBusy(false);
  };

  return (
    <li className="panel p-4 flex flex-col gap-3" data-testid="connection-card" data-connection-id={connection.id} data-enabled={connection.enabled}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h4 className="text-sm font-semibold text-text-primary">{connection.name}</h4>
            <Badge tone={EXECUTION_TONE[connection.execution]} title="Ausführungsort">
              {EXECUTION_LABEL[connection.execution]}
            </Badge>
            {!connection.enabled && <Badge tone="neutral">deaktiviert</Badge>}
            {lastOk !== null && (
              <span data-testid="last-test" data-ok={lastOk}>
                <Badge tone={lastOk ? "green" : "red"} title={lastAt ? `Letzter Test ${formatDateTime(lastAt)}` : "Letzter Test"}>
                  {lastOk ? "Test ok" : "Test fehlgeschlagen"}
                  {lastAt ? ` · ${formatDateTime(lastAt)}` : ""}
                </Badge>
              </span>
            )}
          </div>
          <div className="mt-1 text-xs text-text-secondary flex items-center gap-x-2 gap-y-0.5 flex-wrap">
            <span>{preset?.name ?? connection.provider}</span>
            <span>· {AUTH_LABEL[connection.auth_method]}</span>
            {connection.auth_method === "api_key" && <span>· {connection.has_credential ? `Schlüssel: ${storage}` : "kein Schlüssel hinterlegt"}</span>}
            <span>· Datenfreigabe: {DATA_SHARING_LABEL[connection.data_sharing]}</span>
            {connection.default_model && (
              <span>
                · Standardmodell <span className="font-mono text-text-primary">{connection.default_model}</span>
              </span>
            )}
          </div>
          {connection.base_url && <div className="mt-0.5 text-[11px] font-mono text-text-secondary/80 truncate">{connection.base_url}</div>}
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <Button size="sm" variant={panel === "test" ? "primary" : "secondary"} icon={<Play className="w-3.5 h-3.5" />} onClick={() => setPanel((p) => (p === "test" ? null : "test"))} data-testid="conn-test-toggle">
            Verbindung testen
          </Button>
          <Button size="sm" variant={panel === "models" ? "primary" : "secondary"} onClick={() => void openModels()} data-testid="conn-models-toggle">
            Modelle
          </Button>
          <Button size="sm" variant="ghost" icon={<Pencil className="w-3.5 h-3.5" />} onClick={onEdit} aria-label="Bearbeiten" title="Bearbeiten" data-testid="conn-edit" />
          <Button size="sm" variant="ghost" icon={<Trash2 className="w-3.5 h-3.5" />} onClick={() => setConfirmDelete(true)} aria-label="Löschen" title="Löschen" data-testid="conn-delete" className="hover:text-red-300" />
        </div>
      </div>

      <div className="-my-2">
        <Toggle label="Aktiv" description="Deaktivierte Verbindungen werden im Chat nicht angeboten." checked={connection.enabled} onChange={(v) => void updateConnection(connection.id, { enabled: v })} />
      </div>

      {panel === "test" && (
        <div className="rounded-lg border border-border bg-bg-primary/30 p-3 flex flex-col gap-3" data-testid="conn-test-panel">
          <div className="flex items-center gap-3 flex-wrap">
            <label className="inline-flex items-center gap-2 text-xs text-text-secondary">
              <input type="checkbox" checked={allowGeneration} onChange={(e) => setAllowGeneration(e.target.checked)} data-testid="conn-test-probe" />
              Probe-Antwort senden (kann Kosten verursachen)
            </label>
            {allowGeneration && (
              <input
                value={probeModel}
                onChange={(e) => setProbeModel(e.target.value)}
                placeholder={connection.default_model ?? "Modell-ID für die Probe"}
                aria-label="Probe-Modell"
                className="bg-bg-primary border border-border rounded-md px-2 py-1 text-xs font-mono w-56"
              />
            )}
            <Button size="sm" variant="primary" loading={testing} icon={<Play className="w-3.5 h-3.5" />} onClick={() => void testConnection(connection.id, allowGeneration, probeModel.trim() || null)} data-testid="conn-test-run">
              Test starten
            </Button>
          </div>
          <TestStages connection={connection} allowGeneration={allowGeneration} />
          {result && (
            <div className="text-xs text-text-secondary" data-testid="conn-test-message">
              {result.message_de}
              {result.error && <span className="text-red-300"> – {result.error.message_de}{result.error.retryable ? " (erneut versuchen möglich)" : ""}</span>}
              {typeof result.details.note_de === "string" && <span> {result.details.note_de}</span>}
              <span className="text-text-secondary/60"> · {result.latency_ms} ms</span>
            </div>
          )}
        </div>
      )}

      {panel === "models" && (
        <div className="rounded-lg border border-border bg-bg-primary/30" data-testid="conn-models-panel">
          <div className="flex items-center justify-between px-3 py-2 border-b border-border/60 text-xs text-text-secondary">
            <span>Modelle laut Anbieter · Fähigkeiten: geprüft / laut Anbieter / unbekannt / nein</span>
            <Button size="sm" variant="ghost" icon={<RefreshCw className={cn("w-3 h-3", modelsLoading && "animate-spin")} />} onClick={() => void loadConnectionModels(connection.id, true)} disabled={modelsLoading}>
              Aktualisieren
            </Button>
          </div>
          {modelsLoading && !models ? (
            <div className="flex justify-center py-4">
              <LoadingSpinner size="sm" />
            </div>
          ) : modelsError ? (
            <div className="px-3 py-3 text-xs text-red-300" data-testid="conn-models-error">
              {modelsError}
            </div>
          ) : !models || models.length === 0 ? (
            <div className="px-3 py-3 text-xs text-text-secondary">Keine Modelle gelistet – die Modell-ID kann im Chat von Hand eingegeben werden.</div>
          ) : (
            <ul className="divide-y divide-border/60 max-h-72 overflow-y-auto">
              {models.map((m) => (
                <ModelRow key={m.id} model={m} isDefault={connection.default_model === m.id} onDefault={() => void setDefault(m.id)} busy={busy} />
              ))}
            </ul>
          )}
        </div>
      )}

      <Dialog.Root open={confirmDelete} onOpenChange={(o) => !o && setConfirmDelete(false)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[55]" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-[55] w-[min(440px,calc(100vw-2rem))] bg-bg-secondary border border-border rounded-xl p-5 shadow-2xl focus:outline-none">
            <Dialog.Title className="text-sm font-semibold">Verbindung löschen?</Dialog.Title>
            <Dialog.Description className="mt-2 text-xs text-text-secondary leading-relaxed">
              „{connection.name}“ wird samt hinterlegtem Schlüssel, Fähigkeitsprofilen und Freigaben entfernt. Gespräche, die diese Verbindung nutzen, fallen auf das lokale Modell zurück.
            </Dialog.Description>
            <div className="mt-4 flex justify-end gap-2">
              <Button onClick={() => setConfirmDelete(false)}>Abbrechen</Button>
              <Button
                variant="danger"
                onClick={() => {
                  setConfirmDelete(false);
                  void deleteConnection(connection.id);
                }}
                data-testid="conn-delete-confirm"
              >
                Löschen
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </li>
  );
}
