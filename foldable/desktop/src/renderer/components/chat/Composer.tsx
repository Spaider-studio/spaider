import { Paperclip, SendHorizontal, Square } from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import { Button } from "../ui";

export const ACCEPTED_DOCUMENT_TYPES = ".txt,.md,.markdown,.pdf";

export default function Composer({
  disabled,
  disabledReason,
  streaming,
  statusText,
  onSend,
  onStop,
  onImport,
  importing,
}: {
  disabled: boolean;
  disabledReason: string | null;
  streaming: boolean;
  statusText: string | null;
  onSend: (text: string) => void;
  onStop: () => void;
  onImport: (file: File) => void;
  importing: boolean;
}) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  useEffect(() => {
    if (!disabled && !streaming) textareaRef.current?.focus();
  }, [disabled, streaming]);

  const canSend = !disabled && !streaming && text.trim().length > 0;
  const send = () => {
    if (!canSend) return;
    onSend(text);
    setText("");
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };
  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) onImport(file);
  };

  return (
    <div className="px-4 pb-4 pt-2">
      <div
        className={cn(
          "panel flex items-end gap-2 p-2 transition-colors focus-within:border-accent-blue/60",
          disabled && "opacity-70",
        )}
      >
        <input ref={fileRef} type="file" accept={ACCEPTED_DOCUMENT_TYPES} className="hidden" onChange={onFile} aria-hidden="true" />
        <Button
          variant="ghost"
          size="sm"
          className="h-9 px-2.5"
          onClick={() => fileRef.current?.click()}
          disabled={importing}
          loading={importing}
          icon={<Paperclip className="w-4 h-4" />}
          title="Datei importieren (.txt, .md, .pdf)"
          aria-label="Datei importieren"
        >
          <span className="hidden lg:inline">Datei importieren</span>
        </Button>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          disabled={disabled}
          rows={1}
          placeholder={disabled ? (disabledReason ?? "Nicht verfügbar") : "Nachricht an SpAIder … (Enter sendet, Shift+Enter neue Zeile)"}
          aria-label="Nachricht"
          className="flex-1 min-w-0 resize-none bg-transparent px-2 py-2 text-sm leading-relaxed placeholder:text-text-secondary/60 focus:outline-none max-h-[220px]"
        />
        {streaming ? (
          <Button variant="danger" size="sm" className="h-9" onClick={onStop} icon={<Square className="w-3.5 h-3.5" />} title="Stopp (Esc)">
            Stopp
          </Button>
        ) : (
          <Button variant="primary" size="sm" className="h-9" onClick={send} disabled={!canSend} icon={<SendHorizontal className="w-3.5 h-3.5" />}>
            Senden
          </Button>
        )}
      </div>
      <div className="h-5 mt-1.5 px-1 flex items-center gap-2 text-[11px] text-text-secondary" aria-live="polite">
        {streaming && statusText ? (
          <>
            <LoadingSpinner size="sm" className="w-3 h-3" />
            <span>{statusText}</span>
          </>
        ) : disabled && disabledReason ? (
          <span className="text-amber-300/90">{disabledReason}</span>
        ) : (
          <span className="text-text-secondary/50">Alles bleibt lokal auf diesem Datenträger.</span>
        )}
      </div>
    </div>
  );
}
