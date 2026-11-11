/**
 * Chat header: the active binding of the conversation (local model or
 * connection + model with its execution badge) and its project; a dialog
 * picks "Lokales Modell" or any enabled connection + model id (manual input
 * when the provider lists nothing) and changes the project.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { ChevronDown, Cpu, FolderLock, Globe, X } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import type { Connection } from "../../api/types";
import { bindingLabel, EXECUTION_LABEL, EXECUTION_TONE, selectableConnections } from "../../store/hub";
import { useAppStore } from "../../store/useAppStore";
import { useHubStore } from "../../store/useHubStore";
import { Badge, Button, inputClass, Select } from "../ui";

function ConnectionChoice({ connection, selected, modelId, onSelect }: { connection: Connection; selected: boolean; modelId: string; onSelect: (modelId: string) => void }) {
  const models = useHubStore((s) => s.modelsByConnection[connection.id]);
  const loading = useHubStore((s) => Boolean(s.modelsLoading[connection.id]));
  const loadConnectionModels = useHubStore((s) => s.loadConnectionModels);
  const [manual, setManual] = useState(modelId);
  useEffect(() => {
    if (selected && !models) void loadConnectionModels(connection.id);
  }, [selected, models, connection.id, loadConnectionModels]);
  const options = (models ?? []).map((m) => ({ value: m.id, label: m.display_name && m.display_name !== m.id ? `${m.id} – ${m.display_name}` : m.id }));
  return (
    <div className={cn("rounded-lg border px-3 py-2", selected ? "border-accent-blue/60 bg-accent-blue/5" : "border-border")} data-testid="binding-connection" data-connection-id={connection.id}>
      <button type="button" className="w-full flex items-center gap-2 text-left" onClick={() => onSelect(modelId || connection.default_model || "")}>
        <input type="radio" readOnly checked={selected} aria-label={connection.name} />
        <span className="text-sm text-text-primary">{connection.name}</span>
        <Badge tone={EXECUTION_TONE[connection.execution]}>{EXECUTION_LABEL[connection.execution]}</Badge>
      </button>
      {selected && (
        <div className="mt-2 flex items-center gap-2 flex-wrap">
          {options.length > 0 ? (
            <Select value={modelId} onChange={onSelect} options={modelId && !options.some((o) => o.value === modelId) ? [{ value: modelId, label: modelId }, ...options] : options} ariaLabel="Modell" />
          ) : (
            <input value={manual} onChange={(e) => setManual(e.target.value)} onBlur={() => onSelect(manual.trim())} placeholder={loading ? "Modellliste wird geladen …" : "Modell-ID von Hand (keine Liste vom Anbieter)"} className={cn(inputClass, "font-mono text-xs")} aria-label="Modell-ID" data-testid="binding-manual-model" />
          )}
        </div>
      )}
    </div>
  );
}

export default function BindingPicker({ conversationId }: { conversationId: string }) {
  const binding = useAppStore((s) => s.bindings[conversationId] ?? null);
  const localModel = useAppStore((s) => s.setup?.active_chat_model?.name ?? null);
  const conversation = useAppStore((s) => s.conversations.find((c) => c.id === conversationId) ?? null);
  const setBinding = useAppStore((s) => s.setBinding);
  const setConversationProject = useAppStore((s) => s.setConversationProject);
  const openSettings = useAppStore((s) => s.openSettings);
  const connections = useHubStore((s) => s.connections);
  const projects = useHubStore((s) => s.projects);
  const loadConnections = useHubStore((s) => s.loadConnections);
  const loadProjects = useHubStore((s) => s.loadProjects);
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<{ kind: "local" } | { kind: "connection"; connectionId: string; modelId: string }>({ kind: "local" });
  const [busy, setBusy] = useState(false);

  const label = bindingLabel(binding, localModel);
  const projectId = conversation?.project_id ?? binding?.project?.id ?? "default";
  const project = binding?.project ?? projects.find((p) => p.id === projectId) ?? null;

  useEffect(() => {
    if (!open) return;
    void loadConnections();
    void loadProjects();
    setChoice(binding && binding.binding.kind === "connection" ? { kind: "connection", connectionId: binding.binding.connection_id ?? "", modelId: binding.binding.provider_model_id ?? "" } : { kind: "local" });
  }, [open, binding, loadConnections, loadProjects]);

  const apply = async () => {
    setBusy(true);
    try {
      if (choice.kind === "local") await setBinding(conversationId, { kind: "local" });
      else if (choice.connectionId && choice.modelId) await setBinding(conversationId, { kind: "connection", connection_id: choice.connectionId, provider_model_id: choice.modelId });
      setOpen(false);
    } finally {
      setBusy(false);
    }
  };
  const canApply = choice.kind === "local" || Boolean(choice.connectionId && choice.modelId.trim());

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-bg-secondary px-2 py-1 text-[11px] text-text-secondary hover:text-text-primary transition-colors max-w-[46%]"
        title="Modell und Projekt dieses Gesprächs"
        data-testid="binding-button"
        data-binding-kind={label.kind}
      >
        {label.kind === "local" ? <Cpu className="w-3 h-3 text-accent-cyan flex-shrink-0" /> : <Globe className="w-3 h-3 text-accent-blue flex-shrink-0" />}
        <span className="truncate" data-testid="binding-title">
          {label.title}
        </span>
        <Badge tone={EXECUTION_TONE[label.execution]}>{EXECUTION_LABEL[label.execution]}</Badge>
        {project && (
          <span className="inline-flex items-center gap-1 text-text-secondary/80" data-testid="binding-project">
            {project.local_only && <FolderLock className="w-3 h-3 text-amber-300" />}
            {project.name}
            {project.local_only && <Badge tone="yellow">Nur lokal</Badge>}
          </span>
        )}
        <ChevronDown className="w-3 h-3 flex-shrink-0" />
      </button>

      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 data-[state=open]:animate-fade-in" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[min(560px,calc(100vw-2rem))] max-h-[calc(100vh-2rem)] overflow-y-auto bg-bg-secondary border border-border rounded-xl p-5 shadow-2xl focus:outline-none" data-testid="binding-dialog">
            <div className="flex items-start justify-between gap-3">
              <div>
                <Dialog.Title className="text-sm font-semibold">Modell für dieses Gespräch</Dialog.Title>
                <Dialog.Description className="mt-1 text-xs text-text-secondary">Lokal bleibt alles auf diesem Rechner. Eine Verbindung sendet den Gesprächskontext an den gewählten Anbieter – je Projekt mit Freigabe.</Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <button type="button" aria-label="Schließen" className="text-text-secondary hover:text-text-primary">
                  <X className="w-4 h-4" />
                </button>
              </Dialog.Close>
            </div>

            <div className="mt-4 flex flex-col gap-2">
              <button type="button" className={cn("flex items-center gap-2 rounded-lg border px-3 py-2 text-left", choice.kind === "local" ? "border-accent-blue/60 bg-accent-blue/5" : "border-border")} onClick={() => setChoice({ kind: "local" })} data-testid="binding-local">
                <input type="radio" readOnly checked={choice.kind === "local"} aria-label="Lokales Modell" />
                <span className="text-sm text-text-primary">Lokales Modell</span>
                <span className="text-xs text-text-secondary">{localModel ?? "kein Modell aktiv"} · eingebettete Laufzeit</span>
                <Badge tone="green">lokal</Badge>
              </button>
              {selectableConnections(connections).map((c) => (
                <ConnectionChoice
                  key={c.id}
                  connection={c}
                  selected={choice.kind === "connection" && choice.connectionId === c.id}
                  modelId={choice.kind === "connection" && choice.connectionId === c.id ? choice.modelId : (c.default_model ?? "")}
                  onSelect={(modelId) => setChoice({ kind: "connection", connectionId: c.id, modelId })}
                />
              ))}
              {selectableConnections(connections).length === 0 && <div className="text-xs text-text-secondary px-1">Keine aktive Verbindung – unter Einstellungen → Verbindungen einrichten.</div>}
            </div>

            <div className="mt-4 flex items-center justify-between gap-3">
              <span className="text-xs text-text-secondary">Projekt des Gesprächs</span>
              <Select
                value={projectId}
                onChange={(v) => void setConversationProject(conversationId, v)}
                options={(projects.length ? projects : project ? [project] : []).map((p) => ({ value: p.id, label: p.local_only ? `${p.name} (Nur lokal)` : p.name }))}
                ariaLabel="Projekt"
              />
            </div>
            {binding && !binding.policy.allowed && (
              <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200" data-testid="binding-policy">
                {binding.policy.reason_de}
              </div>
            )}

            <div className="mt-4 flex items-center justify-between gap-2">
              <Button size="sm" variant="ghost" onClick={() => { setOpen(false); openSettings("connections"); }}>
                Verbindungen verwalten
              </Button>
              <div className="flex gap-2">
                <Button onClick={() => setOpen(false)} disabled={busy}>
                  Abbrechen
                </Button>
                <Button variant="primary" onClick={() => void apply()} disabled={!canApply || busy} loading={busy} data-testid="binding-apply">
                  Übernehmen
                </Button>
              </div>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
