"""Two-stage model recommendation for this computer.

Stage 1 (hard filter): type must be chat, license accessible without an account
for the default flow (gated entries are listed under "excluded" with the
reason), architecture supported by the bundled runtime, complete file list,
estimated memory (weights + KV cache + working buffers + OS reserve) must fit
RAM (or VRAM+RAM for GPU offload), download must fit the free disk.

Stage 2 (scoring): local measurements (only real ones), verification status,
German-language evidence, size-class preference per focus, resource cost.

Up to three picks: balanced, fast (smallest resource cost among fitting
candidates), quality (largest fitting model with evidence). Reasons are
explicit; estimates are labelled as estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..hardware import HardwareProfile
from ..inference.options import estimate_kv_bytes
from .catalog import CatalogEntry

GiB = 1024**3
OS_RESERVE = 3 * GiB
WORKING_BUFFERS = int(0.6 * GiB)
EMBEDDING_SHARE = int(0.9 * GiB)  # embedding runtime when enabled


@dataclass
class Estimate:
    ram_bytes: int
    vram_bytes: int
    download_bytes: int
    context_tokens: int
    kv_bytes: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def estimate(entry: CatalogEntry, context_tokens: int, *, use_gpu: bool) -> Estimate:
    res = entry.resources or {}
    kv = estimate_kv_bytes(
        context_tokens,
        n_layers=res.get("n_layers"),
        n_kv_heads=res.get("n_kv_heads"),
        head_dim=res.get("head_dim"),
    )
    weights = entry.total_bytes
    if use_gpu:
        return Estimate(
            ram_bytes=int(0.3 * weights) + WORKING_BUFFERS,
            vram_bytes=int(1.08 * weights) + kv + WORKING_BUFFERS,
            download_bytes=weights,
            context_tokens=context_tokens,
            kv_bytes=kv,
        )
    return Estimate(
        ram_bytes=int(1.05 * weights) + kv + WORKING_BUFFERS,
        vram_bytes=0,
        download_bytes=weights,
        context_tokens=context_tokens,
        kv_bytes=kv,
    )


def hard_filter(
    entry: CatalogEntry, hw: HardwareProfile, *, context_tokens: int, installed: bool, embedding_enabled: bool
) -> tuple[Optional[Estimate], Optional[str], bool]:
    """Returns (estimate, exclusion reason, uses_gpu)."""
    if entry.type != "chat" or entry.role != "chat":
        return None, "Kein Chatmodell.", False
    if entry.license.gated or entry.license.requires_account:
        return None, "Download erfordert ein Konto oder eine Freischaltung (manueller Import möglich).", False
    if entry.verification.status == "unverified":
        return None, "Kompatibilität nicht geprüft.", False
    if hw.max_file_bytes is not None and any(f.size > hw.max_file_bytes for f in entry.files):
        return None, "Datei größer als 4 GB; nicht auf FAT32 speicherbar.", False
    if not installed and entry.total_bytes + GiB > hw.disk_free_bytes:
        return None, f"Nicht genug freier Speicher ({entry.total_bytes / GiB:.1f} GB nötig).", False
    gpu = hw.best_gpu
    avail_ram = hw.ram_total_bytes - OS_RESERVE - (EMBEDDING_SHARE if embedding_enabled else 0)
    if gpu is not None:
        est = estimate(entry, context_tokens, use_gpu=True)
        if est.vram_bytes <= gpu.vram_free_bytes and est.ram_bytes <= avail_ram:
            return est, None, True
    est = estimate(entry, context_tokens, use_gpu=False)
    if est.ram_bytes > avail_ram:
        return (
            None,
            (
                f"Benötigt etwa {est.ram_bytes / GiB:.1f} GB RAM (Gewichte + Kontextcache + Reserve), "
                f"verfügbar etwa {max(avail_ram, 0) / GiB:.1f} GB."
            ),
            False,
        )
    return est, None, False


def _size_class(entry: CatalogEntry) -> float:
    """Billions of parameters from the catalog string (e.g. '1.7B'), 0 if unknown."""
    try:
        return float(str(entry.parameters).upper().rstrip("B").replace(",", "."))
    except ValueError:
        return 0.0


def score(
    entry: CatalogEntry, est: Estimate, hw: HardwareProfile, focus: str, measured: Optional[dict], uses_gpu: bool
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    s = 0.0
    params = _size_class(entry)
    # quality proxy: size class, capped; focus adjusts the sweet spot
    target = {"alltag": 4.0, "dokumente": 8.0, "coding": 8.0}.get(focus, 4.0)
    s += 10.0 - min(abs(params - target), 10.0)
    if entry.verification.status == "executed":
        s += 3.0
        reasons.append("Auf dieser Runtime bereits mit echter Inferenz getestet.")
    else:
        reasons.append("Nach Metadaten geprüft (Architektur, Prüfsumme, Lizenz), hier noch nicht ausgeführt.")
    for ev in entry.evidence:
        if ev.get("kind") == "model_card" and "deutsch" in (ev.get("note_de") or "").lower():
            s += 1.0
            reasons.append("Modellkarte nennt Deutsch als unterstützte Sprache.")
            break
    if measured and measured.get("tokens_per_second"):
        tps = float(measured["tokens_per_second"])
        s += min(tps / 10.0, 4.0)
        reasons.append(f"Gemessen auf diesem Rechner: {tps:.0f} Token/s ({measured.get('backend')}).")
    else:
        reasons.append("Geschwindigkeit hier noch nicht gemessen.")
    if uses_gpu:
        s += 1.5
        reasons.append(f"Passt vollständig in den Grafikspeicher ({hw.best_gpu.name if hw.best_gpu else 'GPU'}).")
    cost = est.vram_bytes if uses_gpu else est.ram_bytes
    s -= cost / (8 * GiB)
    if entry.license.kind == "open-source":
        s += 0.5
    if focus == "coding" and any("cod" in (e.get("note_de") or "").lower() for e in entry.evidence):
        s += 1.0
        reasons.append("Belege für Code-Fragen in der Modellkarte.")
    return s, reasons


def recommend(
    entries: list[CatalogEntry],
    hw: HardwareProfile,
    *,
    focus: str,
    context_tokens: int,
    installed_ids: dict[str, str],
    measured_for,
    embedding_enabled: bool,
) -> dict:
    picks: list[dict] = []
    excluded: list[dict] = []
    scored: list[tuple[float, CatalogEntry, Estimate, list[str], bool]] = []
    for e in entries:
        est, reason, uses_gpu = hard_filter(
            e, hw, context_tokens=context_tokens, installed=e.id in installed_ids, embedding_enabled=embedding_enabled
        )
        if est is None:
            excluded.append({"id": e.id, "name": e.name, "reason_de": reason})
            continue
        sc, reasons = score(e, est, hw, focus, measured_for(e), uses_gpu)
        scored.append((sc, e, est, reasons, uses_gpu))
    scored.sort(key=lambda t: -t[0])
    used: set[str] = set()

    def take(slot: str, cand, extra: str):
        sc, e, est, reasons, uses_gpu = cand
        used.add(e.id)
        picks.append(
            {
                "slot": slot,
                "entry": e,
                "reasons_de": [extra] + reasons,
                "estimate": est.to_dict(),
                "uses_gpu": uses_gpu,
                "measured": measured_for(e),
                "installed_model_id": installed_ids.get(e.id),
            }
        )

    if scored:
        take("balanced", scored[0], "Beste Gesamtbewertung für diesen Rechner und Schwerpunkt.")
    fast = sorted(
        (c for c in scored if c[1].id not in used), key=lambda c: (c[2].vram_bytes if c[4] else c[2].ram_bytes, -c[0])
    )
    if fast:
        take("fast", fast[0], "Geringster Ressourcenbedarf unter den passenden Modellen.")
    quality = sorted((c for c in scored if c[1].id not in used), key=lambda c: (-_size_class(c[1]), -c[0]))
    if quality and _size_class(quality[0][1]) > _size_class(picks[0]["entry"]):
        take("quality", quality[0], "Größtes passendes Modell; mehr Qualität bei längerer Antwortzeit.")
    return {"picks": picks[:3], "excluded": excluded}
