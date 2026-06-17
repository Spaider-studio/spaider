FROM python:3.13-slim-bookworm

WORKDIR /app

# Runtime libs for pdfplumber + python-docx (PIL/Pillow transitively wants
# libxcb1; libgl1 / libglib2.0-0 keep us safe against opencv-shaped libs
# pulled in by trafilatura / future parsers).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libxcb1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# uv (Astral) — install from PyPI in one step (~2 s) instead of pulling
# the upstream uv image from ghcr.io, which has been rate-limiting builds.
RUN pip install --no-cache-dir --root-user-action=ignore --timeout 600 --retries 5 "uv>=0.11.0"

# pyproject.toml is the single source of truth for runtime + dev deps.
# `.[dev]` includes pytest + aiosqlite so `python -m pytest` works inside
# the running container during smoke tests. Adds ~12 MB; acceptable.
#
# Layer-cache caveat: copying the full source before `pip install` means
# any code change invalidates the deps-install layer. If that becomes
# painful we can switch to `uv sync` with a committed uv.lock as a
# follow-up — keeps deps in their own cacheable layer.
COPY . .

# CPU-only torch wheels — avoids ~2 GB of nvidia-cuda-* downloads we can't use
# on Apple Silicon / aarch64-linux. (docling was dropped, but
# the index flag is harmless on a torch-free dep set and ready for the day
# any transitive dep pulls torch back in.)
RUN uv pip install --system --no-cache \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --index-strategy unsafe-best-match \
    ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
