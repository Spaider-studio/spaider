import { Brain, ChevronLeft, ChevronRight, MessageSquarePlus, Power, Settings } from "lucide-react";
import { useState } from "react";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import type { Conversation } from "../api/types";
import { RUNTIME_STATE_LABEL } from "../lib/labels";
import { useAppStore } from "../store/useAppStore";
import ConversationItem from "./ConversationItem";
import Logo from "./Logo";
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

  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const [quitOpen, setQuitOpen] = useState(false);

  const runtime = setup?.runtime_state ?? "stopped";
  const modelName = setup?.active_chat_model?.name ?? "Kein Modell aktiv";
  const background = scheduler?.background.kind ? scheduler.background : null;
  const backgroundText = background
    ? `Gedächtnis wird im Hintergrund aktualisiert${background.pending > 0 ? ` · ${background.pending} ausstehend` : ""}${scheduler?.paused ? " (pausiert)" : ""}`
    : null;

  return (
    <aside
      className={cn(
        "relative flex flex-col h-full flex-shrink-0 bg-bg-secondary/80 border-r border-border transition-[width] duration-200",
        collapsed ? "w-16" : "w-[260px]",
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

      <div className={cn("px-3 pt-3", collapsed && "px-2")}>
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
      </div>

      <nav className={cn("flex-1 overflow-y-auto px-2 py-3 flex flex-col gap-0.5", collapsed && "hidden")} aria-label="Gespräche">
        {!conversationsLoaded ? (
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
