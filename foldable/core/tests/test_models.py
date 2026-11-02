from __future__ import annotations

import hashlib
import http.server
import threading
from pathlib import Path
from threading import Event

import pytest

from spaider_foldable.models.catalog import load_catalog
from spaider_foldable.models.downloader import DownloadCancelled, DownloadError, download_file
from spaider_foldable.models.gguf import GGUFError, is_architecture_supported, read_gguf_info
from spaider_foldable.models.registry import ModelRegistry, ModelValidationError, validate_gguf
from spaider_foldable.sysinfo import HostInfo

from .gguf_fixture import write_fake_gguf

GiB = 1024**3


def _host(ram_gb: float, free_gb: float, avail_gb: float | None = None, fs: str = "NTFS") -> HostInfo:
    return HostInfo(
        os="windows", arch="x64", hostname="h", cpu="cpu", threads=8,
        ram_total_bytes=int(ram_gb * GiB), ram_available_bytes=int((avail_gb or ram_gb * 0.6) * GiB),
        disk_total_bytes=int(free_gb * 2 * GiB), disk_free_bytes=int(free_gb * GiB), filesystem=fs,
        max_file_bytes=4_294_967_295 if fs == "FAT32" else None,
    )  # fmt: skip


def test_catalog_loads_and_has_checksums():
    cat = load_catalog()
    assert cat.get("qwen3-0.6b-q8_0") is not None
    for e in cat.entries:
        assert e.revision and len(e.revision) == 40
        for f in e.files:
            assert len(f.sha256) == 64 and f.size > 0
        assert e.license.id
        assert is_architecture_supported(e.architecture)
    assert all(e.support == "documented" for e in cat.entries if e.id.startswith("qwen3.8"))


def test_catalog_fit_levels():
    cat = load_catalog()
    small = cat.get("qwen3-0.6b-q8_0")
    big = cat.get("qwen3.8-27b-q4_k_m-ggml")
    assert small.fit_for(_host(32, 200), installed=False).level == "recommended"
    assert big.fit_for(_host(16, 200), installed=False).level == "too_large"
    assert big.fit_for(_host(32, 200, avail_gb=10), installed=False).level == "tight"
    assert big.fit_for(_host(32, 200, avail_gb=30), installed=False).level == "ok"
    assert small.fit_for(_host(32, 0.5), installed=False).level == "too_large"  # no disk
    assert big.fit_for(_host(64, 200, fs="FAT32"), installed=False).level == "too_large"  # 4 GB file limit
    assert "FAT32" in big.fit_for(_host(64, 200, fs="FAT32"), installed=False).message_de


def test_gguf_header_parse(tmp_path):
    p = write_fake_gguf(tmp_path / "m.gguf", architecture="qwen3", file_type=15, context_length=32768)
    info = read_gguf_info(p)
    assert info.architecture == "qwen3" and info.quantization == "Q4_K_M" and info.context_length == 32768
    assert info.has_chat_template and not info.is_embedding_model
    emb = read_gguf_info(write_fake_gguf(tmp_path / "e.gguf", name="Qwen3-Embedding", pooling_type=3))
    assert emb.is_embedding_model
    with pytest.raises(GGUFError):
        read_gguf_info(Path(tmp_path / "bad.gguf").write_bytes(b"NOTG" + b"\x00" * 64) and tmp_path / "bad.gguf")


def test_validate_rejects_unsupported_architecture(tmp_path):
    p = write_fake_gguf(tmp_path / "x.gguf", architecture="totally-new-arch")
    with pytest.raises(ModelValidationError) as exc:
        validate_gguf(p)
    assert exc.value.code == "arch_unsupported"
    with pytest.raises(ModelValidationError) as exc2:
        validate_gguf(write_fake_gguf(tmp_path / "e.gguf", pooling_type=3), expected_role="chat")
    assert exc2.value.code == "role_mismatch"


def test_registry_import_and_reconcile(paths, db, tmp_path):
    reg = ModelRegistry(db, paths)
    src = (
        write_fake_gguf(tmp_path / "ext" / "Mein Modell ü.gguf", extra_bytes=5000)
        if (tmp_path / "ext").mkdir() is None
        else None
    )
    model = reg.import_local(src)
    assert model["source"] == "import" and model["role"] == "chat" and model["sha256_verified"]
    assert Path(model["path"]).exists() and paths.is_inside_data(model["path"])
    assert model["rel_path"].startswith("models/")
    # manifest is the source of truth: wipe the table and reconcile
    db.execute("DELETE FROM models")
    reg.reconcile()
    again = reg.get(model["id"])
    assert again and again["state"] == "ready"
    # broken when the file is truncated
    Path(model["path"]).write_bytes(b"GGUF")
    reg.reconcile()
    assert reg.get(model["id"])["state"] == "broken"
    reg.set_state("active_chat_model_id", model["id"])
    reg.reconcile()
    assert reg.get_state("active_chat_model_id") is None  # broken model never stays active
    reg.remove(model["id"])
    assert reg.get(model["id"]) is None and not (paths.models_dir / model["id"]).exists()


class _RangeHandler(http.server.SimpleHTTPRequestHandler):
    payload = b""
    fail_after: int | None = None
    hits = 0

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        type(self).hits += 1
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/file.bin")
            self.end_headers()
            return
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
        if self.fail_after is not None and len(body) > self.fail_after:
            body = body[: self.fail_after]
            type(self).fail_after = None  # only once
            self.send_header("Content-Length", str(len(data) - start))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.connection.close()
            return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def http_server():
    _RangeHandler.payload = bytes(range(256)) * 4000  # 1 MB
    _RangeHandler.hits = 0
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def test_download_rejects_untrusted_host(http_server, tmp_path):
    url = f"http://127.0.0.1:{http_server.server_address[1]}/file.bin"
    with pytest.raises(DownloadError) as exc:
        download_file(url, tmp_path / "f.part", expected_size=1, expected_sha256=None)
    assert exc.value.code == "untrusted_host"


def test_download_resume_and_checksum(http_server, tmp_path, monkeypatch):
    from spaider_foldable.models import downloader

    monkeypatch.setattr(downloader, "_host_allowed", lambda url: True)
    port = http_server.server_address[1]
    data = _RangeHandler.payload
    sha = hashlib.sha256(data).hexdigest()
    dest = tmp_path / "file.bin.part"
    # first attempt: connection cut mid-way, then automatic retry resumes with Range
    _RangeHandler.fail_after = 300_000
    out = download_file(f"http://127.0.0.1:{port}/redirect", dest, expected_size=len(data), expected_sha256=sha)
    assert out.read_bytes() == data
    assert _RangeHandler.hits >= 3  # redirect + cut + resumed
    # wrong checksum is rejected and the part file removed
    dest2 = tmp_path / "f2.part"
    with pytest.raises(DownloadError) as exc:
        download_file(f"http://127.0.0.1:{port}/file.bin", dest2, expected_size=len(data), expected_sha256="0" * 64)
    assert exc.value.code == "checksum_mismatch" and not dest2.exists()


def test_download_cancel(http_server, tmp_path, monkeypatch):
    from spaider_foldable.models import downloader

    monkeypatch.setattr(downloader, "_host_allowed", lambda url: True)
    port = http_server.server_address[1]
    cancel = Event()
    cancel.set()
    with pytest.raises(DownloadCancelled):
        download_file(f"http://127.0.0.1:{port}/file.bin", tmp_path / "c.part", expected_size=1_024_000,
                      expected_sha256=None, cancel=cancel)  # fmt: skip
