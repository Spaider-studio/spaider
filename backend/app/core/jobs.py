"""
Background work without a broker.

``JobQueue`` runs registered handlers on a small worker pool and tracks status
in the key-value store. ``spawn`` is the fire-and-forget primitive: the event
loop only holds weak references to tasks, so a bare ``create_task`` can be
garbage-collected mid-flight and its exception is lost.

Queued jobs live in process memory and do not survive a restart. Deployments
that need durable delivery run the Kafka path instead.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.kv import get_kv

logger = logging.getLogger(__name__)

_JOB_KEY = "spaider:job:{id}"
_JOB_TTL_SECONDS = 86_400

Handler = Callable[[dict], Awaitable[Any]]

_background: set[asyncio.Task] = set()


def spawn(coro: Coroutine, *, name: Optional[str] = None) -> asyncio.Task:
    """Run ``coro`` in the background, keeping it alive and logging failures."""
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _background.add(task)

    def _finished(done: asyncio.Task) -> None:
        _background.discard(done)
        if not done.cancelled() and done.exception() is not None:
            logger.error("Background task %s failed", done.get_name(), exc_info=done.exception())

    task.add_done_callback(_finished)
    return task


async def drain_background(timeout: float = 10.0) -> None:
    """Wait for spawned tasks; cancel whatever is still running at the deadline."""
    tasks = [t for t in _background if t is not asyncio.current_task()]
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class QueueFull(RuntimeError):
    """The queue is at capacity; callers should surface 503."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobQueue:
    def __init__(
        self,
        *,
        workers: int = 2,
        max_size: int = 1000,
        max_attempts: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self._handlers: dict[str, Handler] = {}
        self._queue: asyncio.Queue[tuple[str, str, dict]] = asyncio.Queue(maxsize=max_size)
        self._workers = workers
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay
        self._tasks: list[asyncio.Task] = []

    def register(self, name: str, handler: Handler) -> None:
        self._handlers[name] = handler

    def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [
            asyncio.get_running_loop().create_task(self._worker(), name=f"job-worker-{i}")
            for i in range(self._workers)
        ]

    async def stop(self, *, drain: bool = True, timeout: float = 30.0) -> None:
        if drain and self._tasks:
            try:
                await asyncio.wait_for(self._queue.join(), timeout)
            except asyncio.TimeoutError:
                logger.warning("JobQueue stopped with %d jobs unfinished", self._queue.qsize())
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def submit(
        self, name: str, payload: dict, *, job_id: Optional[str] = None, agent_id: Optional[str] = None
    ) -> str:
        if name not in self._handlers:
            raise KeyError(f"No handler registered for job '{name}'")
        job_id = job_id or str(uuid.uuid4())
        if self._queue.full():
            raise QueueFull("Job queue is full")
        # nx: a duplicate submit must not reset the status of a running job.
        await self._write(
            job_id,
            {"id": job_id, "name": name, "status": "queued", "attempts": 0, "agent_id": agent_id, "created_at": _now()},
            only_if_absent=True,
        )
        self._queue.put_nowait((job_id, name, payload))
        return job_id

    async def status(self, job_id: str) -> Optional[dict]:
        raw = await get_kv().get(_JOB_KEY.format(id=job_id))
        return json.loads(raw) if raw else None

    async def _write(self, job_id: str, state: dict, *, only_if_absent: bool = False) -> None:
        state["updated_at"] = _now()
        await get_kv().set(
            _JOB_KEY.format(id=job_id),
            json.dumps(state, default=str),
            ex=_JOB_TTL_SECONDS,
            nx=only_if_absent,
        )

    async def _update(self, job_id: str, **changes: Any) -> None:
        state = await self.status(job_id) or {"id": job_id}
        state.update(changes)
        await self._write(job_id, state)

    async def _worker(self) -> None:
        while True:
            job_id, name, payload = await self._queue.get()
            try:
                await self._run(job_id, name, payload)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a status-write failure must not kill the worker
                logger.exception("Job %s could not be processed", job_id)
            finally:
                self._queue.task_done()

    async def _run(self, job_id: str, name: str, payload: dict) -> None:
        for attempt in range(1, self._max_attempts + 1):
            await self._update(job_id, status="running", attempts=attempt)
            try:
                result = await self._handlers[name](payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Job %s attempt %d failed: %s", job_id, attempt, exc)
                if attempt == self._max_attempts:
                    await self._update(job_id, status="failed", error=str(exc))
                    return
                await asyncio.sleep(self._retry_delay * 2 ** (attempt - 1))
            else:
                await self._update(job_id, status="done", result=result, error=None)
                return


_queue: Optional[JobQueue] = None


async def job_status(job_id: str) -> Optional[dict]:
    raw = await get_kv().get(_JOB_KEY.format(id=job_id))
    return json.loads(raw) if raw else None


async def mark_job_queued(job_id: str, agent_id: str) -> None:
    """Record a job handed to Kafka. A fast worker may already have written its
    status, so this only applies if nothing is there yet."""
    state = {"id": job_id, "status": "queued", "agent_id": agent_id, "created_at": _now(), "updated_at": _now()}
    await get_kv().set(_JOB_KEY.format(id=job_id), json.dumps(state), ex=_JOB_TTL_SECONDS, nx=True)


async def mark_job_finished(job_id: str, agent_id: str, status: str, error: Optional[str] = None) -> None:
    state = {"id": job_id, "status": status, "agent_id": agent_id, "error": error, "updated_at": _now()}
    await get_kv().set(_JOB_KEY.format(id=job_id), json.dumps(state), ex=_JOB_TTL_SECONDS)


def get_job_queue() -> JobQueue:
    global _queue
    if _queue is None:
        _queue = JobQueue()
    return _queue


def reset_job_queue() -> None:
    global _queue
    _queue = None
