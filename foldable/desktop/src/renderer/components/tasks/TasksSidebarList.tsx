/**
 * Task list in the sidebar section "Aufgaben": state badge (German label for
 * every TaskState), step/max_steps, summary, last action and Stopp / Pause /
 * Weiter. Fed by GET /api/agent/tasks, the per-task event stream and the
 * global channel (`event: task`).
 */
import { Pause, Play, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Task } from "../../api/types";
import { truncate } from "../../lib/format";
import { canCancel, canPause, canResume, type TaskFeed } from "../../store/tasks";
import { IconButton } from "../ui";
import TaskStateBadge from "./TaskStateBadge";

export function lastActionText(feed: TaskFeed | undefined): string | null {
  const last = feed?.actions[feed.actions.length - 1];
  if (!last) return null;
  const result = last.result?.summary_de;
  return `${last.tool}${result ? `: ${result}` : last.expected_effect ? ` → ${last.expected_effect}` : ""}`;
}

export default function TasksSidebarList({
  tasks,
  feeds,
  selectedId,
  onSelect,
  onCancel,
  onPause,
  onResume,
}: {
  tasks: Task[];
  feeds: Record<string, TaskFeed>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCancel: (id: string) => void;
  onPause: (id: string) => void;
  onResume: (id: string) => void;
}) {
  return (
    <ul className="flex flex-col gap-1" aria-label="Aufgabenliste" data-testid="task-list">
      {tasks.map((t) => {
        const active = t.id === selectedId;
        const last = lastActionText(feeds[t.id]);
        return (
          <li
            key={t.id}
            className={cn(
              "group relative rounded-lg border transition-colors",
              active ? "bg-white/5 border-white/15" : "border-transparent hover:bg-white/5",
            )}
            data-testid="task-row"
            data-task-id={t.id}
          >
            {active && <span className="absolute left-0 top-3 w-0.5 h-5 rounded-full bg-accent-blue" />}
            <button type="button" onClick={() => onSelect(t.id)} className="w-full text-left px-3 py-2" aria-current={active ? "true" : undefined}>
              <div className={cn("text-sm truncate", active ? "text-text-primary" : "text-text-secondary group-hover:text-text-primary")} title={t.goal}>
                {truncate(t.goal, 60) || "Aufgabe"}
              </div>
              <div className="mt-1 flex items-center gap-1.5 flex-wrap">
                <TaskStateBadge state={t.state} />
                <span className="text-[10px] text-text-secondary tabular-nums" data-testid="task-steps">
                  Schritt {t.step}/{t.max_steps}
                </span>
                {t.pending_question && <span className="text-[10px] text-violet-300">Rückfrage</span>}
              </div>
              {t.summary_de && (
                <div className="mt-0.5 text-[11px] text-text-secondary truncate" title={t.summary_de} data-testid="task-summary">
                  {t.summary_de}
                </div>
              )}
              {last && (
                <div className="text-[10px] text-text-secondary/70 truncate" title={last} data-testid="task-last-action">
                  Zuletzt: {last}
                </div>
              )}
            </button>
            <div className="absolute right-1.5 top-1.5 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity bg-bg-secondary/90 rounded-md">
              {canPause(t) && (
                <IconButton label="Pause" onClick={() => onPause(t.id)}>
                  <Pause className="w-3.5 h-3.5" />
                </IconButton>
              )}
              {canResume(t) && (
                <IconButton label="Weiter" onClick={() => onResume(t.id)}>
                  <Play className="w-3.5 h-3.5" />
                </IconButton>
              )}
              {canCancel(t) && (
                <IconButton label="Stopp" danger onClick={() => onCancel(t.id)}>
                  <Square className="w-3.5 h-3.5" />
                </IconButton>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
