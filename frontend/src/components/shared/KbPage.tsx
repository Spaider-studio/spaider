"use client";

import { useEffect } from "react";
import DashboardShell from "@/components/layout/DashboardShell";
import { useGraph } from "@/hooks/useGraph";

/**
 * A dashboard page scoped to one knowledge base.
 *
 * Which one is chosen in one place, the header, and shared by the whole app, so
 * this page has no picker of its own. If nothing is chosen yet (the header's "all
 * knowledge bases"), the first one is taken and recorded so the header agrees.
 */
export default function KbPage({
  title,
  children,
}: {
  title: string;
  children: (agentId: string) => React.ReactNode;
}) {
  const agents = useGraph((s) => s.agents);
  const agentsError = useGraph((s) => s.agentsError);
  const chosen = useGraph((s) => s.agentId);
  const setAgentId = useGraph((s) => s.setAgentId);
  const loadAgents = useGraph((s) => s.loadAgents);

  useEffect(() => {
    void loadAgents();
  }, [loadAgents]);

  useEffect(() => {
    if (chosen === null && agents.length > 0) setAgentId(agents[0].id);
  }, [chosen, agents, setAgentId]);

  const current = agents.find((agent) => agent.id === chosen);

  return (
    <DashboardShell>
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mb-4 flex flex-wrap items-baseline gap-3">
          <h1 className="text-lg font-semibold">{title}</h1>
          {current && <span className="text-sm text-zinc-400">{current.name}</span>}
        </div>
        {agentsError ? (
          <div role="alert" className="text-sm text-red-400">
            <p>Could not load your knowledge bases: {agentsError}</p>
            <button
              onClick={() => void loadAgents(true)}
              className="mt-2 rounded border border-white/10 px-3 py-1 text-zinc-200"
            >
              Try again
            </button>
          </div>
        ) : current ? (
          children(current.id)
        ) : agents.length === 0 ? (
          <p className="text-sm text-zinc-400">No knowledge bases yet.</p>
        ) : null}
      </div>
    </DashboardShell>
  );
}
