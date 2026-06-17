"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Search, ChevronRight, Command, Zap } from "lucide-react";
import { useGraph } from "@/hooks/useGraph";
import { getAgents } from "@/lib/api";
import { useEngine } from "@/context/EngineContext";
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
  const { engineVersion, setEngineVersion, isLoading: engineLoading } = useEngine();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [cmdOpen, setCmdOpen] = useState(false);

  const page =
    Object.entries(PAGE_LABELS).find(([k]) => pathname.startsWith(k))?.[1] ??
    "Home";

  const isV2 = engineVersion === "v2";

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

  async function handleEngineToggle(version: "v1" | "v2") {
    if (version === engineVersion || engineLoading) return;
    await setEngineVersion(version);
  }

  return (
    <>
      <header
        className={`h-12 border-b flex items-center px-4 gap-4 flex-shrink-0 z-10 transition-all duration-500 ${
          isV2
            ? "bg-[#12121A] border-purple-500/30 shadow-[0_1px_20px_rgba(139,92,246,0.12)]"
            : "bg-[#12121A] border-[#2A2A35]"
        }`}
      >
        {/* Breadcrumb */}
        <div className="flex items-center gap-1.5 text-sm text-[#6B7280]">
          <span className="text-[#A1A1AA]">SpAIder</span>
          <ChevronRight className="w-3.5 h-3.5" />
          <span className="text-[#E4E4E7] font-medium">{page}</span>
        </div>

        <div className="flex-1" />

        {/* ── Engine Version Toggle ───────────────────────────────────── */}
        <div
          className={`flex items-center gap-1.5 rounded-lg border p-0.5 transition-all duration-500 ${
            isV2
              ? "border-purple-500/50 bg-purple-950/30 shadow-[0_0_15px_rgba(139,92,246,0.35)]"
              : "border-[#2A2A35] bg-[#1A1A25]"
          }`}
        >
          {/* V1 pill */}
          <button
            onClick={() => handleEngineToggle("v1")}
            disabled={engineLoading}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-all duration-300 ${
              !isV2
                ? "bg-[#2A2A35] text-[#E4E4E7]"
                : "text-[#6B7280] hover:text-[#A1A1AA]"
            }`}
          >
            Engine V1
          </button>

          {/* V2 pill */}
          <button
            onClick={() => handleEngineToggle("v2")}
            disabled={engineLoading}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-all duration-300 ${
              isV2
                ? "bg-purple-600 text-white shadow-[0_0_10px_rgba(139,92,246,0.5)]"
                : "text-[#6B7280] hover:text-[#A1A1AA]"
            }`}
          >
            {isV2 && (
              <Zap
                className="w-3 h-3 animate-pulse"
                fill="currentColor"
              />
            )}
            {engineLoading ? (
              <span className="w-3 h-3 rounded-full border border-purple-400/40 border-t-purple-400 animate-spin inline-block" />
            ) : null}
            Engine V2
          </button>
        </div>

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
