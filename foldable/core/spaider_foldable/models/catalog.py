"""Model catalog: pinned Hugging Face revisions with sizes, checksums and
licenses, plus an honest per-machine fit estimate."""

from __future__ import annotations

import json
from importlib import resources
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..sysinfo import HostInfo

GiB = 1024**3


class CatalogFile(BaseModel):
    path: str
    size: int
    sha256: str


class License(BaseModel):
    id: str
    name: str
    url: str


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
    support: Literal["executed", "documented"] = "documented"
    description_de: str = ""

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def repo_url(self) -> str:
        return f"https://huggingface.co/{self.repo}/tree/{self.revision}"

    def estimated_ram_bytes(self, context_tokens: int = 8192) -> int:
        """Rough estimate: weights are memory-mapped (count fully), plus KV cache
        and compute buffers. KV cache for Qwen3-class models at 8k context is a
        few hundred MB; scale by context."""
        kv = int(0.35 * GiB * (context_tokens / 8192))
        return self.total_bytes + kv + int(0.5 * GiB)

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
                message_de="Eine Modelldatei ist größer als 4 GB; das Dateisystem des Datenträgers (FAT32) kann sie nicht speichern.",  # noqa: E501
                estimated_ram_bytes=need,
            )
        if not installed and self.total_bytes + 1 * GiB > host.disk_free_bytes:
            return Fit(
                level="too_large",
                message_de=f"Nicht genug freier Speicher auf dem Datenträger (benötigt etwa {self.total_bytes / GiB:.1f} GB).",  # noqa: E501
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
                message_de=f"Passt nur knapp: etwa {need / GiB:.1f} GB RAM nötig, derzeit {host.ram_available_bytes / GiB:.1f} GB frei.",  # noqa: E501
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

    def get(self, catalog_id: str) -> CatalogEntry | None:
        for e in self.entries:
            if e.id == catalog_id:
                return e
        return None


def load_catalog() -> Catalog:
    raw = (resources.files("spaider_foldable.models") / "catalog.json").read_text(encoding="utf-8")
    return Catalog.model_validate(json.loads(raw))
