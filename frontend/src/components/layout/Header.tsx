"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Search, ChevronRight, Command } from "lucide-react";
import { useGraph } from "@/hooks/useGraph";
import { getAgents } from "@/lib/api";
import type { Agent } from "@/lib/types";
import CommandPalette from "./CommandPalette";

const PAGE_LABELS: Record<string, string> = {
  "/studio": "Neural Studio",
  "/agents": "Agents",
  "/synthesizer": "Synthesizer",
  "/settings": "Settings",
};

export default function Header() {
  const pathname = usePathname();
  const { agentId, setAgentId, nodes, edges } = useGraph();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [cmdOpen, setCmdOpen] = useState(false);

  const page =
    Object.entries(PAGE_LABELS).find(([k]) => pathname.startsWith(k))?.[1] ??
    "Home";

  useEffect(() => {
    getAgents()
      .then(setAgents)
      .catch(() => {});
  }, []);

  // Global Cmd+K / Ctrl+K listener
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setCmdOpen((v) => !v);
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  return (
    <>
      <header className="h-12 border-b border-[#2A2A35] bg-[#12121A] flex items-center px-4 gap-4 flex-shrink-0 z-10">
        {/* Breadcrumb */}
        <div className="flex items-center gap-1.5 text-sm text-[#6B7280]">
          <span className="text-[#A1A1AA]">SpAIder</span>
          <ChevronRight className="w-3.5 h-3.5" />
          <span className="text-[#E4E4E7] font-medium">{page}</span>
        </div>

        <div className="flex-1" />

        {/* Agent selector */}
        {agents.length > 1 ? (
          <select
            value={agentId ?? ""}
            onChange={(e) => setAgentId(e.target.value)}
            className="bg-[#1A1A25] border border-[#2A2A35] text-[#E4E4E7] text-xs rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-[#3B82F6] transition-colors cursor-pointer"
          >
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        ) : (
          <div className="flex items-center gap-1.5 text-xs text-[#6B7280]">
            <span className="text-[#A1A1AA]">Agent:</span>
            <span className="font-mono text-[#3B82F6]">{agentId}</span>
          </div>
        )}

        {/* Stats */}
        <div className="hidden sm:flex items-center gap-1 text-xs text-[#6B7280]">
          <span className="text-[#3B82F6] font-medium">{nodes.length}</span>
          <span>N</span>
          <span className="text-[#2A2A35] mx-0.5">/</span>
          <span className="text-[#10B981] font-medium">{edges.length}</span>
          <span>E</span>
        </div>

        {/* Search / Command palette trigger */}
        <button
          onClick={() => setCmdOpen(true)}
          className="hidden md:flex items-center gap-2 bg-[#1A1A25] hover:bg-[#2A2A35] border border-[#2A2A35] rounded-lg px-3 py-1.5 text-xs text-[#6B7280] hover:text-[#A1A1AA] transition-colors"
        >
          <Search className="w-3.5 h-3.5" />
          <span>Search</span>
          <div className="flex items-center gap-0.5">
            <Command className="w-3 h-3" />
            <span>K</span>
          </div>
        </button>
      </header>

      <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} />
    </>
  );
}
