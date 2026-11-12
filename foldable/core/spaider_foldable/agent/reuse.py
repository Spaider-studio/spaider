"""ReuseGate: decides whether a stored workflow may be reused for a task,
which steps must be repaired, or whether the planner has to start over.

Decision path (binding): valid workflow -> reuse; a deviation that is
locally bounded (a few steps, no external side effect) -> repair those
steps; otherwise -> replan. Model calls happen on demand; validation,
verification and permission checks happen always, regardless of the
decision. Unknown or inferred dependencies lower the coverage and switch
the run into *strict* mode (every step is verified, UI steps re-observe).
"""

from __future__ import annotations

import math
from typing import Any

from .contracts import Dependency, ReusePlan, VerifiedWorkflow, WorkflowBinding

MAX_REPAIR_FRACTION = 0.5
MIN_COVERAGE_FOR_RELAXED = 0.8


def _norm_key(key: str) -> str:
    return key.replace("\\", "/").strip().lower()


def _produced(key: str, produced: set[str]) -> bool:
    """`bericht.md` written relative to the output folder and `{output_dir}/bericht.md` read back are one file."""
    a = _norm_key(key)
    return any(a == b or a.endswith("/" + b) or b.endswith("/" + a) for b in produced)


class ReuseGate:
    def decide(
        self,
        workflow: VerifiedWorkflow,
        *,
        params: dict[str, Any],
        deps_now: list[Dependency],
        bindings: dict[str, str],
        missing_bindings: list[WorkflowBinding],
        available_tools: set[str],
    ) -> ReusePlan:
        reasons: list[str] = []
        if workflow.status in ("demoted", "retired"):
            return ReusePlan(mode="replan", reasons_de=[f"Ablauf ist {('zurückgestuft' if workflow.status == 'demoted' else 'stillgelegt')}; keine automatische Wiederverwendung."], bindings=bindings, missing_bindings=[b.role for b in missing_bindings])
        missing_params = [p.name for p in workflow.parameters if p.required and (params.get(p.name) in (None, ""))]
        if missing_params:
            return ReusePlan(mode="replan", reasons_de=[f"Parameter fehlen: {', '.join(missing_params)}"], bindings=bindings, missing_bindings=[b.role for b in missing_bindings])
        missing_tools = [t for t in workflow.requires_tools() if t not in available_tools]
        if missing_tools:
            return ReusePlan(mode="replan", reasons_de=[f"Werkzeuge fehlen oder sind nicht im Umfang: {', '.join(missing_tools)}"], bindings=bindings, missing_bindings=[b.role for b in missing_bindings])
        produced = {_norm_key(k[5:]) for s in workflow.steps for k in s.write_set if k.startswith("file:")}
        # a file that an earlier step of this workflow writes is produced during the run, not an input
        invalid = [d for d in deps_now if not d.valid and not (d.kind == "file" and _produced(d.key, produced))]
        # tool contract drift: the plan was recorded against other argument schemas
        if any(d.kind == "tool_contract" for d in invalid):
            return ReusePlan(mode="replan", reasons_de=["Werkzeugvertrag hat sich geändert; der Ablauf wird neu geplant."], invalid=invalid, bindings=bindings, missing_bindings=[b.role for b in missing_bindings])
        if any(d.kind == "workflow" for d in invalid):
            reasons.append("Ein abhängiger Ablauf ist nicht mehr gültig.")
        # which steps are affected by invalid facts / missing inputs / apps
        repair: set[int] = set()
        for d in invalid:
            if d.kind == "fact":
                key = f"fact:{d.key}"
                for s in workflow.steps:
                    if key in s.depends_on or key in s.read_set or (d.step_index is not None and s.index == d.step_index) or (d.step_index is not None and d.step_index in s.derive_from):
                        repair.add(s.index)
                if d.step_index is None and d.origin == "inferred":
                    # an inferred relation changed: we do not know which steps rely on it -> every
                    # derived/parameter step is re-checked (strict), repair only the derived ones
                    for s in workflow.steps:
                        if s.derive:
                            repair.add(s.index)
                reasons.append(f"Fakt {d.key[:12]}… {d.reason or 'ungültig'}" + (f" (ersetzt durch {d.invalidated_by[:12]}…)" if d.invalidated_by else ""))
            elif d.kind == "file":
                if "fehlt" in (d.reason or "") or "nicht auflösbar" in (d.reason or ""):
                    reasons.append(f"Eingabedatei {d.key} fehlt")
                    for s in workflow.steps:
                        if f"file:{d.key}" in s.read_set or f"file:{d.key}" in s.depends_on:
                            repair.add(s.index)
                # a *changed* input file is the normal case for a repeated task: steps re-run, nothing to repair
            elif d.kind == "app":
                reasons.append(f"Programm {d.key} nicht verfügbar")
                for s in workflow.steps:
                    if s.tool == "windows.launch":
                        repair.add(s.index)
            elif d.kind == "binding":
                reasons.append(f"Bindung {d.key} fehlt auf diesem Rechner")
        for b in missing_bindings:
            reasons.append(f"Rolle „{b.role}“ ist auf diesem Rechner nicht gebunden")
        # coverage: observed + declared and valid over all; inferred never counts as proof
        total = len(deps_now)
        # a changed input file is a known relation with new content, not missing coverage
        proven = sum(1 for d in deps_now if d.origin in ("observed", "declared") and (d.valid or (d.kind == "file" and ("geändert" in (d.reason or "") or _produced(d.key, produced)))))
        coverage = (proven / total) if total else 0.0
        unknown = total == 0 or any(d.origin == "inferred" for d in deps_now) or any(not s.depends_on and s.tool in ("memory.search", "browser.read", "browser.snapshot", "windows.read", "vision.describe") for s in workflow.steps)
        strict = workflow.status != "verified" or unknown or coverage < MIN_COVERAGE_FOR_RELAXED or bool(invalid)
        if any(workflow.steps[i].side_effect == "external" for i in repair if i < len(workflow.steps)):
            return ReusePlan(mode="replan", reasons_de=reasons + ["Betroffene Schritte wirken nach außen; keine automatische Reparatur."], invalid=invalid, coverage=round(coverage, 2), unknown_dependencies=unknown, strict=True, bindings=bindings, missing_bindings=[b.role for b in missing_bindings])
        if repair and len(repair) > max(1, math.ceil(len(workflow.steps) * MAX_REPAIR_FRACTION)):
            return ReusePlan(mode="replan", reasons_de=reasons + [f"{len(repair)} von {len(workflow.steps)} Schritten betroffen; Neuplanung."], invalid=invalid, coverage=round(coverage, 2), unknown_dependencies=unknown, strict=True, bindings=bindings, missing_bindings=[b.role for b in missing_bindings])
        if repair:
            return ReusePlan(mode="repair", reasons_de=reasons, repair_steps=sorted(repair), strict=True, coverage=round(coverage, 2), unknown_dependencies=unknown, invalid=invalid, bindings=bindings, missing_bindings=[b.role for b in missing_bindings])
        if not reasons:
            reasons.append("Alle Abhängigkeiten gültig" + ("" if not strict else "; Ablauf wird streng geprüft (Kandidat oder unvollständige Abdeckung)"))
        return ReusePlan(mode="reuse", reasons_de=reasons, strict=strict, coverage=round(coverage, 2), unknown_dependencies=unknown, invalid=invalid, bindings=bindings, missing_bindings=[b.role for b in missing_bindings])

    @staticmethod
    def negative_check(workflow: VerifiedWorkflow) -> dict:
        """Synthetic failure case: with every binding missing and every fact dependency invalid the
        gate must refuse plain reuse. Used to promote a candidate ('with independent variants and
        failure cases checked')."""
        gate = ReuseGate()
        deps = [d.model_copy(update={"valid": False, "reason": "synthetisch ungültig"}) if d.kind == "fact" else d for d in workflow.dependencies]
        plan = gate.decide(workflow, params={p.name: p.example or "x" for p in workflow.parameters}, deps_now=deps, bindings={}, missing_bindings=[b for b in workflow.bindings if b.required], available_tools=set(workflow.requires_tools()))
        refused = plan.mode != "reuse" or plan.strict or bool(plan.missing_bindings)
        return {"ok": refused, "mode": plan.mode, "reasons_de": plan.reasons_de[:4]}
