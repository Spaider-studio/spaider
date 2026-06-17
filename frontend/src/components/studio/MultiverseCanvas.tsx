"use client";

import {
  useEffect,
  useRef,
  useCallback,
  forwardRef,
  useImperativeHandle,
} from "react";
import type { GraphNode, GraphEdge } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SwarmFocus {
  source_id: string;
  target_id: string;
}

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedAgentId: string | null;
  swarmFocus?: SwarmFocus | null;
  highlightedIds?: Set<string>;
  onNodeClick?: (node: GraphNode) => void;
}

export interface MultiverseCanvasHandle {
  /** Fly the camera to the SystemAgent core node for this agentId. */
  focusAgent: (agentId: string) => void;
  /** Fly the camera to the midpoint between two SystemAgent cores. */
  focusSwarm: (sourceId: string, targetId: string) => void;
  /** Reset camera to the default overview position. */
  resetCamera: () => void;
}

// ---------------------------------------------------------------------------
// Colour helpers
// ---------------------------------------------------------------------------

const AGENT_PALETTE = [
  "#8B5CF6", // violet
  "#EC4899", // pink
  "#06B6D4", // cyan
  "#10B981", // emerald
  "#F59E0B", // amber
  "#EF4444", // red
  "#3B82F6", // blue
  "#84CC16", // lime
  "#F97316", // orange
  "#A78BFA", // lavender
];

function agentColor(agentId: string | undefined | null): string {
  if (!agentId) return "#6B7280";
  let hash = 0;
  for (let i = 0; i < agentId.length; i++) {
    hash = (Math.imul(31, hash) + agentId.charCodeAt(i)) | 0;
  }
  return AGENT_PALETTE[Math.abs(hash) % AGENT_PALETTE.length];
}

