"use client";

import { useState } from "react";
import { SlidersHorizontal, X } from "lucide-react";
import { ALL_NODE_TYPES, NODE_TYPE_COLORS } from "@/lib/constants";

interface Filters {
  nodeTypes: Set<string>;
  minConfidence: number;
  search: string;
}

interface Props {
  onFiltersChange: (filters: Filters) => void;
}

export default function FilterBar({ onFiltersChange }: Props) {
  const [nodeTypes, setNodeTypes] = useState<Set<string>>(new Set(ALL_NODE_TYPES));
  const [minConfidence, setMinConfidence] = useState(0);
  const [search, setSearch] = useState("");

  function toggleType(type: string) {
    const next = new Set(nodeTypes);
    if (next.has(type)) {
      next.delete(type);
    } else {
      next.add(type);
    }
    setNodeTypes(next);
    onFiltersChange({ nodeTypes: next, minConfidence, search });
  }

  function handleSearch(value: string) {
    setSearch(value);
    onFiltersChange({ nodeTypes, minConfidence, search: value });
  }

  function reset() {
    const full = new Set(ALL_NODE_TYPES);
    setNodeTypes(full);
    setMinConfidence(0);
    setSearch("");
    onFiltersChange({ nodeTypes: full, minConfidence: 0, search: "" });
  }

  const isFiltered = nodeTypes.size < ALL_NODE_TYPES.length || minConfidence > 0 || search;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-1.5">
          <SlidersHorizontal className="w-3 h-3" />
          Filters
        </h3>
        {isFiltered && (
          <button
            onClick={reset}
            className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1 transition-colors"
          >
            <X className="w-3 h-3" />
            Reset
          </button>
        )}
      </div>

      {/* Search */}
      <input
        className="w-full bg-[#12121A] border border-[#2A2A35] rounded-lg px-3 py-1.5 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-accent-blue transition-colors"
        placeholder="Search nodes..."
        value={search}
        onChange={(e) => handleSearch(e.target.value)}
      />

      {/* Node type toggles */}
      <div className="flex flex-wrap gap-1.5">
        {ALL_NODE_TYPES.map((type) => {
          const color = NODE_TYPE_COLORS[type] ?? "#6B7280";
          const active = nodeTypes.has(type);
          return (
            <button
              key={type}
              onClick={() => toggleType(type)}
              className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs transition-all"
              style={{
                backgroundColor: active ? `${color}22` : "transparent",
                color: active ? color : "#52525b",
                border: `1px solid ${active ? `${color}44` : "#2A2A35"}`,
              }}
            >
              <span
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: active ? color : "#52525b" }}
              />
              {type}
            </button>
          );
        })}
      </div>
    </div>
  );
}
