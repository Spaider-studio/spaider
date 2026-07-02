"use client";

import { useState, useRef } from "react";
import { Search, AlertCircle, X, Zap, ThumbsUp, ThumbsDown, Brain } from "lucide-react";
import { useGraph } from "@/hooks/useGraph";
import { useMemoryMode } from "@/context/MemoryModeContext";
import { queryNL } from "@/lib/api";
import type { GraphNode, GraphEdge } from "@/lib/types";

const BASE_URL = "/api/v1";

function avgStrength(nodeId: string, edges: GraphEdge[]): number {
  const weights = edges
    .filter((e) => e.source === nodeId || e.target === nodeId)
    .map((e) => e.utility_weight ?? 1.0);
  if (weights.length === 0) return 1.0;
  return weights.reduce((a, b) => a + b, 0) / weights.length;
}

function StrengthBar({ value }: { value: number }) {
  const pct = Math.min(100, ((value - 0.1) / (2.0 - 0.1)) * 100);
  const color =
    value >= 1.5 ? "#e879f9" : value >= 1.0 ? "#a78bfa" : "#6b7280";
  return (
    <div className="flex items-center gap-1.5 flex-1 min-w-0">
      <div className="flex-1 h-1 bg-[#1a1a2e] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-[10px] font-mono shrink-0" style={{ color }}>
        {value.toFixed(2)}
      </span>
    </div>
  );
}

interface V2Result {
  nodes: GraphNode[];
  edges: GraphEdge[];
  nodeIds: string[];
}

const TOP_K_STEPS = [
  { value: 8,  label: "Small",  hint: "< 1k nodes" },
  { value: 20, label: "Medium", hint: "1k – 50k"   },
  { value: 50, label: "Large",  hint: "50k+ nodes"  },
] as const;

