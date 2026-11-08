"use client";

import { useCallback, useEffect, useState } from "react";
import KbPage from "@/components/shared/KbPage";
import { Button, Empty, ErrorNote, reason } from "@/components/shared/ui";
import {
  createModelVersion,
  listModelVersions,
  promoteModelVersion,
  refreshModelVersion,
  rollbackModel,
  type ModelVersion,
} from "@/lib/api";

function Train({ agentId }: { agentId: string }) {
  const [versions, setVersions] = useState<ModelVersion[]>([]);
  const [provider, setProvider] = useState("openai");
  const [baseModel, setBaseModel] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    listModelVersions(agentId)
      .then(setVersions)
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
      <p className="mb-3 max-w-xl text-sm text-zinc-400">
        Training sends clearance-1 data from this knowledge base to the provider you choose. A model is only served
        after it passes the evaluation gate.
      </p>
      <ErrorNote message={error} />
      <form
        className="mb-4 flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          act(createModelVersion({ agent_id: agentId, provider, base_model: baseModel, data_clearance: 1 }));
        }}
      >
        <select
          aria-label="Provider"
          value={provider}
          onChange={(event) => setProvider(event.target.value)}
          className="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm"
        >
          <option value="openai">OpenAI</option>
          <option value="together">Together</option>
          <option value="tinker">Tinker</option>
        </select>
        <input
          aria-label="Base model"
          placeholder="Base model"
          value={baseModel}
          onChange={(event) => setBaseModel(event.target.value)}
          className="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm"
        />
        <Button type="submit" disabled={!baseModel.trim()}>
          Start training
        </Button>
        <Button type="button" onClick={() => act(rollbackModel(agentId))}>
          Use base model
        </Button>
      </form>
      {versions.length === 0 && <Empty>No training jobs yet.</Empty>}
      <ul className="divide-y divide-white/10">
        {versions.map((version) => (
          <li key={version.id} className="flex items-center justify-between gap-3 py-2 text-sm">
            <span className="min-w-0 truncate">
              {version.provider} / {version.base_model} <span className="text-zinc-500">{version.status}</span>
              {version.error && <span className="text-red-400"> {version.error}</span>}
            </span>
            <span className="flex shrink-0 gap-1">
              {version.status === "training" && (
                <Button onClick={() => act(refreshModelVersion(version.id))}>Refresh</Button>
              )}
              {version.status === "evaluated" && (
                <Button onClick={() => act(promoteModelVersion(version.id))}>Promote</Button>
              )}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

export default function TrainPage() {
  return <KbPage title="Train">{(agentId) => <Train key={agentId} agentId={agentId} />}</KbPage>;
}
