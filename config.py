"""Configuration management for Asymptote API."""

from pathlib import Path
from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    # Data storage
    data_dir: Path = Path("./data")

    # Embedding configuration
    embedding_model: str = "all-MiniLM-L6-v2"

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

    # OCR configuration
    enable_ocr: bool = False  # Enable OCR for scanned PDFs
    ocr_max_pages: int = 25  # Skip OCR for PDFs with more pages than this (0 = no limit)
    ocr_max_file_mb: int = 50  # Skip OCR for files larger than this in MB (0 = no limit)

    # Vision AI OCR - used when enable_ocr=True and vision_ocr_provider is set
    vision_ocr_provider: str = "none"  # "anthropic" | "openai" | "ollama" | "none"
    vision_ocr_model: str = ""
    vision_ocr_api_key: str = ""  # Stored locally for indexing use
    vision_ocr_dpi: int = 150  # PDF render DPI (higher = better quality, slower)
    vision_ocr_enhance_image: bool = True  # Boost contrast/sharpness before sending to model
    vision_ocr_cleanup_pass: bool = True   # Run a second LLM pass to fix OCR errors
    vision_ocr_cleanup_model: str = ""  # Model for cleanup (empty = same as vision model)
    vision_ocr_ollama_url: str = "http://localhost:11434"
    vision_ocr_form_mode: bool = False      # Form-aware prompt + ruled-line image preprocessing

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
    csv_rows_per_chunk: int = 5  # Number of rows per chunk when not row-level

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

