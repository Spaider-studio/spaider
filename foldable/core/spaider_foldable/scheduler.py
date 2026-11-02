"""Task scheduler for the single local inference runtime.

The llama-server runs with one slot, so every LLM call (chat answer, memory
extraction, supersession judge, embeddings, calibration) competes for the same
resource. Rules:

* The active chat has priority. When a chat request arrives while a
  background task holds the slot, the background task is **preempted**: its
  asyncio task is cancelled, which closes the HTTP stream and makes llama-server
  abort that generation; the work item goes back to the queue unchanged.
* Background work waits until the chat has been idle for ``cooldown_s`` (the
  user is probably still reading/typing) and runs with a duty cycle: after an
  item that took ``d`` seconds the worker pauses ``d * (1 - duty) / duty``
  (quiet mode: duty 0.5, auto: 0.75, fast: 1.0).
* ``pause()`` stops background work entirely (used during model switches and
  shutdown).

The scheduler is pure asyncio bookkeeping; it does not know about llama-server.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

DUTY = {"quiet": 0.5, "auto": 0.75, "fast": 1.0}


class Preempted(Exception):
    """Raised inside a background task when the chat takes over the slot."""


class LLMScheduler:
    def __init__(self, *, cooldown_s: float = 2.0, mode: str = "auto"):
        self.cooldown_s = cooldown_s
        self.mode = mode
        self._chat_active = 0
        self._last_chat_end = 0.0
        self._bg_task: Optional[asyncio.Task] = None
        self._bg_kind: Optional[str] = None
        self._bg_started = 0.0
        self._paused = False
        self._state_changed = asyncio.Event()
        self._listeners: list = []
        self.preemptions = 0

    # ---- state -------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in DUTY else "auto"

    def state(self) -> dict:
        return {
            "chat_active": self._chat_active > 0,
            "background": {
                "kind": self._bg_kind,
                "running_s": round(time.monotonic() - self._bg_started, 1) if self._bg_kind else 0,
            },
            "paused": self._paused,
            "mode": self.mode,
            "preemptions": self.preemptions,
        }

    def on_change(self, cb) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        self._state_changed.set()
        self._state_changed = asyncio.Event()
        for cb in self._listeners:
            try:
                cb(self.state())
            except Exception:  # noqa: BLE001
                pass

    # ---- chat side -----------------------------------------------------------
    @contextlib.asynccontextmanager
    async def chat(self):
        """Hold the slot for a chat answer; preempts background work first."""
        self._chat_active += 1
        try:
            await self.preempt_background()
            self._notify()
            yield
        finally:
            self._chat_active -= 1
            self._last_chat_end = time.monotonic()
            self._notify()

    async def preempt_background(self, wait_s: float = 3.0) -> bool:
        task = self._bg_task
        if task is None or task.done():
            return False
        self.preemptions += 1
        logger.info("scheduler: preempting background task %s for chat", self._bg_kind)
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait_s)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
        return True

    def touch_user_activity(self) -> None:
        """Any user-facing request (typing, navigation) delays background work a little."""
        self._last_chat_end = max(self._last_chat_end, time.monotonic() - self.cooldown_s + 0.5)

    # ---- background side -------------------------------------------------------
    def pause(self) -> None:
        self._paused = True
        self._notify()

    def resume(self) -> None:
        self._paused = False
        self._notify()

    @property
    def chat_active(self) -> bool:
        return self._chat_active > 0

    async def wait_for_background_slot(self) -> None:
        """Block until background work may run (no chat, cooldown elapsed, not paused)."""
        while True:
            if not self._paused and self._chat_active == 0:
                remaining = self.cooldown_s - (time.monotonic() - self._last_chat_end)
                if remaining <= 0:
                    return
                await asyncio.sleep(min(remaining, 0.5))
                continue
            await asyncio.sleep(0.25)

    @contextlib.asynccontextmanager
    async def background(self, kind: str):
        """Run one background item. The body runs as a separate task so that
        ``preempt_background`` can cancel it; ``Preempted`` is raised to the caller."""
        await self.wait_for_background_slot()
        self._bg_kind = kind
        self._bg_started = time.monotonic()
        self._notify()
        holder: dict = {}

        async def _runner(coro):
            return await coro

        class _Ctx:
            def run(self_inner, coro):
                task = asyncio.create_task(_runner(coro), name=f"bg-{kind}")
                holder["task"] = task
                self._bg_task = task
                return task

        try:
            yield _Ctx()
        finally:
            task = holder.get("task")
            elapsed = time.monotonic() - self._bg_started
            self._bg_task = None
            self._bg_kind = None
            self._notify()
            # duty cycle pause (never while a chat waits; the chat preempts anyway)
            duty = DUTY.get(self.mode, 0.75)
            if duty < 1.0 and task is not None and not task.cancelled():
                pause = min(elapsed * (1 - duty) / duty, 20.0)
                if pause > 0.05:
                    await asyncio.sleep(pause)

    async def run_background(self, kind: str, coro):
        """Convenience: run ``coro`` as a preemptible background item.
        Returns its result or raises ``Preempted``."""
        async with self.background(kind) as ctx:
            task = ctx.run(coro)
            try:
                return await task
            except asyncio.CancelledError:
                if task.cancelled() and self._chat_active > 0:
                    raise Preempted()
                raise
