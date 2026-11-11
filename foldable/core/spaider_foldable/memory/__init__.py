"""Portable memory: SpAIder's knowledge-graph pipeline on SQLite.

Reused from ``backend/app`` (imported, not copied):
  * ``app.models.schemas`` (Node, Edge, GraphPayload)
  * ``app.services.compressor`` (SemanticCompressor, OntologyManager, prompt, JSON parser, chunking)
  * ``app.services.entity_resolver`` (EntityResolver, build_embed_text)
  * ``app.services.supersession`` (state gate + judge prompts, text helpers)
  * ``app.services.query_service`` (``_rrf_fuse``, ``QueryService._build_context``)

Replaced by local adapters: Neo4j (``LocalMemoryStore``), Redis caches
(no-op), the embedding API (``LocalEmbeddingService`` over llama-server).
"""
