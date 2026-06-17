"use client";

import { useEffect, useMemo, useState } from "react";
import { useGraph } from "@/hooks/useGraph";
import GraphCanvas3D from "@/components/studio/GraphCanvas3D";
import GraphCanvas2D from "@/components/studio/GraphCanvas2D";
import IngestPanel from "@/components/studio/IngestPanel";
import QueryPanel from "@/components/studio/QueryPanel";
import FilterBar from "@/components/studio/FilterBar";
import GraphStats from "@/components/studio/GraphStats";
import NodeDetailPanel from "@/components/studio/NodeDetailPanel";
import SwarmDashboard from "@/components/studio/SwarmDashboard";
import LivePheromoneStream from "@/components/studio/LivePheromoneStream";
import Sidebar from "@/components/layout/Sidebar";
import { getAgents } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { Layers, RefreshCw, Trash2, Box, Grid2X2, X, ChevronDown, Globe2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { GraphCluster, GraphClusterEdge, GraphEdge, GraphNode } from "@/lib/types";
import { AUTO_CLUSTER_THRESHOLD, type GraphViewMode } from "@/hooks/useGraph";

// ---------------------------------------------------------------------------
// Deterministic agent colour (same hash as MultiverseCanvas)
// ---------------------------------------------------------------------------
const AGENT_PALETTE = [
  "#3B82F6", "#8B5CF6", "#10B981", "#F59E0B", "#EF4444",
  "#06B6D4", "#EC4899", "#F97316", "#84CC16", "#6366F1",
];
function agentColor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (Math.imul(31, h) + id.charCodeAt(i)) | 0;
  return AGENT_PALETTE[Math.abs(h) % AGENT_PALETTE.length];
}

