"""InferenceManager: owns the chat and embedding runtimes, chooses the runtime
variant (CPU or GPU backend) from the planned ``RuntimeOptions``, falls back to
CPU when a GPU start fails, exposes state, and keeps ``app.config.settings``
(the reused SpAIder settings singleton) pointed at the live loopback endpoints
so the reused services (compressor, supersession judge) talk to the bundled
runtime."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..paths import PortablePaths
from .client import LocalLLMClient
from .options import RuntimeOptions
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
    options: dict = field(default_factory=dict)
    _client: Optional[LocalLLMClient] = field(default=None, repr=False)

    @property
    def client(self) -> Optional[LocalLLMClient]:
        """One client (with a persistent HTTP connection) per server process."""
        if self.process and self.state == "ready":
            if self._client is None or self._client.base_url != self.process.base_url.rstrip("/"):
                self._client = LocalLLMClient(self.process.base_url, self.process.api_key)
            return self._client
        return None

    @property
    def backend(self) -> str:
        return str(self.options.get("backend") or "cpu")


class InferenceManager:
    def __init__(self, paths: PortablePaths, runtime_dirs: dict[str, Path]):
        self.paths = paths
        self.runtime_dirs = {k: Path(v) for k, v in runtime_dirs.items() if Path(v).exists()}
        if "cpu" not in self.runtime_dirs and runtime_dirs:
            # keep the configured cpu dir even if it does not exist yet (error surfaces at start)
            self.runtime_dirs["cpu"] = Path(runtime_dirs.get("cpu") or next(iter(runtime_dirs.values())))
        self.chat = RuntimeSlot("chat")
        self.embedding = RuntimeSlot("embedding")
        self._lock = asyncio.Lock()
        self.threads: Optional[int] = None
        self.context_tokens = 8192
        self.gpu_failures: set[str] = set()  # backend names that failed to start on this machine
        self.oom_models: set[str] = set()  # model ids that ran out of GPU memory during generation (next start: CPU)
        self.on_state: Optional[Callable[[str, dict], None]] = None

    @property
    def runtime_dir(self) -> Path:
        return self.runtime_dirs.get("cpu", next(iter(self.runtime_dirs.values())))

    def slot(self, role: str) -> RuntimeSlot:
        return self.chat if role == "chat" else self.embedding

    def _notify(self, role: str) -> None:
        if self.on_state:
            try:
                self.on_state(role, self.status()[role])
            except Exception:  # noqa: BLE001
                pass

    # ---- start / stop ------------------------------------------------------
    async def start(
        self,
        role: str,
        model_id: str,
        model_path: Path,
        *,
        context_tokens: Optional[int] = None,
        options: Optional[RuntimeOptions] = None,
        n_layers: Optional[int] = None,
        mmproj_path: Optional[Path] = None,
    ) -> RuntimeSlot:
        async with self._lock:
            slot = self.slot(role)
            if slot.process and slot.process.is_running() and slot.model_id == model_id and slot.state == "ready":
                return slot
            await self._stop_slot(slot)
            opts = options or RuntimeOptions(threads=self.threads)
            if opts.backend != "cpu" and opts.backend not in self.runtime_dirs:
                opts.backend = "cpu"
                opts.reason_de = "Runtime-Variante für die GPU ist nicht gebündelt; CPU wird verwendet."
            attempts = [opts]
            if opts.backend != "cpu":
                attempts.append(
                    RuntimeOptions(
                        backend="cpu",
                        threads=opts.threads,
                        threads_batch=opts.threads_batch,
                        prio=opts.prio,
                        reason_de="Rückfall auf CPU nach GPU-Startfehler.",
                    )
                )
            last_error: Optional[str] = None
            failed_gpu_backend: Optional[str] = None
            for attempt in attempts:
                try:
                    await self._start_once(
                        slot, role, model_id, model_path, context_tokens or self.context_tokens, attempt, n_layers, mmproj_path
                    )
                    if failed_gpu_backend and attempt.backend == "cpu":
                        # the same file runs on the CPU: the failure was GPU-specific, remember it
                        self.gpu_failures.add(failed_gpu_backend)
                    return slot
                except RuntimeError_ as exc:
                    last_error = str(exc)
                    if attempt.backend != "cpu":
                        logger.error(
                            "GPU backend %s failed for %s: %s; trying CPU", attempt.backend, model_id, exc
                        )
                        failed_gpu_backend = attempt.backend
                    else:
                        # CPU failed as well: the model file is the problem, not the GPU
                        break
            slot.state = "error"
            slot.error = last_error
            self._notify(role)
            raise RuntimeError_(last_error or "runtime start failed")

    async def _start_once(
        self,
        slot: RuntimeSlot,
        role: str,
        model_id: str,
        model_path: Path,
        ctx: int,
        opts: RuntimeOptions,
        n_layers: Optional[int],
        mmproj_path: Optional[Path] = None,
    ) -> None:
        slot.model_id = model_id
        slot.model_path = model_path
        slot.state = "starting"
        slot.error = None
        slot.started_at = time.monotonic()
        gpu_layers = opts.gpu_layers
        if gpu_layers is not None and gpu_layers < 0:  # percentage marker from the planner
            gpu_layers = max(1, round((-gpu_layers / 100.0) * (n_layers or 32)))
        slot.options = {**opts.to_dict(), "gpu_layers": gpu_layers, "mmproj": str(mmproj_path) if mmproj_path else None}
        self._notify(role)
        proc = LlamaServerProcess(
            runtime_dir=self.runtime_dirs[opts.backend],
            model_path=model_path,
            role=role,
            log_path=self.paths.logs_dir / f"llama-{role}.log",
            context_tokens=ctx,
            threads=opts.threads or self.threads,
            threads_batch=opts.threads_batch,
            prio=opts.prio,
            gpu_layers=gpu_layers,
            backend=opts.backend,
            mmproj_path=mmproj_path,
            mmproj_offload=opts.mmproj_offload,
            image_max_tokens=opts.image_max_tokens,
        )
        slot.process = proc
        try:
            size = model_path.stat().st_size if model_path.exists() else 0
            timeout = 90 + size / (40 * 1024 * 1024)  # cold USB reads + Vulkan shader compilation
            await asyncio.to_thread(proc.start)
            await asyncio.to_thread(proc.wait_ready, timeout)
        except Exception as exc:  # noqa: BLE001
            await asyncio.to_thread(proc.stop)
            slot.process = None
            slot.state = "error"
            slot.error = str(exc)
            raise RuntimeError_(str(exc)) from exc
        slot.state = "ready"
        slot.startup_seconds = round(time.monotonic() - slot.started_at, 2)
        self._apply_app_settings()
        self._notify(role)
        logger.info(
            "runtime %s ready on port %s via %s (%.1fs) %s",
            role,
            proc.port,
            opts.backend,
            slot.startup_seconds,
            opts.reason_de,
        )

    async def stop(self, role: str) -> None:
        async with self._lock:
            await self._stop_slot(self.slot(role))
            self._apply_app_settings()
            self._notify(role)

    async def stop_all(self) -> None:
        async with self._lock:
            await self._stop_slot(self.chat)
            await self._stop_slot(self.embedding)
            self._apply_app_settings()

    async def _stop_slot(self, slot: RuntimeSlot) -> None:
        if slot._client is not None:
            await slot._client.aclose()
            slot._client = None
        if slot.process is not None:
            proc = slot.process
            await asyncio.to_thread(proc.stop)
        slot.process = None
        slot.state = "stopped"
        slot.model_id = None
        slot.model_path = None
        slot.error = None
        slot.options = {}

    # ---- queries ------------------------------------------------------------
    def running_processes(self) -> int:
        return sum(1 for s in (self.chat, self.embedding) if s.process and s.process.is_running())

    def runtime_state(self) -> str:
        return self.chat.state

    def status(self) -> dict:
        def one(s: RuntimeSlot) -> dict:
            return {
                "state": s.state,
                "model_id": s.model_id,
                "error": s.error,
                "port": s.process.port if s.process else None,
                "startup_seconds": s.startup_seconds,
                "backend": s.backend if s.process else None,
                "options": s.options,
            }

        return {"chat": one(self.chat), "embedding": one(self.embedding)}

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
