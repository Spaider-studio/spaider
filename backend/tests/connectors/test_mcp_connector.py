"""
Unit tests for ``MCPConnector`` ([](/Spaider-studio/spaider/issues/53)).

Strategy
--------
We exercise ``_paginate_resources`` directly against an ``AsyncMock``-backed
``ClientSession``. No real subprocess or network — that would require an MCP
server binary in CI plus tolerate process-lifecycle races.

Coverage:

  1. Full sync — empty RunState → both resources yielded.
  2. Hash-skip — RunState matches both resources' content hashes → no records yielded.
  3. DLQ routing — resource 1 read raises, resource 2 succeeds → only #2
     yielded; ``app.connectors.send_to_dlq`` called for #1.
  4. Pagination — first page sets ``nextCursor``; second list call uses it.
  5. Fast-skip — ``resource.meta.lastModified`` matches stored state → no
     ``read_resource`` call at all (zero-token incremental).
"""
from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import AnyUrl

from mcp.types import (
    ListResourcesResult,
    ReadResourceResult,
    Resource,
    TextResourceContents,
)

from app.connectors import RunState
from app.connectors.mcp_connector import MCPConnector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resource(name: str, uri: str) -> Resource:
    return Resource(name=name, uri=AnyUrl(uri))


def _list_result(
    resources: list[Resource], next_cursor: str | None = None
) -> ListResourcesResult:
    return ListResourcesResult(resources=resources, nextCursor=next_cursor)


