"""
Asymptote — privacy-focused document indexing, grounded chat, and MCP access.

This module only assembles the app: middleware, lifespan, routers, and
frontend serving. Endpoints live in `api/` (one router module per domain);
business logic lives in `services/`.
"""

import asyncio
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
    admin,
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


# ── Security posture ────────────────────────────────────────────────────────
# The app has no authentication unless AUTH_PASSWORD is set, and every route —
# search, upload, delete, chat on your provider keys, the whole /mcp tool
# surface — is reachable by anyone who can open a socket to it. So the two
# settings that decide who can open that socket are checked before we serve.

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _check_security_posture() -> None:
    """Refuse or warn on configurations that promise more safety than we deliver.

    Raises:
        RuntimeError: multi-user mode is requested. Ownership is enforced on
            collection metadata only — search, documents, chat and MCP all take
            a collection_id and never check it — so the flag would advertise an
            isolation boundary that does not exist. See docs/DEPLOYMENT.md.
    """
    # These strings stay ASCII-only: they surface on consoles (Windows cp1252,
    # Docker logs) where an em-dash renders as mojibake.
    if settings.enable_multi_user:
        raise RuntimeError(
            "ENABLE_MULTI_USER is not supported. The flag only filtered the "
            "collection LIST - search, document retrieval, chat and the /mcp "
            "tools accept any collection_id without an ownership check, so it "
            "never isolated anything. Remove ENABLE_MULTI_USER. For per-person "
            "collections with enforced ownership, set PRIVATE_COLLECTIONS=true "
            "behind Cloudflare Access - see docs/DEPLOYMENT.md."
        )

    if settings.private_collections and not (
        settings.cf_access_team_domain and settings.cf_access_aud
    ):
        raise RuntimeError(
            "PRIVATE_COLLECTIONS requires a verified identity source: set "
            "CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD (Cloudflare Access) so "
            "every request carries a signed identity. Without one, ownership "
            "would be enforced against an identity anyone can forge, which is "
            "the half-boundary this app refuses to ship. See "
            "docs/DEPLOYMENT.md."
        )

    if settings.host not in _LOOPBACK_HOSTS and not settings.auth_password:
        logger.warning(
            "SECURITY: bound to %s (reachable from the network) with no "
            "AUTH_PASSWORD set. Anyone who can reach this port can read, "
            "modify and delete every document, and spend your AI provider "
            "credits. Set AUTH_PASSWORD, or set HOST=127.0.0.1 to bind "
            "loopback only. See docs/DEPLOYMENT.md.",
            settings.host,
        )

    if settings.cors_allow_origins.strip() == "*" and not settings.auth_password:
        logger.warning(
            "SECURITY: CORS_ALLOW_ORIGINS=* with no AUTH_PASSWORD set. Any web "
            "page the user visits can read this API from their browser and "
            "exfiltrate indexed documents. List the origins that need access "
            "instead."
        )


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
            """Rebuild an indexer after re-indexing.

            A rebuild (not a reload) so that a re-index run under a newly
            chosen embedding provider — different vector dimension — swaps
            in cleanly instead of failing the old store's dimension check.
            """
            try:
                logger.info("=" * 60)
                logger.info(f"RELOAD CALLBACK TRIGGERED for collection: {collection_id}")
                logger.info("=" * 60)

                indexer_manager.rebuild_indexer(collection_id)

                stats = indexer_manager.get_collection_stats(collection_id)
                logger.info(f"Reload complete. Collection {collection_id} indexed chunks: {stats['total_chunks']}")
                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"RELOAD FAILED for collection {collection_id}: {e}", exc_info=True)

        reindex_service.reload_callback = reload_indexer

        deps.mark_initialized()

        # Retention sweeps for the unbounded log tables (search_history
        # stores result snippets; chat_usage grows per turn). First run is
        # delayed past startup, then daily.
        from services.retention import retention_loop
        retention_task = asyncio.create_task(retention_loop())

        logger.info("Asymptote API ready")
        logger.info(f"Data directory: {settings.data_dir}")
        logger.info(f"Embedded MCP server: {'enabled' if settings.enable_mcp else 'disabled'}")

        yield

        # Cleanup on shutdown
        logger.info("Shutting down Asymptote API...")
        retention_task.cancel()
        indexer_manager.save_all()
        logger.info("Shutdown complete")


_check_security_posture()

