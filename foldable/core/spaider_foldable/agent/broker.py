"""Execution broker: the trusted controller between the planner (model output)
and the adapters (files, browser, windows, vision, memory).

* Tools are registered with a JSON schema for their arguments, a scope and a
  side-effect class. ``available_tools(scopes)`` is what the model gets to see.
* ``validate`` checks structure, scope and freshness (element references must
  come from the latest observation) BEFORE anything runs.
* ``execute`` re-checks the task's cancel generation right before and after the
  adapter call; a late result after a cancel can never start a follow-up.
* Side effects are journaled by the caller (TaskStore) around ``execute``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .models import ActionResult, Scopes

logger = logging.getLogger(__name__)

# names of UI elements whose activation usually has consequences outside the app
CONSEQUENTIAL = re.compile(
    r"\b(senden|send|absenden|submit|kaufen|buy|bestellen|order|bezahlen|pay|checkout|löschen|loeschen|delete|entfernen|remove|"
    r"veröffentlichen|veroeffentlichen|publish|posten|post|teilen|share|überweisen|transfer|abonnieren|subscribe|kündigen|cancel subscription|"
    r"deinstallieren|uninstall|formatieren|format)\b",
    re.IGNORECASE,
)


class ToolError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass
class ToolOutcome:
    ok: bool
    summary_de: str
    data: dict = field(default_factory=dict)  # bounded, goes back to the model
    evidence: Optional[dict] = None  # proof for the journal/UI (hashes, values read back)
    snapshot_id: Optional[str] = None
    verified: bool = False
    error: Optional[str] = None
    artifacts: list[dict] = field(default_factory=list)


@dataclass
class ToolContext:
    task_id: str
    scopes: Scopes
    cancel_generation: int
    is_cancelled: Callable[[], bool]
    latest_snapshot_ids: dict[str, str]  # source_id -> snapshot id the planner saw
    emit: Callable[[str, dict], None]
    extras: dict = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description_de: str
    scope: str  # files | browser | windows | vision | memory
    side_effect: str  # none | local | external
    schema: dict
    run: Callable[[dict, ToolContext], Awaitable[ToolOutcome]]
    timeout_s: float = 60.0
    needs_fresh_element: bool = False  # arguments reference an element of a snapshot
    availability: Callable[[], tuple[bool, Optional[str]]] = lambda: (True, None)

    def info(self, scopes: Optional[Scopes] = None) -> dict:
        ok, reason = self.availability()
        if ok and scopes is not None and not scope_allows(self.scope, scopes):
            ok, reason = False, "Nicht im Umfang dieser Aufgabe."
        return {"name": self.name, "description_de": self.description_de, "scope": self.scope, "side_effect": self.side_effect, "available": ok, "reason_de": reason, "schema": self.schema}


def scope_allows(scope: str, scopes: Scopes) -> bool:
    if scope == "files":
        return bool(scopes.files and scopes.files.roots)
    if scope == "browser":
        return bool(scopes.browser and scopes.browser.enabled)
    if scope == "windows":
        return bool(scopes.windows and (scopes.windows.source_ids or scopes.windows.allowed_processes))
    if scope == "vision":
        return bool(scopes.windows and scopes.windows.source_ids)
    if scope == "memory":
        return bool(scopes.memory)
    return False


def _check_schema(value: Any, schema: dict, path: str = "") -> Optional[str]:
    """Small JSON-schema subset validator (type, required, properties, enum, min/max length, items)."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return f"{path or 'arguments'} muss ein Objekt sein"
        for req in schema.get("required", []):
            if req not in value:
                return f"Pflichtfeld fehlt: {path + '.' if path else ''}{req}"
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(props)
            if extra:
                return f"Unbekannte Felder: {', '.join(sorted(extra))}"
        for k, sub in props.items():
            if k in value:
                err = _check_schema(value[k], sub, f"{path + '.' if path else ''}{k}")
                if err:
                    return err
        return None
    if t == "string":
        if not isinstance(value, str):
            return f"{path} muss Text sein"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return f"{path} ist zu lang (max. {schema['maxLength']})"
        if "minLength" in schema and len(value) < schema["minLength"]:
            return f"{path} ist zu kurz"
        if "enum" in schema and value not in schema["enum"]:
            return f"{path} muss eines von {schema['enum']} sein"
        if "pattern" in schema and not re.search(schema["pattern"], value):
            return f"{path} hat nicht das erwartete Format ({schema['pattern']})"
        return None
    if t == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{path} muss eine ganze Zahl sein"
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path} ist zu klein"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path} ist zu groß"
        return None
    if t == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return f"{path} muss eine Zahl sein"
        return None
    if t == "boolean":
        if not isinstance(value, bool):
            return f"{path} muss true/false sein"
        return None
    if t == "array":
        if not isinstance(value, list):
            return f"{path} muss eine Liste sein"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return f"{path} hat zu viele Einträge"
        item = schema.get("items")
        if item:
            for i, v in enumerate(value):
                err = _check_schema(v, item, f"{path}[{i}]")
                if err:
                    return err
        return None
    return None


