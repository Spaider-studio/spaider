"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { RefreshCw, Maximize2, X, Info, Zap } from "lucide-react";
import MultiverseCanvas, {
  type MultiverseCanvasHandle,
  type SwarmFocus,
} from "./MultiverseCanvas";
import AgentSidebar, { type AgentCluster } from "./AgentSidebar";
import SwarmDashboard from "./SwarmDashboard";
import { getMultiverseGraph } from "@/lib/api";
import { useGraph } from "@/hooks/useGraph";
import type { GraphNode, GraphEdge } from "@/lib/types";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Agent colour palette — must match MultiverseCanvas
// ---------------------------------------------------------------------------

const AGENT_PALETTE = [
  "#8B5CF6", "#EC4899", "#06B6D4", "#10B981",
  "#F59E0B", "#EF4444", "#3B82F6", "#84CC16",
  "#F97316", "#A78BFA",
];

function agentColor(agentId: string | undefined | null): string {
  if (!agentId) return "#6B7280";
  let hash = 0;
  for (let i = 0; i < agentId.length; i++) {
    hash = (Math.imul(31, hash) + agentId.charCodeAt(i)) | 0;
  }
  return AGENT_PALETTE[Math.abs(hash) % AGENT_PALETTE.length];
}

// ---------------------------------------------------------------------------
// NeuralMultiverse — parent component
// ---------------------------------------------------------------------------