def _read_result(text: str, uri: str = "http://mcp-test/r") -> ReadResourceResult:
    return ReadResourceResult(
        contents=[TextResourceContents(uri=AnyUrl(uri), text=text)]
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _state(connector_id: str = "mcp") -> RunState:
    return RunState(connector_id=connector_id)


# ---------------------------------------------------------------------------
# 1. Full sync
# ---------------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_full_sync_yields_every_resource():
    connector = MCPConnector()
    session = AsyncMock()

    resources = [
        _resource("Doc Alpha", "http://mcp-test/docs/1"),
        _resource("Doc Beta", "http://mcp-test/docs/2"),
    ]
    session.list_resources.return_value = _list_result(resources)
    session.read_resource.side_effect = [
        _read_result("Content of doc alpha", "http://mcp-test/docs/1"),
        _read_result("Content of doc beta", "http://mcp-test/docs/2"),
    ]

    run_state = _state()
    records = []
    async for record in connector._paginate_resources(
        session, agent_id="agent-x", run_state=run_state, source_label=None
    ):
        records.append(record)

    assert len(records) == 2
    assert records[0].text == "Content of doc alpha"
    assert records[1].text == "Content of doc beta"
    assert records[0].source_uri == "http://mcp-test/docs/1"
    assert records[1].source_uri == "http://mcp-test/docs/2"
    # Each record carries the framework's connector_id and agent namespacing
    assert records[0].connector_id == "mcp"
    assert records[0].agent_id == "agent-x"
    # RunState now has both URIs tracked with content hashes
    assert run_state.source_states["http://mcp-test/docs/1"]["content_hash"] == _hash(
        "Content of doc alpha"
    )
    assert run_state.source_states["http://mcp-test/docs/2"]["content_hash"] == _hash(
        "Content of doc beta"
    )
    # Hints include parser tag + the resource's name as title
    assert records[0].hints["parser"] == "mcp_passthrough"
    assert records[0].hints["title"] == "Doc Alpha"


# ---------------------------------------------------------------------------
# 2. Hash skip — content unchanged since last run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hash_skip_when_content_unchanged():
    connector = MCPConnector()
    session = AsyncMock()

    text_a = "Content of doc alpha"
    text_b = "Content of doc beta"

    run_state = RunState(
        connector_id="mcp",
        source_states={
            "http://mcp-test/docs/1": {"content_hash": _hash(text_a), "last_modified": None},
            "http://mcp-test/docs/2": {"content_hash": _hash(text_b), "last_modified": None},
        },
    )

    session.list_resources.return_value = _list_result([
        _resource("Doc Alpha", "http://mcp-test/docs/1"),
        _resource("Doc Beta", "http://mcp-test/docs/2"),
    ])
    session.read_resource.side_effect = [
        _read_result(text_a, "http://mcp-test/docs/1"),
        _read_result(text_b, "http://mcp-test/docs/2"),
    ]

    records = []
    async for record in connector._paginate_resources(
        session, agent_id="agent-x", run_state=run_state, source_label=None
    ):
        records.append(record)

    assert records == []  # nothing yielded
    # Both reads happened (we couldn't fast-skip without lastModified)
    assert session.read_resource.call_count == 2


# ---------------------------------------------------------------------------
# 3. DLQ routing — failed resources don't abort the stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dlq_routing_on_read_failure():
    connector = MCPConnector()
    session = AsyncMock()

    session.list_resources.return_value = _list_result([
        _resource("Broken Doc", "http://mcp-test/docs/broken"),
        _resource("Good Doc", "http://mcp-test/docs/good"),
    ])
    session.read_resource.side_effect = [
        Exception("upstream read timeout"),
        _read_result("Good content", "http://mcp-test/docs/good"),
    ]

    run_state = _state()
    with patch(
        "app.connectors.mcp_connector.send_to_dlq", new_callable=AsyncMock
    ) as dlq_mock:
        records = []
        async for record in connector._paginate_resources(
            session, agent_id="agent-x", run_state=run_state, source_label=None
        ):
            records.append(record)

    assert len(records) == 1
    assert records[0].source_uri == "http://mcp-test/docs/good"
    assert records[0].text == "Good content"

    dlq_mock.assert_called_once()
    dlq_kwargs = dlq_mock.call_args.kwargs
    assert dlq_kwargs["source_uri"] == "http://mcp-test/docs/broken"
    assert dlq_kwargs["connector_id"] == "mcp"
    assert dlq_kwargs["reason"] == "mcp_read_error"


# ---------------------------------------------------------------------------
# 4. Pagination — nextCursor drives the second list call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pagination_follows_next_cursor():
    connector = MCPConnector()
    session = AsyncMock()

    session.list_resources.side_effect = [
        _list_result(
            [_resource("Doc 1", "http://mcp-test/docs/1")], next_cursor="page2-token"
        ),
        _list_result(
            [_resource("Doc 2", "http://mcp-test/docs/2")], next_cursor=None
        ),
    ]
    session.read_resource.side_effect = [
        _read_result("c1", "http://mcp-test/docs/1"),
        _read_result("c2", "http://mcp-test/docs/2"),
    ]

    records = []
    async for record in connector._paginate_resources(
        session, agent_id="agent-x", run_state=_state(), source_label=None
    ):
        records.append(record)

    assert len(records) == 2
    assert session.list_resources.call_count == 2
    second_call_kwargs = session.list_resources.call_args_list[1].kwargs
    assert second_call_kwargs.get("cursor") == "page2-token"


# ---------------------------------------------------------------------------
# 5. lastModified fast-skip — no read at all when meta matches state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_last_modified_fast_skip_avoids_read():
    connector = MCPConnector()
    session = AsyncMock()

    uri = "http://mcp-test/docs/1"
    # Resource.meta uses alias '_meta' — set via model_validate to honour it.
    res = Resource.model_validate({
        "name": "Doc 1",
        "uri": uri,
        "_meta": {"lastModified": "2025-01-01T00:00:00Z"},
    })

    run_state = RunState(
        connector_id="mcp",
        source_states={
            uri: {"content_hash": "old-hash", "last_modified": "2025-01-01T00:00:00Z"},
        },
    )

    session.list_resources.return_value = _list_result([res])

    records = []
    async for record in connector._paginate_resources(
        session, agent_id="agent-x", run_state=run_state, source_label=None
    ):
        records.append(record)

    assert records == []
    session.read_resource.assert_not_called()
