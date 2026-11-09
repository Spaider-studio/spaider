"use client";

import { useCallback, useEffect, useState } from "react";
import KbPage from "@/components/shared/KbPage";
import { Button, Empty, ErrorNote, reason } from "@/components/shared/ui";
import {
  createModelVersion,
  listFineTuneProviders,
  listModelVersions,
  promoteModelVersion,
  refreshModelVersion,
  rollbackModel,
  type FineTuneProvider,
  type ModelVersion,
} from "@/lib/api";

const FIELD = "rounded border border-white/10 bg-black/30 px-2 py-1 text-sm";

// Provider catalogs change often, so this suggests a start and links to the real list.
const PROVIDERS: Record<string, { label: string; example: string; docs?: string }> = {
  openai: {
    label: "OpenAI",
    example: "gpt-4o-mini-2024-07-18",
    docs: "https://platform.openai.com/docs/guides/model-optimization",
  },
  together: { label: "Together", example: "", docs: "https://docs.together.ai/docs/fine-tuning/supported-models" },
  tinker: { label: "Tinker", example: "" },
};

function Train({ agentId }: { agentId: string }) {
  const [versions, setVersions] = useState<ModelVersion[]>([]);
  const [provider, setProvider] = useState("openai");
  const [providers, setProviders] = useState<FineTuneProvider[]>([]);
  const [baseModel, setBaseModel] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    listModelVersions(agentId)
      .then(setVersions)
      .catch((e) => setError(reason(e)));
  }, [agentId]);

  useEffect(load, [load]);

  useEffect(() => {
    listFineTuneProviders()
      .then(setProviders)
      .catch(() => setProviders([]));
  }, []);

  const meta = PROVIDERS[provider];
  const current = providers.find((item) => item.id === provider);

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
      <ol className="mb-3 max-w-2xl list-inside list-decimal space-y-0.5 text-sm text-zinc-400">
        <li>Pick a provider and the model to start from. Your data trains a private copy of it.</li>
        <li>Public (clearance 1) knowledge from this knowledge base is sent to that provider for training.</li>
        <li>
          When training finishes, compare it with the current model using{" "}
          <code className="text-zinc-300">spaider finetune evaluate</code>.
        </li>
        <li>If it measurably beats the current model, promote it here. Only then does it answer questions.</li>
      </ol>
      <ErrorNote message={error} />
      {current && !current.configured && (
        <p role="status" className="mb-3 max-w-2xl rounded border border-amber-500/40 bg-amber-500/10 p-2 text-sm">
          {meta.label} is not set up. Add <code>{current.env_var}</code> to your server&apos;s <code>.env</code> and
          restart the API.
        </p>
      )}
      <form
        className="mb-4 flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          act(createModelVersion({ agent_id: agentId, provider, base_model: baseModel.trim(), data_clearance: 1 }));
        }}
      >
        <label className="flex flex-col gap-1 text-xs text-zinc-400">
          Provider
          <select
            value={provider}
            onChange={(event) => {
              setProvider(event.target.value);
              setBaseModel("");
            }}
            className={FIELD}
          >
            {Object.entries(PROVIDERS).map(([id, item]) => (
              <option key={id} value={id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-72 flex-col gap-1 text-xs text-zinc-400">
          Model to start from (the provider&apos;s exact model ID)
          <input
            list="base-models"
            value={baseModel}
            onChange={(event) => setBaseModel(event.target.value)}
            placeholder={meta.example}
            className={FIELD}
          />
          <datalist id="base-models">{meta.example && <option value={meta.example} />}</datalist>
        </label>
        <Button type="submit" disabled={!baseModel.trim() || current?.configured === false} className="!py-1.5">
          Start training
        </Button>
        <Button type="button" onClick={() => act(rollbackModel(agentId))} className="!py-1.5">
          Go back to the default model
        </Button>
      </form>
      <p className="mb-4 text-xs text-zinc-500">
        {meta.docs ? (
          <a href={meta.docs} target="_blank" rel="noreferrer" className="underline">
            See which models {meta.label} can train
          </a>
        ) : (
          `Check your ${meta.label} account for the models it can train.`
        )}
      </p>
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
              {version.status === "trained" && <span className="text-xs text-zinc-500">Ready to evaluate</span>}
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
