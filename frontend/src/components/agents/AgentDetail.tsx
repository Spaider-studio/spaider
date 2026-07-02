"use client";

import { useRef, useState } from "react";
import {
  Download,
  Upload,
  Loader2,
  BrainCircuit,
  Copy,
  CheckCircle2,
} from "lucide-react";
import { importAgentGraph } from "@/lib/api";
import type { Agent, AgentImportResponse } from "@/lib/types";
import { useToast } from "@/components/shared/Toast";
import HibernationCadence from "@/components/shared/HibernationCadence";
import AgentCard from "./AgentCard";

// ---------------------------------------------------------------------------
// Direct backend URL — bypasses the Next.js proxy for large streaming
// transfers (export) and large file uploads (import).
// ---------------------------------------------------------------------------
const BACKEND_URL =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000/api/v1")
    : "http://localhost:8000/api/v1";

// ---------------------------------------------------------------------------
// ImportResultBanner
// Shown inline below the controls after a successful import.
// Displays counts + the new API key (with a one-click copy button).
// ---------------------------------------------------------------------------

interface ImportResultBannerProps {
  result: AgentImportResponse;
  onDismiss: () => void;
}

function ImportResultBanner({ result, onDismiss }: ImportResultBannerProps) {
  const [keyCopied, setKeyCopied] = useState(false);

  function copyKey() {
    navigator.clipboard.writeText(result.new_api_key).then(() => {
      setKeyCopied(true);
      setTimeout(() => setKeyCopied(false), 2500);
    });
  }

  return (
    <div className="mt-2 rounded-lg border border-emerald-500/25 bg-emerald-500/8 px-4 py-3 text-xs">
      {/* Summary row */}
      <div className="flex items-center justify-between gap-4 mb-2.5">
        <span className="text-emerald-400 font-medium flex items-center gap-1.5">
          <CheckCircle2 className="w-3.5 h-3.5" />
          Import complete
        </span>
        <button
          onClick={onDismiss}
          className="text-[#6B7280] hover:text-[#A1A1AA] transition-colors text-[10px]"
        >
          dismiss
        </button>
      </div>

      <div className="flex items-center gap-4 mb-3 text-[#A1A1AA]">
        <span>
          <span className="text-[#E4E4E7] font-semibold">
            {result.nodes_restored.toLocaleString()}
          </span>{" "}
          nodes
        </span>
        <span>
          <span className="text-[#E4E4E7] font-semibold">
            {result.edges_restored.toLocaleString()}
          </span>{" "}
          edges
        </span>
        {result.skipped > 0 && (
          <span className="text-[#6B7280]">{result.skipped} skipped</span>
        )}
      </div>

      {/* New API key — show once */}
      <div className="rounded-md border border-amber-500/20 bg-amber-500/8 px-3 py-2">
        <p className="text-amber-400 text-[10px] font-medium mb-1.5 flex items-center gap-1">
          New API key — copy now, not shown again
        </p>
        <div className="flex items-center gap-2">
          <code className="flex-1 text-[11px] font-mono text-[#E4E4E7] truncate">
            {result.new_api_key}
          </code>
          <button
            onClick={copyKey}
            className="flex-shrink-0 p-1 text-[#6B7280] hover:text-[#A1A1AA] transition-colors"
            title="Copy new API key"
          >
            {keyCopied ? (
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
            ) : (
              <Copy className="w-3.5 h-3.5" />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AgentDetail
// Wraps AgentCard and adds a hibernation controls strip below it.
// ---------------------------------------------------------------------------

interface Props {
  agent: Agent;
  onDelete: () => void;
  onRotate: () => void;
}

export default function AgentDetail({ agent, onDelete, onRotate }: Props) {
  const { toast } = useToast();

  // ── Export state ──────────────────────────────────────────────────────────
  const [includeEmbeddings, setIncludeEmbeddings] = useState(true);

  // ── Import state ──────────────────────────────────────────────────────────
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<AgentImportResponse | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Export: trigger a direct browser download ─────────────────────────────
  // Uses an <a> tag rather than fetch() so the browser streams the response
  // directly to disk — no memory allocation in JS regardless of graph size.
  // Content-Disposition: attachment on the backend triggers the Save dialog.
  function handleExport() {
    const params = new URLSearchParams({
      include_embeddings: String(includeEmbeddings),
    });
    const url = `${BACKEND_URL}/agents/${agent.id}/export?${params}`;
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `spaider_agent_${agent.id}.ndjson`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  }

  // ── Import: parse file + POST with progress states ────────────────────────
  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Reset input immediately so the same file can be re-selected on retry
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (!file) return;

    setImporting(true);
    setImportResult(null);

    try {
      const result = await importAgentGraph(file, agent.id);
      setImportResult(result);

      // new_api_key is empty when importing into an existing target agent
      // (the caller already owns its credential). Only auto-copy when set.
      if (result.new_api_key) {
        navigator.clipboard.writeText(result.new_api_key).catch(() => {});
      }

      toast(
        `Import complete — ${result.nodes_restored.toLocaleString()} nodes, ` +
          `${result.edges_restored.toLocaleString()} edges restored.`,
        "success"
      );
    } catch (err) {
      toast(
        err instanceof Error ? err.message : "Import failed — check file format.",
        "error"
      );
    } finally {
      setImporting(false);
    }
  }

  const busy = importing;

  return (
    <div className="flex flex-col">
      {/* Existing agent card — unchanged */}
      <AgentCard agent={agent} onDelete={onDelete} onRotate={onRotate} />

      {/* ── Hibernation controls strip ──────────────────────────────────── */}
      <div className="bg-[#0E0E17] border border-t-0 border-[#2A2A35] rounded-b-xl px-5 py-3">
        <div className="flex items-center justify-between gap-4 flex-wrap">

          {/* Left: section label */}
          <div className="flex items-center gap-1.5 text-[#6B7280]">
            <BrainCircuit className="w-3.5 h-3.5" />
            <span className="text-[11px] font-medium tracking-wide uppercase">
              Hibernation
            </span>
          </div>

          {/* Right: controls */}
          <div className="flex items-center gap-3 flex-wrap">

            {/* Autonomous consolidation cadence + manual trigger */}
            <HibernationCadence agentId={agent.id} />

            {/* Divider */}
            <span className="w-px h-4 bg-[#2A2A35]" />

            {/* Include-embeddings checkbox */}
            <label className="flex items-center gap-1.5 cursor-pointer select-none group">
              <div className="relative">
                <input
                  type="checkbox"
                  checked={includeEmbeddings}
                  onChange={(e) => setIncludeEmbeddings(e.target.checked)}
                  disabled={busy}
                  className="sr-only peer"
                />
                {/* Custom checkbox */}
                <div className="w-3.5 h-3.5 rounded border border-[#3A3A45] bg-[#12121A] peer-checked:bg-[#3B82F6] peer-checked:border-[#3B82F6] transition-colors flex items-center justify-center">
                  {includeEmbeddings && (
                    <svg
                      viewBox="0 0 10 10"
                      className="w-2 h-2 text-white"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path d="M1.5 5l2.5 2.5 4.5-4.5" />
                    </svg>
                  )}
                </div>
              </div>
              <span className="text-[11px] text-[#6B7280] group-hover:text-[#A1A1AA] transition-colors">
                Include embeddings
              </span>
            </label>

            {/* Export Brain button */}
            <button
              onClick={handleExport}
              disabled={busy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium
                bg-[#1A1A25] border border-[#2A2A35] text-[#A1A1AA]
                hover:text-[#E4E4E7] hover:border-[#3A3A45]
                disabled:opacity-40 disabled:cursor-not-allowed
                transition-all"
              title={`Download ${agent.name}'s knowledge graph as NDJSON`}
            >
              <Download className="w-3 h-3" />
              Export Brain
            </button>

            {/* Import Brain button */}
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={busy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium
                bg-[#1A1A25] border border-[#2A2A35] text-[#A1A1AA]
                hover:text-[#E4E4E7] hover:border-[#3A3A45]
                disabled:opacity-40 disabled:cursor-not-allowed
                transition-all"
              title="Restore knowledge graph from a .ndjson export file"
            >
              {importing ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Upload className="w-3 h-3" />
              )}
              {importing ? "Importing…" : "Import Brain"}
            </button>

            {/* Hidden file input */}
            <input
              ref={fileInputRef}
              type="file"
              accept=".ndjson,application/x-ndjson"
              className="hidden"
              onChange={handleFileChange}
            />
          </div>
        </div>

        {/* Import result banner — shown inline after a successful import */}
        {importResult && (
          <ImportResultBanner
            result={importResult}
            onDismiss={() => setImportResult(null)}
          />
        )}
      </div>
    </div>
  );
}
