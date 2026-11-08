"use client";

import { useCallback, useEffect, useState } from "react";
import KbPage from "@/components/shared/KbPage";
import { Button, Empty, ErrorNote, reason } from "@/components/shared/ui";
import { listMembers, removeMember, setMemberRole, type Member } from "@/lib/api";

const ROLES = ["reader", "contributor", "editor", "steward", "auditor"];

function Members({ agentId }: { agentId: string }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    listMembers(agentId)
      .then(setMembers)
      .catch((e) => setError(reason(e)));
  }, [agentId]);

  useEffect(load, [load]);

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
    <>
      <ErrorNote message={error} />
      {members.length === 0 && <Empty>Nobody has a role on this knowledge base.</Empty>}
      <ul className="divide-y divide-white/10">
        {members.map((member) => (
          <li key={member.id} className="flex items-center justify-between gap-3 py-2 text-sm">
            <span>{member.name || member.id}</span>
            <span className="flex items-center gap-2">
              <select
                aria-label={`Role for ${member.name || member.id}`}
                value={member.role}
                onChange={(event) => act(setMemberRole(agentId, member.id, event.target.value))}
                className="rounded border border-white/10 bg-black/30 px-2 py-1"
              >
                {ROLES.map((role) => (
                  <option key={role}>{role}</option>
                ))}
              </select>
              <Button onClick={() => act(removeMember(agentId, member.id))}>Remove</Button>
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

export default function MembersPage() {
  return <KbPage title="Members">{(agentId) => <Members key={agentId} agentId={agentId} />}</KbPage>;
}
