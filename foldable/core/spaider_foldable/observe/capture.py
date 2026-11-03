"""Screen/window capture (Win32 PrintWindow or GDI screen copy via mss),
image preparation for vision models and change detection.

* ``capture_window``: PrintWindow with PW_RENDERFULLCONTENT (works for most
  DWM/Chromium windows even when partially covered); falls back to a screen
  crop of the window rectangle. Minimized windows cannot be captured.
* ``capture_screen``: one monitor via mss. SpAIder's own overlay is excluded by
  the window's display affinity (set by the launcher), not by this module.
* ``prepare_for_model``: downscale to a token budget friendly size while
  keeping the scale factor so model coordinates map back to screen pixels.
* ``frame_hash``: 8x8 difference hash on a greyscale thumbnail; equal hashes
  mean "nothing visible changed" (blinking cursors do not flip it).
"""

from __future__ import annotations

import base64
import ctypes
import io
import logging
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

from PIL import Image

from .windows import IS_WIN, Rect, WindowSource, ensure_dpi_awareness, monitors, same_window

logger = logging.getLogger(__name__)

if IS_WIN:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
else:  # pragma: no cover
    user32 = gdi32 = None


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


@dataclass
class Frame:
    image: Image.Image  # RGB, physical pixels
    origin_x: int  # screen coordinate of the image's top-left pixel
    origin_y: int
    captured_at: float  # time.monotonic()
    source_kind: str  # window | screen
    quality: str  # ok | black | minimized | lost | error
    method: str = ""
    error: Optional[str] = None

    @property
    def rect(self) -> Rect:
        return Rect(self.origin_x, self.origin_y, self.image.width, self.image.height)


def _dib_to_image(hdc_mem, hbmp, w: int, h: int) -> Image.Image:
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h  # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).copy()


def capture_window(src: WindowSource) -> Frame:
    """PrintWindow capture of one top-level window (physical pixels)."""
    now = time.monotonic()
    if not IS_WIN:
        return Frame(Image.new("RGB", (1, 1)), 0, 0, now, "window", "error", error="not windows")
    ensure_dpi_awareness()
    ok, state = same_window(src)
    if not ok:
        return Frame(Image.new("RGB", (1, 1)), 0, 0, now, "window", "lost", error="Fenster nicht mehr vorhanden")
    if state == "minimized":
        return Frame(Image.new("RGB", (1, 1)), src.rect.x, src.rect.y, now, "window", "minimized", error="Fenster ist minimiert")
    r = RECT_()
    user32.GetWindowRect(src.hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return Frame(Image.new("RGB", (1, 1)), r.left, r.top, now, "window", "error", error="leere Fenstergröße")
    hdc_win = user32.GetWindowDC(src.hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    method = "printwindow"
    try:
        PW_RENDERFULLCONTENT = 0x00000002
        res = user32.PrintWindow(src.hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        img = _dib_to_image(hdc_mem, hbmp, w, h)
        if not res or _is_black(img):
            # fall back to a screen copy of the window rectangle (what the user sees)
            method = "screen-crop"
            img = _screen_crop(Rect(r.left, r.top, w, h))
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(src.hwnd, hdc_win)
    # crop the invisible DWM borders so the image matches the extended frame bounds
    fr = src.rect
    if fr.w > 0 and fr.h > 0 and (fr.x != r.left or fr.y != r.top or fr.w != w or fr.h != h):
        left = max(0, fr.x - r.left)
        top = max(0, fr.y - r.top)
        img = img.crop((left, top, min(w, left + fr.w), min(h, top + fr.h)))
        ox, oy = fr.x, fr.y
    else:
        ox, oy = r.left, r.top
    quality = "black" if _is_black(img) else "ok"
    return Frame(img, ox, oy, now, "window", quality, method=method)


class RECT_(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _screen_crop(rect: Rect) -> Image.Image:
    import mss

    with mss.MSS() as s:
        shot = s.grab({"left": rect.x, "top": rect.y, "width": rect.w, "height": rect.h})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def capture_screen(monitor_id: str) -> Frame:
    now = time.monotonic()
    if not IS_WIN:
        return Frame(Image.new("RGB", (1, 1)), 0, 0, now, "screen", "error", error="not windows")
    ensure_dpi_awareness()
    mons = {m.id: m for m in monitors()}
    m = mons.get(monitor_id)
    if m is None:
        return Frame(Image.new("RGB", (1, 1)), 0, 0, now, "screen", "lost", error="Bildschirm nicht gefunden")
    try:
        img = _screen_crop(m.rect)
    except Exception as exc:  # noqa: BLE001
        return Frame(Image.new("RGB", (1, 1)), m.rect.x, m.rect.y, now, "screen", "error", error=str(exc)[:200])
    return Frame(img, m.rect.x, m.rect.y, now, "screen", "black" if _is_black(img) else "ok", method="mss")


def _is_black(img: Image.Image, threshold: int = 8) -> bool:
    small = img.convert("L").resize((16, 16))
    return max(small.getdata()) <= threshold


def frame_hash(img: Image.Image) -> str:
    """Difference hash (8x8) on a greyscale thumbnail → 16 hex chars."""
    g = img.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
    px = list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left = px[row * 9 + col]
            right = px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hash_distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


@dataclass
class PreparedImage:
    png: bytes
    width: int
    height: int
    scale: float  # model image px * (1/scale) = frame px
    origin_x: int
    origin_y: int

    def to_data_url(self) -> str:
        return "data:image/png;base64," + base64.b64encode(self.png).decode("ascii")


def prepare_for_model(frame: Frame, max_side: int = 1280, max_pixels: int = 1_000_000) -> PreparedImage:
    """Downscale for the vision encoder; keep the factor for coordinate mapping."""
    img = frame.image
    w, h = img.size
    scale = 1.0
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
    if w * h * scale * scale > max_pixels:
        scale = min(scale, (max_pixels / (w * h)) ** 0.5)
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=3)
    return PreparedImage(buf.getvalue(), img.width, img.height, round(scale, 5), frame.origin_x, frame.origin_y)


def preview_png(frame: Frame, max_width: int = 480) -> bytes:
    img = frame.image
    if img.width > max_width:
        img = img.resize((max_width, max(1, int(img.height * max_width / img.width))), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=4)
    return buf.getvalue()


def crop_region(frame: Frame, rect: Rect, pad: int = 8) -> Frame:
    """Sub-region of a frame in screen coordinates (for 'scharf nachladen')."""
    left = max(0, rect.x - frame.origin_x - pad)
    top = max(0, rect.y - frame.origin_y - pad)
    right = min(frame.image.width, rect.x - frame.origin_x + rect.w + pad)
    bottom = min(frame.image.height, rect.y - frame.origin_y + rect.h + pad)
    img = frame.image.crop((left, top, max(left + 1, right), max(top + 1, bottom)))
    return Frame(img, frame.origin_x + left, frame.origin_y + top, frame.captured_at, frame.source_kind, frame.quality, frame.method)


def model_to_screen(prepared: PreparedImage, mx: float, my: float) -> tuple[int, int]:
    """Model image coordinates → physical screen pixels (tested function; see tests)."""
    return int(round(prepared.origin_x + mx / prepared.scale)), int(round(prepared.origin_y + my / prepared.scale))
