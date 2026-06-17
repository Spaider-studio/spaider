"""LLM provider key validation for ``spaider init``.

Each provider check is a tiny smoke call (1-token completion) that returns a
strict ``(ok, error_hint)`` pair. The wizard can then re-prompt the user with
an actionable hint on failure.

OpenAI does a live probe (lists models). Anthropic is a format check (`sk-ant-`
prefix) — a deeper live probe can land later. Ollama checks daemon reachability
(no key needed). All three are wired into ``spaider init``'s provider menu.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class KeyCheckResult:
    ok: bool
    detail: str  # short human-readable hint when not ok; "" when ok


def validate_openai_key(api_key: str, *, timeout_s: float = 10.0) -> KeyCheckResult:
    """Confirm an OpenAI API key works by listing models.

    Listing models is cheaper than running a completion (no token billing) and
    a 200 response is sufficient to confirm both the key format and quota.
    """
    if not api_key or not api_key.startswith("sk-"):
        return KeyCheckResult(
            ok=False,
            detail="OpenAI keys start with 'sk-'. Get one at https://platform.openai.com/api-keys.",
        )
    try:
        with httpx.Client(timeout=timeout_s) as c:
            resp = c.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        return KeyCheckResult(ok=False, detail=f"network error talking to OpenAI: {exc}")

    if resp.status_code == 200:
        return KeyCheckResult(ok=True, detail="")
    if resp.status_code == 401:
        return KeyCheckResult(
            ok=False,
            detail="OpenAI rejected the key (401). Likely typo or revoked key.",
        )
    if resp.status_code == 429:
        return KeyCheckResult(
            ok=False,
            detail="OpenAI returned 429 — key is valid but you're rate limited or out of quota.",
        )
    return KeyCheckResult(
        ok=False,
        detail=f"OpenAI returned status {resp.status_code}: {resp.text[:120]}",
    )


def validate_anthropic_key(api_key: str) -> KeyCheckResult:
    """Anthropic key validation — format check (``sk-ant-`` prefix)."""
    if not api_key.startswith("sk-ant-"):
        return KeyCheckResult(
            ok=False,
            detail="Anthropic keys start with 'sk-ant-'. Get one at https://console.anthropic.com/.",
        )
    return KeyCheckResult(ok=True, detail="(format-only check)")


def validate_ollama_local(base_url: str = "http://localhost:11434") -> KeyCheckResult:
    """Confirm a local Ollama daemon is reachable. No key needed."""
    try:
        with httpx.Client(timeout=2.0) as c:
            resp = c.get(f"{base_url}/api/tags")
            if resp.status_code == 200:
                return KeyCheckResult(ok=True, detail="")
            return KeyCheckResult(
                ok=False,
                detail=f"Ollama responded with {resp.status_code}.",
            )
    except httpx.HTTPError as exc:
        return KeyCheckResult(
            ok=False,
            detail=f"Ollama not reachable at {base_url}: {exc}. Start it with `ollama serve`.",
        )