export default function QueryPanel() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [fromCache, setFromCache] = useState(false);
  const [v2Result, setV2Result] = useState<V2Result | null>(null);
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);
  const [feedbackSent, setFeedbackSent] = useState(false);
  const [topKIndex, setTopKIndex] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const { agentId, clearHighlights, highlightedIds } = useGraph();
  const { memoryMode } = useMemoryMode();
  const isV2 = memoryMode === "on";

  async function handleQuery() {
    if (!question.trim() || loading) return;

    setLoading(true);
    setError(null);
    setAnswer("");
    setFromCache(false);
    setV2Result(null);
    setFeedback(null);
    setFeedbackSent(false);

    const topK = TOP_K_STEPS[topKIndex].value;

    if (isV2) {
      // V2: full endpoint so we get subgraph + utility_weight for strength display
      try {
        const res = await queryNL(question, agentId ?? "", topK);
        setAnswer(res.answer);
        setV2Result({
          nodes: res.subgraph.nodes,
          edges: res.subgraph.edges,
          nodeIds: res.subgraph.nodes.map((n) => n.id),
        });
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
      return;
    }

    // V1: streaming
    abortRef.current = new AbortController();
    try {
      const res = await fetch(`${BASE_URL}/query/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, agent_id: agentId, top_k: topK }),
        signal: abortRef.current.signal,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail ?? `HTTP ${res.status}`);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No response body");
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const token = line.slice(6);
          if (token === "[DONE]") break;
          if (token.startsWith("[ERROR]")) { setError(token.slice(8)); break; }
          setAnswer((prev) => prev + token);
        }
      }
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== "AbortError") setError(e.message);
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }

  async function submitFeedback(success: boolean) {
    if (!v2Result || feedbackSent || v2Result.nodeIds.length === 0) return;
    setFeedback(success ? "up" : "down");
    try {
      await fetch(`${BASE_URL}/system/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query_id: crypto.randomUUID(),
          used_node_ids: v2Result.nodeIds,
          success,
        }),
      });
      setFeedbackSent(true);
    } catch {
      // best-effort
    }
  }

  function handleStop() {
    abortRef.current?.abort();
    setLoading(false);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleQuery(); }
  }

  const topNodes = v2Result
    ? [...v2Result.nodes]
        .map((n) => ({ ...n, strength: avgStrength(n.id, v2Result.edges) }))
        .sort((a, b) => b.strength - a.strength)
        .slice(0, 6)
    : [];

  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
        Query Graph
      </h3>

      {/* Adaptive-memory banner (agent memory_mode = on) */}
      {isV2 && (
        <div className="flex items-start gap-2 px-2.5 py-1.5 rounded-lg bg-purple-500/10 border border-purple-500/25 shadow-[0_0_10px_rgba(139,92,246,0.1)]">
          <Brain className="w-3.5 h-3.5 text-purple-400 shrink-0 mt-0.5" />
          <div className="flex flex-col min-w-0 leading-tight">
            <span className="text-xs text-purple-300 font-medium">
              Synaptic Memory · On
            </span>
            <span className="text-[10px] text-purple-400/60">
              Reinforces on use, decays on disuse
            </span>
          </div>
        </div>
      )}

      <div className="flex gap-2">
        <input
          className="flex-1 bg-[#12121A] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-accent-blue transition-colors"
          placeholder="Ask anything about the graph..."
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
        />
        {loading ? (
          <button
            onClick={handleStop}
            className="flex items-center justify-center bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 text-red-400 rounded-lg px-3 py-2 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        ) : (
          <button
            onClick={handleQuery}
            disabled={!question.trim()}
            className="flex items-center justify-center bg-[#1A1A25] hover:bg-[#2A2A35] border border-[#2A2A35] disabled:opacity-40 text-gray-300 rounded-lg px-3 py-2 transition-colors"
          >
            <Search className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Retrieval depth slider */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-gray-500 uppercase tracking-wider">Retrieval depth</span>
          <span className="text-[10px] text-gray-400 font-mono">
            {TOP_K_STEPS[topKIndex].label}
            <span className="text-gray-600 ml-1">· top_k={TOP_K_STEPS[topKIndex].value}</span>
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={TOP_K_STEPS.length - 1}
          step={1}
          value={topKIndex}
          onChange={(e) => setTopKIndex(Number(e.target.value))}
          disabled={loading}
          className="w-full h-1 rounded-full appearance-none cursor-pointer disabled:opacity-40
            bg-[#2A2A35] accent-[#60a5fa]"
        />
        <div className="flex justify-between">
          {TOP_K_STEPS.map((step, i) => (
            <button
              key={step.value}
              onClick={() => setTopKIndex(i)}
              disabled={loading}
              className={`text-[9px] transition-colors disabled:opacity-40 ${
                topKIndex === i ? "text-[#60a5fa]" : "text-gray-600 hover:text-gray-400"
              }`}
            >
              {step.label}
              <span className="block text-[8px] text-gray-700">{step.hint}</span>
            </button>
          ))}
        </div>
      </div>

      {highlightedIds.size > 0 && (
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <div className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />
          <span>{highlightedIds.size} nodes highlighted</span>
          <button
            onClick={clearHighlights}
            className="ml-auto flex items-center gap-1 text-gray-500 hover:text-gray-300 transition-colors"
          >
            <X className="w-3 h-3" /> Clear
          </button>
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 bg-red-500/10 border border-red-500/20 rounded-lg p-3 text-xs text-red-300">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          {error}
        </div>
      )}

      {(answer || loading) && (
        <div className="flex flex-col gap-2">
          {fromCache && (
            <div className="flex items-center gap-1 text-xs text-yellow-400/70">
              <Zap className="w-3 h-3" /> Cached response
            </div>
          )}

          {/* Answer box */}
          <div className="bg-[#12121A] border border-[#2A2A35] rounded-lg p-3 text-sm text-gray-200 leading-relaxed min-h-[2.5rem]">
            {answer}
            {loading && (
              <span className="inline-block w-1.5 h-4 bg-accent-blue ml-0.5 animate-pulse align-middle" />
            )}
          </div>

          {/* ── V2 Synapse Strength Panel ── */}
          {isV2 && !loading && topNodes.length > 0 && (
            <div className="flex flex-col gap-2 rounded-lg border border-purple-500/25 bg-[#0d0820] p-3 shadow-[0_0_15px_rgba(139,92,246,0.07)]">

              {/* Header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <div className="w-1.5 h-1.5 rounded-full bg-purple-500 animate-pulse" />
                  <span className="text-[10px] font-semibold text-purple-400 uppercase tracking-wider">
                    Synapse Strength
                  </span>
                </div>
                <span className="text-[10px] text-purple-400/50">
                  {v2Result?.nodes.length} nodes · threshold 0.3
                </span>
              </div>

              {/* Node strength bars */}
              <div className="flex flex-col gap-1.5">
                {topNodes.map((n) => (
                  <div key={n.id} className="flex items-center gap-2 min-w-0">
                    <span className="text-[10px] text-gray-400 truncate w-24 shrink-0">
                      {n.label}
                    </span>
                    <StrengthBar value={n.strength} />
                    {n.strength >= 1.5 && (
                      <span className="text-[9px] text-fuchsia-400 shrink-0">★</span>
                    )}
                  </div>
                ))}
              </div>

              {/* Legend */}
              <div className="flex items-center gap-3 text-[9px] text-purple-400/40 pt-0.5">
                <span className="flex items-center gap-1">
                  <span className="inline-block w-2 h-0.5 bg-[#6b7280] rounded" />
                  weak
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block w-2 h-0.5 bg-[#a78bfa] rounded" />
                  normal
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block w-2 h-0.5 bg-[#e879f9] rounded" />
                  strong ★
                </span>
              </div>

              {/* Hebbian feedback */}
              <div className="flex items-center gap-2 pt-1.5 border-t border-purple-500/15">
                <span className="text-[10px] text-purple-400/50">Hebbian update:</span>
                {feedbackSent ? (
                  <span
                    className={`text-[10px] font-medium ${
                      feedback === "up" ? "text-green-400" : "text-red-400"
                    }`}
                  >
                    {feedback === "up"
                      ? "↑ Synapses reinforced (+0.1)"
                      : "↓ Synapses weakened (−0.1)"}
                  </span>
                ) : (
                  <div className="flex gap-1.5 ml-auto">
                    <button
                      onClick={() => submitFeedback(true)}
                      className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded bg-green-500/10 hover:bg-green-500/20 border border-green-500/20 text-green-400 transition-colors"
                    >
                      <ThumbsUp className="w-2.5 h-2.5" /> Good
                    </button>
                    <button
                      onClick={() => submitFeedback(false)}
                      className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 text-red-400 transition-colors"
                    >
                      <ThumbsDown className="w-2.5 h-2.5" /> Bad
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
