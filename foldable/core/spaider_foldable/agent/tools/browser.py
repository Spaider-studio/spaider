"""Own browser (Playwright + pinned Chromium) for agent tasks.

* One persistent context per core process under ``data/browser/profile``
  (cookies, downloads, state stay on the stick; the user's own browser is
  never touched).
* Element references: a small page script tags visible interactive elements
  with ``data-spaider-ref`` and returns role/name/value/rect. References are
  only valid for the snapshot that produced them (navigation invalidates them).
* Actions use Playwright locators (auto-waiting/actionability), never ``force``.
* Private/loopback hosts are blocked unless the task allows an origin
  explicitly; the core's own API is never reachable from pages.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..broker import Broker, Tool, ToolContext, ToolError, ToolOutcome

logger = logging.getLogger(__name__)

ELEMENT_NAME_JS = r"""
el => {
  const tag = el.tagName.toLowerCase();
  const role = el.getAttribute('role') || ({a:'link',button:'button',input:(el.type||'text'),select:'combobox',textarea:'textbox',summary:'button',label:'label'}[tag] || tag);
  const lab = el.getAttribute('aria-label') || (el.id && document.querySelector('label[for="' + el.id + '"]') ? document.querySelector('label[for="' + el.id + '"]').innerText : '') || (el.closest && el.closest('label') ? el.closest('label').innerText.replace(el.value || '', '') : '');
  const name = ((lab || '').trim() || el.innerText || el.placeholder || el.getAttribute('title') || el.alt || el.name || el.value || '').trim().slice(0,120);
  return {role, name};
}
"""

SNAPSHOT_JS = r"""
(limit) => {
  const sel = 'a[href],button,input,select,textarea,[role=button],[role=link],[role=textbox],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[contenteditable=true],summary,h1,h2,h3,label';
  const out = [];
  let i = 0;
  const seen = new Set();
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= limit) { break; }
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    const ref = 'b' + (i++);
    el.setAttribute('data-spaider-ref', ref);
    const tag = el.tagName.toLowerCase();
    let role = el.getAttribute('role') || ({a:'link',button:'button',input:(el.type||'text'),select:'combobox',textarea:'textbox',summary:'button',label:'label'}[tag] || tag);
    const lab = el.getAttribute('aria-label') || (el.id && document.querySelector('label[for="' + el.id + '"]') ? document.querySelector('label[for="' + el.id + '"]').innerText : '') || (el.closest && el.closest('label') ? el.closest('label').innerText.replace(el.value || '', '') : '');
    const name = ((lab || '').trim() || el.innerText || el.placeholder || el.getAttribute('title') || el.alt || el.name || el.value || '').trim().slice(0,120);
    let value = null;
    if (tag === 'input' || tag === 'textarea' || tag === 'select') value = (el.value || '').slice(0,200);
    if (tag === 'input' && (el.type === 'checkbox' || el.type === 'radio')) value = el.checked ? 'on' : 'off';
    if (tag === 'input' && el.type === 'password') { value = value ? '•••' : ''; }
    out.push({ref, role, name, value, rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}, enabled: !el.disabled, focused: document.activeElement === el});
  }
  return {elements: out, truncated: document.querySelectorAll(sel).length > limit, title: document.title, url: location.href};
}
"""


def _is_private_host(host: str) -> bool:
    h = (host or "").strip("[]").lower()
    if h in ("localhost", "") or h.endswith(".local") or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        return False


def find_chrome(data_dir: Path) -> Optional[Path]:
    env = os.environ.get("SPAIDER_FOLDABLE_BROWSER_DIR")
    roots = [Path(env)] if env else []
    roots.append(data_dir / "browser")
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.glob("chromium-*/chrome-win*/chrome.exe"), reverse=True):
            return p
        p = root / "chrome-win64" / "chrome.exe"
        if p.exists():
            return p
    return None


class BrowserAdapter:
    def __init__(self, data_dir: Path, chrome_path: Optional[Path]):
        self.data_dir = data_dir
        self.chrome_path = chrome_path
        self._pw = None
        self._ctx = None
        self._page = None
        self.owner_task_id: Optional[str] = None  # the task that opened the current page
        self._lock = asyncio.Lock()
        self._last_refs: set[str] = set()
        self._last_url: Optional[str] = None
        self.version: Optional[str] = None
        self.allowed_origins: Optional[list[str]] = None

    def status(self) -> dict:
        ok = self.chrome_path is not None and self.chrome_path.exists()
        try:
            import playwright  # noqa: F401

            pw_ok = True
        except Exception:  # noqa: BLE001
            pw_ok = False
        return {"installed": ok and pw_ok, "version": self.version, "path": str(self.chrome_path) if self.chrome_path else None, "playwright": pw_ok, "reason_de": None if (ok and pw_ok) else ("Chromium-Paket nicht vorhanden (Einstellungen → Agent → Browser laden)." if pw_ok else "Playwright fehlt im Kern.")}

    def availability(self) -> tuple[bool, Optional[str]]:
        st = self.status()
        return bool(st["installed"]), st["reason_de"]

    async def _ensure(self):
        if self._page is not None and not self._page.is_closed():
            return self._page
        from playwright.async_api import async_playwright

        if self.chrome_path is None:
            raise ToolError("browser_missing", "Kein Browserpaket vorhanden.")
        profile = self.data_dir / "browser" / "profile"
        profile.mkdir(parents=True, exist_ok=True)
        downloads = self.data_dir / "browser" / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        if self._pw is None:
            self._pw = await async_playwright().start()
        self._ctx = await self._pw.chromium.launch_persistent_context(
            str(profile),
            executable_path=str(self.chrome_path),
            headless=False,
            accept_downloads=True,
            downloads_path=str(downloads),
            viewport={"width": 1200, "height": 800},
            args=["--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-sync", "--remote-debugging-pipe"] if False else ["--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-sync"],
            ignore_default_args=["--enable-automation"],
        )
        try:
            self.version = self._ctx.browser.version if self._ctx.browser else None
        except Exception:  # noqa: BLE001
            self.version = None
        await self._ctx.route("**/*", self._route)
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        self._page.on("framenavigated", lambda f: self._invalidate() if f == self._page.main_frame else None)
        return self._page

    def _invalidate(self) -> None:
        self._last_refs = set()

    async def _route(self, route, request):
        url = urlparse(request.url)
        if url.scheme in ("http", "https"):
            host = url.hostname or ""
            if _is_private_host(host):
                origin = f"{url.scheme}://{url.hostname}" + (f":{url.port}" if url.port else "")
                if not (self.allowed_origins and origin in self.allowed_origins):
                    await route.abort("blockedbyclient")
                    return
        await route.continue_()

    async def close(self) -> None:
        try:
            if self._ctx is not None:
                await self._ctx.close()
            if self._pw is not None:
                await self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._ctx = self._page = self._pw = None

    # ---- observation ---------------------------------------------------------------------
    async def snapshot(self, limit: int = 120) -> dict:
        page = await self._ensure()
        try:
            data = await page.evaluate(SNAPSHOT_JS, limit)
        except Exception as exc:  # noqa: BLE001
            raise ToolError("page", f"Seite nicht lesbar: {str(exc)[:160]}")
        self._last_refs = {e["ref"] for e in data["elements"]}
        self._last_url = data["url"]
        text = ""
        try:
            text = await page.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 6000)")
        except Exception:  # noqa: BLE001
            pass
        data["text"] = text
        return data

    def owned_by(self, task_id: str) -> bool:
        return self._page is not None and not self._page.is_closed() and self.owner_task_id == task_id

    async def observe_text(self, task_id: str) -> str:
        if not self.owned_by(task_id):
            # never show another task's page as this task's observation
            return "Browser: für diese Aufgabe ist noch keine Seite geöffnet (zuerst browser.open mit der Zieladresse verwenden)."
        d = await self.snapshot(limit=60)
        lines = [f"Browser: {d['title'][:80]!r} {d['url'][:120]}" + (" (Elemente gekürzt)" if d["truncated"] else "")]
        for e in d["elements"][:60]:
            val = f", value={e['value'][:60]!r}" if e.get("value") else ""
            lines.append(f"- ref={e['ref']} role={e['role']} name={e['name'][:80]!r}{val}{' focused' if e['focused'] else ''}")
        lines.append("Seitentext (Auszug): " + d["text"][:1200].replace("\n", " "))
        return "\n".join(lines)

    def _locator(self, ref: str):
        if ref not in self._last_refs:
            raise ToolError("stale", f"Referenz {ref} stammt nicht aus der aktuellen Seite; bitte browser.snapshot wiederholen.")
        return self._page.locator(f'[data-spaider-ref="{ref}"]')

    # ---- actions -------------------------------------------------------------------------
    async def open(self, url: str, allowed_origins: Optional[list[str]], task_id: Optional[str] = None) -> dict:
        self.allowed_origins = allowed_origins
        self.owner_task_id = task_id
        u = urlparse(url)
        if u.scheme not in ("http", "https", "file"):
            raise ToolError("invalid", "Nur http(s)- oder file-Adressen.")
        if u.scheme == "file":
            p = Path(url[7:].lstrip("/")) if os.name == "nt" else Path(u.path)
            roots = [Path(r) for r in (allowed_origins or []) if not r.startswith("http")]
            if not any(_under(p, r) for r in roots):
                raise ToolError("scope", "Lokale Dateien nur aus freigegebenen Ordnern (allowed_origins mit Ordnerpfaden).")
        elif _is_private_host(u.hostname or ""):
            origin = f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
            if not (allowed_origins and origin in allowed_origins):
                raise ToolError("scope", "Lokale/private Adressen sind gesperrt, außer sie sind für diese Aufgabe freigegeben.")
        page = await self._ensure()
        t0 = time.monotonic()
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._invalidate()
        status = resp.status if resp else None
        return {"url": page.url, "title": await page.title(), "status": status, "ms": int((time.monotonic() - t0) * 1000)}

    async def _describe(self, loc) -> dict:
        """Role + accessible name of an element, computed like the snapshot does (label, aria-label,
        text, placeholder, name) - recorded workflows target elements by this name, never by ref."""
        try:
            return await loc.evaluate(ELEMENT_NAME_JS, timeout=2000)
        except Exception:  # noqa: BLE001
            return {"role": "", "name": ""}

    async def click(self, ref: str) -> dict:
        loc = self._locator(ref)
        desc = await self._describe(loc) if await loc.count() else {"role": "", "name": ""}
        name = desc.get("name") or ((await loc.inner_text(timeout=2000)) if await loc.count() else "")
        await loc.click(timeout=8000)
        await self._page.wait_for_load_state("domcontentloaded", timeout=10000)
        await asyncio.sleep(0.15)
        return {"clicked": name[:80], "name": name[:120], "role": desc.get("role", ""), "url": self._page.url, "title": await self._page.title(), "text_after": await self._body_text(900)}

    async def fill(self, ref: str, text: str) -> dict:
        loc = self._locator(ref)
        desc = await self._describe(loc)
        await loc.fill(text, timeout=8000)
        value = await loc.input_value(timeout=2000)
        return {"value_after": value[:300], "verified": value == text, "name": desc.get("name", ""), "role": desc.get("role", "")}

    async def select(self, ref: str, value: str) -> dict:
        loc = self._locator(ref)
        desc = await self._describe(loc)
        chosen = await loc.select_option(value, timeout=8000)
        got = await loc.input_value(timeout=2000)
        return {"selected": chosen, "value_after": got, "verified": bool(chosen), "name": desc.get("name", ""), "role": desc.get("role", ""), "text_after": await self._body_text(600)}

    async def press(self, ref: Optional[str], key: str) -> dict:
        if key not in ("Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "PageDown", "PageUp"):
            raise ToolError("invalid", "Taste nicht erlaubt.")
        if ref:
            await self._locator(ref).press(key, timeout=5000)
        else:
            await self._page.keyboard.press(key)
        await self._page.wait_for_load_state("domcontentloaded", timeout=10000)
        return {"url": self._page.url, "title": await self._page.title(), "text_after": await self._body_text(900)}

    async def _body_text(self, n: int) -> str:
        try:
            t = await self._page.evaluate("() => (document.body ? document.body.innerText : '')")
            return t[:n]
        except Exception:  # noqa: BLE001
            return ""

    async def scroll(self, direction: str) -> dict:
        dy = 600 if direction == "down" else -600
        await self._page.mouse.wheel(0, dy)
        await asyncio.sleep(0.2)
        return {"scrolled": direction}

    async def wait_for_text(self, text: str, timeout_ms: int) -> dict:
        t0 = time.monotonic()
        try:
            await self._page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=timeout_ms)
            return {"found": True, "ms": int((time.monotonic() - t0) * 1000)}
        except Exception:  # noqa: BLE001
            return {"found": False, "ms": int((time.monotonic() - t0) * 1000)}

    async def read(self, max_chars: int) -> dict:
        page = await self._ensure()
        text = await page.evaluate("() => (document.body ? document.body.innerText : '')")
        return {"url": page.url, "title": await page.title(), "text": text[:max_chars], "total_chars": len(text), "truncated": len(text) > max_chars}

    async def back(self) -> dict:
        await self._page.go_back(wait_until="domcontentloaded", timeout=15000)
        self._invalidate()
        return {"url": self._page.url, "title": await self._page.title()}


def _under(p: Path, root: Path) -> bool:
    try:
        Path(os.path.realpath(p)).relative_to(Path(os.path.realpath(root)))
        return True
    except ValueError:
        return False


def register(broker: Broker, adapter: BrowserAdapter) -> None:
    ref = {"type": "string", "pattern": "^b[0-9]{1,4}$", "maxLength": 6}  # exactly the ref from browser.snapshot (b0, b1, …), never a field name

    def _origins(ctx: ToolContext):
        return (ctx.scopes.browser.allowed_origins if ctx.scopes.browser else None)

    def _own(ctx: ToolContext) -> None:
        if not adapter.owned_by(ctx.task_id):
            raise ToolError("no_page", "Für diese Aufgabe ist noch keine Seite geöffnet; zuerst browser.open mit der Zieladresse aufrufen.")

    async def t_open(args, ctx):
        d = await adapter.open(args["url"], _origins(ctx), task_id=ctx.task_id)
        return ToolOutcome(ok=True, summary_de=f"Seite geöffnet: {d['title'][:60]} ({d['status']})", data=d, evidence={"url": d["url"], "status": d["status"]}, verified=d["status"] is None or d["status"] < 400)

    async def t_snapshot(args, ctx):
        _own(ctx)
        d = await adapter.snapshot(limit=int(args.get("max_elements") or 120))
        elements = [{"ref": e["ref"], "role": e["role"], "name": e["name"][:80], **({"value": e["value"][:60]} if e.get("value") else {}), **({"focused": True} if e["focused"] else {})} for e in d["elements"]]
        return ToolOutcome(ok=True, summary_de=f"{len(d['elements'])} Elemente auf „{d['title'][:50]}“", data={"url": d["url"], "title": d["title"], "elements": elements, "hint": "Für click/fill/select/press genau den Wert aus 'ref' verwenden (z. B. b3).", "truncated": d["truncated"], "text": d["text"][:2500]}, evidence={"url": d["url"], "title": d["title"], "text_excerpt": d["text"][:600]}, verified=True)

    async def t_click(args, ctx):
        _own(ctx)
        d = await adapter.click(args["ref"])
        return ToolOutcome(ok=True, summary_de=f"Geklickt: {d['clicked'][:40]!r} → {d['title'][:50]}", data=d, evidence={"url": d["url"], "title": d["title"], "text_excerpt": d.get("text_after", "")[:600]}, verified=False)

    async def t_fill(args, ctx):
        _own(ctx)
        d = await adapter.fill(args["ref"], args["text"])
        return ToolOutcome(ok=d["verified"], summary_de=("Feld gefüllt und zurückgelesen" if d["verified"] else "Feldwert weicht ab"), data=d, evidence=d, verified=d["verified"])

    async def t_select(args, ctx):
        _own(ctx)
        d = await adapter.select(args["ref"], args["value"])
        return ToolOutcome(ok=d["verified"], summary_de="Option gewählt" if d["verified"] else "Option nicht gefunden", data=d, verified=d["verified"])

    async def t_press(args, ctx):
        _own(ctx)
        d = await adapter.press(args.get("ref"), args["key"])
        return ToolOutcome(ok=True, summary_de=f"Taste {args['key']} gesendet", data=d, evidence={"url": d["url"], "text_excerpt": d.get("text_after", "")[:600]}, verified=False)

    async def t_scroll(args, ctx):
        _own(ctx)
        return ToolOutcome(ok=True, summary_de="gescrollt", data=await adapter.scroll(args["direction"]), verified=True)

    async def t_wait(args, ctx):
        _own(ctx)
        d = await adapter.wait_for_text(args["text"], int(args.get("timeout_ms") or 10000))
        return ToolOutcome(ok=d["found"], summary_de=("Text erschienen" if d["found"] else "Text nicht erschienen"), data=d, evidence={"text": args["text"], "found": d["found"]}, verified=d["found"])

    async def t_read(args, ctx):
        _own(ctx)
        d = await adapter.read(int(args.get("max_chars") or 4000))
        return ToolOutcome(ok=True, summary_de=f"{len(d['text'])} Zeichen gelesen", data=d, evidence={"url": d["url"], "title": d["title"], "text_excerpt": d["text"][:600]}, verified=True)

    async def t_back(args, ctx):
        _own(ctx)
        return ToolOutcome(ok=True, summary_de="zurück", data=await adapter.back(), verified=False)

    av = adapter.availability
    broker.register(Tool("browser.open", "Öffnet eine Adresse im eigenen Browser (http/https; lokale Adressen nur mit Freigabe).", "browser", "local", {"type": "object", "properties": {"url": {"type": "string", "minLength": 5, "maxLength": 2000}}, "required": ["url"], "additionalProperties": False}, t_open, 45, availability=av))
    broker.register(Tool("browser.snapshot", "Liest die aktuelle Seite: Elemente als Objekte mit ref (b0, b1, …), role, name, value und einen Textauszug. Die ref-Kennung (nicht der Feldname!) wird für click/fill/select verwendet; Refs gelten bis zur nächsten Navigation.", "browser", "none", {"type": "object", "properties": {"max_elements": {"type": "integer", "minimum": 10, "maximum": 300}}, "additionalProperties": False}, t_snapshot, 30, availability=av))
    broker.register(Tool("browser.click", "Klickt ein Element (ref aus browser.snapshot); wartet auf Bedienbarkeit.", "browser", "local", {"type": "object", "properties": {"ref": ref}, "required": ["ref"], "additionalProperties": False}, t_click, 30, needs_fresh_element=True, availability=av))
    broker.register(Tool("browser.fill", "Füllt ein Eingabefeld mit Text und liest den Wert zurück. ref = Kennung aus browser.snapshot (z. B. b2).", "browser", "local", {"type": "object", "properties": {"ref": ref, "text": {"type": "string", "maxLength": 4000}}, "required": ["ref", "text"], "additionalProperties": False}, t_fill, 30, needs_fresh_element=True, availability=av))
    broker.register(Tool("browser.select", "Wählt eine Option in einem Auswahlfeld (ref) per Wert oder Beschriftung.", "browser", "local", {"type": "object", "properties": {"ref": ref, "value": {"type": "string", "maxLength": 200}}, "required": ["ref", "value"], "additionalProperties": False}, t_select, 30, needs_fresh_element=True, availability=av))
    broker.register(Tool("browser.press", "Sendet eine Taste (Enter, Tab, Escape, ArrowDown, ArrowUp, PageDown, PageUp) an ein Element oder die Seite.", "browser", "local", {"type": "object", "properties": {"ref": ref, "key": {"type": "string", "enum": ["Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "PageDown", "PageUp"]}}, "required": ["key"], "additionalProperties": False}, t_press, 20, availability=av))
    broker.register(Tool("browser.scroll", "Scrollt die Seite nach unten oder oben.", "browser", "none", {"type": "object", "properties": {"direction": {"type": "string", "enum": ["down", "up"]}}, "required": ["direction"], "additionalProperties": False}, t_scroll, 10, availability=av))
    broker.register(Tool("browser.wait_for_text", "Wartet, bis ein Text auf der Seite sichtbar ist (Ergebnisprüfung nach Aktionen).", "browser", "none", {"type": "object", "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 200}, "timeout_ms": {"type": "integer", "minimum": 500, "maximum": 30000}}, "required": ["text"], "additionalProperties": False}, t_wait, 40, availability=av))
    broker.register(Tool("browser.read", "Liest den sichtbaren Seitentext (Auszug).", "browser", "none", {"type": "object", "properties": {"max_chars": {"type": "integer", "minimum": 200, "maximum": 12000}}, "additionalProperties": False}, t_read, 20, availability=av))
    broker.register(Tool("browser.back", "Geht eine Seite zurück.", "browser", "local", {"type": "object", "properties": {}, "additionalProperties": False}, t_back, 20, availability=av))
