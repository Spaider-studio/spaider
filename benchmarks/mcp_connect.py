"""Connects the benchmark tools to SpAIder's MCP endpoint (Streamable HTTP)."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

DEFAULT_URL = "http://localhost:8000/api/v1/mcp/"


def mcp_url() -> str:
    return os.environ.get("SPAIDER_MCP_URL", DEFAULT_URL)


@asynccontextmanager
async def connect(api_key: str, url: str | None = None) -> AsyncIterator[tuple]:
    """Yields (read, write) streams, ready for ``ClientSession``."""
    headers = {"Authorization": f"Bearer {api_key}"}
    async with create_mcp_http_client(headers=headers) as client:
        async with streamable_http_client(url or mcp_url(), http_client=client) as streams:
            yield streams[0], streams[1]
