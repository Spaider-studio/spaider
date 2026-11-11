"""Hybrid retrieval over the local memory: FTS5 (BM25) + optional vectors,
fused with SpAIder's ``_rrf_fuse``; 1-hop graph expansion; context built with
``QueryService._build_context``; superseded and uncertain facts annotated;
``Source`` records for the UI."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.models.schemas import GraphPayload, Node
from app.services.query_service import QueryService, _rrf_fuse

from ..db import Database
from .embedding_local import LocalEmbeddingService
from .store import LocalMemoryStore, fts_query

logger = logging.getLogger(__name__)

_UNCERTAIN_BELOW = 0.6


@dataclass
class RetrievedContext:
    context_text: str
    sources: list[dict] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)
    approx_chars: int = 0


def _short_ts(ts: str | None) -> str:
    """'2026-11-02T09:41:12.345Z' -> '2026-11-02 09:41'."""
    if not ts:
        return "unbekannt"
    return ts[:16].replace("T", " ")

    return 0.0


FACT_CONTEXT_CHARS = 480  # per fact inside the context block (sources keep 400-char excerpts separately)


def _reverse_ts(ts: str | None) -> str:
    """Sort key that orders ISO timestamps newest-first with a plain ascending sort."""
    return "".join(chr(0x10FFFF - ord(ch)) for ch in (ts or ""))


def _origin(row: dict) -> dict:
    return {
        "kind": row.get("origin_kind") or "conversation",
        "id": row.get("origin_id") or "",
        "title": (row.get("origin_title") or "") + (" (gelöscht)" if row.get("origin_deleted") else ""),
        "at": row.get("created_at") or "",
    }


def _fact_status(row: dict) -> str:
    if row.get("superseded"):
        return "superseded"
    if float(row.get("confidence") or 1.0) < _UNCERTAIN_BELOW:
        return "uncertain"
    return "current"


class MemoryRetriever:
    def __init__(self, db: Database, store: LocalMemoryStore, embeddings: LocalEmbeddingService):
        self.db = db
        self.store = store
        self.embeddings = embeddings

    async def retrieve(
        self,
        query: str,
        *,
        max_chars: int = 6000,
        top_k: int = 10,
        exclude_fact_ids: Optional[set[str]] = None,
        origin_allowed: Optional[Callable[[str, str], bool]] = None,
    ) -> RetrievedContext:
        """``origin_allowed(kind, id)`` drops nodes whose origin must not leave the
        machine (facts from „Nur lokal“ projects when the answer goes to a cloud
        connection); it is applied before any text is assembled."""
        query = (query or "").strip()
        if not query:
            return RetrievedContext(context_text="")
        ranked: list[list[Node]] = []
        fts_nodes = self.store.fulltext_nodes(query, limit=top_k * 2)
        if fts_nodes:
            ranked.append(fts_nodes)
        if self.embeddings.available:
            try:
                qvec = await self.embeddings.embed_query(query)
                vec_nodes = await self.store.vector_search(qvec, self.store.agent_id, top_k=top_k * 2)
                if vec_nodes:
                    ranked.append(vec_nodes)
            except Exception as exc:  # noqa: BLE001
                logger.warning("vector retrieval failed: %s", exc)
        fused = _rrf_fuse(ranked)[:top_k] if ranked else []
        if exclude_fact_ids:
            fused = [n for n in fused if n.id not in exclude_fact_ids]

        # Graph expansion: entity nodes -> their relations and facts.
        seed_ids = [n.id for n in fused]
        edges = self.store.edges_for_nodes(seed_ids, limit=120)
        neighbour_ids = {e.source_id for e in edges} | {e.target_id for e in edges}
        all_ids = list(dict.fromkeys(seed_ids + [i for i in neighbour_ids if i not in seed_ids]))
        rows = self.store.get_nodes(all_ids)
        nodes: list[Node] = []
        if origin_allowed is not None:
            rows = {k: r for k, r in rows.items() if origin_allowed(r["origin_kind"] or "", r["origin_id"] or "")}
        for nid in all_ids:
            r = rows.get(nid)
            if r is None or r["superseded"] or (exclude_fact_ids and nid in exclude_fact_ids):
                continue
            description = r["description"]
            if r["type"] == "FACT" and description:
                # Small models need the chronology spelled out: prefix each fact
                # with its storage time so "the newer statement wins" is decidable.
                pending = " · Nachverarbeitung ausstehend" if r.get("processing") == "raw" else ""
                if len(description) > FACT_CONTEXT_CHARS:
                    # long pasted messages are stored verbatim as facts; the context block
                    # gets their head only (prompt tokens cost ~15 ms each on a CPU)
                    description = description[:FACT_CONTEXT_CHARS].rstrip() + " …"
                description = f"[gespeichert {_short_ts(r['created_at'])}{pending}] {description}"
            nodes.append(
                Node(
                    id=r["id"],
                    label=r["label"],
                    type=r["type"],
                    description=description,
                    properties=json.loads(r["properties_json"] or "{}"),
                    agent_id=r["agent_id"],
                )
            )
        kept_ids = {n.id for n in nodes}
        edges = [e for e in edges if e.source_id in kept_ids and e.target_id in kept_ids and e.relation != "MENTIONS"]
        # Facts first, newest first (full timestamp, not the minute shown in the prefix).
        nodes.sort(key=lambda n: (n.type != "FACT", "" if n.type != "FACT" else _reverse_ts(rows[n.id]["created_at"])))
        subgraph = GraphPayload(nodes=nodes[:40], edges=edges)
        graph_context = QueryService._build_context(subgraph) if nodes else ""

        # Sources: facts behind the entities + the facts themselves.
        entity_ids = [n.id for n in nodes if n.type != "FACT"]
        fact_rows = [r for r in (rows[i] for i in all_ids if i in rows) if r["type"] == "FACT" and not r["superseded"]]
        for r in self.store.facts_mentioning(entity_ids, limit=10):
            if r["id"] not in {f["id"] for f in fact_rows}:
                if origin_allowed is not None and not origin_allowed(r["origin_kind"] or "", r["origin_id"] or ""):
                    continue
                fact_rows.append(r)
        sources: list[dict] = []
        for r in fact_rows[:12]:
            sources.append(
                {
                    "kind": "fact",
                    "id": r["id"],
                    "label": (r["label"] or "")[:120],
                    "excerpt": (r["description"] or "")[:400],
                    "status": _fact_status(r),
                    "origin": _origin(r),
                    "superseded_by": None,
                    "processing": "pending" if r.get("processing") == "raw" else "done",
                }
            )

        # Older, replaced statements about the same entities (shown as history).
        history_lines: list[str] = []
        for e in self.store.superseded_edges_for_nodes(entity_ids, limit=6):
            if origin_allowed is not None and not origin_allowed(e.get("origin_kind") or "", e.get("origin_id") or ""):
                continue
            props = json.loads(e.get("properties_json") or "{}")
            text = props.get("source_text") or props.get("description") or f"{e['subj']} {e['relation']} {e['obj']}"
            history_lines.append(f"• [veraltet, ersetzt] {text[:200]}")
            sources.append(
                {
                    "kind": "fact",
                    "id": e["id"],
                    "label": f"{e['subj']} {e['relation']} {e['obj']}"[:120],
                    "excerpt": text[:400],
                    "status": "superseded",
                    "origin": {
                        "kind": e.get("origin_kind") or "conversation",
                        "id": e.get("origin_id") or "",
                        "title": "",
                        "at": e.get("created_at") or "",
                    },
                    "superseded_by": {"id": e.get("superseded_by") or "", "excerpt": ""},
                }
            )

        for r in self.store.superseded_facts_for_nodes(entity_ids, limit=5):
            if any(s["id"] == r["id"] for s in sources):
                continue
            history_lines.append(f"• [veraltet, ersetzt] {(r['description'] or '')[:200]}")
            sources.append(
                {
                    "kind": "fact",
                    "id": r["id"],
                    "label": (r["label"] or "")[:120],
                    "excerpt": (r["description"] or "")[:400],
                    "status": "superseded",
                    "origin": _origin(r),
                    "superseded_by": {"id": r.get("superseded_by") or "", "excerpt": ""},
                }
            )

        # Notes and document chunks (fulltext only).
        note_lines, chunk_lines = [], []
        q = fts_query(query)
        if q:
            for r in self.db.query(
                "SELECT n.id, n.text, n.created_at FROM notes_fts f JOIN notes n ON n.rowid = f.rowid "
                "WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) LIMIT 5",
                (q,),
            ):
                note_lines.append(f"• {r['text'][:300]}")
                sources.append(
                    {
                        "kind": "note",
                        "id": r["id"],
                        "label": r["text"][:80],
                        "excerpt": r["text"][:400],
                        "status": "current",
                        "origin": {"kind": "note", "id": r["id"], "title": "Erinnerung", "at": r["created_at"]},
                        "superseded_by": None,
                    }
                )
            for r in self.db.query(
                "SELECT ch.id, ch.text, ch.ord, d.id AS document_id, d.name, d.created_at FROM chunks_fts f "
                "JOIN chunks ch ON ch.rowid = f.rowid JOIN documents d ON d.id = ch.document_id "
                "WHERE chunks_fts MATCH ? AND d.deleted_at IS NULL ORDER BY bm25(chunks_fts) LIMIT 4",
                (q,),
            ):
                chunk_lines.append(f"• Dokument „{r['name']}“, Abschnitt {r['ord'] + 1}: {r['text'][:700]}")
                sources.append(
                    {
                        "kind": "document",
                        "id": r["id"],
                        "label": f"{r['name']} (Abschnitt {r['ord'] + 1})",
                        "excerpt": r["text"][:400],
                        "status": "current",
                        "origin": {
                            "kind": "document",
                            "id": r["document_id"],
                            "title": r["name"],
                            "at": r["created_at"],
                        },
                        "superseded_by": None,
                    }
                )

        parts: list[str] = []
        if graph_context:
            parts.append(
                "Gespeichertes Wissen (Fakten und Beziehungen; [Strength] = häufig bestätigt):\n" + graph_context
            )
        if history_lines:
            parts.append(
                "Ältere, inzwischen ersetzte Angaben (nur zur Einordnung, nicht mehr gültig):\n"
                + "\n".join(history_lines)
            )
        if note_lines:
            parts.append("Persönliche Erinnerungen des Nutzers:\n" + "\n".join(note_lines))
        if chunk_lines:
            parts.append("Auszüge aus importierten Dokumenten:\n" + "\n".join(chunk_lines))
        text = "\n\n".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…(gekürzt)"
        uncertain = [s for s in sources if s["status"] == "uncertain"]
        if uncertain:
            text += "\n\nHinweis: Mit „unsicher“ markierte Angaben haben eine niedrige Extraktionssicherheit."
        return RetrievedContext(
            context_text=text, sources=sources, node_ids=[n.id for n in nodes], approx_chars=len(text)
        )

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Memory search for the UI: facts (incl. superseded), notes, chunks."""
        out: list[dict] = []
        for n in self.store.fulltext_nodes(query, limit=limit, include_superseded=True):
            r = self.store.get_node(n.id) or {}
            if r.get("type") == "FACT":
                out.append(
                    {
                        "kind": "fact",
                        "id": n.id,
                        "label": n.label[:120],
                        "excerpt": (r.get("description") or "")[:400],
                        "status": _fact_status(r),
                        "origin": _origin(r),
                        "superseded_by": None,
                        "score": float(n.properties.get("_score", 0.0)),
                    }
                )
            else:
                facts = self.store.facts_mentioning([n.id], limit=3)
                for f in facts:
                    out.append(
                        {
                            "kind": "fact",
                            "id": f["id"],
                            "label": f"{n.label}: {f['label'][:90]}",
                            "excerpt": (f.get("description") or "")[:400],
                            "status": _fact_status(f),
                            "origin": _origin(f),
                            "superseded_by": None,
                            "score": float(n.properties.get("_score", 0.0)),
                        }
                    )
        q = fts_query(query)
        if q:
            for r in self.db.query(
                "SELECT n.id, n.text, n.created_at FROM notes_fts f JOIN notes n ON n.rowid = f.rowid "
                "WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) LIMIT 5",
                (q,),
            ):
                out.append(
                    {
                        "kind": "note",
                        "id": r["id"],
                        "label": r["text"][:80],
                        "excerpt": r["text"][:400],
                        "status": "current",
                        "origin": {"kind": "note", "id": r["id"], "title": "Erinnerung", "at": r["created_at"]},
                        "superseded_by": None,
                        "score": 0.0,
                    }
                )
            for r in self.db.query(
                "SELECT ch.id, ch.text, ch.ord, d.id AS document_id, d.name, d.created_at FROM chunks_fts f "
                "JOIN chunks ch ON ch.rowid = f.rowid JOIN documents d ON d.id = ch.document_id "
                "WHERE chunks_fts MATCH ? AND d.deleted_at IS NULL ORDER BY bm25(chunks_fts) LIMIT 5",
                (q,),
            ):
                out.append(
                    {
                        "kind": "document",
                        "id": r["id"],
                        "label": f"{r['name']} (Abschnitt {r['ord'] + 1})",
                        "excerpt": r["text"][:400],
                        "status": "current",
                        "origin": {
                            "kind": "document",
                            "id": r["document_id"],
                            "title": r["name"],
                            "at": r["created_at"],
                        },
                        "superseded_by": None,
                        "score": 0.0,
                    }
                )
        seen: set[str] = set()
        dedup = []
        for s in out:
            if s["id"] in seen:
                continue
            seen.add(s["id"])
            dedup.append(s)
        return dedup[:limit]


def source_from_fact_row(r: dict) -> Optional[dict]:
    if not r:
        return None
    return {
        "kind": "fact",
        "id": r["id"],
        "label": (r.get("label") or "")[:120],
        "excerpt": (r.get("description") or "")[:400],
        "status": _fact_status(r),
        "origin": _origin(r),
        "superseded_by": None,
    }
