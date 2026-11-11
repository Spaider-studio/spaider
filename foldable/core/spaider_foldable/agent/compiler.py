"""WorkflowCompiler: turns one successful, evidenced execution (the action
journal of a completed task) into a parametrised, bounded, declarative plan.

What the compiler produces is data only: tool name + argument template per
step, read/write sets, pre-/postconditions, portable bindings (roles instead
of absolute paths, element names instead of snapshot refs), dependencies on
memory facts and files with their revision, and the final checks. There is no
generated code and nothing a model wrote is executed.

Honesty rules baked in:
* arguments that were *derived* from data read earlier (a report written from
  file contents) are marked ``derive`` - replaying them verbatim would copy
  stale content, so such a step needs one model call per run (with the
  template as example); everything else replays without a model;
* fact relations that were *observed* (memory.search returned the fact) are
  recorded as ``observed``; parameter values that merely *match* a stored
  fact are recorded as ``inferred`` and never count as proof;
* UI references (b3, e12) are replaced by role+name targets that must be
  re-resolved against a fresh observation on every run.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from ..store.common import utcnow
from .bindings import slug
from .contracts import Dependency, StepCheck, SuccessCriterion, VerifiedWorkflow, WorkflowBinding, WorkflowParameter, WorkflowStep, tool_contract_hash
from .models import Action, Scopes, Task

READ_TOOLS = {"files.read", "files.list", "files.search", "memory.search", "browser.read", "browser.snapshot", "browser.wait_for_text", "windows.read", "windows.inspect", "vision.describe", "vision.locate"}
UI_ELEMENT_TOOLS = {"browser.click", "browser.fill", "browser.select", "browser.press", "windows.invoke", "windows.set_value", "windows.read"}
_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß\-]{3,}")
# values that identify data rather than wording: numbers (amounts, dates, codes), e-mails, paths
_VALUE_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|[A-Za-z]:[\\/][^\s\"'<>|]+|\d[\d.,:/-]*\d|\d")
_STOP = {"dass", "eine", "einer", "eines", "nicht", "oder", "auch", "sind", "wird", "werden", "wurde", "haben", "nach", "über", "unter", "beim", "zum", "zur", "mit", "ohne", "sowie", "diese", "dieser", "dieses", "sehr", "kann", "soll", "muss", "bitte", "datei", "ordner", "schreibe", "lies", "text", "inhalt", "wert", "status", "zeile", "zeilen", "markdown", "tabelle"}


def templatize(value, parameters: list[dict]):
    """Replace literal parameter values by {name} placeholders (longest first, both slash styles)."""
    subs = []
    for p in parameters or []:
        if not p.get("name") or p.get("value") in (None, ""):
            continue
        val, ph = str(p.get("value")), "{" + str(p.get("name")) + "}"
        for variant in {val, val.replace("\\", "/"), val.replace("/", "\\")}:
            subs.append((variant, ph))
    return _replace_all(value, sorted(subs, key=lambda kv: -len(kv[0])))


def _replace_all(value, subs: list[tuple[str, str]]):
    if isinstance(value, str):
        out = value
        for lit, ph in subs:
            if lit:
                out = _ireplace(out, lit, ph)
        return out
    if isinstance(value, dict):
        return {k: _replace_all(v, subs) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_all(v, subs) for v in value]
    return value


def _ireplace(text: str, lit: str, ph: str) -> str:
    """Case-insensitive replace for paths (Windows), exact for everything else."""
    if re.match(r"^[A-Za-z]:[\\/]", lit) or lit.startswith("/"):
        pat = re.compile(re.escape(lit), re.IGNORECASE)
        return pat.sub(lambda _m: ph, text)
    return text.replace(lit, ph)


def _content_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text or "") if w.lower() not in _STOP}


def _content_values(text: str) -> set[str]:
    """Numbers, dates, amounts, codes, e-mail addresses and paths - a single one of these copied
    from read data makes an argument data-derived."""
    out = set()
    for v in _VALUE_RE.findall(text or ""):
        v = v.strip(".,:;")
        if len(v) >= 2 and not (v.isdigit() and len(v) <= 1):
            out.add(v.lower())
    return out


class WorkflowCompiler:
    def __init__(self, *, tool_infos: Callable[[], list[dict]], find_facts: Optional[Callable[[str], list[dict]]] = None, host_id: str = ""):
        self.tool_infos = tool_infos  # [] of {"name", "schema"} for the contract hash
        self.find_facts = find_facts  # text -> [{"id", "description"}] (fulltext over current FACT nodes)
        self.host_id = host_id

    # ---- public ----------------------------------------------------------------------------
    def compile(
        self,
        task: Task,
        actions: list[Action],
        *,
        name: str,
        parameters: list[dict],
        action_data: Optional[dict[str, dict]] = None,
        element_names: Optional[dict[str, dict[str, str]]] = None,  # source_id -> {element_id: name}
        window_hints: Optional[dict[str, dict]] = None,  # source_id -> {"process", "title"}
        criteria: Optional[list[SuccessCriterion]] = None,
        origin_kind: str = "compiled",
        workflow_id: Optional[str] = None,
        version: int = 1,
    ) -> VerifiedWorkflow:
        action_data = action_data or {}
        element_names = element_names or {}
        window_hints = window_hints or {}
        done = [a for a in actions if a.state == "done"]
        roles, role_subs = self._roles(task.scopes, done, window_hints)
        param_subs = [(str(p["value"]), "{" + p["name"] + "}") for p in parameters if p.get("name") and p.get("value") not in (None, "")]
        goal_words = _content_words(task.goal) | _content_values(task.goal) | {str(p.get("value", "")).lower() for p in parameters}
        steps: list[WorkflowStep] = []
        deps: list[Dependency] = []
        read_texts: dict[int, str] = {}  # step index -> data text read by that step
        browser_elements: dict[str, dict] = {}  # ref -> {role, name} from the latest browser.snapshot
        seen_fact_ids: set[str] = set()
        for idx, a in enumerate(done):
            data = action_data.get(a.action_id) or {}
            args = dict(a.arguments)
            # remember browser element refs of the latest snapshot for target names
            if a.tool == "browser.snapshot":
                for e in data.get("elements") or []:
                    if isinstance(e, dict) and e.get("ref"):
                        browser_elements[str(e["ref"])] = {"role": str(e.get("role") or ""), "name": str(e.get("name") or "")}
            target = self._element_target(a, args, element_names, browser_elements, data)
            tmpl = templatize(args, parameters)
            tmpl = _replace_all(tmpl, role_subs)
            if target is not None:
                for key in ("element_id", "ref"):
                    if key in tmpl:
                        tmpl[key] = "{target}"
            read_set, write_set, pre = self._sets(a, tmpl, roles)
            step = WorkflowStep(index=idx, tool=a.tool, arguments_template=tmpl, target=target, read_set=read_set, write_set=write_set, preconditions=pre, side_effect=a.side_effect, check_de=(a.result.summary_de if a.result else "")[:160])
            # data read by this step (for derivation detection)
            txt = _data_text(a.tool, data)
            if txt:
                read_texts[idx] = txt
            # observed facts (memory.search sources)
            if a.tool == "memory.search":
                ids = [s.get("id") for s in (data.get("sources") or []) if isinstance(s, dict) and s.get("id")]
                for fid in ids:
                    if fid not in seen_fact_ids:
                        seen_fact_ids.add(fid)
                        deps.append(Dependency(kind="fact", key=str(fid), revision="", origin="observed", step_index=idx))
                    step.read_set.append(f"fact:{fid}")
                    step.depends_on.append(f"fact:{fid}")
            # files read -> observed file dependency with the sha from the evidence
            if a.tool == "files.read" and a.result and a.result.evidence and a.result.evidence.get("sha256"):
                key = str(tmpl.get("path") or "")
                deps.append(Dependency(kind="file", key=key, revision=str(a.result.evidence["sha256"]), origin="observed", step_index=idx))
                step.depends_on.append(f"file:{key}")
            # derived arguments: content/text that came from data read earlier
            derived_from = self._derivation(a.tool, tmpl, read_texts, idx, goal_words)
            if derived_from:
                step.derive = True
                step.derive_from = derived_from
                for src in derived_from:
                    step.depends_on.extend(d for d in steps[src].depends_on if d.startswith("fact:") or d.startswith("file:"))
            step.postconditions = self._postconditions(a, tmpl, target, parameters, done, idx, action_data)
            steps.append(step)
        # inferred fact relations: parameter values that match stored facts
        if self.find_facts:
            for p in parameters:
                val = str(p.get("value") or "")
                if len(val) < 3:
                    continue
                for n in self.find_facts(val)[:3]:
                    fid = n.get("id")
                    if fid and fid not in seen_fact_ids and val.lower() in (n.get("description") or "").lower():
                        seen_fact_ids.add(fid)
                        deps.append(Dependency(kind="fact", key=str(fid), revision="", origin="inferred", step_index=None, reason=f"Parameter {p['name']} entspricht einem gespeicherten Fakt"))
        tool_infos = self.tool_infos()
        by_name = {t["name"]: t for t in tool_infos}
        for t in sorted({s.tool for s in steps}):
            deps.append(Dependency(kind="tool_contract", key=t, revision=tool_contract_hash([by_name[t]]) if t in by_name else "", origin="declared"))
        for b in roles:
            if b.required:
                deps.append(Dependency(kind="binding", key=b.role, revision=f"{self.host_id}|{b.original}", origin="declared"))
            if b.kind == "app":
                deps.append(Dependency(kind="app", key=b.hint, revision="", origin="declared"))
        checks = [self._template_criterion(c, parameters, role_subs) for c in (criteria or [])]
        wf = VerifiedWorkflow(
            id=workflow_id or f"wf_{uuid.uuid4().hex[:10]}",
            version=version,
            name=name,
            purpose_de=_replace_all(templatize(task.goal, parameters), role_subs)[:300],
            status="candidate",
            parameters=[WorkflowParameter(name=p["name"], description_de=str(p.get("description_de") or ""), example=str(p.get("example") or p.get("value") or ""), kind=_param_kind(str(p.get("value") or ""))) for p in parameters if p.get("name")],
            preconditions=sorted({f"tool:{s.tool}" for s in steps} | {f"binding:{b.role}" for b in roles if b.required} | {p for s in steps for p in s.preconditions}),
            bindings=roles,
            steps=steps,
            checks=checks,
            dependencies=deps,
            scope=task.scopes,
            origin={"task_id": task.id, "created_at": task.finished_at or task.updated_at, "host_id": self.host_id, "kind": origin_kind},
            tool_contract_hash=tool_contract_hash([by_name[t] for t in sorted({s.tool for s in steps}) if t in by_name]),
            known_limits_de=self._limits(steps),
            created_at=utcnow(),
            updated_at=utcnow(),
            last_verified_at=task.finished_at,
        )
        return wf

    # ---- helpers ---------------------------------------------------------------------------
    @staticmethod
    def _roles(scopes: Scopes, done: list[Action], window_hints: dict[str, dict]) -> tuple[list[WorkflowBinding], list[tuple[str, str]]]:
        roles: list[WorkflowBinding] = []
        subs: list[tuple[str, str]] = []
        used: set[str] = set()
        if scopes.files:
            if scopes.files.write_dir:
                wd = os.path.realpath(scopes.files.write_dir)
                roles.append(WorkflowBinding(role="output_dir", kind="dir", hint=Path(wd).name, required=True, original=wd))
                subs.extend(_path_variants(scopes.files.write_dir, "{output_dir}"))
                used.add("output_dir")
            for r in scopes.files.roots:
                rp = os.path.realpath(r)
                if scopes.files.write_dir and os.path.realpath(scopes.files.write_dir).lower() == rp.lower():
                    continue
                role = f"root_{slug(Path(rp).name)}"
                base, n = role, 2
                while role in used:
                    role, n = f"{base}_{n}", n + 1
                used.add(role)
                roles.append(WorkflowBinding(role=role, kind="dir", hint=Path(rp).name, required=True, original=rp))
                subs.extend(_path_variants(r, "{" + role + "}"))
        if scopes.windows:
            for sid in scopes.windows.source_ids:
                hint = window_hints.get(sid) or {}
                proc = str(hint.get("process") or "").lower()
                role = f"window_{slug(proc.replace('.exe', '') or sid)}"
                base, n = role, 2
                while role in used:
                    role, n = f"{base}_{n}", n + 1
                used.add(role)
                roles.append(WorkflowBinding(role=role, kind="window", hint=(proc + "|" + str(hint.get("title") or "")[:40]) if proc else "", required=True, original=sid))
                subs.append((sid, "{" + role + "}"))
        for a in done:
            if a.tool == "windows.launch" and a.arguments.get("app"):
                app = str(a.arguments["app"]).lower()
                role = f"app_{slug(app)}"
                if role not in used:
                    used.add(role)
                    exe = {"notepad": "notepad.exe", "rechner": "calc.exe", "explorer": "explorer.exe"}.get(app, app)
                    roles.append(WorkflowBinding(role=role, kind="app", hint=exe, required=True, original=app))
        # longest paths first so nested folders are replaced before their parents
        subs.sort(key=lambda kv: -len(kv[0]))
        return roles, subs

    @staticmethod
    def _element_target(a: Action, args: dict, element_names: dict[str, dict[str, str]], browser_elements: dict[str, dict], data: Optional[dict] = None) -> Optional[dict[str, str]]:
        if a.tool.startswith("windows.") and args.get("element_id"):
            sid = str(args.get("source_id") or "")
            name = (element_names.get(sid) or {}).get(str(args["element_id"]))
            if name:
                return {"role": "", "name": name}
            return {"role": "", "name": str(args["element_id"])}
        if a.tool.startswith("browser.") and args.get("ref"):
            # the tool result carries the accessible name the adapter computed at execution time
            d = data if isinstance(data, dict) else {}
            if d.get("name"):
                return {"role": str(d.get("role") or ""), "name": str(d["name"])}
            el = browser_elements.get(str(args["ref"]))
            if el and el.get("name"):
                return {"role": el.get("role", ""), "name": el["name"]}
            if a.result and a.result.evidence and a.result.evidence.get("name"):
                return {"role": str(a.result.evidence.get("role") or ""), "name": str(a.result.evidence["name"])}
            if d.get("clicked"):
                return {"role": "", "name": str(d["clicked"])}
            return None
        return None

    @staticmethod
    def _sets(a: Action, tmpl: dict, roles: list[WorkflowBinding]) -> tuple[list[str], list[str], list[str]]:
        read, write, pre = [], [], []
        if a.tool in ("files.read", "files.list", "files.search"):
            p = str(tmpl.get("path") or "")
            if p:
                read.append(f"file:{p}")
                if a.tool == "files.read":
                    pre.append(f"exists:{p}")
        elif a.tool == "files.write":
            p = str(tmpl.get("path") or "")
            write.append(f"file:{p}")
        elif a.tool.startswith("browser."):
            (write if a.tool in ("browser.click", "browser.fill", "browser.select", "browser.press", "browser.open", "browser.back") else read).append("page")
        elif a.tool.startswith("windows.") or a.tool.startswith("vision."):
            sid = str(tmpl.get("source_id") or "")
            key = f"window:{sid}"
            (write if a.tool in ("windows.invoke", "windows.set_value", "windows.type_text", "windows.send_keys", "windows.click_at", "windows.focus", "windows.launch") else read).append(key)
        elif a.tool == "memory.search":
            read.append("memory")
        return read, write, pre

    @staticmethod
    def _derivation(tool: str, tmpl: dict, read_texts: dict[int, str], idx: int, goal_words: set[str]) -> list[int]:
        field = {"files.write": "content", "browser.fill": "text", "windows.set_value": "text", "windows.type_text": "text"}.get(tool)
        if not field:
            return []
        text = str(tmpl.get(field) or "")
        text_wo_params = re.sub(r"\{[a-z][a-z0-9_]*\}", " ", text)
        words = _content_words(text_wo_params) - goal_words
        values = _content_values(text_wo_params) - goal_words
        if len(words) < 2 and not values:
            return []
        hits: list[int] = []
        for src, data_text in read_texts.items():
            if src >= idx:
                continue
            # two shared content words, or a single shared value (date, amount, e-mail, path)
            if len(words & _content_words(data_text)) >= 2 or (values & _content_values(data_text)):
                hits.append(src)
        return hits

    @staticmethod
    def _postconditions(a: Action, tmpl: dict, target: Optional[dict], parameters: list[dict], done: list[Action], idx: int, action_data: dict) -> list[StepCheck]:
        out: list[StepCheck] = []
        if a.tool == "files.write":
            p = str(tmpl.get("path") or "")
            out.append(StepCheck(kind="file_exists", target=p, description_de=f"Datei {Path(p).name} existiert"))
            tokens = ["{" + x["name"] + "}" for x in parameters if x.get("name") and str(x.get("value") or "") and str(x["value"]) in str(a.arguments.get("content") or "")]
            if tokens:
                out.append(StepCheck(kind="file_contains", target=p, expect=tokens, description_de="Datei enthält die Parameterwerte"))
        elif a.tool == "browser.wait_for_text":
            out.append(StepCheck(kind="page_text", target="browser", expect=[str(tmpl.get("text") or "")], description_de="Text erschien auf der Seite"))
        elif a.tool == "browser.fill" and target:
            # input values are not visible page text: check the field's value through the page elements
            out.append(StepCheck(kind="element_value", target="browser|" + target["name"], expect=[str(tmpl.get("text") or "")], description_de=f"Feld „{target['name']}“ trägt den Wert"))
        elif a.tool == "browser.select" and target:
            out.append(StepCheck(kind="element_value", target="browser|" + target["name"], expect=[str(tmpl.get("value") or "")], description_de=f"Auswahl „{target['name']}“ steht auf dem Wert"))
        elif a.tool == "windows.set_value" and target:
            out.append(StepCheck(kind="element_value", target="{" + _window_role_of(tmpl) + "}|" + target["name"], expect=[str(tmpl.get("text") or "")], description_de=f"Feld „{target['name']}“ trägt den Wert"))
        elif a.tool in ("browser.click", "windows.invoke", "windows.click_at", "windows.send_keys", "browser.press"):
            # the next read/wait step in the original run becomes this step's check
            for later in done[idx + 1 : idx + 3]:
                if later.tool == "browser.wait_for_text":
                    out.append(StepCheck(kind="page_text", target="browser", expect=[str(templatize(later.arguments, parameters).get("text") or "")], description_de="Erwarteter Text erschien"))
                    break
                if later.tool == "windows.read" and later.result and later.result.evidence and later.result.evidence.get("text_excerpt"):
                    excerpt = str(later.result.evidence["text_excerpt"])[:40]
                    if excerpt.strip():
                        out.append(StepCheck(kind="window_text", target="{" + _window_role_of(tmpl) + "}", expect=[templatize(excerpt, parameters)], description_de="Fenster zeigt den erwarteten Text"))
                    break
        return out

    @staticmethod
    def _template_criterion(c: SuccessCriterion, parameters: list[dict], role_subs: list[tuple[str, str]]) -> SuccessCriterion:
        t = _replace_all(templatize(c.target, parameters), role_subs)
        e = [_replace_all(templatize(x, parameters), role_subs) for x in c.expect]
        return c.model_copy(update={"target": t, "expect": e, "origin": "workflow"})

    @staticmethod
    def _limits(steps: list[WorkflowStep]) -> list[str]:
        out = ["Aus genau einem belegten Lauf abgeleitet; jeder Schritt wird bei der Wiederverwendung erneut geprüft."]
        if any(s.derive for s in steps):
            out.append("Inhalte, die aus gelesenen Daten entstanden sind, werden je Lauf neu abgeleitet (ein Modellaufruf je solchem Schritt).")
        if any(s.tool.startswith(("browser.", "windows.")) for s in steps):
            out.append("Oberflächenschritte werden gegen eine frische Beobachtung aufgelöst; bei geänderter Anwendung wird repariert oder neu geplant.")
        if any(s.side_effect == "external" for s in steps):
            out.append("Enthält Wirkungen nach außen; diese werden nie automatisch wiederholt.")
        return out


def _path_variants(path: str, ph: str) -> list[tuple[str, str]]:
    raw = path.rstrip("\\/")
    rp = os.path.realpath(raw)
    out = []
    for v in {raw, rp, raw.replace("\\", "/"), rp.replace("\\", "/"), raw.replace("/", "\\"), rp.replace("/", "\\")}:
        if v:
            out.append((v, ph))
    return out


def _window_role_of(tmpl: dict) -> str:
    sid = str(tmpl.get("source_id") or "")
    m = re.match(r"^\{([a-z0-9_]+)\}$", sid)
    return m.group(1) if m else sid


def _data_text(tool: str, data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    if tool in ("files.read", "browser.read", "windows.read"):
        return str(data.get("text") or "")
    if tool == "memory.search":
        return str(data.get("context") or "")
    if tool == "browser.snapshot":
        return str(data.get("text") or "") + " " + " ".join(str(e.get("name") or "") + " " + str(e.get("value") or "") for e in (data.get("elements") or []) if isinstance(e, dict))
    if tool == "windows.inspect":
        return str(data.get("structure") or "")
    if tool == "vision.describe":
        return str(data.get("answer") or "")
    if tool == "files.search":
        return " ".join(str(h.get("excerpt") or "") for h in (data.get("hits") or []) if isinstance(h, dict))
    return ""


def _param_kind(value: str) -> str:
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("/"):
        return "path"
    if re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", value):
        return "date"
    if re.match(r"^-?\d+([.,]\d+)?$", value):
        return "number"
    return "text"
