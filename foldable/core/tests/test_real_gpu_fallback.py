"""Real-runtime check of the GPU -> CPU fallback (opt-in like test_real_e2e).

The Vulkan runtime directory is replaced by a folder whose ``llama-server.exe``
is a copy of ``where.exe`` (exits immediately with a non-zero code), and the
backend is forced to ``vulkan`` through the environment. The core must

* fail the GPU start, retry on the bundled CPU runtime in the same session,
* record the failed backend (``gpu_failures``) and report it in
  ``GET /api/system/hardware`` and in the runtime options,
* answer a chat on the CPU runtime.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from spaider_foldable.db import Database
from spaider_foldable.models.catalog import load_catalog
from spaider_foldable.models.registry import ModelRegistry
from spaider_foldable.paths import PortablePaths

from .conftest import CHAT_MODEL, requires_real
from .test_real_activity import _send
from .test_real_e2e import Core, _wait_runtime_ready

WHERE_EXE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "where.exe"


@requires_real()
@pytest.mark.skipif(not WHERE_EXE.exists(), reason="needs where.exe as a failing stand-in binary")
def test_gpu_start_failure_falls_back_to_cpu(tmp_path, monkeypatch):
    fake_vulkan = tmp_path / "llama-vulkan-broken"
    fake_vulkan.mkdir()
    shutil.copy2(WHERE_EXE, fake_vulkan / "llama-server.exe")
    monkeypatch.setenv("SPAIDER_FOLDABLE_RUNTIME_DIR_VULKAN", str(fake_vulkan))
    monkeypatch.setenv("SPAIDER_FOLDABLE_BACKEND", "vulkan")

    app_root = tmp_path / "Rückfall" / "SpAIder"
    app_root.mkdir(parents=True)
    paths = PortablePaths.from_app_root(app_root)
    paths.ensure()
    db = Database(paths.db_path)
    db.open()
    reg = ModelRegistry(db, paths)
    entry = load_catalog().get("qwen3-0.6b-q8_0")
    part = paths.downloads_dir / "prep" / entry.files[0].path
    part.parent.mkdir(parents=True)
    shutil.copy2(Path(CHAT_MODEL), part)
    model = reg.install_from_download(entry, part, sha_verified=True)
    reg.set_state("active_chat_model_id", model["id"])
    db.close()

    core = Core(app_root, label="gpu-fallback").start()
    try:
        with core.client() as c:
            active = _wait_runtime_ready(c, timeout=300)
            rt = active["runtime"]["chat"]
            assert rt["backend"] == "cpu", rt
            assert "Rückfall" in (rt["options"] or {}).get("reason_de", ""), rt
            hw = c.get("/system/hardware").json()
            assert hw["gpu_failures"] == ["vulkan"], hw["gpu_failures"]
            assert hw["active_backend"] == "cpu"
            conv = c.post("/conversations", json={"title": "CPU-Rückfall"}).json()
            r = _send(c, conv["id"], "Antworte mit einem Wort: Welche Farbe hat der Himmel bei klarem Wetter?")
            assert r["done"]["status"] == "complete" and r["text"].strip(), r["done"]
            log = (paths.logs_dir / "core.log").read_text(encoding="utf-8", errors="replace")
            assert "GPU backend vulkan failed" in log
        core.shutdown()
    except BaseException:
        core.kill()
        raise
