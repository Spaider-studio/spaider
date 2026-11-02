import { Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { errorMessage } from "../../api/client";
import type { MemoryOverview, Note, SearchResult } from "../../api/types";
import { formatDateTime, formatNumber } from "../../lib/format";
import { notify } from "../../lib/notify";
import { useAppStore } from "../../store/useAppStore";
import { SourceItem } from "../chat/SourcesBlock";
import { Button, EmptyState, IconButton, SectionTitle, inputClass } from "../ui";

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="panel px-3 py-2.5">
      <div className="text-lg font-semibold tabular-nums leading-tight">{formatNumber(value)}</div>
      <div className="text-[11px] text-text-secondary mt-0.5">{label}</div>
    </div>
  );
}

export default function MemoryTab() {
  const client = useAppStore((s) => s.client);
  const [overview, setOverview] = useState<MemoryOverview | null>(null);
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [newNote, setNewNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Note | null>(null);

  const load = useCallback(async () => {
    if (!client) return;
    try {
      const [o, n] = await Promise.all([client.memoryOverview(), client.notes()]);
      setOverview(o);
      setNotes(n);
    } catch (e) {
      notify(`Gedächtnis konnte nicht geladen werden: ${errorMessage(e)}`, "error");
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const search = async (e: FormEvent) => {
    e.preventDefault();
    if (!client || !query.trim()) {
      setResults(null);
      return;
    }
    setSearching(true);
    try {
      setResults(await client.memorySearch(query.trim(), 20));
    } catch (err) {
      notify(`Suche fehlgeschlagen: ${errorMessage(err)}`, "error");
    } finally {
      setSearching(false);
    }
  };

  const addNote = async () => {
    if (!client || !newNote.trim()) return;
    setSaving(true);
    try {
      const note = await client.createNote(newNote.trim());
      setNotes((n) => [note, ...(n ?? [])]);
      setNewNote("");
      notify("Notiz gespeichert.", "success");
      void load();
    } catch (e) {
      notify(`Notiz nicht gespeichert: ${errorMessage(e)}`, "error");
    } finally {
      setSaving(false);
    }
  };

  const saveEdit = async () => {
    if (!client || !editing) return;
    const text = editing.text.trim();
    if (!text) return;
    try {
      const updated = await client.updateNote(editing.id, text);
      setNotes((n) => (n ?? []).map((x) => (x.id === updated.id ? updated : x)));
      setEditing(null);
    } catch (e) {
      notify(`Änderung nicht gespeichert: ${errorMessage(e)}`, "error");
    }
  };

  const removeNote = async (note: Note) => {
    if (!client) return;
    try {
      await client.deleteNote(note.id);
      setNotes((n) => (n ?? []).filter((x) => x.id !== note.id));
      notify("Notiz gelöscht.", "success");
      void load();
    } catch (e) {
      notify(`Löschen fehlgeschlagen: ${errorMessage(e)}`, "error");
    }
  };

  return (
    <div className="flex flex-col gap-8">
      <section>
        <SectionTitle>Überblick</SectionTitle>
        {overview ? (
          <>
            <div className="grid grid-cols-4 gap-2">
              <Stat label="Fakten" value={overview.facts} />
              <Stat label="Entitäten" value={overview.entities} />
              <Stat label="Beziehungen" value={overview.relations} />
              <Stat label="Ersetzte Fakten" value={overview.superseded} />
              <Stat label="Dokumente" value={overview.documents} />
              <Stat label="Notizen" value={overview.notes} />
              <Stat label="Gespräche" value={overview.conversations} />
              <Stat label="Nachrichten" value={overview.messages} />
            </div>
            <div className="mt-2 text-[11px] text-text-secondary">
              Volltextindex: {overview.index.fts ? "aktiv" : "inaktiv"} · Vektorsuche:{" "}
              {overview.index.vectors.enabled
                ? `aktiv (${overview.index.vectors.model_id ?? "Modell"}, ${overview.index.vectors.dimensions ?? "?"} Dimensionen, ${formatNumber(overview.index.vectors.count)} Einträge)`
                : "inaktiv (kein Embedding-Modell)"}
            </div>
          </>
        ) : (
          <div className="flex justify-center py-4">
            <LoadingSpinner size="sm" />
          </div>
        )}
      </section>

      <section>
        <SectionTitle>Gedächtnis durchsuchen</SectionTitle>
        <form onSubmit={(e) => void search(e)} className="flex gap-2">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Suchbegriff …"
              aria-label="Suchbegriff"
              className={`${inputClass} pl-9`}
            />
          </div>
          <Button type="submit" variant="primary" loading={searching} disabled={!query.trim()}>
            Suchen
          </Button>
        </form>
        {results !== null && (
          <div className="mt-3 panel px-3">
            {results.length === 0 ? (
              <EmptyState title="Keine Treffer" description="Andere Begriffe probieren." className="py-6" />
            ) : (
              <ul>
                {results.map((r) => (
                  <SourceItem key={`${r.kind}-${r.id}`} source={r} score={r.score} />
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      <section>
        <SectionTitle>Notizen</SectionTitle>
        <p className="text-xs text-text-secondary mb-3">
          Notizen sind feste Erinnerungen, die SpAIder bei passenden Fragen berücksichtigt (z. B. Vorlieben, Projekte, Namen).
        </p>
        <div className="panel p-3">
          <textarea
            value={newNote}
            onChange={(e) => setNewNote(e.target.value)}
            rows={2}
            placeholder="Neue Notiz …"
            aria-label="Neue Notiz"
            className={`${inputClass} resize-y`}
          />
          <div className="flex justify-end mt-2">
            <Button size="sm" variant="primary" icon={<Plus className="w-3.5 h-3.5" />} onClick={() => void addNote()} loading={saving} disabled={!newNote.trim()}>
              Notiz speichern
            </Button>
          </div>
        </div>
        {notes === null ? (
          <div className="flex justify-center py-4">
            <LoadingSpinner size="sm" />
          </div>
        ) : notes.length === 0 ? (
          <EmptyState title="Noch keine Notizen" className="py-6" />
        ) : (
          <ul className="mt-3 flex flex-col gap-2">
            {notes.map((note) => (
              <li key={note.id} className="panel p-3">
                {editing?.id === note.id ? (
                  <>
                    <textarea
                      value={editing.text}
                      onChange={(e) => setEditing({ id: note.id, text: e.target.value })}
                      rows={3}
                      aria-label="Notiz bearbeiten"
                      className={`${inputClass} resize-y`}
                      autoFocus
                    />
                    <div className="flex justify-end gap-2 mt-2">
                      <Button size="sm" variant="ghost" icon={<X className="w-3.5 h-3.5" />} onClick={() => setEditing(null)}>
                        Abbrechen
                      </Button>
                      <Button size="sm" variant="primary" onClick={() => void saveEdit()} disabled={!editing.text.trim()}>
                        Speichern
                      </Button>
                    </div>
                  </>
                ) : (
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm whitespace-pre-wrap break-words">{note.text}</p>
                      <div className="text-[11px] text-text-secondary mt-1">
                        {formatDateTime(note.updated_at)}
                      </div>
                    </div>
                    <IconButton label="Bearbeiten" onClick={() => setEditing({ id: note.id, text: note.text })}>
                      <Pencil className="w-3.5 h-3.5" />
                    </IconButton>
                    <IconButton label="Löschen" danger onClick={() => setDeleteTarget(note)}>
                      <Trash2 className="w-3.5 h-3.5" />
                    </IconButton>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Notiz löschen?"
        description="Die Notiz wird dauerhaft aus dem Gedächtnis entfernt."
        confirmLabel="Löschen"
        cancelLabel="Abbrechen"
        variant="destructive"
        onConfirm={() => {
          const target = deleteTarget;
          setDeleteTarget(null);
          if (target) void removeNote(target);
        }}
      />
    </div>
  );
}
