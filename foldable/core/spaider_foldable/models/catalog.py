"""Model catalog (schema v2): pinned Hugging Face revisions with sizes,
checksums, licenses, verification status, evidence and resource estimates,
plus an honest per-machine fit estimate.

Sources merged at load time: the built-in ``catalog.json``, the user file
``data/catalog.local.json`` (same schema; extend without a new app version) and
dynamic entries resolved from the hub (stored in the database)."""

from __future__ import annotations

import json
import logging
from importlib import resources
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..sysinfo import HostInfo

logger = logging.getLogger(__name__)
GiB = 1024**3


class CatalogFile(BaseModel):
    path: str
    size: int
    sha256: Optional[str] = None


class License(BaseModel):
    id: str
    name: str
    url: str
    kind: Literal["open-source", "open-weights", "other"] = "other"
    gated: bool = False
    requires_account: bool = False
    note_de: str = ""


class Verification(BaseModel):
    status: Literal["executed", "metadata", "unverified"] = "unverified"
    date: Optional[str] = None
    scope: str = ""


class Fit(BaseModel):
    level: Literal["recommended", "ok", "tight", "too_large", "unknown"]
    message_de: str
    estimated_ram_bytes: int = 0


class CatalogEntry(BaseModel):
    id: str
    name: str
    role: Literal["chat", "embedding"]
    family: str
    publisher: str
    source: Literal["official", "community"]
    repo: str
    revision: str
    files: list[CatalogFile]
    license: License
    architecture: str
    parameters: str
    quantization: str
    context_length: int
    embedding_dimensions: Optional[int] = None
    support: Literal["executed", "documented"] = "documented"  # kept for v1 compatibility
    description_de: str = ""
    # v2
    type: Literal["chat", "embedding", "base", "vision", "audio"] = "chat"
    original_model: Optional[str] = None
    converter: Optional[str] = None
    families: list[str] = Field(default_factory=list)
    verification: Verification = Field(default_factory=Verification)
    evidence: list[dict] = Field(default_factory=list)
    resources: dict = Field(default_factory=dict)  # n_layers, n_kv_heads, head_dim
    template: dict = Field(default_factory=dict)  # reasoning_toggle, system_role
    published: Optional[str] = None
    checked_at: Optional[str] = None
    source_kind: Literal["builtin", "local", "search", "user"] = "builtin"

    def model_post_init(self, __context) -> None:  # noqa: D401
        if self.verification.status == "unverified" and self.source_kind == "builtin":
            self.verification = Verification(
                status="executed" if self.support == "executed" else "metadata",
                date=self.checked_at,
                scope="Katalogeintrag",
            )
        if self.role == "embedding" and self.type == "chat":
            self.type = "embedding"

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def repo_url(self) -> str:
        return f"https://huggingface.co/{self.repo}/tree/{self.revision}"

    def estimated_ram_bytes(self, context_tokens: int = 8192) -> int:
        """Weights (memory-mapped, counted fully) + KV cache + working buffers.
        KV from metadata when known, else a conservative 128 KiB/token."""
        from ..inference.options import estimate_kv_bytes

        kv = estimate_kv_bytes(
            context_tokens,
            n_layers=self.resources.get("n_layers"),
            n_kv_heads=self.resources.get("n_kv_heads"),
            head_dim=self.resources.get("head_dim"),
        )
        return self.total_bytes + kv + int(0.6 * GiB)

    def min_ram_bytes(self) -> int:
        return self.estimated_ram_bytes(4096)

    def recommended_ram_bytes(self) -> int:
        return int(self.estimated_ram_bytes(8192) * 1.5)

    def fit_for(self, host: HostInfo, *, installed: bool, context_tokens: int = 8192) -> Fit:
        need = self.estimated_ram_bytes(context_tokens)
        if host.ram_total_bytes <= 0:
            return Fit(
                level="unknown", message_de="Arbeitsspeicher konnte nicht ermittelt werden.", estimated_ram_bytes=need
            )
        if host.max_file_bytes is not None and any(f.size > host.max_file_bytes for f in self.files):
            return Fit(
                level="too_large",
                message_de=(
                    "Eine Modelldatei ist größer als 4 GB; das Dateisystem des Datenträgers (FAT32) kann sie "
                    "nicht speichern."
                ),
                estimated_ram_bytes=need,
            )
        if not installed and self.total_bytes + 1 * GiB > host.disk_free_bytes:
            return Fit(
                level="too_large",
                message_de=(
                    f"Nicht genug freier Speicher auf dem Datenträger (benötigt etwa {self.total_bytes / GiB:.1f} GB)."
                ),
                estimated_ram_bytes=need,
            )
        if need > host.ram_total_bytes * 0.92:
            return Fit(
                level="too_large",
                message_de=f"Benötigt etwa {need / GiB:.1f} GB RAM, dieser Rechner hat {host.ram_total_bytes / GiB:.0f} GB.",  # noqa: E501
                estimated_ram_bytes=need,
            )
        if need > host.ram_available_bytes:
            return Fit(
                level="tight",
                message_de=(
                    f"Passt nur knapp: etwa {need / GiB:.1f} GB RAM nötig, derzeit "
                    f"{host.ram_available_bytes / GiB:.1f} GB frei."
                ),
                estimated_ram_bytes=need,
            )
        if need <= host.ram_total_bytes * 0.35:
            return Fit(
                level="recommended", message_de=f"Passt gut (etwa {need / GiB:.1f} GB RAM).", estimated_ram_bytes=need
            )
        return Fit(level="ok", message_de=f"Passt (etwa {need / GiB:.1f} GB RAM).", estimated_ram_bytes=need)

    def to_api(self, host: HostInfo, installed_model_id: Optional[str], context_tokens: int = 8192) -> dict:
        data = self.model_dump()
        data["total_bytes"] = self.total_bytes
        data["repo_url"] = self.repo_url
        data["min_ram_bytes"] = self.min_ram_bytes()
        data["recommended_ram_bytes"] = self.recommended_ram_bytes()
        data["fit"] = self.fit_for(
            host, installed=installed_model_id is not None, context_tokens=context_tokens
        ).model_dump()
        data["installed_model_id"] = installed_model_id
        return data


class Catalog(BaseModel):
    version: int
    generated: str
    notes: list[str] = Field(default_factory=list)
    entries: list[CatalogEntry]
    local_extensions: int = 0

    def get(self, catalog_id: str) -> CatalogEntry | None:
        for e in self.entries:
            if e.id == catalog_id:
                return e
        return None

    def add(self, entry: CatalogEntry) -> None:
        self.entries = [e for e in self.entries if e.id != entry.id] + [entry]


def load_catalog(local_path: Optional[Path] = None) -> Catalog:
    raw = (resources.files("spaider_foldable.models") / "catalog.json").read_text(encoding="utf-8")
    cat = Catalog.model_validate(json.loads(raw))
    if local_path and Path(local_path).exists():
        try:
            extra = json.loads(Path(local_path).read_text(encoding="utf-8"))
            for e in extra.get("entries", []):
                e.setdefault("source_kind", "local")
                e.setdefault("checked_at", extra.get("generated"))
                cat.add(CatalogEntry.model_validate(e))
                cat.local_extensions += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog.local.json ignored: %s", exc)
    return cat
