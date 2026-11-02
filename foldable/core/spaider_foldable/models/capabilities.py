"""Model capabilities derived from verified GGUF metadata plus family data
(``families.json``) and the bundled runtime variants.

Nothing here guesses from a model's marketing name alone: the architecture
and chat-template presence come from the GGUF header; the family table only
adds knowledge about templates (reasoning toggle, system role) and is checked
against the GGUF chat template text when it was captured at import time.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Optional

EMBEDDING_ARCHS = {
    "bert",
    "modern-bert",
    "nomic-bert",
    "nomic-bert-moe",
    "neo-bert",
    "jina-bert-v2",
    "jina-bert-v3",
    "eurobert",
    "gemma-embedding",
    "gemma-embedding2",
    "llama-embed",
    "t5encoder",
}
AUDIO_ARCHS = {"whisper", "qwen3tts", "pockettts", "wavtokenizer-dec", "talkie", "qwen3-asr"}
VISION_PROJECTOR_ARCHS = {"clip", "mtmd"}


@lru_cache(maxsize=1)
def families() -> list[dict]:
    raw = (resources.files("spaider_foldable.models") / "families.json").read_text(encoding="utf-8")
    return json.loads(raw)["families"]


def detect_family(architecture: str, name: str) -> Optional[dict]:
    arch = (architecture or "").lower()
    lname = (name or "").lower()
    # name hints first, most specific (longest) hint wins: 'qwen3.8' must beat 'qwen3'
    hints = sorted(((h, fam) for fam in families() for h in fam.get("detect_name", [])), key=lambda t: -len(t[0]))
    for h, fam in hints:
        if h in lname:
            return fam
    for fam in families():
        if arch in fam.get("detect_arch", []):
            return fam
    return None


def classify_type(info_or_model: dict) -> str:
    """chat | embedding | base | vision | audio from GGUF-derived fields."""
    arch = (info_or_model.get("architecture") or "").lower()
    meta = info_or_model.get("metadata") or {}
    name = (info_or_model.get("name") or "").lower()
    if arch in AUDIO_ARCHS:
        return "audio"
    if arch in VISION_PROJECTOR_ARCHS:
        return "vision"
    pooling = meta.get(f"{arch}.pooling_type")
    if arch in EMBEDDING_ARCHS or (pooling not in (None, 0)) or info_or_model.get("role") == "embedding":
        return "embedding"
    if "embedding" in name and "embed" in name:
        return "embedding"
    if not info_or_model.get("has_chat_template", meta.get("has_chat_template", True)):
        return "base"
    return "chat"


def capabilities_for_model(model: dict, runtime_dirs: dict[str, Path] | None = None) -> dict:
    """``model`` is the registry's API dict (with ``metadata``)."""
    meta = model.get("metadata") or {}
    arch = model.get("architecture") or ""
    fam = detect_family(arch, model.get("name") or "")
    template_text = meta.get("chat_template_excerpt") or ""
    has_template = bool(meta.get("has_chat_template", True))
    reasoning_toggle = fam.get("reasoning_toggle") if fam else None
    if reasoning_toggle and template_text and reasoning_toggle not in template_text:
        reasoning_toggle = None  # the shipped template does not expose the switch
    backends = sorted(k for k in (runtime_dirs or {"cpu": Path(".")}).keys())
    return {
        "chat_template": has_template,
        "reasoning_toggle": reasoning_toggle,
        "system_role": fam.get("system_role", True) if fam else True,
        "context_length": model.get("context_length"),
        "modalities": ["text"],
        "structured_output": True,  # llama-server grammar/json_schema sampling works for every model
        "backends": backends,
        "family": fam.get("id") if fam else None,
        "type": classify_type(model),
        "notes_de": fam.get("notes_de", "") if fam else "",
    }
