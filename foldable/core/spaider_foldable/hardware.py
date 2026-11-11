"""Hardware profile for model recommendations and runtime selection.

Detects, without admin rights or drivers: CPU name and physical/logical cores,
RAM, GPUs usable by the bundled Vulkan runtime (``llama-server --list-devices``
of the Vulkan build, which reports total/free VRAM), GPU driver versions (WMI),
free disk space and power state. The profile is cached per fingerprint in
``hardware_profiles``; GPU detection runs once per core start (~1 s).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .sysinfo import host_info

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

logger = logging.getLogger(__name__)

_DEVICE_RE = re.compile(
    r"^\s*(Vulkan\d+|CUDA\d+|SYCL\d+|ROCm\d+|OpenCL\d+):\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)\s*$"
)


@dataclass
class GpuInfo:
    name: str
    backend: str
    device: str
    vram_total_bytes: int
    vram_free_bytes: int
    driver: Optional[str] = None
    usable: bool = True
    note_de: str = ""


@dataclass
class HardwareProfile:
    cpu: dict
    ram_total_bytes: int
    ram_available_bytes: int
    gpus: list[GpuInfo]
    disk_free_bytes: int
    disk_total_bytes: int
    filesystem: str
    max_file_bytes: Optional[int]
    power: dict
    fingerprint: str
    runtime_variants: list[str]
    active_backend: str = "cpu"
    detected_at: str = ""
    notes_de: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @property
    def best_gpu(self) -> Optional[GpuInfo]:
        usable = [g for g in self.gpus if g.usable]
        return max(usable, key=lambda g: g.vram_free_bytes) if usable else None


def _cpu_flags() -> list[str]:
    flags: list[str] = []
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # PF_* constants: AVX2 = 40, AVX512F = 41 (IsProcessorFeaturePresent)
            for pf, name in ((40, "avx2"), (41, "avx512f"), (10, "sse2"), (36, "avx")):
                try:
                    if kernel32.IsProcessorFeaturePresent(pf):
                        flags.append(name)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
    return flags


def _wmi_gpu_drivers() -> dict[str, str]:
    if sys.platform != "win32":
        return {}
    try:
        out = (
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion | ConvertTo-Json -Compress",  # noqa: E501
                ],
                capture_output=True,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            .stdout.decode("utf-8", errors="replace")
            .strip()
        )
        data = json.loads(out) if out else []
        if isinstance(data, dict):
            data = [data]
        return {str(d.get("Name") or ""): str(d.get("DriverVersion") or "") for d in data}
    except Exception as exc:  # noqa: BLE001
        logger.debug("WMI GPU query failed: %s", exc)
        return {}


def list_runtime_devices(runtime_dir: Path, timeout: float = 20.0) -> list[GpuInfo]:
    """Run ``llama-server --list-devices`` for a runtime variant and parse the devices."""
    exe = runtime_dir / ("llama-server.exe" if sys.platform == "win32" else "llama-server")
    if not exe.exists():
        return []
    try:
        out = subprocess.run(
            [str(exe), "--list-devices"],
            capture_output=True,
            timeout=timeout,
            cwd=str(runtime_dir),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = (out.stdout + out.stderr).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.warning("device listing failed for %s: %s", runtime_dir, exc)
        return []
    gpus: list[GpuInfo] = []
    for line in text.splitlines():
        m = _DEVICE_RE.match(line)
        if not m:
            continue
        dev, name, total, free = m.groups()
        backend = re.sub(r"\d+$", "", dev).lower()
        gpus.append(
            GpuInfo(
                name=name,
                backend=backend,
                device=dev,
                vram_total_bytes=int(total) * 2**20,
                vram_free_bytes=int(free) * 2**20,
            )
        )
    return gpus


def detect(data_dir: Path, runtime_dirs: dict[str, Path]) -> HardwareProfile:
    host = host_info(data_dir)
    cores_physical = None
    if psutil:
        try:
            cores_physical = psutil.cpu_count(logical=False)
        except Exception:  # noqa: BLE001
            cores_physical = None
    cpu = {
        "name": host.cpu,
        "cores_physical": cores_physical or host.threads,
        "threads": host.threads,
        "features": _cpu_flags(),
    }
    gpus: list[GpuInfo] = []
    variants = sorted(k for k, p in runtime_dirs.items() if p.exists())
    notes: list[str] = []
    drivers = _wmi_gpu_drivers()
    for variant, rdir in runtime_dirs.items():
        if variant == "cpu" or not rdir.exists():
            continue
        found = list_runtime_devices(rdir)
        for g in found:
            for name, drv in drivers.items():
                if name and (name in g.name or g.name in name):
                    g.driver = drv
            if g.vram_free_bytes < 1 << 30:
                g.usable = False
                g.note_de = "Weniger als 1 GB freier Grafikspeicher."
            gpus.append(g)
        if not found:
            notes.append(f"Runtime-Variante {variant}: keine Geräte gemeldet.")
    power = {"on_battery": None, "power_saver": None}
    if psutil:
        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                power["on_battery"] = not bat.power_plugged
        except Exception:  # noqa: BLE001
            pass
    fp_src = f"{host.cpu}|{cpu['cores_physical']}|{host.threads}|{round(host.ram_total_bytes / 2**30)}|" + "|".join(
        sorted(g.name for g in gpus)
    )
    fingerprint = hashlib.sha256(fp_src.encode("utf-8")).hexdigest()[:16]
    return HardwareProfile(
        cpu=cpu,
        ram_total_bytes=host.ram_total_bytes,
        ram_available_bytes=host.ram_available_bytes,
        gpus=gpus,
        disk_free_bytes=host.disk_free_bytes,
        disk_total_bytes=host.disk_total_bytes,
        filesystem=host.filesystem,
        max_file_bytes=host.max_file_bytes,
        power=power,
        fingerprint=fingerprint,
        runtime_variants=variants,
        detected_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        notes_de=notes,
    )


def refresh_dynamic(profile: HardwareProfile, data_dir: Path) -> HardwareProfile:
    """Update the cheap, volatile parts (available RAM, free disk) without re-running GPU detection."""
    host = host_info(data_dir)
    profile.ram_available_bytes = host.ram_available_bytes
    profile.disk_free_bytes = host.disk_free_bytes
    return profile


def profile_from_dict(d: dict) -> HardwareProfile:
    gpus = [GpuInfo(**g) for g in d.get("gpus", [])]
    return HardwareProfile(**{**d, "gpus": gpus})
