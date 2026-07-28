"""
Asymptote — privacy-focused document indexing, grounded chat, and MCP access.

This module only assembles the app: middleware, lifespan, routers, and
frontend serving. Endpoints live in `api/` (one router module per domain);
business logic lives in `services/`.
"""

import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from services.indexer_manager import indexer_manager
from services.reindex_service import reindex_service
from services.mcp_server import embedded_mcp_app, mcp_server_lifespan
from api import deps
from api import (
    artifacts,
    chat,
    collections,
    documents,
    expertise,
    mcp,
    search,
    sharing,
    system,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - initialize and cleanup services."""
    async with mcp_server_lifespan():
        logger.info("Initializing Asymptote API...")

        # Initialize default collection's indexer to pre-load embedding model
        logger.info("Loading default collection indexer...")
        try:
            default_indexer = indexer_manager.get_indexer("default")
            total_chunks = default_indexer.vector_store.get_total_chunks()
            logger.info(f"Default collection indexed chunks: {total_chunks}")
        except Exception as e:
            logger.warning(f"Could not load default indexer: {e}")

        # Set up reload callback for re-indexing service (collection-aware)
        def reload_indexer(collection_id: str = "default"):
            """Reload an indexer's vector store from disk after re-indexing."""
            try:
                logger.info("=" * 60)
                logger.info(f"RELOAD CALLBACK TRIGGERED for collection: {collection_id}")
                logger.info("=" * 60)

                indexer_manager.reload_indexer(collection_id)

                stats = indexer_manager.get_collection_stats(collection_id)
                logger.info(f"Reload complete. Collection {collection_id} indexed chunks: {stats['total_chunks']}")
                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"RELOAD FAILED for collection {collection_id}: {e}", exc_info=True)

        reindex_service.reload_callback = reload_indexer

        deps.mark_initialized()

        logger.info("Asymptote API ready")
        logger.info(f"Data directory: {settings.data_dir}")
        logger.info(f"Embedded MCP server: {'enabled' if settings.enable_mcp else 'disabled'}")

        yield

        # Cleanup on shutdown
        logger.info("Shutting down Asymptote API...")
        indexer_manager.save_all()
        logger.info("Shutdown complete")


app = FastAPI(
    title="Asymptote API",
    description="Privacy-focused document indexing, grounded chat, and MCP access",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Optional shared-secret auth (AUTH_PASSWORD) ─────────────────────────────
# Aimed at public deployments (PaaS, exposed ports). HTTP Basic keeps the
# browser flow zero-UI; Bearer covers API and MCP clients. /health stays open
# so platform probes work unauthenticated.

def _password_from_auth_header(header: str) -> str:
    """Extract the presented password from a Basic or Bearer Authorization header."""
    scheme, _, value = header.partition(" ")
    scheme = scheme.lower()
    value = value.strip()
    if scheme == "bearer":
        return value
    if scheme == "basic":
        try:
            decoded = base64.b64decode(value).decode("utf-8")
        except Exception:
            return ""
        return decoded.partition(":")[2]
    return ""


if settings.auth_password:
    @app.middleware("http")
    async def require_auth(request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        presented = _password_from_auth_header(request.headers.get("authorization", ""))
        if presented and secrets.compare_digest(presented, settings.auth_password):
            return await call_next(request)
        return JSONResponse(
            {"detail": "Not authenticated"},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Asymptote"'},
        )

for module in (system, documents, search, chat, artifacts, collections, mcp, sharing, expertise):
    app.include_router(module.router)


# ── Frontend serving ────────────────────────────────────────────────────────
# The Vue app is built by Vite into frontend/dist (not committed to git).
# Hashed assets under /assets are immutable and cached for a year; index.html
# always revalidates so a new deploy is picked up immediately.

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    """StaticFiles with cache headers tuned for a Vite SPA build."""

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        if "/assets/" in scope.get("path", ""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


@app.get("/", response_class=HTMLResponse, tags=["ui"], include_in_schema=False)
async def web_interface():
    """Serve the web interface."""
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return HTMLResponse(
            content=index_path.read_text(encoding="utf-8"),
            status_code=200,
            headers={"Cache-Control": "no-cache"},
        )
    return HTMLResponse(
        content=(
            "<h1>Asymptote API</h1>"
            "<p>Frontend build not found — run <code>cd frontend && npm run build</code>, "
            "or visit <a href='/docs'>/docs</a> for API documentation.</p>"
        ),
        status_code=200,
    )


# Mounts must come after all routes so they don't override API routes.
app.mount("/mcp", embedded_mcp_app, name="mcp")

# SERVE_STATIC=false disables frontend serving — used in Electron mode where
# the renderer is bundled with Electron and loaded via file://, not from this server.
_serve_static = os.environ.get("SERVE_STATIC", "true").lower() not in ("false", "0", "no")
if _serve_static and (FRONTEND_DIST / "index.html").exists():
    app.mount("/", SPAStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn_kwargs: dict = {
        "host": settings.host,
        "port": settings.port,
        "reload": True,
    }

    cert_path = Path(settings.ssl_certfile).expanduser() if settings.ssl_certfile else None
    key_path = Path(settings.ssl_keyfile).expanduser() if settings.ssl_keyfile else None
    if cert_path and key_path and cert_path.is_file() and key_path.is_file():
        uvicorn_kwargs["ssl_certfile"] = str(cert_path)
        uvicorn_kwargs["ssl_keyfile"] = str(key_path)
        logger.info(f"HTTPS enabled — serving on https://{settings.host}:{settings.port}")
    elif settings.ssl_certfile or settings.ssl_keyfile:
        logger.warning(
            "ssl_certfile / ssl_keyfile configured but one or both files are missing — "
            "falling back to plain HTTP. Run certs/generate-cert.sh to create a dev pair."
        )

    uvicorn.run("main:app", **uvicorn_kwargs)
