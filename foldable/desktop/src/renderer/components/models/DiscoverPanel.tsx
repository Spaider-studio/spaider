/**
 * "Alle Modelle entdecken": paginated hub search (GET /models/discover) with
 * debounced text/author filters, task filter, GGUF-only toggle and sort;
 * "Mehr laden" follows next_cursor. Offline answers show the cached page with
 * a banner. A row opens the repository detail (variants + model card).
 */
import { ChevronRight, Lock, Search, WifiOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import type { DiscoverItem } from "../../api/types";
import { formatDate, formatDateTime, formatNumber } from "../../lib/format";
import { DISCOVER_SORT_OPTIONS, DISCOVER_TASK_OPTIONS } from "../../store/hub";
import { useAppStore } from "../../store/useAppStore";
import { useHubStore } from "../../store/useHubStore";
import { Badge, Button, inputClass, Select } from "../ui";
import RepoDetailPanel from "./RepoDetailPanel";
import type { ModelJobs } from "./useModelJobs";

export const DISCOVER_DEBOUNCE_MS = 400;

function publisherTone(status: string): "green" | "neutral" {
  return /^offiziell/i.test(status) ? "green" : "neutral";
}

export function DiscoverRow({ item, active, onOpen }: { item: DiscoverItem; active: boolean; onOpen: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className={cn("w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-white/5 transition-colors", active && "bg-white/5")}
        data-testid="discover-row"
        data-repo={item.repo}
        aria-current={active ? "true" : undefined}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-mono text-text-primary truncate">{item.repo}</span>
            <Badge tone={publisherTone(item.publisher_status)} title="Herausgeber">
              {item.publisher_status}
            </Badge>
            {item.gguf ? <Badge tone="blue">GGUF</Badge> : <Badge tone="neutral">kein GGUF</Badge>}
            {item.gated && (
              <Badge tone="yellow" title={item.gated === "manual" ? "Freischaltung durch den Herausgeber nötig" : "Automatische Freischaltung nach Zustimmung"}>
                <Lock className="w-3 h-3" /> Freischaltung {item.gated === "manual" ? "manuell" : "automatisch"}
              </Badge>
            )}
            {item.private && <Badge tone="red">privat</Badge>}
          </div>
          <div className="mt-0.5 text-[11px] text-text-secondary flex items-center gap-x-2 flex-wrap tabular-nums">
            <span>{formatNumber(item.downloads)} Downloads</span>
            <span>· {formatNumber(item.likes)} Likes</span>
            {item.updated && <span>· {formatDate(item.updated)}</span>}
            {item.pipeline_tag && <span>· {item.pipeline_tag}</span>}
            {item.family_hints.length > 0 && <span className="truncate">· {item.family_hints.join(", ")}</span>}
            <span className="text-text-secondary/60">· ungeprüft</span>
          </div>
        </div>
        <ChevronRight className="w-4 h-4 text-text-secondary flex-shrink-0" />
      </button>
    </li>
  );
}

export default function DiscoverPanel({ jobs, busy }: { jobs: ModelJobs; busy: boolean }) {
  const client = useAppStore((s) => s.client);
  const discover = useHubStore((s) => s.discover);
  const repo = useHubStore((s) => s.repo);
  const setDiscoverQuery = useHubStore((s) => s.setDiscoverQuery);
  const runDiscover = useHubStore((s) => s.runDiscover);
  const openRepo = useHubStore((s) => s.openRepo);
  const [text, setText] = useState(discover.query.q);
  const [author, setAuthor] = useState(discover.query.author);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const first = useRef(true);

  // First open: load the first page once.
  useEffect(() => {
    if (client && !discover.loaded && !discover.loading) void runDiscover(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  // Text filters: debounce 400 ms; structured filters apply immediately.
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setDiscoverQuery({ q: text, author });
      void runDiscover(false);
    }, DISCOVER_DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [text, author, setDiscoverQuery, runDiscover]);

  const setFilter = (patch: Parameters<typeof setDiscoverQuery>[0]) => {
    setDiscoverQuery(patch);
    void runDiscover(false);
  };

  return (
    <div className="flex flex-col gap-3" data-testid="discover-panel">
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <label className="relative flex-1 min-w-[220px]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-secondary" />
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Modell suchen (z. B. Qwen3, GLM, Mistral)" aria-label="Modell suchen" className={cn(inputClass, "pl-8")} data-testid="discover-q" />
          </label>
          <input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="Herausgeber (z. B. Qwen)" aria-label="Herausgeber" className={cn(inputClass, "w-44")} data-testid="discover-author" />
          <Select value={discover.query.task} onChange={(v) => setFilter({ task: v as typeof discover.query.task })} options={DISCOVER_TASK_OPTIONS} ariaLabel="Aufgabe" />
          <Select value={discover.query.sort} onChange={(v) => setFilter({ sort: v as typeof discover.query.sort })} options={DISCOVER_SORT_OPTIONS} ariaLabel="Sortierung" />
          <label className="inline-flex items-center gap-2 text-xs text-text-secondary">
            <input type="checkbox" checked={discover.query.gguf_only} onChange={(e) => setFilter({ gguf_only: e.target.checked })} data-testid="discover-gguf-only" />
            nur GGUF
          </label>
        </div>
        {discover.offline && (
          <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200" data-testid="discover-offline">
            <WifiOff className="w-3.5 h-3.5 flex-shrink-0" />
            <span>
              Offline – {discover.cachedAt ? `zuletzt gespeicherte Ergebnisse vom ${formatDateTime(discover.cachedAt)}` : "keine gespeicherten Ergebnisse"}
              {discover.detail ? ` (${discover.detail})` : ""}
            </span>
          </div>
        )}
        {!discover.offline && discover.cachedAt && <div className="text-[11px] text-text-secondary/70 px-1">Stand: {formatDateTime(discover.cachedAt)} · Suchergebnisse sind ungeprüft</div>}
      </div>

      <div className={cn("grid gap-4", repo ? "xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]" : "")}>
        <div className="panel overflow-hidden self-start">
          {discover.error && <div className="px-3 py-2 text-xs text-red-300">{discover.error}</div>}
          {discover.loading ? (
            <div className="flex justify-center py-6">
              <LoadingSpinner size="sm" />
            </div>
          ) : discover.items.length === 0 ? (
            <div className="px-3 py-4 text-xs text-text-secondary">{discover.loaded ? "Keine Treffer." : "Suche …"}</div>
          ) : (
            <ul className="divide-y divide-border/60 max-h-[520px] overflow-y-auto" data-testid="discover-list">
              {discover.items.map((item) => (
                <DiscoverRow key={item.repo} item={item} active={repo?.repo === item.repo} onOpen={() => void openRepo(item.repo)} />
              ))}
            </ul>
          )}
          {discover.nextCursor && !discover.loading && (
            <div className="border-t border-border/60 p-2 flex justify-center">
              <Button size="sm" onClick={() => void runDiscover(true)} loading={discover.loadingMore} data-testid="discover-more">
                Mehr laden
              </Button>
            </div>
          )}
        </div>
        {repo && <RepoDetailPanel jobs={jobs} busy={busy} />}
      </div>
    </div>
  );
}
