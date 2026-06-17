---
name: spaider
description: |
  Use SpAIder MCP tools for durable, queryable memory across sessions.
  Trigger whenever the user references prior conversations, decisions, preferences,
  or facts from past work, and to persist non-obvious learnings worth carrying
  into future sessions. Skip on trivial one-off requests where the tool round-trip
  costs more than the value.
---

# SpAIder: durable memory for AI agents

SpAIder is an MCP server that gives every AI session a persistent, queryable
knowledge graph. Four tools are exposed:

| Tool | Purpose |
|---|---|
| `spaider.query(question, top_k?)` | Natural-language question against the calling agent's graph; returns LLM-synthesised answer + supporting nodes + a `Node IDs (for feedback): ...` trailer |
| `spaider.list_recent(limit?)` | The N most recently created nodes for the calling agent; the fastest "what was I just doing?" probe |
| `spaider.ingest_fact(text, source?, metadata?)` | Write a fact through the standard extraction pipeline |
| `spaider.feedback(used_node_ids, success, rationale?)` | Apply Hebbian +0.1 / −0.1 reinforcement to the RELATION edges between the named nodes; shapes which paths future queries see |

---

## When to call which tool

### Session start

If the user's request looks like it could be informed by prior context (they
reference "the project", "what we agreed", a feature in flight), open with one
or both:

```
spaider.list_recent(limit=20)        # snapshot of what's fresh
spaider.query("user preferences for <topic>")    # if topic is obvious
```

The returned facts inform the rest of the session, so don't repeat questions
whose answers are already in the graph.

**Skip this for trivial requests** (one-line bug fix, simple file rename).
The tool round-trip costs more than the value.

### During the session

Call `spaider.query` instead of asking the user something they've already
answered. Examples that should hit the graph first:

- "What's the preferred merge style?"
- "Which test framework does this repo use?"
- "Did we decide yes or no on adding the X dependency?"

If the answer comes back with low confidence, treat it as a hint, not a fact,
and confirm with the user.

### Session end

Capture **non-obvious** learnings via `spaider.ingest_fact`. The bar is "would
a future session save real time if it knew this?", not a diary. Examples
worth writing:

- A user preference I had to ask about: *"User prefers merge commits over
  squash for this repo. Reason: keeps PR boundary visible in main."*
- A repo-specific gotcha: *"The scheduler runs on a different asyncio loop
  than the API; don't share Redis pools across them."*
- A decision that was made: *"Decided NOT to add OpenAI as a fallback LLM
  provider; cost model doesn't fit the agent budget."*

**Don't** write:

- Secrets, API keys, raw user PII.
- Verbatim code (use git blame / search instead).
- The summary of what you just did this session; that's already in the PR description.
- Anything you wouldn't want a colleague to read out loud at a standup.

---

## Feedback protocol: `spaider.feedback`

When `spaider.query` returns useful (or actively misleading) supporting nodes,
echo that judgment back to the graph via `spaider.feedback`. Hebbian rules:
every RELATION edge between the named nodes gets ±0.1 (capped to [0.1, 2.0]).
The engine prunes weak edges over time; your feedback shapes which paths
future queries see.

### Decision rule: high vs low confidence

```
After a spaider.query result informs my next step:

  Confident outcome (clearly helped or clearly didn't)?
    → POST feedback immediately
       spaider.feedback(used_node_ids=[...from trailer], success=True/False)

  Uncertain outcome?
    → Ask the user "👍 was that useful? (y/N within 10s)"
      ├─ y → POST success=true
      ├─ N → POST success=false
      └─ silence (10s timeout) → no POST
         (neutral; we don't fake either signal)
```

**When you have high confidence**: the query gave you the exact fact you
needed (e.g. you asked for the user's preferred branch convention and got
`feature/issue-NN-...`), or it gave you something demonstrably wrong (it
returned five entity names with no relevance to the question). Fire feedback
unprompted.

**When you have low confidence**: the supporting nodes were related but not
directly useful, or your subsequent answer was generated mostly from training-set
knowledge rather than the retrieved context. Prompt the user; if they don't
respond in ~10 seconds, skip the POST. Silence is *not* neutral if you encode
it as either success or failure; it pollutes the graph more than no signal.

**Don't fire feedback when**:

- The query failed to return any supporting nodes (no edges to update).
- You can't tell which nodes informed your answer (don't guess; skip).
- The answer's correctness depended on user-supplied context, not retrieval.

**Operational note**: the `Node IDs (for feedback): id1, id2, ...` trailer
that `spaider.query` appends is the canonical source for `used_node_ids`.
Don't fabricate IDs from labels.

---

## Failure modes (graceful degradation)

The MCP server can be unreachable for several real reasons:

- The local stack is starting up or being rebuilt.
- The user hasn't completed setup yet, or hasn't restarted their MCP client.
- Network or auth issue.

In all cases: **continue the session from conversation context**. Do not block
on the tool. Do not fail loudly to the user; mention only if the failure
prevents you from doing something they explicitly asked for. The brain not
being there is not the same as the user not being there.

---

## Quality bar

This skill is intentionally narrow: SpAIder is *one form of memory among
many*. Use it for things that benefit from being remembered across sessions
and shared across multiple AI tools that connect to the same SpAIder agent.
Don't use it as a replacement for `Read` / `Edit` / `Bash` / `git`; those
remain authoritative for the current state of code and the file system.

Two facts per session is plenty. Quality over quantity: the warm
`spaider.list_recent` call that opens the next session has limited room.
