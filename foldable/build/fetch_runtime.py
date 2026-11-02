"""Download the pinned llama.cpp Windows release, verify its SHA-256 and
extract the server runtime into ``foldable/build/runtime/llama``.

Pinned build (see docs/portable/ARCHITECTURE.md):
  tag      b12032 (2026-11-01)
  asset    llama-b12032-bin-win-cpu-x64.zip
  sha256   5296f5d18bb7d422c7c26f4bb9bc796d9d4f41e1312a8c296cff475bdc60d0d4

Usage:  python foldable/build/fetch_runtime.py [--variant cpu]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

PINS = {
    "cpu": {
        "tag": "b12032",
        "asset": "llama-b12032-bin-win-cpu-x64.zip",
        "sha256": "5296f5d18bb7d422c7c26f4bb9bc796d9d4f41e1312a8c296cff475bdc60d0d4",
    },
}
# Files needed by llama-server (everything else in the zip is a CLI tool).
KEEP_PREFIXES = ("llama-server", "llama.dll", "llama-common", "ggml", "libomp", "mtmd", "LICENSE")
LICENSE_URL = "https://raw.githubusercontent.com/ggml-org/llama.cpp/{tag}/LICENSE"

HERE = Path(__file__).resolve().parent


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="cpu", choices=sorted(PINS))
    ap.add_argument("--out", type=Path, default=HERE / "runtime" / "llama")
    ap.add_argument("--cache", type=Path, default=HERE / "cache")
    args = ap.parse_args()
    pin = PINS[args.variant]
    args.cache.mkdir(parents=True, exist_ok=True)
    zip_path = args.cache / pin["asset"]
    url = f"https://github.com/ggml-org/llama.cpp/releases/download/{pin['tag']}/{pin['asset']}"
    if not zip_path.exists() or sha256_of(zip_path) != pin["sha256"]:
        print(f"downloading {url}")
        urllib.request.urlretrieve(url, zip_path)  # noqa: S310 (pinned https URL)
    digest = sha256_of(zip_path)
    if digest != pin["sha256"]:
        print(f"ERROR: sha256 mismatch for {zip_path}: {digest}", file=sys.stderr)
        return 1
    print(f"verified {pin['asset']} sha256={digest}")
    if args.out.exists():
        for p in args.out.iterdir():
            p.unlink()
    args.out.mkdir(parents=True, exist_ok=True)
    kept = []
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            name = Path(member).name
            if not name or member.endswith("/"):
                continue
            if name.startswith(KEEP_PREFIXES) and not name.startswith("ggml-rpc"):
                (args.out / name).write_bytes(zf.read(member))
                kept.append(name)
    lic = args.out / "LICENSE-llama.cpp"
    try:
        urllib.request.urlretrieve(LICENSE_URL.format(tag=pin["tag"]), lic)  # noqa: S310
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not fetch llama.cpp LICENSE: {exc}", file=sys.stderr)
    (args.out / "RUNTIME_VERSION.txt").write_text(
        f"llama.cpp {pin['tag']} ({args.variant}, win-x64)\nasset {pin['asset']}\nsha256 {pin['sha256']}\n", encoding="utf-8"
    )
    print(f"extracted {len(kept)} files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
