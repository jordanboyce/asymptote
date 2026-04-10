"""Pydantic schemas for API request/response models."""

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ChunkMetadata(BaseModel):
    """Metadata for a single text chunk."""

    chunk_id: str = Field(..., description="Unique identifier for the chunk")
    document_id: str = Field(..., description="Parent document identifier")
    filename: str = Field(..., description="Original filename")
    page_number: int = Field(..., description="Page number or section number (1-indexed)")
    chunk_index: int = Field(..., description="Index of chunk within the page")
    text: str = Field(..., description="Chunk text content")

    # v3.0: Format-aware metadata
    source_format: Optional[str] = Field(None, description="Source format: pdf, txt, docx, csv, md, json")
    extraction_method: Optional[str] = Field(None, description="Extraction method: text, ocr, hybrid")

    # v3.0: CSV-specific metadata (for row-level indexing)
    csv_row_number: Optional[int] = Field(None, description="Original row number in CSV (for CSV chunks)")
    csv_columns: Optional[List[str]] = Field(None, description="Column names for CSV row")
    csv_values: Optional[Dict[str, Any]] = Field(None, description="Column-value pairs for CSV row")

    # v3.0: Code-specific metadata (for source code indexing)
    language: Optional[str] = Field(None, description="Programming language: pascal, delphi, modula2, assembly")
    unit_name: Optional[str] = Field(None, description="Unit/module name for code files")
    symbol_name: Optional[str] = Field(None, description="Function/procedure/class name")
    symbol_type: Optional[str] = Field(None, description="Symbol type: procedure, function, class, record, macro, label")
    line_start: Optional[int] = Field(None, description="Starting line number in source file")
    line_end: Optional[int] = Field(None, description="Ending line number in source file")
    parent_symbol: Optional[str] = Field(None, description="Parent class/module for nested symbols")


class DocumentMetadata(BaseModel):
    """Metadata for an indexed document."""

    document_id: str = Field(..., description="Unique identifier for the document")
    filename: str = Field(..., description="Original filename")
    total_pages: int = Field(..., description="Total number of pages")
    total_chunks: int = Field(..., description="Total number of chunks created")
    indexed_at: str = Field(default="", description="ISO timestamp of indexing")

    # v3.0: Document-level versioning and format info
    source_format: Optional[str] = Field(None, description="Source format: pdf, txt, docx, csv, md, json")
    extraction_method: Optional[str] = Field(None, description="Primary extraction method used")
    embedding_model: Optional[str] = Field(None, description="Embedding model used for this document")
    chunk_size: Optional[int] = Field(None, description="Chunk size used during indexing")
    chunk_overlap: Optional[int] = Field(None, description="Chunk overlap used during indexing")
    schema_version: str = Field(default="3.1", description="Schema version for migration compatibility")

    # v3.1: Local file reference support
    source_path: Optional[str] = Field(None, description="Original filesystem path for local references")
    source_type: str = Field(default="upload", description="Source type: 'upload' or 'local_reference'")

    # v3.2: Prompt injection warnings (page_num -> scan result dict)
    injection_warnings: Optional[Dict[str, Any]] = Field(None, description="Flagged pages with injection scan details")


class SearchResult(BaseModel):
    """A single search result."""

    filename: str = Field(..., description="Document filename")
    page_number: int = Field(..., description="Page or section number (1-indexed)")
    text_snippet: str = Field(..., description="Matching text chunk")
    similarity_score: float = Field(..., description="Cosine similarity score (0-1)")
    document_id: str = Field(..., description="Document identifier")
    chunk_id: str = Field(..., description="Chunk identifier")
    pdf_url: str = Field(..., description="URL to download the document")
    page_url: str = Field(..., description="URL to view the specific page")

    # v3.0: Format-aware result metadata
    source_format: Optional[str] = Field(None, description="Source format: pdf, txt, docx, csv, md, json")
    extraction_method: Optional[str] = Field(None, description="How text was extracted: text, ocr, hybrid")

    # v3.0: CSV-specific result data (for table rendering)
    csv_row_number: Optional[int] = Field(None, description="Row number for CSV results")
    csv_columns: Optional[List[str]] = Field(None, description="Column names for CSV row")
    csv_values: Optional[Dict[str, Any]] = Field(None, description="Column-value pairs for CSV row")

    # v3.0: Code-specific result data (for code navigation)
    language: Optional[str] = Field(None, description="Programming language")
    unit_name: Optional[str] = Field(None, description="Unit/module name")
    symbol_name: Optional[str] = Field(None, description="Function/procedure/class name")
    symbol_type: Optional[str] = Field(None, description="Symbol type")
    line_start: Optional[int] = Field(None, description="Starting line number")
    line_end: Optional[int] = Field(None, description="Ending line number")

    # v3.1: Local file reference support
    source_type: Optional[str] = Field(None, description="Source type: 'upload' or 'local_reference'")
    source_path: Optional[str] = Field(None, description="Original filesystem path for local references")


