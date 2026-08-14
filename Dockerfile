# Finn API Dockerfile
# Standard deployment without custom SSL certificates.

FROM python:3.13-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install CPU-only torch FIRST, from PyTorch's CPU index.
#
# sentence-transformers depends on torch, and on Linux the default PyPI wheel
# is the CUDA build — it drags in ~2.7 GB of nvidia_* packages (cuBLAS, cuDNN,
# cuFFT…) that are dead weight on a server with no GPU. Measured: 11.9 GB image
# before this line. Satisfying torch from the CPU index up front means the
# requirements install below finds it already present and never reaches for the
# CUDA build.
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    torch

RUN pip install --no-cache-dir -r requirements.txt

# Presidio's default NLP engine loads en_core_web_lg at runtime, and pip does
# not bring it in — requirements.txt only mentions it in a comment. Without
# this line the image builds and starts cleanly, then raises on the first
# redaction, mid-conversation. Redaction is the product; bake the model in.
RUN python -m spacy download en_core_web_lg

# Pre-download the embedding model so first request isn't a cold 90s download
# and so a container with no writable HOME still works.
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy application code
COPY . .

RUN mkdir -p /app/data/documents /app/data/indexes

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data
ENV PORT=8000

EXPOSE 8000

# Bind the port the platform assigns. Railway, Render, Fly and Cloud Run all
# inject $PORT and route to that and nothing else; a hardcoded 8000 makes the
# service unreachable with no error in the logs — the container looks fine and
# every request 502s at the edge.
#
# HOST is overridable because Railway's private network needs `::` on
# environments created before 2025-10-16 (IPv6-only). Binding 0.0.0.0 there
# means another service can resolve finn.railway.internal and still not
# connect — which presents as a tunnel that will not come up.
CMD ["sh", "-c", "exec uvicorn main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000}"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1