export default function NeuralMultiverse() {
  const canvasRef = useRef<MultiverseCanvasHandle>(null);

  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);

  // Single-agent micro focus
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  // Dual-agent swarm focus
  const [selectedSwarm, setSelectedSwarm] = useState<SwarmFocus | null>(null);

  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());

  // Swarm query highlights from Zustand (separate concept: RAG result nodes)
  const { highlightedIds } = useGraph();

  // ------------------------------------------------------------------
  // Fetch multiverse graph data
  // ------------------------------------------------------------------
  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await getMultiverseGraph(2000);
      setNodes(payload.nodes as GraphNode[]);
      setEdges(payload.edges as GraphEdge[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load graph");
    } finally {
      setLoading(false);
      setLastRefresh(new Date());
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // ------------------------------------------------------------------
  // Derive agent cluster summaries
  // ------------------------------------------------------------------
  const agents: AgentCluster[] = (() => {
    const coreNodes = nodes.filter((n) => n.type === "agent_core");
    return coreNodes.map((core) => {
      const memberNodes = nodes.filter(
        (n) => n.type !== "agent_core" && n.agent_id === core.agent_id
      );
      const memberIds = new Set(memberNodes.map((n) => n.id));
      memberIds.add(core.id);
      const clusterEdges = edges.filter(
        (e) =>
          memberIds.has(e.source as string) &&
          memberIds.has(e.target as string) &&
          e.relation !== "BELONGS_TO_AGENT"
      );
      return {
        agentId: core.agent_id ?? core.id,
        name: core.label,
        nodeCount: memberNodes.length,
        edgeCount: clusterEdges.length,
        color: agentColor(core.agent_id),
      };
    });
  })();

  // ------------------------------------------------------------------
  // Selection handlers — each clears the other focus mode
  // ------------------------------------------------------------------
  function handleSelectAgent(agentId: string | null) {
    setSelectedSwarm(null);
    setSelectedAgentId(agentId);
    if (agentId) {
      setTimeout(() => canvasRef.current?.focusAgent(agentId), 80);
    } else {
      canvasRef.current?.resetCamera();
    }
  }

  function handleSelectSwarm(swarm: SwarmFocus | null) {
    setSelectedAgentId(null);
    setSelectedSwarm(swarm);
    if (swarm) {
      setTimeout(
        () => canvasRef.current?.focusSwarm(swarm.source_id, swarm.target_id),
        80
      );
    } else {
      canvasRef.current?.resetCamera();
    }
  }

  function handleNodeClick(node: GraphNode) {
    setHoveredNode((prev) => (prev?.id === node.id ? null : node));
  }

  const dataNodes = nodes.filter((n) => n.type !== "agent_core");
  const dataEdges = edges.filter((e) => e.relation !== "BELONGS_TO_AGENT");

  // Resolve swarm banner names from loaded agents
  const swarmSourceCluster = selectedSwarm
    ? agents.find((a) => a.agentId === selectedSwarm.source_id)
    : null;
  const swarmTargetCluster = selectedSwarm
    ? agents.find((a) => a.agentId === selectedSwarm.target_id)
    : null;

  return (
    <div className="relative flex w-full h-full overflow-hidden bg-[#050508]">
      {/* ------------------------------------------------------------------ */}
      {/* Left column: agent sidebar + swarm panel                            */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex flex-col h-full">
        <div className="flex-1 min-h-0 overflow-hidden">
          <AgentSidebar
            agents={agents}
            selectedAgentId={selectedAgentId}
            selectedSwarm={selectedSwarm}
            totalNodes={dataNodes.length}
            totalEdges={dataEdges.length}
            onSelectAgent={handleSelectAgent}
            onSelectSwarm={handleSelectSwarm}
          />
        </div>

        <div
          className="w-64 border-t border-white/8 bg-black/70 backdrop-blur-xl overflow-y-auto"
          style={{ maxHeight: "45vh" }}
        >
          <div className="p-3">
            <SwarmDashboard />
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Canvas area                                                          */}
      {/* ------------------------------------------------------------------ */}
      {/* min-w-0 + overflow-hidden so flex-1 can actually shrink below the
          canvas's intrinsic content width on narrow browsers. Without this
          the canvas overflows past the viewport, pushing the top-right
          toolbar (Refresh / Overview) off-screen and making the cluster
          appear off-center because clientWidth reports the overflowed size
          while only part of the canvas is visible. */}
      <div className="relative flex-1 min-w-0 h-full overflow-hidden">
        <MultiverseCanvas
          ref={canvasRef}
          nodes={nodes}
          edges={edges}
          selectedAgentId={selectedAgentId}
          swarmFocus={selectedSwarm}
          highlightedIds={highlightedIds}
          onNodeClick={handleNodeClick}
        />

        {/* Loading overlay */}
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-[#050508]/80 backdrop-blur-sm z-10">
            <div className="flex flex-col items-center gap-3">
              <div className="w-10 h-10 rounded-full border-2 border-violet-500/30 border-t-violet-500 animate-spin" />
              <p className="text-white/50 text-sm">Loading neural multiverse…</p>
            </div>
          </div>
        )}

        {/* Error banner */}
        {error && !loading && (
          <div className="absolute top-4 left-1/2 -translate-x-1/2 z-20 bg-red-500/10 border border-red-500/30 text-red-300 text-xs px-4 py-2 rounded-lg backdrop-blur-sm">
            {error}
          </div>
        )}

        {/* Top-right toolbar */}
        <div className="absolute top-4 right-4 z-20 flex items-center gap-2">
          <button
            onClick={fetchData}
            disabled={loading}
            className="flex items-center gap-1.5 bg-black/60 hover:bg-black/80 border border-white/10 text-white/60 hover:text-white/90 text-xs px-3 py-1.5 rounded-lg backdrop-blur-sm transition-colors disabled:opacity-40"
            title="Refresh graph"
          >
            <RefreshCw className={cn("w-3 h-3", loading && "animate-spin")} />
            Refresh
          </button>
          <button
            onClick={() => {
              handleSelectAgent(null);
              handleSelectSwarm(null);
            }}
            className="flex items-center gap-1.5 bg-black/60 hover:bg-black/80 border border-white/10 text-white/60 hover:text-white/90 text-xs px-3 py-1.5 rounded-lg backdrop-blur-sm transition-colors"
            title="Reset camera to overview"
          >
            <Maximize2 className="w-3 h-3" />
            Overview
          </button>
        </div>

        {/* ── Single-agent focus banner ── */}
        {selectedAgentId && (() => {
          const cluster = agents.find((a) => a.agentId === selectedAgentId);
          if (!cluster) return null;
          return (
            <div className="absolute top-4 left-1/2 -translate-x-1/2 z-20">
              <div
                className="flex items-center gap-3 bg-black/70 border rounded-xl px-4 py-2 backdrop-blur-md shadow-xl"
                style={{ borderColor: cluster.color + "44" }}
              >
                <span
                  className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                  style={{
                    backgroundColor: cluster.color,
                    boxShadow: `0 0 8px 2px ${cluster.color}66`,
                  }}
                />
                <div>
                  <p className="text-white/90 text-sm font-semibold leading-tight">
                    {cluster.name}
                  </p>
                  <p className="text-white/35 text-[10px]">
                    {cluster.nodeCount} nodes · {cluster.edgeCount} relations
                  </p>
                </div>
                <button
                  onClick={() => handleSelectAgent(null)}
                  className="ml-2 p-0.5 rounded hover:bg-white/10 text-white/30 hover:text-white/70 transition-colors"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          );
        })()}

        {/* ── Dual-agent swarm focus banner ── */}
        {selectedSwarm && swarmSourceCluster && swarmTargetCluster && (
          <div className="absolute top-4 left-1/2 -translate-x-1/2 z-20">
            <div className="flex items-center gap-3 bg-black/70 border border-cyan-400/25 rounded-xl px-4 py-2 backdrop-blur-md shadow-xl shadow-cyan-900/20">
              <Zap className="w-3.5 h-3.5 text-cyan-400 flex-shrink-0" />
              <div className="flex items-center gap-2">
                <span
                  className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{
                    backgroundColor: swarmSourceCluster.color,
                    boxShadow: `0 0 6px 2px ${swarmSourceCluster.color}66`,
                  }}
                />
                <p className="text-white/90 text-sm font-semibold">
                  {swarmSourceCluster.name}
                </p>
                <span className="text-cyan-400/70 text-xs font-bold">➔</span>
                <span
                  className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{
                    backgroundColor: swarmTargetCluster.color,
                    boxShadow: `0 0 6px 2px ${swarmTargetCluster.color}66`,
                  }}
                />
                <p className="text-white/90 text-sm font-semibold">
                  {swarmTargetCluster.name}
                </p>
              </div>
              <p className="text-cyan-400/50 text-[10px] ml-1">Swarm Focus</p>
              <button
                onClick={() => handleSelectSwarm(null)}
                className="ml-1 p-0.5 rounded hover:bg-white/10 text-white/30 hover:text-white/70 transition-colors"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}

        {/* Node detail tooltip */}
        {hoveredNode && (
          <div className="absolute bottom-6 right-6 z-20 w-72 bg-black/80 border border-white/10 rounded-xl p-4 backdrop-blur-md shadow-2xl">
            <div className="flex items-start justify-between gap-2 mb-2">
              <div className="flex items-center gap-2">
                <span
                  className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{ backgroundColor: agentColor(hoveredNode.agent_id) }}
                />
                <p className="text-white/90 font-semibold text-sm leading-tight truncate">
                  {hoveredNode.label}
                </p>
              </div>
              <button
                onClick={() => setHoveredNode(null)}
                className="p-0.5 text-white/30 hover:text-white/70 transition-colors flex-shrink-0"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
            <p className="text-[10px] text-violet-400 font-mono uppercase mb-2">
              {hoveredNode.type}
            </p>
            {!!hoveredNode.properties?.description && (
              <p className="text-white/50 text-xs leading-relaxed line-clamp-3">
                {String(hoveredNode.properties.description)}
              </p>
            )}
            <div className="mt-2 pt-2 border-t border-white/8 flex items-center gap-1.5">
              <Info className="w-3 h-3 text-white/20 flex-shrink-0" />
              <p className="text-white/25 text-[10px] truncate">
                Agent: {hoveredNode.agent_id ?? "—"}
              </p>
            </div>
          </div>
        )}

        {/* Bottom-left: status */}
        <div className="absolute bottom-3 left-3 z-10 text-white/15 text-[10px] font-mono pointer-events-none">
          Neural Multiverse · {nodes.length} nodes · {edges.length} edges · refreshed{" "}
          {lastRefresh.toLocaleTimeString()}
        </div>
      </div>
    </div>
  );
}
