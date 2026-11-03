"""JobManager tests with a local HTTP server standing in for Hugging Face:
download → verify → install, cancel → paused → resume, insufficient space,
and the activation rollback path with a fake runtime."""

from __future__ import annotations

import asyncio
import hashlib
import http.server
import threading
from pathlib import Path

import pytest

from spaider_foldable.models import downloader, jobs
from spaider_foldable.models.catalog import Catalog, CatalogEntry, CatalogFile, License
from spaider_foldable.models.jobs import JobError, JobManager
from spaider_foldable.models.registry import ModelRegistry
from spaider_foldable.sysinfo import HostInfo

from .gguf_fixture import write_fake_gguf


class _Handler(http.server.SimpleHTTPRequestHandler):
    payload = b""
    slow = False

    def log_message(self, *a):
        pass

    def do_GET(self):
        data = self.payload
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        step = 64 * 1024
        import time

        for i in range(0, len(body), step):
            try:
                self.wfile.write(body[i : i + step])
                self.wfile.flush()
            except (ConnectionAbortedError, BrokenPipeError):
                return
            if self.slow:
                time.sleep(0.05)


@pytest.fixture
def fake_hub(tmp_path, monkeypatch):
    gguf = write_fake_gguf(tmp_path / "hub.gguf", name="Hub Model", extra_bytes=3 * 1024 * 1024)
    data = gguf.read_bytes()
    _Handler.payload = data
    _Handler.slow = False
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    monkeypatch.setattr(downloader, "_host_allowed", lambda url: True)
    monkeypatch.setattr(jobs, "hf_resolve_url", lambda repo, rev, path: f"http://127.0.0.1:{port}/{path}")
    entry = CatalogEntry(
        id="fake-chat", name="Fake Chat", role="chat", family="Fake", publisher="test", source="official",
        repo="test/fake", revision="0" * 40, files=[CatalogFile(path="fake.gguf", size=len(data), sha256=hashlib.sha256(data).hexdigest())],  # noqa: E501
        license=License(id="mit", name="MIT", url="https://example.invalid"), architecture="qwen3", parameters="0",
        quantization="Q8_0", context_length=4096,
    )  # fmt: skip
    catalog = Catalog(version=1, generated="x", entries=[entry])
    yield catalog, srv
    srv.shutdown()


class _FakeInference:
    """Minimal InferenceManager stand-in: activation succeeds unless the model
    name contains 'kaputt'."""

    def __init__(self):
        from spaider_foldable.inference.manager import RuntimeSlot

        self.chat = RuntimeSlot("chat")
        self.embedding = RuntimeSlot("embedding")
        self.started: list[str] = []

    def slot(self, role):
        return self.chat if role == "chat" else self.embedding

    async def start(self, role, model_id, path, context_tokens=None, options=None, n_layers=None, mmproj_path=None):
        slot = self.slot(role)
        if "kaputt" in model_id:
            slot.state = "error"
            slot.error = "load failed"
            raise RuntimeError("load failed")
        slot.state = "ready"
        slot.model_id = model_id
        slot.process = None
        self.started.append(model_id)
        return slot

    async def stop(self, role):
        self.slot(role).state = "stopped"
        self.slot(role).model_id = None


def _manager(db, paths, catalog, inference=None):
    reg = ModelRegistry(db, paths)
    return JobManager(db, paths, reg, catalog, inference=inference or _FakeInference()), reg


@pytest.mark.asyncio
async def test_download_job_installs_verified_model(db, paths, fake_hub):
    catalog, _ = fake_hub
    jm, reg = _manager(db, paths, catalog)
    job = await jm.start_download("fake-chat")
    for _ in range(600):
        job = jm.get(job["id"])
        if job["state"] in ("done", "failed"):
            break
        await asyncio.sleep(0.05)
    assert job["state"] == "done", job
    model = reg.get(job["result_model_id"])
    assert model and model["sha256_verified"] and model["catalog_id"] == "fake-chat" and model["state"] == "ready"
    assert not (paths.downloads_dir / job["id"]).exists()  # partial dir cleaned up
    with pytest.raises(JobError) as exc:
        await jm.start_download("fake-chat")
    assert exc.value.code == "already_installed"


