"""AppContext: wires paths, lock, database, registry, runtimes, scheduler,
activity bus, hardware profile, memory and chat together and owns the orderly
startup/shutdown sequence."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .activity import ActivityBus, utcnow
from .chat.service import ChatService
from .db import Database
from .hardware import HardwareProfile, detect, profile_from_dict, refresh_dynamic
from .inference.manager import InferenceManager
from .inference.options import RuntimeOptions, estimate_kv_bytes, plan
from .inference.runtime import runtime_version
from .lock import InstanceLock
from .memory.embedding_local import LocalEmbeddingService
from .memory.ingest import IngestQueue, IngestWorker, MemoryIngestor
from .memory.retrieve import MemoryRetriever
from .memory.store import LocalMemoryStore
from .agent.broker import Broker
from .agent.loop import AgentManager
from .agent.store import TaskStore
from .agent.tools import files as files_tools
from .agent.tools import browser as browser_tools
from .agent.tools import memory as memory_tools
from .agent.tools import vision as vision_tools
from .agent.tools import windows as windows_tools
from .live.sessions import LiveManager
from .tools.contracts import current_host_id
from .models.capabilities import capabilities_for_model
from .observe.uia import UiaProvider
from .models.catalog import load_catalog
from .models.jobs import JobManager
from .models.registry import ModelRegistry
from .paths import PortablePaths
from .scheduler import LLMScheduler
from .store.conversations import ConversationStore
from .store.documents import DocumentStore
from .store.notes import NoteStore
from .store.settings import SettingsStore, UserSettings

logger = logging.getLogger(__name__)


class AppContext:
    def __init__(
        self,
        paths: PortablePaths,
        runtime_dirs: dict[str, Path] | Path,
        *,
        on_exit: Optional[Callable[[], None]] = None,
    ):
        if isinstance(runtime_dirs, (str, Path)):
            runtime_dirs = {"cpu": Path(runtime_dirs)}
        self.paths = paths
        self.runtime_dirs = {k: Path(v) for k, v in runtime_dirs.items()}
        self.runtime_dir = self.runtime_dirs.get("cpu") or next(iter(self.runtime_dirs.values()))
        self.on_exit = on_exit
        self.lock = InstanceLock(paths.lock_path)
        self.db = Database(paths.db_path)
        self.catalog = load_catalog(local_path=paths.data_dir / "catalog.local.json")
        self.registry = ModelRegistry(self.db, paths)
        self.inference = InferenceManager(paths, self.runtime_dirs)
        self.settings = SettingsStore(self.db)
        self.conversations = ConversationStore(self.db)
        self.documents = DocumentStore(self.db, paths)
        self.notes = NoteStore(self.db)
        self.store = LocalMemoryStore(self.db)
        self.embeddings = LocalEmbeddingService(self.inference)
        self.ingestor = MemoryIngestor(self.db, self.store, self.embeddings)
        self.ingest_queue = IngestQueue(self.db)
        self.retriever = MemoryRetriever(self.db, self.store, self.embeddings)
        self.scheduler = LLMScheduler()
        self.bus = ActivityBus()
        self.chat = ChatService(
            self.conversations,
            self.retriever,
            self.inference,
            self.settings,
            self.ingest_queue,
            self.scheduler,
            self.bus,
            self.store,
            capabilities_for=self.capabilities_for,
        )
        self.uia = UiaProvider()
        self.live = LiveManager(
            uia=self.uia,
            conversations=self.conversations,
            vision_available=self.vision_available,
            on_change=lambda s: self.bus.publish("live", s),
            max_side=lambda: self.settings.load().live_image_max_side,
        )
        self.agent_snapshots = {}
        self.tasks = TaskStore(self.db, current_host_id())
        self.broker = Broker()
        files_tools.register(self.broker, parse_document=self._parse_document_file)
        memory_tools.register(self.broker, retriever=self.retriever)
        self.agent = AgentManager(
            store=self.tasks,
            broker=self.broker,
            scheduler=self.scheduler,
            bus=self.bus,
            get_client=lambda: self.inference.chat.client,
            observe_sources=self._observe_for_agent,
            settings_loader=self.settings.load,
            reasoning_toggle=self._active_reasoning_toggle,
        )
        windows_tools.register(
            self.broker,
            uia=self.uia,
            take_snapshot=self._agent_snapshot,
            get_snapshot=lambda tid, sid: self.agent_snapshots.get((tid, sid)),
            resolve_source=self.live.resolve_source,
            remember_names=self.agent.remember_element_names,
        )
        self.grounding = vision_tools.GroundingCalibrator()
        vision_tools.register(
            self.broker,
            vision_available=self.vision_available,
            active_model_id=lambda: self.inference.chat.model_id,
            ask_vlm=self.ask_vlm,
            take_snapshot=self._agent_snapshot,
            resolve_source=self.live.resolve_source,
            calibrator=self.grounding,
            max_side=lambda: self.settings.load().live_image_max_side,
        )
        self.browser = browser_tools.BrowserAdapter(paths.data_dir, browser_tools.find_chrome(paths.data_dir))
        browser_tools.register(self.broker, self.browser)
        self.worker = IngestWorker(
            self.ingest_queue,
            self.ingestor,
            self.db,
            is_llm_ready=lambda: self.inference.chat.state == "ready",
            llm_state=lambda: self.inference.chat.state,
            scheduler=self.scheduler,
            on_event=self._on_worker_event,
        )
        self.jobs = JobManager(
            self.db,
            paths,
            self.registry,
            self.catalog,
            inference=self.inference,
            on_active_changed=self._on_active_changed,
            runtime_options_for=self.runtime_options_for,
        )
        self.scheduler.on_change(lambda st: self.bus.publish("scheduler", st))
        self.jobs.on_calibrated = self._on_calibrated
        self.jobs.on_browser_installed = lambda chrome: setattr(self.browser, "chrome_path", chrome)
        self.inference.on_state = lambda role, st: self.bus.publish("runtime", {"role": role, **st})
        self._shutdown_task: Optional[asyncio.Task] = None
        self._autostart_task: Optional[asyncio.Task] = None
        self._reindex_task: Optional[asyncio.Task] = None
        self._hardware_task: Optional[asyncio.Task] = None
        self._hardware: Optional[HardwareProfile] = None
        self.shutting_down = False
        self._runtime_version: Optional[str] = None
        self.agent_snapshots: dict = {}

    # ---- lifecycle ----------------------------------------------------------
    def startup_sync(self) -> None:
        """Blocking part of startup (before the event loop serves)."""
        self.paths.ensure()
        self.lock.acquire()
        self.db.open()
        self.registry.reconcile()
        self.jobs.recover_after_crash()
        interrupted = self.tasks.mark_interrupted_after_crash()
        if interrupted:
            logger.info("agent: %d task(s) marked interrupted after restart", len(interrupted))
        self._load_dynamic_catalog()
        self.chat.recover_after_crash()
        prefs = self.settings.load()
        self.apply_settings(prefs)
        self._load_cached_hardware()
        logger.info(
            "SpAIder Foldable core %s, data dir %s, runtimes %s",
            __version__,
            self.paths.data_dir,
            sorted(self.runtime_dirs),
        )

    async def startup_async(self) -> None:
        self.worker.start()
        # UI Automation warm-up in the background: the first pywinauto/comtypes use generates
        # typelib wrappers (seconds in a frozen build); doing it now keeps the first live question fast
        asyncio.get_running_loop().run_in_executor(None, self.uia.warmup)
        self._hardware_task = asyncio.create_task(self._detect_hardware(), name="hardware-detect")
        self._autostart_task = asyncio.create_task(self._autostart_models(), name="autostart-models")

    # ---- catalog ----------------------------------------------------------------
    def _load_dynamic_catalog(self) -> None:
        from .models.catalog import CatalogEntry

        for row in self.db.query("SELECT entry_json FROM catalog_entries"):
            try:
                self.catalog.add(CatalogEntry.model_validate(json.loads(row["entry_json"])))
            except Exception as exc:  # noqa: BLE001
                logger.warning("dynamic catalog entry unusable: %s", exc)

    def save_dynamic_entry(self, entry) -> None:
        now = utcnow()
        self.db.execute(
            "INSERT INTO catalog_entries(id, source_kind, entry_json, created_at, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET entry_json = excluded.entry_json, updated_at = excluded.updated_at",
            (entry.id, "search", entry.model_dump_json(), now, now),
        )
        self.catalog.add(entry)

    def _on_calibrated(self, model: dict, result: dict) -> None:
        self.record_profile(
            model,
            backend=result.get("backend") or "cpu",
            threads=result.get("threads"),
            gpu_layers=result.get("gpu_layers"),
            tokens_per_second=result.get("tokens_per_second"),
            ttft_ms=result.get("ttft_ms"),
            prompt_tps=result.get("prompt_tps"),
            load_seconds=result.get("load_seconds"),
        )

    def measured_for_entry(self, entry) -> Optional[dict]:
        hw = self._hardware
        if hw is None:
            return None
        shas = [f.sha256 for f in entry.files if f.sha256]
        if not shas:
            return None
        marks = ",".join("?" * len(shas))
        row = self.db.query_one(
            f"SELECT * FROM model_profiles WHERE fingerprint = ? AND model_sha256 IN ({marks}) ORDER BY measured_at DESC LIMIT 1",  # noqa: E501
            [hw.fingerprint, *shas],
        )
        if not row:
            return None
        return {
            "backend": row["backend"],
            "tokens_per_second": row["tokens_per_second"],
            "time_to_first_token_ms": row["time_to_first_token_ms"],
            "measured_at": row["measured_at"],
        }

    # ---- hardware -------------------------------------------------------------
    def _load_cached_hardware(self) -> None:
        row = self.db.query_one(
            "SELECT profile_json, detected_at FROM hardware_profiles ORDER BY detected_at DESC LIMIT 1"
        )
        if row:
            try:
                self._hardware = refresh_dynamic(
                    profile_from_dict(json.loads(row["profile_json"])), self.paths.data_dir
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("cached hardware profile unusable: %s", exc)

    async def _detect_hardware(self) -> None:
        try:
            hw = await asyncio.to_thread(detect, self.paths.data_dir, self.runtime_dirs)
        except Exception as exc:  # noqa: BLE001
            logger.error("hardware detection failed: %s", exc)
            return
        self._hardware = hw
        self.db.execute(
            "INSERT INTO hardware_profiles(fingerprint, profile_json, detected_at) VALUES (?,?,?) "
            "ON CONFLICT(fingerprint) DO UPDATE SET profile_json = excluded.profile_json, detected_at = excluded.detected_at",  # noqa: E501
            (hw.fingerprint, json.dumps(hw.to_dict(), ensure_ascii=False), hw.detected_at),
        )
        logger.info(
            "hardware: %s, %d GB RAM, GPUs %s",
            hw.cpu.get("name"),
            round(hw.ram_total_bytes / 2**30),
            [g.name for g in hw.gpus] or "none",
        )
        self.bus.publish("hardware", hw.to_dict())

    async def hardware_profile(self, *, wait: bool = True) -> Optional[HardwareProfile]:
        if self._hardware is None and wait and self._hardware_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._hardware_task), timeout=30)
            except Exception:  # noqa: BLE001
                pass
        if self._hardware is not None:
            refresh_dynamic(self._hardware, self.paths.data_dir)
        return self._hardware

    def planned_backend(self) -> str:
        prefs = self.settings.load()
        forced = os.environ.get("SPAIDER_FOLDABLE_BACKEND")
        if forced:
            return forced
        hw = self._hardware
        if (
            prefs.gpu_mode == "off"
            or hw is None
            or hw.best_gpu is None
            or hw.best_gpu.backend in self.inference.gpu_failures
        ):
            return "cpu"
        return hw.best_gpu.backend if hw.best_gpu.backend in self.runtime_dirs else "cpu"

    def runtime_options_for(self, model: dict) -> tuple[RuntimeOptions, Optional[int]]:
        """Plan launch options for an installed model (dict from the registry)."""
        prefs = self.settings.load()
        meta = model.get("metadata") or {}
        arch = model.get("architecture") or ""
        n_layers = _meta_int(meta, f"{arch}.block_count")
        n_kv_heads = _meta_int(meta, f"{arch}.attention.head_count_kv") or _meta_int(
            meta, f"{arch}.attention.head_count"
        )
        head_dim = None
        emb = _meta_int(meta, f"{arch}.embedding_length")
        heads = _meta_int(meta, f"{arch}.attention.head_count")
        if emb and heads:
            head_dim = emb // heads
        kv = estimate_kv_bytes(prefs.context_tokens, n_layers=n_layers, n_kv_heads=n_kv_heads, head_dim=head_dim)
        forced = os.environ.get("SPAIDER_FOLDABLE_BACKEND")
        hw = self._hardware
        opts = plan(
            prefs.performance_mode,
            "off" if forced == "cpu" else prefs.gpu_mode,
            hw,
            weights_bytes=int(model.get("size_bytes") or 0),
            kv_bytes=kv,
            gpu_failed_before=bool(hw and hw.best_gpu and hw.best_gpu.backend in self.inference.gpu_failures),
        )
        if forced and forced != "cpu" and forced in self.runtime_dirs:
            opts.backend = forced
            opts.gpu_layers = opts.gpu_layers or 999
            opts.reason_de = f"Backend {forced} per Umgebungsvariable SPAIDER_FOLDABLE_BACKEND erzwungen."
        elif forced == "cpu":
            opts.reason_de = "CPU per Umgebungsvariable SPAIDER_FOLDABLE_BACKEND erzwungen."
        env_layers = os.environ.get("SPAIDER_FOLDABLE_GPU_LAYERS")
        if env_layers and opts.backend != "cpu":
            opts.gpu_layers = int(env_layers)
        return opts, n_layers

    # ---- agent ---------------------------------------------------------------------
    def _active_reasoning_toggle(self):
        mid = self.inference.chat.model_id
        caps = self.capabilities_for(mid) if mid else None
        return (caps or {}).get("reasoning_toggle")

    def _parse_document_file(self, path):
        from app.services.parser_service import parse_sync

        data = path.read_bytes()
        ct = "application/pdf" if path.suffix.lower() == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return parse_sync(data, content_type=ct, filename=path.name)

    async def _observe_for_agent(self, task, latest_refs):
        """Fresh, bounded observations for the planner: one UIA read per window source in scope
        (structure only; images are fetched explicitly by vision tools). Returns
        (text, {source_id: {element ids}}, {source_id: snapshot_id})."""
        texts, refs, snaps = [], {}, {}
        ws = task.scopes.windows
        if ws and ws.source_ids:
            names: dict[str, str] = {}
            for sid in ws.source_ids[:4]:
                try:
                    src = self.live.resolve_source(sid)
                except Exception as exc:  # noqa: BLE001
                    texts.append(f"Quelle {sid}: nicht verfügbar ({exc.__class__.__name__}).")
                    continue
                snap = await asyncio.to_thread(self.live.taker.take, task.id, src, want_image=False, max_elements=120)
                snaps[sid] = snap.snapshot_id
                refs[sid] = {e.id for e in snap.tree.elements}
                for e in snap.tree.elements:
                    names[e.id] = e.name
                self.agent_snapshots[(task.id, sid)] = snap
                texts.append(f"[source_id {sid}] " + snap.context_text(max_elements=60, max_chars=2500))
            self.agent.remember_element_names(task.id, names)
        if self.browser is not None and task.scopes.browser and task.scopes.browser.enabled:
            try:
                texts.append(await self.browser.observe_text(task.id))
            except Exception as exc:  # noqa: BLE001
                texts.append(f"Browser: {str(exc)[:160]}")
        return "\n\n".join(texts), refs, snaps

    def _agent_snapshot(self, task_id: str, src, want_image: bool = False):
        snap = self.live.taker.take(task_id, src, want_image=want_image, max_elements=150, max_side=self.settings.load().live_image_max_side)
        self.agent_snapshots[(task_id, src.id)] = snap
        return snap

    async def ask_vlm(self, prompt: str, image_data_url: str, max_tokens: int = 300) -> str:
        """One bounded vision call on the active model (scheduler: user-facing priority)."""
        client = self.inference.chat.client
        if client is None:
            raise RuntimeError("Kein Chatmodell bereit.")
        toggle = self._active_reasoning_toggle()
        text = ""
        async with self.scheduler.chat():
            async for chunk in client.stream_chat(
                [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image_data_url}}]}],
                temperature=0.0,
                max_tokens=max_tokens,
                enable_thinking=(False if toggle else None),
                reasoning_toggle=toggle,
                timeout_s=180.0,
            ):
                if chunk.text:
                    text += chunk.text
        return text

    def agent_capabilities(self) -> dict:
        mid = self.inference.chat.model_id
        caps = (self.capabilities_for(mid) if mid else None) or {}
        from .observe import windows as W
        from .observe.uia import available as uia_available

        return {
            "tools": self.broker.infos(None),
            "model": {
                "id": mid,
                "vision_input": bool(caps.get("vision_input")),
                "image_grounding": caps.get("image_grounding", "none"),
                "tool_call_format": caps.get("tool_call_format", "none"),
                "structured_output": bool(caps.get("structured_output", True)),
            },
            "browser": self.browser.status() if self.browser is not None else {"installed": False, "version": None, "path": None},
            "windows_automation": {"available": uia_available(), "reason_de": "" if uia_available() else "UI Automation (pywinauto) nicht verfügbar."},
            "desktop_interactive": W.desktop_interactive(),
        }

    def vision_available(self) -> bool:
        slot = self.inference.chat
        return slot.state == "ready" and bool((slot.options or {}).get("mmproj"))

    # ---- models -----------------------------------------------------------------
    def capabilities_for(self, model_id: str) -> Optional[dict]:
        model = self.registry.get(model_id)
        if not model:
            return None
        return capabilities_for_model(model, self.runtime_dirs)

    async def _autostart_models(self) -> None:
        prefs = self.settings.load()
        active = self.registry.active_ids()
        # make sure GPU detection has happened before the first start (bounded wait)
        await self.hardware_profile(wait=True)
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
                opts, n_layers = self.runtime_options_for(model)
                if role == "embedding":
                    opts.gpu_layers = None if opts.backend == "cpu" else opts.gpu_layers
                await self.inference.start(
                    role, mid, self.registry.abs_path(model), options=opts, n_layers=n_layers, mmproj_path=_mmproj(model)
                )
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
            try:
                n = await self.scheduler.run_background("reindex", self.ingestor.reindex_missing(model_id))
            except Exception:  # noqa: BLE001  (Preempted or embedding errors: retry later)
                await asyncio.sleep(1.0)
                continue
            if n == 0:
                break
            total += n
        if total:
            logger.info("re-embedded %d nodes for %s", total, model_id)

    def _on_worker_event(self, kind: str, payload: dict) -> None:
        ev = {
            "request_id": "",
            "seq": 0,
            "phase": kind,
            "state": "done" if kind == "postprocess_done" else "failed" if kind == "postprocess_failed" else "started",
            "t_ms": 0,
            "at": utcnow(),
            "message_de": {
                "postprocess_running": "Gedächtnis wird aktualisiert",
                "postprocess_done": "Gedächtnis aktualisiert",
                "postprocess_failed": "Gedächtnis-Nachverarbeitung fehlgeschlagen",
            }.get(kind, kind),
            "counters": {"pending": payload.get("pending", 0)},
            "source_ids": [],
            "conversation_id": payload.get("conversation_id"),
        }
        self.bus.publish("activity", ev)

    def apply_settings(self, prefs: UserSettings) -> None:
        self.inference.context_tokens = prefs.context_tokens
        self.inference.threads = prefs.threads
        self.ingestor.extraction_enabled = prefs.memory_auto_extract
        self.scheduler.set_mode(prefs.performance_mode)
        if not prefs.embedding_enabled and self.inference.embedding.state != "stopped":
            try:
                asyncio.get_event_loop().create_task(self.inference.stop("embedding"))
            except RuntimeError:
                pass
            self.store.set_index_model(None)

    def request_shutdown(self) -> None:
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self.shutdown(), name="shutdown")

    async def shutdown(self) -> None:
        try:
            self.agent.cancel_all()
            self.live.end_all()
            if self.browser is not None:
                await self.browser.close()
        except Exception:  # noqa: BLE001
            pass
        if self.shutting_down:
            return
        self.shutting_down = True
        logger.info("shutdown requested")
        try:
            self.scheduler.pause()
            await self.chat.stop_all()
            for t in (self._autostart_task, self._reindex_task, self._hardware_task):
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
            "chat": self._model_api(ids["chat"]),
            "embedding": self._model_api(ids["embedding"]),
            "runtime_state": self.inference.runtime_state(),
            "embedding_runtime_state": self.inference.embedding.state,
            "error": self.inference.chat.error,
            "runtime": self.inference.status(),
            "scheduler": self.scheduler.state(),
        }

    def _model_api(self, model_id: Optional[str]) -> Optional[dict]:
        if not model_id:
            return None
        model = self.registry.get(model_id)
        if not model:
            return None
        return self.decorate_model(model)

    def decorate_model(self, model: dict) -> dict:
        """Attach capabilities, verification and this machine's measurements."""
        out = dict(model)
        out["capabilities"] = capabilities_for_model(model, self.runtime_dirs)
        entry = self.catalog.get(model["catalog_id"]) if model.get("catalog_id") else None
        out["verification"] = (
            {
                "status": "executed" if entry and entry.support == "executed" else "metadata",
                "date": self.catalog.generated,
                "scope": "Katalogeintrag" if entry else "lokaler Import (nur Header geprüft)",
            }
            if entry
            else {
                "status": "unverified",
                "date": model.get("created_at"),
                "scope": "lokaler Import (nur Header geprüft)",
            }
        )
        out["measured"] = self.measured_profile(model)
        return out

    def measured_profile(self, model: dict) -> Optional[dict]:
        hw = self._hardware
        if hw is None:
            return None
        row = self.db.query_one(
            "SELECT * FROM model_profiles WHERE fingerprint = ? AND (model_sha256 = ? OR model_id = ?) "
            "ORDER BY measured_at DESC LIMIT 1",
            (hw.fingerprint, model.get("sha256"), model["id"]),
        )
        if not row:
            return None
        return {
            "backend": row["backend"],
            "tokens_per_second": row["tokens_per_second"],
            "time_to_first_token_ms": row["time_to_first_token_ms"],
            "prompt_tokens_per_second": row["prompt_tokens_per_second"],
            "load_seconds": row["load_seconds"],
            "threads": row["threads"],
            "gpu_layers": row["gpu_layers"],
            "measured_at": row["measured_at"],
            "fingerprint": row["fingerprint"],
            "kind": row["kind"],
        }

    def record_profile(
        self,
        model: dict,
        *,
        backend: str,
        threads: Optional[int],
        gpu_layers: Optional[int],
        tokens_per_second: Optional[float],
        ttft_ms: Optional[int],
        prompt_tps: Optional[float],
        load_seconds: Optional[float],
        kind: str = "calibration",
    ) -> None:
        hw = self._hardware
        if hw is None:
            return
        self.db.execute(
            "INSERT INTO model_profiles(id, fingerprint, model_sha256, model_id, runtime_build, backend, threads, gpu_layers, "  # noqa: E501
            "context_tokens, tokens_per_second, time_to_first_token_ms, prompt_tokens_per_second, load_seconds, kind, measured_at) "  # noqa: E501
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                __import__("uuid").uuid4().hex,
                hw.fingerprint,
                model.get("sha256"),
                model["id"],
                "b12032",
                backend,
                threads,
                gpu_layers,
                self.inference.context_tokens,
                tokens_per_second,
                ttft_ms,
                prompt_tps,
                load_seconds,
                kind,
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )

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
            "pending_postprocessing": self.ingest_queue.pending_count(),
            "message_de": msg,
        }

    def runtime_info(self) -> dict:
        if self._runtime_version is None:
            self._runtime_version = runtime_version(self.runtime_dir)
        return {
            "name": "llama.cpp",
            "build": "b12032",
            "variant": "cpu+vulkan" if "vulkan" in self.runtime_dirs else "cpu",
            "variants": sorted(self.runtime_dirs),
            "active_backend": self.inference.chat.backend if self.inference.chat.process else None,
            "version": self._runtime_version,
            "path": str(self.runtime_dir),
        }

    def read_log_tail(self, n: int) -> list[str]:
        p = self.paths.logs_dir / "core.log"
        try:
            return p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        except OSError:
            return []


def _mmproj(model: dict):
    p = model.get("mmproj_path")
    return Path(p) if p else None


def _meta_int(meta: dict, key: str) -> Optional[int]:
    v = meta.get(key)
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
