"use client";

import { useState, useRef, useEffect, useCallback, DragEvent } from "react";
import {
  Send, Upload, Link, Loader2, CheckCircle2, AlertCircle,
  X, FileText, Activity,
} from "lucide-react";
import { useIngest } from "@/hooks/useIngest";
import { getConnectorStatus, type ConnectorStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Accepted file extensions + MIME types for the drop-zone validation
// ---------------------------------------------------------------------------

const ACCEPTED_EXTS = new Set([".pdf", ".docx", ".pptx", ".html", ".htm", ".md", ".txt"]);
const ACCEPTED_MIME = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "text/html",
  "text/markdown",
  "text/plain",
]);

function isAccepted(file: File): boolean {
  const ext = "." + file.name.split(".").pop()?.toLowerCase();
  return ACCEPTED_EXTS.has(ext) || ACCEPTED_MIME.has(file.type);
}

function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtRelative(iso: string | null): string {
  if (!iso) return "never";
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Tab = "text" | "files" | "url";

interface Props {
  agentId: string | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function IngestPanel({ agentId }: Props) {
  const isMultiverse = agentId === null;

  // ── Ingest hook ────────────────────────────────────────────────────────────
  const {
    ingestText, ingestFiles, ingestUrl,
    loading, error, result, status, statusMessage, nodesAdded, edgesAdded,
  } = useIngest();

  // ── Tab state ──────────────────────────────────────────────────────────────
  const [tab, setTab] = useState<Tab>("text");

  // ── Text tab ───────────────────────────────────────────────────────────────
  const [text, setText] = useState("");
  const [source, setSource] = useState("");

  // ── Files tab ──────────────────────────────────────────────────────────────
  const [stagedFiles, setStagedFiles] = useState<File[]>([]);
  const [fileDragOver, setFileDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── URL tab ────────────────────────────────────────────────────────────────
  const [urlInput, setUrlInput] = useState("");

  // ── Connector status (polling) ─────────────────────────────────────────────
  const [connStatuses, setConnStatuses] = useState<Record<string, ConnectorStatus>>({});

  const pollConnectors = useCallback(async () => {
    try {
      const [upload, url] = await Promise.allSettled([
        getConnectorStatus("upload"),
        getConnectorStatus("url"),
      ]);
      setConnStatuses({
        upload: upload.status === "fulfilled" ? upload.value : { connector_id: "upload", status: "idle", last_run_at: null, records_processed: 0, last_error: null },
        url:    url.status    === "fulfilled" ? url.value    : { connector_id: "url",    status: "idle", last_run_at: null, records_processed: 0, last_error: null },
      });
    } catch {
      // non-fatal — observability only
    }
  }, []);

  useEffect(() => {
    if (isMultiverse) return;
    pollConnectors();
    const id = setInterval(pollConnectors, 5000);
    return () => clearInterval(id);
  }, [isMultiverse, pollConnectors]);

  // Re-poll immediately after a run completes to show fresh stats.
  useEffect(() => {
    if (status === "done" || status === "error") pollConnectors();
  }, [status, pollConnectors]);

  // ── Handlers ───────────────────────────────────────────────────────────────

  async function handleTextIngest() {
    if (!text.trim() || isMultiverse) return;
    await ingestText(text, source || undefined);
    setText("");
    setSource("");
  }

  function addFiles(incoming: FileList | null) {
    if (!incoming) return;
    const valid = Array.from(incoming).filter(isAccepted);
    setStagedFiles((prev) => {
      const existing = new Set(prev.map((f) => f.name + f.size));
      return [...prev, ...valid.filter((f) => !existing.has(f.name + f.size))];
    });
  }

  function handleFileDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setFileDragOver(false);
    addFiles(e.dataTransfer.files);
  }

  async function handleFilesIngest() {
    if (!stagedFiles.length || isMultiverse) return;
    await ingestFiles(stagedFiles);
    setStagedFiles([]);
  }

  async function handleUrlIngest() {
    if (!urlInput.trim() || isMultiverse) return;
    await ingestUrl(urlInput);
    setUrlInput("");
  }

  // ── Shared status section ──────────────────────────────────────────────────

  const statusSection = (
    <>
      {loading && statusMessage && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2 text-xs text-violet-300/80">
            <Loader2 className="w-3 h-3 animate-spin flex-shrink-0" />
            <span className="truncate">{statusMessage}</span>
          </div>
          {(nodesAdded > 0 || edgesAdded > 0) && (
            <div className="flex items-center gap-3 pl-5 text-xs text-white/40">
              <span><span className="text-blue-400 font-mono">{nodesAdded}</span> nodes</span>
              <span><span className="text-emerald-400 font-mono">{edgesAdded}</span> edges</span>
            </div>
          )}
        </div>
      )}
      {error && (
        <div className="flex items-start gap-2 bg-red-500/10 border border-red-500/20 rounded-lg p-3 text-xs text-red-300">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />{error}
        </div>
      )}
      {result && !error && status === "done" && (
        <div className="flex items-start gap-2 bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-xs text-emerald-300">
          <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span><strong>{result.nodes_created}</strong> nodes · <strong>{result.edges_created}</strong> edges added</span>
        </div>
      )}
    </>
  );

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-xs font-semibold text-white/40 uppercase tracking-wider">
        Ingest Knowledge
      </h3>

      {/* Multiverse warning */}
      {isMultiverse && (
        <div className="flex items-start gap-2 bg-amber-500/10 border border-amber-500/25 rounded-lg p-3 text-xs text-amber-300">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          Select a specific agent to ingest knowledge. You cannot write into the Multiverse.
        </div>
      )}

      {/* ── Tab bar ── */}
      <div className={cn(
        "flex rounded-lg bg-white/5 border border-white/8 p-0.5 gap-0.5",
        isMultiverse && "opacity-40 pointer-events-none",
      )}>
        {(["text", "files", "url"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "flex-1 flex items-center justify-center gap-1.5 py-1 rounded-md text-xs font-medium transition-all",
              tab === t
                ? "bg-violet-600/70 text-white shadow-sm"
                : "text-white/40 hover:text-white/70",
            )}
          >
            {t === "text"  && <Send   className="w-3 h-3" />}
            {t === "files" && <Upload className="w-3 h-3" />}
            {t === "url"   && <Link   className="w-3 h-3" />}
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {/* ── Text tab ── */}
      {tab === "text" && (
        <div className={cn("flex flex-col gap-2", isMultiverse && "opacity-40 pointer-events-none")}>
          <div
            className={cn(
              "relative rounded-lg border transition-colors border-white/10"
            )}
          >
            <textarea
              className="w-full bg-transparent px-3 pt-3 pb-2 text-sm text-white/80 placeholder-white/20 resize-none focus:outline-none font-mono min-h-[110px]"
              placeholder="Paste text, articles, conversation logs…"
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={loading || isMultiverse}
            />
          </div>
          <input
            className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white/80 placeholder-white/20 focus:outline-none focus:border-violet-500/50 transition-colors"
            placeholder="Source label (optional)"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            disabled={loading || isMultiverse}
          />
          <button
            onClick={handleTextIngest}
            disabled={loading || !text.trim() || isMultiverse}
            className="flex items-center justify-center gap-2 bg-violet-600/80 hover:bg-violet-500/80 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg px-4 py-2 transition-colors border border-violet-500/30"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            Ingest
          </button>
        </div>
      )}

      {/* ── Files tab ── */}
      {tab === "files" && (
        <div className={cn("flex flex-col gap-2", isMultiverse && "opacity-40 pointer-events-none")}>
          {/* Drop zone */}
          <div
            className={cn(
              "relative rounded-lg border-2 border-dashed transition-all cursor-pointer",
              fileDragOver
                ? "border-violet-500/70 bg-violet-500/8"
                : "border-white/15 hover:border-white/25",
            )}
            onDragOver={(e) => { e.preventDefault(); setFileDragOver(true); }}
            onDragLeave={() => setFileDragOver(false)}
            onDrop={handleFileDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <div className="flex flex-col items-center gap-1.5 py-4 px-3 text-center pointer-events-none">
              <Upload className={cn("w-5 h-5", fileDragOver ? "text-violet-400" : "text-white/25")} />
              <p className="text-xs text-white/40 leading-tight">
                {fileDragOver
                  ? "Drop files here"
                  : "Drop files or click to browse"}
              </p>
              <p className="text-[10px] text-white/20">
                PDF · DOCX · PPTX · HTML · MD · TXT
              </p>
            </div>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.pptx,.html,.htm,.md,.txt,text/plain,text/html,text/markdown,application/pdf"
            className="hidden"
            onChange={(e) => addFiles(e.target.files)}
          />

          {/* Staged file list */}
          {stagedFiles.length > 0 && (
            <ul className="flex flex-col gap-1 max-h-28 overflow-y-auto pr-0.5">
              {stagedFiles.map((f, i) => (
                <li key={f.name + i} className="flex items-center gap-2 bg-white/5 rounded-md px-2 py-1">
                  <FileText className="w-3 h-3 text-white/30 flex-shrink-0" />
                  <span className="text-xs text-white/70 truncate flex-1 min-w-0">{f.name}</span>
                  <span className="text-[10px] text-white/25 flex-shrink-0">{fmtBytes(f.size)}</span>
                  <button
                    onClick={(e) => { e.stopPropagation(); setStagedFiles((p) => p.filter((_, j) => j !== i)); }}
                    className="text-white/25 hover:text-red-400 transition-colors flex-shrink-0"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </li>
              ))}
            </ul>
          )}

          <button
            onClick={handleFilesIngest}
            disabled={loading || !stagedFiles.length || isMultiverse}
            className="flex items-center justify-center gap-2 bg-violet-600/80 hover:bg-violet-500/80 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg px-4 py-2 transition-colors border border-violet-500/30"
          >
            {loading
              ? <Loader2 className="w-4 h-4 animate-spin" />
              : <Upload className="w-4 h-4" />}
            {stagedFiles.length > 0
              ? `Upload ${stagedFiles.length} file${stagedFiles.length !== 1 ? "s" : ""}`
              : "Upload Files"}
          </button>
        </div>
      )}

      {/* ── URL tab ── */}
      {tab === "url" && (
        <div className={cn("flex flex-col gap-2", isMultiverse && "opacity-40 pointer-events-none")}>
          <textarea
            className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white/70 placeholder-white/20 focus:outline-none focus:border-violet-500/50 resize-none font-mono min-h-[80px] transition-colors"
            placeholder={"https://example.com/article\nhttps://docs.site.com/page"}
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            disabled={loading || isMultiverse}
          />
          <p className="text-[10px] text-white/20 -mt-1">
            One URL per line · incremental sync via ETag
          </p>
          <button
            onClick={handleUrlIngest}
            disabled={loading || !urlInput.trim() || isMultiverse}
            className="flex items-center justify-center gap-2 bg-violet-600/80 hover:bg-violet-500/80 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg px-4 py-2 transition-colors border border-violet-500/30"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Link className="w-4 h-4" />}
            Fetch &amp; Ingest
          </button>
        </div>
      )}

      {/* ── Shared status / error / result ── */}
      {statusSection}

      {/* ── Connector observability ── */}
      {!isMultiverse && Object.keys(connStatuses).length > 0 && (
        <div className="border-t border-white/5 pt-3 flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5 text-[10px] text-white/25 uppercase tracking-wider mb-0.5">
            <Activity className="w-3 h-3" />
            Connector Status
          </div>
          {(["upload", "url"] as const).map((cid) => {
            const s = connStatuses[cid];
            if (!s) return null;
            const dot =
              s.status === "done"  ? "bg-emerald-400" :
              s.status === "error" ? "bg-red-400"     : "bg-white/20";
            return (
              <div key={cid} className="flex items-center gap-2 text-[11px]">
                <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", dot)} />
                <span className="text-white/40 w-10 flex-shrink-0 font-mono">{cid}</span>
                {s.status === "idle" ? (
                  <span className="text-white/20">never run</span>
                ) : (
                  <>
                    <span className="text-white/50">
                      {s.records_processed} record{s.records_processed !== 1 ? "s" : ""}
                    </span>
                    <span className="text-white/20 ml-auto flex-shrink-0">
                      {fmtRelative(s.last_run_at)}
                    </span>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