class UploadResponse(BaseModel):
    """Response from document upload endpoint."""

    message: str = Field(..., description="Status message")
    documents_processed: int = Field(..., description="Number of documents processed")
    total_pages: int = Field(..., description="Total pages across all documents")
    total_chunks: int = Field(..., description="Total chunks created")
    document_ids: List[str] = Field(..., description="List of created document IDs")


class UploadPhase(str, Enum):
    """Upload processing phases for granular progress tracking."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    SAVING = "saving"
    COMPLETED = "completed"
    FAILED = "failed"


class UploadJobResponse(BaseModel):
    """Response from async upload job endpoint."""

    job_id: int = Field(..., description="Unique job identifier")
    collection_id: str = Field(..., description="Target collection ID")
    status: str = Field(..., description="Job status: pending, running, completed, failed")
    total_files: int = Field(..., description="Total files to process")
    processed_files: int = Field(..., description="Files processed so far")
    current_file: Optional[str] = Field(None, description="File currently being processed")
    progress_percent: float = Field(0.0, description="Progress percentage (0-100)")
    error: Optional[str] = Field(None, description="Error message if failed")
    result_summary: Optional[Dict[str, Any]] = Field(None, description="Results when completed")
    started_at: Optional[str] = Field(None, description="ISO timestamp when job started")
    completed_at: Optional[str] = Field(None, description="ISO timestamp when job completed")

    # v4.0: Granular progress tracking
    phase: Optional[str] = Field(None, description="Current processing phase")
    phase_progress: Optional[int] = Field(None, description="Progress within current phase (0-100)")
    phase_detail: Optional[str] = Field(None, description="Detailed phase status message")
    chunks_processed: Optional[int] = Field(None, description="Chunks processed in current file")
    chunks_total: Optional[int] = Field(None, description="Total chunks in current file")


class AIOptions(BaseModel):
    """Optional AI enhancement settings for search."""

    provider: str = Field("anthropic", description="AI provider: 'anthropic', 'openai', or 'ollama'")
    rerank: bool = Field(False, description="Rerank results using AI for better relevance")
    synthesize: bool = Field(False, description="Generate an AI summary with citations")


class SearchMode(str, Enum):
    """Search mode options."""

    SEMANTIC = "semantic"  # Pure semantic/embedding search
    KEYWORD = "keyword"    # Pure BM25 keyword search
    HYBRID = "hybrid"      # Combined semantic + keyword


class SearchRequest(BaseModel):
    """Request body for search endpoint."""

    query: str = Field(..., description="Search query text", min_length=1)
    top_k: int = Field(10, description="Number of results to return", ge=1, le=50)
    mode: SearchMode = Field(SearchMode.SEMANTIC, description="Search mode: semantic, keyword, or hybrid")
    semantic_weight: float = Field(
        0.7,
        description="Weight for semantic search in hybrid mode (0-1). Higher = more semantic.",
        ge=0.0,
        le=1.0
    )
    ai: Optional[AIOptions] = Field(None, description="Optional AI enhancement settings")


class AIUsageDetail(BaseModel):
    """Token usage for a single AI operation."""

    input_tokens: int = Field(..., description="Input tokens consumed")
    output_tokens: int = Field(..., description="Output tokens consumed")
    model: str = Field(..., description="Model used")


class AIUsage(BaseModel):
    """AI usage metadata for cost transparency."""

    features_used: List[str] = Field(default_factory=list, description="AI features that were applied")
    reranking: Optional[AIUsageDetail] = None
    synthesis: Optional[AIUsageDetail] = None
    total_input_tokens: int = Field(0, description="Total input tokens across all AI calls")
    total_output_tokens: int = Field(0, description="Total output tokens across all AI calls")


class SearchResponse(BaseModel):
    """Response from search endpoint."""

    query: str = Field(..., description="Original search query")
    results: List[SearchResult] = Field(..., description="Ranked search results")
    total_results: int = Field(..., description="Total number of results returned")
    synthesis: Optional[str] = Field(None, description="AI-generated answer with citations")
    ai_usage: Optional[AIUsage] = Field(None, description="AI token usage for cost transparency")


class DocumentListResponse(BaseModel):
    """Response from document list endpoint."""

    documents: List[DocumentMetadata] = Field(..., description="List of indexed documents")
    total_documents: int = Field(..., description="Total number of documents")


class DocumentChunkView(BaseModel):
    """Chunk payload for document chunk inspection."""

    chunk_id: str = Field(..., description="Unique identifier for the chunk")
    page_number: int = Field(..., description="Page or section number")
    chunk_index: int = Field(..., description="Chunk index within page")
    text: str = Field(..., description="Chunk text")
    source_format: Optional[str] = Field(None, description="Source format")
    extraction_method: Optional[str] = Field(None, description="Extraction method")
    extracted_fields: Optional[Dict[str, str]] = Field(
        None,
        description="Heuristically extracted field/value pairs from chunk text"
    )
    form_score: Optional[float] = Field(
        None,
        description="Estimated form-likeness score (0-1) used to decide field extraction"
    )
    form_like: Optional[bool] = Field(
        None,
        description="Whether chunk text was classified as form-like"
    )
    field_extraction_applied: Optional[bool] = Field(
        None,
        description="Whether field extraction was applied to this chunk"
    )


class DocumentChunksResponse(BaseModel):
    """Response for listing chunks in a single document."""

    document_id: str = Field(..., description="Document identifier")
    filename: str = Field(..., description="Original filename")
    extraction_method: Optional[str] = Field(None, description="Document extraction method")
    total_chunks: int = Field(..., description="Total chunks stored for this document")
    returned_chunks: int = Field(..., description="Chunks returned in this response")
    chunks: List[DocumentChunkView] = Field(..., description="Chunk records")


# Agent API schemas
class AskRequest(BaseModel):
    """Request for the /api/ask endpoint optimized for coding agents."""

    question: str = Field(..., description="The question to answer", min_length=1)
    collection_id: str = Field("default", description="Collection to search")
    top_k: int = Field(5, description="Number of source chunks to consider", ge=1, le=20)
    rerank: bool = Field(True, description="Rerank results using AI before synthesis (improves answer quality)")
    include_sources: bool = Field(True, description="Include source excerpts in response")
    max_source_length: int = Field(500, description="Max characters per source excerpt", ge=100, le=2000)
    format: str = Field("markdown", description="Response format: 'text', 'markdown', or 'json'")
    mode: SearchMode = Field(SearchMode.SEMANTIC, description="Search mode: semantic, keyword, or hybrid")
    semantic_weight: float = Field(
        0.7,
        description="Weight for semantic search in hybrid mode (0-1). Higher = more semantic.",
        ge=0.0,
        le=1.0
    )


class AskSource(BaseModel):
    """A source used to answer the question."""

    filename: str = Field(..., description="Source document filename")
    page: int = Field(..., description="Page or section number")
    excerpt: str = Field(..., description="Relevant text excerpt")
    relevance: float = Field(..., description="Similarity score (0-1)")


class AskResponse(BaseModel):
    """Response from the /api/ask endpoint."""

    answer: str = Field(..., description="The synthesized answer to the question")
    sources: List[AskSource] = Field(default_factory=list, description="Sources used for the answer")
    collection_id: str = Field(..., description="Collection that was searched")
    model: str = Field(..., description="AI model used for synthesis")
    tokens_used: int = Field(0, description="Total tokens consumed")


# Chat schemas
class ChatMessage(BaseModel):
    """A single message in a chat conversation."""

    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class ChatSource(BaseModel):
    """A document chunk retrieved as context for a chat response."""

    filename: str = Field(..., description="Source document filename")
    page_number: int = Field(..., description="Page or section number")
    text_snippet: str = Field(..., description="Relevant text excerpt")
    similarity_score: float = Field(..., description="Similarity score (0-1)")
    document_id: str = Field(..., description="Document identifier")
    pdf_url: str = Field(..., description="URL to download the document")
    page_url: str = Field(..., description="URL to view the specific page")


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    messages: List[ChatMessage] = Field(..., description="Conversation history including the latest user message")
    provider: str = Field("anthropic", description="AI provider: 'anthropic', 'openai', or 'ollama'")
    mode: SearchMode = Field(SearchMode.SEMANTIC, description="Search mode for context retrieval")
    scope: str = Field("current", description="Collection scope: 'current' (single collection) or 'all' (search across all collections)")
    rerank: bool = Field(False, description="Rerank retrieved context chunks using AI before generating a response")
    top_k: int = Field(5, description="Number of source chunks to consider", ge=1, le=20)


class ChatResponse(BaseModel):
    """Response from the chat endpoint."""

    message: ChatMessage = Field(..., description="Assistant's response message")
    sources: List[ChatSource] = Field(default_factory=list, description="Document chunks used as context")
    ai_usage: Optional[AIUsage] = Field(None, description="AI token usage")


# Repository/Folder upload schemas
class RepoUploadRequest(BaseModel):
    """Request for uploading a code repository or folder."""

    path: str = Field(..., description="Local filesystem path to the repository or folder")
    collection_id: str = Field("default", description="Collection to add files to")
    recursive: bool = Field(True, description="Recursively scan subdirectories")
    include_patterns: Optional[List[str]] = Field(
        None,
        description="Glob patterns to include (e.g., ['*.pas', '*.dpr']). If not set, uses all supported extensions."
    )
    exclude_patterns: Optional[List[str]] = Field(
        default_factory=lambda: ["**/node_modules/**", "**/.git/**", "**/build/**", "**/dist/**", "**/__pycache__/**"],
        description="Glob patterns to exclude"
    )


class RepoUploadResponse(BaseModel):
    """Response from repository upload endpoint."""

    message: str = Field(..., description="Status message")
    job_id: Optional[int] = Field(None, description="Background job ID for async processing")
    files_found: int = Field(0, description="Total files found matching patterns")
    files_indexed: int = Field(0, description="Files successfully indexed")
    files_failed: int = Field(0, description="Files that failed to index")
    total_chunks: int = Field(0, description="Total chunks created")
    document_ids: List[str] = Field(default_factory=list, description="List of created document IDs")
    failed_files: List[Dict[str, str]] = Field(default_factory=list, description="List of files that failed with error messages")
