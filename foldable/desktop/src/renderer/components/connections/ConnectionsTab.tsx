/**
 * Settings -> "Verbindungen": the key store (host / portable vault), the
 * connections with test + model list, the provider cards (equal weight –
 * ChatGPT is one card among others) to add a connection from, and the
 * consents per project with revoke.
 */
import { KeyRound, Lock, LockOpen, Plus, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import type { Connection, ProviderPreset } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import { AUTH_LABEL, CAPABILITY_KEY_LABEL, CAPABILITY_KEYS, CAPABILITY_LABEL, CAPABILITY_TONE, EXECUTION_LABEL, EXECUTION_TONE, PROTOCOL_LABEL } from "../../store/hub";
import { useAppStore } from "../../store/useAppStore";
import { useHubStore } from "../../store/useHubStore";
import { Badge, Button, EmptyState, SectionTitle } from "../ui";
import ConnectionCard from "./ConnectionCard";
import ConnectionForm from "./ConnectionForm";
import VaultDialog from "./VaultDialog";

function ProviderCard({ preset, onAdd }: { preset: ProviderPreset; onAdd: () => void }) {
  const local = preset.id === "llama_local";
  return (
    <li className="panel p-4 flex flex-col gap-2" data-testid="provider-card" data-provider={preset.id}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 className="text-sm font-semibold text-text-primary leading-snug">{preset.name}</h4>
          <div className="mt-1 flex items-center gap-1.5 flex-wrap">
            <Badge tone={EXECUTION_TONE[preset.execution]}>{EXECUTION_LABEL[preset.execution]}</Badge>
            <span className="text-[11px] text-text-secondary">{PROTOCOL_LABEL[preset.protocol] ?? preset.protocol}</span>
            {preset.connections.length > 0 && <Badge tone="blue">{preset.connections.length} Verbindung(en)</Badge>}
          </div>
        </div>
        {!local && (
          <Button size="sm" variant="primary" icon={<Plus className="w-3.5 h-3.5" />} onClick={onAdd} data-testid="provider-add">
            Verbindung einrichten
          </Button>
        )}
      </div>
      <div className="text-[11px] text-text-secondary">Zugang: {preset.auth_methods.map((m) => AUTH_LABEL[m]).join(" oder ")}</div>
      <div className="flex items-center gap-1 flex-wrap">
        {CAPABILITY_KEYS.map((k) => (
          <Badge key={k} tone={CAPABILITY_TONE[preset.capabilities[k]]} title={`${CAPABILITY_KEY_LABEL[k]}: ${CAPABILITY_LABEL[preset.capabilities[k]]}`}>
            {CAPABILITY_KEY_LABEL[k]}: {CAPABILITY_LABEL[preset.capabilities[k]]}
          </Badge>
        ))}
      </div>
      {preset.subscription_note_de && (
        <p className="text-xs text-amber-200/90 leading-relaxed" data-testid="provider-subscription-note">
          {preset.subscription_note_de}
        </p>
      )}
      {preset.data_note_de && (
        <p className="text-xs text-text-secondary leading-relaxed" data-testid="provider-data-note">
          {preset.data_note_de}
        </p>
      )}
      {preset.capabilities.notes_de && <p className="text-[11px] text-text-secondary/80 leading-relaxed">{preset.capabilities.notes_de}</p>}
      {(preset.key_url || preset.docs_url) && (
        <div className="text-[11px] text-text-secondary/80 font-mono break-all">
          {preset.key_url && <div>Schlüssel: {preset.key_url}</div>}
          {preset.docs_url && <div>Doku: {preset.docs_url}</div>}
        </div>
      )}
    </li>
  );
}

function KeyStore() {
  const store = useHubStore((s) => s.credentialStore);
  const openVault = useHubStore((s) => s.openVault);
  const vaultLock = useHubStore((s) => s.vaultLock);
  if (!store) return null;
  const v = store.vault;
  return (
    <div className="panel p-4 flex items-center gap-4 flex-wrap" data-testid="key-store">
      <KeyRound className="w-4 h-4 text-accent-blue flex-shrink-0" />
      <div className="min-w-0 flex-1 text-xs text-text-secondary leading-relaxed">
        <div>
          <span className="text-text-primary">Rechnergebundene Ablage (DPAPI):</span> {store.host_available ? "verfügbar" : "nicht verfügbar – Schlüssel nur im Tresor oder je Sitzung"}
        </div>
        <div>
          <span className="text-text-primary">Portabler Tresor:</span>{" "}
          {!v.exists ? "nicht angelegt" : v.unlocked ? "entsperrt" : "gesperrt"}
          {v.exists && <span className="font-mono"> · {v.path}</span>}
          {store.session_items > 0 && <span> · {store.session_items} Schlüssel nur in dieser Sitzung</span>}
        </div>
      </div>
      <div className="flex items-center gap-2">
        {!v.exists && (
          <Button size="sm" onClick={() => openVault("create")} data-testid="vault-create">
            Tresor anlegen
          </Button>
        )}
        {v.exists && !v.unlocked && (
          <Button size="sm" icon={<LockOpen className="w-3.5 h-3.5" />} onClick={() => openVault("unlock")} data-testid="vault-unlock">
            Tresor entsperren
          </Button>
        )}
        {v.exists && v.unlocked && (
          <Button size="sm" icon={<Lock className="w-3.5 h-3.5" />} onClick={() => void vaultLock()} data-testid="vault-lock">
            Tresor sperren
          </Button>
        )}
      </div>
    </div>
  );
}

function ConsentsSection() {
  const projects = useHubStore((s) => s.projects);
  const consents = useHubStore((s) => s.consents);
  const connections = useHubStore((s) => s.connections);
  const revokeConsent = useHubStore((s) => s.revokeConsent);
  const name = (id: string) => connections.find((c) => c.id === id)?.name ?? id;
  return (
    <section>
      <SectionTitle>Freigaben je Projekt</SectionTitle>
      {consents.length === 0 ? (
        <div className="text-xs text-text-secondary px-1">Noch keine Freigaben. Eine Freigabe entsteht, wenn du im Chat Inhalte eines Projekts erstmals an eine Verbindung sendest.</div>
      ) : (
        <ul className="panel divide-y divide-border/60" data-testid="consents">
          {consents.map((c) => {
            const project = projects.find((p) => p.id === c.project_id);
            return (
              <li key={`${c.connection_id}:${c.project_id}`} className="flex items-center gap-3 px-3 py-2 text-xs" data-testid="consent-row">
                <span className="text-text-primary">{project?.name ?? c.project_id}</span>
                {project?.local_only && <Badge tone="yellow">Nur lokal</Badge>}
                <span className="text-text-secondary">→ {name(c.connection_id)}</span>
                <span className="text-text-secondary/70 ml-auto">{formatDateTime(c.granted_at)}</span>
                <Button size="sm" variant="ghost" onClick={() => void revokeConsent(c.connection_id, c.project_id)} data-testid="consent-revoke">
                  Widerrufen
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      {projects.some((p) => p.local_only) && (
        <div className="mt-2 text-[11px] text-text-secondary px-1">
          „Nur lokal“-Projekte: {projects.filter((p) => p.local_only).map((p) => p.name).join(", ")} – dort sind keine Verbindungen außerhalb dieses Rechners möglich.
        </div>
      )}
    </section>
  );
}

export default function ConnectionsTab() {
  const client = useAppStore((s) => s.client);
  const providers = useHubStore((s) => s.providers);
  const providersLoaded = useHubStore((s) => s.providersLoaded);
  const providersError = useHubStore((s) => s.providersError);
  const connections = useHubStore((s) => s.connections);
  const connectionsLoaded = useHubStore((s) => s.connectionsLoaded);
  const loadAll = useHubStore((s) => s.loadAll);
  const [form, setForm] = useState<{ preset: ProviderPreset; existing: Connection | null } | null>(null);
  const [showProviders, setShowProviders] = useState(false);

  useEffect(() => {
    if (client) void loadAll();
  }, [client, loadAll]);

  const presetOf = (c: Connection) => providers.find((p) => p.id === c.provider) ?? null;
  const providersVisible = showProviders || connections.length === 0;

  return (
    <div className="flex flex-col gap-8" data-testid="connections-tab">
      <KeyStore />

      <section>
        <SectionTitle
          action={
            <div className="flex items-center gap-2">
              <Button size="sm" variant="ghost" icon={<RefreshCw className="w-3.5 h-3.5" />} onClick={() => void loadAll()}>
                Aktualisieren
              </Button>
              <Button size="sm" variant="primary" icon={<Plus className="w-3.5 h-3.5" />} onClick={() => setShowProviders((v) => !v)} data-testid="add-provider">
                Anbieter hinzufügen
              </Button>
            </div>
          }
        >
          Verbindungen
        </SectionTitle>
        {!connectionsLoaded ? (
          <div className="flex justify-center py-6">
            <LoadingSpinner size="sm" />
          </div>
        ) : connections.length === 0 ? (
          <EmptyState title="Noch keine Verbindung" description="Das eingebettete lokale Modell braucht keine Verbindung. Eine Verbindung ergänzt eigene Server oder Anbieter mit eigenem Schlüssel – unten einen Anbieter wählen." className="py-6" />
        ) : (
          <ul className="flex flex-col gap-3">
            {connections.map((c) => (
              <ConnectionCard key={c.id} connection={c} preset={presetOf(c)} onEdit={() => setForm({ preset: presetOf(c) ?? providers[0], existing: c })} />
            ))}
          </ul>
        )}
      </section>

      {providersVisible && (
        <section data-testid="providers">
          <SectionTitle>Anbieter</SectionTitle>
          {!providersLoaded ? (
            <div className="flex justify-center py-6">
              <LoadingSpinner size="sm" />
            </div>
          ) : providersError ? (
            <div className="text-xs text-red-300">{providersError}</div>
          ) : (
            <ul className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              {providers.map((p) => (
                <ProviderCard key={p.id} preset={p} onAdd={() => setForm({ preset: p, existing: null })} />
              ))}
            </ul>
          )}
        </section>
      )}

      <ConsentsSection />

      {form && (
        <ConnectionForm
          preset={form.preset}
          existing={form.existing}
          onClose={() => setForm(null)}
        />
      )}
      <VaultDialog />
    </div>
  );
}
