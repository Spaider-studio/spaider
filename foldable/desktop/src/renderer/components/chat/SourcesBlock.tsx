import { ChevronDown, ChevronRight, FileText, Lightbulb, MessageSquare, StickyNote } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { Source } from "../../api/types";
import { formatDate } from "../../lib/format";
import { SOURCE_KIND_LABEL, SOURCE_STATUS_LABEL } from "../../lib/labels";
import { Badge } from "../ui";

const KIND_ICON: Record<Source["kind"], ReactNode> = {
  fact: <Lightbulb className="w-3.5 h-3.5 text-accent-yellow" />,
  document: <FileText className="w-3.5 h-3.5 text-accent-cyan" />,
  note: <StickyNote className="w-3.5 h-3.5 text-accent-purple" />,
  message: <MessageSquare className="w-3.5 h-3.5 text-accent-blue" />,
};
const STATUS_TONE = { current: "green", superseded: "yellow", uncertain: "neutral" } as const;

export function SourceItem({ source, score }: { source: Source; score?: number }) {
  return (
    <li className="flex items-start gap-2.5 py-2 border-b border-border/60 last:border-b-0" data-testid="source-item">
      <span className="mt-0.5 flex-shrink-0" title={SOURCE_KIND_LABEL[source.kind]}>
        {KIND_ICON[source.kind]}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-medium text-text-primary truncate">{source.label}</span>
          <Badge tone={STATUS_TONE[source.status]}>{SOURCE_STATUS_LABEL[source.status]}</Badge>
          {score !== undefined && <span className="text-[10px] text-text-secondary tabular-nums">{score.toFixed(2)}</span>}
        </div>
        {source.excerpt && <p className="text-xs text-text-secondary mt-0.5 leading-relaxed line-clamp-3">{source.excerpt}</p>}
        <div className="text-[11px] text-text-secondary/70 mt-0.5 truncate">
          {source.origin.title || SOURCE_KIND_LABEL[source.kind]}
          {source.origin.at && ` · ${formatDate(source.origin.at)}`}
        </div>
        {source.superseded_by && (
          <div className="text-[11px] text-amber-200/80 mt-1">Ersetzt durch: {source.superseded_by.excerpt}</div>
        )}
      </div>
    </li>
  );
}

export default function SourcesBlock({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);
  if (sources.length === 0) return null;
  return (
    <div className="mt-2 rounded-lg border border-border bg-bg-secondary/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-xs text-text-secondary hover:text-text-primary transition-colors"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        Quellen ({sources.length})
      </button>
      {open && (
        <ul className="px-3 pb-2">
          {sources.map((s) => (
            <SourceItem key={`${s.kind}-${s.id}`} source={s} />
          ))}
        </ul>
      )}
    </div>
  );
}
