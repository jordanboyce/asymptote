# Finn API

**Privacy-focused, self-hosted advanced search using hybrid semantic + keyword retrieval**

Upload documents (PDF, TXT, DOCX, CSV), run advanced hybrid semantic + keyword retrieval, and get relevant passages with direct source links. Finn is privacy-focused by design: core search runs locally, and external AI is optional.

> **Why "Finn"?** Short, friendly, and easy to say across a desk — Finn is the assistant that sits next to you while you work through your own documents.

---

## Table of Contents

- [Quick Start](#quick-start)
- [What It Does](#what-it-does)
- [Technology Stack](#technology-stack)
- [Installation](#installation)
- [Configuration](#configuration)
- [API Usage](#api-usage)
- [Troubleshooting](#troubleshooting)
- [Advanced Topics](#advanced-topics)

---

## Quick Start

### Python (Recommended)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the server
python main.py

# 3. Open your browser
open http://localhost:8000
```

### Docker

```bash
# Standard deployment
docker-compose up -d
open http://localhost:8000

# Corporate environment (with custom SSL certificates)
docker-compose -f docker-compose.yml up -d --build
# Note: Edit docker-compose.yml to use Dockerfile.corporate if needed
```

### Desktop Application (Windows)

For a native Windows experience with system tray integration:

```bash
# Build Windows executable
cd desktop
build_windows.bat

# Run the desktop app
dist\FinnDesktop.exe

# Create installer (requires Inno Setup)
build_installer.bat
```

**Desktop Features:**
- System tray icon with quick access
- Automatic port management (finds free port if 8000 is taken)
- Native Windows application (no terminal needed)
- Same functionality as web version
- Opens browser automatically on startup

**First time setup**: The embedding model (~90MB) will download automatically on first run.

**SSL/TLS Support**: For corporate environments with custom CA certificates, see the [Corporate SSL Configuration](#corporate-ssl-configuration) section below.

### What You Get

- **Web Interface**: http://localhost:8000 - Simple UI for searching, uploading, and managing documents
- **API Docs**: http://localhost:8000/docs - Interactive OpenAPI documentation
- **API Endpoint**: http://localhost:8000/api - REST API for programmatic access

---

## What It Does

Finn lets you:

1. **Upload Documents** - Drop in PDF, TXT, DOCX, or CSV files (books, papers, manuals, data)
2. **Hybrid Search** - Combine semantic understanding with keyword precision in one retrieval workflow
3. **Privacy-Aware AI Options** (Optional) - Use private/local or external providers for reranking and answer synthesis based on data sensitivity
4. **Get Results** - Find relevant passages with page numbers and direct document links
5. **Scale Up** - Handle hundreds to thousands of documents locally

**Supported file types:** PDF, TXT, DOCX, CSV

**Example:**
- Query: *"How do I optimize database queries?"*
- Result: Points you to page 47 of your database textbook with a direct link

---

## Technology Stack

### Core Dependencies

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **fastapi** | 0.115.5 | Web framework that powers the REST API | `main.py` |
| **uvicorn** | 0.32.1 | ASGI server that runs FastAPI | `main.py` |
| **python-multipart** | 0.0.18 | Handles file uploads (PDFs) | `main.py` |

### Document Processing

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **pypdf** | 5.1.0 | Extracts text from standard PDFs | `services/document_extractor.py` |
| **pdfplumber** | 0.11.4 | Extracts text from complex PDF layouts (columns, tables) | `services/document_extractor.py` |
| **python-docx** | 1.1.2 | Extracts text from DOCX files | `services/document_extractor.py` |
| **pandas** | 2.2.3 | Extracts data from CSV files | `services/document_extractor.py` |

**Why two PDF libraries?** We try pdfplumber first (handles complex layouts better), then fall back to pypdf if needed.

**Supported formats:** PDF, TXT, DOCX, CSV

### Search & Embeddings

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **sentence-transformers** | 3.3.1 | Converts text to semantic embeddings (vectors) | `services/embedder.py` |
| **faiss-cpu** | 1.9.0 | Fast similarity search over millions of vectors | `services/vector_store.py` |

**What are embeddings?** They convert text to numbers that capture meaning. Similar meanings = similar numbers, enabling semantic search.

### AI Integration (Optional)

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **anthropic** | 0.42.0 | Anthropic Claude API client for AI reranking and synthesis | `services/ai_service.py` |
| **openai** | 1.59.5 | OpenAI GPT API client for AI reranking and synthesis | `services/ai_service.py` |

**AI Features** (optional, requires API keys stored in browser localStorage):
- **Result Reranking**: Uses AI to re-order search results by relevance
- **Answer Synthesis**: Generates natural language answers from retrieved documents
- **Multi-Provider**: Run both Claude and OpenAI simultaneously to compare responses

### Configuration & Data

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **pydantic** | 2.10.3 | Data validation for API requests/responses | `models/schemas.py` |
| **pydantic-settings** | 2.6.1 | Loads configuration from `.env` files | `config.py` |
| **python-dotenv** | 1.0.1 | Reads `.env` files | `config.py` |

### Frontend (Web Interface)

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **vue** | 3.5.27 | Progressive JavaScript framework for building UIs | `frontend/src/*.vue` |
| **vite** | 7.3.1 | Fast build tool and dev server | `vite.config.js` |
| **tailwindcss** | 4.0.0 | Utility-first CSS framework | `frontend/src/style.css` |
| **daisyui** | 5.5.17 | Tailwind CSS component library | UI components |
| **axios** | 1.13.4 | HTTP client for API requests | `frontend/src/components/*.vue` |

**Modern UI Stack:** Built with Vue 3 Composition API, styled with Tailwind CSS 4 and DaisyUI 5 components, bundled with Vite for lightning-fast development and production builds.

**Frontend Features:**
- Dark/light theme with persistent preference
- Sticky navigation tabs
- Search history with caching (localStorage-based)
- Real-time stats display (documents, pages, chunks)
- Multi-provider AI selection (Anthropic, OpenAI, INL HPC, and Ollama)
- Responsive design for mobile and desktop

### Development & Testing

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **pytest** | 8.3.4 | Testing framework | `tests/` |
| **httpx** | 0.28.1 | HTTP client for testing APIs | `tests/` |

---

## Installation

### Prerequisites

- **Python 3.13** (highly recommended) or **Python 3.8+** (check: `python --version`)
- **4GB RAM minimum** (8GB recommended for large collections)
- **500MB disk space** (more for storing PDFs and indexes)

### Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

**What happens:** Downloads and installs all packages listed above. Takes 2-5 minutes.

### Step 2: Configure (Optional)

```bash
cp .env.example .env
```

**Default settings work fine for most users.** Only edit `.env` if you need to:
- Change the port
- Use a different embedding model
- Adjust chunk sizes
- Choose metadata storage (JSON vs SQLite)

### Step 3: Run the Server

```bash
python main.py
```

**First run:** The embedding model downloads automatically (~90MB, 1-2 minutes).

**You'll see:**
```
INFO - Loading embedding model: all-MiniLM-L6-v2
INFO - Initializing vector store (metadata: json)
INFO - Finn API ready
INFO - Uvicorn running on http://0.0.0.0:8000
```

**Access the API:**
- Interactive docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

### MCP Integration

Finn now exposes an embedded HTTP MCP endpoint at `http://localhost:8000/mcp` when `ENABLE_MCP=true` (default).

Use the **Settings** tab in the UI to:
- Enable or disable the MCP server without restarting
- Choose the default collection and retrieval settings used by MCP
- Keep MCP in search-only mode (recommended) to avoid duplicate model usage
- Optionally enable Ollama-backed synthesis for MCP when you explicitly want server-side answering
- Copy or download ready-to-paste Claude Code `.mcp.json` and Codex `config.toml` snippets

### Step 4: Verify Setup

```bash
# Check health
curl http://localhost:8000/health

# Expected response:
{"status":"healthy","indexed_chunks":0}
```

---

## Frontend Development

The web interface is built with Vue 3 + Vite and located in the `frontend/` directory. The production build is automatically served by the FastAPI backend.

### Development Mode

To work on the frontend with hot-reload:

```bash
# Navigate to frontend directory
cd frontend

# Install Node.js dependencies (one-time setup)
npm install

# Start the Vite dev server
npm run dev
```

This starts the dev server at http://localhost:5173 with hot-reload. The backend must be running separately at http://localhost:8000.

### Building for Production

```bash
# From the frontend directory
cd frontend
npm run build
```

This compiles the Vue app and outputs static files to the `static/` directory (one level up), which the FastAPI backend serves automatically at http://localhost:8000.

### Frontend Stack

- **Framework**: Vue 3 with Composition API + Pinia for state management
- **Build Tool**: Vite 7.3.1
- **Styling**: Tailwind CSS 4.0 + DaisyUI 5.5
- **HTTP Client**: Axios
- **Features**:
  - Hybrid semantic + keyword search with configurable weighting and keyword highlighting
  - AI-powered answer synthesis (optional, with Claude/GPT)
  - Search history with caching (localStorage-based, case-insensitive)
  - Multi-file document upload (PDF, TXT, DOCX, CSV) with progress tracking
  - Document management with bulk delete functionality
  - Dark/light theme toggle with persistent preference
  - Sticky navigation tabs
  - Real-time stats in header (documents, pages, chunks)
  - Responsive design for mobile and desktop

---

## Configuration

Create a `.env` file to customize settings:

```bash
# Data storage
DATA_DIR=./data                          # Where documents and indexes are stored

# Embedding model
EMBEDDING_MODEL=all-MiniLM-L6-v2        # Default: fast, 384 dimensions

# Text chunking
CHUNK_SIZE=600                          # Characters per chunk
CHUNK_OVERLAP=100                       # Overlap between chunks

# Search
DEFAULT_TOP_K=10                        # Default number of results
MAX_TOP_K=50                            # Maximum results allowed

# Server
HOST=0.0.0.0
PORT=8000
```

### AI Features (Optional)

Finn supports optional AI integration for enhanced search results:

**Features:**
- **Result Reranking**: AI re-orders search results by semantic relevance
- **Answer Synthesis**: AI generates natural language answers from retrieved documents
- **Multi-Provider**: Use Anthropic Claude, OpenAI GPT, INL HPC AI, and Ollama

**Setup:**
1. Open the web interface at http://localhost:8000
2. Navigate to the **Settings** tab
3. Select your AI provider (Anthropic, OpenAI, INL HPC, or Ollama)
4. Enter your API key (stored securely in browser localStorage, never sent to server)
5. Enable desired features:
   - **Reranking**: Improves result ordering (~$0.0005/search)
   - **Synthesis**: Generates AI answers (~$0.015/search)

**Multi-Provider Mode:**
- If both Anthropic and OpenAI keys are configured, you can select which provider(s) to use per search
- Compare responses side-by-side from both models
- Selection preference persists between searches

**Security Note:** API keys are stored only in your browser's localStorage and are sent directly to the AI providers. The Finn server never sees or stores your API keys.

**Get API Keys:**
- **Anthropic**: https://console.anthropic.com/
- **OpenAI**: https://platform.openai.com/api-keys
- **INL HPC**: Use your INL HPC API key for `https://api.hpc.inl.gov/llm/v1`

---

### Corporate SSL Configuration

For organizations using custom SSL certificates or corporate proxies:

**Option 1: Use Dockerfile.corporate**

Create a `certs/` directory and place your certificate file(s) inside:

```bash
mkdir certs
# Copy your corporate CA certificate(s) - must be .crt files
cp /path/to/your/cert.crt certs/
```

Build and run with the corporate Dockerfile:

```bash
# Option A: Direct docker build
docker build -f Dockerfile.corporate -t finn-corporate .
docker run -d -p 8000:8000 -v $(pwd)/data:/app/data finn-corporate

# Option B: Edit docker-compose.yml to use Dockerfile.corporate
# Change the dockerfile line under build:
#   build:
#     context: .
#     dockerfile: Dockerfile.corporate
# Then run:
docker-compose up -d
```

**Important Notes:**
- Certificate files must have `.crt` extension
- Multiple certificates can be placed in `certs/` directory
- The `certs/` directory is in `.gitignore` but NOT in `.dockerignore` (needed for build)

**Option 2: Python Direct Configuration**

For Python deployments, set the SSL certificate path:

```bash
# Add to .env
SSL_CERT_FILE=/path/to/your/cert.crt
# Or set environment variable
export SSL_CERT_FILE=/path/to/your/cert.crt
export REQUESTS_CA_BUNDLE=/path/to/your/cert.crt
```

**What this solves:**
- Corporate proxy SSL interception
- Custom CA certificates
- Internal certificate authorities
- SSL verification errors when downloading models or making AI API calls

**Security Note:** The `certs/` directory is in `.gitignore` and `.dockerignore` to prevent accidental commit of certificates.

---

## API Usage

### Upload Documents

```bash
# Upload PDFs
curl -X POST "http://localhost:8000/documents/upload" \
  -F "files=@document1.pdf" \
  -F "files=@document2.pdf"

# Upload other file types (TXT, DOCX, CSV)
curl -X POST "http://localhost:8000/documents/upload" \
  -F "files=@notes.txt" \
  -F "files=@report.docx" \
  -F "files=@data.csv"
```

**Response:**
```json
{
  "message": "Successfully indexed 2 document(s)",
  "documents_processed": 2,
  "total_pages": 45,
  "total_chunks": 123,
  "document_ids": ["abc123", "def456"]
}
```

**What happens:**
1. Documents are saved to `data/documents/`
2. Text is extracted from each file (page-by-page for PDFs, section-by-section for others)
3. Text is split into overlapping chunks
4. Each chunk is converted to an embedding
5. Embeddings are indexed with FAISS
6. Metadata is saved (JSON or SQLite)

### Search Documents

**Basic search:**
```bash
curl -X POST "http://localhost:8000/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "machine learning algorithms",
    "top_k": 5
  }'
```

**Search with AI features:**
```bash
curl -X POST "http://localhost:8000/search" \
  -H "Content-Type: application/json" \
  -H "X-AI-Key: your-api-key-here" \
  -d '{
    "query": "machine learning algorithms",
    "top_k": 5,
    "ai": {
      "provider": "anthropic",
      "rerank": true,
      "synthesize": true
    }
  }'
```

**Response (basic search):**
```json
{
  "query": "machine learning algorithms",
  "results": [
    {
      "filename": "ml-textbook.pdf",
      "page_number": 42,
      "text_snippet": "Supervised learning algorithms...",
      "similarity_score": 0.87,
      "document_id": "abc123",
      "chunk_id": "abc123_p42_c0",
      "pdf_url": "http://localhost:8000/documents/abc123/pdf",
      "page_url": "http://localhost:8000/documents/abc123/pdf#page=42"
    }
  ],
  "total_results": 5
}
```

**Response (with AI synthesis):**
```json
{
  "query": "machine learning algorithms",
  "results": [...],
  "total_results": 5,
  "synthesis": "Machine learning algorithms can be categorized into supervised, unsupervised, and reinforcement learning...",
  "ai_usage": {
    "provider": "anthropic",
    "features_used": ["rerank", "synthesis"],
    "total_input_tokens": 1523,
    "total_output_tokens": 287
  }
}
```

**Open PDF at specific page:**
```bash
# In browser (opens at page 42)
open "http://localhost:8000/documents/abc123/pdf#page=42"
```

### List Documents

```bash
curl "http://localhost:8000/documents"
```

### OCR Playground Preview

```bash
# Preview OCR extraction without indexing
curl -X POST "http://localhost:8000/api/ocr/playground" \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "C:/path/to/scanned-form.pdf",
    "ocr_engine": "docling",
    "ocr_fallback_only": false,
    "ocr_char_threshold": 20,
    "ocr_render_dpi": 300,
    "ocr_auto_rotate": true,
    "tesseract_psm": 3
  }'
```

This returns page-level OCR text previews and extracted form-like field/value pairs.

### Download Document

```bash
# Download any document type
curl "http://localhost:8000/documents/abc123/pdf" -o output.pdf
```

### Inspect Indexed Chunks (Including OCR Fields)

```bash
# View stored chunks for a document
curl "http://localhost:8000/documents/abc123/chunks?include_fields=true"
```

Use this to inspect exactly what was indexed for OCR PDFs.  
For form-like documents, the response includes heuristic `extracted_fields` key/value pairs per chunk.

### Delete Document

```bash
curl -X DELETE "http://localhost:8000/documents/abc123"
```

**Response:**
```json
{
  "message": "Deleted document abc123",
  "filename": "ml-textbook.pdf",
  "chunks_deleted": 45,
  "pdf_deleted": true
}
```

**What happens:**
1. Document metadata is removed from the index
2. All associated chunks are deleted from the vector store
3. The document file is removed from `data/documents/`
4. Changes are persisted to disk

**Note:** Deletion is permanent and cannot be undone. The document file will be completely removed from the filesystem.

### Interactive Documentation

Visit http://localhost:8000/docs for a full interactive API playground.

---

## Troubleshooting

### Common Issues

#### 1. "Port 8000 already in use"

**Solution:**
```bash
# Change port in .env
echo "PORT=8001" >> .env
python main.py
```

#### 2. "No module named 'faiss'"

**Solution:**
```bash
pip install -r requirements.txt --force-reinstall
```

**Still failing?**
```bash
# Try installing faiss separately
pip install faiss-cpu
```

#### 3. "Model download is slow"

**Why:** First run downloads ~90MB model from HuggingFace.

**Solutions:**
- Wait 1-2 minutes (one-time download)
- Check your internet connection
- If behind corporate firewall, download manually:
  1. Get model from https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
  2. Place in `~/.cache/torch/sentence_transformers/`

#### 4. "SSL certificate error" (Windows Executable)

**Why:** The packaged Windows executable may have trouble with SSL certificates when downloading the embedding model from HuggingFace.

**Error looks like:**
```
SSL: CERTIFICATE_VERIFY_FAILED
```

**Solutions:**

1. **Pre-download the model** (Recommended):
   Run this command once with regular Python (not the exe) before using the desktop app:
   ```bash
   python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
   ```
   This downloads the model to `~/.cache/huggingface/` which the exe will then find automatically.

2. **Run from source instead of exe:**
   ```bash
   pip install -r requirements.txt
   python main.py
   ```

3. **Use the model cache from another machine:**
   Copy the folder `~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2/` from a working machine to the same location on the problem machine.

#### 5. "Document extraction failed"

**Possible causes:**
- **PDFs:** Corrupted file, scanned images (no text layer), or protected/encrypted
- **DOCX:** Corrupted file or unsupported Word version
- **CSV:** Encoding issues or malformed CSV
- **TXT:** Encoding issues

**Solutions:**
```bash
# For PDFs - check with another tool
pdfinfo yourfile.pdf

# For PDFs - try converting first
pdftk input.pdf output output.pdf

# For text files - check encoding
file -i yourfile.txt
```

#### 5a. "Scanned/image PDF indexes with 0 chunks"

This means text extraction returned empty content and OCR could not recover text.

For Windows desktop/exe builds:

1. Enable OCR in **Settings -> PDF Processing**
2. If using `pytesseract`, install both:
   - **Tesseract OCR** (`tesseract.exe`)
   - **Poppler** (`pdfinfo.exe`, `pdftoppm.exe`)
3. Either add both to `PATH`, or place portable folders next to `Finn.exe`:
   - `poppler/bin/...`
   - `tesseract/tesseract.exe`
4. Re-index the PDF after changing OCR settings/dependencies

If OCR still fails, switch OCR engine to `docling` in settings and try again.

#### 6. "Out of memory"

**For large PDFs or many documents:**

```bash
# Reduce chunk size (creates more, smaller chunks)
echo "CHUNK_SIZE=400" >> .env

# Use smaller embedding model
echo "EMBEDDING_MODEL=paraphrase-MiniLM-L3-v2" >> .env
```

#### 7. "Search returns no results"

**Checklist:**
1. Are documents uploaded? Check: `curl http://localhost:8000/documents`
2. Check health: `curl http://localhost:8000/health`
3. Try broader query: "database" instead of "postgresql query optimization"
4. Check logs for errors: Look at terminal output

#### 8. "Docker container crashes"

**Solution:**
```bash
# Check logs
docker-compose logs finn

# Increase memory limit in docker-compose.yml
services:
  finn:
    mem_limit: 4g
```

### Docker-Specific Issues

#### "Cannot connect to Docker daemon"

```bash
# Start Docker Desktop (Windows/Mac)
# Or start Docker service (Linux)
sudo systemctl start docker
```

#### "Permission denied accessing data folder"

```bash
# Fix permissions
chmod -R 777 ./data
```

#### "Container keeps restarting"

```bash
# Check what's wrong
docker-compose logs -f finn

# Common fix: Remove old containers
docker-compose down
docker-compose up -d
```

### Python-Specific Issues

#### "Python version too old"

```bash
# Check version
python --version  # Need 3.8+, Python 3.13 highly recommended

# Upgrade Python or use pyenv
pyenv install 3.13
pyenv local 3.13
```

#### "ModuleNotFoundError"

```bash
# Make sure virtual environment is activated
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Reinstall dependencies
pip install -r requirements.txt
```

#### "Permission denied on Windows"

**Run as administrator or:**
```bash
pip install --user -r requirements.txt
```

---

## Advanced Topics

### Performance & Scalability

**Current capacity:**
- 100-1,000 documents: Works great
- 1,000-10,000 documents: Recommend SQLite metadata storage
- 10,000+ documents: Consider FAISS IVF index or pgvector

**Memory usage estimates:**

| Documents | Chunks | RAM Required |
|-----------|--------|--------------|
| 100       | ~10K   | ~200 MB      |
| 1,000     | ~100K  | ~500 MB      |
| 10,000    | ~1M    | ~3 GB        |

**Speed up indexing:**
```bash
# Larger chunks = fewer chunks = faster
echo "CHUNK_SIZE=1000" >> .env
echo "CHUNK_OVERLAP=150" >> .env
```

### Custom Embedding Models

**Better quality (slower, more memory):**
```bash
echo "EMBEDDING_MODEL=all-mpnet-base-v2" >> .env
```

**Faster (less accurate):**
```bash
echo "EMBEDDING_MODEL=paraphrase-MiniLM-L3-v2" >> .env
```

**See all models:** https://www.sbert.net/docs/pretrained_models.html

### Security Considerations

**This is a local-only service by default. For production:**

1. **Add authentication** (FastAPI supports OAuth2, JWT, API keys)
2. **Restrict CORS** (currently allows all origins)
3. **Use HTTPS** (run behind reverse proxy like nginx)
4. **Rate limiting** (prevent abuse)
5. **Input validation** (check file sizes, types)

**Not recommended for public internet exposure without these protections.**

### Backup & Recovery

**What to backup:**
```bash
# Documents (all file types)
data/documents/

# JSON metadata storage
data/indexes/json/faiss.index
data/indexes/json/metadata.json
data/indexes/json/embeddings.npy

# SQLite metadata storage
data/indexes/sqlite/faiss.index
data/indexes/sqlite/metadata.db
```

**Backup script:**
```bash
#!/bin/bash
tar -czf finn-backup-$(date +%Y%m%d).tar.gz data/
```

**Restore:**
```bash
tar -xzf finn-backup-20240203.tar.gz
python main.py
```

---

## Project Structure

```
finn/
├── main.py                    # FastAPI app entry point
├── config.py                  # Settings from .env
├── requirements.txt           # Python dependencies
│
├── models/                    # Data models
│   └── schemas.py            # Pydantic request/response models
│
├── services/                  # Business logic
│   ├── document_extractor.py # Extracts text from documents (PDF, TXT, DOCX, CSV)
│   ├── chunker.py            # Splits text into chunks
│   ├── embedder.py           # Generates embeddings
│   ├── vector_store.py       # FAISS index with SQLite metadata
│   ├── metadata_store.py     # SQLite metadata storage
│   ├── ai_service.py         # AI provider abstraction (Anthropic, OpenAI, INL HPC, Ollama)
│   └── indexing/
│       └── indexer.py        # Orchestrates indexing pipeline
│
├── frontend/                  # Vue 3 web interface
│   ├── index.html            # HTML entry point
│   ├── package.json          # Node.js dependencies
│   ├── vite.config.js        # Vite build configuration
│   ├── tailwind.config.js    # Tailwind CSS configuration
│   └── src/
│       ├── main.js           # Vue app entry point
│       ├── App.vue           # Root Vue component with sticky tabs & theme toggle
│       ├── style.css         # Global styles (Tailwind imports)
│       ├── stores/
│       │   └── searchStore.js    # Pinia store for search state & caching
│       └── components/
│           ├── SearchTab.vue      # Search interface with AI & history
│           ├── DocumentsTab.vue   # Document upload & management
│           └── SettingsTab.vue    # AI API keys & settings
│
├── static/                    # Built frontend (generated by npm run build)
│   ├── index.html            # Production HTML
│   └── assets/               # JS/CSS bundles
│
├── desktop/                   # Windows desktop application
│   ├── finn_desktop.py  # Desktop wrapper with system tray
│   ├── icon.ico              # Application icon
│   ├── installer.iss         # Inno Setup installer script
│   ├── build_windows.bat     # Build executable script
│   ├── build_installer.bat   # Build installer script
│   ├── requirements_desktop.txt  # Desktop-specific dependencies
│   └── utils/                # Icon generation utilities
│
├── tests/                     # Test suite
│   └── test_api.py           # API tests (placeholder)
│
├── data/                      # Runtime data (gitignored)
│   ├── documents/            # Uploaded documents (PDF, TXT, DOCX, CSV)
│   └── indexes/              # FAISS + metadata
│       ├── json/             # JSON metadata storage
│       └── sqlite/           # SQLite metadata storage
│
├── certs/                     # SSL certificates (gitignored)
│   └── *.crt                 # Corporate CA certificates
│
├── .env.example              # Configuration template
├── Dockerfile                # Docker image (standard)
├── Dockerfile.corporate      # Docker image (with SSL certificates)
├── docker-compose.yml        # Docker setup
├── .dockerignore             # Docker build exclusions
├── .gitignore                # Git exclusions (includes certs/)
├── example_usage.py          # Python client example
└── verify_setup.py           # Installation checker
```

---

## Example: Python Client

```python
import requests

BASE_URL = "http://localhost:8000"

# Upload documents (PDF, TXT, DOCX, CSV)
with open("document.pdf", "rb") as f:
    response = requests.post(
        f"{BASE_URL}/documents/upload",
        files={"files": f}
    )
    print(response.json())

# Upload multiple file types at once
files = [
    ("files", open("notes.txt", "rb")),
    ("files", open("report.docx", "rb")),
    ("files", open("data.csv", "rb"))
]
response = requests.post(f"{BASE_URL}/documents/upload", files=files)
print(response.json())

# Search
response = requests.post(
    f"{BASE_URL}/search",
    json={"query": "machine learning", "top_k": 5}
)
results = response.json()["results"]

# Open first result in browser
if results:
    import webbrowser
    webbrowser.open(results[0]["page_url"])
```

See [example_usage.py](example_usage.py) for more examples.

---

## FAQ

**Q: Can I use this on Windows?**
A: Yes! Python and Docker both work on Windows.

**Q: Does it support other file formats?**
A: Yes! Currently supports PDF, TXT, DOCX, and CSV files.

**Q: Can I use it offline?**
A: Yes, after the first run (model downloads once).

**Q: How accurate is semantic search?**
A: Very good for finding concepts, not exact strings. ~80-90% accuracy for most queries.

**Q: Can I delete the embedding model cache?**
A: It's in `~/.cache/torch/sentence_transformers/`. You can delete it but it'll re-download.

**Q: What's the largest PDF it can handle?**
A: Tested up to 1000+ pages. Limited by RAM.

**Q: Can multiple users access it?**
A: Yes, but no built-in auth. Add authentication for multi-user.

---

## License

This project is provided as-is for educational and internal use.

---

## Contributing

This is a reference implementation. Feel free to fork and adapt for your needs.

---

**Finn** - Always approaching understanding, never quite reaching it.

