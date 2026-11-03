/**
 * "Neue Aufgabe": goal + scopes (files via native folder picker, browser,
 * windows from GET /api/live/sources + allowed processes, memory,
 * external effects default off) + budget from the settings. Scopes come
 * exclusively from this form; nothing is started without "Starten".
 */
import { AlertTriangle, FolderPlus, Plus, RefreshCw, X } from "lucide-react";
import { useEffect, useState, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils";
import type { LiveSources } from "../../api/types";
import { errorMessage } from "../../api/client";
import { selectableSources } from "../../store/overlay";
import { validateDraft } from "../../store/tasks";
import { useAppStore } from "../../store/useAppStore";
import { Button, Field, inputClass, SectionTitle, Toggle } from "../ui";

function Chip({ text, onRemove, label }: { text: string; onRemove: () => void; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-border bg-bg-primary/60 px-2 py-0.5 text-[11px] font-mono max-w-full">
      <span className="truncate" title={text}>
        {text}
      </span>
      <button type="button" onClick={onRemove} aria-label={`${label} entfernen`} className="text-text-secondary hover:text-red-300">
        <X className="w-3 h-3" />
      </button>
    </span>
  );
}

export default function TaskCreateForm() {
  const client = useAppStore((s) => s.client);
  const draft = useAppStore((s) => s.taskDraft);
  const setDraft = useAppStore((s) => s.setTaskDraft);
  const submitTask = useAppStore((s) => s.submitTask);
  const submitting = useAppStore((s) => s.taskSubmitting);
  const capabilities = useAppStore((s) => s.agentCapabilities);
  const recipes = useAppStore((s) => s.recipes);
  const browserStatus = useAppStore((s) => s.browserStatus);
  const loadBrowserStatus = useAppStore((s) => s.loadBrowserStatus);
  const openSettings = useAppStore((s) => s.openSettings);

  // The browser package is downloaded on demand; without it the scope is not offered.
  useEffect(() => {
    if (client && !browserStatus) void loadBrowserStatus();
  }, [client, browserStatus, loadBrowserStatus]);
  const browserMissing = browserStatus !== null && !browserStatus.installed;
  useEffect(() => {
    if (browserMissing && draft.browser) setDraft({ browser: false });
  }, [browserMissing, draft.browser, setDraft]);

  const [sources, setSources] = useState<LiveSources | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [processInput, setProcessInput] = useState("");

  const loadSources = async () => {
    if (!client) return;
    try {
      setSources(await client.liveSources());
      setSourcesError(null);
    } catch (e) {
      setSourcesError(errorMessage(e));
    }
  };

  useEffect(() => {
    if (draft.windowsEnabled && !sources && client) void loadSources();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.windowsEnabled, client]);

  const problem = validateDraft(draft);
  const recipe = draft.recipe_id ? recipes.find((r) => r.id === draft.recipe_id) : null;
  const { windows, screens } = selectableSources(sources);

  const addFolder = async () => {
    const folder = await window.spaider.pickFolder();
    if (folder && !draft.fileRoots.includes(folder)) setDraft({ fileRoots: [...draft.fileRoots, folder] });
  };
  const pickWriteDir = async () => {
    const folder = await window.spaider.pickFolder();
    if (folder) setDraft({ writeDir: folder });
  };
  const toggleSource = (id: string, process: string | null) => {
    const has = draft.windowSourceIds.includes(id);
    const windowSourceIds = has ? draft.windowSourceIds.filter((x) => x !== id) : [...draft.windowSourceIds, id];
    const allowedProcesses = !has && process && !draft.allowedProcesses.includes(process) ? [...draft.allowedProcesses, process] : draft.allowedProcesses;
    setDraft({ windowSourceIds, allowedProcesses });
  };
  const addProcess = () => {
    const p = processInput.trim().toLowerCase();
    if (p && !draft.allowedProcesses.includes(p)) setDraft({ allowedProcesses: [...draft.allowedProcesses, p] });
    setProcessInput("");
  };
  const onProcessKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addProcess();
    }
  };

  const unavailable = capabilities?.tools.filter((t) => !t.available) ?? [];

  return (
    <form
      className="flex flex-col gap-6 max-w-2xl"
      data-testid="task-form"
      onSubmit={(e) => {
        e.preventDefault();
        void submitTask();
      }}
    >
      <section>
        <SectionTitle>Ziel</SectionTitle>
        {recipe && (
          <div className="mb-2 flex items-center gap-2 text-xs text-text-secondary" data-testid="task-form-recipe">
            Ablauf: <span className="text-text-primary">{recipe.name}</span>
            <button type="button" onClick={() => setDraft({ recipe_id: null })} className="underline underline-offset-2 hover:text-text-primary">
              entfernen
            </button>
          </div>
        )}
        <textarea
          value={draft.goal}
          onChange={(e) => setDraft({ goal: e.target.value })}
          rows={3}
          placeholder="Was soll erledigt werden? Beschreibe das Ziel und das erwartete Ergebnis."
          aria-label="Ziel"
          className={cn(inputClass, "resize-y min-h-[72px]")}
          data-testid="task-goal"
        />
        {draft.live_session_id && (
          <div className="mt-1 text-[11px] text-text-secondary">Verknüpft mit der laufenden Live-Sitzung.</div>
        )}
      </section>

      <section>
        <SectionTitle>Zugriffe</SectionTitle>
        <div className="panel px-4 divide-y divide-border">
          <div>
            <Toggle
              label="Dateien"
              description="Lesen in den gewählten Ordnern; schreiben nur im Schreibordner."
              checked={draft.filesEnabled}
              onChange={(v) => setDraft({ filesEnabled: v })}
            />
            {draft.filesEnabled && (
              <div className="pb-3 flex flex-col gap-2" data-testid="task-files">
                <div className="flex flex-wrap items-center gap-1.5">
                  {draft.fileRoots.map((r) => (
                    <Chip key={r} text={r} label="Ordner" onRemove={() => setDraft({ fileRoots: draft.fileRoots.filter((x) => x !== r) })} />
                  ))}
                  <Button size="sm" onClick={() => void addFolder()} icon={<FolderPlus className="w-3.5 h-3.5" />}>
                    Ordner hinzufügen
                  </Button>
                </div>
                <div className="flex items-center gap-2 text-xs text-text-secondary">
                  <span>Schreibordner:</span>
                  {draft.writeDir ? <Chip text={draft.writeDir} label="Schreibordner" onRemove={() => setDraft({ writeDir: null })} /> : <span className="text-text-secondary/60">keiner (nur lesen)</span>}
                  <Button size="sm" variant="ghost" onClick={() => void pickWriteDir()}>
                    Wählen
                  </Button>
                </div>
              </div>
            )}
          </div>
          <div data-testid="task-browser" data-missing={browserMissing ? "true" : undefined}>
            <Toggle
              label="Browser"
              description={
                browserMissing
                  ? `Browser-Paket nicht installiert${browserStatus?.reason_de ? ` – ${browserStatus.reason_de.replace(/\.\s*$/, "")}` : ""}. Unter Einstellungen → Live & Agent „Browser laden“.`
                  : capabilities?.browser?.installed
                    ? `Browser gefunden${capabilities.browser.version ? ` (${capabilities.browser.version})` : ""}.`
                    : "Webseiten im kontrollierten Browser lesen und bedienen."
              }
              checked={draft.browser && !browserMissing}
              disabled={browserMissing}
              onChange={(v) => setDraft({ browser: v })}
            />
            {browserMissing && (
              <div className="pb-3 -mt-1">
                <Button size="sm" variant="ghost" onClick={() => openSettings("live")} data-testid="task-browser-hint">
                  Browser laden (Einstellungen → Live &amp; Agent)
                </Button>
              </div>
            )}
          </div>
          <div>
            <Toggle
              label="Fenster"
              description={capabilities?.windows_automation && !capabilities.windows_automation.available ? capabilities.windows_automation.reason_de : "Nur die gewählten Fenster bzw. erlaubten Prozesse dürfen bedient werden."}
              checked={draft.windowsEnabled}
              onChange={(v) => setDraft({ windowsEnabled: v })}
            />
            {draft.windowsEnabled && (
              <div className="pb-3 flex flex-col gap-2" data-testid="task-windows">
                <div className="flex items-center justify-between text-xs text-text-secondary">
                  <span>Fenster auswählen</span>
                  <button type="button" onClick={() => void loadSources()} className="inline-flex items-center gap-1 hover:text-text-primary">
                    <RefreshCw className="w-3 h-3" /> neu laden
                  </button>
                </div>
                {sourcesError && <div className="text-xs text-red-300">{sourcesError}</div>}
                <div className="max-h-40 overflow-y-auto rounded-lg border border-border divide-y divide-border/60">
                  {windows.length + screens.length === 0 && <div className="px-3 py-2 text-xs text-text-secondary">Keine Fenster gefunden.</div>}
                  {windows.map((w) => (
                    <label key={w.id} className="flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer hover:bg-white/5">
                      <input type="checkbox" checked={draft.windowSourceIds.includes(w.id)} onChange={() => toggleSource(w.id, w.process)} aria-label={w.title} />
                      <span className="truncate flex-1">{w.title}</span>
                      <span className="text-text-secondary font-mono text-[10px]">{w.process}</span>
                    </label>
                  ))}
                  {screens.map((s) => (
                    <label key={s.id} className="flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer hover:bg-white/5">
                      <input type="checkbox" checked={draft.windowSourceIds.includes(s.id)} onChange={() => toggleSource(s.id, null)} aria-label={s.name} />
                      <span className="truncate flex-1">{s.name}</span>
                      <span className="text-text-secondary text-[10px]">Bildschirm</span>
                    </label>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs text-text-secondary">Erlaubte Prozesse:</span>
                  {draft.allowedProcesses.map((p) => (
                    <Chip key={p} text={p} label="Prozess" onRemove={() => setDraft({ allowedProcesses: draft.allowedProcesses.filter((x) => x !== p) })} />
                  ))}
                  <span className="inline-flex items-center gap-1">
                    <input
                      value={processInput}
                      onChange={(e) => setProcessInput(e.target.value)}
                      onKeyDown={onProcessKey}
                      placeholder="z. B. notepad.exe"
                      aria-label="Prozess hinzufügen"
                      className={cn(inputClass, "w-40 py-1 text-xs")}
                    />
                    <Button size="sm" variant="ghost" onClick={addProcess} icon={<Plus className="w-3 h-3" />} aria-label="Prozess übernehmen" />
                  </span>
                </div>
              </div>
            )}
          </div>
          <Toggle label="Gedächtnis" description="Gespeicherte Fakten und Dokumente dürfen für die Aufgabe genutzt werden." checked={draft.memory} onChange={(v) => setDraft({ memory: v })} />
          <Toggle
            label="Externe Wirkung"
            description="Aktionen mit Wirkung nach außen (z. B. senden, bestellen, veröffentlichen). Standard: aus – jede solche Aktion fragt dann nach."
            checked={draft.external_effects}
            onChange={(v) => setDraft({ external_effects: v })}
          />
        </div>
        {unavailable.length > 0 && (
          <div className="mt-2 flex items-start gap-2 text-[11px] text-amber-200/90">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
            <span>
              Nicht verfügbar: {unavailable.map((t) => `${t.name}${t.reason_de ? ` (${t.reason_de})` : ""}`).join(", ")}
            </span>
          </div>
        )}
      </section>

      <section>
        <SectionTitle>Budget</SectionTitle>
        <div className="panel px-4 divide-y divide-border">
          <Field label="Maximale Schritte" description="Die Aufgabe endet spätestens nach so vielen Aktionen.">
            <input
              type="number"
              min={1}
              max={500}
              value={draft.max_steps}
              onChange={(e) => setDraft({ max_steps: Number(e.target.value) })}
              aria-label="Maximale Schritte"
              className={cn(inputClass, "w-24 text-right")}
            />
          </Field>
          <Field label="Zeitlimit (Sekunden)" description="Danach wird die Aufgabe unterbrochen.">
            <input
              type="number"
              min={10}
              max={86400}
              value={draft.max_seconds}
              onChange={(e) => setDraft({ max_seconds: Number(e.target.value) })}
              aria-label="Zeitlimit"
              className={cn(inputClass, "w-24 text-right")}
            />
          </Field>
        </div>
      </section>

      <div className="flex items-center gap-3">
        <Button type="submit" variant="primary" disabled={Boolean(problem) || submitting} loading={submitting} data-testid="task-start">
          Starten
        </Button>
        {problem && (
          <span className="text-xs text-text-secondary" data-testid="task-form-problem">
            {problem}
          </span>
        )}
      </div>
    </form>
  );
}
