"""Configuration management for Asymptote API."""

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

    # Data storage
    data_dir: Path = Path("./data")

    # Embedding configuration
    embedding_model: str = "all-MiniLM-L6-v2"
    # "local" = sentence-transformers (downloaded from HuggingFace)
    # "ollama" = Ollama /api/embed (fully local, no HF dependency)
    embedding_provider: Literal["local", "ollama"] = "local"
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


    # Server configuration
    host: str = "0.0.0.0"
    port: int = 8473

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

    # Multi-user mode (user isolation via X-User-ID header from auth proxy)
    enable_multi_user: bool = False  # Set to True for per-user data isolation
    default_user_id: str = "default"  # User ID used in single-user mode

    # Optional shared-secret auth for public deployments. When set, every
    # request (except /health) must present this password via HTTP Basic auth
    # (any username) or `Authorization: Bearer <password>`. Browsers prompt
    # natively, so no login UI is needed. Leave empty on trusted networks.
    auth_password: str = ""

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


# Global settings instance
settings = Settings()

