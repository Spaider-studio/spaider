"""AppContext: wires paths, lock, database, registry, runtimes, memory and
chat together and owns the orderly startup/shutdown sequence."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .chat.service import ChatService
from .db import Database
from .inference.manager import InferenceManager
from .inference.runtime import runtime_version
from .lock import InstanceLock
from .memory.embedding_local import LocalEmbeddingService
from .memory.ingest import IngestQueue, IngestWorker, MemoryIngestor
from .memory.retrieve import MemoryRetriever
from .memory.store import LocalMemoryStore
from .models.catalog import load_catalog
from .models.jobs import JobManager
from .models.registry import ModelRegistry
from .paths import PortablePaths
from .store.conversations import ConversationStore
from .store.documents import DocumentStore
from .store.notes import NoteStore
from .store.settings import SettingsStore, UserSettings

logger = logging.getLogger(__name__)


class AppContext:
    def __init__(self, paths: PortablePaths, runtime_dir: Path, *, on_exit: Optional[Callable[[], None]] = None):
        self.paths = paths
        self.runtime_dir = runtime_dir
        self.on_exit = on_exit
        self.lock = InstanceLock(paths.lock_path)
        self.db = Database(paths.db_path)
        self.catalog = load_catalog()
        self.registry = ModelRegistry(self.db, paths)
        self.inference = InferenceManager(paths, runtime_dir)
        self.settings = SettingsStore(self.db)
        self.conversations = ConversationStore(self.db)
        self.documents = DocumentStore(self.db, paths)
        self.notes = NoteStore(self.db)
        self.store = LocalMemoryStore(self.db)
        self.embeddings = LocalEmbeddingService(self.inference)
        self.ingestor = MemoryIngestor(self.db, self.store, self.embeddings)
        self.ingest_queue = IngestQueue(self.db)
        self.retriever = MemoryRetriever(self.db, self.store, self.embeddings)
        self.chat_idle = asyncio.Event()
        self.chat = ChatService(
            self.conversations, self.retriever, self.inference, self.settings, self.ingest_queue, self.chat_idle
        )
        self.worker = IngestWorker(
            self.ingest_queue,
            self.ingestor,
            self.db,
            is_llm_ready=lambda: self.inference.chat.state == "ready",
            chat_idle=self.chat_idle,
        )
        self.jobs = JobManager(
            self.db,
            paths,
            self.registry,
            self.catalog,
            inference=self.inference,
            on_active_changed=self._on_active_changed,
        )
        self._shutdown_task: Optional[asyncio.Task] = None
        self._autostart_task: Optional[asyncio.Task] = None
        self._reindex_task: Optional[asyncio.Task] = None
        self.shutting_down = False
        self._runtime_version: Optional[str] = None

    # ---- lifecycle ----------------------------------------------------------
    def startup_sync(self) -> None:
        """Blocking part of startup (before the event loop serves)."""
        self.paths.ensure()
        self.lock.acquire()
        self.db.open()
        self.registry.reconcile()
        self.jobs.recover_after_crash()
        self.chat.recover_after_crash()
        prefs = self.settings.load()
        self.apply_settings(prefs)
        logger.info("SpAIder Foldable core %s, data dir %s", __version__, self.paths.data_dir)

    async def startup_async(self) -> None:
        self.worker.start()
        self._autostart_task = asyncio.create_task(self._autostart_models(), name="autostart-models")

    async def _autostart_models(self) -> None:
        prefs = self.settings.load()
        active = self.registry.active_ids()
        for role in ("chat", "embedding"):
            if role == "embedding" and not prefs.embedding_enabled:
                continue
            mid = active.get(role)
            if not mid:
                continue
            model = self.registry.get(mid)
            if not model or model["state"] != "ready":
                continue
            try:
                await self.inference.start(role, mid, self.registry.abs_path(model))
                await self._on_active_changed(role, mid)
            except Exception as exc:  # noqa: BLE001
                logger.error("autostart of %s model %s failed: %s", role, mid, exc)

    async def _on_active_changed(self, role: str, model_id: Optional[str]) -> None:
        if role != "embedding":
            return
        model = self.registry.get(model_id) if model_id else None
        dims = (model or {}).get("embedding_dimensions")
        self.store.set_index_model(model_id if model else None)
        try:
            from app.config import settings as app_settings

            if dims:
                app_settings.embedding_dimensions = int(dims)
        except Exception:  # noqa: BLE001
            pass
        if model_id and self.inference.embedding.state == "ready":
            if self._reindex_task is None or self._reindex_task.done():
                self._reindex_task = asyncio.create_task(self._reindex(model_id), name="reindex")

    async def _reindex(self, model_id: str) -> None:
        total = 0
        while not self.shutting_down and self.inference.embedding.model_id == model_id:
            await self.chat_idle.wait()
            n = await self.ingestor.reindex_missing(model_id)
            if n == 0:
                break
            total += n
            await asyncio.sleep(0.05)
        if total:
            logger.info("re-embedded %d nodes for %s", total, model_id)

    def apply_settings(self, prefs: UserSettings) -> None:
        self.inference.context_tokens = prefs.context_tokens
        self.inference.threads = prefs.threads
        self.ingestor.extraction_enabled = prefs.memory_auto_extract
        if not prefs.embedding_enabled and self.inference.embedding.state != "stopped":
            asyncio.get_event_loop().create_task(self.inference.stop("embedding"))
            self.store.set_index_model(None)

    def request_shutdown(self) -> None:
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self.shutdown(), name="shutdown")

    async def shutdown(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        logger.info("shutdown requested")
        try:
            await self.chat.stop_all()
            for t in (self._autostart_task, self._reindex_task):
                if t and not t.done():
                    t.cancel()
            await self.worker.stop()
            await self.jobs.shutdown()
            await self.inference.stop_all()
        finally:
            self.db.close()
            self.lock.release()
            logger.info("shutdown complete")
            if self.on_exit:
                self.on_exit()

    # ---- status helpers -------------------------------------------------------
    def setup_status(self) -> dict:
        active = self.active_models()
        state = "ready" if active["chat"] else "needs_model"
        return {
            "state": state,
            "active_chat_model": active["chat"],
            "active_embedding_model": active["embedding"],
            "runtime_state": self.inference.runtime_state(),
        }

    def active_models(self) -> dict:
        ids = self.registry.active_ids()
        return {
            "chat": self.registry.get(ids["chat"]) if ids["chat"] else None,
            "embedding": self.registry.get(ids["embedding"]) if ids["embedding"] else None,
            "runtime_state": self.inference.runtime_state(),
            "embedding_runtime_state": self.inference.embedding.state,
            "error": self.inference.chat.error,
            "runtime": self.inference.status(),
        }

    def embedding_dimensions(self) -> Optional[int]:
        mid = self.inference.embedding.model_id
        model = self.registry.get(mid) if mid else None
        return (model or {}).get("embedding_dimensions")

    def eject_status(self) -> dict:
        streams = self.chat.active_streams
        procs = self.inference.running_processes()
        running = self.jobs.running_count()
        ready = streams == 0 and procs == 0 and not self.db.is_open and running == 0
        if ready:
            msg = "Alle Ressourcen sind freigegeben."
        else:
            parts = []
            if streams:
                parts.append(f"{streams} laufende Antwort(en)")
            if procs:
                parts.append(f"{procs} Modellprozess(e)")
            if running:
                parts.append(f"{running} laufende Vorgänge")
            if self.db.is_open:
                parts.append("Datenbank geöffnet")
            msg = "Noch aktiv: " + ", ".join(parts) + ". Bitte „Sicher beenden“ verwenden."
        return {
            "ready": ready,
            "active_streams": streams,
            "runtime_processes": procs,
            "db_open": self.db.is_open,
            "running_jobs": running,
            "message_de": msg,
        }

    def runtime_info(self) -> dict:
        if self._runtime_version is None:
            self._runtime_version = runtime_version(self.runtime_dir)
        return {
            "name": "llama.cpp",
            "build": "b12032",
            "variant": "cpu",
            "version": self._runtime_version,
            "path": str(self.runtime_dir),
        }

    def read_log_tail(self, n: int) -> list[str]:
        p = self.paths.logs_dir / "core.log"
        try:
            return p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        except OSError:
            return []
