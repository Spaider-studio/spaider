from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from spaider_foldable.store.documents import DocumentStore, UnsupportedDocument, split_chunks
from spaider_foldable.tools import Capability, CapabilityGrant, ToolRegistry, ToolResult, ToolSpec, current_host_id

MINIMAL_PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 60 >> stream
BT /F1 18 Tf 20 100 Td (Projekt Orion Budget 42000) Tj ET
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
xref
0 6
0000000000 65535 f
trailer << /Size 6 /Root 1 0 R >>
startxref
0
%%EOF
"""

SCANNED_PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] >> endobj
trailer << /Root 1 0 R >>
%%EOF
"""


def test_split_chunks_overlap():
    text = ("Satz nummer eins. " * 200).strip()
    chunks = split_chunks(text, size=500, overlap=50)
    assert len(chunks) > 3
    assert all(len(c[2]) <= 520 for c in chunks)
    assert chunks[1][0] < chunks[0][1]  # overlap


def test_import_txt_md_pdf_and_original_copy(paths, db, tmp_path):
    ds = DocumentStore(db, paths)
    src = tmp_path / "außen" / "notizen ü.md"
    src.parent.mkdir()
    src.write_text("# Orion\n\nDas Projekt Orion hat ein Budget von 42000 Euro.\n", encoding="utf-8")
    doc, chunks = ds.import_bytes(src.name, src.read_bytes())
    assert doc["content_type"] == "text/markdown" and doc["text_chars"] > 0 and len(chunks) == 1
    src.unlink()  # original outside the package is gone...
    assert ds.text(doc["id"])["text"].startswith("Orion")  # ...but the imported content stays usable
    assert (paths.imports_dir / doc["id"] / "notizen ü.md").exists()
    hit = db.query("SELECT text FROM chunks_fts WHERE chunks_fts MATCH 'budget'")
    assert hit and "42000" in hit[0][0]

    pdf, pchunks = ds.import_bytes("bericht.pdf", MINIMAL_PDF)
    assert pdf["status"] in ("indexing", "indexed") and "Orion" in ds.text(pdf["id"])["text"]
    scanned, schunks = ds.import_bytes("scan.pdf", SCANNED_PDF)
    assert scanned["status"] == "failed" and scanned["warnings"][0]["code"] == "no_text_layer" and not schunks

    with pytest.raises(UnsupportedDocument):
        ds.import_bytes("bild.png", b"\x89PNG")
    evil, _ = ds.import_bytes("../../evil.txt", b"x")  # name is sanitised to a plain file name, never a path
    assert evil["name"] == "evil.txt" and (paths.imports_dir / evil["id"] / "evil.txt").exists()
    assert not (paths.data_dir.parent / "evil.txt").exists()
    ds.delete(doc["id"])
    assert not (paths.imports_dir / doc["id"]).exists()
    assert db.scalar("SELECT count(*) FROM chunks WHERE document_id = ?", (doc["id"],)) == 0


class _Echo:
    spec = ToolSpec(name="echo", description="echo", required_capabilities=[Capability.READ_PROJECT_FILES])

    async def execute(self, call, *, grant, cancel):
        return ToolResult(call_id=call.id, ok=True, output=str(call.arguments))


@pytest.mark.asyncio
async def test_tool_grants_are_host_bound():
    import asyncio

    from spaider_foldable.tools import ToolCall

    reg = ToolRegistry()
    reg.register(_Echo())
    now = datetime.now(timezone.utc)
    foreign = CapabilityGrant(host_id="someone-elses-pc", capabilities=[Capability.READ_PROJECT_FILES],
                              granted_at=now, expires_at=now + timedelta(hours=1))  # fmt: skip
    res = await reg.execute(ToolCall(id="1", tool="echo", arguments={"a": 1}), grant=foreign, cancel=asyncio.Event())
    assert not res.ok and "host" in (res.error or "")
    mine = CapabilityGrant(host_id=current_host_id(), capabilities=[Capability.READ_PROJECT_FILES],
                           granted_at=now, expires_at=now + timedelta(hours=1))  # fmt: skip
    res2 = await reg.execute(ToolCall(id="2", tool="echo", arguments={"a": 1}), grant=mine, cancel=asyncio.Event())
    assert res2.ok and res2.output == "{'a': 1}"
    expired = CapabilityGrant(host_id=current_host_id(), capabilities=[Capability.READ_PROJECT_FILES],
                              granted_at=now - timedelta(hours=2), expires_at=now - timedelta(hours=1))  # fmt: skip
    res3 = await reg.execute(ToolCall(id="3", tool="echo"), grant=expired, cancel=asyncio.Event())
    assert not res3.ok
    assert len(reg.audit) == 3
