# Asymptote Roadmap

This document outlines planned features and enhancements for future versions of Asymptote.

## Current Status (v1.x)

**Implemented:**
- ✅ Multi-format document support (PDF, TXT, DOCX, CSV)
- ✅ Semantic search with FAISS vector similarity
- ✅ Dual metadata storage (JSON and SQLite)
- ✅ Optional AI enhancements (Claude/OpenAI for reranking and synthesis)
- ✅ Vue 3 web interface with dark mode
- ✅ Windows desktop application with system tray
- ✅ Docker deployment support
- ✅ Corporate SSL certificate handling

---

## Next Revision (v2.0) - Dynamic Configuration & Local AI

### 1. Frontend Configuration Management

**Goal:** Allow users to configure indexing and model settings through the web interface instead of editing config files.

#### 1.1 Embedding Model Selection
- **Feature:** Dropdown or search UI to select embedding models
- **Models to support:**
  - Current: `all-MiniLM-L6-v2` (default, 384 dim)
  - Quality: `all-mpnet-base-v2` (768 dim, slower but more accurate)
  - Speed: `paraphrase-MiniLM-L3-v2` (384 dim, fastest)
  - Multilingual: `paraphrase-multilingual-MiniLM-L12-v2`
  - Custom: Allow entering any sentence-transformers model name
- **Implementation considerations:**
  - Model switching requires re-indexing all documents
  - Show download progress for new models
  - Display model info (size, dimensions, language support)
  - Warn user about re-indexing requirement
  - Store model preference in backend config

**UI Mockup:**
```
Settings Tab > Indexing Configuration
┌─────────────────────────────────────────┐
│ Embedding Model:                        │
│ ┌───────────────────────────────────┐   │
│ │ all-MiniLM-L6-v2 (Current)     ▼ │   │
│ └───────────────────────────────────┘   │
│ Model Info:                             │
│ - Dimensions: 384                       │
│ - Size: ~90 MB                          │
│ - Language: English                     │
│                                         │
│ ⚠️  Changing model requires re-indexing │
│     all documents                       │
│                                         │
│ [Apply and Re-index]                    │
└─────────────────────────────────────────┘
```

#### 1.2 Metadata Storage Toggle
- **Feature:** Switch between JSON and SQLite storage from the UI
- **Functionality:**
  - Radio button or toggle switch
  - Display storage type statistics:
    - JSON: Document count, memory usage estimate
    - SQLite: Document count, database file size
  - Show migration status if switching
  - Auto-migrate data when switching (with progress bar)
  - Warn about performance implications

**UI Mockup:**
```
Settings Tab > Storage Configuration
┌─────────────────────────────────────────┐
│ Metadata Storage:                       │
│ ○ JSON (Simple, <100 docs)              │
│ ● SQLite (Scalable, 1000+ docs)         │
│                                         │
│ Current Storage Stats:                  │
│ - Documents: 127                        │
│ - Database size: 2.4 MB                 │
│ - Index size: 15.3 MB                   │
│                                         │
│ [Switch to JSON] (requires migration)   │
└─────────────────────────────────────────┘
```

#### 1.3 Chunking Configuration
- **Feature:** Adjust chunk size and overlap dynamically
- **Controls:**
  - Chunk size slider (200-2000 characters)
  - Overlap slider (0-500 characters)
  - Visual preview of how many chunks a sample document would create
  - Recommendation based on document collection size
- **Effect:** Requires re-indexing to apply

**UI Mockup:**
```
Settings Tab > Text Chunking
┌─────────────────────────────────────────┐
│ Chunk Size: 600 characters              │
│ ├────────●──────────────┤ (200-2000)    │
│                                         │
│ Overlap: 100 characters                 │
│ ├──●────────────────────┤ (0-500)       │
│                                         │
│ Preview: A 10-page document would       │
│ create ~45 chunks with these settings   │
│                                         │
│ Recommendation: For large collections,  │
│ increase chunk size to reduce memory    │
│                                         │
│ [Apply and Re-index]                    │
└─────────────────────────────────────────┘
```

#### 1.4 Configuration Persistence
- **Backend changes:**
  - New API endpoints:
    - `GET /api/config` - Retrieve current configuration
    - `POST /api/config` - Update configuration (triggers restart if needed)
    - `POST /api/reindex` - Trigger full re-indexing with new settings
  - Store config in database or config file
  - Hot-reload for non-critical settings
  - Server restart trigger for model changes

