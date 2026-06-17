"use client";

import { create } from "zustand";
import {
  getGraph,
  getGraphClusters,
  getGraphStats,
  getMultiverseGraph,
} from "@/lib/api";
import type {
  GraphCluster,
  GraphClusterEdge,
  GraphEdge,
  GraphNode,
  GraphStats,
} from "@/lib/types";

/**
 * Level-of-detail view mode for the graph canvas.
 *
 *  - "auto":     pick clusters when the graph is too large for full render
 *                (threshold = AUTO_CLUSTER_THRESHOLD nodes), otherwise full
 *  - "clusters": always render the aggregated cluster overview
 *  - "full":     always render every node (original behaviour)
 */
export type GraphViewMode = "auto" | "clusters" | "full";

/** Above this many nodes, "auto" falls back to cluster rendering. */
export const AUTO_CLUSTER_THRESHOLD = 1000;

/**
 * Hard cap on nodes passed to the force-graph physics engine.
 *
 * The d3-force simulation runs on the main thread. At 10k+ nodes it freezes
 * the browser; at 2k nodes the simulation converges in <2 s on mid-range
 * hardware.  This constant must stay ≤ the server-side `le=2000` guardrail so
 * the API never returns more nodes than we are willing to render.
 *
 * When the server returns more candidates than this limit (e.g. multiverse
 * mode), nodes are sorted by degree (highest first) before slicing so the
 * most-connected — and therefore most informative — nodes are always visible.
 * A `isTruncated` flag is set so the UI can show a banner.
 */
export const MAX_RENDER_NODES = 2000;

/**
 * Sort nodes by descending degree (in + out connections) and return the top N.
 * Edges are then filtered to only include edges whose both endpoints survived
 * the cut, preserving graph coherence.
 */
function applyRenderCap(
  rawNodes: GraphNode[],
  rawEdges: GraphEdge[],
  cap: number,
): { nodes: GraphNode[]; edges: GraphEdge[]; isTruncated: boolean } {
  if (rawNodes.length <= cap) {
    return { nodes: rawNodes, edges: rawEdges, isTruncated: false };
  }

  // Build a degree map: count every edge endpoint appearance
  const degree = new Map<string, number>();
  for (const e of rawEdges) {
    const src = typeof e.source === "string" ? e.source : (e.source as GraphNode).id;
    const tgt = typeof e.target === "string" ? e.target : (e.target as GraphNode).id;
    degree.set(src, (degree.get(src) ?? 0) + 1);
    degree.set(tgt, (degree.get(tgt) ?? 0) + 1);
  }

  // Sort descending by degree, then slice to cap
  const sorted = [...rawNodes].sort(
    (a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0),
  );
  const nodes = sorted.slice(0, cap);
  const survivorIds = new Set(nodes.map((n) => n.id));

  // Drop edges whose either endpoint was pruned — prevents dangling edge IDs
  // from crashing the force-graph renderer
  const edges = rawEdges.filter((e) => {
    const src = typeof e.source === "string" ? e.source : (e.source as GraphNode).id;
    const tgt = typeof e.target === "string" ? e.target : (e.target as GraphNode).id;
    return survivorIds.has(src) && survivorIds.has(tgt);
  });

  return { nodes, edges, isTruncated: true };
}

interface GraphState {
  nodes: GraphNode[];
  edges: GraphEdge[];
  clusters: GraphCluster[];
  clusterEdges: GraphClusterEdge[];
  totalNodeCount: number;
  selectedNode: GraphNode | null;
  highlightedIds: Set<string>;
  isLoading: boolean;
  error: string | null;
  /** null = Multiverse mode (all agents) */
  agentId: string | null;
  stats: GraphStats | null;
  viewMode: GraphViewMode;
  /**
   * True when the received payload exceeded MAX_RENDER_NODES and was trimmed.
   * The UI should render a banner: "Showing top N of M nodes".
   */
  isTruncated: boolean;
  /** Raw total from the server before client-side render cap is applied. */
  serverNodeCount: number;

