/**
 * Detail of one task: header with state / step / buttons, result, the
 * question dialog for `pending_question` (approve / deny / input ->
 * POST .../answer), "Als Ablauf speichern", the agent's activity line
 * (observe / planning / executing phases from `event: activity`) and the
 * action timeline (tool, compact arguments, state, result summary, evidence
 * path) fed by GET /api/agent/tasks/{id}/events.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { Check, ChevronDown, ChevronRight, Copy, Pause, Play, Save, Square, X } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import type { Action, PendingQuestion, RecipeParameter, Task } from "../../api/types";
import { formatDateTime, formatSeconds } from "../../lib/format";
import ActivityLine from "../chat/ActivityLine";
import { copyText } from "../chat/CodeBlock";
import {
  ACTION_STATE_LABEL,
  canCancel,
  canPause,
  canResume,
  canSaveRecipe,
  compactArguments,
  evidencePath,
  isTerminalTaskState,
  scopeSummary,
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

function EvidencePath({ path }: { path: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => void copyText(path).then((ok) => ok && (setCopied(true), setTimeout(() => setCopied(false), 1500)))}
      className="inline-flex items-center gap-1 text-[11px] text-accent-blue hover:underline underline-offset-2 max-w-full"
      title={`Beleg: ${path} (Pfad kopieren)`}
      data-testid="action-evidence"
    >
      {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
      <span className="truncate font-mono">{path}</span>
    </button>
  );
}

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
              {JSON.stringify({ expected_effect: action.expected_effect, arguments: action.arguments, result: action.result, observation_ref: action.observation_ref }, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </li>
  );
}

function QuestionDialog({ task, question, onAnswer }: { task: Task; question: PendingQuestion; onAnswer: (approve: boolean | null, text: string | null) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
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
              <input value={text} onChange={(e) => setText(e.target.value)} aria-label="Antwort" className={inputClass} placeholder="Antwort eingeben …" autoFocus />
              <div className="flex justify-end gap-2">
                <Button onClick={() => answer(false, null)} disabled={busy}>
                  Abbrechen
                </Button>
                <Button type="submit" variant="primary" disabled={busy || !text.trim()} loading={busy}>
                  Antworten
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

function SaveRecipeDialog({ open, onClose, onSave }: { open: boolean; onClose: () => void; onSave: (name: string, parameters: RecipeParameter[]) => Promise<void> }) {
  const [name, setName] = useState("");
  const [params, setParams] = useState<RecipeParameter[]>([]);
  const [busy, setBusy] = useState(false);
  const update = (i: number, patch: Partial<RecipeParameter>) => setParams((p) => p.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 data-[state=open]:animate-fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[min(560px,calc(100vw-2rem))] bg-bg-secondary border border-border rounded-xl p-5 shadow-2xl focus:outline-none" data-testid="save-recipe-dialog">
          <div className="flex items-start justify-between gap-3">
            <div>
              <Dialog.Title className="text-sm font-semibold">Als Ablauf speichern</Dialog.Title>
              <Dialog.Description className="mt-1 text-xs text-text-secondary">Der Kern leitet aus den ausgeführten Schritten einen wiederverwendbaren Ablauf ab.</Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button type="button" aria-label="Schließen" className="text-text-secondary hover:text-text-primary">
                <X className="w-4 h-4" />
              </button>
            </Dialog.Close>
          </div>
          <form
            className="mt-4 flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!name.trim()) return;
              setBusy(true);
              void onSave(name.trim(), params.filter((p) => p.name.trim())).finally(() => setBusy(false));
            }}
          >
            <input value={name} onChange={(e) => setName(e.target.value)} aria-label="Name des Ablaufs" placeholder="Name des Ablaufs" className={inputClass} autoFocus />
            <div className="text-xs text-text-secondary">Parameter (optional) – Werte, die bei jedem Start anders sein dürfen:</div>
            {params.map((p, i) => (
              <div key={i} className="grid grid-cols-[1fr_1.4fr_1fr_auto] gap-2 items-center">
                <input value={p.name} onChange={(e) => update(i, { name: e.target.value })} placeholder="name" aria-label="Parametername" className={cn(inputClass, "py-1 text-xs font-mono")} />
                <input value={p.description_de} onChange={(e) => update(i, { description_de: e.target.value })} placeholder="Beschreibung" aria-label="Beschreibung" className={cn(inputClass, "py-1 text-xs")} />
                <input value={p.example} onChange={(e) => update(i, { example: e.target.value })} placeholder="Beispiel" aria-label="Beispiel" className={cn(inputClass, "py-1 text-xs")} />
                <button type="button" onClick={() => setParams((x) => x.filter((_, idx) => idx !== i))} aria-label="Parameter entfernen" className="text-text-secondary hover:text-red-300">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
            <div>
              <Button size="sm" variant="ghost" onClick={() => setParams((p) => [...p, { name: "", description_de: "", example: "" }])}>
                Parameter hinzufügen
              </Button>
            </div>
            <div className="flex justify-end gap-2 mt-2">
              <Button onClick={onClose} disabled={busy}>
                Abbrechen
              </Button>
              <Button type="submit" variant="primary" disabled={!name.trim() || busy} loading={busy} icon={<Save className="w-3.5 h-3.5" />}>
                Speichern
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export default function TaskDetail({ task, feed }: { task: Task; feed: TaskFeed | undefined }) {
  const cancelTask = useAppStore((s) => s.cancelTask);
  const pauseTask = useAppStore((s) => s.pauseTask);
  const resumeTask = useAppStore((s) => s.resumeTask);
  const answerTask = useAppStore((s) => s.answerTask);
  const saveRecipe = useAppStore((s) => s.saveRecipe);
  const [saveOpen, setSaveOpen] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);

  const question = task.pending_question ?? feed?.question ?? null;
  const actions = feed?.actions ?? [];

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
            {canSaveRecipe(task) && (
              <Button size="sm" onClick={() => setSaveOpen(true)} icon={<Save className="w-3.5 h-3.5" />} data-testid="task-save-recipe">
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
            {task.result.artifacts.length > 0 && (
              <ul className="mt-2 flex flex-col gap-1">
                {task.result.artifacts.map((a, i) => (
                  <li key={i} className="text-xs flex items-center gap-2">
                    <span className="text-text-secondary">{a.label}</span>
                    {a.path && <EvidencePath path={a.path} />}
                    {a.url && <span className="font-mono text-text-secondary truncate">{a.url}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <div className="mt-3 grid grid-cols-2 gap-x-6">
          <KeyValue label="Angelegt" value={formatDateTime(task.created_at)} />
          <KeyValue label="Beendet" value={task.finished_at ? formatDateTime(task.finished_at) : "–"} />
        </div>
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
      <SaveRecipeDialog
        open={saveOpen}
        onClose={() => setSaveOpen(false)}
        onSave={async (name, parameters) => {
          const recipe = await saveRecipe(task.id, name, parameters);
          if (recipe) setSaveOpen(false);
        }}
      />
    </div>
  );
}
