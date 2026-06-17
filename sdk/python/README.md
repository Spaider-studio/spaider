# spaider-client

Official Python SDK for [SpAIder](https://spaider.studio), the memory infrastructure for AI agents.

## Installation

```bash
pip install spaider-client
```

With LangChain integration:

```bash
pip install "spaider-client[langchain]"
```

With LlamaIndex integration:

```bash
pip install "spaider-client[llamaindex]"
```

All integrations:

```bash
pip install "spaider-client[all]"
```

## Requirements

- Python 3.9+
- A SpAIder API key (`sk-...`) from a self-hosted instance

### Getting an API key (Phase 1: self-hosted only)

There is no hosted SpAIder service yet; every key comes from an instance you run yourself. The fast path:

```bash
git clone https://github.com/Spaider-studio/spaider.git
cd spaider
pip install -e ./cli         # or: pipx install ./cli
spaider init                 # ~5 min interactive wizard
```

`spaider init` brings up the backend stack, provisions a personal agent, and prints the `sk-...` key it issues. Re-run `spaider doctor` later if you want to inspect the install. Manual install (`make setup` → edit `.env` → `make dev` → `scripts/dev/setup_mcp_dev_agent.sh`) is documented in the main repo README for hackers who want to see every step.

Hosted SpAIder (managed instance, no self-hosting required) is on the roadmap but not yet open for signup.

## Quickstart

```python
from spaider import Spaider

sp = Spaider(api_key="sk-your-key-here", agent_id="my-agent")

# Ingest unstructured text
result = sp.ingest("Max Mustermann arbeitet seit 2023 als Engineer bei Google.")
print(f"Created {result.nodes_created} nodes, {result.edges_created} edges")

# Natural-language query
answer = sp.query("Wo arbeitet Max?")
print(answer.text)
# => "Max Mustermann works at Google as an Engineer since 2023."

# Inspect the supporting subgraph
for node in answer.subgraph.nodes:
    print(f"  {node.type}: {node.label}")

# Traverse from a specific node
subgraph = sp.traverse(node_id=answer.subgraph.nodes[0].id, depth=3)

# GDPR: delete a node
sp.delete_node("node-uuid-here")

# Generate a fine-tuning dataset
dataset = sp.synthesize(strategy="reasoning", max_samples=1000)
dataset.save("training.jsonl")
```

## Async Client

```python
import asyncio
from spaider import AsyncSpaider

async def main():
    async with AsyncSpaider(api_key="sk-your-key-here", agent_id="my-agent") as sp:
        result = await sp.ingest("Alice ist CEO von Acme Corp.")
        answer = await sp.query("Wer leitet Acme Corp?")
        print(answer.text)

asyncio.run(main())
```

## Swarm Queries

Connect multiple agents and query across their graphs:

```python
sp = Spaider(api_key="sk-...", agent_id="agent-hr")

# Connect to another agent
sp.create_swarm_connection(target_agent="agent-sales")

# Query across both graphs
result = sp.swarm_query(
    "What are our top clients and who manages their accounts?",
    target_agents=["agent-sales"],
)
print(result.text)
```

## LangChain Integration

```python
from langchain.llms import OpenAI
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from spaider.integrations.langchain import SpaiderMemory

# Create Spaider-backed memory
memory = SpaiderMemory(
    api_key="sk-your-key-here",
    agent_id="my-agent",
    memory_key="history",   # injected into prompt as {history}
    input_key="input",
    output_key="output",
    top_k=5,
)

prompt = PromptTemplate(
    input_variables=["history", "input"],
    template=(
        "You are a helpful assistant with access to a knowledge graph.\n"
        "Relevant context:\n{history}\n\n"
        "Human: {input}\nAssistant:"
    ),
)

chain = LLMChain(llm=OpenAI(temperature=0), prompt=prompt, memory=memory)

# The memory automatically ingests each turn and retrieves relevant context
response = chain.predict(input="Who is Max Mustermann?")
print(response)
```

## LlamaIndex Integration

```python
from spaider.integrations.llamaindex import SpaiderIndex, SpaiderQueryEngine

# Use as a knowledge index
index = SpaiderIndex(api_key="sk-your-key-here", agent_id="my-agent")

index.add_text("Acme Corp was founded in 2001 by John Doe.")
index.add_texts([
    "John Doe is also a board member of TechStart Inc.",
    "TechStart Inc. raised $50M in Series B in 2023.",
])

response = index.query("Who founded Acme Corp and what else do they do?")
print(response.text)

# Use as a LlamaIndex QueryEngine
engine = SpaiderQueryEngine(api_key="sk-your-key-here", agent_id="my-agent")
response = engine.query("What do we know about John Doe?")
print(str(response))
```

## API Reference

### `Spaider` / `AsyncSpaider`

| Method | Description |
|---|---|
| `ingest(text, source?)` | Extract and store knowledge from text |
| `query(question, top_k?)` | Natural-language query, returns `QueryResult` |
| `traverse(node_id, depth?)` | Subgraph traversal from a node |
| `get_graph()` | Fetch the full agent graph |
| `get_node(node_id)` | Fetch a single node |
| `delete_node(node_id)` | Delete a node (GDPR) |
| `synthesize(strategy?, max_samples?)` | Generate fine-tuning dataset |
| `create_swarm_connection(target_agent)` | Connect to another agent |
| `swarm_query(question, target_agents?, top_k?)` | Cross-agent query |

### Models

| Model | Description |
|---|---|
| `Node` | A graph node with `id`, `label`, `type`, `properties` |
| `Edge` | A directed edge with `source_id`, `target_id`, `relation` |
| `GraphPayload` | Collection of nodes and edges |
| `QueryResult` | Answer `text` + supporting `subgraph` |
| `IngestResult` | Creation/merge counts |
| `SynthesisDataset` | Fine-tuning samples + `.save(path)` |

### Exceptions

| Exception | HTTP Status | Description |
|---|---|---|
| `SpaiderError` | n/a | Base exception |
| `AuthError` | 401 | Invalid or missing API key |
| `NotFoundError` | 404 | Resource not found |
| `RateLimitError` | 429 | Rate limit exceeded |
| `ServerError` | 5xx | SpAIder server error |

## Self-Hosted

Point the client at your own deployment:

```python
sp = Spaider(
    api_key="sk-...",
    agent_id="my-agent",
    base_url="http://localhost:8080",  # or your Kong gateway URL
)
```

## License

Apache 2.0. See [LICENSE](LICENSE).
