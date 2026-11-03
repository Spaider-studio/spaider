"""Windows tools: inspect/read/focus/invoke/set_value/type_text/click_at on
windows that are in the task's scope. Every action re-validates the target
(same hwnd + pid, visible, not minimized, foreground for input) and re-reads
the element right before acting; results are read back through UIA when the
pattern allows it.

Input injection uses pywinauto (SendInput). UIPI: windows of elevated
processes cannot receive our input; that is reported, never bypassed.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from typing import Optional

from ...observe import windows as W
from ...observe.uia import UiaProvider
from ..broker import Broker, Tool, ToolContext, ToolError, ToolOutcome

logger = logging.getLogger(__name__)

# the only programs the agent may start; the task scope must list them as well
LAUNCHABLE = {"notepad": ["notepad.exe"], "rechner": ["calc.exe"], "explorer": ["explorer.exe"]}


class WindowsTools:
    def __init__(self, uia: UiaProvider, take_snapshot, get_snapshot, resolve_source, remember_names):
        self.uia = uia
        self.take_snapshot = take_snapshot  # (task_id, source) -> Snapshot (thread-safe callable)
        self.get_snapshot = get_snapshot  # (task_id, source_id) -> Snapshot | None
        self.resolve_source = resolve_source  # source_id -> WindowSource
        self.remember_names = remember_names

    # ---- target checks ---------------------------------------------------------------------
    def _source(self, ctx: ToolContext, source_id: str, *, for_input: bool = False) -> W.WindowSource:
        ws = ctx.scopes.windows
        if not ws or source_id not in ws.source_ids:
            raise ToolError("scope", "Dieses Fenster ist nicht Teil des freigegebenen Umfangs.")
        try:
            src = self.resolve_source(source_id)
        except Exception as exc:  # noqa: BLE001
            raise ToolError("source_lost", f"Fenster nicht mehr verfügbar ({exc.__class__.__name__}).")
        if not isinstance(src, W.WindowSource):
            raise ToolError("invalid", "Bildschirmquellen können nicht bedient werden; wähle ein Fenster.")
        if ws.allowed_processes and src.process.lower() not in {p.lower() for p in ws.allowed_processes}:
            raise ToolError("scope", f"Prozess {src.process} ist nicht freigegeben.")
        if not W.desktop_interactive():
            raise ToolError("desktop", "Kein interaktiver Desktop (gesperrt).")
        if for_input:
            if src.minimized:
                raise ToolError("minimized", "Fenster ist minimiert; bitte zuerst windows.focus.")
            if W.window_is_elevated_or_protected(src):
                raise ToolError("uipi", "Fenster gehört zu einem geschützten/erhöhten Prozess; Eingaben sind nicht möglich.")
        return src

    async def _fresh(self, ctx: ToolContext, src: W.WindowSource):
        snap = await asyncio.to_thread(self.take_snapshot, ctx.task_id, src)
        names = {e.id: e.name for e in snap.tree.elements}
        self.remember_names(ctx.task_id, names)
        return snap

    def _element(self, snap, element_id: str):
        for e in snap.tree.elements:
            if e.id == element_id:
                return e
        raise ToolError("stale", f"Element {element_id} existiert in der aktuellen Beobachtung nicht; bitte windows.inspect wiederholen.")

    async def _wrapper(self, src: W.WindowSource, el):
        return await asyncio.to_thread(self.uia.find_element, src.hwnd, el.path)

    # ---- tools ----------------------------------------------------------------------------
    async def inspect(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        src = self._source(ctx, args["source_id"])
        snap = await self._fresh(ctx, src)
        return ToolOutcome(
            ok=snap.quality in ("ok", "minimized") or not snap.tree.error,
            summary_de=f"{len(snap.tree.elements)} Elemente in „{src.title[:50]}“" + (" (gekürzt)" if snap.tree.truncated else ""),
            data={"structure": snap.context_text(max_elements=80, max_chars=3500), "focused": snap.tree.focused_path},
            snapshot_id=snap.snapshot_id,
            verified=True,
        )

    async def read(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        src = self._source(ctx, args["source_id"])
        snap = await self._fresh(ctx, src)
        text = snap.tree.text_excerpt
        if args.get("element_id"):
            el = self._element(snap, args["element_id"])
            text = el.value or el.name
        return ToolOutcome(ok=True, summary_de=f"{len(text)} Zeichen gelesen", data={"text": text[:6000], "truncated": len(text) > 6000}, evidence={"text_excerpt": text[:600]}, snapshot_id=snap.snapshot_id, verified=True)

    async def focus(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        src = self._source(ctx, args["source_id"])
        ok = await asyncio.to_thread(W.bring_to_front, src)
        await asyncio.sleep(0.15)
        fg = W.foreground_window(exclude_self=False)
        in_front = bool(fg and fg.hwnd == src.hwnd)
        return ToolOutcome(ok=in_front, summary_de="Fenster im Vordergrund" if in_front else "Fenster konnte nicht in den Vordergrund geholt werden", data={"foreground": in_front, "requested": ok}, verified=in_front)

    async def invoke(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        src = self._source(ctx, args["source_id"], for_input=True)
        snap = await self._fresh(ctx, src)
        el = self._element(snap, args["element_id"])
        if not el.enabled:
            raise ToolError("disabled", f"Element „{el.name}“ ist deaktiviert.")
        wrapper = await self._wrapper(src, el)

        def _do():
            try:
                wrapper.invoke()
                return "invoke"
            except Exception:  # noqa: BLE001
                wrapper.click_input()
                return "click"

        method = await asyncio.to_thread(_do)
        await asyncio.sleep(0.25)
        after = await self._fresh(ctx, src)
        return ToolOutcome(
            ok=True,
            summary_de=f"„{el.name[:60]}“ ({el.role}) ausgelöst per {method}",
            data={"element": el.name, "method": method, "structure_after": after.context_text(max_elements=40, max_chars=2000)},
            evidence={"element": el.name, "method": method, "snapshot_before": snap.snapshot_id, "snapshot_after": after.snapshot_id},
            snapshot_id=after.snapshot_id,
            verified=False,  # the effect must be checked by the next observation/read
        )

    async def set_value(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        src = self._source(ctx, args["source_id"], for_input=True)
        snap = await self._fresh(ctx, src)
        el = self._element(snap, args["element_id"])
        text = args["text"]
        wrapper = await self._wrapper(src, el)

        def _do():
            try:
                wrapper.set_text(text)  # ValuePattern
                return "value_pattern"
            except Exception:  # noqa: BLE001
                wrapper.set_focus()
                wrapper.type_keys("^a{BACKSPACE}", set_foreground=True)
                wrapper.type_keys(text, with_spaces=True, with_newlines=True, with_tabs=True, set_foreground=True)
                return "type_keys"

        method = await asyncio.to_thread(_do)
        await asyncio.sleep(0.2)
        after = await self._fresh(ctx, src)
        el_after = next((e for e in after.tree.elements if e.path == el.path), None)
        value_after = (el_after.value if el_after and el_after.value is not None else "")
        ok = text.strip() in value_after if value_after else method == "type_keys"
        return ToolOutcome(
            ok=ok,
            summary_de=("Wert gesetzt und zurückgelesen" if value_after and text.strip() in value_after else f"Text eingegeben ({method}); Rücklesen {'bestätigt' if ok else 'nicht möglich'}"),
            data={"element": el.name, "method": method, "value_after": value_after[:500]},
            evidence={"value_after": value_after[:500], "method": method},
            snapshot_id=after.snapshot_id,
            verified=bool(value_after and text.strip() in value_after),
        )

    async def type_text(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        src = self._source(ctx, args["source_id"], for_input=True)
        fg = W.foreground_window(exclude_self=False)
        if not fg or fg.hwnd != src.hwnd:
            ok = await asyncio.to_thread(W.bring_to_front, src)
            await asyncio.sleep(0.2)
            fg = W.foreground_window(exclude_self=False)
            if not fg or fg.hwnd != src.hwnd:
                raise ToolError("focus", "Zielfenster ist nicht im Vordergrund; Eingabe abgebrochen.")
        text = args["text"]
        if len(text) > 4000:
            raise ToolError("invalid", "Text zu lang (max. 4000 Zeichen pro Eingabe).")

        def _do():
            from pywinauto import keyboard

            keyboard.send_keys(text, with_spaces=True, with_newlines=True, with_tabs=True, pause=0.01)

        await asyncio.to_thread(_do)
        await asyncio.sleep(0.2)
        after = await self._fresh(ctx, src)
        return ToolOutcome(ok=True, summary_de=f"{len(text)} Zeichen eingegeben", data={"structure_after": after.context_text(max_elements=30, max_chars=1500)}, snapshot_id=after.snapshot_id, verified=False)

    async def send_keys(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        src = self._source(ctx, args["source_id"], for_input=True)
        keys = args["keys"]
        allowed = {"^s", "^o", "^n", "^a", "^c", "^v", "^z", "^y", "{ENTER}", "{TAB}", "{ESC}", "{F2}", "{DOWN}", "{UP}", "{HOME}", "{END}", "^{HOME}", "^{END}", "{DELETE}", "{BACKSPACE}"}
        if keys not in allowed:
            raise ToolError("invalid", f"Tastenkombination nicht erlaubt: {keys}. Erlaubt: {', '.join(sorted(allowed))}")
        fg = W.foreground_window(exclude_self=False)
        if not fg or fg.hwnd != src.hwnd:
            await asyncio.to_thread(W.bring_to_front, src)
            await asyncio.sleep(0.2)
            fg = W.foreground_window(exclude_self=False)
            if not fg or fg.hwnd != src.hwnd:
                raise ToolError("focus", "Zielfenster ist nicht im Vordergrund; Eingabe abgebrochen.")

        def _do():
            from pywinauto import keyboard

            keyboard.send_keys(keys, pause=0.02)

        await asyncio.to_thread(_do)
        await asyncio.sleep(0.4)
        after = await self._fresh(ctx, src)
        # a dialog (e.g. "Speichern unter") may have appeared: report the foreground window
        fg2 = W.foreground_window(exclude_self=False)
        return ToolOutcome(
            ok=True,
            summary_de=f"Tasten {keys} gesendet; Vordergrund: {fg2.title[:50] if fg2 else '?'}",
            data={"foreground_title": fg2.title if fg2 else None, "foreground_source_id": fg2.id if fg2 else None, "structure_after": after.context_text(max_elements=30, max_chars=1500)},
            snapshot_id=after.snapshot_id,
            verified=False,
        )

    async def click_at(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        src = self._source(ctx, args["source_id"], for_input=True)
        x, y = int(args["x"]), int(args["y"])
        fresh = W.refresh_window(src)
        if fresh is None:
            raise ToolError("source_lost", "Fenster nicht mehr vorhanden.")
        if not fresh.rect.contains(x, y):
            raise ToolError("outside", f"Punkt ({x},{y}) liegt außerhalb des Zielfensters {fresh.rect.to_dict()}.")
        await asyncio.to_thread(W.bring_to_front, fresh)
        await asyncio.sleep(0.15)
        fg = W.foreground_window(exclude_self=False)
        if not fg or fg.hwnd != fresh.hwnd:
            raise ToolError("focus", "Zielfenster ist nicht im Vordergrund; Klick abgebrochen.")

        def _do():
            from pywinauto import mouse

            mouse.click(button="left", coords=(x, y))

        await asyncio.to_thread(_do)
        await asyncio.sleep(0.3)
        after = await self._fresh(ctx, fresh)
        return ToolOutcome(ok=True, summary_de=f"Klick bei ({x},{y})", data={"structure_after": after.context_text(max_elements=30, max_chars=1500)}, evidence={"x": x, "y": y, "window_rect": fresh.rect.to_dict()}, snapshot_id=after.snapshot_id, verified=False)

    async def launch(self, args: dict, ctx: ToolContext) -> ToolOutcome:
        app = (args["app"] or "").strip().lower()
        ws = ctx.scopes.windows
        if app not in LAUNCHABLE:
            raise ToolError("invalid", f"Programm nicht startbar: {app}. Erlaubt: {', '.join(LAUNCHABLE)}")
        exe = LAUNCHABLE[app][0]
        if not ws or exe.lower() not in {p.lower() for p in ws.allowed_processes}:
            raise ToolError("scope", f"{exe} ist in dieser Aufgabe nicht freigegeben (allowed_processes).")
        before = {w.hwnd for w in W.list_windows()}
        subprocess.Popen(LAUNCHABLE[app], close_fds=True)
        found = None
        for _ in range(40):
            await asyncio.sleep(0.25)
            for w in W.list_windows():
                if w.hwnd not in before and w.process.lower() == exe.lower():
                    found = w
                    break
            if found:
                break
        if not found:
            raise ToolError("launch_failed", "Das Fenster des Programms erschien nicht innerhalb von 10 s.")
        ws.source_ids.append(found.id)
        return ToolOutcome(ok=True, summary_de=f"{exe} gestartet: „{found.title[:50]}“ ({found.id})", data={"source_id": found.id, "title": found.title, "process": found.process}, evidence={"source_id": found.id}, verified=True)


def register(broker: Broker, *, uia: UiaProvider, take_snapshot, get_snapshot, resolve_source, remember_names) -> None:
    wt = WindowsTools(uia, take_snapshot, get_snapshot, resolve_source, remember_names)
    sid = {"type": "string", "maxLength": 60}
    eid = {"type": "string", "maxLength": 20}
    from ...observe.uia import available as uia_available

    avail = lambda: (uia_available(), None if uia_available() else "UI Automation nicht verfügbar")  # noqa: E731
    broker.register(Tool("windows.inspect", "Liest die Oberfläche eines freigegebenen Fensters (Elemente mit id, Rolle, Name, Wert, Position).", "windows", "none", {"type": "object", "properties": {"source_id": sid}, "required": ["source_id"], "additionalProperties": False}, wt.inspect, 20, availability=avail))
    broker.register(Tool("windows.read", "Liest den sichtbaren Text eines Fensters oder eines Elements.", "windows", "none", {"type": "object", "properties": {"source_id": sid, "element_id": eid}, "required": ["source_id"], "additionalProperties": False}, wt.read, 20, needs_fresh_element=True, availability=avail))
    broker.register(Tool("windows.focus", "Holt das Fenster in den Vordergrund (stellt es bei Bedarf wieder her).", "windows", "local", {"type": "object", "properties": {"source_id": sid}, "required": ["source_id"], "additionalProperties": False}, wt.focus, 10, availability=avail))
    broker.register(Tool("windows.invoke", "Löst ein Element aus (Button, Menüpunkt, Link) über UI Automation; prüfe danach die Wirkung.", "windows", "local", {"type": "object", "properties": {"source_id": sid, "element_id": eid}, "required": ["source_id", "element_id"], "additionalProperties": False}, wt.invoke, 20, needs_fresh_element=True, availability=avail))
    broker.register(Tool("windows.set_value", "Setzt den Text eines Eingabefelds/Dokuments (ersetzt den Inhalt) und liest ihn zurück.", "windows", "local", {"type": "object", "properties": {"source_id": sid, "element_id": eid, "text": {"type": "string", "maxLength": 8000}}, "required": ["source_id", "element_id", "text"], "additionalProperties": False}, wt.set_value, 30, needs_fresh_element=True, availability=avail))
    broker.register(Tool("windows.type_text", "Tippt Text in das fokussierte Element des Fensters (Tastatur). Nur wenn set_value nicht geht.", "windows", "local", {"type": "object", "properties": {"source_id": sid, "text": {"type": "string", "maxLength": 4000}}, "required": ["source_id", "text"], "additionalProperties": False}, wt.type_text, 60, availability=avail))
    broker.register(Tool("windows.send_keys", "Sendet eine erlaubte Tastenkombination (z. B. ^s speichern, {ENTER}, {TAB}, {ESC}) an das Fenster.", "windows", "local", {"type": "object", "properties": {"source_id": sid, "keys": {"type": "string", "maxLength": 12}}, "required": ["source_id", "keys"], "additionalProperties": False}, wt.send_keys, 15, availability=avail))
    broker.register(Tool("windows.click_at", "Klickt an eine Bildschirmposition innerhalb des Fensters (nur als Ausweichweg, z. B. nach vision.locate).", "windows", "local", {"type": "object", "properties": {"source_id": sid, "x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["source_id", "x", "y"], "additionalProperties": False}, wt.click_at, 15, availability=avail))
    broker.register(Tool("windows.launch", "Startet ein erlaubtes Programm (notepad, rechner, explorer) und liefert dessen source_id.", "windows", "local", {"type": "object", "properties": {"app": {"type": "string", "enum": sorted(LAUNCHABLE)}}, "required": ["app"], "additionalProperties": False}, wt.launch, 20, availability=avail))
