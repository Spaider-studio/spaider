/**
 * Question input of the overlay: Enter sends, Shift+Enter inserts a newline,
 * Esc bubbles up (hides the overlay). Dev helper: keystroke -> DOM update
 * latency is recorded into window.__spaiderPerf as `overlay_input_to_dom_ms`.
 */
import { SendHorizontal, Square } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils";
import { getPerf } from "../../lib/perf";
import { Button } from "../ui";

export default function OverlayInput({
  disabled,
  streaming,
  stopping,
  placeholder,
  focusKey,
  onSend,
  onStop,
}: {
  disabled: boolean;
  streaming: boolean;
  stopping: boolean;
  placeholder: string;
  /** Changes whenever the overlay is shown: re-focuses the field. */
  focusKey: number;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const keyAt = useRef<number | null>(null);

  useEffect(() => {
    ref.current?.focus();
  }, [focusKey]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 96)}px`;
  }, [text]);

  // Keystroke -> committed DOM update (measured only when the perf helper exists).
  useLayoutEffect(() => {
    const at = keyAt.current;
    if (at === null) return;
    keyAt.current = null;
    getPerf()?.addSample("overlay_input_to_dom_ms", performance.now() - at);
  }, [text]);

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
      return;
    }
    if (e.key.length === 1 || e.key === "Backspace") keyAt.current = e.timeStamp;
  };

  return (
    <div className={cn("flex items-end gap-1.5 rounded-lg border border-border bg-bg-primary/60 px-2 py-1.5 focus-within:border-accent-blue/60 overlay-no-drag", disabled && "opacity-70")}>
      <textarea
        ref={ref}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        disabled={disabled}
        rows={1}
        placeholder={placeholder}
        aria-label="Frage"
        data-testid="overlay-input"
        className="flex-1 min-w-0 resize-none bg-transparent px-1 py-1 text-sm leading-relaxed placeholder:text-text-secondary/60 focus:outline-none max-h-24"
      />
      {streaming ? (
        <Button variant="danger" size="sm" className="h-8" onClick={onStop} disabled={stopping} icon={<Square className="w-3 h-3" />} title="Stopp" data-testid="overlay-stop">
          Stopp
        </Button>
      ) : (
        <Button variant="primary" size="sm" className="h-8" onClick={send} disabled={!canSend} icon={<SendHorizontal className="w-3 h-3" />} title="Senden (Enter)" data-testid="overlay-send">
          Senden
        </Button>
      )}
    </div>
  );
}
