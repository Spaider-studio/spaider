"""
MCP Connector — ingests MCP "Resources" from any compliant MCP server into
SpAIder's knowledge graph.

Implements [](/Spaider-studio/spaider/issues/53). Subclass of the canonical
``app.connectors.BaseConnector`` — same contract used by ``URLConnector`` and
``UploadConnector``. Registered in the global ``ConnectorRegistry`` from
``backend/app/api/v1/ingest.py`` so ``GET /connectors/mcp/status`` and the
shared run-stats surface work for free.

Transports
----------
* ``stdio``: spawn a local MCP server subprocess (``command`` + ``args``).
  Inherits the connector run process's stdin/stdout pair via
  ``mcp.client.stdio.stdio_client``.
* ``sse``:   connect to a remote HTTP MCP server via
  ``mcp.client.sse.sse_client``.

Exactly one transport must be configured per call.

Scope: Resources only
---------------------
MCP also defines ``Tools`` and ``Prompts``. Both are out of scope for this
connector — we only fan out the source's static documents into the agent's
knowledge graph. Tool invocation is a query-time concern, not an ingest one.

Incremental sync
----------------
Per-resource state lives in ``run_state.source_states[uri]`` (main's framework
shape, ``dict[str, dict[str, Any]]``). For every resource:

  1. **Fast skip.** If the server returned a ``lastModified`` in
     ``resource.meta`` AND the stored state has the same value, skip
     entirely. No ``read_resource`` call, zero tokens.

  2. **Hash skip.** Otherwise read the resource, compute SHA-256 over
     concatenated text content. If hash matches stored ``content_hash``,
     skip ingest (refresh ``last_modified`` so future fast-skip works).

  3. **Yield + update state.** Otherwise emit a ``ConnectorRecord`` and
     overwrite ``run_state.source_states[uri]`` with the new
     ``{content_hash, last_modified}`` dict.

DLQ
---
Per-resource exceptions go through ``app.connectors.send_to_dlq`` and the
loop continues — a single bad resource cannot abort the stream. Errors at
the transport / pagination level propagate to the caller.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, AsyncGenerator, Optional

from app.connectors import BaseConnector, ConnectorRecord, RunState, send_to_dlq

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCPConnector
# ---------------------------------------------------------------------------


class MCPConnector(BaseConnector):
    """Yields one ``ConnectorRecord`` per MCP resource whose content changed
    since the last run for the given ``run_state``."""

    connector_id = "mcp"

    async def run(  # type: ignore[override]
        self,
        agent_id: str,
        run_state: RunState,
        *,
        # Exactly one of these two transport tuples must be supplied.
        # stdio:
        command: Optional[str] = None,
        args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
        # sse:
        server_url: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        # Optional source-label tag for downstream metadata.
        source_label: Optional[str] = None,
    ) -> AsyncGenerator[ConnectorRecord, None]:
        """
        Stream resources from one MCP server.

        Parameters
        ----------
        agent_id : str
            Owning agent namespace — stamped on every yielded record.
        run_state : RunState
            Per-source state for incremental sync. Mutated in-place.
        command, args, env :
            stdio transport. Spawn this command and pipe MCP over its
            stdin/stdout. ``args`` defaults to ``[]`` if omitted.
        server_url, headers :
            SSE transport. Connect to this URL with these HTTP headers.
        source_label :
            Optional human label included in each record's ``metadata`` for
            provenance (e.g. the connector_config row's display name).
        """
        if (command and server_url) or (not command and not server_url):
            raise ValueError(
                "MCPConnector requires exactly one of `command` (stdio) "
                "or `server_url` (sse)."
            )

        logger.info(
            "mcp_connector: start  agent_id=%s  transport=%s",
            agent_id, "stdio" if command else "sse",
        )

        if command:
            session_cm = self._stdio_session(command, args or [], env)
        else:
            session_cm = self._sse_session(server_url, headers)  # type: ignore[arg-type]

        async with session_cm as session:
            async for record in self._paginate_resources(
                session, agent_id, run_state, source_label
            ):
                yield record

        logger.info(
            "mcp_connector: complete  agent_id=%s  tracked_uris=%d",
            agent_id, len(run_state.source_states),
        )

    # ------------------------------------------------------------------
    # Transport helpers — return an async context manager that yields a
    # connected ``ClientSession``. Imports happen inside so test
    # environments without ``mcp`` installed can still import this module.
    # ------------------------------------------------------------------

    @staticmethod
    def _stdio_session(command: str, args: list[str], env: Optional[dict[str, str]]):
        from contextlib import asynccontextmanager

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        @asynccontextmanager
        async def _ctx():
            params = StdioServerParameters(command=command, args=args, env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

        return _ctx()

    @staticmethod
    def _sse_session(server_url: str, headers: Optional[dict[str, str]]):
        from contextlib import asynccontextmanager

        from mcp import ClientSession
        from mcp.client.sse import sse_client

        @asynccontextmanager
        async def _ctx():
            async with sse_client(server_url, headers=headers) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    yield session

        return _ctx()

    # ------------------------------------------------------------------
    # Resource pagination
    # ------------------------------------------------------------------

    async def _paginate_resources(
        self,
        session: Any,
        agent_id: str,
        run_state: RunState,
        source_label: Optional[str],
    ) -> AsyncGenerator[ConnectorRecord, None]:
        """Iterate ``list_resources`` pages, yielding records for new/changed items."""
        cursor: Optional[str] = None

        while True:
            page = await session.list_resources(cursor=cursor)

            for resource in page.resources:
                uri_str = str(resource.uri)

                # Fast skip via lastModified comparison
                stored = run_state.source_states.get(uri_str)
                meta_last_mod: Optional[str] = None
                if resource.meta:
                    meta_last_mod = resource.meta.get("lastModified")
                if (
                    stored is not None
                    and meta_last_mod is not None
                    and stored.get("last_modified") == meta_last_mod
                ):
                    logger.debug(
                        "mcp_connector: fast-skip uri=%s (lastModified unchanged)",
                        uri_str,
                    )
                    continue

                # Hash skip — read once, compare, only emit when changed
                try:
                    record = await self._fetch_resource(
                        session, resource, agent_id, run_state, source_label
                    )
                except Exception as exc:
                    logger.warning(
                        "mcp_connector: read_resource failed uri=%s: %s",
                        uri_str, exc,
                    )
                    await send_to_dlq(
                        payload={
                            "connector_id": self.connector_id,
                            "source_uri": uri_str,
                            "agent_id": agent_id,
                        },
                        connector_id=self.connector_id,
                        source_uri=uri_str,
                        agent_id=agent_id,
                        error=str(exc),
                        reason="mcp_read_error",
                    )
                    continue

                if record is not None:
                    yield record

            if not page.nextCursor:
                break
            cursor = page.nextCursor

    # ------------------------------------------------------------------
    # Single-resource fetch with hash comparison
    # ------------------------------------------------------------------

    async def _fetch_resource(
        self,
        session: Any,
        resource: Any,
        agent_id: str,
        run_state: RunState,
        source_label: Optional[str],
    ) -> Optional[ConnectorRecord]:
        """Read one resource, compare hash, return a record or ``None`` on hit."""
        from mcp.types import TextResourceContents

        uri_str = str(resource.uri)
        read_result = await session.read_resource(resource.uri)

        text_parts: list[str] = []
        mime_type = resource.mimeType or "text/plain"
        for block in read_result.contents:
            if isinstance(block, TextResourceContents):
                text_parts.append(block.text or "")
            else:
                # BlobResourceContents — hex-encode for hash + storage. The
                # downstream parser_service won't be able to do anything with
                # this, but the connector's job is to deliver the bytes; what
                # to extract is a separate concern.
                raw = getattr(block, "blob", b"")
                if isinstance(raw, (bytes, bytearray)):
                    text_parts.append(raw.hex())
                else:
                    text_parts.append(str(raw))

        content = "\n".join(text_parts)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        stored = run_state.source_states.get(uri_str)
        last_mod: Optional[str] = None
        if resource.meta:
            last_mod = resource.meta.get("lastModified")

        if stored is not None and stored.get("content_hash") == content_hash:
            # Refresh last_modified so future fast-skip can short-circuit
            stored["last_modified"] = last_mod or stored.get("last_modified")
            logger.debug(
                "mcp_connector: hash-skip uri=%s (content unchanged)", uri_str,
            )
            return None

        run_state.source_states[uri_str] = {
            "content_hash": content_hash,
            "last_modified": last_mod,
        }

        return ConnectorRecord(
            connector_id=self.connector_id,
            source_uri=uri_str,
            text=content,
            mime_type=mime_type,
            hints={
                "title": resource.name or getattr(resource, "title", None) or uri_str,
                "description": getattr(resource, "description", None),
                "parser": "mcp_passthrough",
            },
            agent_id=agent_id,
            metadata={
                "source_label": source_label,
                "mcp_uri": uri_str,
                "mcp_name": resource.name,
                "mcp_mime_type": resource.mimeType,
                "last_modified": last_mod,
                "content_hash": content_hash,
            },
        )
