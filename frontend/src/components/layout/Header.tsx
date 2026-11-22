"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Search, ChevronRight, Command } from "lucide-react";
import { useGraph } from "@/hooks/useGraph";
import CommandPalette from "./CommandPalette";

const PAGE_LABELS: Record<string, string> = {
  "/studio": "Neural Studio",
  "/agents": "Agents",
  "/synthesizer": "Synthesizer",
  "/settings": "Settings",
};

export default function Header() {
  const pathname = usePathname();
  const agentId = useGraph((s) => s.agentId);
  const setAgentId = useGraph((s) => s.setAgentId);
  const agents = useGraph((s) => s.agents);
  const loadAgents = useGraph((s) => s.loadAgents);
  const [cmdOpen, setCmdOpen] = useState(false);

  const page =
    Object.entries(PAGE_LABELS).find(([k]) => pathname.startsWith(k))?.[1] ??
    "Home";

  useEffect(() => {
    void loadAgents();
  }, [loadAgents]);

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
        <div className="flex items-center gap-1.5 text-sm text-[#8B93A3]">
          <span className="text-[#A1A1AA]">SpAIder</span>
          <ChevronRight className="w-3.5 h-3.5" />
          <span className="text-[#E4E4E7] font-medium">{page}</span>
        </div>

        <div className="flex-1" />

        {/* The one place to choose which knowledge base the whole app looks at. */}
        {agents.length > 1 ? (
          <label className="flex items-center gap-2 text-xs text-[#8B93A3]">
            <span className="hidden sm:inline">Knowledge base</span>
            <select
              aria-label="Knowledge base"
              value={agentId ?? ""}
              onChange={(e) => setAgentId(e.target.value || null)}
              className="bg-[#1A1A25] border border-[#2A2A35] text-[#E4E4E7] text-xs rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-[#3B82F6]/50"
            >
              <option value="">All knowledge bases</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
        ) : agents.length === 1 ? (
          <div className="flex items-center gap-1.5 text-xs text-[#8B93A3]">
            <span className="text-[#A1A1AA]">Knowledge base:</span>
            <span className="text-[#3B82F6]">{agents[0].name}</span>
          </div>
        ) : null}

        {/* Search / Command palette trigger */}
        <button
          onClick={() => setCmdOpen(true)}
          className="hidden md:flex items-center gap-2 bg-[#1A1A25] hover:bg-[#2A2A35] border border-[#2A2A35] rounded-lg px-3 py-1.5 text-xs text-[#8B93A3] hover:text-[#A1A1AA] transition-colors"
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
