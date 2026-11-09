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
import { CLEARANCE_LEVELS } from "@/lib/access";

const OTHER = "__other__";
const FIELD = "rounded border border-white/10 bg-black/30 px-2 py-1 text-sm";

const STATUS_NOTE: Record<string, (source: string) => string> = {
  requested: (source) => `waiting for a steward of ${source} to approve`,
  approved: () => "active",
  denied: () => "denied",
  revoked: () => "ended",
};

function Sharing({ agentId }: { agentId: string }) {
  const [grants, setGrants] = useState<Grant[]>([]);
  const [kprs, setKprs] = useState<KnowledgePR[]>([]);
  const [open, setOpen] = useState<KnowledgePR | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [source, setSource] = useState("");
  const [custom, setCustom] = useState("");
  const [ceiling, setCeiling] = useState(1);
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
  const sourceId = source === OTHER ? custom.trim() : source;

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
      <p className="max-w-3xl text-sm text-zinc-400 lg:col-span-2">
        Knowledge bases are private by default. A <strong className="font-medium text-zinc-200">grant</strong> lets one
        read another when answering questions, without copying anything. Whoever is a steward of the knowledge base
        being read must approve it, and either side can end it at any time.
      </p>
      <section>
        <h2 className="mb-2 text-sm font-medium text-zinc-300">Grants</h2>
        <ErrorNote message={error} />
        <form
          className="mb-3 flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (!sourceId) return;
            act(requestGrant({ source_kb: sourceId, target_kb: agentId, max_clearance: ceiling }));
            setSource("");
            setCustom("");
          }}
        >
          <label className="flex min-w-48 flex-1 flex-col gap-1 text-xs text-zinc-400">
            Let {nameOf(agentId)} read from
            <select value={source} onChange={(event) => setSource(event.target.value)} className={FIELD}>
              <option value="">Choose a knowledge base…</option>
              {Object.entries(names)
                .filter(([id]) => id !== agentId)
                .map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              <option value={OTHER}>Another knowledge base (enter its ID)…</option>
            </select>
          </label>
          {source === OTHER && (
            <label className="flex flex-1 flex-col gap-1 text-xs text-zinc-400">
              Knowledge base ID
              <input value={custom} onChange={(event) => setCustom(event.target.value)} className={FIELD} />
            </label>
          )}
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            Up to clearance
            <select value={ceiling} onChange={(event) => setCeiling(Number(event.target.value))} className={FIELD}>
              {CLEARANCE_LEVELS.map((item) => (
                <option key={item.level} value={item.level}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <Button type="submit" disabled={!sourceId} className="!py-1.5">
            Request access
          </Button>
        </form>
        {grants.length === 0 && <Empty>No grants involve this knowledge base.</Empty>}
        <ul className="divide-y divide-white/10">
          {grants.map((grant) => (
            <li key={grant.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="min-w-0">
                {nameOf(grant.source_kb)} → {nameOf(grant.target_kb)}{" "}
                <span className="text-zinc-500">
                  {(STATUS_NOTE[grant.status]?.(nameOf(grant.source_kb)) ?? grant.status) +
                    `, up to clearance ${grant.max_clearance}`}
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
        <h2 className="mb-1 text-sm font-medium text-zinc-300">Knowledge PRs into this knowledge base</h2>
        <p className="mb-2 text-xs text-zinc-500">
          A proposal to copy specific knowledge into this knowledge base, with a diff to review. Applying one can be
          reverted. Proposals are created with <code>spaider kpr create</code> for now.
        </p>
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
