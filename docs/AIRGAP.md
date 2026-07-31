# Air-Gapped Deployment Guide

Asymptote is designed to run entirely inside a disconnected network: documents
are indexed, searched, and chatted with locally, and no data ever leaves the
environment. This guide covers preparing a deployment on a connected machine,
transferring it across the air gap, and verifying that nothing phones home.

## What OFFLINE_MODE guarantees

Setting `OFFLINE_MODE=1` enforces, at runtime:

- **Cloud AI providers are disabled.** Anthropic, OpenAI, Grok, Google, GitHub
  Models, OpenRouter, Ollama Cloud, and AWS Bedrock cannot be instantiated —
  every AI surface (chat, artifacts, vision OCR, schema inference, connection
  tests) goes through one factory, and it rejects them with a clear error.
  Only **local Ollama** and **self-hosted OpenAI-compatible endpoints** (vLLM,
  LM Studio, llama.cpp server, TGI) remain available.
- **No HuggingFace downloads.** `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`
  are set before any ML library loads. Embedding, reranker, Whisper, and
  Docling models load from the local cache only; a missing model fails fast
  with instructions instead of hanging on network retries.

Everything else in the stack is local by construction: FAISS + BM25 search,
SQLite storage, the embedded MCP server, and a frontend with zero CDN
resources, zero telemetry, and zero analytics.

## Option A — Docker (recommended)

### 1. Build the offline bundle on a connected machine

```bash
docker compose build --build-arg OFFLINE_BUNDLE=1
```

`OFFLINE_BUNDLE=1` bakes every runtime model into the image on top of the
default embedding model (`all-MiniLM-L6-v2`):

| Model | Purpose | Approx. size |
|---|---|---|
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | Search reranker | ~90 MB |
| `faster-whisper base` | Audio transcription | ~150 MB |
| Docling layout + TableFormer | Local OCR of scanned PDFs | ~500 MB |

To bake a different Whisper size: `--build-arg WHISPER_MODEL=small`.

### 2. Export, transfer, load

```bash
docker save asymptote-asymptote | gzip > asymptote-image.tar.gz
# transfer via approved media, then inside the air gap:
docker load < asymptote-image.tar.gz
```

(Check the image name with `docker images` — compose names it
`<project-dir>-asymptote`.)

### 3. Run inside the air gap

Copy `docker-compose.yml` alongside the loaded image, point compose at the
image instead of building (replace the `build:` block with
`image: asymptote-asymptote`), and set in the `environment:` section or a
`.env` file:

```bash
OFFLINE_MODE=1
AUTH_PASSWORD=<a-strong-shared-secret>
```

Then `docker compose up -d` and open `http://<host>:8473`.

## Option B — Bare metal

1. On a connected machine with the same OS/Python, create the venv and build
   the frontend once (`./run.sh` or `run.bat` does both), then pre-fetch all
   models:

   ```bash
   python scripts/prefetch_offline_models.py
   ```

2. Transfer the whole project directory **including** `venv/`,
   `frontend/dist/`, and the model caches:
   - `~/.cache/huggingface/` (or wherever `HF_HOME` points)
   - `~/.cache/docling/` (if using local OCR)

3. Inside the air gap, set in `.env`:

   ```bash
   OFFLINE_MODE=true
   AUTH_PASSWORD=<a-strong-shared-secret>
   ```

   and start with `python main.py` (do not re-run the bootstrap scripts — they
   try to reinstall dependencies).

## Local LLM for chat (Ollama)

Grounded chat needs an LLM. Inside an air gap, run Ollama on a host the
Asymptote machine can reach:

1. On a connected machine: `ollama pull llama3.1:8b` (or your chosen model,
   plus `nomic-embed-text` if you want Ollama-served embeddings).
2. Copy the Ollama model store across the gap (`~/.ollama/models` on
   Linux/macOS, `%USERPROFILE%\.ollama\models` on Windows).
3. Point Asymptote at it via `OLLAMA_BASE_URL` (the compose file defaults to
   the Docker host's Ollama at `host.docker.internal:11434`).

Search, indexing, and the MCP tools work with **no LLM at all** — chat is the
only feature that requires one.

## Hardening checklist

- [ ] `OFFLINE_MODE=1` set (verify: `curl http://<host>:8473/health` reports
      `"offline_mode": true`)
- [ ] `AUTH_PASSWORD` set — without it every endpoint, including MCP, is open
      to anyone who can reach the port
- [ ] `CORS_ALLOW_ORIGINS` left empty unless another web origin needs the API;
      list explicit origins rather than using `*`
- [ ] TLS enabled if traffic crosses a shared network segment
      (`SSL_CERTFILE`/`SSL_KEYFILE`, or terminate at a reverse proxy)
- [ ] Everyone who can reach the port can read the whole corpus — there is no
      per-user isolation (see [DEPLOYMENT.md](DEPLOYMENT.md)), so confirm the
      reachable set matches who is cleared for the material
- [ ] Verify zero egress: run a packet capture (`tcpdump`/firewall logs) while
      indexing a document, running a search, and chatting — the only traffic
      should be client↔Asymptote and Asymptote↔Ollama

## What works offline

| Feature | Offline? | Notes |
|---|---|---|
| Document indexing (PDF, DOCX, TXT, MD, CSV/XLSX, code) | ✅ | Fully local |
| Semantic + keyword + hybrid search | ✅ | FAISS/BM25, local embeddings |
| Reranking | ✅ | With baked/pre-seeded model |
| OCR of scanned PDFs | ✅ | Docling/Tesseract locally; vision-LLM OCR only via local Ollama |
| Audio transcription | ✅ | Local Whisper, with baked/pre-seeded model |
| Grounded chat | ✅ | Via local Ollama or self-hosted OpenAI-compatible server |
| MCP endpoint for internal agents | ✅ | Embedded, in-process |
| Cloud AI providers | 🚫 | Deliberately disabled in offline mode |
