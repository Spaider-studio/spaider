# SpAIder DPO Fine-Tuning Guide (RLHG)

## What is RLHG?

**Reinforcement Learning from Graph (RLHG)** is SpAIder's training signal strategy.
Instead of human preference labels, quality is derived directly from the knowledge
graph's ACT-R memory properties:

| Signal | Source | Meaning |
|--------|--------|---------|
| **Chosen** | Node `energy_level > 0.8` | Retrieved repeatedly → proven useful |
| **Rejected** | Node `energy_level < 0.2` | Never revisited → dead-end path |

The chosen response embeds a **Reasoning Chain** that teaches the model to think
in graph traversals before answering:

```
THOUGHT: SpAIder -[:POWERED_BY]-> Neo4j -[:SUPPORTS]-> Redis Streams
ANSWER: Data streaming technology used for stigmergic swarm routing.
```

The rejected response contains only a bare `ANSWER:` with no chain. DPO learns that
graph-grounded reasoning is preferred over direct recall.

---

## Generate the Dataset

```bash
# From repo root; requires Neo4j running with ingested data
cd backend
python -m app.scripts.synthesizer_export \
    --limit 5000 \
    --max-depth 3 \
    --out ../data/exports/dpo_training_v1.jsonl \
    --min-pairs 500

# Specific agent only
python -m app.scripts.synthesizer_export --agent-id my-agent-id

# Tune thresholds for sparse graphs
python -m app.scripts.synthesizer_export \
    --chosen-energy 0.6 \
    --rejected-energy 0.3
```

Output format (one JSON object per line):
```json
{"prompt": "What can you tell me about SpAIder (PRODUCT)?", "chosen": "THOUGHT: SpAIder -[:POWERED_BY]-> Neo4j\nANSWER: Multi-agent knowledge graph platform.", "rejected": "ANSWER: Multi-agent knowledge graph platform."}
```

---

## Fine-Tuning with Unsloth + TRL (Llama-3)

### 1. Install dependencies

```bash
pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
pip install trl datasets
```

### 2. Load the dataset

```python
from datasets import load_dataset

dataset = load_dataset("json", data_files="data/exports/dpo_training_v1.jsonl", split="train")
# Recommended split
dataset = dataset.train_test_split(test_size=0.05, seed=42)
```

### 3. DPO Training loop (TRL)

```python
from unsloth import FastLanguageModel
from trl import DPOTrainer, DPOConfig

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name   = "unsloth/Meta-Llama-3-8B-Instruct",
    max_seq_length = 2048,
    load_in_4bit   = True,
)

model = FastLanguageModel.get_peft_model(
    model,
    r              = 16,
    target_modules = ["q_proj", "v_proj"],
    lora_alpha     = 16,
    lora_dropout   = 0.05,
)

trainer = DPOTrainer(
    model     = model,
    args      = DPOConfig(
        output_dir         = "outputs/spaider-dpo",
        num_train_epochs   = 3,
        per_device_train_batch_size = 2,
        gradient_accumulation_steps = 4,
        beta               = 0.1,      # DPO temperature; lower = stronger preference signal
        learning_rate      = 5e-5,
    ),
    train_dataset = dataset["train"],
    eval_dataset  = dataset["test"],
    tokenizer     = tokenizer,
)

trainer.train()
trainer.save_model("outputs/spaider-dpo/final")
```

### 4. DPO with HuggingFace TRL only (no Unsloth)

```python
from trl import DPOTrainer, DPOConfig
from transformers import AutoModelForCausalLM, AutoTokenizer

model     = AutoModelForCausalLM.from_pretrained("meta-llama/Meta-Llama-3-8B-Instruct")
tokenizer = AutoTokenizer.from_pretrained("meta-llama/Meta-Llama-3-8B-Instruct")

trainer = DPOTrainer(
    model     = model,
    args      = DPOConfig(output_dir="outputs/spaider-dpo", beta=0.1),
    train_dataset = dataset["train"],
    tokenizer     = tokenizer,
)
trainer.train()
```

---

## Threshold Tuning

| Parameter | Default | Effect |
|-----------|---------|--------|
| `--chosen-energy` | `0.8` | Lower → more chosen candidates, lower quality bar |
| `--rejected-energy` | `0.2` | Raise → more rejected candidates, noisier negatives |
| `--max-depth` | `3` | Raise → longer reasoning chains, slower query |
| `--limit` | `5000` | Raise → more pairs, longer export time |

**Minimum recommended dataset size:** 500 pairs for meaningful DPO signal.
Below that, increase graph coverage via more ingest runs before exporting.

---

## Key Files

| File | Purpose |
|------|---------|
| `backend/app/scripts/synthesizer_export.py` | Async DPO extractor (RLHG signal) |
| `data/exports/dpo_training_v1.jsonl` | Generated training data |
| `backend/app/services/cognitive_engine.py` | ACT-R energy/decay source |