// ---------------------------------------------------------------------------
// AgentDropdown
// ---------------------------------------------------------------------------
function AgentDropdown({
  agents,
  agentId,
  onChange,
}: {
  agents: Agent[];
  agentId: string | null;
  onChange: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const current = agents.find((a) => a.id === agentId);
  // Treat a stale / unknown non-null agentId as multiverse so the dropdown
  // never shows a raw UUID that doesn't match any real agent.
  const resolvedId = current ? agentId : null;
  const label = resolvedId === null ? "🌌 Multiverse" : current!.name;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 px-3 py-1.5 bg-white/5 border border-white/10 rounded-lg text-xs text-white/70 hover:border-white/20 hover:text-white transition-all"
      >
        {resolvedId === null ? (
          <Globe2 className="w-3 h-3 text-cyan-400" />
        ) : (
          <span
            className="w-2 h-2 rounded-full flex-shrink-0"
            style={{ background: agentColor(agentId!) }}
          />
        )}
        <span className="max-w-[120px] truncate">{label}</span>
        <ChevronDown className={cn("w-3 h-3 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="absolute top-full mt-1 right-0 w-52 max-h-[60vh] overflow-y-auto bg-[#0d0d14] border border-white/10 rounded-xl shadow-2xl z-[60] backdrop-blur-md">
          {/* Multiverse option */}
          <button
            onClick={() => { onChange(null); setOpen(false); }}
            className={cn(
              "w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors",
              resolvedId === null ? "bg-cyan-500/15 text-cyan-300" : "text-white/60 hover:bg-white/5 hover:text-white"
            )}
          >
            <Globe2 className="w-3.5 h-3.5 text-cyan-400" />
            <span>🌌 All Agents / Multiverse</span>
          </button>

          <div className="border-t border-white/5 my-1" />

          {/* Agent list */}
          {agents.map((a) => (
            <button
              key={a.id}
              onClick={() => { onChange(a.id); setOpen(false); }}
              className={cn(
                "w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors",
                agentId === a.id ? "bg-white/10 text-white" : "text-white/60 hover:bg-white/5 hover:text-white"
              )}
            >
              <span
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ background: agentColor(a.id) }}
              />
              <span className="truncate flex-1 text-left">{a.name}</span>
              {agentId === a.id && <span className="text-white/30 text-[10px]">active</span>}
            </button>
          ))}

          {agents.length === 0 && (
            <p className="px-3 py-2 text-xs text-white/30">No agents yet</p>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// LOD mode toggle — three-state: auto / clusters / full
// ---------------------------------------------------------------------------
function LODToggle({
  mode,
  resolved,
  onChange,
  totalNodeCount,
}: {
  mode: GraphViewMode;
  resolved: "clusters" | "full";
  onChange: (m: GraphViewMode) => void;
  totalNodeCount: number;
}) {
  const tooltip =
    mode === "auto"
      ? `Auto · currently rendering ${resolved} (${totalNodeCount.toLocaleString()} nodes)`
      : mode === "clusters"
      ? "Cluster overview — one sphere per node type"
      : "Full graph — every node rendered";

  return (
    <div
      title={tooltip}
      className="flex items-center bg-white/5 border border-white/10 rounded-lg overflow-hidden"
    >
      <button
        onClick={() => onChange("auto")}
        className={cn(
          "flex items-center gap-1 px-2.5 py-1.5 text-[11px] transition-colors",
          mode === "auto" ? "bg-cyan-500/20 text-cyan-300" : "text-white/40 hover:text-white/70"
        )}
      >
        Auto
      </button>
      <button
        onClick={() => onChange("clusters")}
        className={cn(
          "px-2.5 py-1.5 text-[11px] transition-colors",
          mode === "clusters" ? "bg-cyan-500/20 text-cyan-300" : "text-white/40 hover:text-white/70"
        )}
      >
        Cluster
      </button>
      <button
        onClick={() => onChange("full")}
        className={cn(
          "px-2.5 py-1.5 text-[11px] transition-colors",
          mode === "full" ? "bg-cyan-500/20 text-cyan-300" : "text-white/40 hover:text-white/70"
        )}
      >
        Full
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
interface Filters {
  nodeTypes: Set<string>;
  minConfidence: number;
  search: string;
}

/**
 * Convert cluster payload to GraphNode/GraphEdge shapes so the existing
 * 3D canvas can render them without a second code path.  The synthetic
 * nodes keep `type === "CLUSTER"`; GraphCanvas3D recognises that flag
 * to size the sphere by member count and attach a count sprite.
 */
function clustersToGraphData(
  clusters: GraphCluster[],
  clusterEdges: GraphClusterEdge[],
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = clusters.map((c) => ({
    id: c.id,
    label: c.label,
    type: "CLUSTER" as GraphNode["type"],
    properties: {
      node_count: c.node_count,
      sample_node_ids: c.sample_node_ids,
      original_type: c.type,
    },
  }));

  const edges: GraphEdge[] = clusterEdges.map((e) => ({
    id: e.id,
    source: e.source_cluster_id,
    target: e.target_cluster_id,
    source_id: e.source_cluster_id,
    target_id: e.target_cluster_id,
    relation: `${e.count}`,
    type: "CLUSTER_EDGE",
    properties: { count: e.count },
    utility_weight: Math.min(1 + Math.log10(Math.max(1, e.count)), 3),
  }));

  return { nodes, edges };
}

export default function StudioPage() {
  const {
    nodes, edges, fetchAll, fetchStats,
    clusters, clusterEdges, totalNodeCount,
    selectedNode, setSelectedNode,
    highlightedIds, isLoading,
    agentId, setAgentId,
    viewMode, setViewMode,
    isTruncated, serverNodeCount,
  } = useGraph();

  const [is3D, setIs3D] = useState(true);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [filters, setFilters] = useState<Filters>({
    nodeTypes: new Set(),
    minConfidence: 0,
    search: "",
  });

  // Detail panel visibility is derived from `selectedNode` so the two can never
  // drift out of sync (clearing selection or refreshing closes the panel
  // automatically — no ghost shell left behind).
  const detailOpen = selectedNode !== null;

  useEffect(() => {
    fetchAll();
    fetchStats();
    getAgents().then(setAgents).catch(() => {});
  }, [fetchAll, fetchStats]);

  async function handleRefresh() {
    await fetchAll();
    fetchStats(); // non-blocking — stats panel update doesn't affect canvas
  }

  async function handleClear() {
    // Reset view state, then fetch with `resetSelection` so the data update
    // and the selection/highlight clear land in the SAME zustand set() call.
    // That collapses what used to be two canvas rebuilds (one for clearing,
    // one for the refetch) into a single rebuild.
    setFilters({ nodeTypes: new Set(), minConfidence: 0, search: "" });
    setViewMode("auto");
    await fetchAll(true);
    fetchStats();
  }

  // Resolve "auto" to the concrete mode: cluster when the agent's graph has
  // more than AUTO_CLUSTER_THRESHOLD nodes (totalNodeCount from /graph/clusters
  // is the authoritative count — nodes.length is capped by the paginated
  // /graph endpoint and would misclassify large graphs).
  const resolvedMode: "clusters" | "full" =
    viewMode === "auto"
      ? totalNodeCount > AUTO_CLUSTER_THRESHOLD && clusters.length > 0
        ? "clusters"
        : "full"
      : viewMode;

  const showClusters = resolvedMode === "clusters" && clusters.length > 0 && agentId !== null;

  function handleNodeClick(node: GraphNode) {
    // Clicking a cluster drills down to full view so the user can inspect
    // individual members.  Filters get pre-populated so the drill-in is
    // scoped to that cluster's original type.
    if (node.type === "CLUSTER") {
      const originalType = (node.properties as { original_type?: string } | undefined)?.original_type;
      if (originalType) {
        setFilters((prev) => ({ ...prev, nodeTypes: new Set([originalType]) }));
      }
      setViewMode("full");
      return;
    }
    setSelectedNode(node);
  }

  // Memoised so transient store updates that don't touch graph data
  // (isLoading flips during fetchAll, stats updates from fetchStats, etc.)
  // don't change the array reference passed to the canvas — which would
  // otherwise re-trigger its useEffect and force a full graph rebuild on
  // every refresh / stats tick.
  const canvasData = useMemo(
    () =>
      showClusters
        ? clustersToGraphData(clusters, clusterEdges)
        : { nodes, edges },
    [showClusters, clusters, clusterEdges, nodes, edges],
  );

  const hasFilter = filters.nodeTypes.size > 0 || filters.search.length > 0;

  const displayNodes = useMemo(() => {
    if (showClusters || !hasFilter) return canvasData.nodes;
    return canvasData.nodes.filter((n) => {
      if (filters.nodeTypes.size > 0 && !filters.nodeTypes.has(n.type)) return false;
      if (filters.search && !n.label.toLowerCase().includes(filters.search.toLowerCase())) return false;
      return true;
    });
  }, [canvasData.nodes, showClusters, hasFilter, filters]);

  const displayEdges = useMemo(() => {
    // No filter applied → return canvas edges unchanged (stable reference).
    if (displayNodes === canvasData.nodes) return canvasData.edges;
    const visible = new Set(displayNodes.map((n) => n.id));
    return canvasData.edges.filter(
      (e) => visible.has(e.source as string) && visible.has(e.target as string),
    );
  }, [displayNodes, canvasData]);

  return (
    <div className="flex h-screen overflow-hidden bg-[#050508]">
      <Sidebar />

      <div className="flex-1 relative overflow-hidden">
        {/* ── Full-bleed graph canvas ── */}
        <div className="absolute inset-0">
          {displayNodes.length === 0 && !isLoading ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="text-center">
                <div className="w-20 h-20 rounded-2xl bg-white/5 border border-white/10 backdrop-blur-md flex items-center justify-center mx-auto mb-5 shadow-2xl">
                  <svg viewBox="0 0 24 24" className="w-10 h-10 text-white/20" fill="none" stroke="currentColor" strokeWidth="1">
                    <circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="2" />
                    <line x1="12" y1="2" x2="12" y2="22" /><line x1="2" y1="12" x2="22" y2="12" />
                    <line x1="4.9" y1="4.9" x2="19.1" y2="19.1" /><line x1="19.1" y1="4.9" x2="4.9" y2="19.1" />
                  </svg>
                </div>
                <p className="text-white/50 text-sm mb-1 font-medium">No graph data</p>
                {agentId === null && agents.length === 0 ? (
                  <p className="text-white/25 text-xs">
                    You have no agents yet —{" "}
                    <a href="/agents" className="text-cyan-400 hover:text-cyan-300 underline underline-offset-2">
                      Create your first agent →
                    </a>
                  </p>
                ) : (
                  <p className="text-white/25 text-xs">
                    {agentId === null
                      ? "Switch to an agent to ingest knowledge"
                      : "Ingest text in the left panel to build your knowledge graph"}
                  </p>
                )}
              </div>
            </div>
          ) : is3D ? (
            <GraphCanvas3D nodes={displayNodes} edges={displayEdges} onNodeClick={handleNodeClick} highlightedIds={highlightedIds} selectedNodeId={selectedNode?.id ?? null} isCapped={isTruncated} />
          ) : (
            <GraphCanvas2D nodes={displayNodes} edges={displayEdges} onNodeClick={handleNodeClick} highlightedIds={highlightedIds} />
          )}
        </div>

        {/* ── Toolbar ── */}
        <div className="absolute top-0 left-0 right-0 h-10 z-20 backdrop-blur-md bg-black/40 border-b border-white/10 flex items-center px-4 gap-3">
          <div className="flex items-center gap-1.5 text-xs text-white/40">
            <Layers className="w-3.5 h-3.5" />
            <span className="text-blue-400 font-mono font-medium">{displayNodes.length}</span>
            <span>{showClusters ? "clusters" : "nodes"}</span>
            <span className="text-white/20 mx-0.5">/</span>
            <span className="text-emerald-400 font-mono font-medium">{displayEdges.length}</span>
            <span>edges</span>
            {showClusters && totalNodeCount > 0 && (
              <>
                <span className="text-white/20 mx-0.5">·</span>
                <span className="text-purple-400/80">
                  {totalNodeCount.toLocaleString()} total nodes
                </span>
              </>
            )}
            {isTruncated && !showClusters && (
              <>
                <span className="text-white/20 mx-0.5">·</span>
                <span
                  className="text-amber-400/80"
                  title={`Graph has ${serverNodeCount.toLocaleString()} nodes. Showing top ${displayNodes.length.toLocaleString()} by connectivity. Switch to Clusters view to explore the full graph.`}
                >
                  top {displayNodes.length.toLocaleString()} of {serverNodeCount.toLocaleString()}
                </span>
              </>
            )}
            {highlightedIds.size > 0 && (
              <><span className="text-white/20 mx-0.5">·</span><span className="text-amber-400">{highlightedIds.size} highlighted</span></>
            )}
          </div>

          <div className="flex-1" />

          {/* Agent selector */}
          <AgentDropdown agents={agents} agentId={agentId} onChange={setAgentId} />

          {/* LOD mode toggle (hidden in multiverse — cross-agent view has its own aggregation) */}
          {agentId !== null && (
            <LODToggle
              mode={viewMode}
              resolved={resolvedMode}
              onChange={setViewMode}
              totalNodeCount={totalNodeCount}
            />
          )}

          {/* 2D/3D toggle */}
          <div className="flex items-center bg-white/5 border border-white/10 rounded-lg overflow-hidden">
            <button onClick={() => setIs3D(true)} className={cn("flex items-center gap-1.5 px-3 py-1.5 text-xs transition-colors", is3D ? "bg-violet-500/20 text-violet-300" : "text-white/40 hover:text-white/70")}>
              <Box className="w-3 h-3" />3D
            </button>
            <button onClick={() => setIs3D(false)} className={cn("flex items-center gap-1.5 px-3 py-1.5 text-xs transition-colors", !is3D ? "bg-violet-500/20 text-violet-300" : "text-white/40 hover:text-white/70")}>
              <Grid2X2 className="w-3 h-3" />2D
            </button>
          </div>

          <button onClick={handleRefresh} disabled={isLoading} className="flex items-center gap-1.5 px-3 py-1.5 bg-white/5 border border-white/10 rounded-lg text-xs text-white/50 hover:text-white/80 hover:border-white/20 transition-all disabled:opacity-40 backdrop-blur-sm">
            <RefreshCw className={cn("w-3 h-3", isLoading && "animate-spin")} />Refresh
          </button>
          <button onClick={handleClear} disabled={isLoading} className="flex items-center gap-1.5 px-3 py-1.5 bg-white/5 border border-white/10 rounded-lg text-xs text-white/50 hover:text-red-400 hover:border-red-500/30 transition-all backdrop-blur-sm disabled:opacity-40">
            <Trash2 className="w-3 h-3" />Clear
          </button>
        </div>

        {/* ── Left panel ── */}
        <div className="absolute top-10 left-0 bottom-0 w-[285px] z-10 backdrop-blur-md bg-black/50 border-r border-white/10 overflow-y-auto">
          <div className="flex flex-col divide-y divide-white/5">
            <div className="p-4"><IngestPanel agentId={agentId} /></div>
            <div className="p-4"><QueryPanel /></div>
            <div className="p-4"><SwarmDashboard /></div>
            <div className="p-4"><FilterBar onFiltersChange={setFilters} /></div>
            <div className="p-4"><GraphStats /></div>
            <div className="p-4"><LivePheromoneStream /></div>
          </div>
        </div>

        {/* ── Right detail panel ── */}
        <div className={cn(
          "absolute top-10 right-0 bottom-0 z-10 backdrop-blur-md bg-black/50 border-l border-white/10 overflow-y-auto transition-all duration-300",
          detailOpen ? "w-[320px]" : "w-0 overflow-hidden border-l-0"
        )}>
          {selectedNode && detailOpen && (
            <div className="p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-semibold text-white/40 uppercase tracking-wider">Node Detail</span>
                <button onClick={() => setSelectedNode(null)} className="text-white/30 hover:text-white/70 transition-colors">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <NodeDetailPanel node={selectedNode} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
