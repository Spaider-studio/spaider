"""Model jobs (download / import / activate / delete) with durable state in the
``jobs`` table, live snapshots for SSE subscribers, cancellation and the
loss-free activation procedure (see ``docs/portable/ARCHITECTURE.md``).

Activation procedure (spec §8), implemented in ``_run_activate``:
  1. check_space: the model is installed and verified; RAM estimate vs. host.
  2. (download + verify are their own job kinds; activation needs state=ready)
  3. load: stop the old runtime (frees RAM), start the new one.
  4. probe: /health + a tiny completion (or embedding) must succeed.
  5. commit: persist the new active id in one transaction.
  6. cleanup: remove temporary parts only; the previous model's files are
     deleted only when ``remove_previous`` was requested AND the commit succeeded.
  Rollback: on any failure before commit, restart the previous model and keep
  the previously persisted active id untouched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Awaitable, Callable, Optional

from ..db import Database
from ..paths import PortablePaths
from ..sysinfo import host_info
from .catalog import Catalog, CatalogEntry
from .downloader import DownloadCancelled, DownloadError, Progress, download_file, hf_resolve_url
from .registry import ModelRegistry, ModelValidationError

logger = logging.getLogger(__name__)

TERMINAL = {"done", "failed", "cancelled"}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class JobError(RuntimeError):
    def __init__(self, code: str, detail: str, **extra):
        self.code = code
        self.detail = detail
        self.extra = extra
        super().__init__(detail)


class JobManager:
    def __init__(
        self,
        db: Database,
        paths: PortablePaths,
        registry: ModelRegistry,
        catalog: Catalog,
        *,
        inference,
        on_active_changed: Optional[Callable[[str, Optional[str]], Awaitable[None]]] = None,
    ):
        self.db = db
        self.paths = paths
        self.registry = registry
        self.catalog = catalog
        self.inference = inference
        self.on_active_changed = on_active_changed
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel: dict[str, Event] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    # ---- persistence -------------------------------------------------------
    def _insert(self, kind: str, *, catalog_id=None, model_id=None, message_de="") -> dict:
        jid = str(uuid.uuid4())
        now = utcnow()
        self.db.execute(
            "INSERT INTO jobs(id, kind, state, catalog_id, model_id, phase, progress_json, message_de, created_at, updated_at) "  # noqa: E501
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                jid,
                kind,
                "queued",
                catalog_id,
                model_id,
                None,
                json.dumps({"done_bytes": 0, "total_bytes": 0, "speed_bps": 0, "eta_seconds": None}),
                message_de,
                now,
                now,
            ),
        )
        return self.get(jid)

    def get(self, jid: str) -> dict:
        row = self.db.query_one("SELECT * FROM jobs WHERE id = ?", (jid,))
        if row is None:
            raise KeyError(jid)
        return self._row(row)

    def list(self, limit: int = 50) -> list[dict]:
        return [self._row(r) for r in self.db.query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))]

    @staticmethod
    def _row(row) -> dict:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "state": row["state"],
            "catalog_id": row["catalog_id"],
            "model_id": row["model_id"],
            "phase": row["phase"],
            "progress": json.loads(row["progress_json"] or "{}"),
            "message_de": row["message_de"],
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "result_model_id": row["result_model_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "finished_at": row["finished_at"],
            "journal": json.loads(row["journal_json"] or "[]"),
        }

    def _update(self, jid: str, **fields) -> dict:
        sets, params = ["updated_at = ?"], [utcnow()]
        for k, v in fields.items():
            if k == "progress":
                sets.append("progress_json = ?")
                params.append(json.dumps(v))
            elif k == "error":
                sets.append("error_json = ?")
                params.append(json.dumps(v) if v is not None else None)
            elif k == "journal_append":
                job = self.get(jid)
                journal = job["journal"] + [{"at": utcnow(), **v}]
                sets.append("journal_json = ?")
                params.append(json.dumps(journal))
            else:
                sets.append(f"{k} = ?")
                params.append(v)
        params.append(jid)
        self.db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)
        job = self.get(jid)
        self._publish(job)
        return job

    def _finish(
        self,
        jid: str,
        state: str,
        *,
        error: Optional[dict] = None,
        result_model_id: Optional[str] = None,
        message_de: Optional[str] = None,
    ) -> dict:
        fields = {"state": state, "finished_at": utcnow(), "error": error}
        if result_model_id:
            fields["result_model_id"] = result_model_id
        if message_de is not None:
            fields["message_de"] = message_de
        job = self._update(jid, **fields)
        self._cancel.pop(jid, None)
        return job

    # ---- SSE ----------------------------------------------------------------
    def subscribe(self, jid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(jid, []).append(q)
        return q

    def unsubscribe(self, jid: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(jid, [])
        if q in subs:
            subs.remove(q)

    def _publish(self, job: dict) -> None:
        for q in list(self._subscribers.get(job["id"], [])):
            try:
                q.put_nowait(job)
            except asyncio.QueueFull:
                pass

    # ---- queries ------------------------------------------------------------
    def running(self) -> list[dict]:
        return [j for j in self.list() if j["state"] in ("queued", "running")]

    def running_count(self) -> int:
        return len(self.running())

    def _job_for_model(self, model_id: str) -> Optional[dict]:
        for j in self.running():
            if j["model_id"] == model_id or j["result_model_id"] == model_id:
                return j
        return None

    def recover_after_crash(self) -> None:
        """Jobs left running by a crash: downloads become resumable (paused),
        activations are marked failed (the committed active id is untouched)."""
        for j in self.list(200):
            if j["state"] in ("queued", "running"):
                if j["kind"] == "download":
                    self._update(j["id"], state="paused", message_de="Unterbrochen; kann fortgesetzt werden.")
                else:
                    self._finish(
                        j["id"],
                        "failed",
                        error={"detail": "Vorgang wurde durch einen Neustart unterbrochen.", "code": "interrupted"},
                    )

    # ---- public operations ---------------------------------------------------
    async def start_download(self, catalog_id: str) -> dict:
        entry = self.catalog.get(catalog_id)
        if entry is None:
            raise JobError("not_found", "Unbekannter Katalogeintrag.")
        if self.registry.find_by_catalog(catalog_id):
            raise JobError("already_installed", "Dieses Modell ist bereits installiert.")
        for j in self.running():
            if j["kind"] == "download" and j["catalog_id"] == catalog_id:
                raise JobError("job_running", "Für dieses Modell läuft bereits ein Download.")
        host = host_info(self.paths.data_dir)
        if host.max_file_bytes is not None and any(f.size > host.max_file_bytes for f in entry.files):
            raise JobError(
                "file_too_large_for_filesystem",
                "Das Dateisystem des Datenträgers (FAT32) kann Dateien über 4 GB nicht speichern.",
            )
        # Resume an existing paused job for the same entry instead of starting a new one.
        for j in self.list(200):
            if j["kind"] == "download" and j["catalog_id"] == catalog_id and j["state"] in ("paused", "failed"):
                return await self.resume(j["id"])
        required = entry.total_bytes + 256 * (1 << 20)
        if host.disk_free_bytes < required:
            raise JobError(
                "insufficient_space",
                f"Nicht genug freier Speicher: benötigt {required / 2**30:.1f} GB, frei {host.disk_free_bytes / 2**30:.1f} GB.",  # noqa: E501
                required_bytes=required,
                free_bytes=host.disk_free_bytes,
            )
        job = self._insert("download", catalog_id=catalog_id, message_de="Download wird vorbereitet …")
        self._spawn(job["id"], self._run_download(job["id"], entry))
        return self.get(job["id"])

    async def resume(self, jid: str) -> dict:
        job = self.get(jid)
        if job["kind"] != "download" or job["state"] not in ("paused", "failed", "cancelled"):
            raise JobError("invalid_state", "Dieser Vorgang kann nicht fortgesetzt werden.")
        entry = self.catalog.get(job["catalog_id"])
        if entry is None:
            raise JobError("not_found", "Unbekannter Katalogeintrag.")
        self._update(jid, state="queued", error=None, finished_at=None, message_de="Download wird fortgesetzt …")
        self._spawn(jid, self._run_download(jid, entry))
        return self.get(jid)

    async def cancel(self, jid: str) -> dict:
        job = self.get(jid)
        if job["state"] in TERMINAL or job["state"] == "paused":
            return job
        ev = self._cancel.get(jid)
        if ev:
            ev.set()
        task = self._tasks.get(jid)
        if task and job["kind"] != "download":
            task.cancel()
        if job["kind"] == "download":
            # the worker converts the cancel into state=paused (resumable)
            for _ in range(100):
                await asyncio.sleep(0.1)
                if self.get(jid)["state"] in TERMINAL | {"paused"}:
                    break
        return self.get(jid)

    async def start_import(self, path: str) -> dict:
        src = Path(path)
        if not src.is_absolute() or not src.is_file():
            raise JobError("gguf_invalid", "Bitte eine vorhandene GGUF-Datei mit absolutem Pfad angeben.")
        job = self._insert("import", message_de="Modell wird geprüft und kopiert …")
        self._spawn(job["id"], self._run_import(job["id"], src))
        return self.get(job["id"])

    async def start_activate(self, model_id: str, *, remove_previous: bool = False) -> dict:
        model = self.registry.get(model_id)
        if model is None:
            raise JobError("not_found", "Unbekanntes Modell.")
        if model["state"] != "ready":
            raise JobError("model_broken", "Die Modelldatei fehlt oder ist unvollständig.")
        if self._job_for_model(model_id):
            raise JobError("job_running", "Für dieses Modell läuft bereits ein Vorgang.")
        for j in self.running():
            if j["kind"] == "activate":
                raise JobError("job_running", "Es läuft bereits ein Modellwechsel.")
        job = self._insert("activate", model_id=model_id, message_de="Modellwechsel wird vorbereitet …")
        self._spawn(job["id"], self._run_activate(job["id"], model, remove_previous))
        return self.get(job["id"])

    async def delete_model(self, model_id: str) -> None:
        model = self.registry.get(model_id)
        if model is None:
            raise JobError("not_found", "Unbekanntes Modell.")
        if model["active"]:
            raise JobError(
                "model_active",
                "Das aktive Modell kann nicht gelöscht werden. Bitte zuerst ein anderes Modell aktivieren.",
            )
        if self._job_for_model(model_id):
            raise JobError("job_running", "Für dieses Modell läuft noch ein Vorgang.")
        await asyncio.to_thread(self.registry.remove, model_id)

    async def shutdown(self) -> None:
        for jid, ev in list(self._cancel.items()):
            ev.set()
        for jid, task in list(self._tasks.items()):
            job = self.get(jid)
            if job["kind"] == "download":
                try:
                    await asyncio.wait_for(task, timeout=10)
                except Exception:  # noqa: BLE001
                    task.cancel()
            else:
                task.cancel()
                try:
                    await task
                except Exception:  # noqa: BLE001
                    pass

    # ---- workers ---------------------------------------------------------------
    def _spawn(self, jid: str, coro) -> None:
        self._cancel[jid] = Event()
        task = asyncio.create_task(coro, name=f"job-{jid}")
        self._tasks[jid] = task
        task.add_done_callback(lambda t: self._tasks.pop(jid, None))

    async def _run_download(self, jid: str, entry: CatalogEntry) -> None:
        cancel = self._cancel[jid]
        part_dir = self.paths.downloads_dir / jid
        file = entry.files[0]
        part = part_dir / (file.path + ".part")
        loop = asyncio.get_running_loop()
        self._update(jid, state="running", phase="download", message_de=f"{entry.name} wird heruntergeladen …")
        last = {"t": 0.0}

        def on_progress(p: Progress) -> None:
            import time as _t

            now = _t.monotonic()
            if now - last["t"] < 0.5 and p.done_bytes < p.total_bytes:
                return
            last["t"] = now
            loop.call_soon_threadsafe(
                self._update,
                jid,
                progress={
                    "done_bytes": p.done_bytes,
                    "total_bytes": p.total_bytes,
                    "speed_bps": int(p.speed_bps),
                    "eta_seconds": int(p.eta_seconds) if p.eta_seconds is not None else None,
                },
            )

        try:
            url = hf_resolve_url(entry.repo, entry.revision, file.path)
            self._update(jid, journal_append={"step": "download", "url": url})
            await asyncio.to_thread(
                download_file,
                url,
                part,
                expected_size=file.size,
                expected_sha256=file.sha256,
                progress=on_progress,
                cancel=cancel,
            )
            self._update(jid, phase="verify", message_de="Prüfsumme wurde bestätigt, Modell wird installiert …")
            model = await asyncio.to_thread(self.registry.install_from_download, entry, part, sha_verified=True)
            shutil.rmtree(part_dir, ignore_errors=True)
            self._finish(jid, "done", result_model_id=model["id"], message_de="Download abgeschlossen.")
        except DownloadCancelled:
            self._update(jid, state="paused", message_de="Download angehalten; kann fortgesetzt werden.")
        except DownloadError as exc:
            if exc.code == "network":
                self._update(
                    jid,
                    state="paused",
                    message_de="Netzwerkfehler; der Download kann fortgesetzt werden.",
                    error={"detail": exc.detail, "code": exc.code},
                )
            else:
                self._finish(jid, "failed", error={"detail": exc.detail, "code": exc.code})
        except ModelValidationError as exc:
            shutil.rmtree(part_dir, ignore_errors=True)
            self._finish(jid, "failed", error={"detail": exc.detail, "code": exc.code})
        except asyncio.CancelledError:
            self._update(jid, state="paused", message_de="Download unterbrochen; kann fortgesetzt werden.")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("download job %s failed", jid)
            self._finish(jid, "failed", error={"detail": f"Unerwarteter Fehler: {exc}", "code": "internal"})

    async def _run_import(self, jid: str, src: Path) -> None:
        cancel = self._cancel[jid]
        loop = asyncio.get_running_loop()
        self._update(jid, state="running", phase="copy", message_de="Modelldatei wird geprüft und kopiert …")

        def on_progress(done: int, total: int) -> None:
            loop.call_soon_threadsafe(
                self._update,
                jid,
                progress={"done_bytes": done, "total_bytes": total, "speed_bps": 0, "eta_seconds": None},
            )

        try:
            model = await asyncio.to_thread(self.registry.import_local, src, progress=on_progress, cancel=cancel)
            self._finish(jid, "done", result_model_id=model["id"], message_de="Modell importiert.")
        except ModelValidationError as exc:
            state = "cancelled" if exc.code == "cancelled" else "failed"
            self._finish(jid, state, error=None if state == "cancelled" else {"detail": exc.detail, "code": exc.code})
        except asyncio.CancelledError:
            self._finish(jid, "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("import job %s failed", jid)
            self._finish(jid, "failed", error={"detail": f"Unerwarteter Fehler: {exc}", "code": "internal"})

    async def _run_activate(self, jid: str, model: dict, remove_previous: bool) -> None:
        role = model["role"]
        key = "active_chat_model_id" if role == "chat" else "active_embedding_model_id"
        previous_id = self.registry.get_state(key)
        previous = self.registry.get(previous_id) if previous_id else None
        self._update(
            jid,
            state="running",
            phase="check_space",
            message_de="Speicher und Arbeitsspeicher werden geprüft …",
            journal_append={"step": "begin", "previous": previous_id, "next": model["id"]},
        )
        try:
            host = host_info(self.paths.data_dir)
            need = int(model["size_bytes"] * 1.1) + (1 << 30)
            if host.ram_total_bytes and need > host.ram_total_bytes:
                raise JobError(
                    "insufficient_ram",
                    f"Das Modell benötigt etwa {need / 2**30:.1f} GB RAM, verfügbar sind {host.ram_total_bytes / 2**30:.0f} GB.",  # noqa: E501
                )
            path = self.registry.abs_path(model)
            if not path.exists() or path.stat().st_size != model["size_bytes"]:
                raise JobError("model_broken", "Die Modelldatei fehlt oder ist unvollständig.")
            # 3. load (old runtime is stopped inside InferenceManager.start so both never share RAM)
            self._update(jid, phase="load", message_de="Vorheriges Modell wird entladen, neues Modell wird geladen …")
            if previous_id == model["id"] and self.inference.slot(role).state == "ready":
                self._finish(jid, "done", result_model_id=model["id"], message_de="Modell ist bereits aktiv.")
                return
            await self.inference.start(role, model["id"], path, context_tokens=None)
            # 4. probe
            self._update(jid, phase="probe", message_de="Modell wird getestet …")
            await self._probe(role)
            # 5. commit
            self._update(jid, phase="commit", message_de="Auswahl wird gespeichert …")
            with self.db.transaction():
                self.registry.set_state(key, model["id"])
            self._update(jid, journal_append={"step": "commit", "active": model["id"]})
            if self.on_active_changed:
                await self.on_active_changed(role, model["id"])
            # 6. cleanup
            self._update(jid, phase="cleanup", message_de="Aufräumen …")
            for d in self.paths.downloads_dir.iterdir() if self.paths.downloads_dir.exists() else []:
                try:
                    job = self.get(d.name)
                    if job["state"] == "done":
                        shutil.rmtree(d, ignore_errors=True)
                except KeyError:
                    pass
            if remove_previous and previous and previous["id"] != model["id"]:
                await asyncio.to_thread(self.registry.remove, previous["id"])
                self._update(jid, journal_append={"step": "removed_previous", "model": previous["id"]})
            self._finish(jid, "done", result_model_id=model["id"], message_de="Modell ist aktiv.")
        except asyncio.CancelledError:
            await self._rollback(jid, role, previous, "Abgebrochen; vorheriges Modell bleibt aktiv.")
            raise
        except JobError as exc:
            await self._rollback(jid, role, previous, exc.detail, code=exc.code)
        except Exception as exc:  # noqa: BLE001
            logger.exception("activate job %s failed", jid)
            await self._rollback(
                jid, role, previous, f"Das Modell konnte nicht gestartet werden: {exc}", code="runtime_start_failed"
            )

    async def _probe(self, role: str) -> None:
        slot = self.inference.slot(role)
        client = slot.client
        if client is None:
            raise JobError("runtime_start_failed", "Die Laufzeit meldet sich nicht.")
        if role == "embedding":
            vecs = await client.embeddings(["Probe"])
            if not vecs or not vecs[0]:
                raise JobError("probe_failed", "Das Embedding-Modell liefert keine Vektoren.")
            return
        text = ""
        async for chunk in client.stream_chat(
            [{"role": "user", "content": "Antworte mit einem Wort: Hallo"}], max_tokens=8, temperature=0.0
        ):
            text += chunk.text
        if not text.strip():
            raise JobError("probe_failed", "Das Modell liefert keine Antwort.")

    async def _rollback(
        self, jid: str, role: str, previous: Optional[dict], detail: str, *, code: str = "activation_failed"
    ) -> None:
        self._update(jid, phase="rollback", message_de=f"{detail} Vorheriges Modell wird wiederhergestellt …")
        try:
            await self.inference.stop(role)
            if previous and previous["state"] == "ready":
                await self.inference.start(role, previous["id"], self.registry.abs_path(previous))
                if self.on_active_changed:
                    await self.on_active_changed(role, previous["id"])
                msg = f"{detail} Das vorherige Modell „{previous['name']}“ ist wieder aktiv."
            else:
                msg = f"{detail} Es ist kein Modell aktiv."
        except Exception as exc:  # noqa: BLE001
            msg = f"{detail} Wiederherstellung des vorherigen Modells fehlgeschlagen: {exc}"
        self._finish(jid, "failed", error={"detail": msg, "code": code}, message_de=msg)
