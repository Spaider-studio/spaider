"use client";

import { useEffect, useState } from "react";
import DashboardShell from "@/components/layout/DashboardShell";
import { getAgents } from "@/lib/api";
import type { Agent } from "@/lib/types";

/** A dashboard page scoped to one chosen knowledge base. */
export default function KbPage({
  title,
  children,
}: {
  title: string;
  children: (agentId: string) => React.ReactNode;
}) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState("");

  useEffect(() => {
    getAgents()
      .then((found) => {
        setAgents(found);
        setAgentId((current) => current || found[0]?.id || "");
      })
      .catch(() => setAgents([]));
  }, []);

  return (
    <DashboardShell>
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-semibold">{title}</h1>
          <label className="flex items-center gap-2 text-sm text-zinc-400">
            Knowledge base
            <select
              value={agentId}
              onChange={(event) => setAgentId(event.target.value)}
              className="rounded border border-white/10 bg-black/30 px-2 py-1 text-zinc-200"
            >
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {agentId ? children(agentId) : <p className="text-sm text-zinc-500">No knowledge bases yet.</p>}
      </div>
    </DashboardShell>
  );
}
