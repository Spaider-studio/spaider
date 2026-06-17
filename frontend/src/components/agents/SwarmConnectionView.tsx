"use client";

import { useState } from "react";
import { createSwarmConnection, revokeSwarmConnection, connectAgentBridge } from "@/lib/api";
import { useGraph } from "@/hooks/useGraph";
import type { Agent, SwarmConnection } from "@/lib/types";
import { Network, Plus, Trash2, ArrowRight, ChevronDown } from "lucide-react";
import { useToast } from "@/components/shared/Toast";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { formatDate } from "@/lib/utils";

interface Props {
  agents: Agent[];
  connections: SwarmConnection[];
  onRefresh: () => void;
}

const PERMISSIONS = ["read", "read_write", "admin"];
const SCOPES = ["full", "filtered", "readonly"];

export default function SwarmConnectionView({ agents, connections, onRefresh }: Props) {
  const { toast } = useToast();
  const fetchAll = useGraph((s) => s.fetchAll);
  const [showForm, setShowForm] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<SwarmConnection | null>(null);
  const [creating, setCreating] = useState(false);

  const [form, setForm] = useState({
    source_agent_id: "",
    target_agent_id: "",
    permission: "read",
    scope: "full",
  });

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!form.source_agent_id || !form.target_agent_id) return;
    if (form.source_agent_id === form.target_agent_id) {
      toast("Source and target agents must be different", "error");
      return;
    }
    setCreating(true);
    try {
      // 1. Create the legacy swarm connection record (permission/scope metadata)
      await createSwarmConnection(form);

      // 2. Create the SHARES_KNOWLEDGE_WITH synaptic bridge in Neo4j
      //    This is what makes the cyan particle edge appear in the 3D galaxy.
      await connectAgentBridge(form.source_agent_id, form.target_agent_id);

      // 3. Re-fetch graph data in the Zustand store so Studio/Multiverse
      //    reflects the new edge without a page reload.
      fetchAll();

      onRefresh();
      setShowForm(false);
      setForm({ source_agent_id: "", target_agent_id: "", permission: "read", scope: "full" });
      toast("Synaptic bridge established", "success");
    } catch (err) {
      toast(err instanceof Error ? err.message : "Failed to create connection", "error");
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke() {
    if (!revokeTarget) return;
    try {
      await revokeSwarmConnection(revokeTarget.id);
      onRefresh();
      toast("Connection revoked", "success");
    } catch {
      toast("Failed to revoke connection", "error");
    } finally {
      setRevokeTarget(null);
    }
  }

  function getAgentName(id: string) {
    return agents.find((a) => a.id === id)?.name ?? id.slice(0, 8) + "…";
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-[#A1A1AA]">
          Connect agents with configurable read/write permissions
        </p>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-2 px-4 py-2 bg-[#3B82F6] hover:bg-[#2563EB] text-white text-sm font-medium rounded-lg transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          New Connection
        </button>
      </div>

      {showForm && (
        <form
          onSubmit={handleCreate}
          className="bg-[#12121A] border border-[#2A2A35] rounded-xl p-5 flex flex-col gap-4"
        >
          <h3 className="text-sm font-semibold text-[#E4E4E7]">Create Swarm Connection</h3>

          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-[#A1A1AA] font-medium">Source Agent</label>
              <select
                value={form.source_agent_id}
                onChange={(e) => setForm((f) => ({ ...f, source_agent_id: e.target.value }))}
                className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-[#E4E4E7] focus:outline-none focus:border-[#3B82F6]/50 transition-colors"
                required
              >
                <option value="">Select source…</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-[#A1A1AA] font-medium">Target Agent</label>
              <select
                value={form.target_agent_id}
                onChange={(e) => setForm((f) => ({ ...f, target_agent_id: e.target.value }))}
                className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-[#E4E4E7] focus:outline-none focus:border-[#3B82F6]/50 transition-colors"
                required
              >
                <option value="">Select target…</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-[#A1A1AA] font-medium">Permission</label>
              <select
                value={form.permission}
                onChange={(e) => setForm((f) => ({ ...f, permission: e.target.value }))}
                className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-[#E4E4E7] focus:outline-none focus:border-[#3B82F6]/50 transition-colors"
              >
                {PERMISSIONS.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-[#A1A1AA] font-medium">Scope</label>
              <select
                value={form.scope}
                onChange={(e) => setForm((f) => ({ ...f, scope: e.target.value }))}
                className="bg-[#0A0A0F] border border-[#2A2A35] rounded-lg px-3 py-2 text-sm text-[#E4E4E7] focus:outline-none focus:border-[#3B82F6]/50 transition-colors"
              >
                {SCOPES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={creating}
              className="flex items-center gap-2 px-4 py-2 bg-[#3B82F6] hover:bg-[#2563EB] text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50"
            >
              <Network className="w-3.5 h-3.5" />
              {creating ? "Creating…" : "Create Connection"}
            </button>
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="px-4 py-2 text-sm text-[#A1A1AA] hover:text-[#E4E4E7] transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {connections.length === 0 ? (
        <div className="text-center py-16">
          <div className="w-12 h-12 rounded-xl bg-[#12121A] border border-[#2A2A35] flex items-center justify-center mx-auto mb-3">
            <Network className="w-6 h-6 text-[#6B7280]" />
          </div>
          <p className="text-[#A1A1AA] text-sm mb-1">No swarm connections</p>
          <p className="text-[#6B7280] text-xs">Connect agents to share knowledge graphs</p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {connections.map((conn) => (
            <div
              key={conn.id}
              className="bg-[#12121A] border border-[#2A2A35] rounded-xl px-5 py-4 flex items-center justify-between hover:border-[#3A3A45] transition-colors group"
            >
              <div className="flex items-center gap-3">
                <span className="text-sm text-[#E4E4E7] font-medium">
                  {getAgentName(conn.source_agent_id)}
                </span>
                <ArrowRight className="w-4 h-4 text-[#6B7280]" />
                <span className="text-sm text-[#E4E4E7] font-medium">
                  {getAgentName(conn.target_agent_id)}
                </span>
                <span className="text-[10px] px-1.5 py-0.5 bg-[#3B82F6]/10 border border-[#3B82F6]/20 text-[#3B82F6] rounded-full">
                  {conn.permission}
                </span>
                <span className="text-[10px] px-1.5 py-0.5 bg-[#1A1A25] border border-[#2A2A35] text-[#A1A1AA] rounded-full">
                  {conn.scope}
                </span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-[#6B7280]">{formatDate(conn.created_at)}</span>
                <button
                  onClick={() => setRevokeTarget(conn)}
                  className="opacity-0 group-hover:opacity-100 p-1.5 text-[#6B7280] hover:text-[#EF4444] hover:bg-[#EF4444]/10 rounded-lg transition-all"
                  title="Revoke connection"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={!!revokeTarget}
        onOpenChange={(open) => { if (!open) setRevokeTarget(null); }}
        title="Revoke swarm connection?"
        description="This will immediately revoke cross-agent data access. The agents' individual knowledge graphs will not be affected."
        confirmLabel="Revoke Connection"
        onConfirm={handleRevoke}
        destructive
      />
    </div>
  );
}
