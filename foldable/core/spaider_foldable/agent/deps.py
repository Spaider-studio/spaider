"""DependencyStore: required facts and resources of a workflow with revision,
origin and scope; transitive invalidation when something changes.

Rows live in ``agent_dependencies`` of the profile database (no graph
database). The graph only records relations that were *observed* (a memory
search returned the fact, a file was read) or *declared* (by the compiler or
the user); relations the model merely *suggested* are stored as ``inferred``
and never count as full proof - the ReuseGate treats them as unknown
coverage and verifies more.

Invalidation never deletes anything: ``valid`` flips to 0 with the reason
and the superseding id; a workflow that depends on an invalid row becomes
``stale`` for the gate, and workflows that depend on that workflow follow
(transitively, bounded).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Callable, Optional

from ..db import Database
from ..store.common import utcnow
from .contracts import Dependency

logger = logging.getLogger(__name__)

MAX_TRANSITIVE_DEPTH = 6


class DependencyStore:
    def __init__(self, db: Database, host_id: str):
        self.db = db
        self.host_id = host_id

    # ---- write -----------------------------------------------------------------------------
    def replace(self, workflow_id: str, deps: list[Dependency]) -> list[Dependency]:
        """Store the dependency rows of a workflow version (old rows of that workflow are kept
        only while they are invalid, as history; valid duplicates are replaced)."""
        now = utcnow()
        out: list[Dependency] = []
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM agent_dependencies WHERE workflow_id = ? AND valid = 1", (workflow_id,))
            for d in deps:
                did = d.id or f"dep_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    "INSERT INTO agent_dependencies(id, workflow_id, step_index, kind, key, revision, origin, scope, valid, invalidated_at, invalidated_by, reason, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (did, workflow_id, d.step_index, d.kind, d.key, d.revision, d.origin, d.scope, 1 if d.valid else 0, d.invalidated_at, d.invalidated_by, d.reason, now),
                )
                out.append(d.model_copy(update={"id": did}))
        return out

    def invalidate(self, kind: str, key: str, *, by: Optional[str], reason: str) -> list[str]:
        """Mark every valid dependency on (kind, key) invalid and propagate to workflows that
        depend on the affected workflows. Returns the affected workflow ids."""
        affected: list[str] = []
        seen: set[str] = set()
        frontier = [(kind, key)]
        depth = 0
        now = utcnow()
        while frontier and depth < MAX_TRANSITIVE_DEPTH:
            next_frontier = []
            for k, kk in frontier:
                rows = self.db.query("SELECT id, workflow_id FROM agent_dependencies WHERE kind = ? AND key = ? AND valid = 1", (k, kk))
                for r in rows:
                    self.db.execute(
                        "UPDATE agent_dependencies SET valid = 0, invalidated_at = ?, invalidated_by = ?, reason = ? WHERE id = ?",
                        (now, by, reason[:300], r["id"]),
                    )
                    wid = r["workflow_id"]
                    if wid not in seen:
                        seen.add(wid)
                        affected.append(wid)
                        next_frontier.append(("workflow", wid))
            frontier = next_frontier
            depth += 1
        if affected:
            logger.info("dependencies: %s %s invalidated (%s) -> %d workflow(s) stale", kind, key[:60], reason[:80], len(affected))
        return affected

    def invalidate_facts(self, fact_ids: list[str], *, by: Optional[str] = None, reason: str = "Fakt ersetzt") -> list[str]:
        out: list[str] = []
        for fid in fact_ids:
            out.extend(self.invalidate("fact", fid, by=by, reason=reason))
        return out

    # ---- read ------------------------------------------------------------------------------
    def for_workflow(self, workflow_id: str, *, include_invalid: bool = True) -> list[Dependency]:
        sql = "SELECT * FROM agent_dependencies WHERE workflow_id = ?" + ("" if include_invalid else " AND valid = 1") + " ORDER BY created_at, step_index"
        return [self._row(r) for r in self.db.query(sql, (workflow_id,))]

    def _row(self, r) -> Dependency:
        return Dependency(
            id=r["id"], kind=r["kind"], key=r["key"], revision=r["revision"], origin=r["origin"], scope=r["scope"], step_index=r["step_index"],
            valid=bool(r["valid"]), reason=r["reason"] or "", invalidated_by=r["invalidated_by"], invalidated_at=r["invalidated_at"],
        )

    # ---- current revisions -----------------------------------------------------------------
    def fact_revision(self, fact_id: str) -> tuple[Optional[str], bool, Optional[str]]:
        """(revision, current?, superseded_by) of a FACT node right now."""
        row = self.db.query_one("SELECT updated_at, superseded, superseded_by, processing FROM nodes WHERE id = ?", (fact_id,))
        if row is None:
            return None, False, None
        current = not bool(row["superseded"]) and (row["processing"] if "processing" in row.keys() else None) != "forgotten"
        return f"{row['updated_at']}|sup={int(bool(row['superseded']))}", current, row["superseded_by"]

    @staticmethod
    def file_revision(path: Path) -> Optional[str]:
        try:
            if not path.is_file():
                return None
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 16), b""):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return None

    def check(
        self,
        deps: list[Dependency],
        *,
        resolve_path: Callable[[str], Optional[Path]],
        tool_hashes: dict[str, str],
        app_available: Callable[[str], bool],
        binding_present: Callable[[str], bool],
        workflow_status: Callable[[str], Optional[str]],
    ) -> list[Dependency]:
        """Re-evaluate every dependency against the current state. Returns copies with ``valid``
        and ``reason`` set for *now* (stored validity AND current state must both hold)."""
        out: list[Dependency] = []
        for d in deps:
            cur = d.model_copy()
            if not d.valid:
                cur.reason = d.reason or "als ungültig markiert"
                out.append(cur)
                continue
            if d.kind == "fact":
                rev, current, by = self.fact_revision(d.key)
                if rev is None:
                    cur.valid, cur.reason = False, "Fakt nicht mehr vorhanden"
                elif not current:
                    cur.valid, cur.reason, cur.invalidated_by = False, "Fakt wurde ersetzt oder vergessen", by
                elif d.revision and rev != d.revision:
                    cur.valid, cur.reason = False, "Fakt wurde seit der Aufzeichnung geändert"
            elif d.kind == "file":
                p = resolve_path(d.key)
                rev = self.file_revision(p) if p is not None else None
                if p is None:
                    cur.valid, cur.reason = False, "Pfad nicht auflösbar (Bindung fehlt)"
                elif rev is None:
                    cur.valid, cur.reason = False, "Datei fehlt"
                elif d.revision and rev != d.revision:
                    cur.valid, cur.reason = False, "Datei hat sich geändert"
                cur.revision = rev or cur.revision
            elif d.kind == "binding":
                if not binding_present(d.key):
                    cur.valid, cur.reason = False, "Bindung auf diesem Rechner nicht vorhanden"
            elif d.kind == "app":
                if not app_available(d.key):
                    cur.valid, cur.reason = False, "Programm/Fenster nicht verfügbar"
            elif d.kind == "tool_contract":
                h = tool_hashes.get(d.key)
                if h is None:
                    cur.valid, cur.reason = False, "Werkzeug nicht verfügbar"
                elif d.revision and h != d.revision:
                    cur.valid, cur.reason = False, "Werkzeugvertrag hat sich geändert"
            elif d.kind == "workflow":
                st = workflow_status(d.key)
                if st not in ("candidate", "verified"):
                    cur.valid, cur.reason = False, f"abhängiger Ablauf ist {st or 'nicht vorhanden'}"
            elif d.kind == "model":
                pass  # informational: the model is interchangeable by design
            out.append(cur)
        return out

    def stale_workflows(self) -> list[str]:
        return [r["workflow_id"] for r in self.db.query("SELECT DISTINCT workflow_id FROM agent_dependencies WHERE valid = 0")]

    def summary(self, workflow_id: str) -> dict:
        rows = self.db.query("SELECT kind, origin, valid, count(*) AS n FROM agent_dependencies WHERE workflow_id = ? GROUP BY kind, origin, valid", (workflow_id,))
        out = {"total": 0, "valid": 0, "invalid": 0, "by_kind": {}, "inferred": 0}
        for r in rows:
            out["total"] += r["n"]
            out["valid" if r["valid"] else "invalid"] += r["n"]
            out["by_kind"][r["kind"]] = out["by_kind"].get(r["kind"], 0) + r["n"]
            if r["origin"] == "inferred":
                out["inferred"] += r["n"]
        return out


def dependency_key(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def parse_dependency_key(s: str) -> tuple[str, str]:
    kind, _, key = s.partition(":")
    return kind, key


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
