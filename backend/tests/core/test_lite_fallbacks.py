"""The Neo4j-and-API-only deployment: nothing else may be required."""
from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.core import jobs as jobs_module
from app.core.jobs import JobQueue, QueueFull, drain_background, spawn
from app.core.kv import get_kv, kv_is_external
from app.services.audit_store import AuditStore


@pytest.fixture
def lite(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "kafka_bootstrap_servers", "")
    monkeypatch.setattr(settings, "clickhouse_host", "")
    monkeypatch.setattr(settings, "postgres_url", "")


async def test_kv_without_redis_shares_state_across_clients(lite):
    assert kv_is_external() is False
    await get_kv().set("k", "v")
    assert await get_kv(socket_timeout=2).get("k") == "v"
    raw = get_kv(decode_responses=False)
    assert await raw.get("k") == b"v"


async def test_kv_supports_the_stream_and_lease_calls_the_app_uses(lite):
    kv = get_kv()
    await kv.xgroup_create("s", "g", id="0", mkstream=True)
    await kv.xadd("s", {"a": "1"})
    messages = await kv.xreadgroup("g", "c", {"s": ">"}, count=1)
    assert messages[0][1][0][1] == {"a": "1"}
    assert await kv.set("lease", "me", nx=True, ex=5) is True
    assert await kv.set("lease", "other", nx=True, ex=5) is None


async def test_job_runs_and_reports_done(lite):
    queue = JobQueue(workers=1)

    async def handler(payload):
        return {"doubled": payload["n"] * 2}

    queue.register("double", handler)
    queue.start()
    job_id = await queue.submit("double", {"n": 21})
    await queue.stop()
    state = await queue.status(job_id)
    assert state["status"] == "done"
    assert state["result"] == {"doubled": 42}


async def test_job_retries_then_reports_failure(lite):
    queue = JobQueue(workers=1, max_attempts=3, retry_delay=0)
    calls = []

    async def handler(payload):
        calls.append(1)
        raise ValueError("boom")

    queue.register("bad", handler)
    queue.start()
    job_id = await queue.submit("bad", {})
    await queue.stop()
    state = await queue.status(job_id)
    assert (state["status"], state["attempts"], state["error"]) == ("failed", 3, "boom")
    assert len(calls) == 3


async def test_job_succeeds_on_retry(lite):
    queue = JobQueue(workers=1, retry_delay=0)
    calls = []

    async def handler(payload):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("transient")
        return "ok"

    queue.register("flaky", handler)
    queue.start()
    job_id = await queue.submit("flaky", {})
    await queue.stop()
    assert (await queue.status(job_id))["status"] == "done"


async def test_full_queue_is_refused(lite):
    queue = JobQueue(workers=1, max_size=1)

    async def handler(payload):
        return None

    queue.register("x", handler)
    await queue.submit("x", {})
    with pytest.raises(QueueFull):
        await queue.submit("x", {})


async def test_submit_does_not_reset_a_running_job(lite):
    queue = JobQueue(workers=1)

    async def handler(payload):
        return None

    queue.register("x", handler)
    await queue.submit("x", {}, job_id="fixed")
    await queue._update("fixed", status="running", attempts=1)
    await queue.submit("x", {}, job_id="fixed")
    assert (await queue.status("fixed"))["status"] == "running"


async def test_unknown_job_is_rejected(lite):
    with pytest.raises(KeyError):
        await JobQueue().submit("nope", {})


async def test_spawn_keeps_a_reference_until_done(lite):
    done = asyncio.Event()

    async def work():
        done.set()

    task = spawn(work(), name="t")
    assert task in jobs_module._background
    await drain_background()
    assert done.is_set()
    assert task not in jobs_module._background


async def test_spawn_logs_failures(lite, caplog):
    async def fail():
        raise RuntimeError("lost otherwise")

    spawn(fail(), name="failing-task")
    await drain_background()
    assert "failing-task" in caplog.text


async def test_connector_state_uses_sqlite_without_postgres(lite, monkeypatch, tmp_path):
    from sqlalchemy import select

    from app.db import postgres
    from app.models.connector import ConnectorConfig

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "nested"))
    monkeypatch.setattr(postgres, "_engine", None)
    await postgres.init_db()
    engine = postgres.get_engine()
    assert engine.dialect.name == "sqlite"
    assert (tmp_path / "nested" / "connectors.db").exists()

    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(engine) as session:
        session.add(ConnectorConfig(id="c1", type_id="url", agent_id="a", config_json={"u": [1]}))
        await session.commit()
        row = (await session.execute(select(ConnectorConfig))).scalar_one()
    assert row.config_json == {"u": [1]}
    await engine.dispose()
    monkeypatch.setattr(postgres, "_engine", None)


async def test_audit_store_round_trip(neo4j_test_driver):
    store = AuditStore(neo4j_test_driver)
    await store.record_event("w1", "kb-a", "ingest_received", {"nested": {"n": 1}})
    await store.record_event("w1", "kb-a", "ingest_completed", {})
    await store.record_event("w2", "kb-b", "query_received", {})

    events = await store.get_workflow_events("w1")
    assert [e["event_type"] for e in events] == ["ingest_received", "ingest_completed"]
    assert events[0]["payload"] == {"nested": {"n": 1}}

    only = await store.get_workflow_events("w1", event_types=["ingest_completed"])
    assert len(only) == 1

    assert {w["workflow_id"] for w in await store.list_workflows()} == {"w1", "w2"}
    mine = await store.list_workflows(agent_id="kb-b")
    assert [(w["workflow_id"], w["event_count"]) for w in mine] == [("w2", 1)]


async def _ticks_while(coro_factory, seconds: float = 0.6) -> int:
    """How often the event loop got to run something else during ``seconds``."""
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    tick_task = asyncio.create_task(ticker())
    work = asyncio.create_task(coro_factory())
    await asyncio.sleep(seconds)
    work.cancel()
    tick_task.cancel()
    await asyncio.gather(work, tick_task, return_exceptions=True)
    return ticks


async def test_swarm_listener_does_not_starve_the_loop_without_redis(lite):
    from unittest.mock import MagicMock

    from app.services.redis_service import initialize_stream
    from app.workers.swarm_listener import swarm_listener

    kv = get_kv()
    await initialize_stream(kv)
    graph = MagicMock()
    ticks = await _ticks_while(lambda: swarm_listener(redis_client=kv, graph_service=graph))
    assert ticks > 20


async def test_swarm_log_subscription_does_not_starve_the_loop_without_redis(lite):
    from app.services.redis_service import subscribe_to_swarm_logs

    async def consume():
        async for _ in subscribe_to_swarm_logs(get_kv()):
            pass

    assert await _ticks_while(consume) > 20
