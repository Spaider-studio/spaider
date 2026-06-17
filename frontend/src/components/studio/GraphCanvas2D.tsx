"use client";

import { useEffect, useRef, useCallback } from "react";
import type { GraphNode, GraphEdge } from "@/lib/types";
import { NODE_TYPE_COLORS } from "@/lib/constants";
import { truncate } from "@/lib/utils";

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onNodeClick?: (node: GraphNode) => void;
  highlightedIds?: Set<string>;
}

// Match the multiverse 3D palette so an agent has the same colour in both views.
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

export default function GraphCanvas2D({
  nodes,
  edges,
  onNodeClick,
  highlightedIds = new Set(),
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  const buildData = useCallback(() => ({
    nodes: nodes.map((n) => {
      const isCore = n.type === "agent_core";
      // CLUSTER nodes carry their original type in properties — use that for
      // the palette lookup so each cluster sphere gets its type's colour.
      const paletteType =
        n.type === "CLUSTER"
          ? (n.properties as { original_type?: string } | undefined)?.original_type ?? "OTHER"
          : n.type;
      const baseColor = isCore
        ? agentColor(n.agent_id)
        : (NODE_TYPE_COLORS[paletteType] ?? NODE_TYPE_COLORS.OTHER ?? "#6B7280");
      // agent_core gets a fixed, modest size — it's connected to every node in
      // its agent (via BELONGS_TO_AGENT) so the connection-count formula would
      // make it dwarf the canvas. Other nodes scale with their real connections,
      // capped so a single hub can't dominate.
      const connections = edges.filter((e) => e.source === n.id || e.target === n.id).length;
      const size = isCore ? 10 : Math.min(5 + connections * 0.5, 16);
      return {
        ...n,
        __color: highlightedIds.size === 0 || highlightedIds.has(n.id)
          ? baseColor
          : "#1f2937",
        __size: size,
      };
    }),
    links: edges.map((e) => ({
      ...e,
      source: e.source,
      target: e.target,
    })),
  }), [nodes, edges, highlightedIds]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let mounted = true;

    import("react-force-graph-2d").then((mod) => {
      if (!mounted || !container) return;
      const ForceGraph2D = mod.default;
      const { createRoot } = require("react-dom/client");

      container.innerHTML = "";
      const elem = document.createElement("div");
      container.appendChild(elem);
      const root = createRoot(elem);

      root.render(
        <ForceGraph2D
          graphData={buildData()}
          width={container.clientWidth}
          height={container.clientHeight}
          backgroundColor="#0A0A0F"
          nodeLabel={(n: any) => `${n.label} (${n.type})`}
          nodeColor={(n: any) => n.__color}
          nodeVal={(n: any) => n.__size}
          linkColor={() => "rgba(107,114,128,0.5)"}
          linkDirectionalArrowLength={4}
          linkDirectionalArrowRelPos={0.9}
          linkLabel={(l: any) => l.relation}
          onNodeClick={(n: any) => onNodeClick?.(n as GraphNode)}
          nodeCanvasObject={(node: any, ctx: CanvasRenderingContext2D, scale: number) => {
            ctx.beginPath();
            ctx.arc(node.x, node.y, node.__size, 0, 2 * Math.PI);
            ctx.fillStyle = node.__color;
            ctx.fill();
            if (scale >= 1.8) {
              const fontSize = Math.max(10 / scale, 2);
              ctx.font = `${fontSize}px Inter, sans-serif`;
              ctx.fillStyle = "#e4e4e7";
              ctx.textAlign = "center";
              // Truncate so long FACT labels don't bleed sideways across the canvas.
              ctx.fillText(truncate(node.label, 28), node.x, node.y + node.__size + fontSize + 1);
            }
          }}
        />
      );
    });

    return () => { mounted = false; };
  }, [nodes, edges, highlightedIds]); // eslint-disable-line

  return <div ref={containerRef} className="w-full h-full" />;
}
