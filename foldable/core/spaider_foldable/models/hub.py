"""Hugging Face Hub access for the model area: search, repository resolution
and remote GGUF header inspection. Network access happens only when the user
explicitly uses the model area ("Weitere Modelle", "Katalog aktualisieren");
nothing here runs at chat start.

Security: model cards and file lists are data. No code from the hub is ever
executed; GGUF headers are parsed with the same bounded reader used for local
files, fetched with HTTP Range requests (a few MB at most).
"""

from __future__ import annotations

import io
import logging
import re
import struct
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from .downloader import USER_AGENT, _host_allowed
from .gguf import FILE_TYPES, GGUFError, _read_string, _read_value, is_architecture_supported

logger = logging.getLogger(__name__)

API = "https://huggingface.co/api"
_SPLIT_RE = re.compile(r"^(?P<base>.+)-(?P<idx>\d{5})-of-(?P<total>\d{5})\.gguf$", re.IGNORECASE)
_QUANT_RE = re.compile(r"(IQ\d_[A-Z_]+|Q\d_K_[A-Z]+|Q\d_K|Q\d_\d|BF16|F16|F32|MXFP4|UD-[A-Z0-9_]+)", re.IGNORECASE)
HEADER_FIRST_BYTES = 2 * 1024 * 1024
HEADER_MAX_BYTES = 48 * 1024 * 1024


class HubError(RuntimeError):
    def __init__(self, code: str, detail: str, status: int = 502):
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail)


@dataclass
class HubFile:
    path: str
    size: int
    sha256: Optional[str]
    split_index: Optional[int] = None
    split_total: Optional[int] = None
    split_base: Optional[str] = None
    quantization: Optional[str] = None


@dataclass
class RepoInfo:
    repo: str
    revision: str
    gated: object
    license: Optional[str]
    base_model: Optional[str]
    tags: list[str]
    files: list[HubFile]
    card_data: dict = field(default_factory=dict)
    last_modified: Optional[str] = None
    author: Optional[str] = None


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(
        timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, follow_redirects=True
    )


def search(query: str, *, limit: int = 20, timeout: float = 20.0) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    with _client(timeout) as c:
        r = c.get(
            f"{API}/models",
            params={"search": q, "filter": "gguf", "limit": limit, "sort": "downloads", "direction": -1},
        )
        if r.status_code == 429:
            raise HubError(
                "rate_limited", "Hugging Face begrenzt derzeit die Anfragen; bitte später erneut versuchen.", 429
            )
        r.raise_for_status()
        out = []
        for m in r.json():
            out.append(
                {
                    "repo": m.get("id") or m.get("modelId"),
                    "author": (m.get("id") or "").split("/")[0],
                    "name": (m.get("id") or "").split("/")[-1],
                    "likes": m.get("likes"),
                    "downloads": m.get("downloads"),
                    "updated": m.get("lastModified"),
                    "gguf": "gguf" in (m.get("tags") or []),
                    "status": "unverified",
                }
            )
        return out


def repo_info(repo: str, *, timeout: float = 30.0) -> RepoInfo:
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo or ""):
        raise HubError("invalid_repo", "Ungültige Repository-ID (erwartet „Anbieter/Modell“).", 422)
    with _client(timeout) as c:
        r = c.get(f"{API}/models/{repo}", params={"blobs": "true"})
        if r.status_code == 401:
            raise HubError("gated", "Dieses Repository erfordert ein Hugging-Face-Konto oder eine Freischaltung.", 409)
        if r.status_code == 404:
            raise HubError("not_found", "Repository nicht gefunden.", 404)
        if r.status_code == 429:
            raise HubError(
                "rate_limited", "Hugging Face begrenzt derzeit die Anfragen; bitte später erneut versuchen.", 429
            )
        r.raise_for_status()
        d = r.json()
    files: list[HubFile] = []
    for s in d.get("siblings", []):
        name = s.get("rfilename") or ""
        if not name.lower().endswith(".gguf"):
            continue
        lfs = s.get("lfs") or {}
        hf = HubFile(
            path=name, size=int(s.get("size") or lfs.get("size") or 0), sha256=lfs.get("sha256") or lfs.get("oid")
        )
        m = _SPLIT_RE.match(name.split("/")[-1])
        if m:
            hf.split_index, hf.split_total, hf.split_base = int(m.group("idx")), int(m.group("total")), m.group("base")
        qm = _QUANT_RE.search(name)
        hf.quantization = qm.group(1).upper() if qm else None
        files.append(hf)
    card = d.get("cardData") or {}
    return RepoInfo(
        repo=d.get("id") or repo,
        revision=d.get("sha") or "main",
        gated=d.get("gated", False),
        license=card.get("license"),
        base_model=(
            card.get("base_model")[0]
            if isinstance(card.get("base_model"), list) and card.get("base_model")
            else card.get("base_model")
        ),
        tags=d.get("tags") or [],
        files=files,
        card_data=card,
        last_modified=d.get("lastModified"),
        author=(d.get("id") or repo).split("/")[0],
    )


