import { Check, Pencil, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils";
import type { Conversation } from "../api/types";
import { formatRelativeDay } from "../lib/format";
import { IconButton } from "./ui";

export default function ConversationItem({
  conversation,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  conversation: Conversation;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(conversation.title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const commit = () => {
    const title = draft.trim();
    setEditing(false);
    if (title && title !== conversation.title) onRename(title);
    else setDraft(conversation.title);
  };
  const cancel = () => {
    setEditing(false);
    setDraft(conversation.title);
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      cancel();
    }
  };

  if (editing) {
    return (
      <div className="flex items-center gap-1 px-2 py-1">
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          onBlur={commit}
          aria-label="Neuer Titel"
          className="flex-1 min-w-0 bg-bg-primary border border-accent-blue rounded-md px-2 py-1 text-sm"
          maxLength={120}
        />
        <IconButton label="Übernehmen" onMouseDown={(e) => e.preventDefault()} onClick={commit}>
          <Check className="w-3.5 h-3.5" />
        </IconButton>
        <IconButton label="Abbrechen" onMouseDown={(e) => e.preventDefault()} onClick={cancel}>
          <X className="w-3.5 h-3.5" />
        </IconButton>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "group relative flex items-center rounded-lg transition-colors",
        active ? "bg-white/5 border border-white/15" : "border border-transparent hover:bg-white/5",
      )}
    >
      {active && <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 rounded-full bg-accent-blue" />}
      <button
        type="button"
        onClick={onSelect}
        onDoubleClick={() => setEditing(true)}
        className="flex-1 min-w-0 text-left px-3 py-2"
        aria-current={active ? "true" : undefined}
      >
        <div className={cn("text-sm truncate", active ? "text-text-primary" : "text-text-secondary group-hover:text-text-primary")}>
          {conversation.title || "Neues Gespräch"}
        </div>
        <div className="text-[11px] text-text-secondary/70 mt-0.5">
          {formatRelativeDay(conversation.updated_at)}
          {conversation.message_count > 0 && ` · ${conversation.message_count} Nachrichten`}
        </div>
      </button>
      <div className="flex items-center gap-0.5 pr-1.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
        <IconButton label="Umbenennen" onClick={() => setEditing(true)}>
          <Pencil className="w-3.5 h-3.5" />
        </IconButton>
        <IconButton label="Löschen" danger onClick={onDelete}>
          <Trash2 className="w-3.5 h-3.5" />
        </IconButton>
      </div>
    </div>
  );
}