class Broker:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def available_tools(self, scopes: Scopes) -> list[Tool]:
        out = []
        for t in self._tools.values():
            ok, _ = t.availability()
            if ok and scope_allows(t.scope, scopes):
                out.append(t)
        return out

    def infos(self, scopes: Optional[Scopes]) -> list[dict]:
        return [t.info(scopes) for t in self._tools.values()]

    # ---- validation ------------------------------------------------------------------
    def validate(self, tool_name: str, arguments: Any, scopes: Scopes, *, fresh_refs: Optional[dict[str, set[str]]] = None) -> tuple[Optional[Tool], Optional[str]]:
        tool = self._tools.get(tool_name)
        if tool is None:
            return None, f"Unbekanntes Werkzeug: {tool_name}"
        ok, reason = tool.availability()
        if not ok:
            return tool, f"Werkzeug nicht verfügbar: {reason}"
        if not scope_allows(tool.scope, scopes):
            return tool, "Werkzeug liegt außerhalb des freigegebenen Umfangs dieser Aufgabe."
        err = _check_schema(arguments if arguments is not None else {}, tool.schema)
        if err:
            return tool, f"Ungültige Argumente: {err}"
        if tool.needs_fresh_element and fresh_refs is not None:
            sid = arguments.get("source_id")
            ref = arguments.get("element_id") or arguments.get("ref")
            if ref and sid in fresh_refs and ref not in fresh_refs[sid] and str(ref).strip().lower() not in fresh_refs[sid]:
                return tool, "Elementreferenz stammt nicht aus der aktuellen Beobachtung (bitte erneut beobachten)."
        return tool, None

    @staticmethod
    def requires_approval(tool: Tool, arguments: dict, scopes: Scopes, element_name: Optional[str] = None) -> Optional[str]:
        """Why this action needs an explicit user approval (None = no approval needed)."""
        if tool.side_effect == "external" and not scopes.external_effects:
            return "Diese Aktion wirkt nach außen (z. B. senden, veröffentlichen, kaufen, dauerhaft löschen)."
        label = element_name or str(arguments.get("text") or arguments.get("name") or "")
        if tool.side_effect != "none" and label and CONSEQUENTIAL.search(label) and not scopes.external_effects:
            return f"Das Ziel „{label[:60]}“ sieht nach einer folgenreichen Aktion aus."
        return None

    # ---- execution --------------------------------------------------------------------
    async def execute(self, tool: Tool, arguments: dict, ctx: ToolContext, *, generation_now: Callable[[], int]) -> ToolOutcome:
        if ctx.is_cancelled() or generation_now() != ctx.cancel_generation:
            return ToolOutcome(ok=False, summary_de="Abgebrochen, bevor die Aktion begann.", error="cancelled")
        t0 = time.monotonic()
        try:
            outcome = await asyncio.wait_for(tool.run(arguments, ctx), timeout=tool.timeout_s)
        except asyncio.TimeoutError:
            return ToolOutcome(ok=False, summary_de=f"Zeitüberschreitung ({tool.timeout_s:.0f} s) bei {tool.name}; Ausgang unklar.", error="timeout")
        except ToolError as exc:
            return ToolOutcome(ok=False, summary_de=exc.detail, error=exc.code)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s failed", tool.name)
            return ToolOutcome(ok=False, summary_de=f"Werkzeugfehler: {str(exc)[:200]}", error="tool_error")
        outcome.data = _bound(outcome.data)
        outcome.data["duration_ms"] = int((time.monotonic() - t0) * 1000)
        if generation_now() != ctx.cancel_generation:
            # the action ran, but the task was cancelled meanwhile: report the real outcome, never continue
            outcome.summary_de += " (Aufgabe wurde währenddessen abgebrochen.)"
        return outcome

    @staticmethod
    def to_result(outcome: ToolOutcome) -> ActionResult:
        return ActionResult(ok=outcome.ok, summary_de=outcome.summary_de, evidence=outcome.evidence, snapshot_id=outcome.snapshot_id, error=outcome.error, verified=outcome.verified)


def _bound(data: Any, max_chars: int = 6000) -> dict:
    """Keep tool data small enough for the prompt; long strings are cut and marked."""
    if not isinstance(data, dict):
        return {"value": str(data)[:max_chars]}
    out: dict = {}
    budget = max_chars
    for k, v in data.items():
        if isinstance(v, str):
            if len(v) > budget:
                out[k] = v[: max(0, budget)] + " …[gekürzt]"
                budget = 0
            else:
                out[k] = v
                budget -= len(v)
        elif isinstance(v, list):
            out[k] = v[:60]
            if len(v) > 60:
                out[k + "_truncated"] = True
        else:
            out[k] = v
    return out
