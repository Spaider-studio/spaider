#!/usr/bin/env bash
# Provision a personal dev-{username} agent in SpAIder for the
# MCP-as-memory flow used by Claude Code sessions.
# Idempotent: re-running just rotates the API key for the same agent.
#
# Usage:
#   scripts/dev/setup_mcp_dev_agent.sh                # → dev-${USER}
#   scripts/dev/setup_mcp_dev_agent.sh alice          # → dev-alice
#   scripts/dev/setup_mcp_dev_agent.sh --name custom  # → custom
#
# Defaults username to $USER. Prints the .mcp.json snippet you can paste
# into ~/.claude/.mcp.json.
#
# Naming convention (see CLAUDE.md §9):
#   dev-{username}        → personal sessions (this script)
#   bench-{suite}-{state} → benchmark agents — use setup_bench_agent.sh
#   app-{service}         → production application agents — out of scope
# A dev-/bench- prefix mismatch is flagged below to keep purposes separate.
#
# Prereq: compose stack up (`make dev` or at least `make dev-neo4j`) so the
# main API at :8000 can serve POST /api/v1/agents.

set -euo pipefail

# Argument parsing: accept either a positional username or --name <agent>.
AGENT_NAME=""
if [[ "${1:-}" == "--name" ]]; then
    if [[ -z "${2:-}" ]]; then
        echo "ERROR: --name requires an argument" >&2
        exit 2
    fi
    AGENT_NAME="$2"
elif [[ -n "${1:-}" ]]; then
    AGENT_NAME="dev-${1}"
else
    AGENT_NAME="dev-${USER:-dev}"
fi

# Convention guard: this script is for personal/dev agents. If the caller
# is provisioning something that looks like a benchmark agent, suggest
# the right tool and continue (don't block — sometimes operators have
# good reasons).
if [[ "${AGENT_NAME}" == bench-* ]]; then
    echo "NOTE: '${AGENT_NAME}' looks like a benchmark agent." >&2
    echo "      Prefer scripts/dev/setup_bench_agent.sh for those — keeps" >&2
    echo "      personal session memory separate from benchmark graph." >&2
    echo "      Continuing anyway." >&2
    echo "" >&2
fi

API_BASE="${API_BASE:-http://localhost:8000/api/v1}"

# Pick a port for the .mcp.json URL: 8001 if the host-side standalone is
# running, otherwise the main API on 8000.
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
    # Newly-created agents return the api_key in agent.api_key
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
  SpAIder MCP server — dev agent provisioned
----------------------------------------------------------------
  Agent name : ${AGENT_NAME}
  API key    : ${api_key}
  MCP URL    : http://localhost:${MCP_PORT}/api/v1/mcp
  (port 8001 = host-side standalone; 8000 = compose backend-api)
================================================================

Add to ~/.claude/.mcp.json (merge with existing mcpServers):

{
  "mcpServers": {
    "spaider": {
      "type":    "http",
      "url":     "http://localhost:${MCP_PORT}/api/v1/mcp",
      "headers": {"Authorization": "Bearer ${api_key}"}
    }
  }
}

Quick sanity probe (a bare GET without a session is rejected — that's expected;
a 401 means auth works, a 4xx/200 means the endpoint is live):

  curl -s -o /dev/null -w "%{http_code}\\n" -m 5 \\
    -H "Authorization: Bearer ${api_key}" \\
    "http://localhost:${MCP_PORT}/api/v1/mcp"

  # For a full round-trip use: SPAIDER_API_KEY=${api_key} \\
  #   python scripts/dev/smoke_mcp_client.py

EOF
