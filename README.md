# Asymptote API

**Privacy-focused, self-hosted advanced search using hybrid semantic + keyword retrieval**

Upload documents (PDF, TXT, DOCX, CSV), run advanced hybrid semantic + keyword retrieval, and get relevant passages with direct source links. Asymptote is privacy-focused by design: core search runs locally, and external AI is optional.

> **Why "Asymptote"?** In mathematics, an asymptote is a line that a curve approaches but never quite reaches. Like semantic search continuously approaching perfect understanding of your documents - getting closer with every query, but always refining, always learning. We're forever approaching the answer, never claiming to have reached it completely.

> 📚 **Developer docs** live in [docs/](docs/): [Architecture](docs/ARCHITECTURE.md) · [Roadmap](docs/ROADMAP.md). Agent guidance is in [CLAUDE.md](CLAUDE.md). Financial-analysis features live on the `fintech` branch.

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
open http://localhost:8473
```

Optional feature sets (each pulls in large ML runtimes — install only what you use):

```bash
pip install -r requirements-ocr.txt       # OCR for scanned PDFs
pip install -r requirements-audio.txt     # audio transcription (Whisper)
pip install -r requirements-postgres.txt  # optional PostgreSQL backend (multi-user mode)
```

### Docker (recommended for internal-network deployment)

```bash
docker compose up -d
open http://localhost:8473
```

That's the whole setup — the image builds the frontend, bundles OCR (Tesseract + Poppler), bakes the embedding model into the image, and persists documents/indexes in `./data`. No `.env` is required to start; add one to override defaults. For corporate CA certificates, drop `.crt` files into `certs/` before building (see [Corporate SSL Configuration](#corporate-ssl-configuration)).

Note that `docker compose up -d` publishes port 8473 to your network, and the app is unauthenticated by default. Before sharing the URL with anyone, read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — everyone who can reach Asymptote sees the whole corpus.

### Sharing it with a team

Asymptote is a shared appliance, not a multi-tenant service: there are no per-user permissions, so the only thing to configure is who can reach it. The recommended setup is an SSO proxy (Cloudflare Access, Tailscale, oauth2-proxy, Authelia) with the app bound so the proxy is the only route in — colleagues sign in with the identity they already have, and you write no code. Walkthrough and verification steps: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

### Air-gapped / offline deployment

Asymptote runs fully disconnected: build with `--build-arg OFFLINE_BUNDLE=1` to bake every runtime model (reranker, Whisper, Docling OCR) into the image, transfer it with `docker save`/`docker load`, and run with `OFFLINE_MODE=1` — which disables cloud AI providers and all HuggingFace downloads, guaranteeing zero egress beyond the local Ollama or self-hosted endpoints you configure. Full walkthrough: [docs/AIRGAP.md](docs/AIRGAP.md).

### Hosting on a PaaS (Railway, Render, Fly.io, Coolify, …)

The image is designed to deploy anywhere that builds from a Dockerfile. Three things to configure:

1. **Port** — the container listens on the platform's injected `PORT` (falls back to `8473`). Platforms that ask for an internal port instead (Fly, Coolify): use `8473`.
2. **Persistent volume** — mount one at `/app/data`. Everything stateful (documents, vector indexes, app database) lives under that single path. Without a volume the app still runs, but data is lost on redeploy.
3. **Auth** — the app has no login of its own, so before exposing a public URL set `AUTH_PASSWORD=<secret>` in the environment. Browsers prompt for it natively (HTTP Basic, any username); API and MCP clients send `Authorization: Bearer <secret>`. `/health` stays open for platform health checks. A shared password gives no per-person revocation or audit trail — for a standing team deployment prefer an SSO proxy ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

The embedding model is baked into the image at build time, so cold starts don't download anything and the container works on fully ephemeral filesystems (as long as `/app/data` is a volume). TLS is the platform's job — leave `SSL_CERTFILE`/`SSL_KEYFILE` unset and let the platform terminate HTTPS.

#### Railway

The repo ships a [railway.json](railway.json) (Dockerfile builder, `/health` healthcheck, restart-on-failure), so deploying is:

1. **New Project → Deploy from GitHub repo** — Railway detects the Dockerfile and builds it (the image is large: CPU torch + OCR + Whisper; expect a long first build).
2. **Attach a volume** to the service (right-click the service → *Attach Volume*) with mount path `/app/data`.
3. **Set the `AUTH_PASSWORD` variable** on the service before generating a public domain — the app is unauthenticated without it.
4. **Settings → Networking → Generate Domain.** Railway injects `PORT` and terminates HTTPS at the edge automatically; when prompted for a target port, any value works since the app listens on `$PORT`.

API and MCP clients then authenticate with `Authorization: Bearer <AUTH_PASSWORD>` against `https://<your-app>.up.railway.app`.

