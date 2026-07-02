"use client";

import { useEffect, useRef, useCallback } from "react";
import type { GraphNode, GraphEdge } from "@/lib/types";
import { NODE_TYPE_COLORS } from "@/lib/constants";
import { useMemoryMode } from "@/context/MemoryModeContext";

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onNodeClick?: (node: GraphNode) => void;
  onNodeRightClick?: (node: GraphNode, event: MouseEvent) => void;
  highlightedIds?: Set<string>;
  /**
   * External (store-driven) selection.  Passing `null` clears the in-canvas
   * neighbor-dim highlight; otherwise the canvas treats this id the same as
   * if the user had clicked the node themselves.
   */
  selectedNodeId?: string | null;
  /**
   * When true the render cap was applied and a warning banner is shown.
   * Passed from the parent rather than read from the store so the component
   * stays testable and free of store coupling.
   */
  isCapped?: boolean;
}

function nodeBaseColor(type: string): string {
  return NODE_TYPE_COLORS[type] ?? NODE_TYPE_COLORS.OTHER ?? "#6B7280";
}

function nodeSize(node: GraphNode, edges: GraphEdge[]): number {
  // Cluster LOD nodes: size scales with member count so large groups
  // read at a glance even when zoomed out.
  const count = (node.properties as { node_count?: number } | undefined)?.node_count;
  if (node.type === "CLUSTER" && typeof count === "number") {
    return Math.min(18 + Math.sqrt(count) * 3, 60);
  }
  const connections = edges.filter(
    (e) => e.source === node.id || e.target === node.id
  ).length;
  return Math.min(4 + connections * 0.8, 18);
}


function getNeighborIds(nodeId: string, edges: GraphEdge[]): Set<string> {
  const ids = new Set<string>([nodeId]);
  for (const e of edges) {
    if (e.source === nodeId) ids.add(e.target as string);
    if (e.target === nodeId) ids.add(e.source as string);
  }
  return ids;
}

