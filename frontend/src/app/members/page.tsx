"use client";

import { useCallback, useEffect, useState } from "react";
import KbPage from "@/components/shared/KbPage";
import SecretOnce from "@/components/shared/SecretOnce";
import { Button, Empty, ErrorNote, reason } from "@/components/shared/ui";
import {
  createServiceAccount,
  createUser,
  listMembers,
  listPrincipals,
  removeMember,
  setMemberRole,
  type Member,
  type Principal,
} from "@/lib/api";
import { CLEARANCE_LEVELS, ROLES, roleLabel } from "@/lib/access";

const FIELD = "rounded border border-white/10 bg-black/30 px-2 py-1 text-sm";

function RoleGuide() {
  return (
    <details className="mb-4 rounded border border-white/10 p-3 text-sm" open>
      <summary className="cursor-pointer font-medium">What each role can do</summary>
      <dl className="mt-2 grid gap-1 sm:grid-cols-[7rem_1fr]">
        {ROLES.map((role) => (
          <div key={role.id} className="contents">
            <dt className="text-zinc-200">{role.label}</dt>
            <dd className="text-zinc-400">{role.can}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-zinc-500">
        Roles apply to this knowledge base only. Clearance is separate: it caps how sensitive a piece of knowledge
        someone can see, in every knowledge base.
      </p>
    </details>
  );
}

function AddMember({
  agentId,
  memberIds,
  onAdded,
}: {
  agentId: string;
  memberIds: Set<string>;
  onAdded: () => void;
}) {
  const [mode, setMode] = useState<"person" | "new" | "app">("person");
  const [people, setPeople] = useState<Principal[] | null>(null);
  const [personId, setPersonId] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("reader");
  const [clearance, setClearance] = useState(1);
  const [error, setError] = useState("");
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(null);

  useEffect(() => {
    // Only organization admins may list everyone; others can still create app keys.
    listPrincipals()
      .then(setPeople)
      .catch(() => {
        setPeople(null);
        setMode("app");
      });
  }, []);

  const candidates = (people ?? []).filter(
    (person) => person.kind === "user" && !person.disabled && !memberIds.has(person.id)
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    try {
      if (mode === "person") {
        await setMemberRole(agentId, personId, role);
        setPersonId("");
      } else if (mode === "new") {
        const created = await createUser({ name, email: email || undefined, clearance_level: clearance });
        await setMemberRole(agentId, created.id, role);
        setSecret({ title: `Sign-in key for ${name}. Send it to them privately.`, value: created.api_key });
        setPeople(await listPrincipals());
      } else {
        const created = await createServiceAccount(agentId, { name, role, clearance_level: clearance });
        setSecret({ title: `API key for ${name}`, value: created.api_key });
      }
      setName("");
      setEmail("");
      onAdded();
    } catch (e) {
      setError(reason(e));
    }
  }

  const ready = mode === "person" ? Boolean(personId) : Boolean(name.trim());

  return (
    <section className="mb-6 rounded border border-white/10 p-3">
      <h2 className="mb-2 text-sm font-medium">Add to this knowledge base</h2>
      {secret && <SecretOnce title={secret.title} value={secret.value} onClose={() => setSecret(null)} />}
      <ErrorNote message={error} />
      <div role="radiogroup" aria-label="Who to add" className="mb-3 flex flex-wrap gap-3 text-sm">
        {people !== null && (
          <>
            <label className="flex items-center gap-1">
              <input type="radio" checked={mode === "person"} onChange={() => setMode("person")} /> Someone in your
              organization
            </label>
            <label className="flex items-center gap-1">
              <input type="radio" checked={mode === "new"} onChange={() => setMode("new")} /> A new person
            </label>
          </>
        )}
        <label className="flex items-center gap-1">
          <input type="radio" checked={mode === "app"} onChange={() => setMode("app")} /> An app or agent (API key)
        </label>
      </div>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        {mode === "person" && (
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            Person
            <select value={personId} onChange={(e) => setPersonId(e.target.value)} className={FIELD}>
              <option value="">{candidates.length ? "Choose…" : "Everyone is already a member"}</option>
              {candidates.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name || person.email || person.id}
                </option>
              ))}
            </select>
          </label>
        )}
        {mode !== "person" && (
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            {mode === "new" ? "Name" : "What will use this key?"}
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={mode === "new" ? "Ada Lovelace" : "Claude Desktop"}
              className={FIELD}
            />
          </label>
        )}
        {mode === "new" && (
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            Email (optional)
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={FIELD} />
          </label>
        )}
        <label className="flex flex-col gap-1 text-xs text-zinc-400">
          Role
          <select value={role} onChange={(e) => setRole(e.target.value)} className={FIELD}>
            {ROLES.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        {mode !== "person" && (
          <label className="flex flex-col gap-1 text-xs text-zinc-400">
            Clearance
            <select value={clearance} onChange={(e) => setClearance(Number(e.target.value))} className={FIELD}>
              {CLEARANCE_LEVELS.map((item) => (
                <option key={item.level} value={item.level}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <Button type="submit" disabled={!ready} className="!py-1.5">
          Add
        </Button>
      </form>
    </section>
  );
}

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
      <RoleGuide />
      <AddMember agentId={agentId} memberIds={new Set(members.map((member) => member.id))} onAdded={load} />
      <h2 className="mb-1 text-sm font-medium">Who has access</h2>
      <ErrorNote message={error} />
      {members.length === 0 && <Empty>Nobody has a role on this knowledge base yet.</Empty>}
      <ul className="divide-y divide-white/10">
        {members.map((member) => (
          <li key={member.id} className="flex items-center justify-between gap-3 py-2 text-sm">
            <span className="min-w-0 truncate">
              {member.name || member.id}{" "}
              <span className="text-xs text-zinc-500">
                {member.kind === "service_account" ? "app or agent key" : "person"}
              </span>
            </span>
            <span className="flex shrink-0 items-center gap-2">
              <select
                aria-label={`Role for ${member.name || member.id}`}
                title={ROLES.find((role) => role.id === member.role)?.can}
                value={member.role}
                onChange={(event) => act(setMemberRole(agentId, member.id, event.target.value))}
                className={FIELD}
              >
                {ROLES.map((role) => (
                  <option key={role.id} value={role.id}>
                    {roleLabel(role.id)}
                  </option>
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