---

### 2. Online Document Support

**Goal:** Allow users to index documents stored at URLs instead of only uploaded files.

#### 2.1 URL-Based Document Indexing
- **Feature:** Accept URLs to PDF/document files
- **Supported sources:**
  - Direct document URLs (e.g., `https://example.com/paper.pdf`)
  - Cloud storage links (Google Drive, Dropbox, OneDrive with public access)
  - Web pages (extract text content from HTML)
- **Functionality:**
  - Download document to temporary location
  - Extract text using existing DocumentExtractor
  - Store URL as metadata (for re-fetching updates)
  - Option to cache locally or fetch on-demand
  - Support authentication for private URLs (API keys, bearer tokens)

**UI Mockup:**
```
Upload Tab
┌─────────────────────────────────────────┐
│ Upload Method:                          │
│ ● Local Files  ○ URL                    │
│                                         │
│ Document URLs (one per line):           │
│ ┌───────────────────────────────────┐   │
│ │ https://arxiv.org/pdf/2301.00234 │   │
│ │ https://example.com/manual.pdf    │   │
│ │                                   │   │
│ └───────────────────────────────────┘   │
│                                         │
│ ☑ Cache documents locally               │
│ ☐ Require authentication                │
│                                         │
│ [Index URLs]                            │
└─────────────────────────────────────────┘
```

#### 2.2 URL Document Management
- **Features:**
  - Show document source (uploaded vs URL) in documents list
  - "Refresh from URL" button to re-fetch and re-index
  - Automatic periodic refresh option (configurable interval)
  - Handle URL changes (404, moved, etc.)
  - Support URL placeholders for paginated documents

**Backend Implementation:**
- New schema field: `source_type` ("upload" or "url")
- New schema field: `source_url` (null for uploads)
- New field: `last_refreshed` (timestamp for URL documents)
- Download service with retry logic and timeout
- Support for `Range` requests for large files
- Content-Type detection and validation

---

### 3. Ollama Integration

**Goal:** Support local LLM inference via Ollama as an alternative to Anthropic/OpenAI.

#### 3.1 Ollama Detection
- **Feature:** Auto-detect if Ollama is installed and running
- **Implementation:**
  - Check for Ollama API at `http://localhost:11434` on startup
  - Ping `/api/tags` to get available models
  - Show Ollama status in settings UI
  - Fallback gracefully if Ollama is not available

**Detection Logic:**
```python
async def detect_ollama():
    try:
        response = await httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        if response.status_code == 200:
            models = response.json().get("models", [])
            return {"available": True, "models": models}
    except:
        return {"available": False, "models": []}
```

