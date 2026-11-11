# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the SpAIder Foldable core (onedir, console app).

Build:  pyinstaller foldable/build/spaider_foldable_core.spec --noconfirm
Output: foldable/build/dist/spaider-foldable-core/

The reused SpAIder backend package (``app``) is bundled from ../../backend.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
CORE_DIR = SPEC_DIR.parent / "core"
BACKEND_DIR = SPEC_DIR.parent.parent / "backend"

datas = []
binaries = []
hiddenimports = []

# litellm ships a large JSON cost map + tokenizer data; collect everything.
for pkg in ("litellm", "tiktoken", "tiktoken_ext", "tokenizers", "openai", "pdfplumber", "pdfminer", "pypdfium2"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:  # package may be absent (optional)
        pass

# The package is used from the checkout (not pip-installed), so list its data
# files explicitly instead of relying on collect_data_files.
_PKG = CORE_DIR / "spaider_foldable"
datas += [
    (str(_PKG / "models" / "catalog.json"), "spaider_foldable/models"),
    (str(_PKG / "models" / "supported_architectures.json"), "spaider_foldable/models"),
    (str(_PKG / "db" / "schema_v1.sql"), "spaider_foldable/db"),
    (str(_PKG / "db" / "schema_v2.sql"), "spaider_foldable/db"),
    (str(_PKG / "db" / "schema_v3.sql"), "spaider_foldable/db"),
    (str(_PKG / "db" / "schema_v4.sql"), "spaider_foldable/db"),
    (str(_PKG / "db" / "schema_v5.sql"), "spaider_foldable/db"),
    (str(_PKG / "models" / "families.json"), "spaider_foldable/models"),
]
# SpAIder prompt files used by the compressor.
datas += [(str(BACKEND_DIR / "app" / "prompts"), "app/prompts")]
hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("spaider_foldable")
hiddenimports += ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
                  "uvicorn.lifespan.off", "anyio._backends._asyncio", "Levenshtein", "redis.asyncio", "psutil"]
# 0.3: desktop observation/automation and the own browser
for _mod in ("pywinauto", "comtypes", "mss", "playwright", "greenlet", "pyee"):
    try:
        hiddenimports += collect_submodules(_mod)
    except Exception:  # noqa: BLE001
        pass
hiddenimports += ["win32gui", "win32api", "win32con", "win32process", "win32clipboard", "pywintypes", "win32ui", "win32event",
                  "comtypes.gen", "comtypes.client", "pywinauto.controls.uiawrapper", "pywinauto.uia_defines", "PIL.ImageDraw", "PIL.ImageFont"]
try:
    # Playwright's node driver (node.exe + package) must travel as data; the browser itself is a separate download.
    import playwright as _pw
    _pw_dir = Path(_pw.__file__).parent
    datas += [(str(_pw_dir / "driver"), "playwright/driver")]
    datas += collect_data_files("comtypes")
except Exception:  # noqa: BLE001
    pass

a = Analysis(
    [str(SPEC_DIR / "core_entry.py")],
    pathex=[str(CORE_DIR), str(BACKEND_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL.ImageTk", "pytest", "IPython", "jupyter", "notebook", "torch", "transformers",
              "aiokafka", "asyncpg", "sqlalchemy", "clickhouse_connect", "pandas", "trafilatura", "docx"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="spaider-foldable-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="spaider-foldable-core",
)
