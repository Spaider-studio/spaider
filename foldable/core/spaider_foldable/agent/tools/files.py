"""File tools: list/search/read inside granted roots, atomic writes inside the
write directory. Paths are canonicalised (realpath, no UNC, no device paths)
and checked against the task's roots; model text never widens the grant."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from ..broker import Broker, Tool, ToolContext, ToolError, ToolOutcome

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".ini", ".yaml", ".yml", ".tsv", ".html", ".htm", ".xml", ".py", ".ts", ".js"}
DOC_EXT = {".pdf", ".docx"}
MAX_READ = 12000
MAX_LIST = 200


def _canon(p: str) -> Path:
    raw = (p or "").strip().strip('"')
    if not raw:
        raise ToolError("invalid_path", "Leerer Pfad.")
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise ToolError("invalid_path", "Netzwerkpfade (UNC) sind nicht erlaubt.")
    if re.match(r"^(\\\\\.\\|\\\\\?\\)", raw):
        raise ToolError("invalid_path", "Gerätepfade sind nicht erlaubt.")
    return Path(os.path.realpath(raw))


def _under(path: Path, roots: list[Path]) -> bool:
    for r in roots:
        try:
            path.relative_to(r)
            return True
        except ValueError:
            continue
    return False


class FileTools:
    def __init__(self, parse_document=None):
        self.parse_document = parse_document  # callable(path) -> text (pdf/docx), optional

    def _roots(self, ctx: ToolContext) -> list[Path]:
        fs = ctx.scopes.files
        if not fs or not fs.roots:
            raise ToolError("scope", "Keine Ordner freigegeben.")
        return [_canon(r) for r in fs.roots]

    def _resolve_read(self, ctx: ToolContext, p: str) -> Path:
        roots = self._roots(ctx)
        path = _canon(p)
        if not _under(path, roots):
            raise ToolError("scope", f"Pfad liegt außerhalb der freigegebenen Ordner: {path}")
        return path

    def _resolve_write(self, ctx: ToolContext, p: str) -> Path:
        fs = ctx.scopes.files
        if not fs or not fs.write_dir:
            raise ToolError("scope", "Kein Schreibordner freigegeben.")
        wd = _canon(fs.write_dir)
        path = _canon(p) if os.path.isabs(p) else Path(os.path.realpath(wd / p))
        if not _under(path, [wd]):
            raise ToolError("scope", f"Schreiben nur im freigegebenen Ordner {wd} erlaubt.")
        return path

    # ---- tools ----------------------------------------------------------------------------
    async def list_dir(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        path = self._resolve_read(ctx, args["path"])
        if not path.is_dir():
            raise ToolError("not_found", "Ordner nicht gefunden.")
        pattern = (args.get("pattern") or "*").strip() or "*"
        entries = []
        for p in sorted(path.glob(pattern))[: MAX_LIST + 1]:
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append({"name": p.name, "kind": "dir" if p.is_dir() else "file", "bytes": st.st_size if p.is_file() else None})
        truncated = len(entries) > MAX_LIST
        return ToolOutcome(ok=True, summary_de=f"{min(len(entries), MAX_LIST)} Einträge in {path.name or path}", data={"path": str(path), "entries": entries[:MAX_LIST], "truncated": truncated}, verified=True)

    async def search(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        root = self._resolve_read(ctx, args.get("path") or ctx.scopes.files.roots[0])
        query = (args["query"] or "").strip()
        if len(query) < 2:
            raise ToolError("invalid", "Suchbegriff zu kurz.")
        q = query.lower()
        hits = []
        scanned = 0

        def _scan():
            nonlocal scanned
            for p in root.rglob("*"):
                if scanned >= 400 or len(hits) >= 40:
                    break
                if not p.is_file():
                    continue
                scanned += 1
                if q in p.name.lower():
                    hits.append({"path": str(p), "match": "name"})
                    continue
                if p.suffix.lower() in TEXT_EXT and p.stat().st_size <= 2_000_000:
                    try:
                        text = p.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    i = text.lower().find(q)
                    if i >= 0:
                        hits.append({"path": str(p), "match": "content", "excerpt": text[max(0, i - 80) : i + 120].replace("\n", " ")})

        await asyncio.to_thread(_scan)
        return ToolOutcome(ok=True, summary_de=f"{len(hits)} Treffer für „{query}“", data={"hits": hits, "scanned": scanned}, verified=True)

    async def read(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        path = self._resolve_read(ctx, args["path"])
        if not path.is_file():
            raise ToolError("not_found", "Datei nicht gefunden.")
        max_chars = int(args.get("max_chars") or 6000)
        max_chars = max(200, min(max_chars, MAX_READ))
        offset = int(args.get("offset") or 0)
        ext = path.suffix.lower()
        if ext in DOC_EXT and self.parse_document:
            text = await asyncio.to_thread(self.parse_document, path)
        else:
            if path.stat().st_size > 8_000_000:
                raise ToolError("too_large", "Datei ist zu groß (max. 8 MB).")
            raw = await asyncio.to_thread(path.read_bytes)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("cp1252", errors="replace")
        chunk = text[offset : offset + max_chars]
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        return ToolOutcome(
            ok=True,
            summary_de=f"{len(chunk)} von {len(text)} Zeichen gelesen: {path.name}",
            data={"path": str(path), "text": chunk, "offset": offset, "total_chars": len(text), "truncated": offset + max_chars < len(text), "sha256": digest},
            evidence={"sha256": digest, "chars": len(text)},
            verified=True,
        )

    async def write(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        path = self._resolve_write(ctx, args["path"])
        content = args["content"]
        mode = args.get("mode") or "create"
        if path.suffix.lower() not in TEXT_EXT:
            raise ToolError("invalid", "Nur Text-, Markdown-, CSV-, JSON- oder ähnliche Textdateien dürfen geschrieben werden.")
        if path.exists():
            if mode == "create":
                raise ToolError("exists", f"Datei existiert bereits: {path.name}. Für Änderungen mode=\"overwrite\" mit if_sha256 des gelesenen Stands verwenden.")
            current = hashlib.sha256(path.read_bytes().decode("utf-8", errors="replace").encode("utf-8", errors="replace")).hexdigest()
            expected = args.get("if_sha256")
            if not expected or expected != current:
                raise ToolError("conflict", "Die Datei hat sich seit dem letzten Lesen geändert oder if_sha256 fehlt; bitte erneut lesen.")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")

        def _atomic():
            fd, tmp = tempfile.mkstemp(prefix=".spaider-", dir=str(path.parent))
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)

        await asyncio.to_thread(_atomic)
        back = path.read_bytes()
        digest = hashlib.sha256(back.decode("utf-8", errors="replace").encode("utf-8", errors="replace")).hexdigest()
        ok = back == data
        return ToolOutcome(
            ok=ok,
            summary_de=(f"{len(data)} Bytes geschrieben und zurückgelesen: {path.name}" if ok else "Rücklesen stimmt nicht überein"),
            data={"path": str(path), "bytes": len(data), "sha256": digest},
            evidence={"sha256": digest, "bytes": len(data), "read_back_equal": ok},
            verified=ok,
            artifacts=[{"kind": "file", "path": str(path), "label": path.name}],
        )


def register(broker: Broker, *, parse_document=None) -> None:
    ft = FileTools(parse_document=parse_document)
    broker.register(
        Tool(
            name="files.list",
            description_de="Listet einen freigegebenen Ordner (optional Glob-Muster).",
            scope="files",
            side_effect="none",
            schema={"type": "object", "properties": {"path": {"type": "string", "maxLength": 500}, "pattern": {"type": "string", "maxLength": 80}}, "required": ["path"], "additionalProperties": False},
            run=ft.list_dir,
            timeout_s=20,
        )
    )
    broker.register(
        Tool(
            name="files.search",
            description_de="Sucht Dateinamen und Textinhalte in einem freigegebenen Ordner (max. 40 Treffer).",
            scope="files",
            side_effect="none",
            schema={"type": "object", "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 120}, "path": {"type": "string", "maxLength": 500}}, "required": ["query"], "additionalProperties": False},
            run=ft.search,
            timeout_s=30,
        )
    )
    broker.register(
        Tool(
            name="files.read",
            description_de="Liest eine Datei (Text, Markdown, CSV, JSON, PDF, DOCX) abschnittsweise; liefert text, total_chars und sha256.",
            scope="files",
            side_effect="none",
            schema={"type": "object", "properties": {"path": {"type": "string", "maxLength": 500}, "offset": {"type": "integer", "minimum": 0}, "max_chars": {"type": "integer", "minimum": 200, "maximum": 12000}}, "required": ["path"], "additionalProperties": False},
            run=ft.read,
            timeout_s=30,
        )
    )
    broker.register(
        Tool(
            name="files.write",
            description_de="Schreibt eine neue Textdatei im Schreibordner (mode=create) oder ersetzt eine gelesene Datei (mode=overwrite mit if_sha256). Atomar, wird zurückgelesen.",
            scope="files",
            side_effect="local",
            schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "maxLength": 500},
                    "content": {"type": "string", "maxLength": 60000},
                    "mode": {"type": "string", "enum": ["create", "overwrite"]},
                    "if_sha256": {"type": "string", "maxLength": 64},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            run=ft.write,
            timeout_s=30,
        )
    )
