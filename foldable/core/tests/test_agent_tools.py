"""Pure-logic checks of the tool adapters: coordinate transform, private-host
rule, path canonicalisation/scope, grounding calibration parsing, read-only
question detection."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from spaider_foldable.agent.loop import _is_read_only_request
from spaider_foldable.agent.tools.browser import _is_private_host
from spaider_foldable.agent.tools.files import _canon, _under
from spaider_foldable.agent.tools.vision import GroundingCalibrator, _parse_point
from spaider_foldable.observe.capture import Frame, PreparedImage, crop_region, frame_hash, hash_distance, model_to_screen, prepare_for_model
from spaider_foldable.observe.windows import Rect, parse_source_id


def test_model_to_screen_with_scale_and_negative_origin():
    # a 2000x1000 window on a monitor left of the primary one (negative x), downscaled to 1280 wide
    frame = Frame(Image.new("RGB", (2000, 1000)), -1920, 40, 0.0, "window", "ok")
    prep = prepare_for_model(frame, max_side=1280)
    assert prep.width == 1280 and prep.scale == pytest.approx(0.64, abs=1e-4)
    # a point in the middle of the model image maps to the middle of the window
    sx, sy = model_to_screen(prep, 640, 320)
    assert (sx, sy) == (-1920 + 1000, 40 + 500)
    # identity when nothing was scaled
    prep2 = PreparedImage(b"", 100, 50, 1.0, 10, 20)
    assert model_to_screen(prep2, 5, 5) == (15, 25)


def test_crop_region_and_hash():
    img = Image.new("RGB", (400, 300), "white")
    for x in range(100, 200):
        for y in range(100, 150):
            img.putpixel((x, y), (0, 0, 0))
    frame = Frame(img, 1000, 500, 0.0, "window", "ok")
    sub = crop_region(frame, Rect(1100, 600, 100, 50), pad=0)
    assert sub.image.size == (100, 50) and sub.origin_x == 1100 and sub.origin_y == 600
    h1 = frame_hash(img)
    img2 = img.copy()
    img2.putpixel((0, 0), (1, 1, 1))  # cursor-sized change
    assert hash_distance(h1, frame_hash(img2)) <= 2
    img3 = Image.new("RGB", (400, 300), "black")
    assert hash_distance(h1, frame_hash(img3)) > 0


def test_source_ids():
    assert parse_source_id("win:1234:88") == ("win", 1234, 88)
    assert parse_source_id("screen:2") == ("screen", 2, 0)
    with pytest.raises(ValueError):
        parse_source_id("desk:1")


def test_private_hosts_blocked():
    for h in ("localhost", "127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.4.4", "::1", "169.254.1.1", "printer.local"):
        assert _is_private_host(h), h
    for h in ("example.com", "8.8.8.8", "huggingface.co"):
        assert not _is_private_host(h), h


def test_path_canon_and_scope(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "a.txt"
    inside.write_text("x", encoding="utf-8")
    assert _under(_canon(str(inside)), [_canon(str(root))])
    assert not _under(_canon(str(tmp_path / "other.txt")), [_canon(str(root))])
    assert not _under(_canon(str(root / ".." / "other.txt")), [_canon(str(root))])
    from spaider_foldable.agent.broker import ToolError

    with pytest.raises(ToolError):
        _canon(r"\\server\share\file.txt")
    with pytest.raises(ToolError):
        _canon(r"\\.\PhysicalDrive0")


def test_grounding_parse_and_calibration_conventions():
    assert _parse_point('```json\n{"x":107,"y":367}\n```') == (107.0, 367.0)
    assert _parse_point("Die Mitte liegt bei (640, 300).") == (640.0, 300.0)
    assert _parse_point("keine Ahnung") is None
    import asyncio

    cal = GroundingCalibrator()
    _, (cx, cy) = cal.image()

    async def normalized(prompt, url):
        return f'{{"x": {round(cx / 1024 * 1000)}, "y": {round(cy / 576 * 1000)}}}'

    async def absolute(prompt, url):
        return f'{{"x": {cx + 3}, "y": {cy - 2}}}'

    async def wrong(prompt, url):
        return '{"x": 5, "y": 5}'

    r = asyncio.run(cal.calibrate("m-norm", normalized))
    assert r["status"] == "verified" and r["convention"] == "normalized_1000"
    r = asyncio.run(cal.calibrate("m-abs", absolute))
    assert r["status"] == "verified" and r["convention"] == "absolute"
    r = asyncio.run(cal.calibrate("m-bad", wrong))
    assert r["status"] == "failed"


def test_read_only_question_detection():
    assert _is_read_only_request("Möchten Sie die Dateien im Ordner lesen, um Informationen zu sammeln?")
    assert _is_read_only_request("Soll ich den Ordner auflisten?")
    assert not _is_read_only_request("Soll ich die Datei löschen?")
    assert not _is_read_only_request("Darf ich die E-Mail senden?")
    assert not _is_read_only_request("Soll ich die Datei speichern und schließen?")


def test_windows_element_lookup_accepts_unique_names():
    """Planners pass the visible name instead of the id (matrix W1): a unique name resolves, an
    ambiguous one is rejected with the candidate ids, an unknown one is stale."""
    from types import SimpleNamespace as NS

    from spaider_foldable.agent.tools.windows import WindowsTools
    from spaider_foldable.agent.broker import ToolError

    els = [NS(id="e1", name="Name", role="Edit"), NS(id="e2", name="Notizen", role="Edit"), NS(id="e3", name="Speichern", role="Button"), NS(id="e4", name="Speichern", role="MenuItem")]
    snap = NS(tree=NS(elements=els))
    wt = WindowsTools(None, None, None, None, None)
    assert wt._element(snap, "e2").id == "e2"
    assert wt._element(snap, "name").id == "e1"
    assert wt._element(snap, " Notizen ").id == "e2"
    try:
        wt._element(snap, "Speichern")
        assert False, "ambiguous name must be rejected"
    except ToolError as exc:
        assert exc.code == "ambiguous" and "e3" in exc.detail
    try:
        wt._element(snap, "Abbrechen")
        assert False
    except ToolError as exc:
        assert exc.code == "stale"
