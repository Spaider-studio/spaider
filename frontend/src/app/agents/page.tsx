"use client";

import { useEffect, useState } from "react";
import {
  getAgents,
  createAgent,
  deleteAgent,
  rotateAgentKey,
  listSwarmConnections,
} from "@/lib/api";
import type { Agent, SwarmConnection } from "@/lib/types";
import {
  Users,
  Plus,
  Trash2,
  Copy,
  RefreshCw,
  CheckCircle2,
  Key,
  Network,
  Link,
} from "lucide-react";
import { useToast } from "@/components/shared/Toast";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import KeyRevealDialog from "@/components/shared/KeyRevealDialog";
import { formatDate, maskApiKey } from "@/lib/utils";
import LoadingSpinner from "@/components/shared/LoadingSpinner";
import Sidebar from "@/components/layout/Sidebar";
import Header from "@/components/layout/Header";
import AgentDetail from "@/components/agents/AgentDetail";
import AgentRegistrationForm from "@/components/agents/AgentRegistrationForm";
import SwarmConnectionView from "@/components/agents/SwarmConnectionView";
import SwarmConnectionManager from "@/components/agents/SwarmConnectionManager";

export default function AgentsPage() {
  const { toast } = useToast();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [connections, setConnections] = useState<SwarmConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Agent | null>(null);
  const [rotateTarget, setRotateTarget] = useState<Agent | null>(null);
  const [rotating, setRotating] = useState(false);
  const [revealed, setRevealed] = useState<{ agent: Agent; key: string } | null>(null);
  const [activeTab, setActiveTab] = useState<"agents" | "swarm">("agents");
  const [bridgeRefreshTick, setBridgeRefreshTick] = useState(0);

  async function load() {
    setLoading(true);
    try {
      const [agentsData, connsData] = await Promise.allSettled([
        getAgents(),
        listSwarmConnections(),
      ]);
      if (agentsData.status === "fulfilled") setAgents(agentsData.value);
      if (connsData.status === "fulfilled") setConnections(connsData.value);
    } catch {
      toast("Failed to load agents", "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      await deleteAgent(deleteTarget.id);
      setAgents((prev) => prev.filter((a) => a.id !== deleteTarget.id));
      toast(`Agent "${deleteTarget.name}" deleted`, "success");
    } catch {
      toast("Failed to delete agent", "error");
    } finally {
      setDeleteTarget(null);
    }
  }

  async function handleRotate() {
    if (!rotateTarget) return;
    setRotating(true);
    const target = rotateTarget;
    try {
      const res = await rotateAgentKey(target.id);
      // Surface the raw key inline on the card (create-flow parity) and
      // open the one-time-reveal modal.
      setAgents((prev) =>
        prev.map((a) =>
          a.id === target.id ? { ...a, api_key: res.api_key } : a
        )
      );
      setRevealed({ agent: { ...target, api_key: res.api_key }, key: res.api_key });
      setRotateTarget(null);
      toast(`Rotated key for "${target.name}"`, "success");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to rotate key";
      toast(msg, "error");
    } finally {
      setRotating(false);
    }
  }

  function handleAgentCreated(agent: Agent) {
    setAgents((prev) => [agent, ...prev]);
    setShowForm(false);
    toast(`Agent "${agent.name}" created`, "success");
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#0A0A0F]">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Header />

        <div className="flex-1 overflow-y-auto p-6">
          <div className="max-w-5xl mx-auto">
            {/* Page header */}
            <div className="flex items-center justify-between mb-6">
              <div>
                <h1 className="text-2xl font-bold text-[#E4E4E7] mb-1">
                  Agents
                </h1>
                <p className="text-sm text-[#A1A1AA]">
                  Manage isolated AI agents with independent knowledge graphs
                </p>
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={load}
                  disabled={loading}
                  className="flex items-center gap-2 px-3 py-2 bg-[#1A1A25] border border-[#2A2A35] rounded-lg text-xs text-[#A1A1AA] hover:text-[#E4E4E7] transition-colors disabled:opacity-50"
                >
                  <RefreshCw
                    className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`}
                  />
                </button>
                <button
                  onClick={() => setShowForm(true)}
                  className="flex items-center gap-2 px-4 py-2 bg-[#3B82F6] hover:bg-[#2563EB] text-white text-sm font-medium rounded-lg transition-colors"
                >
                  <Plus className="w-4 h-4" />
                  New Agent
                </button>
              </div>
            </div>

            {/* Tabs */}
            <div className="flex items-center gap-1 bg-[#12121A] border border-[#2A2A35] rounded-lg p-1 mb-6 w-fit">
              <button
                onClick={() => setActiveTab("agents")}
                className={`flex items-center gap-2 px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
                  activeTab === "agents"
                    ? "bg-[#1A1A25] text-[#E4E4E7] border border-[#2A2A35]"
                    : "text-[#A1A1AA] hover:text-[#E4E4E7]"
                }`}
              >
                <Users className="w-3.5 h-3.5" />
                Agents ({agents.length})
              </button>
              <button
                onClick={() => setActiveTab("swarm")}
                className={`flex items-center gap-2 px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
                  activeTab === "swarm"
                    ? "bg-[#1A1A25] text-[#E4E4E7] border border-[#2A2A35]"
                    : "text-[#A1A1AA] hover:text-[#E4E4E7]"
                }`}
              >
                <Network className="w-3.5 h-3.5" />
                Swarm ({connections.length})
              </button>
            </div>

            {/* Agent Registration Form Modal */}
            {showForm && (
              <AgentRegistrationForm
                onSuccess={handleAgentCreated}
                onClose={() => setShowForm(false)}
              />
            )}

            {activeTab === "agents" && (
              <>
                {loading ? (
                  <div className="flex justify-center py-20">
                    <LoadingSpinner size="lg" />
                  </div>
                ) : agents.length === 0 ? (
                  <div className="text-center py-20">
                    <div className="w-14 h-14 rounded-xl bg-[#12121A] border border-[#2A2A35] flex items-center justify-center mx-auto mb-4">
                      <Users className="w-7 h-7 text-[#6B7280]" />
                    </div>
                    <p className="text-[#A1A1AA] text-sm mb-1">
                      No agents registered
                    </p>
                    <p className="text-[#6B7280] text-xs">
                      Create your first agent to get started
                    </p>
                  </div>
                ) : (
                  <div className="flex flex-col gap-3">
                    {agents.map((agent) => (
                      <AgentDetail
                        key={agent.id}
                        agent={agent}
                        onDelete={() => setDeleteTarget(agent)}
                        onRotate={() => setRotateTarget(agent)}
                      />
                    ))}
                  </div>
                )}
              </>
            )}

            {activeTab === "swarm" && (
              <div className="flex flex-col gap-6">
                <SwarmConnectionView
                  agents={agents}
                  connections={connections}
                  onRefresh={() => {
                    load();
                    setBridgeRefreshTick((t) => t + 1);
                  }}
                />
                <SwarmConnectionManager refreshTrigger={bridgeRefreshTick} />
              </div>
            )}
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title={`Delete "${deleteTarget?.name}"?`}
        description="This will permanently delete the agent and all its associated knowledge graph data. This action cannot be undone."
        confirmLabel="Delete Agent"
        onConfirm={handleDelete}
        destructive
      />

      <ConfirmDialog
        open={!!rotateTarget}
        onOpenChange={(open) => {
          if (!open && !rotating) setRotateTarget(null);
        }}
        title={`Rotate API key for "${rotateTarget?.name}"?`}
        description="This invalidates the current API key immediately. The agent's knowledge graph is preserved — only the credential changes. Any client still using the old key will stop working."
        confirmLabel="Rotate Key"
        onConfirm={handleRotate}
        variant="warning"
        loading={rotating}
      />

      <KeyRevealDialog
        open={!!revealed}
        onClose={() => setRevealed(null)}
        title="New API key"
        apiKey={revealed?.key ?? ""}
        agentName={revealed?.agent.name}
      />
    </div>
  );
}
