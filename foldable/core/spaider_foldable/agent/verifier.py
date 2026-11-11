"""OutcomeVerifier: proves the *current* result state of a task.

"Done" needs evidence: a saved file is read back, a form value is read, the
application's state is inspected. The verifier runs read-only broker tools
right now (never old evidence) and reports ``fulfilled`` / ``not_fulfilled``
/ ``unclear`` with the checks, causes and evidence paths. A model may claim
success; it never confirms it on its own.

Criteria come from (in this order) the workflow's checks, explicit user
criteria, and conservative heuristics over the goal text (output files
named in the goal, "prüfe, dass „…“ angezeigt wird" patterns). When nothing
can be derived, the verifier falls back to ``action_evidence``: the last
successful action must itself be verified (read-back, value read).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

from ..store.common import utcnow
from .broker import Broker, ToolContext
from .contracts import CheckResult, OutcomeReport, SuccessCriterion
from .models import Action, Scopes

_FILE_RE = re.compile(r"((?:[A-Za-z]:[\\/]|/)[^\s„“\"']+?\.(?:txt|md|markdown|csv|json|log|html|htm|xml|tsv|yaml|yml|eml))", re.IGNORECASE)
_FILE_NAME_RE = re.compile(r"\b([\wäöüÄÖÜß\-]+\.(?:txt|md|markdown|csv|json|log|html|htm|xml|tsv|yaml|yml|eml))\b", re.IGNORECASE)
_CONFIRM_RE = re.compile(r"(?:prüfe|pruefe|kontrolliere|bestätig\w*|warte)[^„\"]{0,80}[„\"]([^“\"]{2,120})[“\"]", re.IGNORECASE)
_QUOTED_RE = re.compile(r"[„\"]([^“\"\n]{2,120})[“\"]")
_LITERAL_LEAD_RE = re.compile(r"(?:\bmit(?: dem Text| dem Betreff| der Überschrift| der Zeile)?|\bBetreff|\benthält|\benthalt|\bÜberschrift|\bZeile)\s*$", re.IGNORECASE)
_OUTPUT_WORDS = re.compile(r"\b(nach|in|als|unter|erstelle|schreibe|speichere|lege|erzeuge)\b", re.IGNORECASE)


def derive_criteria(goal: str, scopes: Scopes, parameters: dict | None = None, *, workflow_checks: Optional[list[SuccessCriterion]] = None) -> list[SuccessCriterion]:
    """Conservative success criteria from the goal (no model involved)."""
    out: list[SuccessCriterion] = []
    if workflow_checks:
        out.extend(c.model_copy(update={"origin": "workflow"}) for c in workflow_checks)
    text = goal or ""
    write_dir = scopes.files.write_dir if scopes.files else None
    seen: set[str] = set()
    # output files: absolute paths with a text extension, or bare names when a write dir exists
    for m in _FILE_RE.finditer(text):
        p = m.group(1).rstrip(".,;:)")
        if write_dir and _is_under(p, write_dir) and p not in seen:
            seen.add(p)
            out.append(SuccessCriterion(kind="file_exists", target=p, origin="goal", description_de=f"Datei {Path(p).name} existiert und ist nicht leer"))
    if write_dir:
        for m in _FILE_NAME_RE.finditer(text):
            name = m.group(1)
            before = text[max(0, m.start() - 60) : m.start()]
            if _OUTPUT_WORDS.search(before) and not any(Path(s).name.lower() == name.lower() for s in seen):
                p = str(Path(write_dir) / name)
                if p not in seen and not _mentioned_as_input(text, name):
                    seen.add(p)
                    out.append(SuccessCriterion(kind="file_exists", target=p, origin="goal", description_de=f"Datei {name} existiert und ist nicht leer"))
    # literal labels the output file must carry: quoted strings right after "mit" / "enthält" / "Betreff"
    # (a placeholder part like <E-Mail der Leitung> is cut off; too short remainders are ignored)
    if out and out[-1].kind == "file_exists":
        target = out[-1].target
        for m in _QUOTED_RE.finditer(text):
            before = text[max(0, m.start() - 25) : m.start()]
            # the keyword must stand directly before the quote ("mit „An:“", "Betreff „Bericht“");
            # "mit der Rolle „Leitung“" names a selector, not required content
            if not _LITERAL_LEAD_RE.search(before):
                continue
            literal = m.group(1).split("<")[0].strip()
            if len(literal) >= 2 and "{" not in literal and not _mentioned_as_input(text, literal):
                out.append(SuccessCriterion(kind="file_contains", target=target, expect=[literal], origin="goal", description_de=f"Datei enthält „{literal}“"))
    # confirmation texts to check on the page / in the window
    for m in _CONFIRM_RE.finditer(text):
        token = m.group(1).strip()
        if len(token) < 2:
            continue
        if scopes.browser and scopes.browser.enabled:
            out.append(SuccessCriterion(kind="page_text", target="browser", expect=[token], origin="goal", description_de=f"Seite zeigt „{token}“"))
        elif scopes.windows and scopes.windows.source_ids:
            out.append(SuccessCriterion(kind="window_text", target=scopes.windows.source_ids[0], expect=[token], origin="goal", description_de=f"Fenster zeigt „{token}“"))
    if not out:
        out.append(SuccessCriterion(kind="action_evidence", origin="goal", description_de="Letzte erfolgreiche Aktion ist selbst belegt"))
    return out


def _is_under(p: str, root: str) -> bool:
    try:
        Path(os.path.realpath(p)).relative_to(Path(os.path.realpath(root)))
        return True
    except ValueError:
        return False


def _mentioned_as_input(text: str, name: str) -> bool:
    i = text.lower().find(name.lower())
    before = text[max(0, i - 40) : i].lower()
    return any(w in before for w in ("lies ", "lese ", "aus der datei", "aus ", "öffne", "oeffne", "datei {", "der datei"))


class OutcomeVerifier:
    def __init__(self, broker: Broker):
        self.broker = broker

    async def verify(self, criteria: list[SuccessCriterion], ctx: ToolContext, actions: list[Action], *, params: dict | None = None) -> OutcomeReport:
        checks: list[CheckResult] = []
        for c in criteria:
            try:
                checks.append(await self._check(c, ctx, actions, params or {}))
            except Exception as exc:  # noqa: BLE001
                checks.append(CheckResult(criterion=c, ok=None, detail_de=f"Prüfung nicht möglich: {str(exc)[:160]}"))
        oks = [c.ok for c in checks]
        if oks and all(o is True for o in oks):
            status = "fulfilled"
        elif any(o is False for o in oks):
            status = "not_fulfilled"
        else:
            status = "unclear"
        causes = [c.detail_de for c in checks if c.ok is not True and c.detail_de]
        paths = [str(c.evidence.get("path")) for c in checks if c.ok and c.evidence.get("path")]
        return OutcomeReport(status=status, checks=checks, causes_de=causes[:6], evidence_paths=paths, verified_at=utcnow(), verified_by="verifier" if any(c.criterion.kind != "action_evidence" for c in checks) else "action_evidence")

    async def _check(self, c: SuccessCriterion, ctx: ToolContext, actions: list[Action], params: dict) -> CheckResult:
        expect = [_fill(e, params) for e in c.expect]
        target = _fill(c.target, params)
        if c.kind in ("file_exists", "file_contains", "file_not_contains"):
            tool = self.broker.get("files.read")
            if tool is None:
                return CheckResult(criterion=c, ok=None, detail_de="files.read nicht verfügbar")
            try:
                out = await tool.run({"path": target, "max_chars": 12000}, ctx)
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "code", "")
                if code == "not_found":
                    return CheckResult(criterion=c, ok=False, detail_de=f"Datei fehlt: {Path(target).name}")
                return CheckResult(criterion=c, ok=None, detail_de=f"Datei nicht prüfbar: {str(exc)[:120]}")
            text = str(out.data.get("text") or "")
            total = int(out.data.get("total_chars") or 0)
            ev = {"path": target, "sha256": out.data.get("sha256"), "chars": total}
            if c.kind == "file_exists":
                ok = total > 0
                return CheckResult(criterion=c, ok=ok, detail_de="" if ok else f"Datei ist leer: {Path(target).name}", evidence=ev)
            low = text.lower()
            if c.kind == "file_contains":
                missing = [e for e in expect if e.lower() not in low]
                return CheckResult(criterion=c, ok=not missing, detail_de="" if not missing else f"Inhalt fehlt in {Path(target).name}: {', '.join(missing)[:160]}", evidence={**ev, "missing": missing})
            present = [e for e in expect if e.lower() in low]
            return CheckResult(criterion=c, ok=not present, detail_de="" if not present else f"Unerwünschter Inhalt in {Path(target).name}: {', '.join(present)[:160]}", evidence={**ev, "present": present})
        if c.kind == "page_text":
            tool = self.broker.get("browser.read")
            if tool is None:
                return CheckResult(criterion=c, ok=None, detail_de="browser.read nicht verfügbar")
            out = await tool.run({"max_chars": 12000}, ctx)
            text = str(out.data.get("text") or "").lower()
            missing = [e for e in expect if e.lower() not in text]
            return CheckResult(criterion=c, ok=not missing, detail_de="" if not missing else f"Seite zeigt nicht: {', '.join(missing)[:160]}", evidence={"url": out.data.get("url"), "title": out.data.get("title")})
        if c.kind in ("window_text", "element_value"):
            tool = self.broker.get("windows.read")
            if tool is None:
                return CheckResult(criterion=c, ok=None, detail_de="windows.read nicht verfügbar")
            sid, _, element = target.partition("|")
            args = {"source_id": sid}
            if element:
                args["element_id"] = element
            out = await tool.run(args, ctx)
            text = str(out.data.get("text") or "").lower()
            missing = [e for e in expect if e.lower() not in text]
            return CheckResult(criterion=c, ok=not missing, detail_de="" if not missing else f"Fenster zeigt nicht: {', '.join(missing)[:160]}", evidence={"source_id": sid, "element": element or None, "text": text[:200]})
        if c.kind == "action_evidence":
            done = [a for a in actions if a.state == "done" and a.result]
            if not done:
                return CheckResult(criterion=c, ok=False, detail_de="Keine ausgeführte Aktion")
            last = done[-1]
            if last.result.verified:
                return CheckResult(criterion=c, ok=True, detail_de="", evidence={"tool": last.tool, **(last.result.evidence or {})})
            return CheckResult(criterion=c, ok=None, detail_de=f"Letzte Aktion ({last.tool}) ist nicht selbst belegt")
        return CheckResult(criterion=c, ok=None, detail_de="Unbekannte Prüfung")


def _fill(value: str, params: dict) -> str:
    out = value or ""
    for k, v in (params or {}).items():
        out = out.replace("{" + str(k) + "}", str(v))
    return out


def criteria_to_json(criteria: list[SuccessCriterion]) -> list[dict[str, Any]]:
    return [c.model_dump() for c in criteria]
