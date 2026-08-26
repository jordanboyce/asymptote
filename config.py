"""Configuration management for Asymptote API."""

import os
from pathlib import Path
from typing import Literal, Optional
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
    # Key for embedding_provider="ollama_cloud". Empty falls back to the team
    # key saved on the Ollama Cloud provider card (Settings → AI Providers).
    ollama_cloud_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"   # used for embeddings and inference
    ollama_embedding_model: str = "nomic-embed-text"  # Ollama model when embedding_provider="ollama"

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

    # Database backend: "sqlite" for single-user/simple, "postgresql" for multi-user/enterprise
    db_backend: Literal["sqlite", "postgresql"] = "sqlite"
    postgres_url: str = ""  # e.g. postgresql://user:pass@localhost:5432/asymptote

    # Multi-user mode is NOT supported and the app refuses to start with it on.
    # Asymptote is a shared team appliance: everyone who can reach it sees the
    # whole corpus, and access is controlled at the edge (see
    # docs/DEPLOYMENT.md). The flag only ever filtered the collection list —
    # search, document retrieval, chat and the /mcp tools take a collection_id
    # and never check ownership — so it looked like an isolation boundary
    # without being one. Turning it into a real one means threading user_id
    # through every one of those entry points; the setting stays here (rather
    # than being deleted) so existing .env files fail loudly instead of
    # silently changing behaviour.
    enable_multi_user: bool = False
    default_user_id: str = "default"  # Owner recorded on every collection

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

