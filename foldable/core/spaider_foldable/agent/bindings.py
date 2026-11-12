"""BindingResolver: binds portable roles ("output folder of the project",
"the notes app", "the test window") to the current machine.

Roles come from the workflow; targets come only from the task's granted
scopes on *this* host, from bindings the user confirmed on this host
(``agent_bindings``, keyed by host id) or from an explicit question to the
user. Coordinates, window handles, sessions and permissions of another PC are
never taken over. Permissions are still checked by the broker on every call -
a binding is a name resolution, not a grant.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from ..db import Database
from ..store.common import utcnow
from .contracts import VerifiedWorkflow, WorkflowBinding
from .models import Scopes

ROLE_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:40] or "x"


class BindingResolver:
    def __init__(self, db: Database, host_id: str):
        self.db = db
        self.host_id = host_id

    # ---- stored host bindings -------------------------------------------------------------
    def stored(self) -> dict[str, dict]:
        return {r["role"]: dict(r) for r in self.db.query("SELECT * FROM agent_bindings WHERE host_id = ?", (self.host_id,))}

    def remember(self, role: str, kind: str, target: str) -> dict:
        now = utcnow()
        self.db.execute(
            "INSERT INTO agent_bindings(host_id, role, kind, target, verified_at, created_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(host_id, role) DO UPDATE SET kind = excluded.kind, target = excluded.target, verified_at = excluded.verified_at",
            (self.host_id, role, kind, target, now, now),
        )
        return {"host_id": self.host_id, "role": role, "kind": kind, "target": target, "verified_at": now}

    def forget(self, role: str) -> None:
        self.db.execute("DELETE FROM agent_bindings WHERE host_id = ? AND role = ?", (self.host_id, role))

    # ---- resolution ---------------------------------------------------------------------
    def resolve(self, workflow: VerifiedWorkflow, scopes: Scopes, *, windows: Optional[list] = None) -> tuple[dict[str, str], list[WorkflowBinding]]:
        """Returns (role -> target, unresolved bindings). Directory roles are matched by the
        task's granted roots/write dir (same folder name first, then a stored binding whose
        target still exists and lies inside the granted roots); apps by allowed process; windows
        by granted source ids whose process/title matches the hint."""
        resolved: dict[str, str] = {}
        missing: list[WorkflowBinding] = []
        stored = self.stored()
        roots = [Path(os.path.realpath(r)) for r in (scopes.files.roots if scopes.files else [])]
        write_dir = Path(os.path.realpath(scopes.files.write_dir)) if scopes.files and scopes.files.write_dir else None
        for b in workflow.bindings:
            target: Optional[str] = None
            if b.kind == "dir":
                if b.role == "output_dir":
                    target = str(write_dir) if write_dir else None
                if target is None:
                    for r in roots:
                        if b.hint and slug(r.name) == slug(b.hint):
                            target = str(r)
                            break
                if target is None and b.role in stored:
                    cand = Path(stored[b.role]["target"])
                    if cand.is_dir() and (any(_under(cand, r) for r in roots) or (write_dir and _under(cand, write_dir))):
                        target = str(cand)
                if target is None and b.role != "output_dir" and len(roots) == 1 and len([x for x in workflow.bindings if x.kind == "dir" and x.role != "output_dir"]) == 1:
                    target = str(roots[0])  # exactly one granted folder for exactly one input role
            elif b.kind == "file":
                for r in roots:
                    cand = r / b.hint
                    if b.hint and cand.is_file():
                        target = str(cand)
                        break
            elif b.kind == "app":
                allowed = {p.lower() for p in (scopes.windows.allowed_processes if scopes.windows else [])}
                if b.hint.lower() in allowed:
                    target = b.hint.lower()
            elif b.kind == "window":
                for w in windows or []:
                    sid = getattr(w, "id", None) or (w.get("id") if isinstance(w, dict) else None)
                    proc = (getattr(w, "process", None) or (w.get("process") if isinstance(w, dict) else "") or "").lower()
                    title = (getattr(w, "title", None) or (w.get("title") if isinstance(w, dict) else "") or "").lower()
                    granted = scopes.windows.source_ids if scopes.windows else []
                    if sid in granted and (not b.hint or b.hint.lower() in proc or b.hint.lower().split("|")[0] in title):
                        target = sid
                        break
                if target is None and scopes.windows and len(scopes.windows.source_ids) == 1 and len([x for x in workflow.bindings if x.kind == "window"]) == 1:
                    target = scopes.windows.source_ids[0]
            elif b.kind == "url":
                target = b.original or b.hint or None
            elif b.kind in ("account", "model"):
                target = b.original or None
            if target is None:
                if b.required:
                    missing.append(b)
            else:
                resolved[b.role] = target
        return resolved, missing

    @staticmethod
    def fill(value, bindings: dict[str, str], params: dict | None):
        """Replace {role} and {param} placeholders (recursively); unknown names stay."""
        subs = {**(params or {}), **bindings}
        if isinstance(value, str):
            out = value
            for k, v in subs.items():
                out = out.replace("{" + str(k) + "}", str(v))
            return out
        if isinstance(value, dict):
            return {k: BindingResolver.fill(v, bindings, params) for k, v in value.items()}
        if isinstance(value, list):
            return [BindingResolver.fill(v, bindings, params) for v in value]
        return value

    @staticmethod
    def unresolved_placeholders(value) -> set[str]:
        found: set[str] = set()
        if isinstance(value, str):
            found.update(ROLE_RE.findall(value))
        elif isinstance(value, dict):
            for v in value.values():
                found |= BindingResolver.unresolved_placeholders(v)
        elif isinstance(value, list):
            for v in value:
                found |= BindingResolver.unresolved_placeholders(v)
        return found


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
