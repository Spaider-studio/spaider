"""Robust server-sent-events / NDJSON reader over an httpx streaming response.

Handles: ``data:`` lines (with or without a space), multi-line ``data:`` blocks,
``event:`` names, comment/heartbeat lines (``: keep-alive``), blank separators,
``[DONE]`` sentinels, NDJSON (one JSON object per line without any prefix),
fragmented UTF-8 sequences (incremental decoder over raw bytes) and CRLF.

Yields ``(event_name, data_text)`` tuples; ``event_name`` is ``""`` when the
block had no ``event:`` line. The caller parses ``data_text`` (JSON or not).
"""

from __future__ import annotations

import codecs
from typing import AsyncIterator, Optional

import httpx


async def iter_sse(resp: httpx.Response, *, max_line_bytes: int = 4 * 1024 * 1024) -> AsyncIterator[tuple[str, str]]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    event: str = ""
    data_lines: list[str] = []
    async for chunk in resp.aiter_bytes():
        buf += decoder.decode(chunk)
        while True:
            nl = buf.find("\n")
            if nl < 0:
                if len(buf) > max_line_bytes:
                    raise ValueError("SSE line too long")
                break
            line = buf[:nl]
            buf = buf[nl + 1 :]
            if line.endswith("\r"):
                line = line[:-1]
            if line == "":
                if data_lines:
                    yield event, "\n".join(data_lines)
                event, data_lines = "", []
                continue
            if line.startswith(":"):
                continue  # comment / heartbeat
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
                continue
            if line.startswith(("id:", "retry:")):
                continue
            # NDJSON or a bare line: treat it as a complete data block
            stripped = line.strip()
            if stripped:
                if data_lines:
                    yield event, "\n".join(data_lines)
                    event, data_lines = "", []
                yield "", stripped
    tail = decoder.decode(b"", final=True)
    if tail:
        buf += tail
    rest = buf.strip("\r\n")
    if rest:
        if rest.startswith("data:"):
            data_lines.append(rest[5:].lstrip(" "))
        elif not rest.startswith(":") and not rest.startswith("event:"):
            data_lines.append(rest)
    if data_lines:
        yield event, "\n".join(data_lines)


def is_done_sentinel(data: Optional[str]) -> bool:
    return (data or "").strip() == "[DONE]"
