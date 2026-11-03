"""Resumable, verified HTTPS downloads from the Hugging Face Hub.

* URL: ``https://huggingface.co/<repo>/resolve/<revision>/<path>`` (official
  download route; the server redirects LFS files to its CDN).
* Every redirect hop must stay on ``huggingface.co`` / ``hf.co`` hosts.
* Partial files live in ``downloads/<job_id>/<name>.part``; a resume sends
  ``Range: bytes=<have>-`` and continues only if the server answers 206.
* SHA-256 is computed incrementally (and re-computed over an existing partial
  file before resuming) and compared to the catalog value at the end.
* Progress is reported through a callback; cancellation through a
  ``threading.Event``. Runs synchronously in a worker thread.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "SpAIder-Foldable/0.1 (+https://github.com/Spaider-studio/spaider)"
ALLOWED_HOST_SUFFIXES = ("huggingface.co", "hf.co", "cdn.playwright.dev", "storage.googleapis.com")  # models; browser package (CDN redirects to the Chrome-for-Testing bucket)
CHUNK = 1 << 20  # 1 MiB
RETRIES = 4


class DownloadError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


class DownloadCancelled(DownloadError):
    def __init__(self) -> None:
        super().__init__("cancelled", "Download abgebrochen.")


@dataclass
class Progress:
    done_bytes: int
    total_bytes: int
    speed_bps: float
    eta_seconds: Optional[float]


ProgressCallback = Callable[[Progress], None]


def hf_resolve_url(repo: str, revision: str, path: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/{revision}/{path}?download=true"


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


def _hash_existing(path: Path) -> tuple[hashlib._Hash, int]:  # type: ignore[name-defined]
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
            size += len(block)
    return h, size


def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def download_file(
    url: str,
    dest_part: Path,
    *,
    expected_size: int,
    expected_sha256: Optional[str],
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[Event] = None,
    timeout: float = 60.0,
) -> Path:
    """Download ``url`` into ``dest_part`` (resuming if it exists) and return
    the verified path. Raises :class:`DownloadError` with a machine code."""
    if not _host_allowed(url):
        raise DownloadError("untrusted_host", f"Download-Host nicht erlaubt: {url}")
    dest_part.parent.mkdir(parents=True, exist_ok=True)
    cancel = cancel or Event()

    have = 0
    hasher = hashlib.sha256()
    if dest_part.exists():
        hasher, have = _hash_existing(dest_part)
        if have > expected_size:
            dest_part.unlink()
            have, hasher = 0, hashlib.sha256()

    if _free_bytes(dest_part.parent) < (expected_size - have) + 64 * (1 << 20):
        raise DownloadError(
            "insufficient_space",
            "Nicht genug freier Speicher für den Download.",
        )

    attempt = 0
    while True:
        attempt += 1
        if cancel.is_set():
            raise DownloadCancelled()
        if have == expected_size:
            break  # a complete .part only needs the checksum check below (no Range request)
        try:
            have, hasher = _stream(url, dest_part, have, hasher, expected_size, progress, cancel, timeout)
            break
        except DownloadCancelled:
            raise
        except (httpx.HTTPError, OSError) as exc:
            if attempt >= RETRIES:
                raise DownloadError("network", f"Download fehlgeschlagen: {exc}") from exc
            delay = min(2.0**attempt, 15.0)
            logger.warning("download attempt %d failed (%s); retrying in %.0fs", attempt, exc, delay)
            # Wait but stay cancellable.
            if cancel.wait(delay):
                raise DownloadCancelled()
            if dest_part.exists():
                hasher, have = _hash_existing(dest_part)

    if have != expected_size:
        raise DownloadError("size_mismatch", f"Dateigröße stimmt nicht ({have} statt {expected_size} Bytes).")
    digest = hasher.hexdigest()
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        try:
            dest_part.unlink()
        except OSError:
            pass
        raise DownloadError("checksum_mismatch", "Prüfsumme stimmt nicht; die Datei wurde verworfen.")
    return dest_part


def _stream(url, dest_part, have, hasher, expected_size, progress, cancel, timeout):
    headers = {"User-Agent": USER_AGENT}
    if have > 0:
        headers["Range"] = f"bytes={have}-"
    with httpx.Client(follow_redirects=False, timeout=httpx.Timeout(timeout, read=timeout)) as client:
        # Manual redirect loop so every hop is checked against the allowlist.
        current = url
        for _hop in range(8):
            req = client.build_request("GET", current, headers=headers)
            resp = client.send(req, stream=True)
            if resp.is_redirect:
                nxt = str(resp.headers.get("location") or "")
                resp.close()
                if nxt.startswith("/"):
                    parsed = urlparse(current)
                    nxt = f"{parsed.scheme}://{parsed.netloc}{nxt}"
                if not _host_allowed(nxt):
                    raise DownloadError("untrusted_host", f"Weiterleitung auf nicht erlaubten Host: {nxt}")
                logger.info("download redirect -> %s", urlparse(nxt).hostname)
                current = nxt
                continue
            break
        else:
            raise DownloadError("network", "Zu viele Weiterleitungen.")

        try:
            if have > 0 and resp.status_code == 200:
                # Server ignored the range: start over.
                logger.info("server ignored Range; restarting download from 0")
                resp.close()
                dest_part.unlink(missing_ok=True)
                return _stream(url, dest_part, 0, hashlib.sha256(), expected_size, progress, cancel, timeout)
            if resp.status_code not in (200, 206):
                raise DownloadError("http_error", f"HTTP {resp.status_code} beim Download.")
            if resp.status_code == 206 and not (resp.headers.get("content-range") or "").startswith(f"bytes {have}-"):
                raise DownloadError("network", "Unerwartete Content-Range-Antwort beim Fortsetzen.")
            total = expected_size
            mode = "ab" if have > 0 else "wb"
            t0 = time.monotonic()
            last_report = 0.0
            window_t = t0
            window_b = 0
            speed = 0.0
            with open(dest_part, mode) as fh:
                for block in resp.iter_bytes(CHUNK):
                    if cancel.is_set():
                        raise DownloadCancelled()
                    fh.write(block)
                    hasher.update(block)
                    have += len(block)
                    window_b += len(block)
                    now = time.monotonic()
                    if now - window_t >= 1.0:
                        speed = window_b / (now - window_t)
                        window_t, window_b = now, 0
                    if progress and (now - last_report >= 0.25 or have >= total):
                        eta = (total - have) / speed if speed > 0 else None
                        progress(Progress(have, total, speed, eta))
                        last_report = now
                fh.flush()
        finally:
            resp.close()
    return have, hasher
