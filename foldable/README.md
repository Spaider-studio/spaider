# SpAIder Foldable

Portable, fully local SpAIder chat for Windows 11 x64: launcher + UI (Electron),
core (Python, reuses `backend/app` as a library), bundled llama.cpp runtime,
SQLite memory. Everything persistent lives next to `SpAIder.exe` in `data/`.

Documentation (German): [docs/portable/](../docs/portable/)
`QUICKSTART_DE.md`, `ARCHITECTURE.md`, `API_CONTRACT.md`, `TEST_REPORT.md`,
`KNOWN_LIMITATIONS.md`, `INTEGRATION_HANDOFF.md`, `PR_DRAFT.md`, `WORKLOG.md`.

## Layout

```
foldable/
  core/      Python package spaider_foldable (FastAPI on loopback) + tests
  desktop/   Electron main/preload + React renderer (Vite, Tailwind v4)
  build/     fetch_runtime.py, spaider_foldable_core.spec, assemble_portable.py
```

## Developer setup (Windows)

```powershell
# Python core (uses ../../backend as a library; no Docker, no Neo4j)
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e foldable/core[dev]
python foldable/build/fetch_runtime.py             # pinned llama.cpp b12032 -> foldable/build/runtime/llama
cd foldable/core; pytest                            # unit tests (no model needed)

# Real end-to-end test (downloads nothing; point it at local GGUF files)
$env:SPAIDER_FOLDABLE_REAL_TESTS=1
$env:SPAIDER_FOLDABLE_RUNTIME_DIR="...\foldable\build\runtime\llama"
$env:SPAIDER_FOLDABLE_TEST_CHAT_MODEL="...\Qwen3-0.6B-Q8_0.gguf"
$env:SPAIDER_FOLDABLE_TEST_EMBED_MODEL="...\Qwen3-Embedding-0.6B-Q8_0.gguf"
pytest tests/test_real_e2e.py -s

# Run the core by hand
$env:SPAIDER_FOLDABLE_TOKEN="dev-token-0123456789abcdef"
python -m spaider_foldable --app-root C:\path\to\AppRoot --runtime-dir ...\runtime\llama
# prints {"event":"ready","port":N}; API at http://127.0.0.1:N/api with header X-SpAIder-Token

# Desktop
cd foldable/desktop; npm ci; npm run dev            # see foldable/desktop/README.md
```

## Build the portable package

```powershell
python foldable/build/fetch_runtime.py
pyinstaller foldable/build/spaider_foldable_core.spec --noconfirm --distpath foldable/build/dist --workpath foldable/build/work
cd foldable/desktop; npm ci; npm run build; npm run package; cd ../..
python foldable/build/assemble_portable.py --version 0.1.0
# -> foldable/build/out/SpAIder-Foldable-0.1.0-win-x64.zip + SHA256SUMS.txt
```

License: AGPL-3.0-only (same as the SpAIder core it extends).