def split_group(files: list[HubFile], chosen: HubFile) -> list[HubFile]:
    """All parts of a split GGUF (sorted), or just the chosen file."""
    if chosen.split_base is None:
        return [chosen]
    parts = sorted((f for f in files if f.split_base == chosen.split_base), key=lambda f: f.split_index or 0)
    expected = chosen.split_total or len(parts)
    if len(parts) != expected:
        raise HubError(
            "split_incomplete",
            f"Geteilte Modelldatei unvollständig: {len(parts)} von {expected} Teilen vorhanden.",
            409,
        )
    return parts


def remote_gguf_header(repo: str, revision: str, path: str, *, timeout: float = 60.0) -> dict:
    """Read only the GGUF metadata of a remote file via Range requests."""
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{path}"
    if not _host_allowed(url):
        raise HubError("untrusted_host", "Nicht erlaubter Host.", 400)
    data = b""
    want = HEADER_FIRST_BYTES
    with _client(timeout) as c:
        while True:
            r = c.get(url, headers={"Range": f"bytes={len(data)}-{want - 1}"})
            if r.status_code == 401:
                raise HubError("gated", "Die Datei erfordert ein Hugging-Face-Konto oder eine Freischaltung.", 409)
            if r.status_code not in (200, 206):
                raise HubError("http_error", f"HTTP {r.status_code} beim Lesen des GGUF-Headers.", 502)
            data += r.content
            try:
                return _parse_header(data)
            except _NeedMore:
                if len(data) >= HEADER_MAX_BYTES or r.status_code == 200:
                    raise HubError("gguf_invalid", "GGUF-Header konnte nicht vollständig gelesen werden.", 422)
                want = min(len(data) * 2, HEADER_MAX_BYTES)


class _NeedMore(Exception):
    pass


def _parse_header(data: bytes) -> dict:
    fh = io.BytesIO(data)
    try:
        if fh.read(4) != b"GGUF":
            raise GGUFError("Keine GGUF-Datei.")
        (version,) = struct.unpack("<I", fh.read(4))
        (tensor_count,) = struct.unpack("<Q", fh.read(8))
        (kv_count,) = struct.unpack("<Q", fh.read(8))
        if kv_count > 100_000:
            raise GGUFError("Unplausible GGUF-Kopfdaten.")
        meta: dict = {}
        for _ in range(kv_count):
            key = _read_string(fh, True) or ""
            (vtype,) = struct.unpack("<I", fh.read(4))
            keep = key.startswith(("general.", "tokenizer.chat_template")) or key.endswith(
                (
                    ".context_length",
                    ".embedding_length",
                    ".block_count",
                    ".pooling_type",
                    ".attention.head_count",
                    ".attention.head_count_kv",
                    ".expert_count",
                )
            )
            value = _read_value(fh, vtype, keep)
            if keep and value is not None:
                meta[key] = value
    except GGUFError as exc:
        if "end of file" in str(exc):
            raise _NeedMore() from exc
        raise
    except Exception as exc:  # truncated buffer -> fetch more
        raise _NeedMore() from exc
    arch = str(meta.get("general.architecture") or "")
    ft = meta.get("general.file_type")
    return {
        "architecture": arch,
        "name": str(meta.get("general.name") or ""),
        "version": version,
        "tensor_count": tensor_count,
        "quantization": FILE_TYPES.get(int(ft), f"ftype_{ft}") if isinstance(ft, int) else "unknown",
        "context_length": meta.get(f"{arch}.context_length"),
        "embedding_length": meta.get(f"{arch}.embedding_length"),
        "block_count": meta.get(f"{arch}.block_count"),
        "head_count": meta.get(f"{arch}.attention.head_count"),
        "head_count_kv": meta.get(f"{arch}.attention.head_count_kv"),
        "pooling_type": meta.get(f"{arch}.pooling_type"),
        "has_chat_template": bool(meta.get("tokenizer.chat_template")),
        "chat_template_excerpt": str(meta.get("tokenizer.chat_template") or "")[:4000],
        "architecture_supported": is_architecture_supported(arch) if arch else False,
    }


def pick_default_file(files: list[HubFile]) -> Optional[HubFile]:
    """Prefer a single-file Q4_K_M, then Q5/Q6/Q8, excluding projector/draft files."""
    cands = [f for f in files if not any(x in f.path.lower() for x in ("mmproj", "mtp-", "dflash", "draft", "imatrix"))]
    cands = [f for f in cands if f.split_index in (None, 1)]
    order = ["Q4_K_M", "Q5_K_M", "Q4_0", "Q6_K", "Q8_0", "IQ4_XS", "Q5_0", "F16", "BF16"]
    for q in order:
        for f in cands:
            if (f.quantization or "").upper() == q:
                return f
    return cands[0] if cands else None


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
