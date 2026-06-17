"use client";

import { useState, useEffect } from "react";
import { Network, ChevronRight, Eye, Cpu, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import { listSwarmLinks } from "@/lib/api";
import type { SwarmLink } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AgentCluster {
  agentId: string;
  name: string;
  nodeCount: number;
  edgeCount: number;
  color: string;
}

export interface SelectedSwarm {
  source_id: string;
  target_id: string;
}

interface Props {
  agents: AgentCluster[];
  selectedAgentId: string | null;
  selectedSwarm: SelectedSwarm | null;
  totalNodes: number;
  totalEdges: number;
  onSelectAgent: (agentId: string | null) => void;
  onSelectSwarm: (swarm: SelectedSwarm | null) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AgentSidebar({
  agents,
  selectedAgentId,
  selectedSwarm,
  totalNodes,
  totalEdges,
  onSelectAgent,
  onSelectSwarm,
}: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const [swarmLinks, setSwarmLinks] = useState<SwarmLink[]>([]);

  useEffect(() => {
    listSwarmLinks()
      .then(setSwarmLinks)
      .catch(() => setSwarmLinks([]));
  }, []);

  function handleAgentClick(agentId: string) {
    onSelectSwarm(null);
    onSelectAgent(selectedAgentId === agentId ? null : agentId);
  }

  function handleAllAgents() {
    onSelectSwarm(null);
    onSelectAgent(null);
  }

  function handleSwarmClick(link: SwarmLink) {
    const isCurrent =
      selectedSwarm?.source_id === link.source_id &&
      selectedSwarm?.target_id === link.target_id;
    onSelectAgent(null);
    onSelectSwarm(isCurrent ? null : { source_id: link.source_id, target_id: link.target_id });
  }

  const noFocus = !selectedAgentId && !selectedSwarm;

  return (
    <div
      className={cn(
        "flex flex-col bg-black/60 backdrop-blur-xl border-r border-white/8 transition-all duration-300 h-full",
        collapsed ? "w-12" : "w-64"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-white/8 min-h-[52px]">
        {!collapsed && (
          <div className="flex items-center gap-2">
            <Network className="w-4 h-4 text-violet-400 flex-shrink-0" />
            <span className="text-xs font-semibold text-white/70 uppercase tracking-widest">
              Neural Agents
            </span>
          </div>
        )}
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="ml-auto p-1 rounded hover:bg-white/10 text-white/40 hover:text-white/80 transition-colors"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <ChevronRight
            className={cn(
              "w-4 h-4 transition-transform",
              !collapsed && "rotate-180"
            )}
          />
        </button>
      </div>

      {!collapsed && (
        <>
          {/* Totals */}
          <div className="px-3 py-2.5 border-b border-white/5 flex gap-4">
            <Stat label="Nodes" value={totalNodes} />
            <Stat label="Edges" value={totalEdges} />
            <Stat label="Agents" value={agents.length} />
          </div>

          {/* "All Agents" reset row */}
          <button
            onClick={handleAllAgents}
            className={cn(
              "flex items-center gap-2.5 px-3 py-2.5 text-sm transition-colors border-b border-white/5",
              noFocus
                ? "bg-violet-600/20 text-violet-300"
                : "text-white/50 hover:text-white/80 hover:bg-white/5"
            )}
          >
            <Eye className="w-3.5 h-3.5 flex-shrink-0" />
            <span className="font-medium">All Agents</span>
            {noFocus && (
              <span className="ml-auto w-1.5 h-1.5 rounded-full bg-violet-400" />
            )}
          </button>

          {/* Scrollable body: agents + swarms */}
          <div className="flex-1 overflow-y-auto">
            {/* ── Agent list ── */}
            <div className="py-1">
              {agents.length === 0 ? (
                <div className="px-3 py-6 text-center text-white/20 text-xs">
                  No agents yet.
                  <br />
                  Create one to start ingesting.
                </div>
              ) : (
                agents.map((agent) => (
                  <AgentRow
                    key={agent.agentId}
                    agent={agent}
                    isSelected={selectedAgentId === agent.agentId}
                    onClick={() => handleAgentClick(agent.agentId)}
                  />
                ))
              )}
            </div>

            {/* ── Active Swarms section ── */}
            {swarmLinks.length > 0 && (
              <div className="border-t border-white/5 pt-1 pb-2">
                <div className="flex items-center gap-1.5 px-3 py-2">
                  <Zap className="w-3 h-3 text-cyan-400/70" />
                  <span className="text-[10px] font-semibold text-white/30 uppercase tracking-widest">
                    Active Swarms
                  </span>
                </div>
                {swarmLinks.map((link) => {
                  const isActive =
                    selectedSwarm?.source_id === link.source_id &&
                    selectedSwarm?.target_id === link.target_id;
                  return (
                    <SwarmLinkRow
                      key={`${link.source_id}->${link.target_id}`}
                      link={link}
                      isActive={isActive}
                      onClick={() => handleSwarmClick(link)}
                    />
                  );
                })}
              </div>
            )}
          </div>
        </>
      )}

      {/* Collapsed — show coloured dots only */}
      {collapsed && (
        <div className="flex flex-col items-center gap-2 py-3">
          <button
            onClick={handleAllAgents}
            className="w-7 h-7 rounded-full flex items-center justify-center hover:bg-white/10 transition-colors"
            title="All Agents"
          >
            <Eye className="w-3.5 h-3.5 text-white/40" />
          </button>
          {agents.map((agent) => (
            <button
              key={agent.agentId}
              onClick={() => handleAgentClick(agent.agentId)}
              className={cn(
                "w-7 h-7 rounded-full border-2 transition-all hover:scale-110",
                selectedAgentId === agent.agentId
                  ? "border-white/70 shadow-lg"
                  : "border-transparent"
              )}
              style={{ backgroundColor: agent.color + "66" }}
              title={agent.name}
            >
              <span
                className="block w-2.5 h-2.5 rounded-full mx-auto"
                style={{ backgroundColor: agent.color }}
              />
            </button>
          ))}
          {swarmLinks.length > 0 && (
            <div className="w-full border-t border-white/8 mt-1 pt-2 flex flex-col items-center gap-1.5">
              {swarmLinks.map((link) => {
                const isActive =
                  selectedSwarm?.source_id === link.source_id &&
                  selectedSwarm?.target_id === link.target_id;
                return (
                  <button
                    key={`${link.source_id}->${link.target_id}`}
                    onClick={() => handleSwarmClick(link)}
                    className={cn(
                      "w-7 h-7 rounded-full flex items-center justify-center border transition-all hover:scale-110",
                      isActive
                        ? "border-cyan-400/70 bg-cyan-400/15 shadow-[0_0_8px_2px_rgba(6,182,212,0.3)]"
                        : "border-white/10 hover:border-cyan-400/30 hover:bg-white/5"
                    )}
                    title={`${link.source_name} ➔ ${link.target_name}`}
                  >
                    <Zap className="w-3 h-3 text-cyan-400/70" />
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function AgentRow({
  agent,
  isSelected,
  onClick,
}: {
  agent: AgentCluster;
  isSelected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-2.5 px-3 py-2.5 text-sm transition-colors text-left",
        isSelected
          ? "bg-white/8 text-white"
          : "text-white/55 hover:text-white/85 hover:bg-white/5"
      )}
    >
      <span
        className="flex-shrink-0 w-2.5 h-2.5 rounded-full ring-2 ring-offset-0 transition-all"
        style={{
          backgroundColor: agent.color,
          boxShadow: isSelected ? `0 0 8px 2px ${agent.color}66` : "none",
        }}
      />
      <Cpu className="w-3 h-3 flex-shrink-0 opacity-50" />
      <div className="flex-1 min-w-0">
        <div className="truncate font-medium text-xs leading-tight">
          {agent.name}
        </div>
        <div className="text-white/30 text-[10px] mt-0.5">
          {agent.nodeCount}n · {agent.edgeCount}e
        </div>
      </div>
      {isSelected && (
        <span
          className="w-1.5 h-1.5 rounded-full flex-shrink-0"
          style={{ backgroundColor: agent.color }}
        />
      )}
    </button>
  );
}

function SwarmLinkRow({
  link,
  isActive,
  onClick,
}: {
  link: SwarmLink;
  isActive: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-1.5 px-3 py-2 text-left transition-all",
        isActive
          ? "bg-cyan-500/10 text-cyan-200 border-l-2 border-cyan-400/60"
          : "text-white/40 hover:text-white/75 hover:bg-white/4 border-l-2 border-transparent"
      )}
    >
      <Zap
        className={cn(
          "w-2.5 h-2.5 flex-shrink-0",
          isActive ? "text-cyan-400" : "text-white/20"
        )}
      />
      <span className="text-[10px] font-medium truncate leading-snug">
        {link.source_name}
        <span className="mx-1 text-cyan-400/60">➔</span>
        {link.target_name}
      </span>
      {isActive && (
        <span className="ml-auto w-1.5 h-1.5 rounded-full bg-cyan-400 flex-shrink-0 shadow-[0_0_4px_2px_rgba(6,182,212,0.4)]" />
      )}
    </button>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] text-white/30 uppercase tracking-wider">
        {label}
      </span>
      <span className="text-sm font-mono text-white/70">{value}</span>
    </div>
  );
}
