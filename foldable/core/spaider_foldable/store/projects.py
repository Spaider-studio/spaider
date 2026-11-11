"""Projects: a light grouping of conversations and documents with a
``local_only`` flag that blocks every non-local connection (ADR-8)."""

from __future__ import annotations

from typing import Optional

from ..db import Database
from .common import NotFound, new_id, utcnow

DEFAULT_ID = "default"


def _row(r) -> dict:
    return {"id": r["id"], "name": r["name"], "local_only": bool(r["local_only"]), "created_at": r["created_at"], "updated_at": r["updated_at"]}


class ProjectStore:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict]:
        rows = self.db.query(
            "SELECT p.*, (SELECT count(*) FROM conversations c WHERE c.project_id = p.id AND c.deleted_at IS NULL) AS conversations, "
            "(SELECT count(*) FROM documents d WHERE d.project_id = p.id) AS documents FROM projects p ORDER BY (p.id = 'default') DESC, p.name"
        )
        out = []
        for r in rows:
            d = _row(r)
            d["conversations"] = int(r["conversations"] or 0)
            d["documents"] = int(r["documents"] or 0)
            out.append(d)
        return out

    def get(self, pid: str) -> dict:
        row = self.db.query_one("SELECT * FROM projects WHERE id = ?", (pid,))
        if row is None:
            raise NotFound(pid)
        return _row(row)

    def get_or_default(self, pid: Optional[str]) -> dict:
        try:
            return self.get(pid or DEFAULT_ID)
        except NotFound:
            return self.get(DEFAULT_ID)

    def create(self, name: str, *, local_only: bool = False) -> dict:
        pid = new_id()
        now = utcnow()
        self.db.execute("INSERT INTO projects(id, name, local_only, created_at, updated_at) VALUES (?,?,?,?,?)", (pid, name.strip()[:120] or "Projekt", 1 if local_only else 0, now, now))
        return self.get(pid)

    def update(self, pid: str, *, name: Optional[str] = None, local_only: Optional[bool] = None) -> dict:
        cur = self.get(pid)
        self.db.execute(
            "UPDATE projects SET name = ?, local_only = ?, updated_at = ? WHERE id = ?",
            ((name.strip()[:120] if name is not None else cur["name"]), 1 if (local_only if local_only is not None else cur["local_only"]) else 0, utcnow(), pid),
        )
        return self.get(pid)

    def delete(self, pid: str) -> None:
        if pid == DEFAULT_ID:
            raise ValueError("Das Standardprojekt kann nicht gelöscht werden.")
        self.get(pid)
        with self.db.transaction() as conn:
            conn.execute("UPDATE conversations SET project_id = ? WHERE project_id = ?", (DEFAULT_ID, pid))
            conn.execute("UPDATE documents SET project_id = ? WHERE project_id = ?", (DEFAULT_ID, pid))
            conn.execute("DELETE FROM sharing_consents WHERE project_id = ?", (pid,))
            conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
