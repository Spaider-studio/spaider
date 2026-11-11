"""Portable profiles (ADR-5).

Layout::

    data/
      profiles.json                 registry: {"version": 1, "profiles": [{"id","name","created_at"}], "active_id": …}
      profiles/<uuid>/              one profile: spaider.db, models/, downloads/, imports/, cache/, tmp/,
                                    vault.json, catalog.local.json, providers.local.json
      host/                         machine-bound state (DPAPI credentials, connector setup)   (shared)
      logs/, electron/, connectors/, instance.lock                                               (shared)

A legacy data folder (0.1–0.3: everything directly under ``data/``) is moved
into the first profile on the first start. Moves are renames inside the same
volume; a journal file records the plan so a crash in the middle resumes
instead of showing an empty database.

Switching profiles tears the current ``AppContext`` down (streams, tasks,
worker, jobs, model processes, DB) and builds a new one for the other profile;
the generation counter increases so late events of the old context are
discarded by clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from .lock import InstanceLock
from .paths import PortablePaths

logger = logging.getLogger(__name__)

REGISTRY_VERSION = 1
LEGACY_ITEMS = ("spaider.db", "spaider.db-wal", "spaider.db-shm", "models", "downloads", "imports", "cache", "catalog.local.json", "providers.local.json", "vault.json", "browser")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ProfileError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ProfileRegistry:
    def __init__(self, root_data_dir: Path):
        self.root = Path(root_data_dir)
        self.path = self.root / "profiles.json"
        self.profiles_dir = self.root / "profiles"

    # ---- registry file ----------------------------------------------------------------
    def _read(self) -> Optional[dict]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("profiles"), list):
                return d
        except (OSError, ValueError):
            pass
        return None

    def _write(self, d: dict) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def exists(self) -> bool:
        return self.path.exists()

    def list(self) -> list[dict]:
        d = self._read() or {"profiles": []}
        out = []
        for p in d["profiles"]:
            pdir = self.profiles_dir / p["id"]
            out.append({**p, "exists": pdir.exists(), "db_bytes": (pdir / "spaider.db").stat().st_size if (pdir / "spaider.db").exists() else 0})
        return out

    def active_id(self) -> Optional[str]:
        d = self._read()
        return d.get("active_id") if d else None

    def get(self, pid: str) -> Optional[dict]:
        return next((p for p in self.list() if p["id"] == pid), None)

    def set_active(self, pid: str) -> None:
        d = self._read()
        if d is None or not any(p["id"] == pid for p in d["profiles"]):
            raise ProfileError("not_found", "Profil nicht gefunden.")
        d["active_id"] = pid
        self._write(d)

    def create(self, name: str) -> dict:
        d = self._read() or {"version": REGISTRY_VERSION, "profiles": [], "active_id": None}
        pid = str(uuid.uuid4())
        entry = {"id": pid, "name": (name or "Profil").strip()[:80], "created_at": _now()}
        (self.profiles_dir / pid).mkdir(parents=True, exist_ok=True)
        d["profiles"].append(entry)
        if d.get("active_id") is None:
            d["active_id"] = pid
        self._write(d)
        return entry

    def rename(self, pid: str, name: str) -> dict:
        d = self._read()
        if d is None:
            raise ProfileError("not_found", "Profil nicht gefunden.")
        for p in d["profiles"]:
            if p["id"] == pid:
                p["name"] = name.strip()[:80] or p["name"]
                self._write(d)
                return p
        raise ProfileError("not_found", "Profil nicht gefunden.")

    def delete(self, pid: str, *, active: Optional[str]) -> None:
        d = self._read()
        if d is None or not any(p["id"] == pid for p in d["profiles"]):
            raise ProfileError("not_found", "Profil nicht gefunden.")
        if pid == active:
            raise ProfileError("active", "Das aktive Profil kann nicht gelöscht werden; zuerst wechseln.")
        d["profiles"] = [p for p in d["profiles"] if p["id"] != pid]
        self._write(d)
        shutil.rmtree(self.profiles_dir / pid, ignore_errors=True)

    # ---- first start: legacy migration ----------------------------------------------------
    def ensure_initialized(self) -> dict:
        """Create the registry; move a legacy ``data/`` layout into the first profile (resumable)."""
        journal = self.root / "profiles.migrating.json"
        if journal.exists():
            plan = json.loads(journal.read_text(encoding="utf-8"))
            self._apply_moves(plan)
            journal.unlink(missing_ok=True)
        d = self._read()
        if d is not None and d.get("active_id"):
            return d
        legacy = [n for n in LEGACY_ITEMS if (self.root / n).exists()]
        pid = str(uuid.uuid4())
        entry = {"id": pid, "name": "Standard", "created_at": _now(), "migrated_from_legacy": bool(legacy)}
        (self.profiles_dir / pid).mkdir(parents=True, exist_ok=True)
        if legacy:
            plan = {"profile_id": pid, "items": legacy, "started_at": _now()}
            journal.write_text(json.dumps(plan), encoding="utf-8")
            self._apply_moves(plan)
            journal.unlink(missing_ok=True)
            logger.info("profiles: moved legacy data (%s) into profile %s", ", ".join(legacy), pid)
        self._write({"version": REGISTRY_VERSION, "profiles": [entry], "active_id": pid})
        return self._read() or {}

    def _apply_moves(self, plan: dict) -> None:
        target = self.profiles_dir / plan["profile_id"]
        target.mkdir(parents=True, exist_ok=True)
        for name in plan.get("items", []):
            src = self.root / name
            dst = target / name
            if not src.exists():
                continue  # already moved (resume after crash)
            if dst.exists():
                # both present: keep the profile copy, park the legacy one instead of merging blindly
                parked = self.root / f"{name}.legacy-{int(time.time())}"
                os.replace(src, parked)
                logger.warning("profiles: %s existed in both places; legacy copy parked as %s", name, parked.name)
                continue
            os.replace(src, dst)


class ProfileManager:
    """Owns the active AppContext and performs profile switches."""

    def __init__(self, root_paths: PortablePaths, runtime_dirs: dict, make_context: Callable[[PortablePaths, InstanceLock], object], *, on_exit: Optional[Callable[[], None]] = None):
        self.root_paths = root_paths
        self.runtime_dirs = runtime_dirs
        self.make_context = make_context
        self.on_exit = on_exit
        self.registry = ProfileRegistry(root_paths.data_dir)
        self.lock = InstanceLock(root_paths.lock_path)  # one lock for the process, shared by all contexts
        self.ctx = None
        self.generation = 0
        self.active_id: Optional[str] = None
        self._switching = asyncio.Lock() if False else None  # created lazily on the loop
        self.app = None  # FastAPI app; its state.ctx is replaced on switch

    def profile_paths(self, pid: str) -> PortablePaths:
        return self.root_paths.for_profile(pid)

    def start_sync(self) -> None:
        """Blocking: registry/migration, build the first context, startup_sync."""
        reg = self.registry.ensure_initialized()
        self.active_id = reg["active_id"]
        self.ctx = self.make_context(self.profile_paths(self.active_id), self.lock)
        self.ctx.startup_sync()
        self.generation = 1

    async def start_async(self) -> None:
        await self.ctx.startup_async()

    def status(self) -> dict:
        return {"active_id": self.active_id, "generation": self.generation, "profiles": self.registry.list()}

    async def switch(self, pid: str) -> dict:
        if self._switching is None:
            self._switching = asyncio.Lock()
        async with self._switching:
            if pid == self.active_id:
                return self.status()
            if self.registry.get(pid) is None:
                raise ProfileError("not_found", "Profil nicht gefunden.")
            old = self.ctx
            t0 = time.monotonic()
            logger.info("profiles: switching %s -> %s", self.active_id, pid)
            # 1) tear down (keeps the process and the instance lock)
            await old.shutdown(keep_process=True)
            # 2) build the new context
            new_ctx = self.make_context(self.profile_paths(pid), self.lock)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, new_ctx.startup_sync)
            self.generation += 1
            self.active_id = pid
            self.registry.set_active(pid)
            self.ctx = new_ctx
            if self.app is not None:
                self.app.state.ctx = new_ctx
            await new_ctx.startup_async()
            new_ctx.bus.publish("profile", {"active_id": pid, "generation": self.generation, "switched_in_ms": int((time.monotonic() - t0) * 1000)})
            logger.info("profiles: switched to %s (generation %d) in %d ms", pid, self.generation, int((time.monotonic() - t0) * 1000))
            return self.status()

    async def shutdown(self) -> None:
        if self.ctx is not None:
            await self.ctx.shutdown()
