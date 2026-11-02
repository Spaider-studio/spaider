"""Document import: copy the original into ``data/imports/<id>/``, extract text
with SpAIder's ``parser_service`` (TXT, Markdown, text-based PDF), chunk for
fulltext search and queue the chunks for memory extraction."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Optional

from app.services.parser_service import UnsupportedParserError, parse_sync

from ..db import Database
from ..paths import PortablePaths
from .common import NotFound, new_id, utcnow

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".pdf": "application/pdf",
}
MAX_BYTES = 50 * 1024 * 1024
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
_SAFE_NAME = re.compile(r"[^\w .()\-\[\]äöüÄÖÜß]", re.UNICODE)


class UnsupportedDocument(ValueError):
    pass


class DocumentTooLarge(ValueError):
    pass


def _doc(row, facts: int = 0) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "content_type": row["content_type"],
        "size_bytes": row["size_bytes"],
        "text_chars": row["text_chars"],
        "status": row["status"],
        "warnings": json.loads(row["warnings_json"] or "[]"),
        "conversation_id": row["conversation_id"],
        "created_at": row["created_at"],
        "facts_count": facts,
        "progress": {"done": row["progress_done"], "total": row["progress_total"]},
    }


def split_chunks(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[tuple[int, int, str]]:
    """Character chunks with overlap, cut at sentence/paragraph boundaries when
    possible (same idea as ``SemanticCompressor._split_chunks_char_legacy``)."""
    text = text.strip()
    if not text:
        return []
    chunks: list[tuple[int, int, str]] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            cut = max(text.rfind("\n\n", start + size // 2, end), text.rfind(". ", start + size // 2, end))
            if cut > start:
                end = cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append((start, end, piece))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


class DocumentStore:
    def __init__(self, db: Database, paths: PortablePaths):
        self.db = db
        self.paths = paths

    def list(self) -> list[dict]:
        rows = self.db.query(
            "SELECT d.*, (SELECT count(*) FROM nodes n WHERE n.origin_kind = 'document' AND n.origin_id = d.id AND n.type = 'FACT') AS facts "  # noqa: E501
            "FROM documents d WHERE d.deleted_at IS NULL ORDER BY d.created_at DESC"
        )
        return [_doc(r, r["facts"]) for r in rows]

    def get(self, did: str) -> dict:
        row = self.db.query_one(
            "SELECT d.*, (SELECT count(*) FROM nodes n WHERE n.origin_kind = 'document' AND n.origin_id = d.id AND n.type = 'FACT') AS facts "  # noqa: E501
            "FROM documents d WHERE d.id = ? AND d.deleted_at IS NULL",
            (did,),
        )
        if row is None:
            raise NotFound(did)
        return _doc(row, row["facts"])

    def text(self, did: str, offset: int = 0, limit: int = 5000) -> dict:
        self.get(did)
        full = self.db.scalar("SELECT text FROM document_text WHERE document_id = ?", (did,)) or ""
        return {"text": full[offset : offset + limit], "total_chars": len(full)}

    def import_bytes(
        self, name: str, payload: bytes, *, conversation_id: Optional[str] = None
    ) -> tuple[dict, list[str]]:
        """Store the original, parse, chunk. Returns (document, chunk_ids)."""
        safe_name = _SAFE_NAME.sub("_", Path(name).name).strip() or "dokument"
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise UnsupportedDocument(f"Dateityp {suffix or '(ohne Endung)'} wird nicht unterstützt (TXT, MD, PDF).")
        if len(payload) > MAX_BYTES:
            raise DocumentTooLarge("Die Datei ist größer als 50 MB.")
        did = new_id()
        target_dir = self.paths.imports_dir / did
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / safe_name
        if not self.paths.is_inside_data(target):
            raise UnsupportedDocument("Ungültiger Dateiname.")
        target.write_bytes(payload)
        sha = hashlib.sha256(payload).hexdigest()
        content_type = SUPPORTED_SUFFIXES[suffix]
        warnings: list[dict] = []
        status = "indexing"
        try:
            result = parse_sync(payload, content_type=content_type, filename=safe_name)
            text = result.text or ""
        except UnsupportedParserError as exc:
            text = ""
            warnings.append({"code": "parse_failed", "message_de": f"Die Datei konnte nicht gelesen werden: {exc}"})
            status = "failed"
        if suffix == ".pdf" and not text.strip() and status != "failed":
            warnings.append(
                {
                    "code": "no_text_layer",
                    "message_de": "Kein Textinhalt erkannt (gescannte PDF ohne Textebene?). Der Inhalt kann nicht durchsucht werden.",  # noqa: E501
                }
            )
            status = "failed"
        chunks = split_chunks(text) if text.strip() else []
        if not chunks and status == "indexing":
            status = "indexed"
        now = utcnow()
        chunk_ids: list[str] = []
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO documents(id, name, content_type, size_bytes, stored_path, sha256, text_chars, status, warnings_json, "  # noqa: E501
                "conversation_id, progress_done, progress_total, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    did,
                    safe_name,
                    content_type,
                    len(payload),
                    self.paths.relative_to_data(target),
                    sha,
                    len(text),
                    status,
                    json.dumps(warnings, ensure_ascii=False),
                    conversation_id,
                    0,
                    len(chunks),
                    now,
                ),
            )
            conn.execute("INSERT INTO document_text(document_id, text) VALUES (?, ?)", (did, text))
            for ord_, (s, e, piece) in enumerate(chunks):
                cid = new_id()
                chunk_ids.append(cid)
                conn.execute(
                    "INSERT INTO chunks(id, document_id, ord, text, char_start, char_end) VALUES (?,?,?,?,?,?)",
                    (cid, did, ord_, piece, s, e),
                )
        return self.get(did), chunk_ids

    def delete(self, did: str) -> None:
        doc = self.get(did)
        row = self.db.query_one("SELECT stored_path FROM documents WHERE id = ?", (did,))
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (did,))
            conn.execute("DELETE FROM document_text WHERE document_id = ?", (did,))
            conn.execute("DELETE FROM documents WHERE id = ?", (did,))
            conn.execute("DELETE FROM ingest_queue WHERE kind = 'chunk' AND ref_id NOT IN (SELECT id FROM chunks)")
        target_dir = self.paths.imports_dir / did
        if row and self.paths.is_inside_data(target_dir) and target_dir.exists():
            import shutil

            shutil.rmtree(target_dir, ignore_errors=True)
        logger.info("deleted document %s (%s)", did, doc["name"])

    def count(self) -> int:
        return int(self.db.scalar("SELECT count(*) FROM documents WHERE deleted_at IS NULL") or 0)
