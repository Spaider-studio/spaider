"""Assemble the portable Windows package.

Inputs (all produced by other steps):
  foldable/desktop/out/SpAIder-win32-x64/      packaged Electron app (@electron/packager)
  foldable/build/dist/spaider-foldable-core/    frozen core (PyInstaller)
  foldable/build/runtime/llama/                 pinned llama.cpp runtime (fetch_runtime.py)

Output:
  foldable/build/out/SpAIder-Foldable-<version>-win-x64/   staged folder
  foldable/build/out/SpAIder-Foldable-<version>-win-x64.zip
  foldable/build/out/SHA256SUMS.txt                        zip digest + git commit

Layout inside the zip (one folder, unpack anywhere, no installation):
  SpAIder/
    SpAIder.exe                       launcher (Electron)
    resources/app.asar                UI
    resources/core/                   spaider-foldable-core.exe + libs
    resources/runtime/llama/          llama-server.exe + ggml DLLs
    licenses/                         AGPL-3.0, llama.cpp MIT, Electron, OpenMP
    README-ERSTE-SCHRITTE.txt
    data/                             created on first start (models, db, imports, logs)
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOLDABLE = HERE.parent
REPO = FOLDABLE.parent


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def copytree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="0.3.0")
    ap.add_argument("--desktop-out", type=Path, default=FOLDABLE / "desktop" / "out" / "SpAIder-win32-x64")
    ap.add_argument("--core-dist", type=Path, default=HERE / "dist" / "spaider-foldable-core")
    ap.add_argument("--runtime", type=Path, default=HERE / "runtime" / "llama")
    ap.add_argument("--out", type=Path, default=HERE / "out")
    ap.add_argument("--no-zip", action="store_true")
    args = ap.parse_args()

    for p, what in ((args.desktop_out, "packaged Electron app"), (args.core_dist, "frozen core"), (args.runtime, "runtime")):
        if not p.exists():
            print(f"ERROR: missing {what}: {p}", file=sys.stderr)
            return 1
    name = f"SpAIder-Foldable-{args.version}-win-x64"
    stage = args.out / name / "SpAIder"
    if stage.parent.exists():
        if (stage / "data").exists():
            # A staged folder that has been RUN holds user data (models, chats).
            # Never wipe it silently; the caller must pick another --version/--out.
            print(f"ERROR: {stage} contains a data/ directory (it was used); refusing to overwrite. "
                  "Use a new --version or --out, or remove it yourself.", file=sys.stderr)
            return 1
        shutil.rmtree(stage.parent)
    stage.mkdir(parents=True)

    print("copying Electron app ...")
    copytree(args.desktop_out, stage)
    print("copying core ...")
    copytree(args.core_dist, stage / "resources" / "core")
    print("copying runtime (cpu) ...")
    copytree(args.runtime, stage / "resources" / "runtime" / "llama")
    vulkan = args.runtime.parent / "llama-vulkan"
    if vulkan.exists():
        print("copying runtime (vulkan) ...")
        copytree(vulkan, stage / "resources" / "runtime" / "llama-vulkan")

    lic = stage / "licenses"
    lic.mkdir(exist_ok=True)
    shutil.copy2(REPO / "LICENSE", lic / "LICENSE-SpAIder-AGPL-3.0.txt")
    for f in ("LICENSE-llama.cpp", "LICENSE-LLVM-OpenMP", "RUNTIME_VERSION.txt"):
        src = args.runtime / f
        if src.exists():
            shutil.copy2(src, lic / f)
    for f in ("LICENSE", "LICENSES.chromium.html"):
        src = stage / f
        if src.exists():
            shutil.move(str(src), str(lic / f"Electron-{f}"))
    (stage / "README-ERSTE-SCHRITTE.txt").write_text(
        "SpAIder Foldable\n"
        "================\n\n"
        "1. Diesen Ordner (SpAIder) vollständig auf den USB-Datenträger entpacken.\n"
        "2. SpAIder.exe doppelklicken. Beim ersten Start ein Modell auswählen und herunterladen.\n"
        "3. Danach funktioniert der Chat offline. Alle Daten liegen im Unterordner data/.\n"
        "4. Zum Beenden in der App „Sicher beenden“ wählen und erst dann den Datenträger entfernen.\n\n"
        "Details: docs/portable/QUICKSTART_DE.md im Quellcode-Repository.\n",
        encoding="utf-8",
    )
    (stage / "BUILD_INFO.txt").write_text(
        f"version {args.version}\ncommit {git_commit()}\n", encoding="utf-8"
    )

    total = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file())
    print(f"staged {stage} ({total / 2**20:.0f} MB)")
    if args.no_zip:
        return 0
    zip_path = args.out / f"{name}.zip"
    print(f"zipping {zip_path} ...")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(stage.parent.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(stage.parent.parent))
    digest = sha256_of(zip_path)
    record = f"{digest}  {zip_path.name}\ncommit {git_commit()}\nsize {zip_path.stat().st_size}\n"
    (args.out / f"SHA256SUMS-{args.version}.txt").write_text(record, encoding="utf-8")
    # cumulative list: keep the records of earlier versions, replace only this zip's block
    sums = args.out / "SHA256SUMS.txt"
    blocks = []
    if sums.exists():
        for block in sums.read_text(encoding="utf-8").split("\n\n"):
            if block.strip() and f"  {zip_path.name}" not in block:
                blocks.append(block.strip() + "\n")
    blocks.append(record)
    sums.write_text("\n".join(blocks), encoding="utf-8")
    print(f"done: {zip_path} sha256={digest} size={zip_path.stat().st_size / 2**20:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
