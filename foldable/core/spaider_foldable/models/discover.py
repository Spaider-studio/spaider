"""„Alle Modelle entdecken“: paginated Hugging Face Hub search for relevant model
tasks with filters (family keyword, author, exact repo id, task), variant
grouping per repository and a sanitised model card excerpt.

Hub metadata is foreign input: bounded sizes, no HTML/scripts (the renderer
shows the card excerpt as plain text/markdown without raw HTML), no endpoints
or instructions are derived from it. Network access only on explicit user
action; results are cached with their age (``catalog_cache``)."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from .downloader import USER_AGENT
from .hub import API, HubError, RepoInfo, _QUANT_RE, repo_info

logger = logging.getLogger(__name__)

TASKS = {"text-generation": "Text", "image-text-to-text": "Text + Bild", "any": "Alle Aufgaben"}
SORTS = {"downloads": "downloads", "likes": "likes", "updated": "lastModified", "trending": "trendingScore"}
_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
_HTML_RE = re.compile(r"<[^>]+>")
_CURSOR_RE = re.compile(r"[?&]cursor=([^&]+)")
CARD_MAX_CHARS = 6000
VERIFIED_PUBLISHERS = {
    # publisher account -> how it was verified (official organisations on the Hub, checked 2026-11-11)
    "Qwen": "offiziell (Alibaba Qwen)",
    "zai-org": "offiziell (Z.ai / GLM)",
    "deepseek-ai": "offiziell (DeepSeek)",
    "mistralai": "offiziell (Mistral AI)",
    "meta-llama": "offiziell (Meta Llama)",
    "google": "offiziell (Google)",
    "microsoft": "offiziell (Microsoft)",
    "ggml-org": "llama.cpp-Projekt (Konvertierungen)",
    "unsloth": "Community-Konvertierungen (Unsloth)",
    "bartowski": "Community-Konvertierungen",
}


@dataclass
class DiscoverPage:
    items: list[dict]
    next_cursor: Optional[str]
    fetched_at: str
    query: dict = field(default_factory=dict)


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, follow_redirects=True)


def discover(
    *,
    query: str = "",
    author: str = "",
    task: str = "any",
    gguf_only: bool = True,
    sort: str = "downloads",
    limit: int = 20,
    cursor: Optional[str] = None,
    timeout: float = 20.0,
) -> DiscoverPage:
    params: dict = {"limit": max(1, min(limit, 50)), "sort": SORTS.get(sort, "downloads"), "direction": -1, "full": "false"}
    q = (query or "").strip()
    if q:
        params["search"] = q[:120]
    if author.strip():
        params["author"] = author.strip()[:80]
    filters = []
    if gguf_only:
        filters.append("gguf")
    if task != "any" and task in TASKS:
        params["pipeline_tag"] = task
    if filters:
        params["filter"] = ",".join(filters)
    if cursor:
        params["cursor"] = cursor[:400]
    with _client(timeout) as c:
        r = c.get(f"{API}/models", params=params)
        if r.status_code == 429:
            raise HubError("rate_limited", "Hugging Face begrenzt derzeit die Anfragen; bitte später erneut versuchen.", 429)
        r.raise_for_status()
        items = []
        for m in r.json():
            rid = m.get("id") or m.get("modelId") or ""
            if not rid:
                continue
            tags = m.get("tags") or []
            author_name = rid.split("/")[0]
            items.append(
                {
                    "repo": rid,
                    "author": author_name,
                    "name": rid.split("/")[-1],
                    "publisher_status": VERIFIED_PUBLISHERS.get(author_name, "unbekannt / nicht verifiziert"),
                    "likes": m.get("likes"),
                    "downloads": m.get("downloads"),
                    "updated": m.get("lastModified"),
                    "gguf": "gguf" in tags,
                    "gated": m.get("gated", False),
                    "private": bool(m.get("private")),
                    "pipeline_tag": m.get("pipeline_tag"),
                    "library": m.get("library_name"),
                    "family_hints": [t for t in tags if t in ("qwen3", "qwen3_vl", "glm4", "llama", "mistral", "gemma3", "gemma4", "phi3", "deepseek_v3", "conversational")][:5],
                    "status": "unverified",
                }
            )
        nxt = None
        link = r.headers.get("link") or r.headers.get("Link")
        if link:
            m = _LINK_RE.search(link)
            if m:
                cm = _CURSOR_RE.search(m.group(1))
                nxt = cm.group(1) if cm else None
    return DiscoverPage(items=items, next_cursor=nxt, fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), query={"query": q, "author": author, "task": task, "gguf_only": gguf_only, "sort": sort})


def variants(info: RepoInfo) -> list[dict]:
    """Group the GGUF files of a repository: one entry per quantisation (+ projector / split parts)."""
    groups: dict[str, dict] = {}
    for f in info.files:
        name = f.path.split("/")[-1]
        low = name.lower()
        is_proj = "mmproj" in low
        is_draft = any(x in low for x in ("draft", "dflash", "mtp-", "imatrix"))
        key = f.split_base or name
        if is_proj:
            key = "mmproj:" + (f.quantization or "?")
        g = groups.setdefault(key, {"key": key, "quantization": f.quantization, "files": [], "bytes": 0, "kind": "projector" if is_proj else ("auxiliary" if is_draft else "weights"), "parts": 0})
        g["files"].append({"path": f.path, "size": f.size, "sha256": f.sha256, "split_index": f.split_index, "split_total": f.split_total})
        g["bytes"] += f.size
        g["parts"] = len(g["files"])
    out = sorted(groups.values(), key=lambda g: (g["kind"] != "weights", g["bytes"]))
    projectors = [g for g in out if g["kind"] == "projector"]
    for g in out:
        if g["kind"] == "weights":
            g["needs_projector"] = bool(projectors)
            g["projector_options"] = [p["key"] for p in projectors]
    return out


def model_card(repo: str, revision: str, *, timeout: float = 20.0) -> dict:
    """README excerpt (front matter removed, HTML tags stripped, bounded length)."""
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo or ""):
        raise HubError("invalid_repo", "Ungültige Repository-ID.", 422)
    url = f"https://huggingface.co/{repo}/raw/{revision or 'main'}/README.md"
    with _client(timeout) as c:
        r = c.get(url, headers={"Range": f"bytes=0-{CARD_MAX_CHARS * 4}"})
        if r.status_code in (401, 403):
            raise HubError("gated", "Die Modellkarte erfordert eine Freischaltung.", 409)
        if r.status_code == 404:
            return {"repo": repo, "excerpt": "", "front_matter": {}, "truncated": False}
        if r.status_code not in (200, 206):
            raise HubError("http_error", f"HTTP {r.status_code} beim Laden der Modellkarte.", 502)
        text = r.content[: CARD_MAX_CHARS * 4].decode("utf-8", errors="replace")
    fm: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            front = text[3:end]
            body = text[end + 4 :]
            for line in front.splitlines():
                if ":" in line and not line.startswith(" "):
                    k, _, v = line.partition(":")
                    if k.strip() in ("license", "base_model", "pipeline_tag", "library_name", "language", "tags", "license_name", "license_link"):
                        fm[k.strip()] = v.strip()[:200]
    body = _HTML_RE.sub("", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    truncated = len(body) > CARD_MAX_CHARS
    return {"repo": repo, "excerpt": body[:CARD_MAX_CHARS], "front_matter": fm, "truncated": truncated, "url": f"https://huggingface.co/{repo}"}


def repo_detail(repo: str, *, timeout: float = 30.0) -> dict:
    info = repo_info(repo, timeout=timeout)
    v = variants(info)
    return {
        "repo": info.repo,
        "revision": info.revision,
        "author": info.author,
        "publisher_status": VERIFIED_PUBLISHERS.get(info.author or "", "unbekannt / nicht verifiziert"),
        "gated": info.gated,
        "license": info.license,
        "base_model": info.base_model,
        "tags": info.tags[:40],
        "last_modified": info.last_modified,
        "variants": v,
        "total_gguf_bytes": sum(f.size for f in info.files),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
