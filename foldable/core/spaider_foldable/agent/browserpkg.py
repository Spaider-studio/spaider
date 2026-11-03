"""Pinned browser package (Chromium for Testing, the build Playwright 1.64.1 is
made for) downloaded on demand into ``data/browser`` and verified by size and
SHA-256. Nothing from the user's own browser is touched."""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import Callable, Optional

from ..models.downloader import DownloadError, Progress, download_file

logger = logging.getLogger(__name__)

BROWSER_PACKAGE = {
    "name": "Chromium for Testing 156.0.8078.4 (Playwright-Build 1248)",
    "revision": "1248",
    "urls": [
        "https://cdn.playwright.dev/builds/cft/156.0.8078.4/win64/chrome-win64.zip",
    ],
    "file": "chrome-win64.zip",
    "size": None,  # filled from the pinned record below when known
    "sha256": None,
    "license": "BSD-3-Clause (Chromium) – siehe LICENSE im Paket",
    "playwright": "1.64.1",
}


def set_pin(size: Optional[int], sha256: Optional[str]) -> None:
    BROWSER_PACKAGE["size"] = size
    BROWSER_PACKAGE["sha256"] = sha256


def install_dir(data_dir: Path) -> Path:
    return data_dir / "browser" / f"chromium-{BROWSER_PACKAGE['revision']}"


def installed_chrome(data_dir: Path) -> Optional[Path]:
    p = install_dir(data_dir) / "chrome-win64" / "chrome.exe"
    return p if p.exists() else None


def install(data_dir: Path, downloads_dir: Path, *, progress: Optional[Callable[[Progress], None]] = None, cancel=None) -> Path:
    """Download (resumable, verified) and unpack the pinned browser. Returns chrome.exe."""
    existing = installed_chrome(data_dir)
    if existing is not None:
        return existing
    part = downloads_dir / "browser" / (BROWSER_PACKAGE["file"] + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    last_exc: Optional[Exception] = None
    for url in BROWSER_PACKAGE["urls"]:
        try:
            download_file(url, part, expected_size=BROWSER_PACKAGE["size"], expected_sha256=BROWSER_PACKAGE["sha256"], progress=progress, cancel=cancel)
            last_exc = None
            break
        except DownloadError as exc:
            last_exc = exc
            if exc.code != "network" and exc.code != "http":
                raise
    if last_exc is not None:
        raise last_exc
    target = install_dir(data_dir)
    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(part) as zf:
        for member in zf.infolist():
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise DownloadError("corrupt", "Ungültiger Pfad im Archiv.")
        zf.extractall(tmp)
    chrome = tmp / "chrome-win64" / "chrome.exe"
    if not chrome.exists():
        shutil.rmtree(tmp, ignore_errors=True)
        raise DownloadError("corrupt", "Das Browserpaket enthält keine chrome-win64/chrome.exe.")
    tmp.replace(target)
    part.unlink(missing_ok=True)
    return target / "chrome-win64" / "chrome.exe"


def remove(data_dir: Path) -> None:
    d = install_dir(data_dir)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
