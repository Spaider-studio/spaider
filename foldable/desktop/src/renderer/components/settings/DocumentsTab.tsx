import { FileText, Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import type { Document } from "../../api/types";
import { formatBytes, formatDateTime, formatNumber } from "../../lib/format";
import { DOCUMENT_STATUS_LABEL, documentWarningText } from "../../lib/labels";
import { useAppStore } from "../../store/useAppStore";
import { ACCEPTED_DOCUMENT_TYPES } from "../chat/Composer";
import { Badge, Button, EmptyState, IconButton, ProgressBar, SectionTitle } from "../ui";

const STATUS_TONE = { imported: "neutral", indexing: "yellow", indexed: "green", failed: "red" } as const;

export default function DocumentsTab() {
  const documents = useAppStore((s) => s.documents);
  const conversations = useAppStore((s) => s.conversations);
  const loadDocuments = useAppStore((s) => s.loadDocuments);
  const uploadDocument = useAppStore((s) => s.uploadDocument);
  const deleteDocument = useAppStore((s) => s.deleteDocument);
  const [deleteTarget, setDeleteTarget] = useState<Document | null>(null);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setImporting(true);
    try {
      await uploadDocument(file);
    } finally {
      setImporting(false);
    }
  };

  const conversationTitle = (id: string | null) => (id ? conversations.find((c) => c.id === id)?.title : null);

  return (
    <div className="flex flex-col gap-4">
      <input ref={fileRef} type="file" accept={ACCEPTED_DOCUMENT_TYPES} className="hidden" onChange={(e) => void onFile(e)} aria-hidden="true" />
      <SectionTitle
        action={
          <Button size="sm" variant="primary" icon={<Upload className="w-3.5 h-3.5" />} onClick={() => fileRef.current?.click()} loading={importing}>
            Datei importieren
          </Button>
        }
      >
        Importierte Dokumente
      </SectionTitle>
      <p className="text-xs text-text-secondary -mt-2">
        Unterstützt: .txt, .md, .markdown, .pdf (mit Textebene), bis 50 MB. Der Inhalt wird lokal in Fakten zerlegt und
        im Gedächtnis abgelegt.
      </p>

      {documents.length === 0 ? (
        <EmptyState
          icon={<FileText className="w-8 h-8" />}
          title="Keine Dokumente importiert"
          description="Dateien lassen sich hier oder direkt im Chat über „Datei importieren“ hinzufügen."
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {documents.map((d) => (
            <li key={d.id} className="panel p-3 flex items-start gap-3" data-testid="document-row">
              <FileText className="w-4 h-4 text-accent-cyan mt-0.5 flex-shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium truncate">{d.name}</span>
                  <Badge tone={STATUS_TONE[d.status]}>{DOCUMENT_STATUS_LABEL[d.status]}</Badge>
                </div>
                <div className="text-[11px] text-text-secondary mt-0.5 flex flex-wrap gap-x-3">
                  <span>{formatBytes(d.size_bytes)}</span>
                  <span>{formatNumber(d.text_chars)} Zeichen</span>
                  <span>{formatNumber(d.facts_count)} Fakten</span>
                  <span>{formatDateTime(d.created_at)}</span>
                  {conversationTitle(d.conversation_id) && <span>Gespräch: {conversationTitle(d.conversation_id)}</span>}
                </div>
                {d.status === "indexing" && d.progress.total > 0 && (
                  <ProgressBar value={(d.progress.done / d.progress.total) * 100} className="mt-2" />
                )}
                {d.warnings?.map((w) => (
                  <div key={w.code} className="mt-1.5 text-[11px] text-amber-200">
                    {documentWarningText(w)}
                  </div>
                ))}
              </div>
              <IconButton label="Dokument löschen" danger onClick={() => setDeleteTarget(d)}>
                <Trash2 className="w-3.5 h-3.5" />
              </IconButton>
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Dokument löschen?"
        description={`„${deleteTarget?.name ?? ""}“ und seine Textabschnitte werden entfernt. Bereits gewonnene Fakten bleiben im Gedächtnis (Herkunft wird als gelöscht markiert).`}
        confirmLabel="Löschen"
        cancelLabel="Abbrechen"
        variant="destructive"
        onConfirm={() => {
          const target = deleteTarget;
          setDeleteTarget(null);
          if (target) void deleteDocument(target.id);
        }}
      />
    </div>
  );
}