function withOpacity(hex: string, opacity: number): string {
  const clamped = Math.min(1, Math.max(0, opacity));
  const alpha = Math.round(clamped * 255).toString(16).padStart(2, "0");
  return hex + alpha;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const MultiverseCanvas = forwardRef<MultiverseCanvasHandle, Props>(
  function MultiverseCanvas(
    { nodes, edges, selectedAgentId, swarmFocus, highlightedIds, onNodeClick },
    ref
  ) {
    const containerRef = useRef<HTMLDivElement>(null);
    const graphRef = useRef<any>(null);
    const mountedRef = useRef(false);
    const nodesRef = useRef(nodes);
    const edgesRef = useRef(edges);
    const selectedRef = useRef(selectedAgentId);
    const swarmFocusRef = useRef(swarmFocus);
    const highlightedRef = useRef(highlightedIds);

    nodesRef.current = nodes;
    edgesRef.current = edges;
    selectedRef.current = selectedAgentId;
    swarmFocusRef.current = swarmFocus;
    highlightedRef.current = highlightedIds;

    // ------------------------------------------------------------------
    // Build the graphData object consumed by 3d-force-graph
    // ------------------------------------------------------------------
    const buildGraphData = useCallback(() => {
      const sel = selectedRef.current;
      const swarm = swarmFocusRef.current;
      const highlighted = highlightedRef.current;
      const ns = nodesRef.current;
      // The multiverse endpoint caps nodes and edges independently, so an edge
      // can reference a node beyond the node cap. 3d-force-graph throws
      // "node not found" on such dangling links, which breaks the layout —
      // drop any edge whose endpoints aren't both present.
      const nodeIds = new Set(ns.map((n) => n.id));
      const es = edgesRef.current.filter(
        (e) =>
          nodeIds.has(e.source as string) && nodeIds.has(e.target as string)
      );
      const hasHighlight = highlighted && highlighted.size > 0;

      return {
        nodes: ns.map((n) => {
          const baseColor = agentColor(n.agent_id);
          const isCore = n.type === "agent_core";
          const isHighlighted = hasHighlight && highlighted!.has(n.id);

          const connections = es.filter(
            (e) =>
              (e.source as string) === n.id || (e.target as string) === n.id
          ).length;

          let color: string;
          let size: number;

          // ── Priority 1: Dual-agent swarm focus ──────────────────────────
          if (swarm) {
            const inFocus =
              n.agent_id === swarm.source_id ||
              n.agent_id === swarm.target_id ||
              (isCore && (n.id === swarm.source_id || n.id === swarm.target_id));

            if (inFocus) {
              // Full brightness — keep each agent's own colour
              color = isCore ? baseColor : withOpacity(baseColor, 0.95);
              size = isCore ? 22 : Math.min(4 + connections * 0.7, 16) * 1.1;
            } else {
              // Everything else fades to near-black
              color = isCore ? withOpacity(baseColor, 0.08) : "#0d0d15";
              size = isCore ? 12 : Math.min(4 + connections * 0.7, 16) * 0.5;
            }

          // ── Priority 2: Swarm query highlight (Zustand) ─────────────────
          } else if (hasHighlight) {
            color = isHighlighted
              ? "#06B6D4"
              : isCore
              ? withOpacity(baseColor, 0.2)
              : "#111118";
            size = isCore
              ? 22
              : isHighlighted
              ? Math.min(4 + connections * 0.7, 16) * 1.8
              : Math.min(4 + connections * 0.7, 16);

          // ── Priority 3: Single-agent focus ──────────────────────────────
          } else if (sel) {
            const belongsToSelected =
              n.agent_id === sel || (isCore && n.id === sel);
            color = belongsToSelected ? baseColor : "#111118";
            size = isCore ? 22 : Math.min(4 + connections * 0.7, 16);

          // ── Priority 4: Overview — full galaxy ──────────────────────────
          } else {
            color = isCore ? baseColor : withOpacity(baseColor, 0.85);
            size = isCore ? 22 : Math.min(4 + connections * 0.7, 16);
          }

          return { ...n, __color: color, __size: size };
        }),

        links: es.map((e) => {
          const isBta = e.relation === "BELONGS_TO_AGENT";
          const isBridge = e.relation === "SHARES_KNOWLEDGE_WITH";
          const src = e.source as string;
          const tgt = e.target as string;

          let linkColor: string;
          let particleColor: string;
          let particleCount: number;

          // Synaptic bridges always render as glowing cyan — highest priority
          if (isBridge) {
            // In swarm focus, the active bridge gets extra glow; others stay cyan but dimmer
            const isActiveBridge =
              swarmFocus &&
              ((src === swarmFocus.source_id && tgt === swarmFocus.target_id) ||
                (src === swarmFocus.target_id && tgt === swarmFocus.source_id));

            linkColor = isActiveBridge
              ? "#00ffff"
              : swarmFocus
              ? "rgba(0,255,255,0.25)"
              : "#00ffff";
            particleColor = isActiveBridge ? "#00ffff" : swarmFocus ? "rgba(0,255,255,0.3)" : "#00ffff";
            particleCount = isActiveBridge ? 6 : swarmFocus ? 2 : 4;

            return {
              ...e,
              source: src,
              target: tgt,
              __color: linkColor,
              __particleColor: particleColor,
              __particleCount: particleCount,
              __width: isActiveBridge ? 3 : 2,
            };
          }

          // ── Swarm focus link colouring ───────────────────────────────────
          if (swarmFocus) {
            const srcNode = ns.find((n) => n.id === src);
            const tgtNode = ns.find((n) => n.id === tgt);
            const srcInFocus =
              srcNode?.agent_id === swarmFocus.source_id ||
              srcNode?.agent_id === swarmFocus.target_id;
            const tgtInFocus =
              tgtNode?.agent_id === swarmFocus.source_id ||
              tgtNode?.agent_id === swarmFocus.target_id;
            const inFocus = srcInFocus && tgtInFocus;

            if (inFocus && !isBta) {
              const edgeColor = agentColor(e.agent_id);
              linkColor = withOpacity(edgeColor, 0.75);
              particleColor = edgeColor;
              particleCount = 3;
            } else if (isBta && srcInFocus) {
              linkColor = "rgba(80,80,120,0.12)";
              particleColor = "rgba(0,0,0,0)";
              particleCount = 0;
            } else {
              linkColor = "rgba(20,20,30,0.04)";
              particleColor = "rgba(0,0,0,0)";
              particleCount = 0;
            }

            return {
              ...e,
              source: src,
              target: tgt,
              __color: linkColor,
              __particleColor: particleColor,
              __particleCount: particleCount,
              __width: isBta ? 0.2 : inFocus ? 1 : 0.3,
            };
          }

          // ── Swarm query highlight ────────────────────────────────────────
          const hasHighlight = highlighted && highlighted.size > 0;
          const hasHighlightedEndpoint =
            hasHighlight &&
            (highlighted!.has(src) || highlighted!.has(tgt));

          if (hasHighlight) {
            linkColor = hasHighlightedEndpoint
              ? "rgba(6,182,212,0.6)"
              : "rgba(30,30,50,0.05)";
            particleColor = hasHighlightedEndpoint
              ? "rgba(6,182,212,0.8)"
              : "rgba(0,0,0,0)";
            particleCount = hasHighlightedEndpoint && !isBta ? 3 : 0;
          // ── Single-agent focus ───────────────────────────────────────────
          } else if (sel) {
            const srcNode = ns.find((n) => n.id === src);
            const tgtNode = ns.find((n) => n.id === tgt);
            const inCluster =
              srcNode?.agent_id === sel && tgtNode?.agent_id === sel;
            const isAgentBta =
              isBta &&
              (srcNode?.agent_id === sel || tgtNode?.agent_id === sel);

            if (inCluster) {
              const edgeColor = agentColor(e.agent_id);
              linkColor = edgeColor;
              particleColor = edgeColor;
              particleCount = 4;
            } else if (isAgentBta) {
              linkColor = "rgba(100,100,180,0.3)";
              particleColor = "rgba(0,0,0,0)";
              particleCount = 0;
            } else {
              linkColor = "rgba(30,30,50,0.08)";
              particleColor = "rgba(0,0,0,0)";
              particleCount = 0;
            }
          // ── Overview ─────────────────────────────────────────────────────
          } else {
            const edgeColor = agentColor(e.agent_id);
            linkColor = isBta
              ? "rgba(80,80,120,0.15)"
              : withOpacity(edgeColor, 0.65);
            particleColor = isBta ? "rgba(0,0,0,0)" : edgeColor;
            particleCount = isBta ? 0 : 3;
          }

          return {
            ...e,
            source: src,
            target: tgt,
            __color: linkColor,
            __particleColor: particleColor,
            __particleCount: particleCount,
            __width: isBta ? 0.3 : 1,
          };
        }),
      };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // ------------------------------------------------------------------
    // Imperative handle
    // ------------------------------------------------------------------
    useImperativeHandle(ref, () => ({
      focusAgent(agentId: string) {
        if (!graphRef.current) return;
        const data = graphRef.current.graphData() as { nodes: any[] };

        // Collect every node in this cluster (SystemAgent core + its members)
        // that has a settled position from the force simulation.
        const cluster = data.nodes.filter((n: any) =>
          n.x != null && n.y != null && n.z != null && (
            (n.type === "agent_core" && n.id === agentId) ||
            n.agent_id === agentId
          )
        );
        if (cluster.length === 0) return;

        // Axis-aligned bounding box. Center of bbox is the camera lookAt.
        // We deliberately fly in along world +z (same direction every time)
        // so different agents don't end up viewed from wildly different
        // angles — that was the reason framing felt inconsistent before.
        let minX = Infinity, minY = Infinity, minZ = Infinity;
        let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
        for (const n of cluster) {
          if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
          if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
          if (n.z < minZ) minZ = n.z; if (n.z > maxZ) maxZ = n.z;
        }
        const cx = (minX + maxX) / 2;
        const cy = (minY + maxY) / 2;
        const cz = (minZ + maxZ) / 2;

        // We're flying in along +z, so what fits the screen is the (x,y)
        // bbox extent and the FOV. Distance must satisfy:
        //   distance * tan(vertical_half_fov)   >= bbox_height/2
        //   distance * tan(horizontal_half_fov) >= bbox_width/2
        // Plus enough room for the bbox depth (along z) to not poke through
        // the camera.
        const bboxW = Math.max(maxX - minX, 1);
        const bboxH = Math.max(maxY - minY, 1);
        const bboxD = Math.max(maxZ - minZ, 1);

        const tanV = Math.tan((50 * Math.PI / 180) / 2);
        const cw = containerRef.current?.clientWidth ?? 1;
        const ch = Math.max(containerRef.current?.clientHeight ?? 1, 1);
        const aspect = cw / ch;
        const tanH = tanV * aspect;

        const distVert = (bboxH / 2) / tanV;
        const distHoriz = (bboxW / 2) / tanH;
        const distance = Math.max(distVert, distHoriz, 60) * 1.4 + bboxD / 2;

        graphRef.current.cameraPosition(
          { x: cx, y: cy, z: cz + distance },
          { x: cx, y: cy, z: cz },
          1500
        );
      },

      focusSwarm(sourceId: string, targetId: string) {
        if (!graphRef.current) return;
        const data = graphRef.current.graphData() as { nodes: any[] };

        const srcCore = data.nodes.find(
          (n: any) => n.type === "agent_core" && n.id === sourceId
        );
        const tgtCore = data.nodes.find(
          (n: any) => n.type === "agent_core" && n.id === targetId
        );

        // Fall back to focusAgent on whichever core exists
        if (!srcCore && !tgtCore) return;
        if (!srcCore) return graphRef.current.cameraPosition(
          { x: tgtCore.x * 1.5, y: tgtCore.y * 1.5, z: tgtCore.z * 1.5 },
          tgtCore, 2000
        );
        if (!tgtCore) return graphRef.current.cameraPosition(
          { x: srcCore.x * 1.5, y: srcCore.y * 1.5, z: srcCore.z * 1.5 },
          srcCore, 2000
        );

        // Midpoint between the two cores
        const mx = (srcCore.x + tgtCore.x) / 2;
        const my = (srcCore.y + tgtCore.y) / 2;
        const mz = (srcCore.z + tgtCore.z) / 2;

        // Camera sits on the same radial line as the midpoint, pulled back enough
        // to fit both clusters in frame (distance = separation + margin)
        const separation = Math.hypot(
          tgtCore.x - srcCore.x,
          tgtCore.y - srcCore.y,
          tgtCore.z - srcCore.z
        );
        const pullBack = Math.max(separation * 0.9, 200);
        const midMag = Math.hypot(mx || 1, my || 1, mz || 1);
        const ratio = 1 + pullBack / (midMag || 1);

        const lookAt = { x: mx, y: my, z: mz };

        graphRef.current.cameraPosition(
          { x: mx * ratio, y: my * ratio, z: mz * ratio },
          lookAt,
          2000
        );
      },

      resetCamera() {
        if (!graphRef.current) return;
        const data = graphRef.current.graphData() as { nodes: any[] };
        const positioned = data.nodes.filter((n: any) =>
          n.x != null && n.y != null && n.z != null
        );
        // Empty graph — fall back to a reasonable default.
        if (positioned.length === 0) {
          graphRef.current.cameraPosition({ x: 0, y: 0, z: 500 }, undefined, 1500);
          return;
        }

        let minX = Infinity, minY = Infinity, minZ = Infinity;
        let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
        for (const n of positioned) {
          if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
          if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
          if (n.z < minZ) minZ = n.z; if (n.z > maxZ) maxZ = n.z;
        }
        const cx = (minX + maxX) / 2;
        const cy = (minY + maxY) / 2;
        const cz = (minZ + maxZ) / 2;

        const bboxW = Math.max(maxX - minX, 1);
        const bboxH = Math.max(maxY - minY, 1);
        const bboxD = Math.max(maxZ - minZ, 1);

        const tanV = Math.tan((50 * Math.PI / 180) / 2);
        const cw = containerRef.current?.clientWidth ?? 1;
        const ch = Math.max(containerRef.current?.clientHeight ?? 1, 1);
        const tanH = tanV * (cw / ch);

        const distVert = (bboxH / 2) / tanV;
        const distHoriz = (bboxW / 2) / tanH;
        const distance = Math.max(distVert, distHoriz, 200) * 1.3 + bboxD / 2;

        graphRef.current.cameraPosition(
          { x: cx, y: cy, z: cz + distance },
          { x: cx, y: cy, z: cz },
          1500
        );
      },
    }));

    // ------------------------------------------------------------------
    // Mount: import and initialize 3d-force-graph (runs only once)
    // ------------------------------------------------------------------
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
          .nodeLabel((n: any) =>
            // Bounded + wrapped so long FACT text stays in the box (clamped to
            // 4 lines) instead of stretching the tooltip off screen.
            `<div style="max-width:340px;background:rgba(8,8,20,0.92);border:1px solid ${agentColor(n.agent_id)}55;border-radius:6px;color:#e4e4e7;font-size:12px;backdrop-filter:blur(6px);padding:4px 10px">
              <span style="color:${agentColor(n.agent_id)};font-weight:600;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word">${n.label}</span>
              <span style="color:#6b7280;margin-left:6px">${n.type}</span>
            </div>`
          )
          .nodeColor((n: any) => n.__color ?? agentColor(n.agent_id))
          .nodeVal((n: any) => n.__size ?? 5)
          .nodeOpacity(0.95)
          .linkColor((l: any) => l.__color ?? "rgba(80,80,120,0.3)")
          .linkWidth((l: any) => l.__width ?? 1)
          .linkOpacity(0.7)
          .linkDirectionalArrowLength((l: any) =>
            l.relation === "BELONGS_TO_AGENT" ? 0 : 3.5
          )
          .linkDirectionalArrowRelPos(0.88)
          .linkLabel((l: any) =>
            l.relation === "BELONGS_TO_AGENT" ? "" : l.relation
          )
          .linkDirectionalParticles((l: any) => l.__particleCount ?? 0)
          .linkDirectionalParticleSpeed(0.005)
          .linkDirectionalParticleWidth(2)
          .linkDirectionalParticleColor(
            (l: any) => l.__particleColor ?? "rgba(0,0,0,0)"
          )
          .onNodeClick((n: any) => {
            onNodeClick?.(n as GraphNode);
          })
          .graphData(buildGraphData());

        graphRef.current = graph;

        try {
          const composer = graph.postProcessingComposer();
          if (composer) {
            // strength / radius / threshold. Kept conservative: at high
            // strength + near-zero threshold the whole galaxy blows out to a
            // solid white blob once a few hundred nodes overlap. A higher
            // threshold means only the bright agent cores bloom.
            const bloom = new UnrealBloomPass(
              new THREE.Vector2(container.clientWidth, container.clientHeight),
              0.7,
              0.5,
              0.2
            );
            composer.addPass(bloom);
          }
        } catch {
          // Bloom not critical
        }

        const ro = new ResizeObserver(() => {
          graphRef.current
            ?.width(container.clientWidth)
            .height(container.clientHeight);
        });
        ro.observe(container);
      })();

      return () => {
        mountedRef.current = false;
        graphRef.current = null;
      };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // ------------------------------------------------------------------
    // Update graph data whenever any relevant state changes
    // ------------------------------------------------------------------
    // Rebuild graph data — and re-seed the layout — ONLY when the underlying
    // nodes/edges change. graphData() reheats the force simulation, so calling
    // it on a mere selection toggle made the layout drift away and never
    // settle (worse for small clusters, where the close camera magnifies it).
    useEffect(() => {
      if (graphRef.current) graphRef.current.graphData(buildGraphData());
    }, [nodes, edges, buildGraphData]);

    // Visual-only changes (selection / swarm / highlight) must NOT touch the
    // simulation. Recompute colours + sizes on a throwaway build, copy them
    // onto the live node/link objects by id (positions untouched), then
    // re-apply the accessors to repaint. No graphData() -> no reheat -> the
    // layout stays exactly where it settled.
    useEffect(() => {
      const g = graphRef.current;
      if (!g) return;
      const fresh = buildGraphData();
      const nById = new Map((fresh.nodes as any[]).map((n) => [n.id, n]));
      const lById = new Map((fresh.links as any[]).map((l) => [l.id, l]));
      const live = g.graphData();
      for (const n of live.nodes as any[]) {
        const f = nById.get(n.id);
        if (f) { n.__color = f.__color; n.__size = f.__size; }
      }
      for (const l of live.links as any[]) {
        const f = lById.get(l.id);
        if (f) {
          l.__color = f.__color;
          l.__width = f.__width;
          l.__particleColor = f.__particleColor;
          l.__particleCount = f.__particleCount;
        }
      }
      // Re-applying an accessor repaints that attribute without re-seeding.
      g.nodeColor(g.nodeColor()).nodeVal(g.nodeVal())
        .linkColor(g.linkColor()).linkWidth(g.linkWidth())
        .linkDirectionalParticles(g.linkDirectionalParticles())
        .linkDirectionalParticleColor(g.linkDirectionalParticleColor());
    }, [selectedAgentId, swarmFocus, highlightedIds, buildGraphData]);

    return (
      <div
        ref={containerRef}
        className="w-full h-full"
        style={{
          background:
            "radial-gradient(ellipse at 50% 35%, #0d0820 0%, #050508 70%)",
        }}
      />
    );
  }
);

MultiverseCanvas.displayName = "MultiverseCanvas";
export default MultiverseCanvas;
