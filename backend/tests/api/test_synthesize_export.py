"""Endpoint tests for the /synthesize export surface (DPO + ChatML).

Harness: a FastAPI app mounting only the synthesize router. GraphService and
the DPO pair stream are replaced with deterministic fakes; the clearance
dependency is overridden so no Redis/auth stack is needed.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import synthesize as synthesize_module
from app.scripts.synthesizer_export import DPOSample


@pytest.fixture(autouse=True)
def _reset_graph_singleton():
    """The module caches GraphService in a global — reset around each test."""
    before = synthesize_module._graph_service
    yield
    synthesize_module._graph_service = before


def _make_app(graph_service) -> FastAPI:
    app = FastAPI()
    app.include_router(synthesize_module.router, prefix="/api/v1/synthesize")
    app.dependency_overrides[synthesize_module._resolve_caller_clearance] = lambda: 5
    synthesize_module._graph_service = graph_service
    return app


def _sample(i: int) -> DPOSample:
    return DPOSample(
        prompt=f"What can you tell me about Thing{i} (CONCEPT)?",
        chosen=f"THOUGHT: Thing{i} -[:RELATES_TO]-> Other\nANSWER: chosen {i}",
        rejected=f"ANSWER: rejected {i}",
        agent_id="agent-x",
    )


@pytest.mark.asyncio
async def test_dpo_export_streams_pairs():
    async def fake_stream(driver, agent_id, limit, batch_size, max_depth, caller_clearance=5):
        for i in range(2):
            yield _sample(i)

    with patch("app.scripts.synthesizer_export._stream_dpo_pairs", fake_stream):
        app = _make_app(MagicMock())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.get("/api/v1/synthesize/dpo", params={"agent_id": "agent-x"})

    assert resp.status_code == 200
    assert 'filename="spaider_agent-x_dpo.jsonl"' in resp.headers["content-disposition"]
    lines = [json.loads(line) for line in resp.text.strip().splitlines()]
    assert len(lines) == 2
    assert set(lines[0]) == {"prompt", "chosen", "rejected"}
    assert lines[0]["chosen"].startswith("THOUGHT:")


@pytest.mark.asyncio
async def test_dpo_export_422_when_graph_has_no_usage_signal():
    """A freshly-seeded graph (no energy separation) must fail loud and
    actionable — not hand the caller an empty training file."""

    async def empty_stream(driver, agent_id, limit, batch_size, max_depth, caller_clearance=5):
        return
        yield  # pragma: no cover — makes this an async generator

    with patch("app.scripts.synthesizer_export._stream_dpo_pairs", empty_stream):
        app = _make_app(MagicMock())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.get("/api/v1/synthesize/dpo", params={"agent_id": "agent-x"})

    assert resp.status_code == 422
    assert "usage signal" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dpo_export_requires_agent_id():
    app = _make_app(MagicMock())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/v1/synthesize/dpo")
    assert resp.status_code == 422  # missing required query param


# ---------------------------------------------------------------------------
# ChatML export — minimal async Neo4j fakes
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __aiter__(self):
        self._iter = iter(self._records)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeSession:
    def __init__(self, records: list[dict]):
        self._records = records

    async def run(self, *_a, **_kw):
        return _FakeResult(self._records)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeGraph:
    def __init__(self, records: list[dict]):
        self._driver = MagicMock()
        self._driver.session = lambda: _FakeSession(records)


@pytest.mark.asyncio
async def test_chatml_export_prompts_are_english():
    """The exported ChatML system/user prompts must be English — they used to
    be German dev leftovers, which made every public download look broken."""
    record = {
        "id": "n1", "label": "Olivia", "type": "PERSON",
        "properties": json.dumps({"description": "CTO of AcmeAI"}),
        "agent_id": "agent-x",
    }
    app = _make_app(_FakeGraph([record]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/v1/synthesize/export", params={"agent_id": "agent-x"})

    assert resp.status_code == 200
    line = json.loads(resp.text.strip().splitlines()[0])
    contents = " ".join(m["content"] for m in line["messages"])
    assert "What can you tell me about" in contents
    assert "CTO of AcmeAI" in contents
    assert "Du bist" not in contents