#### 3.2 Ollama as AI Provider
- **Feature:** Add Ollama as a third AI provider option
- **Supported models:**
  - `llama3.2` (Meta's Llama 3.2)
  - `mistral` (Mistral 7B)
  - `phi3` (Microsoft Phi-3)
  - `gemma2` (Google Gemma 2)
  - Any other installed Ollama model
- **Functionality:**
  - Use Ollama for reranking results
  - Use Ollama for answer synthesis
  - No API key required (local inference)
  - Show model selection dropdown (only models actually pulled)

**UI Mockup:**
```
Settings Tab > AI Provider
┌─────────────────────────────────────────┐
│ Provider:                               │
│ ○ Anthropic (Claude)                    │
│ ○ OpenAI (GPT)                          │
│ ● Ollama (Local) ✓ Available           │
│                                         │
│ Ollama Model:                           │
│ ┌───────────────────────────────────┐   │
│ │ llama3.2 (8GB)                 ▼ │   │
│ └───────────────────────────────────┘   │
│                                         │
│ Available Models:                       │
│ - llama3.2 (pulled, 8GB)                │
│ - mistral (pulled, 7GB)                 │
│ - phi3 (not pulled)                     │
│                                         │
│ [Pull New Model]                        │
│                                         │
│ Features:                               │
│ ☑ Reranking (free, local)               │
│ ☑ Synthesis (free, local)               │
└─────────────────────────────────────────┘
```

#### 3.3 Ollama Provider Implementation
- **New class:** `OllamaProvider` extending `AIProvider`
- **Endpoints:**
  - `/api/generate` - For synthesis
  - `/api/chat` - For reranking with conversational context
- **Features:**
  - Streaming support for real-time response
  - Temperature and top_p configuration
  - Context window management (different per model)
  - Performance metrics (tokens/sec, total time)

**Implementation Skeleton:**
```python
class OllamaProvider(AIProvider):
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.model = None  # Set by user in settings

    async def validate(self, api_key: str = None) -> bool:
        # Check if Ollama is running and model is available
        pass

    async def complete(self, prompt: str, temperature: float = 0.7) -> str:
        # Call Ollama generate API
        pass

    async def complete_streaming(self, prompt: str):
        # Stream response chunks
        pass
```

#### 3.4 Model Management UI
- **Feature:** Download/manage Ollama models from the UI
- **Functionality:**
  - Browse available models from Ollama registry
  - Show model size before downloading
  - Download progress bar
  - Delete pulled models to free space
  - Show disk space used by models

---

### 4. Additional Enhancements

#### 4.0 Markdown File Support (Quick Win)
- **Add .md file support**
  - Treat as structured text (similar to TXT)
  - Optionally chunk on headers (h1/h2 boundaries)
  - Strip markdown syntax for cleaner embeddings, or keep for context
  - Common use case: AI-generated docs, READMEs, notes

#### 4.1 Search Configuration
- **Feature:** Per-search advanced options
- **Options:**
  - Top K slider (1-100 results)
  - Similarity threshold (filter low-quality results)
  - Date range filter (if documents have timestamps)
  - File type filter (PDF, DOCX, TXT, CSV)
  - Document-specific search (search within one document)

#### 4.2 Performance Monitoring
- **Feature:** Display performance metrics in the UI
- **Metrics:**
  - Search latency (embedding time + FAISS time + AI time)
  - Index size and memory usage
  - Cache hit rate (for repeated queries)
  - AI token usage and cost estimate
  - Embedding model load time

#### 4.3 Batch Operations
- **Feature:** Bulk document management
- **Operations:**
  - Bulk upload from folder (drag & drop folder)
  - Bulk delete with multi-select
  - Export search results as JSON/CSV
  - Backup and restore index
  - Duplicate document detection

#### 4.4 Advanced Chunking Strategies
- **Feature:** Alternative chunking methods
- **Strategies:**
  - Semantic chunking (split on topic boundaries using AI)
  - Sentence-based chunking (keep sentences intact)
  - Paragraph-based chunking
  - Custom delimiter chunking (e.g., split on headers)
  - Recursive chunking for hierarchical documents

---

## Implemented in v3.0

### 0. Data Persistence Across Versions ✅
- **Persistent indexes across updates**
  - ✅ Schema version tracking in SQLite metadata store
  - ✅ Automatic migrations on startup (v1/v2 → v3)
  - ✅ Store version metadata (schema version, embedding model, chunk settings) per document
- **Backup and restore**
  - ✅ Export collections with indexes as portable ZIP archives
  - ✅ Import/restore collections from backup files
  - ✅ API endpoints for backup management (`/api/backups`)
  - ✅ Include or exclude source documents in backups

### 1. Format-Aware Indexing & Search ✅
- **Row-level indexing for CSV/tabular data**
  - ✅ Each CSV row indexed as its own chunk with column headers
  - ✅ Metadata stores column names, row number, and raw values
  - ✅ Search results render as tables for CSV results
  - ✅ Unified search across mixed content (PDFs + CSVs in same results)
- **Chunk metadata schema extension**
  - ✅ Added `source_format` field (pdf, txt, docx, csv, md, json)
  - ✅ Added `extraction_method` field (text, ocr, hybrid)
  - ✅ Added `csv_columns`, `csv_values`, `csv_row_number` for tabular data
  - ✅ Frontend format-aware icons and rendering

### 2. OCR for Image-Based PDFs ✅
- **Scanned document support**
  - ✅ Detect when PDF pages contain no extractable text
  - ✅ Fall back to OCR using pytesseract + pdf2image
  - ✅ Support for hybrid extraction (text + OCR for mixed documents)
  - ✅ Optional EasyOCR as alternative engine
  - ✅ Configurable via environment variables

---

## v4.0 - MCP Server, Real-Time Uploads & Conversational RAG

### 1. Real-Time Upload Progress (Priority: HIGH)

**Goal:** Provide granular, real-time feedback during large file uploads (especially JSONL).

#### 1.1 Server-Sent Events (SSE) for Progress Streaming
- **Feature:** Replace polling with SSE for instant progress updates
- **Implementation:**
  - New endpoint: `GET /documents/upload/{job_id}/stream` - SSE stream
  - Events: `extraction_start`, `extraction_progress`, `chunking_progress`, `embedding_progress`, `complete`, `error`
  - Sub-task granularity: Show which phase is active (extracting → chunking → embedding)
  - Per-chunk progress for large files (e.g., "Embedded 1,500/5,200 chunks")

**Event Schema:**
```json
{
  "event": "embedding_progress",
  "data": {
    "job_id": "abc123",
    "phase": "embedding",
    "current_file": "large_data.jsonl",
    "file_progress": { "current": 3, "total": 5 },
    "phase_progress": { "current": 1500, "total": 5200, "percent": 28.8 },
    "message": "Embedding chunks..."
  }
}
```

#### 1.2 JSONL Streaming Extraction
- **Feature:** Stream JSONL processing instead of loading entire file into memory
- **Implementation:**
  - Line-by-line streaming with configurable batch size
  - Progress callbacks during extraction (every N lines)
  - Chunking as we go (don't wait for full extraction)
  - Memory-efficient: constant RAM usage regardless of file size
  - Yield chunks to embedding service incrementally

**Chunking Strategy Options for JSONL:**
- **Per-line mode:** Each JSON line = one chunk (ideal for structured records)
- **Batch mode:** Group N lines per chunk (current behavior, configurable N)
- **Size-based mode:** Chunk by byte size, respecting JSON boundaries

#### 1.3 Frontend Progress UI Overhaul
- **Feature:** Rich progress display in UploadTab and notification bell
- **UI Elements:**
  - Phase indicator: `Extracting → Chunking → Embedding → Complete`
  - Dual progress bars: File progress + Phase progress
  - Estimated time remaining based on throughput
  - Cancel button to abort in-progress uploads
  - Detailed error display with retry option

**UI Mockup:**
```
┌─────────────────────────────────────────────────────────┐
│ Uploading: large_dataset.jsonl                          │
│                                                         │
│ Phase: Embedding (3/3)                                  │
│ ████████████░░░░░░░░░░░░░░░░░░░░░░  35%                │
│                                                         │
│ Progress: 1,847 / 5,234 chunks embedded                │
│ Speed: ~142 chunks/sec | ETA: ~24 seconds              │
│                                                         │
│ Files: 3 of 5 complete                                  │
│ ████████████████████░░░░░░░░░░░░░░  60%                │
│                                                         │
│ [Cancel Upload]                                         │
└─────────────────────────────────────────────────────────┘
```

#### 1.4 Upload Cancellation Support
- **Feature:** Allow users to cancel in-progress uploads
- **Implementation:**
  - New endpoint: `POST /documents/upload/{job_id}/cancel`
  - Graceful thread interruption with cleanup
  - Partial cleanup: Remove already-indexed chunks if cancelled mid-file
  - Status transitions: `running → cancelled`

---

### 2. Conversational RAG (Priority: HIGH)

**Goal:** Allow users to continue conversations based on search/ask responses, maintaining context.

#### 2.1 Conversation Sessions
- **Feature:** Create and maintain conversation threads tied to search context
- **Implementation:**
  - New model: `Conversation` with `id`, `collection_id`, `created_at`, `messages[]`
  - Messages include: `role` (user/assistant), `content`, `sources[]`, `timestamp`
  - Initial message = original question + synthesized answer + sources
  - Follow-up messages maintain full context window

**Data Model:**
```python
class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    sources: Optional[List[SourceReference]] = None
    timestamp: datetime

class Conversation(BaseModel):
    id: str
    collection_id: Optional[str]
    title: str  # Auto-generated from first question
    messages: List[ConversationMessage]
    context_chunks: List[str]  # Embedded chunk IDs for context
    created_at: datetime
    updated_at: datetime
```

#### 2.2 Conversation API Endpoints
- **New endpoints:**
  - `POST /api/conversations` - Create conversation from search result
  - `GET /api/conversations` - List user's conversations
  - `GET /api/conversations/{id}` - Get conversation with messages
  - `POST /api/conversations/{id}/messages` - Add follow-up message
  - `DELETE /api/conversations/{id}` - Delete conversation

**Follow-up Request:**
```json
{
  "message": "Can you explain the authentication flow in more detail?",
  "include_new_sources": true,  // Optionally search for more context
  "max_context_chunks": 10
}
```

**Follow-up Response:**
```json
{
  "message": {
    "role": "assistant",
    "content": "The authentication flow works as follows...",
    "sources": [...]
  },
  "conversation_id": "conv_abc123",
  "tokens_used": 1456
}
```

#### 2.3 Context Window Management
- **Feature:** Smart context management for long conversations
- **Implementation:**
  - Include original source chunks in every request (grounding)
  - Sliding window for conversation history (last N messages)
  - Option to "refresh context" by re-searching with refined query
  - Token counting to prevent context overflow
  - Summarize older messages when context limit approached

#### 2.4 Conversation UI
- **Feature:** Chat interface for follow-up questions
- **Implementation:**
  - "Continue conversation" button on search results
  - Opens chat panel/modal with conversation history
  - Source references linked in assistant messages
  - "New search" vs "Ask follow-up" distinction
  - Conversation history sidebar/list

**UI Mockup:**
```
┌─────────────────────────────────────────────────────────┐
│ Conversation: "How does authentication work?"           │
│ Collection: legacy-codebase | Started: 2 hours ago     │
├─────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────┐ │
│ │ 👤 How does authentication work in this codebase?   │ │
│ └─────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ 🤖 Authentication is handled by the AuthManager     │ │
│ │ class in auth/manager.pas [Source 1]. It uses...    │ │
│ │                                                     │ │
│ │ Sources: [1] auth/manager.pas:45-120                │ │
│ │          [2] models/user.pas:12-35                  │ │
│ └─────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ 👤 What about the JWT token validation?             │ │
│ └─────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ 🤖 JWT validation happens in ValidateToken()...     │ │
│ └─────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────┐ │
│ │ Ask a follow-up question...                      📤│ │
│ └─────────────────────────────────────────────────────┘ │
│ [🔍 New Search] [📚 Add More Sources] [🗑️ Clear]       │
└─────────────────────────────────────────────────────────┘
```

---

### 3. MCP Server Protocol (Priority: MEDIUM)

**Goal:** Expose Asymptote as an MCP server for coding agents (Claude Code, Cursor, etc.).

#### 3.1 MCP Transport Layer
- **Feature:** JSON-RPC 2.0 transport over stdio or HTTP
- **Implementation:**
  - New entry point: `asymptote_mcp.py` for MCP server mode
  - Support stdio transport (for local IDE integration)
  - Support HTTP+SSE transport (for remote agents)
  - MCP protocol version negotiation

#### 3.2 MCP Tool Definitions
- **Tools to expose:**
  ```
  search_documents:
    description: "Semantic search across indexed documents"
    parameters: query, collection_id, top_k, mode (semantic/keyword/hybrid)
    returns: Array of {filename, excerpt, relevance, line_numbers}

  ask_question:
    description: "Ask a question and get AI-synthesized answer with sources"
    parameters: question, collection_id, include_sources
    returns: {answer, sources[], tokens_used}

  continue_conversation:
    description: "Continue an existing conversation with follow-up"
    parameters: conversation_id, message
    returns: {response, sources[]}

  list_collections:
    description: "List available document collections"
    returns: Array of {id, name, document_count}

  list_documents:
    description: "List documents in a collection"
    parameters: collection_id
    returns: Array of {id, filename, page_count, chunk_count}
  ```

#### 3.3 MCP Resource Definitions
- **Resources to expose:**
  ```
  asymptote://collections - List of collections
  asymptote://collections/{id}/documents - Documents in collection
  asymptote://documents/{id}/content - Document content/chunks
  asymptote://conversations/{id} - Conversation history
  ```

#### 3.4 Modern Language Support for Code Indexing
- **Extend CodeExtractor for:**
  - Python (.py) - functions, classes, decorators
  - JavaScript/TypeScript (.js, .ts, .jsx, .tsx) - functions, classes, exports
  - Go (.go) - functions, types, interfaces
  - Rust (.rs) - functions, structs, traits, impls
  - Java (.java) - classes, methods, interfaces
  - C/C++ (.c, .cpp, .h, .hpp) - functions, classes, structs
- **Use tree-sitter for robust parsing**
- **Extract:** function signatures, docstrings, class hierarchies, imports

---

### 4. Additional v4 Enhancements

#### 4.1 Hybrid Search Tuning UI
- **Feature:** Let users adjust semantic/keyword balance from search UI
- **Implementation:**
  - Slider in advanced search options (0% keyword ↔ 100% semantic)
  - Presets: "Exact Match", "Balanced", "Semantic"
  - Per-collection default settings

#### 4.2 Search Result Explanation
- **Feature:** Explain why each result matched
- **Implementation:**
  - Show which terms matched (for keyword/hybrid)
  - Highlight semantic similarity regions
  - "Why this result?" expandable section

#### 4.3 Batch Upload Improvements
- **Feature:** Parallel file processing within a batch
- **Implementation:**
  - Process N files concurrently (configurable, default 3)
  - Per-file progress tracking
  - Continue on error (don't stop batch for one failure)
  - Summary report at end with success/failure breakdown

---

## Future Considerations (v5.0+)

### 5. Additional Multi-modal Search
- Support image search (extract text from images with OCR)
- Search within tables and charts
- Audio transcription and search (Whisper integration)

### 6. Advanced AI Features
- Query enhancement (rewrite user queries for better results)
- Multi-hop reasoning (combine info from multiple documents)
- Document summarization (generate executive summaries)

### 7. Collaboration Features
- Multi-user support with authentication
- Shared document collections
- User-specific search history
- Document annotations and highlights

### 8. Enterprise Features
- Role-based access control (RBAC)
- Audit logging
- SSO integration (SAML, OAuth)
- API rate limiting and quotas
- Document retention policies

### 9. Performance & Scaling
- Distributed FAISS index (multiple shards)
- GPU acceleration for embedding generation
- Redis caching layer
- PostgreSQL with pgvector as alternative to FAISS
- Horizontal scaling with load balancer

---

## Implementation Priority

**Phase 1-2 (v1.x-v2.x): ✅ COMPLETED**
- Frontend model configuration
- Ollama integration
- Chunking configuration UI
- Online document support

**Phase 3 (v3.0 - Format & OCR): ✅ COMPLETED**
1. ✅ Format-aware indexing for CSV/tabular data (row-level chunks, table rendering)
2. ✅ OCR for image-based/scanned PDFs (pytesseract + pdf2image)
3. ✅ Backup and restore functionality
4. ✅ Data versioning and migrations
5. ✅ Hybrid search (semantic + BM25 keyword search)
6. ✅ Code indexing for legacy languages (Pascal, Delphi, Modula-2, Assembly)

**Phase 4 (v4.0 - MCP & Real-Time): 🚧 IN PROGRESS**
1. 🔴 Real-time upload progress with SSE (HIGH - immediate pain point)
2. 🔴 JSONL streaming extraction (HIGH - large file support)
3. 🔴 Conversational RAG with follow-up questions (HIGH - agent usability)
4. 🟡 MCP server protocol (MEDIUM - coding agent integration)
5. 🟡 Modern language support in CodeExtractor (MEDIUM - broader adoption)
6. 🟢 Batch upload parallelization (LOW - nice to have)

**Phase 5 (v5.0):**
1. Multi-modal search (images, audio)
2. Advanced AI features (query enhancement, multi-hop)
3. Multi-user collaboration

---

## Technical Debt to Address

1. **Testing:** Expand test suite beyond placeholder tests
2. **Type Hints:** Complete type annotations across all modules
3. **Error Handling:** Improve error messages and user feedback
4. **Documentation:** API documentation for all endpoints (OpenAPI/Swagger)
5. **Logging:** Structured logging with levels and rotation
6. **Multi-user Mode:** Either implement or remove the config option
7. **Upload Service Refactor:** Replace polling with SSE, add cancellation support
8. **JSONL Memory:** Streaming extraction to prevent OOM on large files

---

## Community Feedback

If you have feature requests or suggestions, please open an issue on GitHub or contribute directly!

**Most Requested Features:**
- ✅ Ollama integration (completed)
- ✅ Frontend configuration (completed)
- ✅ Hybrid search (completed)
- 🔥 Real-time upload progress (v4.0 priority)
- 🔥 Conversational RAG / follow-up questions (v4.0 priority)
- 🚀 MCP server for coding agents (v4.0 planned)
- 📄 Modern language support (Python, JS, Go, Rust) (v4.0 planned)

---

**Last Updated:** 2025-03-02
