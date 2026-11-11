/**
 * Main area of the sidebar section "Aufgaben": create form, task detail or
 * the recipes list.
 */
import { BookOpen, Plus } from "lucide-react";
import { useAppStore } from "../../store/useAppStore";
import { Button } from "../ui";
import RecipesList from "./RecipesList";
import TaskCreateForm from "./TaskCreateForm";
import TaskDetail from "./TaskDetail";

export default function TasksArea() {
  const view = useAppStore((s) => s.tasksView);
  const selectedId = useAppStore((s) => s.selectedTaskId);
  const task = useAppStore((s) => (s.selectedTaskId ? (s.tasks.find((t) => t.id === s.selectedTaskId) ?? null) : null));
  const feed = useAppStore((s) => (s.selectedTaskId ? s.taskFeeds[s.selectedTaskId] : undefined));
  const openTaskForm = useAppStore((s) => s.openTaskForm);
  const showRecipes = useAppStore((s) => s.showRecipes);
  const lastStop = useAppStore((s) => s.lastStop);

  const title = view === "recipes" ? "Abläufe" : view === "detail" && task ? "Aufgabe" : "Neue Aufgabe";

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
        <Button size="sm" variant={view === "recipes" ? "primary" : "secondary"} onClick={showRecipes} icon={<BookOpen className="w-3.5 h-3.5" />} data-testid="tasks-recipes">
          Abläufe
        </Button>
      </header>
      <div className="flex-1 min-h-0 overflow-y-auto px-5 py-5">
        {view === "recipes" ? (
          <RecipesList />
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
