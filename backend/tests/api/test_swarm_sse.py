"""The pheromone SSE stream must emit a connect frame + heartbeats.

Regression guard for the "PHEROMONE STREAM always shows disconnected" bug:
the generator used to yield only when a real event arrived, so an idle
connection was killed by the proxy idle-timeout and EventSource flapped to
disconnected. The fix sends an immediate ``heartbeat`` connect frame and a
periodic keep-alive, and wraps real events as SSE data frames.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import swarm as swarm_module


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(swarm_module.router, prefix="/api/v1/swarm")
    return app


async def _one_event_then_stop(_redis):
    """Fake subscribe_to_swarm_logs: yield one pheromone frame then end."""
    yield json.dumps({"type": "pheromone", "agent": "system", "message": "Boosted 2 node(s)"})


@pytest.mark.asyncio
async def test_sse_emits_connect_frame_and_real_event():
    app = _make_app()
    with patch("app.api.v1.swarm._get_redis", new=AsyncMock(return_value=object())), \
         patch("app.api.v1.swarm.subscribe_to_swarm_logs", new=_one_event_then_stop):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            async with c.stream("GET", "/api/v1/swarm/events/stream") as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                body = ""
                async for chunk in resp.aiter_text():
                    body += chunk
                    if "pheromone" in body:
                        break

    frames = [f for f in body.split("\n\n") if f.strip()]
    # First frame is the immediate connect heartbeat (so onopen is reliable).
    first = json.loads(frames[0].removeprefix("data: "))
    assert first == {"type": "heartbeat", "message": "connected"}
    # The real event is wrapped as an SSE data frame.
    assert any('"type": "pheromone"' in f or '"type":"pheromone"' in f for f in frames)


@pytest.mark.asyncio
async def test_sse_heartbeat_only_when_redis_down():
    """Redis unavailable → still a 200 stream that opens with a connect frame.

    Consumes the StreamingResponse generator directly (one frame, then
    aclose) so we never enter the infinite heartbeat loop's teardown.
    """
    from starlette.requests import Request

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": "GET", "headers": [], "client": ("test", 0)}
    request = Request(scope, receive=_receive)

    with patch("app.api.v1.swarm._get_redis", new=AsyncMock(return_value=None)):
        resp = await swarm_module.swarm_events_stream(request)
        gen = resp.body_iterator
        first_raw = await gen.__anext__()
        await gen.aclose()

    first = json.loads(first_raw.removeprefix("data: ").split("\n\n")[0])
    assert first == {"type": "heartbeat", "message": "connected"}


@pytest.mark.asyncio
async def test_sse_idle_channel_emits_repeated_keepalives_without_teardown(monkeypatch):
    """An idle channel must keep emitting keep-alives, NOT tear the stream down.

    Regression for the flicker: the heartbeat used asyncio.wait_for(__anext__),
    which CANCELS the read on timeout, closing the Pub/Sub subscription and
    ending the stream every heartbeat. The browser then reconnected on a loop.
    The read must survive heartbeats, so a quiet channel yields connect +
    repeated keep-alives from a single, still-open subscription.
    """
    from starlette.requests import Request

    monkeypatch.setattr(swarm_module, "_HEARTBEAT_S", 0.05, raising=False)

    async def _blocks_forever(_redis):
        await asyncio.Event().wait()  # never yields, like a quiet channel
        yield  # pragma: no cover

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {"type": "http", "method": "GET", "headers": [], "client": ("t", 0)},
        receive=_receive,
    )
    with patch("app.api.v1.swarm._get_redis", new=AsyncMock(return_value=object())), \
         patch("app.api.v1.swarm.subscribe_to_swarm_logs", new=_blocks_forever):
        gen = (await swarm_module.swarm_events_stream(request)).body_iterator
        frames = [await asyncio.wait_for(gen.__anext__(), timeout=2) for _ in range(4)]
        await gen.aclose()

    msgs = [json.loads(f.removeprefix("data: ").split("\n\n")[0])["message"] for f in frames]
    assert msgs[0] == "connected"
    assert msgs.count("keep-alive") >= 2  # stream stayed open across heartbeats
