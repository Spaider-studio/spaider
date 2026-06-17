#!/usr/bin/env bash
# Provision a bench-{suite}-{state} agent for the SpAIder benchmark
# harness. Same idempotent semantics as setup_mcp_dev_agent.sh — re-running
# rotates the API key on the same agent.
#
# Why a separate script (and a separate agent):
#   Personal Claude Code sessions write learnings into dev-{username}.
#   Benchmark sweeps mutate utility_weight via #74's feedback loop. Mixing
#   the two contaminates both: relevance signal in your personal queries
#   gets polluted by AcmeAI corpus nodes, and the benchmark graph carries
#   stray facts from session work. See CLAUDE.md §9.
#
# Usage:
#   scripts/dev/setup_bench_agent.sh                        # → bench-acmeai-clean
#   scripts/dev/setup_bench_agent.sh acmeai-soak            # → bench-acmeai-soak
#   scripts/dev/setup_bench_agent.sh --name bench-mycorp    # → bench-mycorp
#
# After provisioning, ingest a corpus into it (the AcmeAI corpus ships in
# benchmarks/corpus/acmeai_30d.yaml):
#
#   export SPAIDER_API_KEY=<key from this script>
#   benchmarks/.venv/bin/python -m benchmarks.seed \
#       --corpus benchmarks/corpus/acmeai_30d.yaml
#
# Prereq: compose stack up (`make dev` or `make dev-neo4j`).

set -euo pipefail

# Argument parsing — same shape as setup_mcp_dev_agent.sh.
AGENT_NAME=""
if [[ "${1:-}" == "--name" ]]; then
    if [[ -z "${2:-}" ]]; then
        echo "ERROR: --name requires an argument" >&2
        exit 2
    fi
    AGENT_NAME="$2"
elif [[ -n "${1:-}" ]]; then
    AGENT_NAME="bench-${1}"
else
    AGENT_NAME="bench-acmeai-clean"
fi

# Convention guard — opposite direction of the dev-agent script.
if [[ "${AGENT_NAME}" == dev-* ]]; then
    echo "NOTE: '${AGENT_NAME}' looks like a personal dev agent." >&2
    echo "      Prefer scripts/dev/setup_mcp_dev_agent.sh for those." >&2
    echo "      Continuing anyway." >&2
    echo "" >&2
fi

API_BASE="${API_BASE:-http://localhost:8000/api/v1}"
MCP_PORT=8000
if curl -sf -o /dev/null --max-time 1 "http://localhost:8001/health"; then
    MCP_PORT=8001
fi

echo "Looking for an existing agent named '${AGENT_NAME}'…" >&2
existing=$(curl -sf "${API_BASE}/agents" | python3 -c "
import sys, json
agents = json.load(sys.stdin).get('agents', [])
for a in agents:
    if a.get('name') == '${AGENT_NAME}':
        print(a['id'])
        break
" 2>/dev/null || true)

if [[ -n "${existing}" ]]; then
    echo "  found ${AGENT_NAME} (id=${existing}); rotating its API key…" >&2
    response=$(curl -sf -X POST "${API_BASE}/agents/${existing}/rotate-key")
else
    echo "  not found; creating ${AGENT_NAME}…" >&2
    response=$(curl -sf -X POST "${API_BASE}/agents" \
        -H 'Content-Type: application/json' \
        -d "{\"name\":\"${AGENT_NAME}\",\"tenant_id\":\"default\",\"permissions\":[\"read\",\"write\",\"query\"]}")
    response=$(printf '%s' "${response}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(json.dumps({'api_key': d.get('agent', {}).get('api_key')}))
")
fi

api_key=$(printf '%s' "${response}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('api_key',''))")

if [[ -z "${api_key}" ]]; then
    echo "ERROR: could not extract api_key from API response:" >&2
    echo "${response}" >&2
    exit 1
fi

cat <<EOF
================================================================
  SpAIder MCP server — benchmark agent provisioned
----------------------------------------------------------------
  Agent name : ${AGENT_NAME}
  API key    : ${api_key}
  MCP URL    : http://localhost:${MCP_PORT}/api/v1/mcp/sse
================================================================

Next step — ingest the corpus:

  export SPAIDER_API_KEY=${api_key}
  export SPAIDER_MCP_URL=http://localhost:${MCP_PORT}/api/v1/mcp/sse
  benchmarks/.venv/bin/python -m benchmarks.seed \\
      --corpus benchmarks/corpus/acmeai_30d.yaml

Then run a benchmark sweep against this agent:

  benchmarks/.venv/bin/python -m benchmarks.runner \\
      --tasks benchmarks/tasks/compounding_brain --mode with-mcp \\
      --runs benchmarks/runs

EOF
