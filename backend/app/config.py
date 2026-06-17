from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    # Dev-only fallback. Every real deployment overrides this via NEO4J_PASSWORD
    # in .env (`spaider init` generates a random one).
    neo4j_password: str = "spaider-dev-2024"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic_ingest: str = "spaider.ingest.raw"
    kafka_topic_dlq: str = "spaider.ingest.dlq"
    kafka_consumer_group: str = "spaider-compressor-workers"
    kafka_agent_namespace: str = "default"
    kafka_topic_workflow_events: str = "spaider.workflow.events"
    kafka_replay_consumer_group: str = "spaider-replay-readers"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # LLM (via LiteLLM)
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_base_url: Optional[str] = None
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096

    # Embeddings
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""
    embedding_base_url: Optional[str] = None
    embedding_dimensions: int = Field(
        default=1536,
        description=(
            "Dimensionality of the embedding vectors produced by `embedding_model`. "
            "Defaults to 1536 (OpenAI text-embedding-3-small / text-embedding-ada-002). "
            "\n\n"
            "Bring Your Own Vectors (BYOV) support\n"
            "--------------------------------------\n"
            "Callers may supply pre-computed embeddings on Node objects instead of\n"
            "letting EntityResolver call the embedding service.  EntityResolver\n"
            "validates each supplied embedding against this value:\n"
            "  - Correct dimension  → embedding is preserved as-is (zero LLM cost).\n"
            "  - Wrong dimension, API caller   → HTTP 422 raised immediately.\n"
            "  - Wrong dimension, Kafka caller → warning logged, node re-embedded.\n"
            "  - No embedding supplied         → node is embedded normally.\n"
            "\n"
            "Override via environment variable EMBEDDING_DIMENSIONS when switching\n"
            "to a different model (e.g. 768 for text-embedding-3-large truncated,\n"
            "384 for all-MiniLM-L6-v2, etc.)."
        ),
    )

    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    # ClickHouse
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_database: str = "spaider"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""

    # PostgreSQL (connector state & secrets store)
    # Scheme MUST be postgresql+asyncpg:// — the sync psycopg2 scheme will
    # deadlock inside an asyncio event loop.
    postgres_url: str = "postgresql+asyncpg://spaider:spaider@localhost:5432/spaider"

    # Connector secret encryption key — 32-byte value base64-encoded.
    # Used as the Key Encryption Key (KEK) for envelope-encrypting connector
    # credentials at rest in ConnectorSecret.ciphertext.
    # Generate with: python -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
    connector_secret_key: str = ""

    # Rate Limiting
    rate_limit_requests_per_minute: int = 60

    # LiteLLM call retry — exponential backoff on transient provider
    # errors (RateLimitError, APIConnectionError, Timeout, 5xx). Without
    # this, gpt-4o-class models under burst load reliably produce 429s
    # that propagate as workflow failures. See backend/app/lib/litellm_retry.py.
    litellm_retry_max_attempts: int = Field(
        default=5,
        ge=1,
        le=10,
        description=(
            "Maximum number of attempts (including the first) for each "
            "litellm.acompletion call. Set to 1 to disable retry. "
            "Env var: LITELLM_RETRY_MAX_ATTEMPTS."
        ),
    )
    litellm_retry_base_delay: float = Field(
        default=1.0,
        ge=0.0,
        le=30.0,
        description=(
            "Initial backoff delay in seconds. Doubles each retry, with "
            "±50%% jitter. Provider-supplied Retry-After hints override "
            "this when present. Env var: LITELLM_RETRY_BASE_DELAY."
        ),
    )

    # Agentic QA loop guardrails
    max_qa_iterations: int = Field(
        default=3,
        description=(
            "Maximum number of retrieve-verify iterations before the loop "
            "forces a final synthesis with accumulated context."
        ),
    )
    qa_time_budget_seconds: float = Field(
        default=15.0,
        description=(
            "Wall-clock budget for the full QA loop (seconds). When exceeded "
            "the loop breaks immediately and synthesises from whatever context "
            "has been accumulated so far."
        ),
    )

    # Multi-subquery seed expansion. When enabled, query_nl runs a small
    # LLM call to split the natural-language question into 1-5 focused
    # sub-questions, then performs vector search per sub-question and
    # unions the seed-node sets before traversal. Targets bridge /
    # comparison questions where a single embedding misses an entity.
    # Adds ~1 LLM call (~$0.001 with gpt-4o-mini) per query; off by
    # default so existing deployments are unchanged.
    query_decomposition_enabled: bool = Field(
        default=False,
        description=(
            "Decompose multi-entity questions into focused sub-questions before "
            "vector search; union the resulting seed-node sets."
        ),
    )

    # Synthesis Ensemble — Best-of-N parallel generation.
    # N=1 keeps the existing single-call fast path (no overhead).
    # N>1 generates N candidates in parallel at the given temperature,
    # evaluates each with the verifier, and returns the highest-confidence answer.
    synthesis_ensemble_n: int = Field(
        default=1,
        description=(
            "Number of parallel synthesis candidates to generate. "
            "1 = single-call fast path (default). "
            ">1 enables Best-of-N verifier ranking. "
            "Env var: SYNTHESIS_ENSEMBLE_N."
        ),
    )
    synthesis_ensemble_temperature: float = Field(
        default=0.7,
        description=(
            "LiteLLM temperature used for ensemble candidate generation. "
            "Higher values increase diversity across candidates. "
            "Ignored when synthesis_ensemble_n == 1. "
            "Env var: SYNTHESIS_ENSEMBLE_TEMPERATURE."
        ),
    )

    # MCP server (SpAIder-as-MCP-server, exposed at /api/v1/mcp/sse).
    # Set to false to skip mounting the MCP routes entirely — useful when
    # ops policy requires LLM agents to reach SpAIder only through the
    # REST API, or when the deployment doesn't issue agent API keys to
    # external MCP clients. Defaults to true; the routes are still
    # auth-gated whether enabled or not.
    spaider_mcp_enabled: bool = Field(
        default=True,
        description="Mount the SpAIder-as-MCP-server routes at /api/v1/mcp/*.",
    )

    # Airflow REST API integration — used by the on-demand consolidate
    # endpoint to trigger the graph_maintenance DAG outside
    # its normal weekly cadence. When unset, POST /api/v1/system/consolidate
    # returns 503 with a message pointing operators at the CLI fallback
    # (``python -m app.scripts.run_consolidation``).
    airflow_base_url: str = Field(
        default="",
        description=(
            "Base URL of the Airflow webserver (e.g. http://airflow-webserver:8080). "
            "Empty disables on-demand DAG triggering."
        ),
    )
    airflow_username: str = Field(
        default="admin",
        description="Airflow basic-auth username (matches docker-compose.airflow.yml default)."
    )
    airflow_password: str = Field(
        default="spaider-airflow",
        description="Airflow basic-auth password (matches docker-compose.airflow.yml default)."
    )
    airflow_consolidate_dag_id: str = Field(
        default="spaider_graph_maintenance",
        description="DAG id triggered by POST /api/v1/system/consolidate.",
    )

    # Alchemist Pass — Tier 3 proactive knowledge-graph completion.
    # Off by default; set CONSOLIDATION_PROPOSE_EDGES=true to activate.
    # The cosine band [cosine_min, cosine_max] targets pairs that are
    # semantically related but not identical — too similar pairs are likely
    # duplicates (handled by the fuse pass); too distant pairs are unrelated.
    consolidation_propose_edges: bool = Field(
        default=False,
        description=(
            "Enable the Alchemist inverse pass: use an LLM to propose new "
            "RELATION edges between semantically similar nodes that have no "
            "existing path of length ≤ 2. Env var: CONSOLIDATION_PROPOSE_EDGES."
        ),
    )
    consolidation_propose_path_max: int = Field(
        default=1,
        ge=1,
        le=4,
        description=(
            "Maximum existing path length between a candidate pair before the "
            "alchemist will propose a new edge. With default=1, the alchemist "
            "only proposes when there is no DIRECT edge between the pair "
            "(useful when MENTIONS-style ingestion already creates 2-hop "
            "connectivity across most semantically-related entities). "
            "Set to 2 to require true 2-hop unconnectedness; setting higher "
            "values only matters on sparser graph topologies. "
            "Env var: CONSOLIDATION_PROPOSE_PATH_MAX."
        ),
    )
    consolidation_propose_cosine_min: float = Field(
        default=0.78,
        description=(
            "Lower bound of the cosine-similarity band for candidate pairs. "
            "Pairs below this are too dissimilar to propose a link. "
            "Env var: CONSOLIDATION_PROPOSE_COSINE_MIN."
        ),
    )
    consolidation_propose_cosine_max: float = Field(
        default=0.92,
        description=(
            "Upper bound of the cosine-similarity band for candidate pairs. "
            "Pairs above this are near-duplicates (fuse pass handles them). "
            "Env var: CONSOLIDATION_PROPOSE_COSINE_MAX."
        ),
    )
    consolidation_propose_min_confidence: float = Field(
        default=0.8,
        description=(
            "Minimum LLM confidence score required to persist a proposed edge. "
            "Proposals below this threshold are discarded silently. "
            "Env var: CONSOLIDATION_PROPOSE_MIN_CONFIDENCE."
        ),
    )

    # App
    app_name: str = "SpAIder"
    debug: bool = False
    environment: str = "development"

    model_config = {"env_file": ".env", "populate_by_name": True, "extra": "ignore"}

    @property
    def litellm_model(self) -> str:
        """Return model with provider prefix for LiteLLM when missing."""
        model = (self.llm_model or "").strip()
        if not model:
            return model
        if "/" in model:
            return model

        provider = (self.llm_provider or "").strip().lower()
        base_url = (self.llm_base_url or "").strip().lower()
        if "ollama.com/api" in base_url or base_url.endswith(":11434"):
            provider = "ollama"
        if provider == "openai":
            return model
        return f"{provider}/{model}" if provider else model

    @property
    def litellm_embedding_model(self) -> str:
        """Return embedding model with provider prefix when required."""
        model = (self.embedding_model or "").strip()
        if not model:
            return model
        if "/" in model:
            return model

        provider = (self.embedding_provider or "").strip().lower()
        base_url = (self.embedding_base_url or "").strip().lower()
        if "ollama.com/api" in base_url or base_url.endswith(":11434"):
            provider = "ollama"
        if provider == "openai":
            return model
        return f"{provider}/{model}" if provider else model


settings = Settings()
