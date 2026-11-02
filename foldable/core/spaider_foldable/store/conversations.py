from __future__ import annotations

import json
from typing import Optional

from ..db import Database
from .common import NotFound, new_id, utcnow


def _conv(row, count: int) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "message_count": count,
        "last_model_id": row["last_model_id"],
    }


def _msg(row) -> dict:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"],
        "status": row["status"],
        "model_id": row["model_id"],
        "sources": json.loads(row["sources_json"] or "[]"),
        "error": row["error"],
        "usage": json.loads(row["usage_json"]) if row["usage_json"] else None,
    }


class ConversationStore:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict]:
        rows = self.db.query(
            "SELECT c.*, (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id) AS n "
            "FROM conversations c WHERE c.deleted_at IS NULL ORDER BY c.updated_at DESC"
        )
        return [_conv(r, r["n"]) for r in rows]

    def create(self, title: Optional[str] = None) -> dict:
        cid = new_id()
        now = utcnow()
        self.db.execute(
            "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (cid, (title or "Neues Gespräch").strip()[:200], now, now),
        )
        return self.get(cid)

    def get(self, cid: str) -> dict:
        row = self.db.query_one("SELECT * FROM conversations WHERE id = ? AND deleted_at IS NULL", (cid,))
        if row is None:
            raise NotFound(cid)
        count = self.db.scalar("SELECT count(*) FROM messages WHERE conversation_id = ?", (cid,)) or 0
        return _conv(row, int(count))

    def rename(self, cid: str, title: str) -> dict:
        self.get(cid)
        self.db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title.strip()[:200], utcnow(), cid)
        )
        return self.get(cid)

    def touch(self, cid: str, model_id: Optional[str] = None) -> None:
        self.db.execute(
            "UPDATE conversations SET updated_at = ?, last_model_id = COALESCE(?, last_model_id) WHERE id = ?",
            (utcnow(), model_id, cid),
        )

    def delete(self, cid: str) -> None:
        self.get(cid)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
            conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))

    # ---- messages ---------------------------------------------------------
    def messages(self, cid: str) -> list[dict]:
        rows = self.db.query("SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq", (cid,))
        return [_msg(r) for r in rows]

    def get_message(self, mid: str) -> dict:
        row = self.db.query_one("SELECT * FROM messages WHERE id = ?", (mid,))
        if row is None:
            raise NotFound(mid)
        return _msg(row)

    def add_message(
        self, cid: str, role: str, content: str, *, status: str = "complete", model_id: Optional[str] = None
    ) -> dict:
        mid = new_id()
        now = utcnow()
        with self.db.transaction() as conn:
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE conversation_id = ?", (cid,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO messages(id, conversation_id, seq, role, content, status, model_id, created_at) VALUES (?,?,?,?,?,?,?,?)",  # noqa: E501
                (mid, cid, seq, role, content, status, model_id, now),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, cid))
        return self.get_message(mid)

    def update_message(
        self,
        mid: str,
        *,
        content: Optional[str] = None,
        status: Optional[str] = None,
        sources: Optional[list] = None,
        usage: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> dict:
        sets, params = [], []
        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if sources is not None:
            sets.append("sources_json = ?")
            params.append(json.dumps(sources, ensure_ascii=False))
        if usage is not None:
            sets.append("usage_json = ?")
            params.append(json.dumps(usage))
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if sets:
            params.append(mid)
            self.db.execute(f"UPDATE messages SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_message(mid)

    def streaming_messages(self) -> list[dict]:
        return [_msg(r) for r in self.db.query("SELECT * FROM messages WHERE status = 'streaming'")]
