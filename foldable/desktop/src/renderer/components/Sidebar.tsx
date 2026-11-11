import { BookOpen, Brain, ChevronLeft, ChevronRight, ListChecks, ListPlus, MessageSquare, MessageSquarePlus, Power, Radio, Settings } from "lucide-react";
import { useState } from "react";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import type { Conversation } from "../api/types";
import { RUNTIME_STATE_LABEL } from "../lib/labels";
import { useAppStore } from "../store/useAppStore";
import ConversationItem from "./ConversationItem";
import Logo from "./Logo";
import TasksSidebarList from "./tasks/TasksSidebarList";
import { EmptyState } from "./ui";

const RUNTIME_DOT = {
  ready: "bg-accent-green",
  starting: "bg-accent-yellow",
  stopped: "bg-[#6B7280]",
  error: "bg-accent-red",
} as const;

export default function Sidebar() {
  const conversations = useAppStore((s) => s.conversations);
  const conversationsLoaded = useAppStore((s) => s.conversationsLoaded);
  const activeId = useAppStore((s) => s.activeId);
  const collapsed = useAppStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const newConversation = useAppStore((s) => s.newConversation);
  const selectConversation = useAppStore((s) => s.selectConversation);
  const renameConversation = useAppStore((s) => s.renameConversation);
  const deleteConversation = useAppStore((s) => s.deleteConversation);
  const openSettings = useAppStore((s) => s.openSettings);
  const requestQuit = useAppStore((s) => s.requestQuit);
  const setup = useAppStore((s) => s.setup);
  const scheduler = useAppStore((s) => s.scheduler);
  // v1.2
  const mainView = useAppStore((s) => s.mainView);
  const setMainView = useAppStore((s) => s.setMainView);
  const tasks = useAppStore((s) => s.tasks);
  const tasksLoaded = useAppStore((s) => s.tasksLoaded);
  const taskFeeds = useAppStore((s) => s.taskFeeds);
  const selectedTaskId = useAppStore((s) => s.selectedTaskId);
  const selectTask = useAppStore((s) => s.selectTask);
  const openTaskForm = useAppStore((s) => s.openTaskForm);
  const showRecipes = useAppStore((s) => s.showRecipes);
  const cancelTask = useAppStore((s) => s.cancelTask);
  const pauseTask = useAppStore((s) => s.pauseTask);
  const resumeTask = useAppStore((s) => s.resumeTask);

  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const [quitOpen, setQuitOpen] = useState(false);

  const runtime = setup?.runtime_state ?? "stopped";
  const modelName = setup?.active_chat_model?.name ?? "Kein Modell aktiv";
  const background = scheduler?.background.kind ? scheduler.background : null;
  const backgroundText = background
    ? `Gedächtnis wird im Hintergrund aktualisiert${background.pending > 0 ? ` · ${background.pending} ausstehend` : ""}${scheduler?.paused ? " (pausiert)" : ""}`
    : null;
  const waiting = tasks.filter((t) => t.state === "waiting_for_user").length;
  const tasksMode = mainView === "tasks";

  return (
    <aside
      className={cn(
        "relative flex flex-col h-full flex-shrink-0 bg-bg-secondary/80 border-r border-border transition-[width] duration-200",
        collapsed ? "w-16" : "w-[280px]",
      )}
      aria-label="Seitenleiste"
    >
      <div className={cn("flex items-center gap-3 px-4 py-4 border-b border-border", collapsed && "justify-center px-0")}>
        <Logo />
        {!collapsed && (
          <div className="min-w-0">
            <div className="text-sm font-bold tracking-tight leading-none">SpAIder</div>
            <div className="text-[10px] text-text-secondary mt-0.5">Foldable</div>
          </div>
        )}
      </div>

      {/* Section switch: Gespräche | Aufgaben */}
      <div className={cn("px-3 pt-3", collapsed && "px-2")} role="tablist" aria-label="Bereich">
        <div className={cn("flex rounded-lg border border-border bg-bg-primary/40 p-0.5", collapsed && "flex-col")}>
          <button
            type="button"
            role="tab"
            aria-selected={!tasksMode}
            onClick={() => setMainView("chat")}
            title="Gespräche"
            className={cn(
              "flex-1 flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors",
              !tasksMode ? "bg-white/10 text-text-primary" : "text-text-secondary hover:text-text-primary",
            )}
            data-testid="section-chats"
          >
            <MessageSquare className="w-3.5 h-3.5" />
            {!collapsed && "Gespräche"}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tasksMode}
            onClick={() => setMainView("tasks")}
            title="Aufgaben"
            className={cn(
              "flex-1 flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors",
              tasksMode ? "bg-white/10 text-text-primary" : "text-text-secondary hover:text-text-primary",
            )}
            data-testid="section-tasks"
          >
            <ListChecks className="w-3.5 h-3.5" />
            {!collapsed && "Aufgaben"}
            {waiting > 0 && (
              <span className="inline-flex items-center justify-center min-w-[16px] h-4 rounded-full bg-violet-500/30 text-violet-200 text-[10px] px-1" title={`${waiting} Rückfrage(n)`} data-testid="tasks-waiting-badge">
                {waiting}
              </span>
            )}
          </button>
        </div>
      </div>

      <div className={cn("px-3 pt-3", collapsed && "px-2")}>
        {tasksMode ? (
          <div className={cn("flex gap-1.5", collapsed && "flex-col")}>
            <button
              type="button"
              onClick={() => openTaskForm()}
              title="Neue Aufgabe"
              className={cn(
                "flex-1 flex items-center justify-center gap-2 rounded-lg border border-border bg-bg-tertiary hover:bg-[#22222e] hover:border-[#3A3A45] text-sm font-medium transition-colors",
                collapsed ? "p-2.5" : "px-3 py-2",
              )}
              data-testid="sidebar-new-task"
            >
              <ListPlus className="w-4 h-4 text-accent-blue flex-shrink-0" />
              {!collapsed && <span>Neue Aufgabe</span>}
            </button>
            <button
              type="button"
              onClick={showRecipes}
              title="Abläufe"
              className={cn(
                "flex items-center justify-center gap-2 rounded-lg border border-border bg-bg-tertiary hover:bg-[#22222e] hover:border-[#3A3A45] text-sm transition-colors",
                collapsed ? "p-2.5" : "px-3 py-2",
              )}
              data-testid="sidebar-recipes"
            >
              <BookOpen className="w-4 h-4 text-accent-purple flex-shrink-0" />
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => void newConversation()}
            title="Neues Gespräch (Strg+N)"
            className={cn(
              "w-full flex items-center justify-center gap-2 rounded-lg border border-border bg-bg-tertiary hover:bg-[#22222e] hover:border-[#3A3A45] text-sm font-medium transition-colors",
              collapsed ? "p-2.5" : "px-3 py-2",
            )}
          >
            <MessageSquarePlus className="w-4 h-4 text-accent-blue flex-shrink-0" />
            {!collapsed && <span>Neues Gespräch</span>}
          </button>
        )}
      </div>

      <nav className={cn("flex-1 overflow-y-auto px-2 py-3 flex flex-col gap-0.5", collapsed && "hidden")} aria-label={tasksMode ? "Aufgaben" : "Gespräche"}>
        {tasksMode ? (
          !tasksLoaded ? (
            <div className="flex justify-center py-6">
              <LoadingSpinner size="sm" />
            </div>
          ) : tasks.length === 0 ? (
            <EmptyState title="Noch keine Aufgaben" description="Mit „Neue Aufgabe“ ein Ziel beschreiben und die erlaubten Zugriffe festlegen." className="py-8" />
          ) : (
            <TasksSidebarList
              tasks={tasks}
              feeds={taskFeeds}
              selectedId={selectedTaskId}
              onSelect={(id) => void selectTask(id)}
              onCancel={(id) => void cancelTask(id)}
              onPause={(id) => void pauseTask(id)}
              onResume={(id) => void resumeTask(id)}
            />
          )
        ) : !conversationsLoaded ? (
          <div className="flex justify-center py-6">
            <LoadingSpinner size="sm" />
          </div>
        ) : conversations.length === 0 ? (
          <EmptyState
            title="Noch keine Gespräche"
            description="Mit „Neues Gespräch“ starten oder einfach unten eine Nachricht schreiben."
            className="py-8"
          />
        ) : (
          conversations.map((c) => (
            <ConversationItem
              key={c.id}
              conversation={c}
              active={c.id === activeId}
              onSelect={() => void selectConversation(c.id)}
              onRename={(title) => void renameConversation(c.id, title)}
              onDelete={() => setDeleteTarget(c)}
            />
          ))
        )}
      </nav>
      {collapsed && <div className="flex-1" />}

      <div className={cn("border-t border-border p-3 flex flex-col gap-2", collapsed && "p-2 items-center")}>
        <div
          className={cn("flex items-center gap-2 px-1 py-1 text-xs text-text-secondary", collapsed && "px-0")}
          title={`${modelName} · ${RUNTIME_STATE_LABEL[runtime]}`}
        >
          <span className="relative flex-shrink-0">
            <span className={cn("block w-2 h-2 rounded-full", RUNTIME_DOT[runtime])} />
            {runtime === "ready" && <span className="absolute inset-0 w-2 h-2 rounded-full bg-accent-green animate-ping opacity-60" />}
          </span>
          {!collapsed && <span className="truncate">{modelName}</span>}
        </div>
        {backgroundText && (
          <div
            className={cn("flex items-center gap-2 px-1 text-[11px] text-amber-300/90", collapsed && "px-0")}
            title={backgroundText}
            data-testid="scheduler-indicator"
          >
            <Brain className="w-3 h-3 flex-shrink-0 activity-dot" />
            {!collapsed && <span className="truncate">{backgroundText}</span>}
          </div>
        )}
        <button
          type="button"
          onClick={() => void window.spaider.overlay.toggle()}
          title="Live-Fenster (Hotkey: siehe Einstellungen → Live & Agent)"
          className={cn(
            "flex items-center gap-2 rounded-lg text-sm text-text-secondary hover:text-text-primary hover:bg-white/5 transition-colors",
            collapsed ? "p-2.5 justify-center" : "px-3 py-2",
          )}
          data-testid="sidebar-overlay"
        >
          <Radio className="w-4 h-4 flex-shrink-0" />
          {!collapsed && <span>Live-Fenster</span>}
        </button>
        <button
          type="button"
          onClick={() => openSettings()}
          title="Einstellungen"
          className={cn(
            "flex items-center gap-2 rounded-lg text-sm text-text-secondary hover:text-text-primary hover:bg-white/5 transition-colors",
            collapsed ? "p-2.5 justify-center" : "px-3 py-2",
          )}
        >
          <Settings className="w-4 h-4 flex-shrink-0" />
          {!collapsed && <span>Einstellungen</span>}
        </button>
        <button
          type="button"
          onClick={() => setQuitOpen(true)}
          title="Sicher beenden"
          className={cn(
            "flex items-center gap-2 rounded-lg border border-border text-sm text-text-secondary hover:text-red-300 hover:border-red-500/40 hover:bg-red-500/5 transition-colors",
            collapsed ? "p-2.5 justify-center" : "px-3 py-2",
          )}
        >
          <Power className="w-4 h-4 flex-shrink-0" />
          {!collapsed && <span>Sicher beenden</span>}
        </button>
      </div>

      <button
        type="button"
        onClick={toggleSidebar}
        aria-label={collapsed ? "Seitenleiste ausklappen" : "Seitenleiste einklappen"}
        className="absolute -right-3 top-1/2 -translate-y-1/2 w-6 h-6 rounded-full bg-bg-secondary border border-border flex items-center justify-center text-text-secondary hover:text-text-primary hover:border-[#3A3A45] transition-colors z-10 shadow-md"
      >
        {collapsed ? <ChevronRight className="w-3 h-3" /> : <ChevronLeft className="w-3 h-3" />}
      </button>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Gespräch löschen?"
        description={`„${deleteTarget?.title || "Neues Gespräch"}“ wird dauerhaft gelöscht. Erinnerungen, die aus diesem Gespräch gewonnen wurden, bleiben im Gedächtnis erhalten.`}
        confirmLabel="Löschen"
        cancelLabel="Abbrechen"
        variant="destructive"
        onConfirm={() => {
          const target = deleteTarget;
          setDeleteTarget(null);
          if (target) void deleteConversation(target.id);
        }}
      />
      <ConfirmDialog
        open={quitOpen}
        onOpenChange={setQuitOpen}
        title="SpAIder sicher beenden?"
        description="Der Kern wird sauber gestoppt und alle Daten werden auf den Datenträger geschrieben. Den Datenträger erst entfernen, wenn das Fenster geschlossen ist."
        confirmLabel="Sicher beenden"
        cancelLabel="Abbrechen"
        variant="warning"
        onConfirm={() => {
          setQuitOpen(false);
          void requestQuit();
        }}
      />
    </aside>
  );
}
