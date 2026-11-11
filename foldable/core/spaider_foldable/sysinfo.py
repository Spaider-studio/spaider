"""Host facts needed for honest model recommendations: RAM, disk, filesystem
limits and CPU. Pure stdlib + ctypes; no admin rights required."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import socket
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

FAT32_MAX_FILE = 4_294_967_295


@dataclass
class HostInfo:
    os: str
    arch: str
    hostname: str
    cpu: str
    threads: int
    ram_total_bytes: int
    ram_available_bytes: int
    disk_total_bytes: int
    disk_free_bytes: int
    filesystem: str
    max_file_bytes: int | None

    def to_dict(self) -> dict:
        return asdict(self)


def _memory() -> tuple[int, int]:
    if sys.platform == "win32":

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
            return int(stat.ullTotalPhys), int(stat.ullAvailPhys)
        return 0, 0
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        avail = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
        return int(total), int(avail)
    except (ValueError, OSError, AttributeError):
        return 0, 0


def _filesystem(path: Path) -> str:
    if sys.platform == "win32":
        root = os.path.splitdrive(str(path.resolve()))[0] + "\\"
        if not os.path.splitdrive(str(path.resolve()))[0]:
            # UNC path: use the full share root
            parts = path.resolve().parts
            root = parts[0] if parts else str(path)
        buf = ctypes.create_unicode_buffer(64)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(root), None, 0, None, None, None, buf, ctypes.sizeof(buf)
        )
        return buf.value if ok else "unknown"
    return "unknown"


def _cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return str(value).strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def host_info(data_dir: Path) -> HostInfo:
    total, avail = _memory()
    try:
        usage = shutil.disk_usage(data_dir if data_dir.exists() else data_dir.parent)
        disk_total, disk_free = usage.total, usage.free
    except OSError:
        disk_total, disk_free = 0, 0
    fs = _filesystem(data_dir if data_dir.exists() else data_dir.parent)
    max_file = FAT32_MAX_FILE if fs.upper() == "FAT32" else None
    return HostInfo(
        os="windows" if sys.platform == "win32" else sys.platform,
        arch=platform.machine().lower().replace("amd64", "x64").replace("x86_64", "x64"),
        hostname=socket.gethostname(),
        cpu=_cpu_name(),
        threads=os.cpu_count() or 1,
        ram_total_bytes=total,
        ram_available_bytes=avail,
        disk_total_bytes=disk_total,
        disk_free_bytes=disk_free,
        filesystem=fs,
        max_file_bytes=max_file,
    )


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total
