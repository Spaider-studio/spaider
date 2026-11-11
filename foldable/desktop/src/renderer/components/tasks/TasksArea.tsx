/**
 * Main area of the sidebar section "Aufgaben": create form, task detail,
 * the workflow list or the teach panel ("Ablauf beibringen").
 */
import { BookOpen, GraduationCap, Plus } from "lucide-react";
import { useAppStore } from "../../store/useAppStore";
import { Button } from "../ui";
import TaskCreateForm from "./TaskCreateForm";
import TaskDetail from "./TaskDetail";
import TeachPanel from "./TeachPanel";
import WorkflowsList from "./WorkflowsList";

export default function TasksArea() {
  const view = useAppStore((s) => s.tasksView);
  const selectedId = useAppStore((s) => s.selectedTaskId);
  const task = useAppStore((s) => (s.selectedTaskId ? (s.tasks.find((t) => t.id === s.selectedTaskId) ?? null) : null));
  const feed = useAppStore((s) => (s.selectedTaskId ? s.taskFeeds[s.selectedTaskId] : undefined));
  const openTaskForm = useAppStore((s) => s.openTaskForm);
  const showWorkflows = useAppStore((s) => s.showWorkflows);
  const showTeach = useAppStore((s) => s.showTeach);
  const teachRecording = useAppStore((s) => s.teachSession?.state === "recording");
  const lastStop = useAppStore((s) => s.lastStop);

  const title = view === "workflows" ? "Abläufe" : view === "teach" ? "Ablauf beibringen" : view === "detail" && task ? "Aufgabe" : "Neue Aufgabe";

  return (
    <section className="flex-1 min-w-0 flex flex-col h-full bg-bg-primary" aria-label="Aufgaben" data-testid="tasks-area">
      <header className="flex items-center gap-3 px-5 py-3 border-b border-border min-h-[52px]">
        <h2 className="text-sm font-medium truncate flex-1">{title}</h2>
        {lastStop && (
          <span className="text-[11px] text-amber-300/90" data-testid="last-stop">
            Globaler Stopp ({lastStop.source === "hotkey" ? "Hotkey" : "API"})
          </span>
        )}
        <Button size="sm" variant={view === "create" ? "primary" : "secondary"} onClick={() => openTaskForm()} icon={<Plus className="w-3.5 h-3.5" />} data-testid="tasks-new">
          Neue Aufgabe
        </Button>
        <Button size="sm" variant={view === "workflows" ? "primary" : "secondary"} onClick={showWorkflows} icon={<BookOpen className="w-3.5 h-3.5" />} data-testid="tasks-workflows">
          Abläufe
        </Button>
        <Button size="sm" variant={view === "teach" ? "primary" : "secondary"} onClick={showTeach} icon={<GraduationCap className="w-3.5 h-3.5" />} data-testid="tasks-teach">
          Ablauf beibringen
          {teachRecording && view !== "teach" && <span className="ml-1 inline-block w-2 h-2 rounded-full bg-red-400 animate-pulse" aria-label="Aufnahme läuft" />}
        </Button>
      </header>
      <div className="flex-1 min-h-0 overflow-y-auto px-5 py-5">
        {view === "workflows" ? (
          <WorkflowsList />
        ) : view === "teach" ? (
          <TeachPanel />
        ) : view === "detail" && task ? (
          <TaskDetail task={task} feed={feed} />
        ) : view === "detail" && selectedId ? (
          <div className="text-sm text-text-secondary">Aufgabe wird geladen …</div>
        ) : (
          <TaskCreateForm />
        )}
      </div>
    </section>
  );
}
