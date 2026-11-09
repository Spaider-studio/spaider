"use client";

import { useCallback, useEffect, useState } from "react";
import KbPage from "@/components/shared/KbPage";
import { Button, Empty, ErrorNote, reason } from "@/components/shared/ui";
import {
  approveGrant,
  decideKpr,
  getAgents,
  denyGrant,
  getKpr,
  listGrants,
  listKprs,
  requestGrant,
  revokeGrant,
  type Grant,
  type KnowledgePR,
} from "@/lib/api";

function Sharing({ agentId }: { agentId: string }) {
  const [grants, setGrants] = useState<Grant[]>([]);
  const [kprs, setKprs] = useState<KnowledgePR[]>([]);
  const [open, setOpen] = useState<KnowledgePR | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [source, setSource] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    Promise.all([listGrants(), listKprs(agentId)])
      .then(([foundGrants, foundKprs]) => {
        setGrants(foundGrants.filter((g) => g.source_kb === agentId || g.target_kb === agentId));
        setKprs(foundKprs);
      })
      .catch((e) => setError(reason(e)));
  }, [agentId]);

  useEffect(load, [load]);

  useEffect(() => {
    getAgents()
      .then((agents) => setNames(Object.fromEntries(agents.map((agent) => [agent.id, agent.name]))))
      .catch(() => setNames({}));
  }, []);

  const nameOf = (id: string) => names[id] ?? id;

  async function act(action: Promise<unknown>) {
    setError("");
    try {
      await action;
      load();
    } catch (e) {
      setError(reason(e));
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <section>
        <h2 className="mb-2 text-sm font-medium text-zinc-300">Grants</h2>
        <ErrorNote message={error} />
        <form
          className="mb-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            act(requestGrant({ source_kb: source, target_kb: agentId, max_clearance: 1 }));
            setSource("");
          }}
        >
          <input
            aria-label="ID of the knowledge base to read from"
            placeholder="ID of the knowledge base to read from"
            value={source}
            onChange={(event) => setSource(event.target.value)}
            className="flex-1 rounded border border-white/10 bg-black/30 px-2 py-1 text-sm"
          />
          <Button type="submit" disabled={!source.trim()}>
            Request
          </Button>
        </form>
        {grants.length === 0 && <Empty>No grants involve this knowledge base.</Empty>}
        <ul className="divide-y divide-white/10">
          {grants.map((grant) => (
            <li key={grant.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="min-w-0 truncate">
                {nameOf(grant.source_kb)} → {nameOf(grant.target_kb)}{" "}
                <span className="text-zinc-500">
                  {grant.status}, clearance {grant.max_clearance}
                </span>
              </span>
              <span className="flex shrink-0 gap-1">
                {grant.status === "requested" && (
                  <>
                    <Button onClick={() => act(approveGrant(grant.id))}>Approve</Button>
                    <Button onClick={() => act(denyGrant(grant.id))}>Deny</Button>
                  </>
                )}
                {(grant.status === "requested" || grant.status === "approved") && (
                  <Button onClick={() => act(revokeGrant(grant.id))}>Revoke</Button>
                )}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-medium text-zinc-300">Knowledge PRs into this knowledge base</h2>
        {kprs.length === 0 && <Empty>No proposals yet.</Empty>}
        <ul className="divide-y divide-white/10">
          {kprs.map((kpr) => (
            <li key={kpr.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <button className="min-w-0 truncate text-left hover:underline" onClick={() => getKpr(kpr.id).then(setOpen)}>
                {kpr.title} <span className="text-zinc-500">{kpr.status}</span>
              </button>
              <span className="flex shrink-0 gap-1">
                {kpr.status === "open" && (
                  <>
                    <Button onClick={() => act(decideKpr(kpr.id, "approve"))}>Approve</Button>
                    <Button onClick={() => act(decideKpr(kpr.id, "reject"))}>Reject</Button>
                  </>
                )}
                {kpr.status === "applied" && <Button onClick={() => act(decideKpr(kpr.id, "revert"))}>Revert</Button>}
              </span>
            </li>
          ))}
        </ul>
        {open?.diff && (
          <div className="mt-3 rounded border border-white/10 p-3 text-xs">
            <p className="mb-1 font-medium">{open.title}</p>
            <ul>
              {open.diff.nodes.map((node) => (
                <li key={node.source_id}>
                  {node.action}: {node.label}
                </li>
              ))}
            </ul>
            <p className="mt-1 text-zinc-500">{open.diff.edges.length} edges</p>
          </div>
        )}
      </section>
    </div>
  );
}

export default function SharingPage() {
  return <KbPage title="Sharing">{(agentId) => <Sharing key={agentId} agentId={agentId} />}</KbPage>;
}
