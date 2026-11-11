"""Builds tiny synthetic GGUF headers for validation tests (no tensors)."""

from __future__ import annotations

import struct
from pathlib import Path


def _s(text: str) -> bytes:
    b = text.encode("utf-8")
    return struct.pack("<Q", len(b)) + b


def _kv_string(key: str, value: str) -> bytes:
    return _s(key) + struct.pack("<I", 8) + _s(value)


def _kv_u32(key: str, value: int) -> bytes:
    return _s(key) + struct.pack("<I", 4) + struct.pack("<I", value)


def _kv_str_array(key: str, values: list[str]) -> bytes:
    body = struct.pack("<I", 8) + struct.pack("<Q", len(values)) + b"".join(_s(v) for v in values)
    return _s(key) + struct.pack("<I", 9) + body


def write_fake_gguf(
    path: Path,
    *,
    architecture: str = "qwen3",
    name: str = "Fake Qwen3",
    file_type: int = 7,
    context_length: int = 4096,
    pooling_type: int | None = None,
    extra_bytes: int = 0,
) -> Path:
    kvs = [
        _kv_string("general.architecture", architecture),
        _kv_string("general.name", name),
        _kv_u32("general.file_type", file_type),
        _kv_u32(f"{architecture}.context_length", context_length),
        _kv_u32(f"{architecture}.embedding_length", 1024),
        _kv_str_array("tokenizer.ggml.tokens", ["<s>", "</s>", "hallo", "welt"]),
        _kv_string("tokenizer.chat_template", "{{ messages }}"),
    ]
    if pooling_type is not None:
        kvs.append(_kv_u32(f"{architecture}.pooling_type", pooling_type))
    header = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(kvs)) + b"".join(kvs)
    path.write_bytes(header + b"\x00" * extra_bytes)
    return path
