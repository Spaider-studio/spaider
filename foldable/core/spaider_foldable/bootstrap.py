"""Process bootstrap: make the SpAIder backend importable and pin its settings
to the portable operating mode BEFORE ``app.config`` is imported anywhere.

Why this exists
---------------
``backend/app`` is used as a library (parser, schemas, ontology, compressor,
supersession judge, entity resolver, context formatting). Its ``Settings``
singleton is created at import time from environment variables and an optional
``.env`` file in the current working directory. The portable core therefore:

1. puts ``backend/`` on ``sys.path`` (dev checkout) or relies on the frozen
   bundle that ships the ``app`` package,
2. sets every environment variable that would otherwise point at Neo4j, Kafka,
   Redis, Postgres, ClickHouse or a cloud LLM to inert loopback placeholders,
3. disables all network side effects of ``litellm`` (model cost map download,
   telemetry).

Nothing here connects to any service. The LLM base URL is replaced at runtime by
``InferenceManager`` once the bundled llama.cpp server is up.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BOOTSTRAPPED = False


def backend_dir() -> Path | None:
    """Return the ``backend/`` directory of the monorepo checkout, if present."""
    here = Path(__file__).resolve()
    candidate = here.parents[3] / "backend"  # foldable/core/spaider_foldable/bootstrap.py -> repo/backend
    if (candidate / "app" / "__init__.py").exists():
        return candidate
    return None


def portable_environment() -> dict[str, str]:
    """Environment values that put ``app.config.Settings`` into portable mode."""
    return {
        # litellm must never phone home: the model cost map ships in the package.
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "LITELLM_LOG": "ERROR",
        # LLM via the OpenAI-compatible llama.cpp server. The base URL is a
        # placeholder until the runtime reports its port.
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "openai/spaider-local-chat",
        "LLM_API_KEY": "local-placeholder",
        "LLM_BASE_URL": "http://127.0.0.1:1/v1",
        "LLM_TEMPERATURE": "0.0",
        "LLM_MAX_TOKENS": "2048",
        "LITELLM_RETRY_MAX_ATTEMPTS": "2",
        "LITELLM_RETRY_BASE_DELAY": "0.5",
        # Embeddings via the second llama.cpp server (optional at runtime).
        "EMBEDDING_PROVIDER": "openai",
        "EMBEDDING_MODEL": "openai/spaider-local-embedding",
        "EMBEDDING_API_KEY": "local-placeholder",
        "EMBEDDING_BASE_URL": "http://127.0.0.1:1/v1",
        "EMBEDDING_DIMENSIONS": "1024",
        # Services that are never started in portable mode. The URLs are inert:
        # port 1 on loopback refuses immediately and every reused code path
        # treats the cache as optional.
        "NEO4J_URI": "bolt://127.0.0.1:1",
        "KAFKA_BOOTSTRAP_SERVERS": "127.0.0.1:1",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "POSTGRES_URL": "postgresql+asyncpg://none:none@127.0.0.1:1/none",
        "CLICKHOUSE_HOST": "127.0.0.1",
        "CLICKHOUSE_PORT": "1",
        "SPAIDER_MCP_ENABLED": "false",
        "CONSOLIDATION_SCHEDULER_ENABLED": "false",
        # Working-memory behaviour: newer single-valued facts supersede older ones.
        "DEFAULT_SUPERSESSION": "true",
        "ENVIRONMENT": "portable",
    }


def bootstrap() -> None:
    """Idempotent. Must run before the first ``import app...``."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    for key, value in portable_environment().items():
        os.environ.setdefault(key, value)
    # Force the critical ones even if the host environment defines them: a
    # developer's OPENAI-style LLM_* variables must never leak into the
    # portable app.
    for key in (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_BASE_URL",
        "REDIS_URL",
        "NEO4J_URI",
        "KAFKA_BOOTSTRAP_SERVERS",
        "POSTGRES_URL",
    ):
        os.environ[key] = portable_environment()[key]

    if not getattr(sys, "frozen", False):
        bdir = backend_dir()
        if bdir is not None and str(bdir) not in sys.path:
            sys.path.insert(0, str(bdir))

    import litellm  # noqa: E402  (after env is set)

    litellm.telemetry = False
    litellm.suppress_debug_info = True
    _BOOTSTRAPPED = True
