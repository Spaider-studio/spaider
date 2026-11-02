/**
 * Collapsed "Denktext des Modells" above the answer. Rendered only when the
 * active model exposes a reasoning toggle and `thinking` events actually
 * arrived. It is the model's own text, never a list of actions.
 */
import { ChevronDown, ChevronRight } from "lucide-react";
import { memo, useState } from "react";

function ThinkingBlockInner({ text, streaming }: { text: string; streaming: boolean }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="mb-2 rounded-lg border border-border bg-bg-secondary/60" data-testid="thinking-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-xs text-text-secondary hover:text-text-primary transition-colors"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        Denktext des Modells
        {streaming && <span className="ml-1 text-text-secondary/60">…</span>}
      </button>
      {open && (
        <pre className="px-3 pb-3 text-xs text-text-secondary whitespace-pre-wrap break-words font-sans leading-relaxed max-h-64 overflow-y-auto">
          {text}
        </pre>
      )}
    </div>
  );
}

const ThinkingBlock = memo(ThinkingBlockInner);
export default ThinkingBlock;
