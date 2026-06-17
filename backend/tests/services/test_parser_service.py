"""
Unit tests for ``parser_service`` after the docling → lightweight swap
([](/Spaider-studio/spaider/issues/51) / [#52](/Spaider-studio/spaider/issues/52)).

Strategy
--------
Each parser is exercised against a tiny, real payload — no mocking of the
underlying library. PDF / DOCX use real binary fixtures generated in the
test (no external files needed). HTML / Markdown / TXT exercise the
trafilatura / markdown-it / passthrough paths directly.
"""
from __future__ import annotations

import io

import pytest

from app.services.parser_service import (
    ParseResult,
    UnsupportedParserError,
    parse,
    parse_sync,
    resolve_parser,
)


# ---------------------------------------------------------------------------
# resolve_parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type,filename,expected",
    [
        ("application/pdf", None, "pdf"),
        ("application/pdf; charset=binary", None, "pdf"),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", None, "docx"),
        ("text/html", None, "html"),
        ("application/xhtml+xml", None, "html"),
        ("text/markdown", None, "md"),
        ("text/x-markdown", None, "md"),
        ("text/plain", None, "txt"),
        ("application/json", None, "txt"),     # text/* fallback
        (None, "file.pdf", "pdf"),             # suffix dispatch
        (None, "FILE.HTML", "html"),           # case insensitive
        (None, "notes.md", "md"),
        (None, None, "txt"),                   # last-resort default
        ("application/octet-stream", "x.docx", "docx"),  # MIME unknown → suffix
    ],
)
def test_resolve_parser_table(content_type, filename, expected):
    assert resolve_parser(content_type, filename) == expected


# ---------------------------------------------------------------------------
# Markdown — no external deps
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_parse_markdown_extracts_inline_text_and_counts_headings():
    payload = b"# Title\n\n## Subtitle\n\nHello *world*.\n"
    result = parse_sync(payload, content_type="text/markdown")
    assert isinstance(result, ParseResult)
    assert "Title" in result.text
    assert "Subtitle" in result.text
    assert "Hello" in result.text
    assert result.hints["heading_count"] == 2
    assert result.hints["parser"] == "md"
    assert result.hints["word_count"] > 0


# ---------------------------------------------------------------------------
# Plain text passthrough
# ---------------------------------------------------------------------------


def test_parse_passthrough_decodes_utf8():
    payload = "héllo wörld\n".encode("utf-8")
    result = parse_sync(payload, content_type="text/plain")
    assert result.text == "héllo wörld"
    assert result.hints["parser"] == "txt"


def test_parse_passthrough_for_unknown_mime():
    # text/* falls back to txt; other unknowns also fall back to txt.
    result = parse_sync(b"plain content", content_type="application/json")
    assert result.text == "plain content"
    assert result.hints["parser"] == "txt"


def test_parse_passthrough_resilient_to_invalid_utf8():
    # Latin-1 mojibake bytes — should not raise; replace with U+FFFD.
    payload = b"hello \xff world"
    result = parse_sync(payload)
    assert "hello" in result.text
    assert "world" in result.text


# ---------------------------------------------------------------------------
# HTML — minimal trafilatura test
# ---------------------------------------------------------------------------


def test_parse_html_extracts_article_body():
    html = b"""<html><head><title>SpAIder docs</title></head>
<body>
<nav>nav links should be stripped</nav>
<main><article><p>Trafilatura keeps the body, drops the navigation.</p>
<p>Multiple paragraphs are preserved.</p></article></main>
<footer>boilerplate</footer>
</body></html>"""
    result = parse_sync(html, content_type="text/html")
    assert "Trafilatura keeps the body" in result.text
    assert "boilerplate" not in result.text   # footer should be stripped
    # HTML metadata may or may not be picked up depending on the version, but
    # the parser key should always be set.
    assert result.hints["parser"] == "html"


def test_parse_html_detects_js_fallback_marker():
    """A short body containing 'enable JavaScript' is suspicious and gets
    surfaced via hints['js_fallback'] = True."""
    html = (
        b"<html><body><noscript>You need to enable JavaScript to run this app."
        b"</noscript></body></html>"
    )
    result = parse_sync(html, content_type="text/html")
    assert result.text == ""
    assert result.hints.get("js_fallback") is True
    assert "javascript" in result.hints["js_fallback_preview"].lower()


# ---------------------------------------------------------------------------
# PDF — generate a minimal valid PDF via reportlab if available, else skip
# ---------------------------------------------------------------------------


@pytest.fixture
def small_pdf() -> bytes:
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "Hello SpAIder PDF parser test.")
    c.showPage()
    c.save()
    return buf.getvalue()


def test_parse_pdf_extracts_text(small_pdf: bytes):
    result = parse_sync(small_pdf, content_type="application/pdf")
    assert "Hello SpAIder PDF parser test" in result.text
    assert result.hints["parser"] == "pdf"
    assert result.hints["page_count"] == 1


def test_parse_pdf_garbage_raises_unsupported_parser_error():
    with pytest.raises(UnsupportedParserError):
        parse_sync(b"this is not a pdf", content_type="application/pdf")


# ---------------------------------------------------------------------------
# DOCX — generate a minimal valid DOCX via python-docx
# ---------------------------------------------------------------------------


@pytest.fixture
def small_docx() -> bytes:
    docx_module = pytest.importorskip("docx")  # python-docx
    buf = io.BytesIO()
    doc = docx_module.Document()
    doc.add_paragraph("Hello DOCX parser.")
    doc.add_paragraph("Second paragraph for assertion.")
    doc.save(buf)
    return buf.getvalue()


def test_parse_docx_extracts_paragraphs(small_docx: bytes):
    result = parse_sync(
        small_docx,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert "Hello DOCX parser" in result.text
    assert "Second paragraph" in result.text
    assert result.hints["parser"] == "docx"
    assert result.hints["paragraph_count"] >= 2


# ---------------------------------------------------------------------------
# Async parse() backwards compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_parse_delegates_to_sync():
    """Existing callers do `await parse(content, content_type)`. After the
    swap the entry point is async-with-thread-offload and must still return
    a ParseResult identical to the sync version."""
    payload = "# Heading\n\nHello.".encode("utf-8")
    sync_result = parse_sync(payload, content_type="text/markdown")
    async_result = await parse(payload, content_type="text/markdown")
    assert async_result.text == sync_result.text
    assert async_result.hints["heading_count"] == sync_result.hints["heading_count"]


@pytest.mark.asyncio
async def test_async_parse_accepts_str_content():
    """Some callers pass `str` (legacy interface). It must work."""
    result = await parse("plain text via str", content_type="text/plain")
    assert result.text == "plain text via str"