export default function GraphCanvas3D({
  nodes,
  edges,
  onNodeClick,
  onNodeRightClick,
  highlightedIds = new Set(),
  selectedNodeId,
  isCapped = false,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<any>(null);
  const mountedRef = useRef(false);
  const selectedNodeIdRef = useRef<string | null>(null);
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  // Track previous cluster-view state so we only reheat the force simulation
  // on mode transitions (cluster↔full), not on every incremental data update.
  const wasClusterViewRef = useRef<boolean | null>(null);

  // Engine version ref — readable inside force-graph callbacks every frame
  // without needing to re-initialise the graph object.
  const { memoryMode } = useMemoryMode();
  const memoryModeRef = useRef(memoryMode);
  memoryModeRef.current = memoryMode;

  nodesRef.current = nodes;
  edgesRef.current = edges;

  const buildGraphData = useCallback(() => {
    const selectedId = selectedNodeIdRef.current;
    const neighbors = selectedId ? getNeighborIds(selectedId, edgesRef.current) : null;
    const hasHighlights = highlightedIds.size > 0;
    const isOn = memoryModeRef.current === "on";

    return {
      nodes: nodesRef.current.map((n) => {
        const isHighlighted = highlightedIds.has(n.id);
        const isNeighbor = neighbors ? neighbors.has(n.id) : true;
        // Cluster LOD nodes carry the original type in properties so the
        // sphere inherits the correct palette colour (PERSON clusters
        // look like PERSONs, ORG clusters like ORGs …).
        const paletteType =
          n.type === "CLUSTER"
            ? (n.properties as { original_type?: string } | undefined)?.original_type ?? "OTHER"
            : n.type;
        const color =
          isHighlighted
            ? "#FBBF24"
            : hasHighlights
            ? isHighlighted ? nodeBaseColor(paletteType) : "#1a1a2e"
            : neighbors
            ? isNeighbor ? nodeBaseColor(paletteType) : "#111118"
            : nodeBaseColor(paletteType);

        return {
          ...n,
          __color: color,
          __size: nodeSize(n, edgesRef.current),
        };
      }),
      links: edgesRef.current.map((e) => {
        const src = e.source as string;
        const tgt = e.target as string;
        const isBridge = e.type === "SHARES_KNOWLEDGE_WITH";
        const weight = e.utility_weight ?? 1.0;
        const isStrongSynapse = isOn && weight > 1.5;
        const isActive = neighbors
          ? neighbors.has(src) && neighbors.has(tgt)
          : true;

        // V2: strong synapses glow brighter; V1: normal palette
        const linkColor = isBridge
          ? "#00ffff"
          : isStrongSynapse
          ? "rgba(192,132,252,1.0)"   // bright violet for strong synapses
          : isActive
          ? "rgba(139,92,246,0.7)"
          : "rgba(40,40,60,0.2)";

        const particleColor = isBridge
          ? "#00ffff"
          : isStrongSynapse
          ? "#e879f9"                 // fuchsia particles on strong synapses
          : isActive
          ? "#c084fc"
          : "rgba(0,0,0,0)";

        return {
          ...e,
          source: src,
          target: tgt,
          __color: linkColor,
          __particleColor: particleColor,
          __weight: weight,
          __isStrongSynapse: isStrongSynapse,
          __isBridge: isBridge,
        };
      }),
    };
  }, [highlightedIds]);

  useEffect(() => {
    if (!containerRef.current || mountedRef.current) return;
    mountedRef.current = true;
    const container = containerRef.current;

    (async () => {
      const [{ default: _FG3D }, THREE, { UnrealBloomPass }] =
        await Promise.all([
          import("3d-force-graph"),
          import("three"),
          import("three/examples/jsm/postprocessing/UnrealBloomPass.js"),
        ]);

      if (!container) return;

      const ForceGraph3D = _FG3D as any;
      const graph = ForceGraph3D()(container)
        .backgroundColor("#050508")
        .width(container.clientWidth)
        .height(container.clientHeight)
        .nodeLabel((n: any) => {
          // max-width + wrapping keeps long FACT-node text (~200 chars) inside a
          // bounded box (line-clamped to 4 lines) instead of a runaway one-liner.
          const base = `max-width:340px;white-space:normal;word-break:break-word;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden;padding:4px 8px;background:rgba(10,10,20,0.9);border:1px solid rgba(139,92,246,0.5);border-radius:6px;color:#e4e4e7;font-size:12px;backdrop-filter:blur(4px)`;
          if (n.type === "CLUSTER") {
            const count = n.properties?.node_count ?? 0;
            const originalType = n.properties?.original_type ?? n.label;
            return `<div style="${base}">${originalType} cluster · <span style="color:#a78bfa">${count.toLocaleString()} nodes</span> — click to drill in</div>`;
          }
          return `<div style="${base}">${n.label} · <span style="color:#a78bfa">${n.type}</span></div>`;
        })
        .nodeColor((n: any) => n.__color)
        .nodeVal((n: any) => n.__size)
        .nodeOpacity(0.92)
        .linkColor((l: any) => l.__color)
        .linkDirectionalArrowLength(4)
        .linkDirectionalArrowRelPos(0.88)
        .linkLabel((l: any) => l.relation)
        .linkOpacity(0.6)
        // ── V2 Cognitive Graph: edge width = synapse strength ─────────────
        // memoryModeRef is read every frame (callbacks are called per-link
        // by the force-graph renderer), so no re-init needed on toggle.
        .linkWidth((l: any) => {
          if (l.__isBridge) return 1.5;
          if (memoryModeRef.current === "on") {
            return Math.max(1, (l.__weight ?? 1) * 1.5);
          }
          return 1;
        })
        // ── Particles ──────────────────────────────────────────────────────
        // V1: only synaptic bridges get particles
        // V2: bridges + strong synapses (weight > 1.5)
        .linkDirectionalParticles((l: any) => {
          if (l.__isBridge) return 4;
          if (memoryModeRef.current === "on" && l.__isStrongSynapse) return 2;
          return 0;
        })
        .linkDirectionalParticleSpeed((l: any) => {
          if (l.__isBridge) return 0.01;
          if (memoryModeRef.current === "on" && l.__isStrongSynapse) return 0.008;
          return 0;
        })
        .linkDirectionalParticleWidth((l: any) => {
          if (l.__isBridge) return 2;
          if (memoryModeRef.current === "on" && l.__isStrongSynapse) return 1.5;
          return 0;
        })
        .linkDirectionalParticleColor((l: any) => l.__particleColor)
        .onNodeClick((n: any) => {
          const prev = selectedNodeIdRef.current;
          selectedNodeIdRef.current = prev === n.id ? null : n.id;

          if (selectedNodeIdRef.current) {
            const dist = 80;
            const mag = Math.hypot(n.x ?? 1, n.y ?? 1, n.z ?? 1);
            const ratio = 1 + dist / (mag || 1);
            graph.cameraPosition(
              { x: n.x * ratio, y: n.y * ratio, z: n.z * ratio },
              n,
              800
            );
          }

          graph.graphData(buildGraphData());
          onNodeClick?.(n as GraphNode);
        })
        .onNodeRightClick((n: any, event: MouseEvent) =>
          onNodeRightClick?.(n as GraphNode, event)
        )
        // Pre-converge the simulation off-screen before the first paint so
        // nodes don't explode outward from the origin on mount.  100 ticks
        // is enough to reach a stable layout on graphs up to ~2000 nodes
        // without blocking the main thread noticeably.
        .warmupTicks(100)
        .graphData(buildGraphData());

      graphRef.current = graph;

      // UnrealBloom — neon glow
      try {
        const composer = graph.postProcessingComposer();
        if (composer) {
          // strength / radius / threshold. Lowered strength + higher threshold
          // so dense graphs don't wash out to a white blob — only bright nodes
          // glow (mirrors MultiverseCanvas).
          const bloom = new UnrealBloomPass(
            new THREE.Vector2(container.clientWidth, container.clientHeight),
            0.7,
            0.5,
            0.2
          );
          composer.addPass(bloom);
        }
      } catch (err) {
        console.warn("[SpAIder] Bloom unavailable:", err);
      }

      const ro = new ResizeObserver(() => {
        if (graphRef.current) {
          graphRef.current.width(container.clientWidth).height(container.clientHeight);
        }
      });
      ro.observe(container);
    })();

    return () => {
      mountedRef.current = false;
      graphRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Refresh graph data when nodes/edges or engine version changes
  useEffect(() => {
    if (!graphRef.current) return;

    // External selection control: keep the in-canvas neighbor-dim highlight
    // in sync with the store's `selectedNode`.  Caller passes `null` (e.g. on
    // Clear) and the dimming resets without needing an extra rebuild — this
    // runs in the same effect as the data update so it's one commit.
    if (selectedNodeId !== undefined) {
      const next = selectedNodeId ?? null;
      if (selectedNodeIdRef.current !== next) {
        selectedNodeIdRef.current = next;
      }
    }

    // If the selected node is no longer in the incoming dataset (e.g. switching
    // between Cluster / Full views) clear the stale selection so all nodes
    // render at full brightness instead of appearing dark grey.
    if (
      selectedNodeIdRef.current &&
      !nodes.some((n) => n.id === selectedNodeIdRef.current)
    ) {
      selectedNodeIdRef.current = null;
    }

    graphRef.current.graphData(buildGraphData());

    // Only reconfigure forces and reheat the simulation when the view MODE
    // changes (full ↔ cluster).  Reheating on every incremental data update
    // (e.g. fetchGraph then fetchClusters completing separately) makes the
    // graph jump and re-layout multiple times per refresh cycle.
    const isClusterView = nodes.length > 0 && nodes.every((n) => n.type === "CLUSTER");
    const modeChanged = wasClusterViewRef.current !== isClusterView;
    wasClusterViewRef.current = isClusterView;

    if (modeChanged) {
      try {
        const chargeForce = graphRef.current.d3Force("charge");
        if (chargeForce) {
          // Full graph: -150 gives good node spread up to 2k nodes while
          // converging in ~2 s on mid-range hardware.  The previous -30 was
          // too weak (nodes clumped in the centre) and -2500 on cluster view
          // burned excessive ticks for any graph with 200+ cluster types.
          chargeForce.strength(isClusterView ? -500 : -150);
        }
        const linkForce = graphRef.current.d3Force("link");
        if (linkForce) {
          linkForce.distance(isClusterView ? 300 : 30);
        }
        graphRef.current.d3ReheatSimulation();
      } catch {
        // d3Force may not be available in all versions — silent fallback
      }
    }
  }, [buildGraphData, nodes, edges, memoryMode, selectedNodeId]);

  return (
    <div className="relative w-full h-full">
      <div
        ref={containerRef}
        className="w-full h-full"
        style={{
          background:
            "radial-gradient(ellipse at 50% 40%, #0d0820 0%, #050508 65%)",
        }}
      />
      {isCapped && (
        // Anchored bottom-centre, z-40 (below the top toolbar / agent dropdown
        // at z-[60]) and click-through, so it never overlaps or blocks the
        // controls the way the old top-centre banner did.
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-40 bg-amber-500/10 border border-amber-500/50 text-amber-400 px-4 py-2 rounded-md backdrop-blur-md text-xs whitespace-nowrap pointer-events-none select-none opacity-90">
          ⚠️ Showing top 2,000 nodes to preserve performance. Use search to filter.
        </div>
      )}
    </div>
  );
}
