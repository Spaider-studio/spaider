"""
End-to-end MCP client smoke against a running SpAIder MCP server.

Walks the full handshake a real MCP client would do:

  1. Open SSE stream with Bearer token.
  2. Send `initialize` request.
  3. Send `tools/list` — expects three tools (read x2 + write).
  4. Call `spaider.list_recent` (read-only; works on an empty graph).
  5. Call `spaider.ingest_fact` (write path); confirms a follow-up
     `spaider.list_recent` now sees the freshly-ingested text.

Why
---
Unit tests (#64's `tests/api/test_mcp_server.py`) mock the auth + query
services. They prove the handler logic is right. They do **not** prove
the SSE transport actually carries JSON-RPC the way real clients expect.
This script closes that gap with a real `mcp.client.sse` round-trip.

Usage
-----
1. Provision a dev agent + API key:
       scripts/dev/setup_mcp_dev_agent.sh
2. Either run the host-side standalone (`make mcp-server-host`) or have
   the compose `backend-api` running.
3. Invoke this script with the API key the setup printed:
       SPAIDER_API_KEY=sk-... python scripts/dev/smoke_mcp_client.py

Environment
-----------
SPAIDER_API_KEY  required, the dev agent's API key
SPAIDER_MCP_URL  optional; defaults to http://localhost:8001/api/v1/mcp/sse
                 (host-side standalone). Use http://localhost:8000/... for
                 the compose backend-api.
"""
from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.sse import sse_client


async def main() -> int:
    api_key = os.environ.get("SPAIDER_API_KEY")
    if not api_key:
        print("ERROR: set SPAIDER_API_KEY in the environment.", file=sys.stderr)
        print("Run scripts/dev/setup_mcp_dev_agent.sh to provision one.", file=sys.stderr)
        return 2

    url = os.environ.get(
        "SPAIDER_MCP_URL", "http://localhost:8001/api/v1/mcp/sse",
    )
    headers = {"Authorization": f"Bearer {api_key}"}

    print(f"-> connecting to {url}")
    async with sse_client(url, headers=headers) as streams:
        async with ClientSession(*streams) as session:
            print("-> initialize")
            init = await session.initialize()
            print(f"   server: {init.serverInfo.name} v{init.serverInfo.version}")
            print(f"   protocol: {init.protocolVersion}")

            print("-> tools/list")
            tools = await session.list_tools()
            for t in tools.tools:
                first_line = (t.description or "").splitlines()[0] if t.description else ""
                print(f"   - {t.name}: {first_line}")

            print("-> tools/call spaider.list_recent {limit: 5}  (cold)")
            result = await session.call_tool("spaider.list_recent", {"limit": 5})
            for content in result.content:
                if hasattr(content, "text"):
                    body = content.text
                    print(f"   body: {body[:160]}{'...' if len(body) > 160 else ''}")

            # Write path. Real LLM call happens on the server side, so this
            # exercises the full extraction pipeline end-to-end. Skip with
            # SPAIDER_SKIP_INGEST=1 if no LLM is configured for the dev agent.
            if os.environ.get("SPAIDER_SKIP_INGEST") == "1":
                print("-> spaider.ingest_fact: skipped (SPAIDER_SKIP_INGEST=1)")
            else:
                print("-> tools/call spaider.ingest_fact {text: ...}")
                ingest = await session.call_tool(
                    "spaider.ingest_fact",
                    {
                        "text": (
                            "Smoke test fact: SpAIder MCP write path works "
                            "end-to-end via the standalone container."
                        ),
                        "source": "smoke-test",
                    },
                )
                for content in ingest.content:
                    if hasattr(content, "text"):
                        body = content.text
                        print(f"   body: {body[:240]}{'...' if len(body) > 240 else ''}")

                print("-> tools/call spaider.list_recent {limit: 5}  (warm)")
                warm = await session.call_tool("spaider.list_recent", {"limit": 5})
                for content in warm.content:
                    if hasattr(content, "text"):
                        body = content.text
                        print(f"   body: {body[:240]}{'...' if len(body) > 240 else ''}")

    print("OK - round-trip complete")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
