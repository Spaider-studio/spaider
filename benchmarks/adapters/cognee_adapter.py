"""Cognee adapter — local file-based store (LanceDB + file graph), OpenAI pinned.

Runs on Cognee's local defaults under ``benchmarks/.bench_data/cognee/``
(gitignored); never touches SpAIder's Neo4j. ``cognee`` is imported lazily so
the module loads without the dependency present.

Cognee's public API has shifted across releases (``search`` arg order, the
``SearchType`` members). The calls here are written defensively (try both
signatures, fall back across completion search types). If a first real run
surfaces an API change, the adjustment is localized to ``_search`` /
``_configure`` here.

Token counts: Cognee does not expose its internal LLM spend, so native runs
report ``tokens=0`` (documented asymmetry — all internal LLMs are pinned to
gpt-4o-mini so cost is estimable from call counts instead).
"""
from __future__ import annotations

import os
import time

from benchmarks.adapters.base import (
    AdapterAnswer,
    MemorySystemAdapter,
    RetrievedContext,
)

_DEFAULT_DATA_DIR = "benchmarks/.bench_data/cognee"


def _stringify_results(results) -> str:
    """Flatten cognee.search output into a context/answer string.

    cognee 1.1.x wraps results as ``[{dataset_id, dataset_name, search_result:
    [...]}]``. The inner ``search_result`` is a list of answer *strings* for the
    *_COMPLETION search types, or chunk *dicts* (with a ``text`` field) for
    CHUNKS. We descend into ``search_result`` and pull text, skipping the pure
    metadata wrappers so UUIDs never leak into the answer."""
    texts: list[str] = []

    def _walk(x) -> None:
        if x is None:
            return
        if isinstance(x, str):
            if x.strip():
                texts.append(x.strip())
        elif isinstance(x, dict):
            if "search_result" in x:
                _walk(x["search_result"])
            elif isinstance(x.get("text"), str):
                texts.append(x["text"].strip())
            elif isinstance(x.get("content"), str):
                texts.append(x["content"].strip())
            elif isinstance(x.get("answer"), str):
                texts.append(x["answer"].strip())
            # else: pure-metadata dict — skip.
        elif isinstance(x, (list, tuple)):
            for item in x:
                _walk(item)
        else:
            t = getattr(x, "text", None)
            if isinstance(t, str):
                texts.append(t.strip())

    _walk(results)
    return "\n".join(t for t in texts if t)


class CogneeAdapter(MemorySystemAdapter):
    name = "cognee"

    def __init__(
        self,
        *,
        data_dir: str | None = None,
        model: str = "gpt-4o-mini",
        dataset: str = "bench",
    ) -> None:
        # Cognee builds file:// URIs from these paths, which requires absolute.
        self._data_dir = os.path.abspath(
            data_dir or os.environ.get("COGNEE_DATA_DIR", _DEFAULT_DATA_DIR)
        )
        self._model = model
        self._dataset = dataset
        self._configured = False

    def _configure(self) -> None:
        if self._configured:
            return
        import cognee

        # Pin the LLM/embeddings via env (Cognee reads these on first use).
        os.environ.setdefault("LLM_PROVIDER", "openai")
        os.environ.setdefault("LLM_MODEL", self._model)
        os.makedirs(self._data_dir, exist_ok=True)
        # Isolate storage to our scratch dir (best-effort across versions).
        for setter, sub in (
            ("data_root_directory", "data"),
            ("system_root_directory", "system"),
        ):
            fn = getattr(cognee.config, setter, None)
            if callable(fn):
                try:
                    fn(os.path.join(self._data_dir, sub))
                except Exception:  # noqa: BLE001
                    pass
        # Pin LLM config + key. cognee reads its key from its own config, not
        # OPENAI_API_KEY — without this the *_COMPLETION search types raise
        # LLMAPIKeyNotSetError and silently degrade to chunk retrieval.
        fn = getattr(cognee.config, "set_llm_config", None)
        if callable(fn):
            key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
            llm_cfg = {"llm_provider": "openai", "llm_model": self._model}
            if key:
                llm_cfg["llm_api_key"] = key
            try:
                fn(llm_cfg)
            except Exception:  # noqa: BLE001
                pass
        self._configured = True

    async def ingest(self, facts: list[dict]) -> None:
        import cognee

        self._configure()
        for fact in facts:
            try:
                await cognee.add(fact["text"], dataset_name=self._dataset)
            except TypeError:
                await cognee.add(fact["text"])
        try:
            await cognee.cognify(datasets=[self._dataset])
        except TypeError:
            await cognee.cognify()

    async def _search(self, question: str, query_type_name: str):
        import cognee
        from cognee.api.v1.search import SearchType

        self._configure()
        qt = getattr(SearchType, query_type_name, None)
        if qt is None:
            raise ValueError(f"cognee SearchType.{query_type_name} unavailable")
        # Newer kwargs signature first, then legacy positional.
        try:
            return await cognee.search(query_text=question, query_type=qt)
        except TypeError:
            return await cognee.search(qt, question)

    async def retrieve(self, question: str, top_k: int = 10) -> RetrievedContext:
        results = await self._search(question, "CHUNKS")
        return RetrievedContext(text=_stringify_results(results))

    async def answer_native(self, question: str, max_tokens: int) -> AdapterAnswer:
        t0 = time.perf_counter()
        # Prefer a graph-aware completion search; fall back across names, then
        # to raw chunks if no completion search type is available.
        for qt in ("GRAPH_COMPLETION", "COMPLETION", "RAG_COMPLETION"):
            try:
                results = await self._search(question, qt)
            except Exception:  # noqa: BLE001
                continue
            text = _stringify_results(results)
            if text.strip():
                return AdapterAnswer(
                    text=text.strip(),
                    latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                    retrieved_text=text,
                )
        results = await self._search(question, "CHUNKS")
        text = _stringify_results(results)
        return AdapterAnswer(
            text=text.strip(),
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            retrieved_text=text,
        )
