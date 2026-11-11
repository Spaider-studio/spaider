/**
 * Detail of one task: header with state / step / buttons, result, outcome
 * report (v1.5: erfüllt / nicht erfüllt / ungeklärt with every check), reuse
 * decision (mode, reasons, coverage), the question dialog for
 * `pending_question` (approve / deny / input / binding picker ->
 * POST .../answer), "Als Ablauf speichern" (POST .../compile), the agent's
 * activity line, the timeline of steps that really happened (`event: step`)
 * and the action timeline fed by GET /api/agent/tasks/{id}/events. Evidence
 * paths open through the launcher bridge (openPath / revealPath).
 */
import * as Dialog from "@radix-ui/react-dialog";
import {
  BadgeCheck,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  ClipboardCheck,
  Copy,
  ExternalLink,
  Eye,
  FolderOpen,
  GitBranch,
  Pause,
  PauseCircle,
  PencilLine,
  Play,
  RefreshCw,
  Save,
  SkipForward,
  Square,
  Wrench,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import type { Action, CompileParameter, LiveSources, PendingQuestion, QuestionBinding, ReusePlan, StepEvent, StepKind, Task, Workflow } from "../../api/types";
import { errorMessage } from "../../api/client";
import { formatDateTime, formatSeconds } from "../../lib/format";
import { notify } from "../../lib/notify";
import { selectableSources } from "../../store/overlay";
import ActivityLine from "../chat/ActivityLine";
import { copyText } from "../chat/CodeBlock";
import {
  ACTION_STATE_LABEL,
  BINDING_KIND_LABEL,
  canCancel,
  canCompileWorkflow,
  canPause,
  canResume,
  compactArguments,
  evidencePath,
  formatCoverage,
  isTerminalTaskState,
  OUTCOME_LABEL,
  OUTCOME_TONE,
  REUSE_MODE_LABEL,
  REUSE_MODE_TONE,
  scopeSummary,
  STEP_KIND_LABEL,
  type TaskFeed,
} from "../../store/tasks";
import { useAppStore } from "../../store/useAppStore";
import { Badge, Button, inputClass, KeyValue, SectionTitle } from "../ui";
import TaskStateBadge from "./TaskStateBadge";

const ACTION_TONE: Record<Action["state"], "neutral" | "blue" | "green" | "yellow" | "red"> = {
  proposed: "neutral",
  validated: "blue",
  rejected: "red",
  executing: "blue",
  done: "green",
  failed: "red",
  unknown_outcome: "yellow",
  cancelled: "neutral",
};

const ACTION_ORIGIN_LABEL: Record<Action["origin"], string> = {
  planner: "Planer",
  workflow: "Ablauf",
  repair: "Reparatur",
  teach: "beigebracht",
  verifier: "Prüfer",
};

// ----- evidence paths (launcher bridge) -----------------------------------------------------

async function openEvidence(path: string): Promise<void> {
  try {
    const ok = await window.spaider?.openPath?.(path);
    if (!ok) notify("Datei konnte nicht geöffnet werden (Pfad nicht vorhanden oder nicht erlaubt).", "error");
  } catch (e) {
    notify(`Öffnen fehlgeschlagen: ${errorMessage(e)}`, "error");
  }
}

async function revealEvidence(path: string): Promise<void> {
  try {
    const ok = await window.spaider?.revealPath?.(path);
    if (!ok) notify("Pfad konnte nicht angezeigt werden (nicht vorhanden).", "error");
  } catch (e) {
    notify(`Anzeigen fehlgeschlagen: ${errorMessage(e)}`, "error");
  }
}

function EvidencePath({ path, label }: { path: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const smallButton = "inline-flex items-center justify-center w-5 h-5 rounded text-text-secondary hover:text-text-primary hover:bg-white/5";
  return (
    <span className="inline-flex items-center gap-0.5 max-w-full" data-testid="action-evidence" data-path={path}>
      <button
        type="button"
        onClick={() => void openEvidence(path)}
        className="inline-flex items-center gap-1 text-[11px] text-accent-blue hover:underline underline-offset-2 min-w-0"
        title={`Öffnen: ${path}`}
        data-testid="evidence-open"
      >
        <ExternalLink className="w-3 h-3 flex-shrink-0" />
        <span className="truncate font-mono">{label ?? path}</span>
      </button>
      <button type="button" onClick={() => void revealEvidence(path)} className={smallButton} aria-label="Im Ordner zeigen" title="Im Ordner zeigen" data-testid="evidence-reveal">
        <FolderOpen className="w-3 h-3" />
      </button>
      <button
        type="button"
        onClick={() => void copyText(path).then((ok) => ok && (setCopied(true), setTimeout(() => setCopied(false), 1500)))}
        className={smallButton}
        aria-label="Pfad kopieren"
        title="Pfad kopieren"
      >
        {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
      </button>
    </span>
  );
}

// ----- actions --------------------------------------------------------------------------------

function ActionRow({ action }: { action: Action }) {
  const [open, setOpen] = useState(false);
  const evidence = evidencePath(action);
  const args = compactArguments(action.arguments);
  return (
    <li className="px-3 py-2" data-testid="action-row" data-action-state={action.state}>
      <div className="flex items-start gap-2">
        <span className="text-[11px] text-text-secondary/60 tabular-nums w-6 flex-shrink-0 pt-0.5">#{action.seq}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium font-mono">{action.tool}</span>
            <Badge tone={ACTION_TONE[action.state]}>{ACTION_STATE_LABEL[action.state] ?? action.state}</Badge>
            {action.origin && action.origin !== "planner" && <Badge tone="neutral">{ACTION_ORIGIN_LABEL[action.origin] ?? action.origin}</Badge>}
            {typeof action.duration_ms === "number" && <span className="text-[11px] text-text-secondary tabular-nums">{formatSeconds(action.duration_ms)}</span>}
          </div>
          {args && (
            <div className="text-[11px] text-text-secondary font-mono truncate" title={args}>
              {args}
            </div>
          )}
          {action.result?.summary_de && <div className={cn("text-xs mt-0.5", action.result.ok ? "text-text-primary" : "text-red-300")}>{action.result.summary_de}</div>}
          {action.reason_de && action.state === "rejected" && <div className="text-xs text-red-300 mt-0.5">{action.reason_de}</div>}
          {evidence && (
            <div className="mt-0.5">
              <EvidencePath path={evidence} />
            </div>
          )}
          <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="mt-1 inline-flex items-center gap-0.5 text-[11px] text-text-secondary hover:text-text-primary">
            {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            Details
          </button>
          {open && (
            <pre className="mt-1 text-[11px] text-text-secondary whitespace-pre-wrap break-words font-mono bg-bg-primary/60 rounded-md p-2 border border-border/60 max-h-48 overflow-y-auto">
              {JSON.stringify(
                { expected_effect: action.expected_effect, arguments: action.arguments, result: action.result, observation_ref: action.observation_ref, call_id: action.call_id, origin: action.origin },
                null,
                2,
              )}
            </pre>
          )}
        </div>
      </div>
    </li>
  );
}

// ----- real steps (`event: step`) -------------------------------------------------------------

const STEP_ICON: Record<StepKind, ReactNode> = {
  read: <Eye className="w-3.5 h-3.5" />,
  write: <PencilLine className="w-3.5 h-3.5" />,
  check: <ClipboardCheck className="w-3.5 h-3.5" />,
  verify: <BadgeCheck className="w-3.5 h-3.5" />,
  skip: <SkipForward className="w-3.5 h-3.5" />,
  repair: <Wrench className="w-3.5 h-3.5" />,
  repaired: <Check className="w-3.5 h-3.5" />,
  replan: <RefreshCw className="w-3.5 h-3.5" />,
  gate: <GitBranch className="w-3.5 h-3.5" />,
  derive: <GitBranch className="w-3.5 h-3.5" />,
  pause: <PauseCircle className="w-3.5 h-3.5" />,
};

function formatClock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function StepRow({ step }: { step: StepEvent }) {
  const failed = step.ok === false;
  const tone = failed ? "text-red-300" : step.ok === true ? "text-emerald-300" : "text-text-secondary";
  return (
    <li className="px-3 py-1.5 flex items-start gap-2" data-testid="step-row" data-kind={step.kind} data-ok={step.ok === null || step.ok === undefined ? undefined : String(step.ok)}>
      <span className={cn("mt-0.5 flex-shrink-0", tone)} title={STEP_KIND_LABEL[step.kind] ?? step.kind}>
        {STEP_ICON[step.kind] ?? <ClipboardCheck className="w-3.5 h-3.5" />}
      </span>
      <span className="text-[11px] text-text-secondary/70 tabular-nums flex-shrink-0 w-16 mt-0.5">{formatClock(step.at)}</span>
      <span className="min-w-0 flex-1 text-xs">
        <span className={cn(failed && "text-red-300")}>{step.message_de}</span>
        {(typeof step.step === "number" || step.mode || typeof step.coverage === "number" || (step.repair_steps && step.repair_steps.length > 0)) && (
          <span className="ml-1.5 text-[11px] text-text-secondary">
            {typeof step.step === "number" && `Schritt ${step.step} `}
            {step.mode && `· ${REUSE_MODE_LABEL[step.mode as keyof typeof REUSE_MODE_LABEL] ?? step.mode} `}
            {typeof step.coverage === "number" && `· Abdeckung ${formatCoverage(step.coverage)} `}
            {step.repair_steps && step.repair_steps.length > 0 && `· Reparatur ${step.repair_steps.map((s) => `#${s}`).join(", ")}`}
          </span>
        )}
      </span>
    </li>
  );
}

// ----- outcome and reuse ----------------------------------------------------------------------

function CheckIcon({ ok }: { ok: boolean | null }) {
  if (ok === true) return <Check className="w-3.5 h-3.5 text-emerald-300 flex-shrink-0 mt-0.5" aria-label="erfüllt" />;
  if (ok === false) return <X className="w-3.5 h-3.5 text-red-300 flex-shrink-0 mt-0.5" aria-label="nicht erfüllt" />;
  return <CircleHelp className="w-3.5 h-3.5 text-amber-300 flex-shrink-0 mt-0.5" aria-label="nicht prüfbar" />;
}

function OutcomeBlock({ task }: { task: Task }) {
  const report = task.outcome_report ?? null;
  const outcome = task.outcome ?? report?.status ?? task.result?.outcome ?? null;
  if (!outcome && !report) return null;
  const paths = [...new Set([...(report?.evidence_paths ?? []), ...(task.result?.evidence_paths ?? [])])];
  const verifiedBy = report?.verified_by ?? task.result?.verified_by ?? null;
  return (
    <div className="mt-3 rounded-lg border border-border bg-bg-primary/60 px-3 py-2 text-sm" data-testid="task-outcome-block" data-outcome={outcome ?? undefined}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-medium">Ergebnisprüfung</span>
        {outcome && <Badge tone={OUTCOME_TONE[outcome] ?? "neutral"}>{OUTCOME_LABEL[outcome] ?? outcome}</Badge>}
        {verifiedBy && <span className="text-[11px] text-text-secondary">geprüft durch {verifiedBy}</span>}
        {report?.verified_at && <span className="text-[11px] text-text-secondary">· {formatDateTime(report.verified_at)}</span>}
      </div>
      {report && report.checks.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1" data-testid="outcome-checks">
          {report.checks.map((c, i) => (
            <li key={i} className="flex items-start gap-2 text-xs" data-ok={c.ok === null ? "unknown" : String(c.ok)}>
              <CheckIcon ok={c.ok} />
              <span className="min-w-0">
                <span>{c.criterion?.description_de || c.criterion?.target || "Prüfung"}</span>
                {c.detail_de && <span className="text-text-secondary"> – {c.detail_de}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {report && report.causes_de.length > 0 && (
        <div className="mt-2 text-xs">
          <div className="text-text-secondary">Ursachen:</div>
          <ul className="list-disc pl-4 text-red-300/90">
            {report.causes_de.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      {paths.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1" data-testid="outcome-evidence">
          {paths.map((p) => (
            <li key={p} className="text-xs flex items-center gap-2 min-w-0">
              <span className="text-text-secondary flex-shrink-0">Beleg</span>
              <EvidencePath path={p} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ReuseBlock({ reuse }: { reuse: ReusePlan }) {
  return (
    <div className="mt-3 rounded-lg border border-border bg-bg-primary/60 px-3 py-2 text-xs" data-testid="task-reuse" data-mode={reuse.mode}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-medium text-sm">Wiederverwendung</span>
        <Badge tone={REUSE_MODE_TONE[reuse.mode] ?? "neutral"}>{REUSE_MODE_LABEL[reuse.mode] ?? reuse.mode}</Badge>
        {reuse.workflow_name && <span className="text-text-secondary">Ablauf „{reuse.workflow_name}“</span>}
        <span className="text-text-secondary">· Abdeckung {formatCoverage(reuse.coverage)}</span>
        <span className="text-text-secondary tabular-nums">· Prüfung {Math.round(reuse.gate_ms)} ms</span>
        {reuse.strict && <span className="text-text-secondary">· streng</span>}
      </div>
      {reuse.reasons_de.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-text-secondary">
          {reuse.reasons_de.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      {reuse.repair_steps.length > 0 && <div className="mt-1 text-text-secondary">Reparierte Schritte: {reuse.repair_steps.map((s) => `#${s}`).join(", ")}</div>}
      {reuse.missing_bindings.length > 0 && <div className="mt-1 text-amber-200/90">Fehlende Zuordnungen: {reuse.missing_bindings.join(", ")}</div>}
      {reuse.invalid.length > 0 && (
        <div className="mt-1 text-text-secondary">
          Ungültige Abhängigkeiten: {reuse.invalid.map((d) => `${d.key}${d.reason ? ` (${d.reason})` : ""}`).join(", ")}
        </div>
      )}
      {reuse.unknown_dependencies && <div className="mt-1 text-amber-200/90">Nicht alle Abhängigkeiten sind bekannt.</div>}
    </div>
  );
}

// ----- question dialog (approval / input / binding) --------------------------------------------

/** Picks the target of a binding question: folder picker, window list or plain text. */
function BindingPicker({ binding, value, onChange }: { binding: QuestionBinding; value: string; onChange: (v: string) => void }) {
  const client = useAppStore((s) => s.client);
  const [sources, setSources] = useState<LiveSources | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  useEffect(() => {
    if (binding.kind !== "window" || !client) return;
    let active = true;
    client
      .liveSources()
      .then((s) => active && setSources(s))
      .catch((e: unknown) => active && setSourcesError(errorMessage(e)));
    return () => {
      active = false;
    };
  }, [binding.kind, client]);

  const kindLabel = BINDING_KIND_LABEL[binding.kind] ?? binding.kind;
  if (binding.kind === "dir") {
    const pick = async () => {
      const folder = await window.spaider.pickFolder();
      if (folder) onChange(folder);
    };
    return (
      <div className="flex flex-col gap-2" data-testid="binding-picker" data-kind="dir">
        <div className="flex items-center gap-2">
          <input value={value} onChange={(e) => onChange(e.target.value)} aria-label={`${kindLabel} für ${binding.role}`} placeholder={binding.hint || "Ordnerpfad"} className={cn(inputClass, "font-mono text-xs")} />
          <Button size="sm" onClick={() => void pick()} icon={<FolderOpen className="w-3.5 h-3.5" />} data-testid="binding-pick-folder">
            Ordner wählen
          </Button>
        </div>
      </div>
    );
  }
  if (binding.kind === "window") {
    const { windows } = selectableSources(sources);
    return (
      <div className="flex flex-col gap-2" data-testid="binding-picker" data-kind="window">
        {sourcesError && <div className="text-xs text-red-300">{sourcesError}</div>}
        <div className="max-h-40 overflow-y-auto rounded-lg border border-border divide-y divide-border/60">
          {!sources && !sourcesError && <div className="px-3 py-2 text-xs text-text-secondary">Fenster werden geladen …</div>}
          {sources && windows.length === 0 && <div className="px-3 py-2 text-xs text-text-secondary">Keine Fenster gefunden.</div>}
          {windows.map((w) => (
            <label key={w.id} className="flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer hover:bg-white/5">
              <input type="radio" name="binding-window" checked={value === w.id} onChange={() => onChange(w.id)} aria-label={w.title} />
              <span className="truncate flex-1">{w.title}</span>
              <span className="text-text-secondary font-mono text-[10px]">{w.process}</span>
            </label>
          ))}
        </div>
      </div>
    );
  }
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={`${kindLabel} für ${binding.role}`}
      placeholder={binding.hint || kindLabel}
      className={cn(inputClass, (binding.kind === "file" || binding.kind === "url") && "font-mono text-xs")}
      data-testid="binding-picker"
      data-kind={binding.kind}
      autoFocus
    />
  );
}

function QuestionDialog({ task, question, onAnswer }: { task: Task; question: PendingQuestion; onAnswer: (approve: boolean | null, text: string | null) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const binding = question.binding ?? null;
  const answer = (approve: boolean | null, value: string | null) => {
    setBusy(true);
    onAnswer(approve, value);
  };
  return (
    <Dialog.Root open>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 data-[state=open]:animate-fade-in" />
        <Dialog.Content
          className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[min(520px,calc(100vw-2rem))] bg-bg-secondary border border-violet-500/40 rounded-xl p-5 shadow-2xl data-[state=open]:animate-fade-in focus:outline-none"
          data-testid="question-dialog"
          onEscapeKeyDown={(e) => e.preventDefault()}
          onPointerDownOutside={(e) => e.preventDefault()}
        >
          <Dialog.Title className="text-sm font-semibold">Rückfrage der Aufgabe</Dialog.Title>
          <Dialog.Description className="mt-1 text-xs text-text-secondary truncate" title={task.goal}>
            {task.goal}
          </Dialog.Description>
          <p className="mt-3 text-sm leading-relaxed whitespace-pre-wrap">{question.text_de}</p>
          {question.action && (
            <div className="mt-3 rounded-lg border border-border bg-bg-primary/60 px-3 py-2 text-xs">
              <div className="font-mono font-medium">{question.action.tool}</div>
              <div className="text-text-secondary font-mono truncate" title={compactArguments(question.action.arguments, 400)}>
                {compactArguments(question.action.arguments, 160)}
              </div>
              {question.action.expected_effect && <div className="text-text-secondary mt-1">Erwartete Wirkung: {question.action.expected_effect}</div>}
            </div>
          )}
          {question.kind === "input" ? (
            <form
              className="mt-4 flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (text.trim()) answer(null, text.trim());
              }}
            >
              {binding ? (
                <div className="flex flex-col gap-1.5">
                  <div className="text-xs text-text-secondary">
                    Gesucht: <span className="text-text-primary">{BINDING_KIND_LABEL[binding.kind] ?? binding.kind}</span> für <span className="font-mono">{binding.role}</span>
                    {binding.hint && ` – ${binding.hint}`}
                  </div>
                  <BindingPicker binding={binding} value={text} onChange={setText} />
                </div>
              ) : (
                <input value={text} onChange={(e) => setText(e.target.value)} aria-label="Antwort" className={inputClass} placeholder="Antwort eingeben …" autoFocus />
              )}
              <div className="flex justify-end gap-2">
                <Button onClick={() => answer(false, null)} disabled={busy}>
                  Abbrechen
                </Button>
                <Button type="submit" variant="primary" disabled={busy || !text.trim()} loading={busy} data-testid="question-answer">
                  {binding ? "Übernehmen" : "Antworten"}
                </Button>
              </div>
            </form>
          ) : (
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="danger" onClick={() => answer(false, null)} disabled={busy} data-testid="question-deny">
                Ablehnen
              </Button>
              <Button variant="primary" onClick={() => answer(true, null)} disabled={busy} loading={busy} data-testid="question-approve">
                Freigeben
              </Button>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ----- "Als Ablauf speichern" (compile) -------------------------------------------------------

function CompileDialog({
  open,
  onClose,
  onSave,
  onShowWorkflows,
}: {
  open: boolean;
  onClose: () => void;
  onSave: (name: string, parameters: CompileParameter[]) => Promise<Workflow | null>;
  onShowWorkflows: () => void;
}) {
  const [name, setName] = useState("");
  const [params, setParams] = useState<CompileParameter[]>([]);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Workflow | null>(null);
  const update = (i: number, patch: Partial<CompileParameter>) => setParams((p) => p.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));
  const close = () => {
    setCreated(null);
    onClose();
  };
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && close()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 data-[state=open]:animate-fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[min(600px,calc(100vw-2rem))] bg-bg-secondary border border-border rounded-xl p-5 shadow-2xl focus:outline-none" data-testid="compile-dialog">
          <div className="flex items-start justify-between gap-3">
            <div>
              <Dialog.Title className="text-sm font-semibold">Als Ablauf speichern</Dialog.Title>
              <Dialog.Description className="mt-1 text-xs text-text-secondary">
                Der Kern leitet aus den ausgeführten Schritten einen Ablauf-Kandidaten ab; nach dem ersten erfolgreichen Prüflauf gilt er als geprüft.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button type="button" aria-label="Schließen" className="text-text-secondary hover:text-text-primary">
                <X className="w-4 h-4" />
              </button>
            </Dialog.Close>
          </div>
          {created ? (
            <div className="mt-4 flex flex-col gap-3" data-testid="compile-done">
              <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm">
                Ablauf „{created.name}“ wurde als Kandidat gespeichert ({created.steps.length} Schritte).
              </div>
              <div className="flex justify-end gap-2">
                <Button onClick={close}>Schließen</Button>
                <Button
                  variant="primary"
                  icon={<BookOpen className="w-3.5 h-3.5" />}
                  onClick={() => {
                    close();
                    onShowWorkflows();
                  }}
                  data-testid="compile-show-workflows"
                >
                  Ablauf ansehen
                </Button>
              </div>
            </div>
          ) : (
            <form
              className="mt-4 flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (!name.trim()) return;
                setBusy(true);
                void onSave(
                  name.trim(),
                  params.filter((p) => p.name.trim()).map((p) => ({ ...p, value: p.value?.trim() ? p.value.trim() : undefined })),
                )
                  .then((w) => w && setCreated(w))
                  .finally(() => setBusy(false));
              }}
            >
              <input value={name} onChange={(e) => setName(e.target.value)} aria-label="Name des Ablaufs" placeholder="Name des Ablaufs" className={inputClass} autoFocus />
              <div className="text-xs text-text-secondary">Parameter (optional) – Werte, die bei jedem Start anders sein dürfen. „Wert“ ist der in dieser Aufgabe benutzte Wert, den der Kern durch den Parameter ersetzt.</div>
              {params.map((p, i) => (
                <div key={i} className="grid grid-cols-[1fr_1.4fr_1fr_1fr_auto] gap-2 items-center">
                  <input value={p.name} onChange={(e) => update(i, { name: e.target.value })} placeholder="name" aria-label="Parametername" className={cn(inputClass, "py-1 text-xs font-mono")} />
                  <input value={p.description_de} onChange={(e) => update(i, { description_de: e.target.value })} placeholder="Beschreibung" aria-label="Beschreibung" className={cn(inputClass, "py-1 text-xs")} />
                  <input value={p.example} onChange={(e) => update(i, { example: e.target.value })} placeholder="Beispiel" aria-label="Beispiel" className={cn(inputClass, "py-1 text-xs")} />
                  <input value={p.value ?? ""} onChange={(e) => update(i, { value: e.target.value })} placeholder="Wert in dieser Aufgabe" aria-label="Wert in dieser Aufgabe" className={cn(inputClass, "py-1 text-xs")} />
                  <button type="button" onClick={() => setParams((x) => x.filter((_, idx) => idx !== i))} aria-label="Parameter entfernen" className="text-text-secondary hover:text-red-300">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
              <div>
                <Button size="sm" variant="ghost" onClick={() => setParams((p) => [...p, { name: "", description_de: "", example: "", value: "" }])}>
                  Parameter hinzufügen
                </Button>
              </div>
              <div className="flex justify-end gap-2 mt-2">
                <Button onClick={close} disabled={busy}>
                  Abbrechen
                </Button>
                <Button type="submit" variant="primary" disabled={!name.trim() || busy} loading={busy} icon={<Save className="w-3.5 h-3.5" />} data-testid="compile-save">
                  Speichern
                </Button>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ----- detail ---------------------------------------------------------------------------------

export default function TaskDetail({ task, feed }: { task: Task; feed: TaskFeed | undefined }) {
  const cancelTask = useAppStore((s) => s.cancelTask);
  const pauseTask = useAppStore((s) => s.pauseTask);
  const resumeTask = useAppStore((s) => s.resumeTask);
  const answerTask = useAppStore((s) => s.answerTask);
  const compileWorkflow = useAppStore((s) => s.compileWorkflow);
  const showWorkflows = useAppStore((s) => s.showWorkflows);
  const workflowName = useAppStore((s) => (task.workflow_id ? (s.workflows.find((w) => w.id === task.workflow_id)?.name ?? null) : null));
  const [compileOpen, setCompileOpen] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);

  const question = task.pending_question ?? feed?.question ?? null;
  const actions = feed?.actions ?? [];
  const steps = feed?.steps ?? [];
  const resultPaths = (task.result?.evidence_paths ?? []).filter((p) => !task.result?.artifacts.some((a) => a.path === p));

  return (
    <div className="flex flex-col gap-6 max-w-3xl" data-testid="task-detail" data-task-id={task.id}>
      <section className="panel p-4">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold leading-snug whitespace-pre-wrap break-words">{task.goal}</h3>
            <div className="mt-2 flex items-center gap-2 flex-wrap text-[11px] text-text-secondary">
              <TaskStateBadge state={task.state} />
              <span className="tabular-nums">
                Schritt {task.step}/{task.max_steps}
              </span>
              <span>· {task.llm_calls} Modellaufrufe</span>
              <span>· {scopeSummary(task.scopes)}</span>
              {task.host_bound && <span>· an diesen Rechner gebunden</span>}
              {task.workflow_id && (
                <button type="button" onClick={showWorkflows} className="inline-flex items-center gap-1 hover:text-text-primary underline underline-offset-2" data-testid="task-workflow-link">
                  <BookOpen className="w-3 h-3" /> Ablauf{workflowName ? ` „${workflowName}“` : ""}
                </button>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1.5 flex-shrink-0">
            {canPause(task) && (
              <Button size="sm" onClick={() => void pauseTask(task.id)} icon={<Pause className="w-3.5 h-3.5" />} data-testid="task-pause">
                Pause
              </Button>
            )}
            {canResume(task) && (
              <Button size="sm" onClick={() => void resumeTask(task.id)} icon={<Play className="w-3.5 h-3.5" />} data-testid="task-resume">
                Weiter
              </Button>
            )}
            {canCancel(task) && (
              <Button size="sm" variant="danger" onClick={() => void cancelTask(task.id)} icon={<Square className="w-3.5 h-3.5" />} data-testid="task-cancel">
                Stopp
              </Button>
            )}
            {canCompileWorkflow(task) && (
              <Button size="sm" onClick={() => setCompileOpen(true)} icon={<Save className="w-3.5 h-3.5" />} data-testid="task-save-recipe">
                Als Ablauf speichern
              </Button>
            )}
          </div>
        </div>
        {task.summary_de && (
          <p className="mt-3 text-sm text-text-secondary leading-relaxed" data-testid="task-detail-summary">
            {task.summary_de}
          </p>
        )}
        {task.last_error && <p className="mt-2 text-xs text-red-300">{task.last_error}</p>}
        {feed?.activity && (
          <div data-testid="task-activity">
            <ActivityLine track={feed.activity} live={!isTerminalTaskState(task.state) && !feed.done} stopping={false} />
          </div>
        )}
        {task.result && (
          <div className={cn("mt-3 rounded-lg border px-3 py-2 text-sm", task.result.ok ? "border-emerald-500/30 bg-emerald-500/10" : "border-red-500/30 bg-red-500/10")} data-testid="task-result">
            <div className="flex items-center gap-2">
              <span className="font-medium">{task.result.ok ? "Ergebnis" : "Nicht erreicht"}</span>
              <Badge tone={task.result.verified ? "green" : "yellow"}>{task.result.verified ? "geprüft" : "ungeprüft"}</Badge>
            </div>
            <p className="mt-1 text-text-secondary">{task.result.summary_de}</p>
            {(task.result.artifacts.length > 0 || resultPaths.length > 0) && (
              <ul className="mt-2 flex flex-col gap-1">
                {task.result.artifacts.map((a, i) => (
                  <li key={i} className="text-xs flex items-center gap-2 min-w-0">
                    <span className="text-text-secondary flex-shrink-0">{a.label}</span>
                    {a.path && <EvidencePath path={a.path} />}
                    {a.url && <span className="font-mono text-text-secondary truncate">{a.url}</span>}
                  </li>
                ))}
                {resultPaths.map((p) => (
                  <li key={p} className="text-xs flex items-center gap-2 min-w-0">
                    <span className="text-text-secondary flex-shrink-0">Beleg</span>
                    <EvidencePath path={p} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <OutcomeBlock task={task} />
        {task.reuse && <ReuseBlock reuse={task.reuse} />}
        <div className="mt-3 grid grid-cols-2 gap-x-6">
          <KeyValue label="Angelegt" value={formatDateTime(task.created_at)} />
          <KeyValue label="Beendet" value={task.finished_at ? formatDateTime(task.finished_at) : "–"} />
        </div>
      </section>

      <section>
        <SectionTitle>Tatsächliche Schritte</SectionTitle>
        {steps.length === 0 ? (
          <div className="text-xs text-text-secondary px-1">Noch keine belegten Schritte.</div>
        ) : (
          <ol className="panel divide-y divide-border/60" data-testid="step-timeline">
            {steps.map((s, i) => (
              <StepRow key={`${s.at}-${i}`} step={s} />
            ))}
          </ol>
        )}
      </section>

      <section>
        <SectionTitle>Ablauf der Aktionen</SectionTitle>
        {actions.length === 0 ? (
          <div className="text-xs text-text-secondary px-1">Noch keine Aktionen.</div>
        ) : (
          <ol className="panel divide-y divide-border/60" data-testid="action-timeline">
            {actions.map((a) => (
              <ActionRow key={a.action_id} action={a} />
            ))}
          </ol>
        )}
      </section>

      {feed?.notes && (
        <section>
          <button type="button" onClick={() => setNotesOpen((v) => !v)} aria-expanded={notesOpen} className="inline-flex items-center gap-1 text-xs text-text-secondary hover:text-text-primary">
            {notesOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            Erklärtext des Agenten (kein Beleg)
          </button>
          {notesOpen && <pre className="mt-2 panel p-3 text-xs text-text-secondary whitespace-pre-wrap break-words font-sans leading-relaxed max-h-64 overflow-y-auto">{feed.notes}</pre>}
        </section>
      )}

      {question && <QuestionDialog task={task} question={question} onAnswer={(approve, text) => void answerTask(task.id, { question_id: question.id, approve, text })} />}
      <CompileDialog open={compileOpen} onClose={() => setCompileOpen(false)} onSave={(name, parameters) => compileWorkflow(task.id, name, parameters)} onShowWorkflows={showWorkflows} />
    </div>
  );
}
