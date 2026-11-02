"""Runtime launch options derived from the performance mode, the hardware
profile and the model (no user-facing thread/batch knobs).

Modes (settings.performance_mode):
  auto   GPU when a usable device is present and the model fits, threads =
         physical cores (batch threads = logical), normal priority
  quiet  CPU threads halved (min 2), low process priority, GPU still allowed
         (GPU inference loads the CPU less than CPU inference)
  fast   all logical threads, GPU when present, normal priority
GPU (settings.gpu_mode): auto | off.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..hardware import HardwareProfile

GiB = 1024**3


@dataclass
class RuntimeOptions:
    backend: str = "cpu"  # cpu | vulkan | ...
    device: Optional[str] = None
    gpu_layers: Optional[int] = None
    threads: Optional[int] = None
    threads_batch: Optional[int] = None
    prio: int = 0
    reason_de: str = ""

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "device": self.device,
            "gpu_layers": self.gpu_layers,
            "threads": self.threads,
            "threads_batch": self.threads_batch,
            "prio": self.prio,
            "reason_de": self.reason_de,
        }


def estimate_kv_bytes(
    context_tokens: int,
    *,
    n_layers: Optional[int],
    n_kv_heads: Optional[int],
    head_dim: Optional[int],
    bytes_per_value: int = 2,
) -> int:
    """KV cache size = 2 (K and V) * layers * ctx * kv_heads * head_dim * bytes.
    Falls back to a conservative 128 KiB/token when the metadata is missing."""
    if n_layers and n_kv_heads and head_dim:
        return 2 * n_layers * context_tokens * n_kv_heads * head_dim * bytes_per_value
    return context_tokens * 128 * 1024


def plan(
    mode: str,
    gpu_mode: str,
    hw: Optional[HardwareProfile],
    *,
    weights_bytes: int,
    kv_bytes: int,
    gpu_failed_before: bool = False,
) -> RuntimeOptions:
    cores = (hw.cpu.get("cores_physical") if hw else None) or 4
    threads_logical = (hw.cpu.get("threads") if hw else None) or cores
    opts = RuntimeOptions()
    if mode == "quiet":
        opts.threads = max(2, cores // 2)
        opts.threads_batch = max(2, cores // 2)
        opts.prio = -1
    elif mode == "fast":
        opts.threads = threads_logical
        opts.threads_batch = threads_logical
    else:
        opts.threads = cores
        opts.threads_batch = threads_logical
    gpu = hw.best_gpu if hw else None
    if gpu is None or gpu_mode == "off" or gpu_failed_before or not hw or gpu.backend not in hw.runtime_variants:
        opts.backend = "cpu"
        opts.reason_de = (
            "GPU deaktiviert."
            if gpu_mode == "off"
            else "GPU-Start ist zuvor fehlgeschlagen; CPU wird verwendet."
            if gpu_failed_before
            else "Keine nutzbare GPU erkannt; CPU wird verwendet."
        )
        return opts
    need = int(weights_bytes * 1.08) + kv_bytes + 256 * 2**20
    free = gpu.vram_free_bytes
    opts.backend = gpu.backend
    opts.device = gpu.device
    if need <= free:
        opts.gpu_layers = 999
        opts.reason_de = f"Alle Schichten auf {gpu.name} (geschätzt {need / GiB:.1f} GB von {free / GiB:.1f} GB frei)."
    else:
        frac = max(0.0, (free - kv_bytes - 256 * 2**20) / max(weights_bytes * 1.08, 1))
        if frac < 0.25:
            opts.backend = "cpu"
            opts.device = None
            opts.reason_de = (
                f"Modell passt nicht ausreichend in den Grafikspeicher ({free / GiB:.1f} GB frei); CPU wird verwendet."
            )
        else:
            # layer count unknown here; the manager converts the fraction to layers once the GGUF layer count is known
            opts.gpu_layers = -int(frac * 100)  # negative = percentage marker, resolved by the manager
            opts.reason_de = f"Teilweise auf {gpu.name} ({int(frac * 100)} % der Schichten), Rest im RAM."
    return opts
