"use client";

import { useState } from "react";
import { swarmIntelligenceQuery } from "@/lib/api";
import { useGraph } from "@/hooks/useGraph";
import { Zap, Bot, Loader2, AlertCircle, Sparkles, X } from "lucide-react";
import { cn } from "@/lib/utils";

const SWARM_CLEAR_DELAY_MS = 14_000;

export default function SwarmDashboard() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{
    answer: string;
    source_node_ids: string[];
    agents_involved: string[];
  } | null>(null);
  const [highlightActive, setHighlightActive] = useState(false);

  // Use existing Zustand highlight mechanism — no prop drilling, canvas untouched
  const { highlightNodes, clearHighlights } = useGraph();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim() || loading) return;

    setLoading(true);
    setError(null);
    setResult(null);
    clearHighlights();
    setHighlightActive(false);

    try {
      const res = await swarmIntelligenceQuery(query.trim());
      setResult(res);

      if (res.source_node_ids.length > 0) {
        highlightNodes(res.source_node_ids);
        setHighlightActive(true);
        setTimeout(() => {
          clearHighlights();
          setHighlightActive(false);
        }, SWARM_CLEAR_DELAY_MS);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Swarm query failed");
    } finally {
      setLoading(false);
    }
  }

  function handleClear() {
    clearHighlights();
    setHighlightActive(false);
  }

  return (
    <div className="flex flex-col gap-3">
      {/* ── Swarm Mode Header ──────────────────────────────────────────── */}
      <div
        className="flex items-center gap-2 px-3 py-2 rounded-lg border"
        style={{
          background:
            "linear-gradient(135deg, rgba(6,182,212,0.12) 0%, rgba(16,185,129,0.08) 100%)",
          borderColor: "rgba(6,182,212,0.35)",
          boxShadow:
            "0 0 16px rgba(6,182,212,0.15), inset 0 0 12px rgba(6,182,212,0.05)",
        }}
      >
        <Zap
          className="w-3.5 h-3.5 flex-shrink-0"
          style={{ color: "#06B6D4", filter: "drop-shadow(0 0 4px #06B6D4)" }}
        />
        <span
          className="text-xs font-bold tracking-widest uppercase"
          style={{ color: "#06B6D4", textShadow: "0 0 8px rgba(6,182,212,0.6)" }}
        >
          Swarm Intelligence
        </span>
        {highlightActive && (
          <div className="ml-auto flex items-center gap-1.5">
            <span className="text-[10px] font-medium text-amber-400">
              {result?.source_node_ids.length} nodes lit
            </span>
            <button
              onClick={handleClear}
              className="text-white/30 hover:text-white/70 transition-colors"
              title="Clear spotlight"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )}
      </div>

      {/* ── Query Input ────────────────────────────────────────────────── */}
      <form onSubmit={handleSubmit} className="flex flex-col gap-2">
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit(e);
          }}
          placeholder="Ask the entire multiverse…"
          rows={3}
          className={cn(
            "w-full resize-none rounded-lg px-3 py-2 text-xs text-white/80",
            "placeholder:text-white/25 bg-white/5 border border-white/10",
            "focus:outline-none focus:border-cyan-500/50 focus:ring-1 focus:ring-cyan-500/20",
            "transition-colors"
          )}
        />
        <button
          type="submit"
          disabled={loading || !query.trim()}
          className={cn(
            "flex items-center justify-center gap-2 w-full py-2 rounded-lg",
            "text-xs font-semibold uppercase tracking-wider transition-all",
            "disabled:opacity-40 disabled:cursor-not-allowed"
          )}
          style={{
            background: loading
              ? "rgba(6,182,212,0.15)"
              : "linear-gradient(135deg, rgba(6,182,212,0.3), rgba(16,185,129,0.25))",
            border: "1px solid rgba(6,182,212,0.4)",
            color: "#06B6D4",
            boxShadow: loading ? "none" : "0 0 12px rgba(6,182,212,0.2)",
          }}
        >
          {loading ? (
            <>
              <Loader2 className="w-3 h-3 animate-spin" />
              Querying swarm…
            </>
          ) : (
            <>
              <Sparkles className="w-3 h-3" />
              Ask the Swarm
            </>
          )}
        </button>
      </form>

      {/* ── Error ──────────────────────────────────────────────────────── */}
      {error && (
        <div className="flex items-start gap-2 p-2.5 rounded-lg bg-red-500/10 border border-red-500/25 text-xs text-red-400">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* ── Result ─────────────────────────────────────────────────────── */}
      {result && (
        <div
          className="flex flex-col gap-3 rounded-lg p-3"
          style={{
            background: "rgba(6,182,212,0.04)",
            border: "1px solid rgba(6,182,212,0.18)",
          }}
        >
          {result.agents_involved.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {result.agents_involved.map((agentId) => (
                <span
                  key={agentId}
                  className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium"
                  style={{
                    background: "rgba(6,182,212,0.12)",
                    border: "1px solid rgba(6,182,212,0.3)",
                    color: "#67E8F9",
                  }}
                >
                  <Bot className="w-2.5 h-2.5" />
                  {agentId.slice(0, 8)}…
                </span>
              ))}
              <span className="flex items-center px-2 py-0.5 text-[10px] text-white/30">
                {result.source_node_ids.length} source nodes
              </span>
            </div>
          )}
          <p className="text-xs text-white/80 leading-relaxed whitespace-pre-wrap">
            {result.answer}
          </p>
          {highlightActive && (
            <p className="text-[10px] text-amber-400">
              ⚡ Source nodes highlighted in the graph
            </p>
          )}
        </div>
      )}
    </div>
  );
}
