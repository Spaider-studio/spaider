"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Network, Users, Database, Settings, Search, ArrowRight } from "lucide-react";
import { useGraph } from "@/hooks/useGraph";
import { truncate } from "@/lib/utils";
import { NODE_TYPE_COLORS } from "@/lib/constants";

interface CommandItem {
  id: string;
  label: string;
  description?: string;
  icon: React.ReactNode;
  onSelect: () => void;
  category: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function CommandPalette({ open, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const router = useRouter();
  const { nodes, setSelectedNode } = useGraph();

  const navigate = useCallback(
    (href: string) => {
      router.push(href);
      onClose();
    },
    [router, onClose]
  );

  const pages: CommandItem[] = [
    {
      id: "studio",
      label: "Neural Studio",
      description: "3D Knowledge Graph visualization",
      icon: <Network className="w-4 h-4 text-[#3B82F6]" />,
      onSelect: () => navigate("/studio"),
      category: "Pages",
    },
    {
      id: "agents",
      label: "Agents",
      description: "Manage AI agents and API keys",
      icon: <Users className="w-4 h-4 text-[#8B5CF6]" />,
      onSelect: () => navigate("/agents"),
      category: "Pages",
    },
    {
      id: "synthesizer",
      label: "Synthesizer",
      description: "Generate training datasets",
      icon: <Database className="w-4 h-4 text-[#10B981]" />,
      onSelect: () => navigate("/synthesizer"),
      category: "Pages",
    },
    {
      id: "settings",
      label: "Settings",
      description: "API keys, GDPR, swarm connections",
      icon: <Settings className="w-4 h-4 text-[#A1A1AA]" />,
      onSelect: () => navigate("/settings"),
      category: "Pages",
    },
  ];

  const nodeItems: CommandItem[] = nodes
    .filter(
      (n) =>
        !query ||
        n.label.toLowerCase().includes(query.toLowerCase()) ||
        n.type.toLowerCase().includes(query.toLowerCase())
    )
    .slice(0, 8)
    .map((n) => ({
      id: `node-${n.id}`,
      label: n.label,
      description: n.type,
      icon: (
        <div
          className="w-4 h-4 rounded-full flex-shrink-0"
          style={{ background: NODE_TYPE_COLORS[n.type] ?? "#6B7280" }}
        />
      ),
      onSelect: () => {
        navigate("/studio");
        setSelectedNode(n);
      },
      category: "Nodes",
    }));

  const filteredPages = query
    ? pages.filter(
        (p) =>
          p.label.toLowerCase().includes(query.toLowerCase()) ||
          (p.description ?? "").toLowerCase().includes(query.toLowerCase())
      )
    : pages;

  const allItems = [...filteredPages, ...nodeItems];

  useEffect(() => {
    setSelected(0);
  }, [query]);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setSelected(0);
    }
  }, [open]);

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((v) => Math.min(v + 1, allItems.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((v) => Math.max(v - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      allItems[selected]?.onSelect();
    } else if (e.key === "Escape") {
      onClose();
    }
  }

  if (!open) return null;

  const grouped = allItems.reduce<Record<string, CommandItem[]>>((acc, item) => {
    if (!acc[item.category]) acc[item.category] = [];
    acc[item.category].push(item);
    return acc;
  }, {});

  let globalIndex = 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-lg bg-[#12121A] border border-[#2A2A35] rounded-2xl shadow-2xl overflow-hidden animate-fade-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search Input */}
        <div className="flex items-center gap-3 px-4 py-3.5 border-b border-[#2A2A35]">
          <Search className="w-4 h-4 text-[#A1A1AA] flex-shrink-0" />
          <input
            autoFocus
            className="flex-1 bg-transparent text-[#E4E4E7] placeholder-[#6B7280] text-sm focus:outline-none"
            placeholder="Search pages, nodes, actions..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <kbd className="px-2 py-0.5 text-[10px] text-[#6B7280] bg-[#1A1A25] border border-[#2A2A35] rounded font-mono">
            ESC
          </kbd>
        </div>

        {/* Results */}
        <div className="max-h-[400px] overflow-y-auto py-2">
          {allItems.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-[#6B7280]">
              No results found for &quot;{query}&quot;
            </div>
          ) : (
            Object.entries(grouped).map(([category, items]) => (
              <div key={category}>
                <div className="px-4 py-2 text-[10px] font-semibold text-[#6B7280] uppercase tracking-wider">
                  {category}
                </div>
                {items.map((item) => {
                  const idx = globalIndex++;
                  const isSelected = idx === selected;
                  return (
                    <button
                      key={item.id}
                      className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors ${
                        isSelected
                          ? "bg-[#3B82F6]/10 text-[#E4E4E7]"
                          : "text-[#A1A1AA] hover:bg-[#1A1A25] hover:text-[#E4E4E7]"
                      }`}
                      onClick={item.onSelect}
                      onMouseEnter={() => setSelected(idx)}
                    >
                      <span className="flex-shrink-0">{item.icon}</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium truncate">
                          {item.label}
                        </div>
                        {item.description && (
                          <div className="text-xs text-[#6B7280] truncate">
                            {truncate(item.description, 50)}
                          </div>
                        )}
                      </div>
                      {isSelected && (
                        <ArrowRight className="w-3.5 h-3.5 text-[#3B82F6] flex-shrink-0" />
                      )}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-2 border-t border-[#2A2A35] flex items-center gap-4 text-[10px] text-[#6B7280]">
          <span>
            <kbd className="font-mono">↑↓</kbd> navigate
          </span>
          <span>
            <kbd className="font-mono">↵</kbd> select
          </span>
          <span>
            <kbd className="font-mono">esc</kbd> close
          </span>
          <span className="ml-auto">{allItems.length} results</span>
        </div>
      </div>
    </div>
  );
}