app = FastAPI(
    title="Asymptote API",
    description="Privacy-focused document indexing, grounded chat, and MCP access",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS: origins come from CORS_ALLOW_ORIGINS (comma-separated). Empty — the
# default — installs no CORS middleware at all, so the same-origin policy keeps
# other sites from reading responses. The bundled frontend is same-origin and
# the Vite dev server proxies to the backend, so neither needs an exception;
# non-browser clients (MCP, curl, SDKs) are unaffected by CORS entirely.
# Credentialed cross-origin requests are allowed only for explicitly listed
# origins — wildcard + credentials would make Starlette echo any Origin back,
# letting arbitrary web pages drive the API with the browser's stored
# credentials.
_cors_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=_cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ── Rate limiting ───────────────────────────────────────────────────────────
# Registered BEFORE the auth block on purpose: Starlette runs the last-added
# middleware first, so auth ends up outermost and resolves
# request.state.auth_identity before the limiter reads it. Deliberately
# always-on (unlike auth, which only exists when a password or private
# collections is configured) — an open deployment still deserves limits.
from middleware.rate_limit import enforce_rate_limit as _enforce_rate_limit

app.middleware("http")(_enforce_rate_limit)


# ── Shared-secret auth (AUTH_PASSWORD) ──────────────────────────────────────
# Required for any deployment reachable beyond loopback. HTTP Basic keeps the
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


if settings.auth_password or settings.private_collections:
    @app.middleware("http")
    async def require_auth(request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        # CORS preflights are sent without credentials by spec, and this
        # middleware runs outside CORSMiddleware — pass them through so the
        # preflight can be answered; the actual request still authenticates.
        if request.method == "OPTIONS":
            return await call_next(request)

        # Cloudflare Access trust: a valid signed assertion from the edge is
        # stronger auth than the shared password (per-identity, revocable),
        # and accepting it removes the browser's second login prompt after
        # SSO. Signature/issuer/audience/expiry are all verified — a spoofed
        # header without the team's private key gets nowhere.
        from services.access_jwt import get_verifier
        verifier = get_verifier()
        if verifier is not None:
            assertion = request.headers.get("cf-access-jwt-assertion", "")
            if assertion:
                claims = await asyncio.to_thread(verifier.verify, assertion)
                if claims is not None:
                    request.state.auth_identity = verifier.identity_from_claims(claims)
                    request.state.auth_via = "cloudflare-access"
                    return await _call_with_user_context(request, call_next)

        presented = _password_from_auth_header(request.headers.get("authorization", ""))

        if settings.auth_password:
            if presented and secrets.compare_digest(presented, settings.auth_password):
                request.state.auth_identity = None
                request.state.auth_via = "password"
                return await _call_with_user_context(request, call_next)

        # Personal MCP access tokens: self-serve alternative to a Cloudflare
        # Access service token, minted from the app itself (Settings → MCP)
        # by anyone who can already reach it. Deliberately scoped to /mcp —
        # a leaked token cannot touch the rest of the API or the UI. Under
        # private collections it resolves to the identity that created it,
        # so an MCP client sees exactly that person's collections.
        if request.url.path.startswith("/mcp") and presented and presented.startswith("asy_mcp_"):
            from services.mcp_tokens import verify_token
            token_record = await asyncio.to_thread(verify_token, presented)
            if token_record is not None:
                request.state.auth_identity = token_record.get("user_id")
                request.state.auth_via = "mcp_token"
                return await _call_with_user_context(request, call_next)

        return JSONResponse(
            {"detail": "Not authenticated"},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Asymptote"'},
        )

    async def _call_with_user_context(request, call_next):
        """Bind the verified identity to a contextvar for the request's scope.

        Routers read identity from request.state; service-layer code that has
        no Request in reach (deps.get_indexer, the chat tool loop) reads the
        contextvar. Set before call_next so the downstream task inherits it.
        """
        if not settings.private_collections:
            return await call_next(request)
        from middleware.user_context import set_request_user, reset_request_user
        token = set_request_user(getattr(request.state, "auth_identity", None))
        try:
            return await call_next(request)
        finally:
            reset_request_user(token)

for module in (system, documents, search, chat, artifacts, collections, mcp, sharing, expertise, admin):
    app.include_router(module.router)


# ── Request metadata (ids, access log, in-flight counters) ─────────────────
# Added last, which makes it the OUTERMOST middleware: it times auth and rate
# limiting too, and reads the identity auth left on request.state for its
# access line. /api/admin/stats reads its counters.
from middleware.request_meta import track_request as _track_request

app.middleware("http")(_track_request)


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

if (FRONTEND_DIST / "index.html").exists():
    app.mount("/", SPAStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    # Auto-reload is opt-in (RELOAD=1) because uvicorn's reloader runs the
    # actual server in a multiprocessing child. Anything that stops the parent
    # without a clean Ctrl+C — a task kill, a crashed terminal — orphans that
    # child, which keeps running and holds the port.
    # With reload off, this process IS the server: kill it and the port frees.
    dev_reload = os.environ.get("RELOAD", "").lower() in ("1", "true", "yes")

    uvicorn_kwargs: dict = {
        "host": settings.host,
        "port": settings.port,
        "reload": dev_reload,
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
