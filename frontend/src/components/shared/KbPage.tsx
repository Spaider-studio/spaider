"use client";

import { useCallback, useEffect, useState } from "react";
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
  const [failure, setFailure] = useState<string | null>(null);

  const load = useCallback(() => {
    getAgents()
      .then((found) => {
        setAgents(found);
        setAgentId((current) => current || found[0]?.id || "");
      })
      .catch((error: unknown) => {
        // A rate limit or a server error must not look like an install with no KBs.
        setFailure(error instanceof Error ? error.message : "unknown error");
      });
  }, []);

  useEffect(load, [load]);

  const retry = () => {
    setFailure(null);
    load();
  };

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
        {failure ? (
          <div role="alert" className="text-sm text-red-400">
            <p>Could not load your knowledge bases: {failure}</p>
            <button onClick={retry} className="mt-2 rounded border border-white/10 px-3 py-1 text-zinc-200">
              Try again
            </button>
          </div>
        ) : agentId ? (
          children(agentId)
        ) : (
          <p className="text-sm text-zinc-500">No knowledge bases yet.</p>
        )}
      </div>
    </DashboardShell>
  );
}
