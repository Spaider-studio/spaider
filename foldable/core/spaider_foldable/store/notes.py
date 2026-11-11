from __future__ import annotations

from ..db import Database
from .common import NotFound, new_id, utcnow


def _note(row) -> dict:
    return {"id": row["id"], "text": row["text"], "created_at": row["created_at"], "updated_at": row["updated_at"]}


class NoteStore:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict]:
        return [_note(r) for r in self.db.query("SELECT * FROM notes ORDER BY updated_at DESC")]

    def get(self, nid: str) -> dict:
        row = self.db.query_one("SELECT * FROM notes WHERE id = ?", (nid,))
        if row is None:
            raise NotFound(nid)
        return _note(row)

    def create(self, text: str) -> dict:
        nid = new_id()
        now = utcnow()
        self.db.execute(
            "INSERT INTO notes(id, text, created_at, updated_at) VALUES (?,?,?,?)", (nid, text.strip(), now, now)
        )
        return self.get(nid)

    def update(self, nid: str, text: str) -> dict:
        self.get(nid)
        self.db.execute("UPDATE notes SET text = ?, updated_at = ? WHERE id = ?", (text.strip(), utcnow(), nid))
        return self.get(nid)

    def delete(self, nid: str) -> None:
        self.get(nid)
        self.db.execute("DELETE FROM notes WHERE id = ?", (nid,))

    def count(self) -> int:
        return int(self.db.scalar("SELECT count(*) FROM notes") or 0)