@pytest.mark.asyncio
async def test_download_cancel_pauses_and_resume_finishes(db, paths, fake_hub):
    catalog, _ = fake_hub
    _Handler.slow = True
    jm, reg = _manager(db, paths, catalog)
    job = await jm.start_download("fake-chat")
    for _ in range(200):
        await asyncio.sleep(0.05)
        if jm.get(job["id"])["progress"]["done_bytes"] > 0:
            break
    job = await jm.cancel(job["id"])
    assert job["state"] == "paused", job
    part = next((paths.downloads_dir / job["id"]).glob("*.part"))
    assert 0 < part.stat().st_size < catalog.entries[0].total_bytes
    _Handler.slow = False
    job = await jm.resume(job["id"])
    for _ in range(600):
        job = jm.get(job["id"])
        if job["state"] in ("done", "failed"):
            break
        await asyncio.sleep(0.05)
    assert job["state"] == "done", job
    assert reg.get(job["result_model_id"])["sha256_verified"]


@pytest.mark.asyncio
async def test_download_refuses_without_space(db, paths, fake_hub, monkeypatch):
    catalog, _ = fake_hub
    jm, _ = _manager(db, paths, catalog)
    tiny = HostInfo(os="windows", arch="x64", hostname="h", cpu="c", threads=4, ram_total_bytes=8 << 30, ram_available_bytes=4 << 30,  # noqa: E501
                    disk_total_bytes=1 << 30, disk_free_bytes=100 << 20, filesystem="NTFS", max_file_bytes=None)  # fmt: skip  # noqa: E501
    monkeypatch.setattr(jobs, "host_info", lambda p: tiny)
    with pytest.raises(JobError) as exc:
        await jm.start_download("fake-chat")
    assert exc.value.code == "insufficient_space" and exc.value.extra["free_bytes"] == 100 << 20
    fat = HostInfo(
        **{**tiny.__dict__, "disk_free_bytes": 50 << 30, "filesystem": "FAT32", "max_file_bytes": 4_294_967_295}
    )  # noqa: E501
    monkeypatch.setattr(jobs, "host_info", lambda p: fat)
    big = catalog.entries[0].model_copy(update={"files": [CatalogFile(path="big.gguf", size=5 << 30, sha256="0" * 64)]})
    jm.catalog = Catalog(version=1, generated="x", entries=[big])
    with pytest.raises(JobError) as exc2:
        await jm.start_download("fake-chat")
    assert exc2.value.code == "file_too_large_for_filesystem"


@pytest.mark.asyncio
async def test_activate_rollback_keeps_previous_model(db, paths, tmp_path, monkeypatch):
    inference = _FakeInference()
    jm, reg = _manager(db, paths, Catalog(version=1, generated="x", entries=[]), inference)
    good = reg.import_local(write_fake_gguf(tmp_path / "gut.gguf", name="Gut", extra_bytes=4096))
    bad = reg.import_local(write_fake_gguf(tmp_path / "kaputt.gguf", name="kaputt", extra_bytes=4096))
    monkeypatch.setattr(jobs, "host_info", lambda p: HostInfo(os="windows", arch="x64", hostname="h", cpu="c", threads=4,  # noqa: E501
                        ram_total_bytes=16 << 30, ram_available_bytes=8 << 30, disk_total_bytes=1 << 40, disk_free_bytes=1 << 40,  # noqa: E501
                        filesystem="NTFS", max_file_bytes=None))  # fmt: skip

    async def ok_probe(role):
        return None

    monkeypatch.setattr(jm, "_probe", ok_probe)
    job = await jm.start_activate(good["id"])
    for _ in range(200):
        job = jm.get(job["id"])
        if job["state"] in ("done", "failed"):
            break
        await asyncio.sleep(0.02)
    assert job["state"] == "done" and reg.get_state("active_chat_model_id") == good["id"]
    assert [j["step"] for j in job["journal"]] == ["begin", "plan", "commit"]

    job = await jm.start_activate(bad["id"])
    for _ in range(200):
        job = jm.get(job["id"])
        if job["state"] in ("done", "failed"):
            break
        await asyncio.sleep(0.02)
    assert job["state"] == "failed" and job["phase"] == "rollback", job
    assert reg.get_state("active_chat_model_id") == good["id"]  # persisted choice untouched
    assert inference.chat.model_id == good["id"] and inference.chat.state == "ready"  # previous model restarted
    assert "wieder aktiv" in job["error"]["detail"]
    # both model files still exist: nothing was deleted by the failed switch
    assert Path(good["path"]).exists() and Path(bad["path"]).exists()
    with pytest.raises(JobError) as exc:
        await jm.delete_model(good["id"])
    assert exc.value.code == "model_active"
    await jm.delete_model(bad["id"])
    assert reg.get(bad["id"]) is None
