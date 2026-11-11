"""Memory tool: read-only access to stored facts, notes and document chunks."""

from __future__ import annotations

from ..broker import Broker, Tool, ToolContext, ToolOutcome


def register(broker: Broker, *, retriever) -> None:
    async def search(args: dict, ctx: ToolContext) -> ToolOutcome:
        query = (args["query"] or "").strip()
        ctx_res = await retriever.retrieve(query, max_chars=3500)
        sources = [
            {"id": s.get("id"), "kind": s.get("kind"), "status": s.get("status"), "origin": s.get("origin"), "excerpt": (s.get("excerpt") or "")[:300]}
            for s in (ctx_res.sources or [])[:12]
        ]
        return ToolOutcome(
            ok=True,
            summary_de=f"{len(sources)} Gedächtnisquellen zu „{query[:40]}“",
            data={"context": ctx_res.context_text[:3500], "sources": sources},
            evidence={"source_ids": [s["id"] for s in sources]},
            verified=True,
        )

    broker.register(
        Tool(
            name="memory.search",
            description_de="Durchsucht das gespeicherte Wissen (Fakten, Notizen, importierte Dokumente) und liefert Kontext mit Quellen.",
            scope="memory",
            side_effect="none",
            schema={"type": "object", "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 300}}, "required": ["query"], "additionalProperties": False},
            run=search,
            timeout_s=30,
        )
    )
