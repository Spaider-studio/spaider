"""InferenceManager: owns the chat and embedding runtimes, exposes their
state, and keeps ``app.config.settings`` (the reused SpAIder settings
singleton) pointed at the live loopback endpoints so the reused services
(compressor, supersession judge) talk to the bundled runtime."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..paths import PortablePaths
from .client import LocalLLMClient
from .runtime import LlamaServerProcess, RuntimeError_

logger = logging.getLogger(__name__)


@dataclass
class RuntimeSlot:
    role: str
    model_id: Optional[str] = None
    model_path: Optional[Path] = None
    process: Optional[LlamaServerProcess] = None
    state: str = "stopped"  # stopped | starting | ready | error
    error: Optional[str] = None
    started_at: Optional[float] = None
    startup_seconds: Optional[float] = None

    @property
    def client(self) -> Optional[LocalLLMClient]:
        if self.process and self.state == "ready":
            return LocalLLMClient(self.process.base_url, self.process.api_key)
        return None


class InferenceManager:
    def __init__(self, paths: PortablePaths, runtime_dir: Path):
        self.paths = paths
        self.runtime_dir = runtime_dir
        self.chat = RuntimeSlot("chat")
        self.embedding = RuntimeSlot("embedding")
        self._lock = asyncio.Lock()
        self.threads: Optional[int] = None
        self.context_tokens = 8192

    def slot(self, role: str) -> RuntimeSlot:
        return self.chat if role == "chat" else self.embedding

    # ---- start / stop ------------------------------------------------------
    async def start(
        self, role: str, model_id: str, model_path: Path, *, context_tokens: Optional[int] = None
    ) -> RuntimeSlot:
        async with self._lock:
            slot = self.slot(role)
            if slot.process and slot.process.is_running() and slot.model_id == model_id and slot.state == "ready":
                return slot
            await self._stop_slot(slot)
            slot.model_id = model_id
            slot.model_path = model_path
            slot.state = "starting"
            slot.error = None
            slot.started_at = time.monotonic()
            ctx = context_tokens or self.context_tokens
            proc = LlamaServerProcess(
                runtime_dir=self.runtime_dir,
                model_path=model_path,
                role=role,
                log_path=self.paths.logs_dir / f"llama-{role}.log",
                context_tokens=ctx,
                threads=self.threads,
            )
            slot.process = proc
            try:
                size = model_path.stat().st_size if model_path.exists() else 0
                # Generous: cold reads from a USB SSD at ~100 MB/s plus startup.
                timeout = 60 + size / (40 * 1024 * 1024)
                await asyncio.to_thread(proc.start)
                await asyncio.to_thread(proc.wait_ready, timeout)
            except Exception as exc:  # noqa: BLE001
                slot.state = "error"
                slot.error = str(exc)
                await asyncio.to_thread(proc.stop)
                slot.process = None
                logger.error("runtime %s failed to start: %s", role, exc)
                raise RuntimeError_(str(exc)) from exc
            slot.state = "ready"
            slot.startup_seconds = round(time.monotonic() - slot.started_at, 2)
            self._apply_app_settings()
            logger.info("runtime %s ready on port %s (%.1fs)", role, proc.port, slot.startup_seconds)
            return slot

    async def stop(self, role: str) -> None:
        async with self._lock:
            await self._stop_slot(self.slot(role))
            self._apply_app_settings()

    async def stop_all(self) -> None:
        async with self._lock:
            await self._stop_slot(self.chat)
            await self._stop_slot(self.embedding)
            self._apply_app_settings()

    async def _stop_slot(self, slot: RuntimeSlot) -> None:
        if slot.process is not None:
            proc = slot.process
            await asyncio.to_thread(proc.stop)
        slot.process = None
        slot.state = "stopped"
        slot.model_id = None
        slot.model_path = None
        slot.error = None

    # ---- queries ------------------------------------------------------------
    def running_processes(self) -> int:
        return sum(1 for s in (self.chat, self.embedding) if s.process and s.process.is_running())

    def runtime_state(self) -> str:
        return self.chat.state

    def status(self) -> dict:
        return {
            "chat": {
                "state": self.chat.state,
                "model_id": self.chat.model_id,
                "error": self.chat.error,
                "port": self.chat.process.port if self.chat.process else None,
                "startup_seconds": self.chat.startup_seconds,
            },
            "embedding": {
                "state": self.embedding.state,
                "model_id": self.embedding.model_id,
                "error": self.embedding.error,
                "port": self.embedding.process.port if self.embedding.process else None,
                "startup_seconds": self.embedding.startup_seconds,
            },
        }

    # ---- reused SpAIder settings ---------------------------------------------
    def _apply_app_settings(self) -> None:
        """Point ``app.config.settings`` at the live servers. litellm's
        ``openai/`` provider sends ``Authorization: Bearer <api_key>``, which
        llama-server's ``--api-key`` accepts."""
        try:
            from app.config import settings  # noqa: WPS433 (reused SpAIder settings)
        except Exception:  # noqa: BLE001
            return
        if self.chat.process and self.chat.state == "ready":
            settings.llm_base_url = f"{self.chat.process.base_url}/v1"
            settings.llm_api_key = self.chat.process.api_key
        else:
            settings.llm_base_url = "http://127.0.0.1:1/v1"
            settings.llm_api_key = "local-placeholder"
        if self.embedding.process and self.embedding.state == "ready":
            settings.embedding_base_url = f"{self.embedding.process.base_url}/v1"
            settings.embedding_api_key = self.embedding.process.api_key
        else:
            settings.embedding_base_url = "http://127.0.0.1:1/v1"
            settings.embedding_api_key = "local-placeholder"
