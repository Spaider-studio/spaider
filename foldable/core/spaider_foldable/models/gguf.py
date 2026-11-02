"""Minimal GGUF header reader for validation (no tensor data is read).

Spec: https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
Reads magic, version, tensor/kv counts and the key/value metadata. Only the
metadata is needed to decide: is this a GGUF file at all, which architecture,
what context length, is it an embedding model, which quantisation.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, BinaryIO

GGUF_MAGIC = b"GGUF"

# general.file_type -> quantisation name (llama_ftype enum)
FILE_TYPES: dict[int, str] = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    7: "Q8_0",
    8: "Q5_0",
    9: "Q5_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    21: "Q2_K_S",
    22: "IQ3_XS",
    23: "IQ3_XXS",
    24: "IQ1_S",
    25: "IQ4_NL",
    26: "IQ3_S",
    27: "IQ3_M",
    28: "IQ2_S",
    29: "IQ2_M",
    30: "IQ4_XS",
    31: "IQ1_M",
    32: "BF16",
    36: "TQ1_0",
    37: "TQ2_0",
    38: "MXFP4",
}

(
    _T_UINT8,
    _T_INT8,
    _T_UINT16,
    _T_INT16,
    _T_UINT32,
    _T_INT32,
    _T_FLOAT32,
    _T_BOOL,
    _T_STRING,
    _T_ARRAY,
    _T_UINT64,
    _T_INT64,
    _T_FLOAT64,
) = range(13)

_SCALAR_FMT = {
    _T_UINT8: "<B",
    _T_INT8: "<b",
    _T_UINT16: "<H",
    _T_INT16: "<h",
    _T_UINT32: "<I",
    _T_INT32: "<i",
    _T_FLOAT32: "<f",
    _T_BOOL: "<?",
    _T_UINT64: "<Q",
    _T_INT64: "<q",
    _T_FLOAT64: "<d",
}

# Keys worth keeping. Everything else (notably the tokenizer vocabulary arrays)
# is skipped to keep memory small.
_KEEP_PREFIXES = ("general.", "tokenizer.chat_template")
_KEEP_SUFFIXES = (
    ".context_length",
    ".embedding_length",
    ".block_count",
    ".pooling_type",
    ".vocab_size",
    ".attention.head_count",
    ".expert_count",
)
_MAX_STRING_KEEP = 20_000


class GGUFError(ValueError):
    pass


@dataclass
class GGUFInfo:
    path: Path
    version: int
    tensor_count: int
    kv_count: int
    architecture: str
    name: str
    file_type: int | None
    quantization: str
    context_length: int | None
    embedding_length: int | None
    pooling_type: int | None
    has_chat_template: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_embedding_model(self) -> bool:
        if self.pooling_type is not None and self.pooling_type != 0:
            return True
        lower = (self.name or "").lower()
        return "embedding" in lower or "embed" in lower and "qwen" in lower


def _read(fh: BinaryIO, n: int) -> bytes:
    data = fh.read(n)
    if len(data) != n:
        raise GGUFError("unexpected end of file while reading GGUF header")
    return data


def _read_string(fh: BinaryIO, keep: bool) -> str | None:
    (length,) = struct.unpack("<Q", _read(fh, 8))
    if length > 1 << 31:
        raise GGUFError("implausible string length in GGUF header")
    if keep and length <= _MAX_STRING_KEEP:
        return _read(fh, length).decode("utf-8", errors="replace")
    fh.seek(length, 1)
    return None


def _read_value(fh: BinaryIO, vtype: int, keep: bool) -> Any:
    if vtype in _SCALAR_FMT:
        fmt = _SCALAR_FMT[vtype]
        raw = _read(fh, struct.calcsize(fmt))
        return struct.unpack(fmt, raw)[0]
    if vtype == _T_STRING:
        return _read_string(fh, keep)
    if vtype == _T_ARRAY:
        (etype,) = struct.unpack("<I", _read(fh, 4))
        (count,) = struct.unpack("<Q", _read(fh, 8))
        if count > 1 << 32:
            raise GGUFError("implausible array length in GGUF header")
        if etype in _SCALAR_FMT:
            size = struct.calcsize(_SCALAR_FMT[etype])
            if keep and count <= 64:
                raw = _read(fh, size * count)
                return list(struct.unpack("<" + _SCALAR_FMT[etype][1] * count, raw))
            fh.seek(size * count, 1)
            return f"<array {count}>"
        if etype == _T_STRING:
            for _ in range(count):
                _read_string(fh, False)
            return f"<array {count} strings>"
        if etype == _T_ARRAY:
            for _ in range(count):
                _read_value(fh, _T_ARRAY, False)
            return f"<nested array {count}>"
        raise GGUFError(f"unknown GGUF array element type {etype}")
    raise GGUFError(f"unknown GGUF value type {vtype}")


def read_gguf_info(path: str | Path) -> GGUFInfo:
    p = Path(path)
    with open(p, "rb", buffering=1 << 20) as fh:
        if _read(fh, 4) != GGUF_MAGIC:
            raise GGUFError("Keine GGUF-Datei (Magic-Bytes fehlen).")
        (version,) = struct.unpack("<I", _read(fh, 4))
        if version < 2 or version > 3:
            raise GGUFError(f"Nicht unterstützte GGUF-Version {version} (erwartet 2 oder 3).")
        (tensor_count,) = struct.unpack("<Q", _read(fh, 8))
        (kv_count,) = struct.unpack("<Q", _read(fh, 8))
        if kv_count > 100_000 or tensor_count > 10_000_000:
            raise GGUFError("Unplausible GGUF-Kopfdaten.")
        meta: dict[str, Any] = {}
        for _ in range(kv_count):
            key = _read_string(fh, True) or ""
            (vtype,) = struct.unpack("<I", _read(fh, 4))
            keep = key.startswith(_KEEP_PREFIXES) or key.endswith(_KEEP_SUFFIXES)
            value = _read_value(fh, vtype, keep)
            if keep and value is not None:
                meta[key] = value
    arch = str(meta.get("general.architecture") or "")
    if not arch:
        raise GGUFError("GGUF ohne general.architecture.")
    file_type = meta.get("general.file_type")
    quant = FILE_TYPES.get(int(file_type), f"ftype_{file_type}") if isinstance(file_type, int) else "unknown"
    return GGUFInfo(
        path=p,
        version=version,
        tensor_count=tensor_count,
        kv_count=kv_count,
        architecture=arch,
        name=str(meta.get("general.name") or p.stem),
        file_type=file_type if isinstance(file_type, int) else None,
        quantization=quant,
        context_length=_int_or_none(meta.get(f"{arch}.context_length")),
        embedding_length=_int_or_none(meta.get(f"{arch}.embedding_length")),
        pooling_type=_int_or_none(meta.get(f"{arch}.pooling_type")),
        has_chat_template=bool(meta.get("tokenizer.chat_template")),
        metadata={k: v for k, v in meta.items() if k != "tokenizer.chat_template"},
    )


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def supported_architectures() -> dict[str, Any]:
    data = (resources.files("spaider_foldable.models") / "supported_architectures.json").read_text(encoding="utf-8")
    return json.loads(data)


def is_architecture_supported(arch: str) -> bool:
    return arch in set(supported_architectures()["architectures"])