  // Actions
  /**
   * Fetch graph + clusters in one go and commit in a single set() call so the
   * canvas only re-renders once.  This prevents the double-layout that happened
   * when fetchGraph and fetchClusters completed at different times and each
   * independently wrote to the store.
   *
   * When `resetSelection` is true, selection + highlights are cleared in the
   * same set() — used by the toolbar Clear button so the canvas only rebuilds
   * once instead of twice (once for the clear, once for the refetch).
   */
  fetchAll: (resetSelection?: boolean) => Promise<void>;
  fetchStats: () => Promise<void>;
  setSelectedNode: (node: GraphNode | null) => void;
  highlightNodes: (ids: string[]) => void;
  clearHighlights: () => void;
  setAgentId: (id: string | null) => void;
  setViewMode: (mode: GraphViewMode) => void;
  removeNode: (nodeId: string) => void;
  addNodes: (nodes: GraphNode[]) => void;
  addEdges: (edges: GraphEdge[]) => void;
}

export const useGraph = create<GraphState>((set, get) => ({
  nodes: [],
  edges: [],
  clusters: [],
  clusterEdges: [],
  totalNodeCount: 0,
  selectedNode: null,
  highlightedIds: new Set(),
  isLoading: false,
  error: null,
  agentId: null,
  stats: null,
  viewMode: "auto",
  isTruncated: false,
  serverNodeCount: 0,

  fetchAll: async (resetSelection = false) => {
    const id = get().agentId;
    set({ isLoading: true, error: null });
    try {
      // Fetch graph data and cluster data in parallel; clusters are only
      // available for specific agents (not multiverse).
      const [graphData, clusterData] = await Promise.all([
        id === null
          ? getMultiverseGraph(MAX_RENDER_NODES)
          : getGraph(id, MAX_RENDER_NODES),
        id !== null ? getGraphClusters(id).catch(() => null) : Promise.resolve(null),
      ]);

      // Apply the render cap before handing data to the physics engine.
      // Nodes are sorted by degree so the most-connected nodes are always
      // visible when the graph is trimmed.  Edges whose endpoints were pruned
      // are dropped to prevent dangling edge IDs crashing the renderer.
      const rawServerCount = graphData.node_count ?? graphData.nodes.length;
      const { nodes, edges, isTruncated } = applyRenderCap(
        graphData.nodes as GraphNode[],
        graphData.edges as GraphEdge[],
        MAX_RENDER_NODES,
      );

      // Single set() → single re-render → single canvas update → no double layout
      set({
        nodes,
        edges,
        clusters: clusterData?.clusters ?? [],
        clusterEdges: clusterData?.cluster_edges ?? [],
        totalNodeCount: clusterData?.total_nodes ?? rawServerCount,
        isTruncated,
        serverNodeCount: rawServerCount,
        isLoading: false,
        ...(resetSelection
          ? { selectedNode: null, highlightedIds: new Set<string>() }
          : {}),
      });
    } catch (e) {
      set({
        error: e instanceof Error ? e.message : "Failed to load graph",
        isLoading: false,
      });
    }
  },

  fetchStats: async () => {
    const id = get().agentId;
    if (id === null) return; // no per-agent stats in multiverse mode
    try {
      const stats = await getGraphStats(id);
      set({ stats });
    } catch {
      // Non-critical
    }
  },

  setSelectedNode: (node) => set({ selectedNode: node }),

  highlightNodes: (ids) => set({ highlightedIds: new Set(ids) }),

  clearHighlights: () => set({ highlightedIds: new Set() }),

  setViewMode: (mode) => set({ viewMode: mode }),

  setAgentId: (id) => {
    set({
      agentId: id,
      nodes: [],
      edges: [],
      clusters: [],
      clusterEdges: [],
      totalNodeCount: 0,
      selectedNode: null,
      stats: null,
      highlightedIds: new Set(),
      isTruncated: false,
      serverNodeCount: 0,
    });
    get().fetchAll();
    if (id !== null) get().fetchStats();
  },

  removeNode: (nodeId) =>
    set((state) => ({
      nodes: state.nodes.filter((n) => n.id !== nodeId),
      edges: state.edges.filter(
        (e) => e.source !== nodeId && e.target !== nodeId
      ),
      selectedNode:
        state.selectedNode?.id === nodeId ? null : state.selectedNode,
    })),

  addNodes: (newNodes) =>
    set((state) => {
      const existingIds = new Set(state.nodes.map((n) => n.id));
      const fresh = newNodes.filter((n) => !existingIds.has(n.id));
      return { nodes: [...state.nodes, ...fresh] };
    }),

  addEdges: (newEdges) =>
    set((state) => {
      const existingIds = new Set(state.edges.map((e) => e.id));
      const fresh = newEdges.filter((e) => !existingIds.has(e.id));
      return { edges: [...state.edges, ...fresh] };
    }),
}));
