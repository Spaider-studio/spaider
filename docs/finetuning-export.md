# Export a training dataset

SpAIder turns an agent's knowledge graph, and how that graph actually gets *used*, into fine-tuning data. Two formats, three ways to get them (UI, REST, CLI).

| Format | Shape | Train with | Needs |
|---|---|---|---|
| **ChatML (SFT)** | `{"messages": [system, user, assistant]}` per node | OpenAI fine-tuning, LM-Studio, any SFT trainer | Any graph with knowledge text |
| **DPO (preference pairs)** | `{"prompt", "chosen", "rejected"}` per pair | TRL `DPOTrainer`, Unsloth, Axolotl | A graph with **usage signal** (see below) |

## Where the DPO labels come from (RLHG)

Nobody hand-labels anything. Every node carries an ACT-R `energy_level` that resets to full each time the node is retrieved into an answer and decays when idle, and every edge carries a Hebbian `utility_weight` nudged by `spaider.feedback`. The exporter walks graph paths and emits:

- **chosen**: a path ending at a high-energy node (proven useful in production), serialized with its reasoning chain: `THOUGHT: A -[:REL]-> B … ANSWER: …`
- **rejected**: a low-energy / low-utility dead end, serialized as a bare `ANSWER:` with no reasoning.

DPO training then teaches the model to prefer graph-grounded reasoning over unsupported recall. We call this **RLHG (Reinforcement Learning from Graph)**: your production usage *is* the labeling.

> **Guardrail:** a freshly-seeded agent has no energy separation (every node at 1.0, nothing retrieved), so there are no pairs to emit. The DPO export fails with an explanatory `422` instead of handing you an empty file. Ingest, query the agent for a while, then export. The ChatML export works on any graph.

## From the Studio UI

Open **Synthesizer → Training Data Export**, pick the format (ChatML or DPO) and the agent, and hit *Download .jsonl*. DPO requires a specific agent (it traverses that agent's usage signal); ChatML also supports a full multiverse export.

## From the REST API

```bash
# ChatML (SFT): one record per node; omit agent_id for the multiverse
curl -H "Authorization: Bearer $SPAIDER_API_KEY" \
  "http://localhost:8000/api/v1/synthesize/export?agent_id=<agent>" -o sft.jsonl

# DPO preference pairs: agent required; 422 if the graph has no usage signal yet
curl -H "Authorization: Bearer $SPAIDER_API_KEY" \
  "http://localhost:8000/api/v1/synthesize/dpo?agent_id=<agent>&limit=5000&max_depth=3" -o dpo.jsonl
```

Both endpoints stream record-by-record (O(1) memory, fine for huge graphs) and enforce per-node clearance from your API key (Diplomat Protocol, fail-closed).

## From the CLI (operators)

```bash
cd backend
python -m app.scripts.synthesizer_export \
    --agent-id <agent> --limit 5000 --max-depth 3 \
    --out ../data/exports/dpo_training_v1.jsonl
```

Threshold tuning (`--chosen-energy`, `--rejected-energy`) and the full training recipe (Unsloth + TRL on a free Colab GPU, Llama-3-8B in 4-bit with LoRA) live in [dpo-finetuning.md](dpo-finetuning.md).

## What to do with the file

- **DPO:** load with `datasets.load_dataset("json", ...)` and feed TRL's `DPOTrainer`; complete code in [dpo-finetuning.md](dpo-finetuning.md). ≥500 pairs recommended for a meaningful signal.
- **ChatML:** upload to OpenAI fine-tuning, or train locally with any SFT stack.
- **Evaluate honestly:** run your fine-tuned model through the [benchmark harness](../benchmarks/README.md) against the base model: same tasks, same judge.

Managed in-product fine-tuning (kick off a training job from SpAIder, provider-agnostic via LiteLLM) is on the roadmap.
