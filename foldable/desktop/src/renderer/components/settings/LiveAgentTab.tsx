/**
 * Settings -> "Live & Agent": hotkeys (validated accelerator syntax, "Prüfen"
 * registers them for real and shows the result), tray mode, live preview,
 * agent budget. Persisted through PATCH /api/settings; the launcher
 * re-registers hotkeys / applies the tray mode after a successful PATCH.
 */
import { Download, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import { validateAccelerator } from "../../../shared-types/accelerator";
import { DEFAULT_HOTKEYS, type HotkeyResult } from "../../../shared-types/bridge";
import { isTerminalJob } from "../../api/types";
import { formatBytes } from "../../lib/format";
import { useAppStore } from "../../store/useAppStore";
import JobProgress from "../models/JobProgress";
import { Badge, Button, Field, inputClass, KeyValue, SectionTitle, Toggle } from "../ui";

/** v1.5: read-only facts about the execution core (AgentCapabilities.execution_core). */
function ExecutionCoreSection() {
  const client = useAppStore((s) => s.client);
  const capabilities = useAppStore((s) => s.agentCapabilities);
  const loadAgentCapabilities = useAppStore((s) => s.loadAgentCapabilities);

  useEffect(() => {
    if (client && !capabilities) void loadAgentCapabilities();
  }, [client, capabilities, loadAgentCapabilities]);

  const core = capabilities?.execution_core ?? null;
  return (
    <section data-testid="execution-core">
      <SectionTitle
        action={
          <Button size="sm" variant="ghost" onClick={() => void loadAgentCapabilities()} aria-label="Ausführungskern neu laden" icon={<RefreshCw className="w-3.5 h-3.5" />} />
        }
      >
        Ausführungskern
      </SectionTitle>
      <div className="panel px-4">
        {!capabilities ? (
          <div className="py-3 text-xs text-text-secondary">Wird geladen …</div>
        ) : !core ? (
          <div className="py-3 text-xs text-text-secondary">Dieser Kern meldet keinen Ausführungskern (älter als Vertrag 1.5).</div>
        ) : (
          <div className="py-1">
            <KeyValue label="Version" value={core.version} mono />
            <KeyValue label="Abläufe gesamt" value={String(core.workflows)} />
            <KeyValue
              label="Abläufe mit geänderten Abhängigkeiten"
              value={core.stale_workflows > 0 ? <span className="text-amber-300">{core.stale_workflows}</span> : "0"}
            />
            <KeyValue
              label="Eingabesperre"
              value={
                <span className="inline-flex items-center gap-1.5 flex-wrap justify-end">
                  <Badge tone={core.input_guard.enabled ? "green" : "yellow"}>{core.input_guard.enabled ? "aktiv" : "aus"}</Badge>
                  {core.input_guard.paused_by_user > 0 && <span>{core.input_guard.paused_by_user}× vom Nutzer unterbrochen</span>}
                  {typeof core.input_guard.user_idle_ms === "number" && <span>Nutzer inaktiv seit {Math.round(core.input_guard.user_idle_ms / 1000)} s</span>}
                </span>
              }
            />
            <KeyValue label="Rechner-Kennung" value={core.host_id.length > 12 ? `${core.host_id.slice(0, 12)}…` : core.host_id} mono />
            <KeyValue label="Modell-Werkzeugaufrufe" value={capabilities.model?.native_tool_calls === "verified" ? "nativ geprüft" : capabilities.model?.native_tool_calls === "failed" ? "nativ fehlgeschlagen (JSON-Schema)" : "nativ ungeprüft"} />
            {core.teach && core.teach.active > 0 && <KeyValue label="Laufende Aufnahmen" value={String(core.teach.active)} />}
          </div>
        )}
      </div>
    </section>
  );
}

/** Settings row for the on-demand browser package (GET /api/agent/browser). */
function BrowserRow() {
  const client = useAppStore((s) => s.client);
  const status = useAppStore((s) => s.browserStatus);
  const job = useAppStore((s) => s.browserJob);
  const loadBrowserStatus = useAppStore((s) => s.loadBrowserStatus);
  const installBrowser = useAppStore((s) => s.installBrowser);
  const cancelBrowserJob = useAppStore((s) => s.cancelBrowserJob);

  useEffect(() => {
    if (client && !status) void loadBrowserStatus();
  }, [client, status, loadBrowserStatus]);

  const running = Boolean(job && !isTerminalJob(job));
  const size = `≈ ${status?.package?.size ? formatBytes(status.package.size, 0) : "207 MB"}`;
  let text: string;
  if (!status) text = "Status wird geladen …";
  else if (status.installed) text = `Installiert${status.version ? ` · Version ${status.version}` : ""}${status.playwright ? " · Playwright-fähig" : ""}${status.path ? ` · ${status.path}` : ""}`;
  else text = `Nicht installiert${status.reason_de ? ` – ${status.reason_de}` : ""}`;

  return (
    <div className="py-3" data-testid="browser-row" data-installed={status ? String(status.installed) : undefined}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-sm font-medium text-text-primary flex items-center gap-2">
            Eigener Browser für Aufgaben
            {status && <Badge tone={status.installed ? "green" : "yellow"}>{status.installed ? "installiert" : "nicht installiert"}</Badge>}
          </div>
          <div className="text-xs text-text-secondary mt-0.5 break-all" data-testid="browser-status">
            {text}
          </div>
          {status?.package && (
            <div className="text-[11px] text-text-secondary/80 mt-0.5">
              Paket: {status.package.name} {status.package.revision} · {formatBytes(status.package.size)} · Lizenz {status.package.license}
            </div>
          )}
        </div>
        <div className="flex-shrink-0 flex items-center gap-1.5">
          {status && !status.installed && (
            <Button size="sm" variant="primary" onClick={() => void installBrowser()} disabled={running} loading={running} icon={<Download className="w-3.5 h-3.5" />} data-testid="browser-install">
              Browser laden ({size})
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => void loadBrowserStatus()} aria-label="Browser-Status neu laden" icon={<RefreshCw className="w-3.5 h-3.5" />} />
        </div>
      </div>
      {job && (
        <div className="mt-3">
          <JobProgress job={job} title="Browser-Paket" onCancel={running ? () => void cancelBrowserJob() : undefined} />
        </div>
      )}
    </div>
  );
}

function HotkeyInput({ label, description, value, onChange, testId }: { label: string; description: string; value: string; onChange: (v: string) => void; testId: string }) {
  const check = validateAccelerator(value);
  return (
    <div className="py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-sm font-medium text-text-primary">{label}</div>
          <div className="text-xs text-text-secondary mt-0.5">{description}</div>
        </div>
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-label={label}
          aria-invalid={!check.ok}
          spellCheck={false}
          className={cn(inputClass, "w-56 font-mono", !check.ok && "border-red-500/60")}
          data-testid={testId}
        />
      </div>
      <div className={cn("mt-1 text-[11px] text-right", check.ok ? "text-text-secondary" : "text-red-300")} data-testid={`${testId}-check`}>
        {check.ok ? `gültig: ${check.normalized}` : check.error}
      </div>
    </div>
  );
}

export default function LiveAgentTab() {
  const settings = useAppStore((s) => s.settings);
  const loadSettings = useAppStore((s) => s.loadSettings);
  const updateSettings = useAppStore((s) => s.updateSettings);

  const [overlayKey, setOverlayKey] = useState(settings?.hotkey_overlay ?? DEFAULT_HOTKEYS.overlay);
  const [stopKey, setStopKey] = useState(settings?.hotkey_stop ?? DEFAULT_HOTKEYS.stop);
  const [result, setResult] = useState<HotkeyResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [maxSteps, setMaxSteps] = useState(String(settings?.agent_max_steps ?? 20));
  const [maxSeconds, setMaxSeconds] = useState(String(settings?.agent_max_seconds ?? 600));

  useEffect(() => {
    if (!settings) void loadSettings();
  }, [settings, loadSettings]);

  useEffect(() => {
    if (!settings) return;
    setOverlayKey(settings.hotkey_overlay ?? DEFAULT_HOTKEYS.overlay);
    setStopKey(settings.hotkey_stop ?? DEFAULT_HOTKEYS.stop);
    setMaxSteps(String(settings.agent_max_steps ?? 20));
    setMaxSeconds(String(settings.agent_max_seconds ?? 600));
  }, [settings]);

  if (!settings) {
    return (
      <div className="flex justify-center py-10">
        <LoadingSpinner />
      </div>
    );
  }

  const overlayCheck = validateAccelerator(overlayKey);
  const stopCheck = validateAccelerator(stopKey);
  const bothValid = overlayCheck.ok && stopCheck.ok;
  const dirty = overlayKey !== (settings.hotkey_overlay ?? DEFAULT_HOTKEYS.overlay) || stopKey !== (settings.hotkey_stop ?? DEFAULT_HOTKEYS.stop);

  const check = async () => {
    setChecking(true);
    try {
      setResult(await window.spaider.registerHotkeys({ overlay: overlayKey, stop: stopKey }));
    } finally {
      setChecking(false);
    }
  };
  const save = async () => {
    const ok = await updateSettings({ hotkey_overlay: overlayCheck.normalized, hotkey_stop: stopCheck.normalized });
    if (ok) setResult(null);
  };
  const saveBudget = (key: "agent_max_steps" | "agent_max_seconds", raw: string, min: number, max: number) => {
    const n = Number(raw);
    if (!Number.isInteger(n) || n < min || n > max) {
      if (key === "agent_max_steps") setMaxSteps(String(settings.agent_max_steps ?? 20));
      else setMaxSeconds(String(settings.agent_max_seconds ?? 600));
      return;
    }
    if (n !== settings[key]) void updateSettings({ [key]: n });
  };

  return (
    <div className="flex flex-col gap-8 max-w-2xl" data-testid="live-agent-tab">
      <section>
        <SectionTitle>Hotkeys</SectionTitle>
        <div className="panel px-4 divide-y divide-border">
          <HotkeyInput label="Live-Fenster" description="Öffnet das Live-Fenster über dem aktiven Programm (Electron-Accelerator-Syntax, z. B. Ctrl+Alt+Space)." value={overlayKey} onChange={setOverlayKey} testId="hotkey-overlay" />
          <HotkeyInput label="Stopp" description="Stoppt sofort alle Antworten und Aufgaben." value={stopKey} onChange={setStopKey} testId="hotkey-stop" />
          <div className="py-3 flex items-center gap-2 flex-wrap">
            <Button size="sm" onClick={() => void check()} disabled={!bothValid || checking} loading={checking} data-testid="hotkey-check">
              Prüfen
            </Button>
            <Button size="sm" variant="primary" onClick={() => void save()} disabled={!bothValid || !dirty} data-testid="hotkey-save">
              Übernehmen
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void window.spaider.overlay.toggle()}>
              Live-Fenster testen
            </Button>
            {result && (
              <div className="w-full mt-1 text-xs" data-testid="hotkey-result">
                <div className={result.overlay ? "text-emerald-300" : "text-red-300"}>Live-Fenster: {result.overlay ? "registriert" : "nicht registriert"}</div>
                <div className={result.stop ? "text-emerald-300" : "text-red-300"}>Stopp: {result.stop ? "registriert" : "nicht registriert"}</div>
                {result.errors.map((e, i) => (
                  <div key={i} className="text-red-300">
                    {e}
                  </div>
                ))}
                {dirty && result.overlay && result.stop && <div className="text-text-secondary mt-1">Noch nicht gespeichert – „Übernehmen“ hält die Kombinationen dauerhaft fest.</div>}
              </div>
            )}
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>Fenster</SectionTitle>
        <div className="panel px-4 divide-y divide-border">
          <Toggle
            label="Beim Schließen in den Infobereich minimieren"
            description="SpAIder bleibt mit Tray-Symbol aktiv (Hotkeys funktionieren weiter). Es wird kein Autostart eingerichtet."
            checked={settings.tray_mode ?? false}
            onChange={(v) => void updateSettings({ tray_mode: v })}
          />
          <Toggle
            label="Vorschaubild im Live-Fenster"
            description="Kleines Vorschaubild der Quelle (max. 480 px breit), das der Kern liefert. Das Vollbild bleibt im Kern."
            checked={settings.live_preview ?? true}
            onChange={(v) => void updateSettings({ live_preview: v })}
          />
        </div>
      </section>

      <section>
        <SectionTitle>Aufgaben (Agent)</SectionTitle>
        <div className="panel px-4 divide-y divide-border">
          <BrowserRow />
          <Field label="Maximale Schritte je Aufgabe" description="Voreinstellung für neue Aufgaben (1 bis 500).">
            <input
              type="number"
              min={1}
              max={500}
              value={maxSteps}
              onChange={(e) => setMaxSteps(e.target.value)}
              onBlur={() => saveBudget("agent_max_steps", maxSteps, 1, 500)}
              aria-label="Maximale Schritte je Aufgabe"
              className={cn(inputClass, "w-24 text-right")}
              data-testid="agent-max-steps"
            />
          </Field>
          <Field label="Zeitlimit je Aufgabe (Sekunden)" description="Voreinstellung für neue Aufgaben (10 bis 86400).">
            <input
              type="number"
              min={10}
              max={86400}
              value={maxSeconds}
              onChange={(e) => setMaxSeconds(e.target.value)}
              onBlur={() => saveBudget("agent_max_seconds", maxSeconds, 10, 86400)}
              aria-label="Zeitlimit je Aufgabe"
              className={cn(inputClass, "w-24 text-right")}
              data-testid="agent-max-seconds"
            />
          </Field>
        </div>
      </section>

      <ExecutionCoreSection />
    </div>
  );
}