### What You Get

- **Web Interface**: http://localhost:8473 - Simple UI for searching, uploading, and managing documents
- **API Docs**: http://localhost:8473/docs - Interactive OpenAPI documentation
- **API Endpoint**: http://localhost:8473/api - REST API for programmatic access
- **MCP Endpoint**: http://localhost:8473/mcp - Streamable-HTTP MCP server for external agents

**First time setup**: The embedding model (~90MB) will download automatically on first run.

**SSL/TLS Support**: For corporate environments with custom CA certificates, see the [Corporate SSL Configuration](#corporate-ssl-configuration) section below.

---

## What It Does

Asymptote lets you:

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

**What happens:** Downloads and installs the core packages. Takes 2-5 minutes.

Scanned-PDF OCR, audio transcription, and the PostgreSQL backend are optional
extras — add `-r requirements-ocr.txt`, `-r requirements-audio.txt`, or
`-r requirements-postgres.txt` if you need them. The app runs fine without
them and tells you which extra to install if you enable a feature that needs it.

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
INFO - Asymptote API ready
INFO - Uvicorn running on http://127.0.0.1:8473
```

**Access the API:**
- Interactive docs: http://localhost:8473/docs
- Health check: http://localhost:8473/health

To reach it from another machine, set `HOST=0.0.0.0` — and read
[Security Considerations](#security-considerations) first, since the app is
unauthenticated until you set `AUTH_PASSWORD`.

### MCP Integration

Asymptote now exposes an embedded HTTP MCP endpoint at `http://localhost:8473/mcp` when `ENABLE_MCP=true` (default).

Use the **Settings** tab in the UI to:
- Enable or disable the MCP server without restarting
- Choose the default collection and retrieval settings used by MCP
- Keep MCP in search-only mode (recommended) to avoid duplicate model usage
- Optionally enable Ollama-backed synthesis for MCP when you explicitly want server-side answering
- Copy or download ready-to-paste Claude Code `.mcp.json` and Codex `config.toml` snippets

### Step 4: Verify Setup

```bash
# Check health
curl http://localhost:8473/health

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

This starts the dev server at http://localhost:5173 with hot-reload. The backend must be running separately at http://localhost:8473.

### Building for Production

```bash
# From the frontend directory
cd frontend
npm run build
```

This compiles the Vue app into `frontend/dist/` (gitignored), which the FastAPI backend serves automatically at http://localhost:8473 — hashed assets get long-lived immutable cache headers, so repeat loads are fast and deploys are picked up immediately. Docker images build the frontend in a dedicated stage; `run.sh`/`run.bat` build it on first run.

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
HOST=127.0.0.1                          # This machine only. See Security below
                                        # before changing to 0.0.0.0.
PORT=8473
```

### AI Features (Optional)

Asymptote supports optional AI integration for enhanced search results:

**Features:**
- **Result Reranking**: AI re-orders search results by semantic relevance
- **Answer Synthesis**: AI generates natural language answers from retrieved documents
- **Multi-Provider**: Use Anthropic Claude, OpenAI GPT, INL HPC AI, and Ollama

**Setup:**
1. Open the web interface at http://localhost:8473
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

**Security Note:** API keys are stored only in your browser's localStorage and are sent directly to the AI providers. The Asymptote server never sees or stores your API keys.

**Get API Keys:**
- **Anthropic**: https://console.anthropic.com/
- **OpenAI**: https://platform.openai.com/api-keys
- **INL HPC**: Use your INL HPC API key for `https://api.hpc.inl.gov/llm/v1`

---

### Corporate SSL Configuration

For organizations using custom SSL certificates or corporate proxies:

**Option 1: Docker (built in)**

Place your corporate CA certificate(s) — `.crt` files — into the `certs/` directory and rebuild; the standard Dockerfile installs anything it finds there automatically (a no-op when the directory is empty):

```bash
cp /path/to/your/cert.crt certs/
docker compose up -d --build
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
curl -X POST "http://localhost:8473/documents/upload" \
  -F "files=@document1.pdf" \
  -F "files=@document2.pdf"

# Upload other file types (TXT, DOCX, CSV)
curl -X POST "http://localhost:8473/documents/upload" \
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
curl -X POST "http://localhost:8473/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "machine learning algorithms",
    "top_k": 5
  }'
```

**Search with AI features:**
```bash
curl -X POST "http://localhost:8473/search" \
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
      "pdf_url": "http://localhost:8473/documents/abc123/pdf",
      "page_url": "http://localhost:8473/documents/abc123/pdf#page=42"
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
open "http://localhost:8473/documents/abc123/pdf#page=42"
```

### List Documents

```bash
curl "http://localhost:8473/documents"
```

### Download Document

```bash
# Download any document type
curl "http://localhost:8473/documents/abc123/pdf" -o output.pdf
```

### Inspect Indexed Chunks (Including OCR Fields)

```bash
# View stored chunks for a document
curl "http://localhost:8473/documents/abc123/chunks?include_fields=true"
```

Use this to inspect exactly what was indexed for OCR PDFs.  
For form-like documents, the response includes heuristic `extracted_fields` key/value pairs per chunk.

### Delete Document

```bash
curl -X DELETE "http://localhost:8473/documents/abc123"
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

Visit http://localhost:8473/docs for a full interactive API playground.

---

## Troubleshooting

### Common Issues

#### 1. "Port 8473 already in use"

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

#### 4. "SSL certificate error" downloading the model

**Why:** Corporate TLS interception can break the HuggingFace download of the embedding model.

**Error looks like:**
```
SSL: CERTIFICATE_VERIFY_FAILED
```

**Solutions:**

1. **Install your corporate CA** — see [Corporate SSL Configuration](#corporate-ssl-configuration) below.

2. **Use the model cache from another machine:**
   Copy the folder `~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2/` from a working machine to the same location on the problem machine.

3. **Run fully offline** — pre-fetch the models with `scripts/prefetch_offline_models.py` and set `OFFLINE_MODE=true`. See [docs/AIRGAP.md](docs/AIRGAP.md).

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

1. Enable OCR in **Settings → OCR** and pick an engine:
   - **Vision AI** (recommended): uses a vision-capable model from an AI provider you've already configured — most accurate for complex layouts and degraded scans.
   - **Local**: free/offline via Docling or Tesseract. Requires `pip install -r requirements-ocr.txt`; on bare-metal Windows also install **Tesseract OCR** and **Poppler** and add them to `PATH`. The Docker image ships with both preinstalled.
2. Re-index the PDF after changing OCR settings.

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
1. Are documents uploaded? Check: `curl http://localhost:8473/documents`
2. Check health: `curl http://localhost:8473/health`
3. Try broader query: "database" instead of "postgresql query optimization"
4. Check logs for errors: Look at terminal output

#### 8. "Docker container crashes"

**Solution:**
```bash
# Check logs
docker-compose logs asymptote

# Increase memory limit in docker-compose.yml
services:
  asymptote:
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
docker-compose logs -f asymptote

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

**Asymptote is a shared team appliance: everyone who can reach it sees the whole
corpus.** There are no per-user permissions inside the app — every route
(search, upload, delete, chat on your stored provider keys, and the whole `/mcp`
tool surface) is available to anyone who can open the port. So the only question
to answer is *who can reach it*, and two settings decide:

| Setting | Default | Meaning |
|---|---|---|
| `HOST` | `127.0.0.1` | This machine only. Docker sets `0.0.0.0` itself, where the published port controls exposure. |
| `AUTH_PASSWORD` | empty | No auth. Required for anything reachable beyond loopback. |

The defaults are safe together: a loopback-only bind needs no password. Change
one and you must change the other — the app warns loudly at startup if it is
bound to the network with no password set.

**For a team deployment, put an SSO proxy in front** (Cloudflare Access,
Tailscale, oauth2-proxy, Authelia) and bind the app so the proxy is the only
route in. Colleagues get in with the SSO they already have — no password to
type, share, or rotate — and you get per-person revocation and an access log
without writing any code. Full walkthrough, including how to verify the bypass
is closed: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

There is no multi-user mode. `ENABLE_MULTI_USER` refuses to start: it filtered
the collection list by owner but never checked ownership on search, document
retrieval, chat, or MCP, so it looked like an isolation boundary without being
one. Scope access to a group cleared for the whole corpus, and run a separate
instance for material with a different audience.

**Also worth setting:**

1. **HTTPS** (`SSL_CERTFILE`/`SSL_KEYFILE`, or terminate at the proxy)
2. **Leave `CORS_ALLOW_ORIGINS` empty** unless a separate web app needs it. Empty
   sends no CORS headers, so other sites cannot read responses; `*` lets any page
   your browser visits read this API. Non-browser clients (MCP, curl, SDKs) are
   unaffected either way. Credentialed cross-origin requests are never allowed
   for the wildcard.
3. **Rate limiting** — not implemented; front with a proxy that provides it if
   abuse is a concern
4. **Air-gapped environments** — set `OFFLINE_MODE=1` to disable cloud AI
   providers and model downloads entirely (see [docs/AIRGAP.md](docs/AIRGAP.md))

Stored credentials (the OCR vision key) are masked in `GET /api/config` and
never returned to a client.

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
tar -czf asymptote-backup-$(date +%Y%m%d).tar.gz data/
```

**Restore:**
```bash
tar -xzf asymptote-backup-20240203.tar.gz
python main.py
```

---

## Project Structure

```
asymptote/
├── main.py                    # App assembly: middleware, lifespan, routers, frontend serving
├── config.py                  # Settings from .env
├── requirements.txt           # Python dependencies
│
├── api/                       # HTTP endpoints — one router module per domain
│   ├── deps.py               # Shared helpers/state (get_indexer, expertise store)
│   ├── documents.py          # Upload, indexing, document management
│   ├── search.py             # Semantic search, facets, embeddings
│   ├── chat.py               # Chat, streaming chat, ask, provider config
│   ├── artifacts.py          # Source-grounded document generation
│   ├── collections.py        # Collection CRUD + re-indexing
│   ├── mcp.py                # MCP server configuration
│   ├── sharing.py            # Users + collection sharing
│   ├── expertise.py          # Expertise packs
│   └── system.py             # Health, capabilities, config, local file pickers
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
│       ├── stores/           # Pinia stores (collections, chat, search, jobs, user)
│       ├── components/
│       │   ├── ChatTab.vue        # Grounded chat (primary surface)
│       │   ├── SourcesSidebar.vue # Source upload & management
│       │   ├── StudioSidebar.vue  # Studio panel (generate, tables, notes)
│       │   ├── SearchTab.vue      # Search interface with history
│       │   ├── MCPTab.vue         # MCP server status & setup
│       │   └── SettingsTab.vue    # AI API keys & settings
│       └── dist/             # Built frontend (gitignored; generated by npm run build)
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
├── certs/                     # Corporate CA certs (.crt, gitignored) — auto-installed by Docker build
│
├── .env.example              # Configuration template
├── Dockerfile                # Multi-stage image: frontend build + Python runtime (OCR included)
├── docker-compose.yml        # One-command deployment (docker compose up -d)
├── example_usage.py          # Python client example
└── verify_setup.py           # Installation checker
```

---

## Example: Python Client

```python
import requests

BASE_URL = "http://localhost:8473"

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

Licensed under the [Apache License 2.0](LICENSE). You're free to use, modify,
and deploy this on internal systems — including commercial use — provided the
license and notices are retained. The license includes an express patent grant
from contributors, which is why it's the usual choice for software adopted
inside enterprises and laboratories.

---

## Contributing

This is a reference implementation. Feel free to fork and adapt for your needs.

---

**Asymptote** - Always approaching understanding, never quite reaching it.

