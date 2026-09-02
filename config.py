"""Configuration management for Asymptote API."""

import os
from pathlib import Path
from typing import Literal, Optional
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Cloud providers that authenticate with a simple API key, passed per-request
# via the X-AI-Key header or stored server-side as a team key via
# /api/agent/config. AWS Bedrock is intentionally absent: it uses the AWS
# SigV4 credential chain (configured server-side), not a header key.
CLOUD_AI_PROVIDERS = ("anthropic", "openai", "grok", "google", "github", "openrouter", "ollama_cloud", "openai_compatible")
ALL_AI_PROVIDERS = CLOUD_AI_PROVIDERS + ("ollama",)


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    # Air-gapped / offline mode. When True:
    #   - Cloud AI providers (Anthropic, OpenAI, Grok, Google, GitHub,
    #     OpenRouter, Ollama Cloud, Bedrock) are disabled — only local Ollama
    #     and self-hosted OpenAI-compatible endpoints can be used.
    #   - HuggingFace hub access is disabled (HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE),
    #     so models load from the local cache only and never attempt a download.
    # The app makes NO outbound network connections beyond the endpoints you
    # explicitly configure on your own network. See docs/AIRGAP.md.
    offline_mode: bool = False

    # Data storage
    data_dir: Path = Path("./data")

    # Embedding configuration
    embedding_model: str = "all-MiniLM-L6-v2"
    # "local" = sentence-transformers (downloaded from HuggingFace)
    # "ollama" = Ollama /api/embed (fully local, no HF dependency)
    # "ollama_cloud" = ollama.com /api/embed with an API key — no local model
    #   at all; note chunks are sent to ollama.com at index time and queries
    #   at search time, so pick this only when that egress is acceptable.
    embedding_provider: Literal["local", "ollama", "ollama_cloud"] = "local"
    # Ollama Cloud API key, used by embedding_provider="ollama_cloud" and as
    # the automatic key for vision OCR when VISION_OCR_PROVIDER=ollama_cloud.
    # Accepted under either env name (people reasonably write both). Empty
    # falls back to the team key saved on the Ollama Cloud provider card
    # (Settings → AI Providers).
    ollama_cloud_api_key: str = Field(
        "", validation_alias=AliasChoices("OLLAMA_CLOUD_API_KEY", "OLLAMA_CLOUD_TOKEN")
    )
    ollama_base_url: str = "http://localhost:11434"   # used for embeddings and inference
    ollama_embedding_model: str = "nomic-embed-text"  # Ollama model when embedding_provider="ollama"

    # Semantic answer cache: single-turn chat questions that closely match a
    # previously answered one (cosine similarity of LOCAL embeddings — the
    # lookup never calls an API) return the stored answer instead of spending
    # provider tokens. Entries are invalidated when any source document the
    # answer cited changes or disappears, and a request with use_cache=false
    # (the Regenerate button) bypasses and replaces the entry. Note the
    # deliberate scope of invalidation: NEW unrelated documents don't evict
    # existing answers, so a cached answer reflects the corpus as of when it
    # was generated until its sources change or it's regenerated.
    enable_answer_cache: bool = True
    answer_cache_threshold: float = 0.9   # cosine similarity to count as "same question"
    answer_cache_max_per_scope: int = 200  # LRU cap per collection/scope

    # Share invitations by email via Resend (https://resend.com). When the
    # key is set, a collection owner can email a share invitation directly
    # from the share dialog: the recipient gets the share token and a join
    # link. RESEND_FROM must be a sender your Resend account may use — an
    # address on a domain you verified there (e.g. "Asymptote
    # <asymptote@your-domain>"); the default onboarding sender only delivers
    # to your own Resend account's email, so it's for testing. Disabled in
    # OFFLINE_MODE like every outbound integration.
    resend_api_key: str = ""
    resend_from: str = "Asymptote <onboarding@resend.dev>"

    # Text chunking configuration
    chunk_size: int = 1000
    chunk_overlap: int = 200

    # Search configuration
    default_top_k: int = 10
    max_top_k: int = 50

    # Local cross-encoder reranker (second-stage relevance reordering).
    # Runs locally (no API tokens, no PII egress) and applies to every search
    # caller, including the MCP tools. Opt-in because it downloads/loads a model
    # on first use. When enabled, retrieval fetches a wider candidate pool
    # (top_k * reranker_candidate_multiplier, capped) and the cross-encoder
    # reorders it down to top_k.
    enable_reranker: bool = False
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_candidate_multiplier: int = 5


    # Server configuration. The default binds loopback only: the app has no
    # auth unless AUTH_PASSWORD is set, so reaching the network has to be a
    # deliberate act. Docker and PaaS images set HOST=0.0.0.0 explicitly
    # because there the container boundary is what limits exposure.
    host: str = "127.0.0.1"
    port: int = 8473

    # CORS: comma-separated list of allowed browser origins, or "*" for any.
    # Empty (the default) sends no CORS headers, so only the same origin can
    # read a response — the bundled frontend is served same-origin and the Vite
    # dev server proxies to the backend, so neither needs this. Set it only for
    # a separate web app on another origin, and prefer listing that origin over
    # "*": with "*" any page the user visits can read this API. Credentialed
    # requests are allowed only for explicitly listed origins, never for "*".
    cors_allow_origins: str = ""

    # HTTPS / TLS. If both files exist, uvicorn serves HTTPS on `port`.
    # Leave empty to run plain HTTP (default). Paths are relative to the
    # project root unless absolute. Use `certs/generate-cert.sh` to create
    # a dev self-signed pair.
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    # MCP configuration
    enable_mcp: bool = True
    mcp_server_id: str = "asymptote"
    mcp_default_collection: str = "default"
    mcp_top_k: int = 5
    mcp_mode: Literal["semantic", "keyword", "hybrid"] = "semantic"
    mcp_semantic_weight: float = 0.7
    mcp_include_sources: bool = True
    mcp_max_source_length: int = 500
    mcp_ai_provider: str = "none"   # AI provider for MCP synthesis: "none" | "anthropic" | "openai" | "ollama"
    mcp_ollama_model: str = ""      # Ollama model to use when mcp_ai_provider = "ollama"

    # Database backend for app metadata (collections, shares, jobs, usage).
    # "postgresql" moves ONLY app.db — per-collection vector/BM25/metadata
    # stores, structured tables, the answer cache, and faiss.index all stay
    # as local files, so this is not HA and does not enable multi-replica.
    # See docs/DEPLOYMENT.md "Capacity & scaling".
    db_backend: Literal["sqlite", "postgresql"] = "sqlite"
    postgres_url: str = ""  # e.g. postgresql://user:pass@localhost:5432/asymptote

    # Multi-user mode is NOT supported and the app refuses to start with it on.
    # The flag only ever filtered the collection list — search, document
    # retrieval, chat and the /mcp tools take a collection_id and never checked
    # ownership — so it looked like an isolation boundary without being one.
    # The setting stays here (rather than being deleted) so existing .env files
    # fail loudly instead of silently changing behaviour. The supported
    # replacement is PRIVATE_COLLECTIONS below.
    enable_multi_user: bool = False
    default_user_id: str = "default"  # Owner recorded on every collection

    # Private collections: per-person ownership and sharing, enforced at every
    # entry point that takes a collection_id (search, documents, chat, /mcp).
    # Requires Cloudflare Access (CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD below)
    # so every request carries a cryptographically verified identity — the app
    # refuses to start with this flag on and no identity source configured,
    # because without one the boundary would be cosmetic.
    #
    # Semantics when on:
    #   - Collections owned by a person are visible only to the owner and to
    #     users who accepted a share link (read or readwrite).
    #   - Collections owned by "default" (everything created before this mode,
    #     and anything created by password-authenticated callers) are team
    #     collections: visible and writable to everyone, unchanged behaviour.
    #   - AUTH_PASSWORD callers have no identity: they see team collections
    #     only. MCP clients get an identity via Access service tokens (the
    #     token's common_name), so collections can be shared to them by name.
    private_collections: bool = False

    # Shared-secret auth. When set, every request (except /health) must present
    # this password via HTTP Basic auth (any username) or
    # `Authorization: Bearer <password>`. Browsers prompt natively, so no login
    # UI is needed. Required whenever the server is reachable beyond loopback.
    auth_password: str = ""

    # Trust Cloudflare Access authentication. When both are set, a request
    # carrying a valid Cf-Access-Jwt-Assertion (signed by the team's keys,
    # audience = one of the listed Access app AUD tags) is authenticated
    # WITHOUT the shared password — no browser Basic-auth prompt after SSO,
    # and service-token MCP clients drop the bearer header. AUTH_PASSWORD
    # still works as a fallback for direct/non-Access access paths.
    cf_access_team_domain: str = ""  # e.g. "yourteam.cloudflareaccess.com"
    cf_access_aud: str = ""  # comma-separated Access application AUD tags

    # Automated edge admission for share invitations. Sharing by email
    # delivers a share token, but the recipient is still stopped at the
    # Cloudflare Access login until their address is on the Access policy —
    # which otherwise means an admin editing the dashboard by hand for every
    # new person. With these set, inviting someone also admits them at the
    # edge, so the invitation is genuinely self-serve (pair it with the
    # one-time PIN login method and guests need no account in your IdP).
    #
    # CF_API_TOKEN needs exactly one permission: Account / Access: Apps and
    # Policies / Edit. That is write access to the deployment's front door,
    # so do not reuse a broader token. CF_ACCESS_POLICY_ID is the reusable
    # policy attached to the app; scripts/provision_cloudflare.py creates it
    # and writes all three values.
    #
    # ADMIN_EMAILS gates the privileged half: an admission grants edge access
    # to the whole deployment, not just the collection being shared (the
    # private-collections layer is what confines the person to that). Any
    # owner may still share; only an admin's invite admits a stranger. Empty
    # means nobody, so this fails closed rather than open.
    cf_api_token: str = ""
    cf_account_id: str = ""
    cf_access_policy_id: str = ""
    admin_emails: str = ""  # comma-separated

    # ── Load protection ──────────────────────────────────────────────────
    # These exist so one person can't starve everyone else on a shared
    # deployment — the shared free-tier LLM key and the single embedding
    # model are common resources. All limits are per identity (Cloudflare
    # Access email when present, else client IP), 0 disables that limit.

    # Requests per minute, by expense class. Chat turns can cost up to ~11
    # provider round-trips each; search is a local embed + FAISS scan;
    # everything else is cheap metadata traffic.
    rate_limit_enabled: bool = True
    rate_limit_chat_per_minute: int = 6
    rate_limit_search_per_minute: int = 30
    rate_limit_default_per_minute: int = 120

    # Provider tokens (input+output) one identity may spend on chat per UTC
    # day. 0 = unlimited. Cached answers are free and still served once the
    # budget is spent. Anonymous password callers share a single budget.
    chat_daily_token_budget: int = 0

    # Indexing jobs that may run concurrently across all collections. Every
    # job funnels through one process-wide embedding lock anyway, so more
    # parallel jobs mostly shuffle the queue while starving live search.
    max_concurrent_index_jobs: int = 2

    # Retention (days). search_history stores result snippets, so it is a
    # privacy liability with no expiry; chat_usage is small but unbounded.
    search_history_retention_days: int = 30
    usage_retention_days: int = 180

    # Extra Host header values the embedded /mcp endpoint accepts, comma-
    # separated (e.g. "asymptote.example.com"). The MCP SDK ships DNS-rebinding
    # protection that only trusts localhost Hosts by default; when the app is
    # served through a tunnel or reverse proxy under a public hostname, list
    # that hostname here. Localhost stays allowed either way.
    mcp_allowed_hosts: str = ""

    # OCR configuration — deliberately minimal: an on/off switch and an engine.
    # Scanned pages either go through a vision-capable LLM (best quality) or the
    # free local engine (Docling/Tesseract) when no provider is set. Rendering
    # and image-prep details are fixed at sensible defaults in the extractor.
    enable_ocr: bool = False  # OCR scanned PDFs during indexing
    ocr_max_pages: int = 25  # Cost guard: skip OCR for PDFs with more pages (0 = no limit; env-only)
    ocr_max_file_mb: int = 50  # Cost guard: skip OCR for files larger than this in MB (0 = no limit; env-only)

    # "auto" = zero-config: auto-pick a vision model from the local Ollama
    # install (no API key, no model selection needed). "none" = local OCR.
    vision_ocr_provider: str = "none"  # "auto" | "anthropic" | "openai" | "ollama" | "none"
    vision_ocr_model: str = ""  # Leave empty with "auto"/"ollama" to auto-detect the Ollama model
    vision_ocr_api_key: str = ""  # Stored locally so background indexing can use it

    # Local Ollama context window (num_ctx). Ollama defaults to a small context
    # (~2048 tokens) and SILENTLY truncates anything longer — which drops most of
    # the retrieved documents in a RAG prompt. We set num_ctx explicitly on every
    # Ollama call so local models actually see the context. Raise this for big
    # documents (bounded by the model's trained max and your VRAM).
    ollama_num_ctx: int = 8192

    # LLM-assisted column role inference (P0.5)
    # When enabled, columns that can't be mapped by vendor profiles or regex
    # heuristics are sent (with column names and sample values) to an LLM
    # for role assignment.  Requires mcp_ai_provider != "none" and a valid key.
    enable_llm_schema_inference: bool = False
    llm_schema_inference_threshold: float = 0.5  # Trigger when ≥ this fraction of columns are unmapped

    # UI feature flags
    enable_chat_tab: bool = True  # Show/hide the Chat tab in the frontend

    # v3.0: CSV indexing configuration
    csv_row_level_indexing: bool = True  # Index CSV rows individually

    # Bulk ingest tuning (upload/index jobs over many small files). Chunks are
    # accumulated across files until bulk_flush_chunks, then embedded together
    # (so EMBED_BATCH_SIZE batches actually fill) and persisted in one
    # SQLite/BM25/FAISS pass. Extraction runs in a small worker pool so it
    # overlaps embedding; embedding itself stays serialized on the encode lock.
    bulk_flush_chunks: int = 256
    bulk_extract_workers: int = 4

    # Audio transcription (meeting recordings via local Whisper)
    # Model size: tiny | base | small | medium | large-v3
    #   tiny   ~39MB   fastest, lowest accuracy
    #   base   ~142MB  good default for CPU
    #   small  ~466MB  better accuracy, ~2x slower than base
    #   medium ~1.5GB  much better, GPU recommended
    whisper_model: str = "base"
    whisper_device: str = "cpu"          # "cpu" or "cuda"
    whisper_compute_type: str = "int8"   # "int8" (fast, CPU) | "float16" (GPU) | "float32"
    whisper_language: str = ""           # "" = auto-detect, else ISO code like "en"

    # v3.0: Schema version (for data persistence)
    schema_version: str = "3.0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Ensure data directories exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "documents").mkdir(exist_ok=True)
        (self.data_dir / "indexes").mkdir(exist_ok=True)
        (self.data_dir / "backups").mkdir(exist_ok=True)  # v3.0: Backup directory

        # Air-gap enforcement must happen before huggingface_hub/transformers
        # are imported anywhere (they read these at import time). config is the
        # first app module imported by main.py, so this is early enough.
        if self.offline_mode:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


# Global settings instance
settings = Settings()

