# Asymptote — single production Dockerfile.
# Start with: docker compose up -d

# ── Stage 1: build the Vue frontend ──
# (debian-based node image: the pinned rollup/esbuild natives are glibc builds)
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python runtime ──
FROM python:3.13-slim AS runtime

WORKDIR /app

# System dependencies: build tools for pip, curl for the healthcheck,
# Tesseract + Poppler so OCR of scanned PDFs works out of the box.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Optional corporate CA certificates (internal networks / TLS-intercepting
# proxies): drop .crt files into certs/ before building; no-op when empty.
COPY certs/ /tmp/certs/
RUN if ls /tmp/certs/*.crt 1>/dev/null 2>&1; then \
      cp /tmp/certs/*.crt /usr/local/share/ca-certificates/ && \
      update-ca-certificates; \
    fi && rm -rf /tmp/certs
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Python dependencies first for better layer caching
# (server image ships full-featured: OCR + audio transcription)
# torch is installed first from the CPU-only wheel index: sentence-transformers
# pulls torch, and without this pin pip downloads the CUDA build (~2GB of GPU
# libraries that do nothing in this container).
COPY requirements.txt requirements-ocr.txt requirements-audio.txt ./
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt -r requirements-ocr.txt -r requirements-audio.txt

# Bake the embedding model into the image. Cold starts stay fast on platforms
# with ephemeral filesystems (Railway, Render, Fly), and the container never
# needs HuggingFace reachable at runtime.
ENV HF_HOME=/opt/hf-cache
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Application code and the built frontend
COPY . .
COPY --from=frontend /build/dist ./frontend/dist

RUN mkdir -p /app/data/documents /app/data/indexes

EXPOSE 8473

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

# PaaS platforms (Railway, Render, Heroku-style) inject PORT and expect the
# app to listen on it; fall back to the documented default otherwise.
# Shell form is deliberate — exec form would not expand ${PORT}.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8473}

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8473}/health || exit 1
