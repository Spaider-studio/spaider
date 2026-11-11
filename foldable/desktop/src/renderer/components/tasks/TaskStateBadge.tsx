import type { TaskState } from "../../api/types";
import { TASK_STATE_LABEL, TASK_STATE_TONE } from "../../store/tasks";
import { Badge } from "../ui";

export default function TaskStateBadge({ state, className }: { state: TaskState; className?: string }) {
  return (
    <span data-testid="task-state" data-state={state} className={className}>
      <Badge tone={TASK_STATE_TONE[state]}>{TASK_STATE_LABEL[state] ?? state}</Badge>
    </span>
  );
}
