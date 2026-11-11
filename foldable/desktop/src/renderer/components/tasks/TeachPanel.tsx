/**
 * "Ablauf beibringen" (v1.5 teach): the user demonstrates a workflow in the
 * own browser page or in one chosen window. Start form (name, source, URL /
 * window, scopes) -> POST /api/agent/teach; while recording the live step
 * list (polled / global channel), "Schritt erfassen" / "Element auslösen
 * erfassen" for window sources, "Prüfung hinzufügen", per-step remove and the
 * parameter editor for recorded values; "Fertig" -> POST .../finish creates a
 * candidate workflow. Only the chosen source is recorded - never the whole
 * keyboard or screen.
 */
import { Camera, Check, Flag, GraduationCap, MousePointerClick, Play, RefreshCw, Square, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { cn } from "@/lib/utils";
import type { CompileParameter, LiveSources, Scopes, SuccessCriterion, TeachSession, TeachSource } from "../../api/types";
import { errorMessage } from "../../api/client";
import { formatDateTime } from "../../lib/format";
import { selectableSources } from "../../store/overlay";
import { compactArguments, scopeSummary, suggestParameterName, teachValues, type TeachValue } from "../../store/tasks";
import { useAppStore } from "../../store/useAppStore";
import { Badge, Button, Field, inputClass, SectionTitle, Toggle } from "../ui";

const SOURCE_NOTE = "Aufgezeichnet wird nur die gewählte Quelle (diese Seite bzw. dieses Fenster) – keine globale Tastatur- oder Bildschirmaufzeichnung.";

function isHttpUrl(value: string): boolean {
  return /^https?:\/\/\S+$/i.test(value.trim());
}

/** Scopes of the demonstration: browser (+ optional local folder) or exactly one window. */
export function teachScopes(source: TeachSource, opts: { folder: string | null; sourceId: string | null; process: string | null }): Scopes {
  if (source === "browser") {
    return {
      files: opts.folder ? { roots: [opts.folder], write_dir: opts.folder } : null,
      browser: { enabled: true, allowed_origins: null },
      windows: null,
      memory: false,
      external_effects: false,
    };
  }
  return {
    files: null,
    browser: null,
    windows: { source_ids: opts.sourceId ? [opts.sourceId] : [], allowed_processes: opts.process ? [opts.process] : [] },
    memory: false,
    external_effects: false,
  };
}

// ----- start form ---------------------------------------------------------------------------

function StartForm() {
  const client = useAppStore((s) => s.client);
  const teachStart = useAppStore((s) => s.teachStart);
  const busy = useAppStore((s) => s.teachBusy);
  const browserStatus = useAppStore((s) => s.browserStatus);
  const loadBrowserStatus = useAppStore((s) => s.loadBrowserStatus);
  const [name, setName] = useState("");
  const [source, setSource] = useState<TeachSource>("browser");
  const [url, setUrl] = useState("");
  const [folder, setFolder] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [sources, setSources] = useState<LiveSources | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  useEffect(() => {
    if (client && !browserStatus) void loadBrowserStatus();
  }, [client, browserStatus, loadBrowserStatus]);

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
    if (source === "window" && !sources && client) void loadSources();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, client]);

  const { windows } = selectableSources(sources);
  const chosen = windows.find((w) => w.id === sourceId) ?? null;
  const browserMissing = browserStatus !== null && !browserStatus.installed;
  const problem = !name.trim()
    ? "Bitte einen Namen für den Ablauf angeben."
    : source === "browser"
      ? browserMissing
        ? "Das Browser-Paket ist nicht installiert (Einstellungen → Live & Agent)."
        : !isHttpUrl(url)
          ? "Bitte eine Startadresse mit http:// oder https:// angeben."
          : null
      : !sourceId
        ? "Bitte ein Fenster wählen."
        : null;

  const pickFolder = async () => {
    const f = await window.spaider.pickFolder();
    if (f) setFolder(f);
  };

  const start = async () => {
    if (problem) return;
    const scopes = teachScopes(source, { folder, sourceId, process: chosen?.process ?? null });
    await teachStart({
      name: name.trim(),
      source,
      url: source === "browser" ? url.trim() : undefined,
      source_id: source === "window" ? (sourceId ?? undefined) : undefined,
      scopes,
    });
  };

  return (
    <form
      className="flex flex-col gap-6 max-w-2xl"
      data-testid="teach-form"
      onSubmit={(e) => {
        e.preventDefault();
        void start();
      }}
    >
      <div className="panel px-4 py-3 text-xs text-text-secondary flex items-start gap-2">
        <GraduationCap className="w-4 h-4 mt-0.5 flex-shrink-0 text-accent-blue" />
        <span>
          Du zeigst SpAIder einen Ablauf einmal vor; daraus entsteht ein Ablauf-Kandidat mit Prüfungen und Parametern. {SOURCE_NOTE}
        </span>
      </div>

      <section>
        <SectionTitle>Ablauf</SectionTitle>
        <input value={name} onChange={(e) => setName(e.target.value)} aria-label="Name des Ablaufs" placeholder="Name des Ablaufs, z. B. Rechnung im Portal herunterladen" className={inputClass} data-testid="teach-name" autoFocus />
      </section>

      <section>
        <SectionTitle>Quelle</SectionTitle>
        <div className="panel px-4 divide-y divide-border">
          <div className="py-3 flex items-center gap-6" role="radiogroup" aria-label="Quelle">
            <label className="inline-flex items-center gap-2 text-sm cursor-pointer">
              <input type="radio" name="teach-source" value="browser" checked={source === "browser"} onChange={() => setSource("browser")} />
              Browser (eigene Seite)
            </label>
            <label className="inline-flex items-center gap-2 text-sm cursor-pointer">
              <input type="radio" name="teach-source" value="window" checked={source === "window"} onChange={() => setSource("window")} />
              Fenster
            </label>
          </div>
          {source === "browser" ? (
            <>
              <Field label="Startadresse" description="Die Seite wird im eigenen Browser von SpAIder geöffnet; Klicks und Eingaben dort werden aufgezeichnet.">
                <input value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Startadresse" placeholder="https://…" className={cn(inputClass, "w-72 font-mono text-xs")} data-testid="teach-url" />
              </Field>
              <Field label="Erlaubter lokaler Ordner (optional)" description="Nur nötig, wenn der Ablauf Dateien herunterlädt oder liest.">
                <div className="flex items-center gap-1.5">
                  {folder ? (
                    <span className="font-mono text-[11px] max-w-[16rem] truncate" title={folder}>
                      {folder}
                    </span>
                  ) : (
                    <span className="text-xs text-text-secondary">keiner</span>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => void pickFolder()}>
                    Wählen
                  </Button>
                  {folder && <Button size="sm" variant="ghost" onClick={() => setFolder(null)} aria-label="Ordner entfernen" icon={<X className="w-3 h-3" />} />}
                </div>
              </Field>
              {browserMissing && <div className="py-2 text-xs text-amber-200/90">Browser-Paket nicht installiert – unter Einstellungen → Live &amp; Agent „Browser laden“.</div>}
            </>
          ) : (
            <div className="py-3 flex flex-col gap-2" data-testid="teach-windows">
              <div className="flex items-center justify-between text-xs text-text-secondary">
                <span>Fenster wählen (nur dieses Fenster wird gelesen)</span>
                <button type="button" onClick={() => void loadSources()} className="inline-flex items-center gap-1 hover:text-text-primary">
                  <RefreshCw className="w-3 h-3" /> neu laden
                </button>
              </div>
              {sourcesError && <div className="text-xs text-red-300">{sourcesError}</div>}
              <div className="max-h-40 overflow-y-auto rounded-lg border border-border divide-y divide-border/60">
                {windows.length === 0 && <div className="px-3 py-2 text-xs text-text-secondary">Keine Fenster gefunden.</div>}
                {windows.map((w) => (
                  <label key={w.id} className="flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer hover:bg-white/5">
                    <input type="radio" name="teach-window" checked={sourceId === w.id} onChange={() => setSourceId(w.id)} aria-label={w.title} />
                    <span className="truncate flex-1">{w.title}</span>
                    <span className="text-text-secondary font-mono text-[10px]">{w.process}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="mt-2 text-[11px] text-text-secondary">Zugriffe der Aufnahme: {scopeSummary(teachScopes(source, { folder, sourceId, process: chosen?.process ?? null }))}</div>
      </section>

      <div className="flex items-center gap-3">
        <Button type="submit" variant="primary" disabled={Boolean(problem) || busy} loading={busy} icon={<Camera className="w-3.5 h-3.5" />} data-testid="teach-start">
          Aufnahme starten
        </Button>
        {problem && (
          <span className="text-xs text-text-secondary" data-testid="teach-form-problem">
            {problem}
          </span>
        )}
      </div>
    </form>
  );
}

// ----- recording -----------------------------------------------------------------------------

interface ParamDraft {
  enabled: boolean;
  name: string;
  description_de: string;
}

function valueKey(v: TeachValue): string {
  return `${v.step}:${v.key}`;
}

function Recording({ session }: { session: TeachSession }) {
  const busy = useAppStore((s) => s.teachBusy);
  const teachCapture = useAppStore((s) => s.teachCapture);
  const teachAddCheck = useAppStore((s) => s.teachAddCheck);
  const teachRemoveStep = useAppStore((s) => s.teachRemoveStep);
  const teachFinish = useAppStore((s) => s.teachFinish);
  const teachCancel = useAppStore((s) => s.teachCancel);
  const [invokeName, setInvokeName] = useState("");
  const [checkText, setCheckText] = useState("");
  const [checks, setChecks] = useState<SuccessCriterion[]>([]);
  const [params, setParams] = useState<Record<string, ParamDraft>>({});
  const [cancelOpen, setCancelOpen] = useState(false);

  const values = useMemo(() => teachValues(session), [session]);
  const isWindow = session.source === "window";

  const paramOf = (v: TeachValue, i: number): ParamDraft => params[valueKey(v)] ?? { enabled: false, name: suggestParameterName(v.target, `wert_${i + 1}`), description_de: v.target ?? "" };
  const setParam = (v: TeachValue, i: number, patch: Partial<ParamDraft>) => setParams((p) => ({ ...p, [valueKey(v)]: { ...paramOf(v, i), ...patch } }));

  const addCheck = async () => {
    const text = checkText.trim();
    if (!text) return;
    const check: SuccessCriterion = {
      kind: isWindow ? "window_text" : "page_text",
      target: isWindow ? (session.source_id ?? "") : (session.url ?? ""),
      expect: [text],
      origin: "user",
      description_de: `„${text}“ ist ${isWindow ? "im Fenster" : "auf der Seite"} sichtbar`,
    };
    await teachAddCheck(check);
    setChecks((c) => [...c, check]);
    setCheckText("");
  };

  const finish = async () => {
    const parameters: CompileParameter[] = [];
    const seen = new Set<string>();
    values.forEach((v, i) => {
      const p = paramOf(v, i);
      if (!p.enabled || !p.name.trim() || seen.has(p.name.trim())) return;
      seen.add(p.name.trim());
      parameters.push({ name: p.name.trim(), description_de: p.description_de.trim(), example: v.value, value: v.value });
    });
    await teachFinish(parameters, checks);
  };

  const paramProblem = (() => {
    const names = new Set<string>();
    for (const [i, v] of values.entries()) {
      const p = paramOf(v, i);
      if (!p.enabled) continue;
      const n = p.name.trim();
      if (!n) return "Jeder Parameter braucht einen Namen.";
      if (!/^[a-z][a-z0-9_]*$/.test(n)) return `Parametername „${n}“: nur Kleinbuchstaben, Ziffern und Unterstrich.`;
      if (names.has(n)) return `Parametername „${n}“ ist doppelt.`;
      names.add(n);
    }
    return null;
  })();

  return (
    <div className="flex flex-col gap-6 max-w-3xl" data-testid="teach-recording" data-session-id={session.id}>
      <section className="panel p-4">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold flex items-center gap-2 flex-wrap">
              {session.name}
              <Badge tone="red">
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse" /> Aufnahme läuft
              </Badge>
            </h3>
            <div className="mt-1 text-xs text-text-secondary break-all">
              {session.source === "browser" ? `Browser · ${session.url ?? ""}` : `Fenster · ${session.source_id ?? ""}`} · seit {formatDateTime(session.created_at)} · {scopeSummary(session.scopes)}
            </div>
            {session.note_de && <div className="mt-1 text-xs text-text-secondary">{session.note_de}</div>}
            <div className="mt-1 text-[11px] text-text-secondary/80">{SOURCE_NOTE}</div>
          </div>
          <div className="flex items-center gap-1.5 flex-shrink-0">
            <Button size="sm" variant="primary" onClick={() => void finish()} disabled={busy || session.steps.length === 0 || Boolean(paramProblem)} loading={busy} icon={<Check className="w-3.5 h-3.5" />} data-testid="teach-finish">
              Fertig
            </Button>
            <Button size="sm" variant="danger" onClick={() => setCancelOpen(true)} disabled={busy} icon={<Square className="w-3.5 h-3.5" />} data-testid="teach-cancel">
              Abbrechen
            </Button>
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>Schritte erfassen</SectionTitle>
        <div className="panel px-4 py-3 flex flex-col gap-3">
          {isWindow ? (
            <>
              <div className="text-xs text-text-secondary">Führe den Schritt im gewählten Fenster aus und drücke danach „Schritt erfassen“ – SpAIder vergleicht das Fenster und merkt sich geänderte Feldwerte.</div>
              <div className="flex items-center gap-2 flex-wrap">
                <Button size="sm" onClick={() => void teachCapture()} disabled={busy} icon={<Camera className="w-3.5 h-3.5" />} data-testid="teach-capture">
                  Schritt erfassen
                </Button>
                <span className="inline-flex items-center gap-1.5">
                  <input
                    value={invokeName}
                    onChange={(e) => setInvokeName(e.target.value)}
                    placeholder="Name der gedrückten Schaltfläche"
                    aria-label="Name der gedrückten Schaltfläche"
                    className={cn(inputClass, "w-56 py-1 text-xs")}
                    data-testid="teach-invoke-name"
                  />
                  <Button
                    size="sm"
                    onClick={() => {
                      const n = invokeName.trim();
                      setInvokeName("");
                      void teachCapture(n);
                    }}
                    disabled={busy || !invokeName.trim()}
                    icon={<MousePointerClick className="w-3.5 h-3.5" />}
                    data-testid="teach-capture-invoke"
                  >
                    Element auslösen erfassen
                  </Button>
                </span>
              </div>
            </>
          ) : (
            <div className="flex items-center gap-2 flex-wrap text-xs text-text-secondary">
              <span>Die Seite im eigenen Browser zeichnet Klicks und Eingaben selbst auf.</span>
              <Button size="sm" variant="ghost" onClick={() => void teachCapture()} disabled={busy} icon={<RefreshCw className="w-3.5 h-3.5" />} data-testid="teach-capture">
                Seite neu einlesen
              </Button>
            </div>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            <input
              value={checkText}
              onChange={(e) => setCheckText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void addCheck();
                }
              }}
              placeholder={isWindow ? "Text, der im Fenster erscheinen muss" : "Text, der auf der Seite erscheinen muss"}
              aria-label="Text der Prüfung"
              className={cn(inputClass, "w-72 py-1 text-xs")}
              data-testid="teach-check-text"
            />
            <Button size="sm" onClick={() => void addCheck()} disabled={busy || !checkText.trim()} icon={<Flag className="w-3.5 h-3.5" />} data-testid="teach-add-check">
              Prüfung hinzufügen
            </Button>
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>Aufgezeichnete Schritte ({session.steps.length})</SectionTitle>
        {session.steps.length === 0 ? (
          <div className="text-xs text-text-secondary px-1">Noch keine Schritte aufgezeichnet.</div>
        ) : (
          <ol className="panel divide-y divide-border/60" data-testid="teach-steps">
            {session.steps.map((s, i) => {
              const args = compactArguments(s.arguments, 120);
              return (
                <li key={`${s.at}-${i}`} className="px-3 py-2 flex items-start gap-2" data-testid="teach-step">
                  <span className="text-[11px] text-text-secondary/60 tabular-nums w-6 flex-shrink-0 pt-0.5">#{i + 1}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium font-mono">{s.tool}</span>
                      {s.target?.name && <span className="text-xs text-text-secondary">{s.target.role ? `${s.target.role} ` : ""}„{s.target.name}“</span>}
                    </div>
                    {args && (
                      <div className="text-[11px] text-text-secondary font-mono truncate" title={args}>
                        {args}
                      </div>
                    )}
                    {s.check_de && <div className="text-xs text-text-secondary mt-0.5">{s.check_de}</div>}
                  </div>
                  <button type="button" onClick={() => void teachRemoveStep(i)} disabled={busy} aria-label={`Schritt ${i + 1} entfernen`} className="text-text-secondary hover:text-red-300 disabled:opacity-40 mt-0.5">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </section>

      <section data-testid="teach-parameters">
        <SectionTitle>Parameter</SectionTitle>
        {values.length === 0 ? (
          <div className="text-xs text-text-secondary px-1">Sobald Werte eingegeben oder gewählt wurden, kannst du sie hier als Parameter markieren.</div>
        ) : (
          <div className="panel px-4 divide-y divide-border">
            {values.map((v, i) => {
              const p = paramOf(v, i);
              return (
                <div key={valueKey(v)} className="py-2.5 flex items-center gap-3 flex-wrap" data-testid="teach-parameter">
                  <Toggle label="als Parameter" checked={p.enabled} onChange={(enabled) => setParam(v, i, { enabled })} />
                  <span className="text-xs min-w-0">
                    <span className="text-text-secondary">Schritt #{v.step + 1}{v.target ? ` · ${v.target}` : ""}:</span> <span className="font-mono">„{v.value}“</span>
                  </span>
                  {p.enabled && (
                    <span className="inline-flex items-center gap-1.5 w-full">
                      <input value={p.name} onChange={(e) => setParam(v, i, { name: e.target.value })} placeholder="name" aria-label="Parametername" className={cn(inputClass, "w-44 py-1 text-xs font-mono")} />
                      <input value={p.description_de} onChange={(e) => setParam(v, i, { description_de: e.target.value })} placeholder="Beschreibung" aria-label="Beschreibung des Parameters" className={cn(inputClass, "flex-1 py-1 text-xs")} />
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {paramProblem && (
          <div className="mt-2 text-xs text-red-300" data-testid="teach-parameter-problem">
            {paramProblem}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        title="Aufnahme abbrechen?"
        description="Die aufgezeichneten Schritte werden verworfen."
        confirmLabel="Abbrechen der Aufnahme"
        cancelLabel="Weiter aufnehmen"
        variant="destructive"
        onConfirm={() => {
          setCancelOpen(false);
          void teachCancel();
        }}
      />
    </div>
  );
}

// ----- finished -------------------------------------------------------------------------------

function Finished() {
  const result = useAppStore((s) => s.teachResult);
  const startWithWorkflow = useAppStore((s) => s.startWithWorkflow);
  const showWorkflows = useAppStore((s) => s.showWorkflows);
  const selectTask = useAppStore((s) => s.selectTask);
  const teachReset = useAppStore((s) => s.teachReset);
  if (!result) return null;
  const w = result.workflow;
  return (
    <div className="flex flex-col gap-4 max-w-2xl" data-testid="teach-finished" data-workflow-id={w.id}>
      <div className="panel p-4">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          Ablauf-Kandidat „{w.name}“ angelegt <Badge tone="blue">Kandidat</Badge>
        </h3>
        <p className="mt-1 text-sm text-text-secondary">
          {w.steps.length} Schritte, {w.parameters.length} Parameter, {w.checks.length} Prüfungen. Ein erfolgreicher Prüflauf auf diesem Rechner macht den Kandidaten zu einem geprüften Ablauf.
        </p>
        {result.task_id && (
          <p className="mt-2 text-xs text-text-secondary">
            Der Kern hat bereits einen Prüflauf gestartet.{" "}
            <button type="button" onClick={() => void selectTask(result.task_id)} className="underline underline-offset-2 hover:text-text-primary">
              Aufgabe öffnen
            </button>
          </p>
        )}
        <div className="mt-3 flex items-center gap-2 flex-wrap">
          <Button variant="primary" size="sm" onClick={() => startWithWorkflow(w)} icon={<Play className="w-3.5 h-3.5" />} data-testid="teach-run-check">
            Prüflauf starten
          </Button>
          <Button size="sm" onClick={showWorkflows} data-testid="teach-show-workflows">
            Abläufe anzeigen
          </Button>
          <Button size="sm" variant="ghost" onClick={teachReset}>
            Neue Aufnahme
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function TeachPanel() {
  const session = useAppStore((s) => s.teachSession);
  const result = useAppStore((s) => s.teachResult);
  const loadTeachStatus = useAppStore((s) => s.loadTeachStatus);

  useEffect(() => {
    if (!session) void loadTeachStatus();
  }, [session, loadTeachStatus]);

  if (result) return <Finished />;
  if (session?.state === "recording") return <Recording session={session} />;
  return <StartForm />;
}
