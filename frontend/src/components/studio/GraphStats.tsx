"use client";

import { useGraph } from "@/hooks/useGraph";
import { NODE_TYPE_COLORS } from "@/lib/constants";
import { formatNumber } from "@/lib/utils";

export default function GraphStats() {
  const { nodes, edges, stats } = useGraph();

  const typeCounts = nodes.reduce<Record<string, number>>((acc, n) => {
    acc[n.type] = (acc[n.type] ?? 0) + 1;
    return acc;
  }, {});

  const topTypes = Object.entries(typeCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  const maxCount = topTypes[0]?.[1] ?? 1;

  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
        Graph Stats
      </h3>

      <div className="grid grid-cols-2 gap-2">
        <div className="bg-[#12121A] border border-[#2A2A35] rounded-lg p-3 text-center">
          <div className="text-2xl font-bold text-accent-blue">{formatNumber(nodes.length)}</div>
          <div className="text-xs text-gray-500 mt-0.5">Nodes</div>
        </div>
        <div className="bg-[#12121A] border border-[#2A2A35] rounded-lg p-3 text-center">
          <div className="text-2xl font-bold text-accent-green">{formatNumber(edges.length)}</div>
          <div className="text-xs text-gray-500 mt-0.5">Edges</div>
        </div>
      </div>

      {topTypes.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {topTypes.map(([type, count]) => {
            const color = NODE_TYPE_COLORS[type] ?? "#6B7280";
            const pct = (count / maxCount) * 100;
            return (
              <div key={type} className="flex items-center gap-2">
                <span className="text-xs text-gray-400 w-20 truncate">{type}</span>
                <div className="flex-1 bg-[#0A0A0F] rounded-full h-1.5 overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${pct}%`, backgroundColor: color }}
                  />
                </div>
                <span className="text-xs text-gray-500 w-6 text-right">{count}</span>
              </div>
            );
          })}
        </div>
      )}

      {stats && (
        <div className="text-xs text-gray-600 text-right">
          density: {(stats.density * 100).toFixed(3)}%
        </div>
      )}
    </div>
  );
}
