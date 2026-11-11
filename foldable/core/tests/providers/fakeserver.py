"""Deterministic local HTTP test servers for the provider contract tests.

A ``FakeProvider`` serves scripted responses: status code + body, an SSE
byte script (sent chunk by chunk to exercise fragmented UTF-8 and split
events), or an abrupt close. It records requests (headers, JSON body) so the
tests can assert what was sent (auth header, allowlisted parameters).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional


@dataclass
class Scripted:
    status: int = 200
    content_type: str = "text/event-stream"
    body: bytes = b""  # full body (JSON) or SSE text
    chunks: Optional[list[bytes]] = None  # when set, sent one by one with a tiny delay
    headers: dict = field(default_factory=dict)
    abort_after: Optional[int] = None  # close the socket after n chunks
    delay_s: float = 0.0


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict
    body: Optional[dict]
    query: str = ""


class FakeProvider:
    def __init__(self):
        self.routes: dict[tuple[str, str], Callable[[Recorded], Scripted] | Scripted] = {}
        self.requests: list[Recorded] = []
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port = 0

    def on(self, method: str, path: str, scripted) -> None:
        self.routes[(method.upper(), path)] = scripted

    def start(self) -> "FakeProvider":
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence
                return

            def _handle(self, method: str):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except ValueError:
                    body = {"_raw": raw.decode("utf-8", errors="replace")}
                rec = Recorded(method, self.path.split("?")[0], {k.lower(): v for k, v in self.headers.items()}, body, self.path.partition("?")[2])
                fake.requests.append(rec)
                scripted = fake.routes.get((method, rec.path))
                if scripted is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    out = b'{"error":{"message":"no route"}}'
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                if callable(scripted):
                    scripted = scripted(rec)
                if scripted.delay_s:
                    time.sleep(scripted.delay_s)
                self.send_response(scripted.status)
                self.send_header("Content-Type", scripted.content_type)
                for k, v in scripted.headers.items():
                    self.send_header(k, v)
                if scripted.chunks is None:
                    self.send_header("Content-Length", str(len(scripted.body)))
                    self.end_headers()
                    self.wfile.write(scripted.body)
                    return
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for i, c in enumerate(scripted.chunks):
                    if scripted.abort_after is not None and i >= scripted.abort_after:
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    self.wfile.write(f"{len(c):x}\r\n".encode() + c + b"\r\n")
                    self.wfile.flush()
                    time.sleep(0.005)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        class QuietServer(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):  # aborted sockets are part of the scripts
                return

        self._server = QuietServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()


def sse(*events: dict | str, done: bool = True) -> bytes:
    """Build an SSE body from JSON objects (``data:`` lines) or raw strings."""
    out = []
    for e in events:
        if isinstance(e, str):
            out.append(e)
        else:
            out.append("data: " + json.dumps(e, ensure_ascii=False) + "\n\n")
    if done:
        out.append("data: [DONE]\n\n")
    return "".join(out).encode("utf-8")


def split_bytes(data: bytes, size: int) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


def cc_chunk(text: str = "", reasoning: str = "", finish: Optional[str] = None, tool: Optional[dict] = None, usage: Optional[dict] = None) -> dict:
    delta: dict = {}
    if text:
        delta["content"] = text
    if reasoning:
        delta["reasoning_content"] = reasoning
    if tool:
        delta["tool_calls"] = [tool]
    obj = {"id": "c1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        obj["usage"] = usage
    return obj
