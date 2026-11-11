"""LocalMemoryStore: SpAIder GraphPayloads in SQLite + FTS5 + numpy vectors.

It implements the two ``GraphService`` methods the reused ``EntityResolver``
calls (``list_nodes_for_resolver``, ``vector_search``) and the three
supersession queries from ``app.services.supersession`` (candidates, mark,
neutralize) as SQL.
"""

from __future__ import annotations

import json
import logging
import re
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

import numpy as np
from app.models.schemas import Edge, GraphPayload, Node

from ..db import Database

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[\w]+", re.UNICODE)
_STOPWORDS = {
    "der",
    "die",
    "das",
    "und",
    "oder",
    "ist",
    "sind",
    "war",
    "waren",
    "ein",
    "eine",
    "einer",
    "eines",
    "einem",
    "einen",
    "ich",
    "du",
    "er",
    "sie",
    "es",
    "wir",
    "ihr",
    "mich",
    "mir",
    "dich",
    "dir",
    "uns",
    "euch",
    "sich",
    "mein",
    "meine",
    "dein",
    "deine",
    "sein",
    "seine",
    "ihre",
    "unser",
    "unsere",
    "nicht",
    "kein",
    "keine",
    "wie",
    "was",
    "wer",
    "wo",
    "wann",
    "warum",
    "welche",
    "welcher",
    "welches",
    "bitte",
    "mal",
    "noch",
    "auch",
    "mit",
    "von",
    "zu",
    "zum",
    "zur",
    "im",
    "in",
    "an",
    "am",
    "auf",
    "für",
    "fur",
    "bei",
    "nach",
    "aus",
    "über",
    "uber",
    "um",
    "den",
    "dem",
    "des",
    "da",
    "dass",
    "als",
    "ob",
    "so",
    "nur",
    "schon",
    "jetzt",
    "heute",
    "the",
    "a",
    "an",
    "and",
    "or",
    "is",
    "are",
    "was",
    "were",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "what",
    "who",
    "when",
    "where",
    "why",
    "how",
    "which",
    "do",
    "does",
    "did",
    "hat",
    "habe",
    "hast",
    "haben",
    "hatte",
    "wird",
    "werden",
    "kann",
    "kannst",
    "können",
    "soll",
    "sollte",
    "möchte",
    "mochte",
    "will",
    "gibt",
    "sag",
    "sage",
    "erzähl",
    "erzahl",
    "weißt",
    "weisst",
    "mich",
    "denn",
    "doch",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class Origin:
    kind: str  # conversation | document | note
    id: str
    title: str


def fts_query(text: str, *, prefix: bool = True, max_terms: int = 12) -> Optional[str]:
    """Turn free text into a safe FTS5 MATCH expression (OR of quoted terms)."""
    terms: list[str] = []
    for w in _WORD_RE.findall(text or ""):
        lw = w.lower()
        if len(lw) < 2 or lw in _STOPWORDS or lw.isdigit() and len(lw) < 3:
            continue
        if lw not in terms:
            terms.append(lw)
        if len(terms) >= max_terms:
            break
    if not terms:
        return None
    parts = []
    for t in terms:
        q = '"' + t.replace('"', '""') + '"'
        if prefix and len(t) >= 4:
            q += "*"
        parts.append(q)
    return " OR ".join(parts)


def _content_words(text: str) -> set[str]:
    """Lower-cased words with >= 4 characters, minus stopwords (fuzzy overlap checks)."""
    return {w.lower() for w in _WORD_RE.findall(text or "") if len(w) >= 4 and w.lower() not in _STOPWORDS}


def _vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


class LocalMemoryStore:
    def __init__(self, db: Database, agent_id: str = "local"):
        self.db = db
        self.agent_id = agent_id
        self._index_model: Optional[str] = None
        self._matrix: Optional[np.ndarray] = None
        self._matrix_ids: list[str] = []
        self._matrix_dirty = True

    # ------------------------------------------------------------------
    # Node/edge mapping
    # ------------------------------------------------------------------
    def _row_to_node(self, row, *, with_embedding: Optional[np.ndarray] = None) -> Node:
        props = json.loads(row["properties_json"] or "{}")
        node = Node(
            id=row["id"],
            label=row["label"],
            type=row["type"],
            description=row["description"],
            properties=props,
            agent_id=row["agent_id"],
        )
        if with_embedding is not None:
            node.embedding = [float(x) for x in with_embedding]
        return node

    def _row_to_edge(self, row) -> Edge:
        return Edge(
            id=row["id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            relation=row["relation"],
            properties=json.loads(row["properties_json"] or "{}"),
            agent_id=row["agent_id"],
            utility_weight=row["utility_weight"],
        )

    # ------------------------------------------------------------------
    # GraphService duck-type (used by EntityResolver)
    # ------------------------------------------------------------------
    async def list_nodes_for_resolver(self, agent_id: str) -> list[Node]:
        rows = self.db.query(
            "SELECT id, label, type, description, properties_json, agent_id FROM nodes "
            "WHERE agent_id = ? AND type <> 'FACT' AND superseded = 0",
            (agent_id,),
        )
        return [self._row_to_node(r) for r in rows]

    async def resolver_candidates(self, agent_id: str, labels: list[str], per_label: int = 25) -> list[Node]:
        """Upstream 0.5.0 resolver contract: existing non-FACT nodes that could match
        any of ``labels``. Small local graphs return the full list (same behaviour as
        before: fuzzy matching over everything); larger graphs use the FTS index per
        label so the cost follows the batch, not the graph."""
        total = self.db.scalar(
            "SELECT count(*) FROM nodes WHERE agent_id = ? AND type <> 'FACT' AND superseded = 0", (agent_id,)
        ) or 0
        if total <= 2000:
            return await self.list_nodes_for_resolver(agent_id)
        out: dict[str, Node] = {}
        for label in dict.fromkeys(labels):
            for n in self.fulltext_nodes(label, limit=per_label * 8):
                if n.type != "FACT" and n.id not in out:
                    out[n.id] = n
        return list(out.values())

    async def vector_search(
        self, embedding: list[float], agent_id: str, top_k: int = 8, *, include_facts: bool = True
    ) -> list[Node]:
        if not embedding or self._index_model is None:
            return []
        self._ensure_matrix()
        if self._matrix is None or len(self._matrix_ids) == 0:
            return []
        q = np.asarray(embedding, dtype="<f4")
        if q.shape[0] != self._matrix.shape[1]:
            return []
        qn = q / (np.linalg.norm(q) or 1.0)
        scores = self._matrix @ qn
        order = np.argsort(-scores)[: max(top_k * 3, top_k)]
        ids = [self._matrix_ids[i] for i in order]
        rows = self._nodes_by_ids(ids)
        out: list[Node] = []
        for i in order:
            nid = self._matrix_ids[i]
            row = rows.get(nid)
            if row is None or row["superseded"]:
                continue
            if not include_facts and row["type"] == "FACT":
                continue
            node = self._row_to_node(row, with_embedding=self._matrix[i])
            node.properties["_score"] = float(scores[i])
            out.append(node)
            if len(out) >= top_k:
                break
        return out

    def set_index_model(self, model_id: Optional[str]) -> None:
        if model_id != self._index_model:
            self._index_model = model_id
            self._matrix_dirty = True

    def _ensure_matrix(self) -> None:
        if not self._matrix_dirty:
            return
        rows = self.db.query(
            "SELECT e.node_id, e.vector FROM node_embeddings e JOIN nodes n ON n.id = e.node_id "
            "WHERE e.model_id = ? AND n.agent_id = ?",
            (self._index_model, self.agent_id),
        )
        if not rows:
            self._matrix, self._matrix_ids = None, []
        else:
            vecs = [_blob_to_vec(r["vector"]) for r in rows]
            dims = {v.shape[0] for v in vecs}
            if len(dims) != 1:
                logger.warning("mixed embedding dimensions in index model %s; ignoring index", self._index_model)
                self._matrix, self._matrix_ids = None, []
            else:
                m = np.stack(vecs).astype("<f4")
                norms = np.linalg.norm(m, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                self._matrix = m / norms
                self._matrix_ids = [r["node_id"] for r in rows]
        self._matrix_dirty = False

    def _nodes_by_ids(self, ids: Iterable[str]) -> dict[str, object]:
        ids = list(ids)
        if not ids:
            return {}
        out: dict[str, object] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self.db.query(f"SELECT * FROM nodes WHERE id IN ({marks})", chunk):
                out[r["id"]] = r
        return out

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def write_raw_fact(self, fact_id: str, text: str, origin: Origin, *, source: str) -> None:
        """Store the verbatim user text as a FACT node immediately (processing='raw').
        FTS indexes it at once, so the next answer can see it before the
        background extraction has run."""
        label_preview = text.strip().replace("\n", " ")[:200] + ("…" if len(text) > 200 else "")
        now = utcnow()
        self.db.execute(
            "INSERT OR IGNORE INTO nodes(id, agent_id, label, type, description, source_text, properties_json, confidence, "  # noqa: E501
            "origin_kind, origin_id, origin_title, created_at, updated_at, processing) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'raw')",
            (
                fact_id,
                self.agent_id,
                f"fact: {label_preview}",
                "FACT",
                text,
                None,
                json.dumps({"source": source}, ensure_ascii=False),
                1.0,
                origin.kind,
                origin.id,
                origin.title,
                now,
                now,
            ),
        )

    def write_graph(self, payload: GraphPayload, origin: Origin, *, embedding_model_id: Optional[str]) -> dict:
        created = merged = edges_created = 0
        now = utcnow()
        with self.db.transaction() as conn:
            for node in payload.nodes:
                props = dict(node.properties or {})
                props.pop("_score", None)
                description = node.description or props.get("description")
                source_text = props.get("source_text")
                confidence = float(props.get("confidence", 1.0) or 1.0)
                existing = conn.execute("SELECT id, properties_json FROM nodes WHERE id = ?", (node.id,)).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO nodes(id, agent_id, label, type, description, source_text, properties_json, confidence, "  # noqa: E501
                        "origin_kind, origin_id, origin_title, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",  # noqa: E501
                        (
                            node.id,
                            self.agent_id,
                            node.label,
                            node.type or "OTHER",
                            description,
                            source_text,
                            json.dumps(props, ensure_ascii=False),
                            confidence,
                            origin.kind,
                            origin.id,
                            origin.title,
                            now,
                            now,
                        ),
                    )
                    created += 1
                else:
                    old = json.loads(existing["properties_json"] or "{}")
                    old.update(props)
                    conn.execute(
                        "UPDATE nodes SET properties_json = ?, description = COALESCE(?, description), "
                        "source_text = COALESCE(source_text, ?), updated_at = ?, "
                        "processing = CASE WHEN type = 'FACT' THEN 'extracted' ELSE processing END WHERE id = ?",
                        (json.dumps(old, ensure_ascii=False), description, source_text, now, node.id),
                    )
                    merged += 1
                if node.embedding and embedding_model_id:
                    conn.execute(
                        "INSERT INTO node_embeddings(node_id, model_id, dims, vector) VALUES (?,?,?,?) "
                        "ON CONFLICT(node_id, model_id) DO UPDATE SET dims = excluded.dims, vector = excluded.vector",
                        (node.id, embedding_model_id, len(node.embedding), _vec_to_blob(node.embedding)),
                    )
                    self._matrix_dirty = True
            for edge in payload.edges:
                if edge.source_id == edge.target_id:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO edges(id, agent_id, source_id, target_id, relation, properties_json, "
                    "utility_weight, origin_kind, origin_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        edge.id,
                        self.agent_id,
                        edge.source_id,
                        edge.target_id,
                        edge.relation,
                        json.dumps(edge.properties or {}, ensure_ascii=False),
                        edge.utility_weight,
                        origin.kind,
                        origin.id,
                        now,
                    ),
                )
                edges_created += cur.rowcount
        return {"nodes_created": created, "nodes_merged": merged, "edges_created": edges_created}

    def store_embeddings(self, items: list[tuple[str, list[float]]], model_id: str) -> None:
        if not items:
            return
        with self.db.transaction() as conn:
            for node_id, vec in items:
                conn.execute(
                    "INSERT INTO node_embeddings(node_id, model_id, dims, vector) VALUES (?,?,?,?) "
                    "ON CONFLICT(node_id, model_id) DO UPDATE SET dims = excluded.dims, vector = excluded.vector",
                    (node_id, model_id, len(vec), _vec_to_blob(vec)),
                )
        self._matrix_dirty = True

    def nodes_missing_embeddings(self, model_id: str, limit: int = 64) -> list[Node]:
        rows = self.db.query(
            "SELECT n.* FROM nodes n LEFT JOIN node_embeddings e ON e.node_id = n.id AND e.model_id = ? "
            "WHERE e.node_id IS NULL AND n.agent_id = ? AND n.superseded = 0 ORDER BY n.created_at LIMIT ?",
            (model_id, self.agent_id, limit),
        )
        return [self._row_to_node(r) for r in rows]

    def mark_origin_deleted(self, kind: str, origin_id: str) -> None:
        self.db.execute(
            "UPDATE nodes SET origin_deleted = 1 WHERE origin_kind = ? AND origin_id = ?", (kind, origin_id)
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def fulltext_nodes(self, text: str, limit: int = 12, *, include_superseded: bool = False) -> list[Node]:
        q = fts_query(text)
        if not q:
            return []
        rows = self.db.query(
            "SELECT n.*, bm25(nodes_fts, 3.0, 1.0, 1.0) AS score FROM nodes_fts f "
            "JOIN nodes n ON n.rowid = f.rowid WHERE nodes_fts MATCH ? AND n.agent_id = ? "
            + ("" if include_superseded else "AND n.superseded = 0 ")
            + "ORDER BY score LIMIT ?",
            (q, self.agent_id, limit),
        )
        out = []
        for r in rows:
            node = self._row_to_node(r)
            node.properties["_score"] = -float(r["score"])
            out.append(node)
        return out

    def get_node(self, node_id: str) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM nodes WHERE id = ?", (node_id,))
        return dict(row) if row else None

    def get_nodes(self, ids: Iterable[str]) -> dict[str, dict]:
        return {k: dict(v) for k, v in self._nodes_by_ids(ids).items()}

    def edges_for_nodes(
        self, node_ids: Iterable[str], *, include_superseded: bool = False, limit: int = 200
    ) -> list[Edge]:
        ids = list(node_ids)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self.db.query(
            f"SELECT * FROM edges WHERE (source_id IN ({marks}) OR target_id IN ({marks})) "
            + ("" if include_superseded else "AND superseded = 0 ")
            + "ORDER BY utility_weight DESC, created_at DESC LIMIT ?",
            ids + ids + [limit],
        )
        return [self._row_to_edge(r) for r in rows]

    def superseded_edges_for_nodes(self, node_ids: Iterable[str], limit: int = 10) -> list[dict]:
        ids = list(node_ids)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self.db.query(
            f"SELECT e.*, a.label AS subj, b.label AS obj FROM edges e "
            f"JOIN nodes a ON a.id = e.source_id JOIN nodes b ON b.id = e.target_id "
            f"WHERE e.superseded = 1 AND (e.source_id IN ({marks}) OR e.target_id IN ({marks})) "
            f"AND e.relation <> 'MENTIONS' ORDER BY e.created_at DESC LIMIT ?",
            ids + ids + [limit],
        )
        return [dict(r) for r in rows]

    def facts_mentioning(self, entity_ids: Iterable[str], *, limit: int = 20) -> list[dict]:
        ids = list(entity_ids)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self.db.query(
            f"SELECT DISTINCT n.* FROM edges e JOIN nodes n ON n.id = e.source_id "
            f"WHERE e.relation = 'MENTIONS' AND e.target_id IN ({marks}) AND n.type = 'FACT' AND n.superseded = 0 "
            f"ORDER BY n.created_at DESC LIMIT ?",
            ids + [limit],
        )
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        q = self.db.scalar
        return {
            "facts": q("SELECT count(*) FROM nodes WHERE type = 'FACT' AND superseded = 0") or 0,
            "entities": q("SELECT count(*) FROM nodes WHERE type <> 'FACT' AND superseded = 0") or 0,
            "relations": q("SELECT count(*) FROM edges WHERE relation <> 'MENTIONS' AND superseded = 0") or 0,
            "superseded": q("SELECT count(*) FROM nodes WHERE superseded = 1") or 0,
            "vectors": q("SELECT count(*) FROM node_embeddings WHERE model_id = ?", (self._index_model,))
            if self._index_model
            else 0,
        }

    def list_facts(self, *, limit: int, offset: int, include_superseded: bool) -> tuple[list[dict], int]:
        where = "type = 'FACT'" + ("" if include_superseded else " AND superseded = 0")
        total = self.db.scalar(f"SELECT count(*) FROM nodes WHERE {where}") or 0
        rows = self.db.query(
            f"SELECT * FROM nodes WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        return [dict(r) for r in rows], int(total)

    # ------------------------------------------------------------------
    # Supersession (SQL ports of the three Cypher statements)
    # ------------------------------------------------------------------
    def supersession_candidates(self, *, src: str, tgt: str, new_id: str, max_candidates: int) -> list[dict]:
        rows = self.db.query(
            "SELECT e.id AS edge_id, a.label AS subj, e.relation AS rel, b.label AS obj, e.properties_json AS props "
            "FROM edges e JOIN nodes a ON a.id = e.source_id JOIN nodes b ON b.id = e.target_id "
            "WHERE e.agent_id = ? AND e.superseded = 0 AND e.id <> ? AND e.relation <> 'MENTIONS' "
            "AND (a.id IN (?, ?) OR b.id IN (?, ?)) ORDER BY e.created_at DESC LIMIT ?",
            (self.agent_id, new_id, src, tgt, src, tgt, max_candidates),
        )
        return [dict(r) for r in rows]

    def mark_superseded(self, items: list[dict], new_id: str) -> int:
        """Mark edges and the FACT nodes whose text contains the stale sentence."""
        marked = 0
        with self.db.transaction() as conn:
            for item in items:
                conn.execute(
                    "UPDATE edges SET superseded = 1, superseded_by = ? WHERE id = ?", (new_id, item["edge_id"])
                )
            texts = [t for t in (i.get("text") or "" for i in items) if t]
            for t in texts:
                cur = conn.execute(
                    "UPDATE nodes SET superseded = 1, superseded_by = ?, updated_at = ? "
                    "WHERE agent_id = ? AND type = 'FACT' AND superseded = 0 AND instr(description, ?) > 0",
                    (new_id, utcnow(), self.agent_id, t),
                )
                marked += cur.rowcount
                # The stale sentence is also denormalised onto the entities it was
                # extracted from (source_text). Drop that copy so retrieval shows the
                # entity without the outdated statement; the sentence itself stays
                # available as history on the superseded FACT node.
                conn.execute(
                    "UPDATE nodes SET source_text = NULL, "
                    "properties_json = json_remove(properties_json, '$.source_text'), updated_at = ? "
                    "WHERE agent_id = ? AND type <> 'FACT' AND source_text IS NOT NULL AND instr(?, source_text) > 0",
                    (utcnow(), self.agent_id, t),
                )
            # Adapter extension: the LLM rarely quotes the stale sentence verbatim,
            # so exact containment misses the FACT node that carried the old relation.
            # Also mark FACT nodes that mention BOTH endpoints of a superseded edge and
            # share most content words with the stale text. The superseding fact itself
            # (the one that produced ``new_id``) is never marked.
            for item in items:
                stale_words = _content_words(item.get("text") or "")
                if len(stale_words) < 2:
                    continue
                edge = conn.execute(
                    "SELECT source_id, target_id FROM edges WHERE id = ?", (item["edge_id"],)
                ).fetchone()
                if edge is None:
                    continue
                rows = conn.execute(
                    "SELECT f.id, f.description FROM nodes f "
                    "JOIN edges m1 ON m1.source_id = f.id AND m1.relation = 'MENTIONS' AND m1.target_id = ? "
                    "JOIN edges m2 ON m2.source_id = f.id AND m2.relation = 'MENTIONS' AND m2.target_id = ? "
                    "WHERE f.type = 'FACT' AND f.superseded = 0 AND f.agent_id = ? "
                    "AND NOT EXISTS (SELECT 1 FROM edges n WHERE n.id = ? AND n.origin_id = f.origin_id "
                    "                AND n.created_at <= f.created_at)",
                    (edge["source_id"], edge["target_id"], self.agent_id, new_id),
                ).fetchall()
                for r in rows:
                    fact_words = _content_words(r["description"] or "")
                    overlap = len(stale_words & fact_words) / len(stale_words)
                    if overlap >= 0.8:
                        conn.execute(
                            "UPDATE nodes SET superseded = 1, superseded_by = ?, updated_at = ? "
                            "WHERE id = ? AND superseded = 0",
                            (new_id, utcnow(), r["id"]),
                        )
                        marked += 1
        self._matrix_dirty = True
        return marked

    def apply_explicit_correction(self, new_fact_id: str, text: str, *, candidates: int = 40) -> list[str]:
        """Model-independent correction layer (see ``memory/corrections.py``):
        when ``text`` is an explicit correction, older current FACT nodes that
        it rewrites are marked superseded by ``new_fact_id`` right away. Returns
        the ids that were marked. Idempotent."""
        from .corrections import find_corrected_facts_detailed, is_explicit_correction

        if not is_explicit_correction(text):
            return []
        q = fts_query(text, prefix=False)
        if not q:
            return []
        rows = self.db.query(
            "SELECT n.id, n.description, n.created_at FROM nodes_fts f JOIN nodes n ON n.rowid = f.rowid "
            "WHERE nodes_fts MATCH ? AND n.agent_id = ? AND n.type = 'FACT' AND n.superseded = 0 AND n.id <> ? "
            "ORDER BY n.created_at DESC LIMIT ?",
            (q, self.agent_id, new_fact_id, candidates),
        )
        new_row = self.db.query_one("SELECT created_at FROM nodes WHERE id = ?", (new_fact_id,))
        older = [dict(r) for r in rows if new_row is None or (r["created_at"] or "") <= new_row["created_at"]]
        matched = find_corrected_facts_detailed(text, older)
        if not matched:
            return []
        ids = [fid for fid, _ in matched]
        now = utcnow()
        with self.db.transaction() as conn:
            for fid, remaining in matched:
                if remaining and len(remaining) >= 12:
                    # the untouched sentences of a multi-sentence fact stay current as a
                    # derived fact (same origin; entities were extracted from the original)
                    src = conn.execute(
                        "SELECT origin_kind, origin_id, origin_title, properties_json FROM nodes WHERE id = ?", (fid,)
                    ).fetchone()
                    if src is not None:
                        preview = remaining.replace("\n", " ")[:200] + ("…" if len(remaining) > 200 else "")
                        props = json.loads(src["properties_json"] or "{}")
                        props["derived_from"] = fid
                        conn.execute(
                            "INSERT INTO nodes(id, agent_id, label, type, description, source_text, properties_json, "
                            "confidence, origin_kind, origin_id, origin_title, created_at, updated_at, processing) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'extracted')",
                            (
                                str(uuid.uuid4()),
                                self.agent_id,
                                f"fact: {preview}",
                                "FACT",
                                remaining,
                                None,
                                json.dumps(props, ensure_ascii=False),
                                1.0,
                                src["origin_kind"],
                                src["origin_id"],
                                src["origin_title"],
                                now,
                                now,
                            ),
                        )
                conn.execute(
                    "UPDATE nodes SET superseded = 1, superseded_by = ?, updated_at = ? WHERE id = ? AND superseded = 0",
                    (new_fact_id, now, fid),
                )
                # relations extracted from the replaced statement become history as well
                conn.execute(
                    "UPDATE edges SET superseded = 1, superseded_by = ? WHERE superseded = 0 AND relation <> 'MENTIONS' "
                    "AND origin_id = (SELECT origin_id FROM nodes WHERE id = ?) "
                    "AND source_id IN (SELECT target_id FROM edges WHERE source_id = ? AND relation = 'MENTIONS') "
                    "AND target_id IN (SELECT target_id FROM edges WHERE source_id = ? AND relation = 'MENTIONS')",
                    (new_fact_id, fid, fid, fid),
                )
                old_desc = conn.execute("SELECT description FROM nodes WHERE id = ?", (fid,)).fetchone()
                if old_desc and old_desc[0]:
                    conn.execute(
                        "UPDATE nodes SET source_text = NULL, "
                        "properties_json = json_remove(properties_json, '$.source_text'), updated_at = ? "
                        "WHERE agent_id = ? AND type <> 'FACT' AND source_text IS NOT NULL "
                        "AND length(source_text) >= 12 AND instr(?, source_text) > 0",
                        (now, self.agent_id, old_desc[0]),
                    )
        self._matrix_dirty = True
        return ids

    def superseded_facts_for_nodes(self, node_ids: Iterable[str], limit: int = 6) -> list[dict]:
        ids = list(node_ids)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self.db.query(
            f"SELECT DISTINCT f.* FROM nodes f JOIN edges m ON m.source_id = f.id AND m.relation = 'MENTIONS' "
            f"WHERE f.type = 'FACT' AND f.superseded = 1 AND m.target_id IN ({marks}) "
            f"ORDER BY f.created_at DESC LIMIT ?",
            ids + [limit],
        )
        return [dict(r) for r in rows]

    def neutralize(self, items: list[dict]) -> int:
        """Clear the stale denormalised role description on endpoint entities
        when no other non-superseded FACT still mentions them."""
        count = 0
        with self.db.transaction() as conn:
            for item in items:
                edge = conn.execute(
                    "SELECT source_id, target_id FROM edges WHERE id = ?", (item["edge_id"],)
                ).fetchone()
                if edge is None:
                    continue
                ftext = (item.get("text") or "").lower()
                for ent_id in (edge["source_id"], edge["target_id"]):
                    ent = conn.execute("SELECT id, type, description FROM nodes WHERE id = ?", (ent_id,)).fetchone()
                    if ent is None or ent["type"] == "FACT" or not ent["description"]:
                        continue
                    if ent["description"].lower() not in ftext:
                        continue
                    others = conn.execute(
                        "SELECT count(*) FROM edges e JOIN nodes f ON f.id = e.source_id "
                        "WHERE e.relation = 'MENTIONS' AND e.target_id = ? AND f.type = 'FACT' AND f.superseded = 0",
                        (ent_id,),
                    ).fetchone()[0]
                    if others == 0:
                        conn.execute("UPDATE nodes SET description = NULL, role_superseded = 1 WHERE id = ?", (ent_id,))
                        count += 1
        return count


def new_id() -> str:
    return str(uuid.uuid4())
