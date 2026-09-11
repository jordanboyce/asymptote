"""Embedded MCP server for Asymptote."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from config import settings
from models.schemas import SearchMode
from services.app_database import app_db
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from services.structured_chat import (
    build_structured_context,
    collect_structured_tables,
    render_table_as_jsonl,
    render_table_as_rows,
)
from services.structured_store import SQLValidationError, StructuredStore

logger = logging.getLogger(__name__)

_DEFAULT_INLINE_ROW_THRESHOLD = 150

MCP_PROFILE_FIELDS = (
    "collection_id",
    "top_k",
    "mode",
    "semantic_weight",
    "include_sources",
    "max_source_length",
    "inline_row_threshold",
)

_request_mcp_profile: ContextVar[dict[str, Any]] = ContextVar("asymptote_request_mcp_profile", default={})

# Collection ids a personal MCP token was explicitly scoped to. Only a
# request authenticated with such a token has one; it is what unlocks a
# *restricted* collection over MCP (services/governance.py).
_request_mcp_token_scope: ContextVar[list[str] | None] = ContextVar(
    "asymptote_request_mcp_token_scope", default=None
)


def get_request_mcp_token_scope() -> list[str] | None:
    return _request_mcp_token_scope.get(None)


# Whether the current MCP request may add or update sources. A personal
# token carries its own flag (read-only unless minted with writes enabled);
# every other caller — an SSO session, the shared password, an Access
# service token, the in-process chat loop — is a full user and may write,
# subject to the collection's own write permission and the AUP gate.
_request_mcp_can_write: ContextVar[bool] = ContextVar("asymptote_request_mcp_can_write", default=True)


def get_request_mcp_can_write() -> bool:
    return _request_mcp_can_write.get(True)

MCP_CONFIG_FIELDS = (
    "enable_mcp",
    "mcp_server_id",
    "mcp_default_collection",
    "mcp_top_k",
    "mcp_mode",
    "mcp_semantic_weight",
    "mcp_include_sources",
    "mcp_max_source_length",
)

def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection with localhost plus any configured public hosts.

    Without an explicit setting the MCP SDK trusts only localhost Host headers,
    which breaks the endpoint behind a tunnel/reverse proxy (the original
    public Host is forwarded). MCP_ALLOWED_HOSTS extends the allowlist while
    keeping the protection itself enabled.
    """
    hosts = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*"]
    origins = ["http://127.0.0.1", "http://127.0.0.1:*", "http://localhost", "http://localhost:*"]
    for host in settings.mcp_allowed_hosts.split(","):
        host = host.strip()
        if host:
            hosts += [host, f"{host}:*"]
            origins += [f"https://{host}", f"http://{host}"]
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)


_asymptote_mcp = FastMCP(
    "Asymptote",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=_transport_security(),
)


def _tool_response(response: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Return an MCP tool response unchanged.

    Retained as the single seam every tool result passes through, in case
    post-processing is needed later. `tool_name` identifies the producing
    tool for callers and diagnostics.
    """
    return response


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def get_mcp_settings_payload() -> dict[str, Any]:
    return {field: getattr(settings, field) for field in MCP_CONFIG_FIELDS}


def _mcp_profile_preference_key(collection_id: str) -> str:
    return f"mcp_collection_profile:{collection_id}"


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "collection_id": profile.get("collection_id") or settings.mcp_default_collection,
        "top_k": max(1, min(int(profile.get("top_k", settings.mcp_top_k)), 20)),
        "mode": str(profile.get("mode", settings.mcp_mode) or settings.mcp_mode),
        "semantic_weight": max(0.0, min(float(profile.get("semantic_weight", settings.mcp_semantic_weight)), 1.0)),
        "include_sources": _coerce_bool(profile.get("include_sources"), settings.mcp_include_sources),
        "max_source_length": max(100, min(int(profile.get("max_source_length", settings.mcp_max_source_length)), 2000)),
        "inline_row_threshold": max(0, min(int(profile.get("inline_row_threshold", _DEFAULT_INLINE_ROW_THRESHOLD)), 2000)),
    }

    if normalized["mode"] not in {"semantic", "keyword", "hybrid"}:
        normalized["mode"] = settings.mcp_mode

    return normalized


def get_request_mcp_profile() -> dict[str, Any]:
    return _request_mcp_profile.get({})


def _mcp_safe_name(collection: dict[str, Any] | None, fallback: str = "") -> str:
    """Return the display name for MCP output."""
    if not collection:
        return fallback
    return collection.get("mcp_display_name") or collection.get("name", fallback)


def _mcp_safe_description(collection: dict[str, Any] | None, fallback: str = "") -> str:
    """Return the description for MCP output."""
    if not collection:
        return fallback
    return collection.get("mcp_display_description") or collection.get("description", fallback)


_GUIDE_SUMMARY_CHAR_LIMIT = 500

import re as _re

# Single-word triggers matched on word boundaries so "sum" doesn't fire on
# "summarize", "max" doesn't fire on "maximum_value", etc.
_NUMERIC_INTENT_WORDS = (
    "total", "totals", "sum", "sums", "average", "averages", "avg", "mean",
    "median", "count", "largest", "smallest", "highest", "lowest", "min",
    "max", "ratio", "percent", "percentage", "weight", "concentration",
    "breakdown", "aggregate",
)
_NUMERIC_INTENT_WORD_RE = _re.compile(
    r"\b(" + "|".join(_re.escape(w) for w in _NUMERIC_INTENT_WORDS) + r")\b"
)

# Multi-word / phrase triggers (matched as literal substrings)
_NUMERIC_INTENT_PHRASES = (
    "how many", "how much", "top ", "bottom ", "group by",
)

_EXACT_MATCH_INTENT_WORDS = (
    "verbatim", "exact", "exactly", "literal", "literally", "quoted",
)
_EXACT_MATCH_INTENT_WORD_RE = _re.compile(
    r"\b(" + "|".join(_re.escape(w) for w in _EXACT_MATCH_INTENT_WORDS) + r")\b"
)


def _detect_numeric_intent(query: str) -> bool:
    q = query.lower()
    if _NUMERIC_INTENT_WORD_RE.search(q):
        return True
    return any(phrase in q for phrase in _NUMERIC_INTENT_PHRASES)


def _detect_exact_match_intent(query: str) -> bool:
    if '"' in query:
        return True
    return bool(_EXACT_MATCH_INTENT_WORD_RE.search(query.lower()))


def _guide_summary(guide: str | None, limit: int = _GUIDE_SUMMARY_CHAR_LIMIT) -> str | None:
    """Return a short guide snippet suitable for inlining in every tool response."""
    if not guide:
        return None
    text = guide.strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _resolve_collection_id(explicit: str | None = None) -> str:
    """Resolve which collection an MCP tool call should target.

    Precedence: explicit tool argument → URL-pinned request profile →
    `settings.mcp_default_collection`. The resolved id is validated against
    `collection_service` so callers get a clear error (pointing them at
    `list_collections`) instead of a cryptic downstream failure.
    """
    candidate = explicit
    if not candidate:
        candidate = get_request_mcp_profile().get("collection_id")
    if not candidate:
        candidate = settings.mcp_default_collection
    candidate = (candidate or "").strip()
    if not candidate:
        raise ValueError(
            "No collection specified. Pass collection_id explicitly or call "
            "list_collections() to see what is available."
        )
    if not collection_service.get_collection(candidate):
        # The model may have passed the collection's display name rather than its
        # UUID — do a case-insensitive name lookup before giving up. The lookup
        # runs over the caller's visible collections only, so in private-
        # collections mode it can't leak names.
        all_cols = _visible_collections()
        name_match = next(
            (c for c in all_cols if c.get("name", "").strip().lower() == candidate.lower()),
            None,
        )
        if name_match:
            candidate = name_match["id"]
        else:
            raise ValueError(
                f"Collection '{candidate}' not found. Call list_collections() to "
                f"see available collections."
            )

    if settings.private_collections:
        # Access enforcement for every tool that funnels through here. An
        # invisible collection answers exactly like a missing one, so ids
        # can't be probed over MCP.
        from middleware.user_context import get_request_user
        from services.sharing_service import sharing_service

        if not sharing_service.check_collection_access(candidate, get_request_user()):
            raise ValueError(
                f"Collection '{candidate}' not found. Call list_collections() to "
                f"see available collections."
            )

    # A restricted collection is invisible to agents unless this request
    # carries a personal token scoped to it — in every deployment mode.
    from services.governance import mcp_can_expose

    if not mcp_can_expose(collection_service.get_collection(candidate), get_request_mcp_token_scope()):
        raise ValueError(
            f"Collection '{candidate}' not found. Call list_collections() to "
            f"see available collections."
        )
    return candidate


def _visible_collections() -> list[dict[str, Any]]:
    """Collections an MCP request may enumerate: the caller's accessible
    set (private-collections aware), minus restricted ones the token is not
    scoped to. Every fan-out and listing tool uses this, never the raw
    collection list."""
    from services.governance import mcp_can_expose

    scope = get_request_mcp_token_scope()
    return [
        c for c in collection_service.get_all_collections()
        if mcp_can_expose(c, scope)
    ]


def _visible_documents(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Quarantined documents never appear in MCP listings."""
    from services.governance import visible_documents

    return visible_documents(docs)


def _is_hidden_doc(doc_info: dict[str, Any] | None) -> bool:
    from services.governance import is_hidden

    return is_hidden(doc_info)


def _serialize_result(
    result: Any, rank: int, max_source_length: int, collection: dict[str, Any] | None = None
) -> dict[str, Any]:
    from services.governance import effective_sensitivity

    payload = {
        "rank": rank,
        "filename": result.filename,
        "page_number": result.page_number,
        "similarity_score": round(float(result.similarity_score), 4),
        "excerpt": _truncate(result.text_snippet, max_source_length),
        "document_id": result.document_id,
        "source_format": result.source_format,
        "source_type": result.source_type,
        "source_path": result.source_path,
        # Label in force, so an agent can honour handling rules downstream.
        "sensitivity": effective_sensitivity(collection, getattr(result, "sensitivity", None)),
    }

    if getattr(result, "symbol_name", None):
        payload["symbol_name"] = result.symbol_name
    if getattr(result, "unit_name", None):
        payload["unit_name"] = result.unit_name
    if getattr(result, "line_start", None) is not None:
        payload["line_start"] = result.line_start
    if getattr(result, "line_end", None) is not None:
        payload["line_end"] = result.line_end

    return payload


@_asymptote_mcp.tool()
def health_check() -> dict[str, Any]:
    """Check that the Asymptote MCP server is reachable and report its status.

    Call this FIRST when connecting to a new server, or whenever tools start
    returning errors, to verify the server is healthy before spending context
    on retries.

    Returns:
      - status: "ok" when the server is healthy
      - server_id: unique identifier for this Asymptote instance
      - version: schema/data version
      - embedding: active embedding provider and model name
      - mcp: key MCP settings (mode, top_k, default_collection)
      - collections: list of all available collection ids and names
    """

    try:
        collections = _visible_collections()
        col_summary = [
            {
                "collection_id": c.get("id", ""),
                "name": c.get("mcp_display_name") or c.get("name", c.get("id", "")),
                "document_count": c.get("document_count", 0),
            }
            for c in collections
            if c.get("id")
        ]
    except Exception as e:
        col_summary = []
        logger.warning(f"health_check: could not list collections: {e}")

    from services.embedding_providers import get_provider, default_model_for

    embedding_info: dict[str, Any] = {
        "provider": settings.embedding_provider,
        "provider_label": (get_provider(settings.embedding_provider) or {}).get("label"),
    }
    if settings.embedding_provider == "local":
        embedding_info["model"] = settings.embedding_model
    else:
        embedding_info["model"] = (
            settings.remote_embedding_model or default_model_for(settings.embedding_provider)
        )
        if settings.embedding_provider == "ollama":
            embedding_info["base_url"] = settings.ollama_base_url

    return _tool_response({
        "status": "ok",
        "server_id": settings.mcp_server_id or "asymptote",
        "version": settings.schema_version,
        "embedding": embedding_info,
        "mcp": {
            "mode": settings.mcp_mode,
            "top_k": settings.mcp_top_k,
            "default_collection": settings.mcp_default_collection,
            "semantic_weight": settings.mcp_semantic_weight,
        },
        "collections": col_summary,
    }, "health_check")


@_asymptote_mcp.tool()
def list_collections() -> dict[str, Any]:
    """List every document collection available on this MCP server.

    Call this FIRST whenever the user's question could plausibly target a
    different collection than the current default — e.g. "What's in Jane
    Smith's portfolio?", "Compare client A and client B", or any question
    that names a person, account, or project that might map to its own
    collection. Each collection is typically one client / account / project
    and has its own documents, CSVs, and portfolio tables.

    Returns for each collection:
      - collection_id   → pass this as `collection_id` to any other tool
      - name            → human-facing name (match this against the user's phrasing)
      - description     → free-text description, may contain client identifiers
      - document_count  → number of indexed documents

    Once you know the target collection_id, pass it explicitly to
    `get_collection_info`, `search_collection`, `list_tables`,
    `query_table`, `aggregate_table`, etc. If
    you omit `collection_id`, those tools fall back to the server's default
    collection, which may not be what the user asked about.
    """

    collections = _visible_collections()
    default_id = (settings.mcp_default_collection or "").strip() or None

    entries = []
    for c in collections:
        cid = c.get("id")
        if not cid:
            continue
        entries.append({
            "collection_id": cid,
            "name": c.get("mcp_display_name") or c.get("name", cid),
            "description": c.get("mcp_display_description") or c.get("description", "") or "",
            "document_count": c.get("document_count", 0),
            "is_default": cid == default_id,
        })

    return _tool_response({
        "total_collections": len(entries),
        "default_collection_id": default_id,
        "collections": entries,
    }, "list_collections")


@_asymptote_mcp.tool()
def get_collection_info(
    collection_id: str | None = None,
    detail: Literal["counts", "with_documents"] = "with_documents",
) -> dict[str, Any]:
    """Get detailed information about a document collection.

    Call this after `list_collections` (or when you already know which
    collection to target) to inspect its documents, file types, and counts.
    Helps you craft better search queries by seeing what's indexed.

    Parameters:
      - collection_id: Optional. If omitted, uses the server's default
        collection. Pass an explicit id (from `list_collections`) to inspect
        a specific client / account / project collection.
      - detail: "with_documents" (default) returns the full per-document
        listing with filenames, chunk/page counts, and source metadata.
        "counts" returns only aggregate stats (total documents/chunks/pages
        and file-type histogram) — use this when you only need a quick sizing
        check and don't want the full document array in your context window.

    The response includes a `guide` field — the user-authored markdown brief
    for this collection (currency assumptions, entity aliases, column meanings,
    date conventions, etc.). Read it before answering — it encodes durable
    context you cannot recover from the files alone. A short summary is also
    travelled inline in every `search_collection` response; `get_collection_info`
    returns the full guide.
    """

    resolved_collection = _resolve_collection_id(collection_id)
    collection = collection_service.get_collection(resolved_collection)

    stats = indexer_manager.get_collection_stats(resolved_collection)

    try:
        indexer = indexer_manager.get_indexer(resolved_collection)
        documents = _visible_documents(indexer.list_documents())
    except Exception:
        documents = []

    file_types: dict[str, int] = {}
    doc_list = []
    for doc in documents:
        filename = doc.get("filename", "unknown")
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "unknown"
        file_types[ext] = file_types.get(ext, 0) + 1
        entry: dict[str, Any] = {
            "filename": filename,
            "document_id": doc.get("document_id"),
            "chunks": doc.get("num_chunks", 0),
            "pages": doc.get("num_pages", 0),
            "source_type": doc.get("source_type", "upload"),
        }
        if doc.get("source_path"):
            entry["source_path"] = doc["source_path"]
        doc_list.append(entry)

    guide_text = (collection or {}).get("guide") or None

    payload: dict[str, Any] = {
        "collection_id": resolved_collection,
        "collection_name": _mcp_safe_name(collection, resolved_collection),
        "description": _mcp_safe_description(collection, ""),
        "guide": guide_text,
        "total_documents": stats.get("total_documents", 0),
        "total_chunks": stats.get("total_chunks", 0),
        "total_pages": stats.get("total_pages", 0),
        "file_types": file_types,
        "search_modes": ["semantic", "keyword", "hybrid"],
    }
    if detail == "with_documents":
        payload["documents"] = doc_list
    return _tool_response(payload, "get_collection_info")


_FILTER_KEYS = ("document_ids", "filenames", "source_formats", "date_from", "date_to")


def _normalize_search_filters(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate and normalize a raw filters dict into the indexer's shape.

    Drops unknown/empty keys, coerces single strings into lists for the
    list-valued filters, and returns None when nothing usable remains so the
    search path stays on its unfiltered fast track.
    """
    if not filters or not isinstance(filters, dict):
        return None

    out: dict[str, Any] = {}
    for key in ("document_ids", "filenames", "source_formats"):
        val = filters.get(key)
        if val is None:
            continue
        if isinstance(val, str):
            val = [val]
        cleaned = [str(v).strip() for v in val if str(v).strip()]
        if cleaned:
            out[key] = cleaned

    for key in ("date_from", "date_to"):
        val = filters.get(key)
        if val and str(val).strip():
            out[key] = str(val).strip()

    unknown = set(filters) - set(_FILTER_KEYS)
    if unknown:
        logger.info("search filters: ignoring unknown keys %s", sorted(unknown))

    return out or None


@_asymptote_mcp.tool()
def get_collection_facets(collection_id: str | None = None) -> dict[str, Any]:
    """List the filterable metadata for a collection.

    Returns the distinct `source_formats` present and the `documents`
    (document_id, filename, source_format, upload_timestamp) so you can build a
    precise `filters` argument for search_collection — e.g. restrict a query to
    a single PDF, to all spreadsheets, or to a date range.
    """
    resolved_collection = _resolve_collection_id(collection_id)
    indexer = indexer_manager.get_indexer(resolved_collection)
    facets = indexer.vector_store.metadata_store.get_filter_facets()
    return _tool_response(
        {"collection_id": resolved_collection, **facets},
        "get_collection_facets",
    )


def search_all_collections_sync(
    query: str,
    mode: Literal["semantic", "keyword", "hybrid"] | None = None,
    top_k_per_collection: int = 3,
    collection_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Synchronous implementation of search_all_collections.

    Safe for in-process callers already running off the event loop; the MCP
    tool below wraps it in a worker thread.
    """
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty")

    all_cols = _visible_collections()
    if collection_ids:
        target_ids = [c.get("id") for c in all_cols if c.get("id") in set(collection_ids)]
    else:
        target_ids = [c.get("id") for c in all_cols if c.get("id")]

    if not target_ids:
        raise ValueError("No collections available to search.")

    request_profile = get_request_mcp_profile()
    per_k = max(1, min(int(top_k_per_collection), 10))
    mode_source = mode or request_profile.get("mode") or settings.mcp_mode
    try:
        resolved_mode = SearchMode(mode_source)
    except ValueError:
        resolved_mode = SearchMode(settings.mcp_mode)
    resolved_weight = max(0.0, min(
        float(request_profile.get("semantic_weight", settings.mcp_semantic_weight)), 1.0
    ))
    resolved_max_len = max(100, min(
        int(request_profile.get("max_source_length") or settings.mcp_max_source_length), 2000
    ))

    collection_results = []
    best_score = -1.0
    best_collection_id: str | None = None

    for cid in target_ids:
        collection = collection_service.get_collection(cid)
        try:
            indexer = indexer_manager.get_indexer(cid)
            search_result = indexer.search(
                query=normalized_query,
                top_k=per_k,
                mode=resolved_mode,
                semantic_weight=resolved_weight,
            )
            hits = search_result.get("results", [])
        except Exception as e:
            logger.warning("search_all_collections: failed for collection %s: %s", cid, e)
            continue

        if not hits:
            continue

        top_score = round(float(hits[0].similarity_score), 4)
        if top_score > best_score:
            best_score = top_score
            best_collection_id = cid

        collection_results.append({
            "collection_id": cid,
            "collection_name": _mcp_safe_name(collection, cid),
            "top_score": top_score,
            "results": [
                _serialize_result(r, rank, resolved_max_len, collection=collection)
                for rank, r in enumerate(hits, start=1)
            ],
        })

    collection_results.sort(key=lambda x: x["top_score"], reverse=True)

    return _tool_response({
        "query": normalized_query,
        "mode": resolved_mode.value,
        "top_k_per_collection": per_k,
        "collections_searched": len(collection_results),
        "best_collection": best_collection_id,
        "results_by_collection": collection_results,
    }, "search_all_collections")


@_asymptote_mcp.tool()
async def search_all_collections(
    query: str,
    mode: Literal["semantic", "keyword", "hybrid"] | None = None,
    top_k_per_collection: int = 3,
    collection_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Search across ALL collections (or a named subset) in one call.

    Use this when the user's question isn't obviously scoped to a single
    collection, when you want to find which collection holds the most
    relevant content, or when explicitly asked to "search everything."

    Parameters:
      - query: Natural-language or keyword query.
      - mode: "semantic", "keyword", or "hybrid". Defaults to server setting.
      - top_k_per_collection: Results returned per collection (1–10, default 3).
        Kept small to avoid context explosion across many collections.
      - collection_ids: Optional list of specific collection IDs to include.
        If omitted, all collections are searched.

    Returns results grouped by collection and ranked by top score. The
    top-level `best_collection` field names the collection with the single
    highest-scoring result — start there for single-collection follow-up
    calls to `search_collection` or `get_document_context`.
    """
    # Embedding + FAISS are synchronous CPU work; run the sync implementation
    # in a worker thread so concurrent UI/MCP requests stay responsive.
    return await asyncio.to_thread(
        search_all_collections_sync,
        query,
        mode=mode,
        top_k_per_collection=top_k_per_collection,
        collection_ids=collection_ids,
    )


@_asymptote_mcp.tool()
def list_recent_documents(
    limit: int = 10,
    collection_id: str | None = None,
) -> dict[str, Any]:
    """List the most recently indexed documents, newest first.

    Parameters:
      - limit: max documents to return (default 10, cap 50).
      - collection_id: if omitted, returns recent documents across ALL
        collections (up to `limit` total, sorted by upload time). Pass an
        explicit id to scope to one collection.

    Useful for "what did I just add?", "what's new?", or orientation after
    a fresh indexing run.
    """
    capped = max(1, min(int(limit), 50))

    def _docs_for_collection(cid: str) -> list[dict[str, Any]]:
        try:
            indexer = indexer_manager.get_indexer(cid)
            docs = _visible_documents(indexer.list_documents())
        except Exception:
            return []
        results = []
        for d in docs:
            results.append({
                "collection_id": cid,
                "collection_name": _mcp_safe_name(
                    collection_service.get_collection(cid), cid
                ),
                "document_id": d.get("document_id"),
                "filename": d.get("filename", "unknown"),
                "source_format": d.get("source_format"),
                "num_pages": d.get("num_pages", 0),
                "num_chunks": d.get("num_chunks", 0),
                "upload_timestamp": d.get("upload_timestamp") or "",
            })
        return results

    if collection_id:
        resolved = _resolve_collection_id(collection_id)
        all_docs = _docs_for_collection(resolved)
    else:
        all_docs = []
        for c in _visible_collections():
            cid = c.get("id")
            if cid:
                all_docs.extend(_docs_for_collection(cid))

    all_docs.sort(key=lambda d: d.get("upload_timestamp") or "", reverse=True)
    recent = all_docs[:capped]

    return _tool_response({
        "total_returned": len(recent),
        "scope": collection_id or "all_collections",
        "documents": recent,
    }, "list_recent_documents")


def search_collection_sync(
    query: str,
    collection_id: str | None = None,
    mode: Literal["semantic", "keyword", "hybrid"] | None = None,
    top_k: int | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synchronous implementation of search_collection.

    Called directly by in-process consumers that already run off the event
    loop (the chat tool executor in structured_chat, sync endpoints); the MCP
    tool below wraps it in a worker thread. Full parameter docs live on the
    MCP wrapper.
    """

    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty")

    request_profile = get_request_mcp_profile()
    resolved_collection = _resolve_collection_id(collection_id)
    top_k_source = top_k if top_k is not None else (request_profile.get("top_k") or settings.mcp_top_k)
    resolved_top_k = max(1, min(int(top_k_source), 20))
    mode_source = mode if mode is not None else (request_profile.get("mode") or settings.mcp_mode)
    try:
        resolved_mode = SearchMode(mode_source)
    except ValueError:
        raise ValueError(
            f"Invalid mode '{mode_source}'. Must be one of: semantic, keyword, hybrid."
        )
    resolved_weight = max(0.0, min(float(request_profile.get("semantic_weight", settings.mcp_semantic_weight)), 1.0))
    resolved_include_sources = request_profile.get("include_sources", settings.mcp_include_sources)
    resolved_max_source_length = max(100, min(int(request_profile.get("max_source_length") or settings.mcp_max_source_length), 2000))
    resolved_inline_row_threshold = max(0, min(int(request_profile.get("inline_row_threshold") or _DEFAULT_INLINE_ROW_THRESHOLD), 2000))

    collection = collection_service.get_collection(resolved_collection)
    collection_name = _mcp_safe_name(collection, resolved_collection)

    # Inline small CSV/XLSX tables before searching so we can (a) hand the
    # host LLM the full authoritative rows for numeric questions and (b)
    # drop the corresponding chunks from the `results` array — otherwise
    # the LLM might prefer the partial excerpts over the full data.
    structured_tables_payload: list[dict[str, Any]] = []
    inlined_filenames: set[str] = set()
    if resolved_inline_row_threshold > 0:
        try:
            s_tables, s_stores = collect_structured_tables([resolved_collection])
            if s_tables:
                ctx = build_structured_context(
                    s_tables,
                    s_stores,
                    inline_row_threshold=resolved_inline_row_threshold,
                )
                inlined_filenames = ctx.get("inlined_filenames") or set()
                for t in s_tables:
                    if t.get("filename") not in inlined_filenames:
                        continue
                    cid = t.get("collection_id")
                    store = s_stores.get(cid) if cid else None
                    if not store:
                        continue
                    rendered = render_table_as_rows(
                        store, t, max_rows=resolved_inline_row_threshold
                    )
                    if not rendered:
                        continue
                    entry: dict[str, Any] = {
                        "filename": t.get("filename"),
                        "sheet_name": t.get("sheet_name"),
                        "table_name": t.get("table_name"),
                        "row_count": t.get("row_count"),
                        "column_count": t.get("column_count"),
                        "columns": rendered["columns"],
                        "rows": rendered["rows"],
                    }
                    # Keep rows_jsonl as a fallback for large inlined tables
                    # where the list-of-lists representation would bloat the
                    # response (very wide columns × many rows).
                    if len(rendered["rows"]) > 50:
                        jsonl = render_table_as_jsonl(
                            store, t, max_rows=resolved_inline_row_threshold
                        )
                        if jsonl:
                            entry["rows_jsonl"] = jsonl
                    structured_tables_payload.append(entry)
        except Exception as e:
            logger.warning(f"MCP search_collection: structured context failed: {e}")

    resolved_filters = _normalize_search_filters(filters)

    indexer = indexer_manager.get_indexer(resolved_collection)
    search_result = indexer.search(
        query=normalized_query,
        top_k=resolved_top_k,
        mode=resolved_mode,
        semantic_weight=resolved_weight,
        skip_filenames=inlined_filenames or None,
        filters=resolved_filters,
    )

    # Always include collection stats inline so the MCP client can answer
    # meta-questions like "how many sources are in this collection?" without
    # having to call get_collection_info() as a separate round-trip.
    stats = indexer_manager.get_collection_stats(resolved_collection)
    guide_snippet = _guide_summary((collection or {}).get("guide"))
    collection_summary = {
        "collection_id": resolved_collection,
        "collection_name": collection_name,
        "description": collection.get("description", "") if collection else "",
        "total_documents": stats.get("total_documents", 0),
        "total_chunks": stats.get("total_chunks", 0),
        "total_pages": stats.get("total_pages", 0),
    }
    if guide_snippet:
        collection_summary["guide_summary"] = guide_snippet
        collection_summary["guide_summary_note"] = (
            "User-authored brief for this collection. For the full guide call "
            "get_collection_info()."
        )

    response: dict[str, Any] = {
        "query": normalized_query,
        "collection_id": resolved_collection,
        "collection_name": collection_name,
        "collection_summary": collection_summary,
        "mode": resolved_mode.value,
        "top_k": resolved_top_k,
        "total_results": len(search_result.get("results", [])),
    }
    if resolved_filters:
        response["filters_applied"] = resolved_filters

    suggested_next: list[dict[str, str]] = []
    if _detect_numeric_intent(normalized_query):
        suggested_next.append({
            "tool": "list_tables",
            "reason": (
                "Query looks numeric/aggregation-shaped. If the answer lives "
                "in a CSV/XLSX table, list_tables → get_table_schema → "
                "aggregate_table / query_table "
                "will give correct totals; search_collection results are "
                "truncated chunks."
            ),
        })
    if _detect_exact_match_intent(normalized_query):
        suggested_next.append({
            "tool": "find_in_documents",
            "reason": (
                "Query contains a quoted string or explicit 'exact/verbatim' "
                "wording. find_in_documents does a literal substring match "
                "without stopword-dropping and is more precise than semantic "
                "search for verbatim lookups."
            ),
        })
    if suggested_next:
        response["suggested_next"] = suggested_next

    if structured_tables_payload:
        response["structured_tables"] = structured_tables_payload
        response["structured_tables_hint"] = (
            "The collection contains CSV/XLSX tables whose FULL contents are "
            "included above in `structured_tables`. Each entry has `columns` "
            "(display headers) and `rows` (list of lists, same order as "
            "`columns`); larger tables may additionally carry `rows_jsonl` as "
            "a JSONL fallback. For any numeric, aggregation, sum, count, "
            "average, filter, ranking, or date-range question about these "
            "files, answer DIRECTLY and EXCLUSIVELY from those rows — the "
            "chunk excerpts in `results` are truncated and must not be used "
            "for numeric reasoning. Chunks for fully-inlined files have "
            "already been removed from `results`."
        )

    if resolved_include_sources:
        response["results"] = [
            _serialize_result(result, rank, resolved_max_source_length, collection=collection)
            for rank, result in enumerate(search_result.get("results", []), start=1)
        ]
    else:
        response["results"] = [
            {
                "rank": rank,
                "filename": result.filename,
                "page_number": result.page_number,
                "similarity_score": round(float(result.similarity_score), 4),
                "document_id": result.document_id,
            }
            for rank, result in enumerate(search_result.get("results", []), start=1)
        ]

    return _tool_response(response, "search_collection")


@_asymptote_mcp.tool()
async def search_collection(
    query: str,
    collection_id: str | None = None,
    mode: Literal["semantic", "keyword", "hybrid"] | None = None,
    top_k: int | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Search indexed documents using semantic, keyword, or hybrid retrieval.

    Parameters:
      - query: Natural-language query for semantic mode or exact terms for keyword.
      - collection_id: Optional. If omitted, uses the server's default
        collection. When the user asks about a specific client / account /
        project, first call `list_collections` to find the right id, then
        pass it here.
      - mode: Retrieval strategy. "semantic" uses vector similarity (best for
        conceptual / paraphrased questions). "keyword" uses BM25 and is the
        right pick for exact phrases, proper nouns, identifiers, or quoted
        strings the user cites verbatim. "hybrid" blends both and is a safe
        default. Omit to use the server-configured default.
      - top_k: Max number of chunks to return (1-20). Omit to use the
        server-configured default. Smaller values produce tighter, more
        focused context; larger values widen the recall net.
      - filters: Optional metadata pre-filter to narrow retrieval BEFORE ranking
        — strongly recommended on large collections. A dict with any of:
          - document_ids: list of document_id strings (restrict to these docs)
          - filenames: list of exact filenames
          - source_formats: list of file types, e.g. ["pdf", "docx"]
          - date_from / date_to: ISO timestamps bounding the document upload time
        Call get_collection_info() (or get_collection_facets) first to discover
        document ids, filenames, and available formats.

    When NOT to use this tool:
    For any numeric, aggregation, sum, count, average, ranking, filter, or
    date-range question about CSV / Excel data, DO NOT use search_collection.
    Chunk retrieval truncates tabular data and the excerpts you get back will
    be a subset of rows — answering numeric questions from them leads to
    hallucinated totals. Instead use:
      - `list_tables` → `get_table_rows` (small tables, read full data)
      - `list_tables` → `aggregate_table` (group-by / aggregation, no SQL)
      - `list_tables` → `query_table` (ad-hoc SQL for any domain)

    When this tool IS the right choice:
    - Narrative / prose / conceptual questions about PDFs, text, code, notes.
    - "How does X work", "where is Y described", "what does the doc say about Z".

    For exact-string lookups (ticker symbols, CUSIPs, quoted phrases, policy
    numbers, any verbatim identifier the user cites word-for-word), prefer
    `find_in_documents` — it does a literal substring match and doesn't
    drop stopwords or tokenize the query.

    Convenience: the response auto-inlines the full contents of any small
    CSV/XLSX table in the collection as `structured_tables` — each entry
    carries `columns` and `rows` (list of lists). Any question involving
    numbers from those files must be answered DIRECTLY and EXCLUSIVELY from
    `structured_tables` — not from the `results` excerpts. Chunks for
    fully-inlined files are dropped from `results` so you aren't tempted to
    use them.

    The response always includes a `collection_summary` field with document,
    page, and chunk counts so you can answer meta-questions about the
    collection without a separate get_collection_info() call.
    """
    # Embedding + FAISS are synchronous CPU work; run the sync implementation
    # in a worker thread so concurrent UI/MCP requests stay responsive.
    return await asyncio.to_thread(
        search_collection_sync,
        query,
        collection_id=collection_id,
        mode=mode,
        top_k=top_k,
        filters=filters,
    )


_DOC_CONTEXT_MAX_CHARS_DEFAULT = 12000
_DOC_CONTEXT_MAX_CHARS_CAP = 40000


@_asymptote_mcp.tool()
def get_document_context(
    document_id: str,
    page_number: int | None = None,
    chunk_id: str | None = None,
    window: int = 1,
    max_chars: int = _DOC_CONTEXT_MAX_CHARS_DEFAULT,
    collection_id: str | None = None,
) -> dict[str, Any]:
    """Fetch the full text of a specific document, page, or chunk neighborhood.

    Use this AFTER `search_collection` when you need more than the truncated
    excerpt — e.g. the user asks "what else does that page say?", "quote the
    full paragraph", or "show me the surrounding context." It's also the right
    tool when you already know a `document_id` (from a prior citation or
    `get_collection_info`) and want to read it directly without another
    similarity search.

    Parameters:
      - document_id: The id returned in search `results[].document_id` or
        `get_collection_info(...).documents[].document_id`. Required.
      - page_number: If set, return only chunks on this page (1-indexed).
        Best when the user cites a specific page from a prior search result.
      - chunk_id: If set, return the matching chunk plus `window` neighbors
        on each side in the document's natural order. Use when `search_collection`
        returned a specific chunk and you want its immediate surroundings.
      - window: Number of neighboring chunks to include on each side of
        `chunk_id` (default 1). Ignored unless `chunk_id` is also set.
      - max_chars: Soft cap on returned text length (default 12000, max 40000).
        Chunks are concatenated in order until the cap is reached; the
        response flags `truncated: true` if there's more to read.
      - collection_id: Optional. If omitted, uses the server's default
        collection. Pass an explicit id (from `list_collections`) when the
        document lives in a specific client / account / project collection.

    Returns:
      - document_id, filename, source_format, total_pages
      - chunks: list of {chunk_id, page_number, chunk_index, text}
      - text: concatenated text of the returned chunks (newline-joined)
      - total_chars: length of `text`
      - truncated: true if `max_chars` was hit before all matching chunks
        were included — call again with a narrower filter or a larger cap

    When NOT to use this tool:
      - To search for a concept or topic — use `search_collection` instead.
        This tool assumes you already know the `document_id`.
      - For numeric / aggregation questions about CSV or Excel data — use
        `get_table_rows` or `query_table`; this tool returns chunk text
        which is truncated for tabular sources.
    """

    resolved_collection = _resolve_collection_id(collection_id)
    indexer = indexer_manager.get_indexer(resolved_collection)
    metadata_store = indexer.vector_store.metadata_store

    doc_info = metadata_store.get_document_info(document_id)
    if not doc_info or _is_hidden_doc(doc_info):
        raise ValueError(
            f"Document '{document_id}' not found in collection "
            f"'{resolved_collection}'. Call get_collection_info() to see "
            f"available document_ids."
        )

    all_chunks = metadata_store.get_chunks_by_document(document_id)
    if not all_chunks:
        raise ValueError(
            f"Document '{document_id}' has no indexed chunks."
        )

    selected: list[dict[str, Any]]
    if chunk_id:
        anchor_idx = next(
            (i for i, c in enumerate(all_chunks) if c.get("chunk_id") == chunk_id),
            None,
        )
        if anchor_idx is None:
            raise ValueError(
                f"chunk_id '{chunk_id}' not found in document '{document_id}'."
            )
        w = max(0, int(window))
        lo = max(0, anchor_idx - w)
        hi = min(len(all_chunks), anchor_idx + w + 1)
        selected = all_chunks[lo:hi]
    elif page_number is not None:
        selected = [c for c in all_chunks if c.get("page_number") == page_number]
        if not selected:
            raise ValueError(
                f"No chunks found for page {page_number} in document "
                f"'{document_id}'. The document has {doc_info.get('num_pages', 0)} pages."
            )
    else:
        selected = all_chunks

    cap = max(500, min(int(max_chars), _DOC_CONTEXT_MAX_CHARS_CAP))
    emitted: list[dict[str, Any]] = []
    running = 0
    truncated = False
    for chunk in selected:
        text = chunk.get("text") or ""
        if running and running + len(text) + 1 > cap:
            truncated = True
            break
        emitted.append({
            "chunk_id": chunk.get("chunk_id"),
            "page_number": chunk.get("page_number"),
            "chunk_index": chunk.get("chunk_index"),
            "text": text,
        })
        running += len(text) + 1

    joined = "\n".join(c["text"] for c in emitted)

    return _tool_response({
        "collection_id": resolved_collection,
        "document_id": document_id,
        "filename": doc_info.get("filename"),
        "source_format": doc_info.get("source_format"),
        "total_pages": doc_info.get("num_pages"),
        "total_chunks_in_document": len(all_chunks),
        "chunks": emitted,
        "text": joined,
        "total_chars": len(joined),
        "truncated": truncated,
    }, "get_document_context")


_FIND_EXCERPT_PAD_CHARS = 80
_FIND_MAX_RESULTS = 100


def _find_literal_excerpt(text: str, pattern: str, case_insensitive: bool) -> tuple[int, str] | None:
    """Locate pattern in text, return (offset, excerpt_with_markers) or None."""
    if not text or not pattern:
        return None
    hay = text.lower() if case_insensitive else text
    needle = pattern.lower() if case_insensitive else pattern
    idx = hay.find(needle)
    if idx < 0:
        return None
    start = max(0, idx - _FIND_EXCERPT_PAD_CHARS)
    end = min(len(text), idx + len(pattern) + _FIND_EXCERPT_PAD_CHARS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    excerpt = (
        prefix
        + text[start:idx]
        + "«"
        + text[idx:idx + len(pattern)]
        + "»"
        + text[idx + len(pattern):end]
        + suffix
    )
    return idx, excerpt


def _find_regex_excerpt(
    text: str, regex: "_re.Pattern[str]"
) -> tuple[int, str, str] | None:
    """Find the first regex match in text.

    Returns (offset, excerpt_with_markers, matched_text) or None. Mirrors
    `_find_literal_excerpt` but wraps the actual matched span (which may differ
    from the raw pattern) in « » markers.
    """
    if not text:
        return None
    m = regex.search(text)
    if not m:
        return None
    idx, end_idx = m.start(), m.end()
    matched = text[idx:end_idx]
    start = max(0, idx - _FIND_EXCERPT_PAD_CHARS)
    end = min(len(text), end_idx + _FIND_EXCERPT_PAD_CHARS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    excerpt = (
        prefix
        + text[start:idx]
        + "«"
        + matched
        + "»"
        + text[end_idx:end]
        + suffix
    )
    return idx, excerpt, matched


def find_in_documents_sync(
    pattern: str,
    literal: bool = True,
    case_sensitive: bool = False,
    collection_id: str | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
    """Synchronous implementation of find_in_documents.

    Safe for in-process callers already running off the event loop; the MCP
    tool below wraps it in a worker thread. Full parameter docs live on the
    MCP wrapper.
    """
    if not pattern or not pattern.strip():
        raise ValueError("pattern must not be empty")

    regex: "_re.Pattern[str] | None" = None
    if not literal:
        flags = 0 if case_sensitive else _re.IGNORECASE
        try:
            regex = _re.compile(pattern, flags)
        except _re.error as e:
            raise ValueError(f"Invalid regular expression: {e}")

    resolved_collection = _resolve_collection_id(collection_id)
    capped = max(1, min(int(max_results), _FIND_MAX_RESULTS))

    indexer = indexer_manager.get_indexer(resolved_collection)
    metadata_store = indexer.vector_store.metadata_store
    hidden_docs = metadata_store.get_hidden_document_ids()

    def _scan() -> tuple[list[dict[str, Any]], int]:
        # Literal ASCII patterns are prefiltered in SQL (INSTR), so only
        # candidate chunks stream out of the database; regex and non-ASCII
        # patterns stream the whole corpus through a cursor without ever
        # materializing it in memory. Matches are always re-verified here.
        sql_prefilter = pattern if (literal and pattern.isascii()) else None
        rows = metadata_store.iter_chunk_texts(
            substring=sql_prefilter, case_sensitive=case_sensitive
        )

        matches: list[dict[str, Any]] = []
        scanned = 0
        for chunk_id, document_id, filename, page_number, text in rows:
            if document_id in hidden_docs:
                continue
            scanned += 1
            text = text or ""
            if regex is not None:
                hit = _find_regex_excerpt(text, regex)
                if not hit:
                    continue
                offset, excerpt, matched = hit
            else:
                literal_hit = _find_literal_excerpt(text, pattern, case_insensitive=not case_sensitive)
                if not literal_hit:
                    continue
                offset, excerpt = literal_hit
                matched = pattern
            matches.append({
                "filename": filename,
                "document_id": document_id,
                "chunk_id": chunk_id,
                "page_number": page_number,
                "offset": offset,
                "excerpt": excerpt,
                "match": matched,
            })
            if len(matches) >= capped:
                break
        return matches, scanned

    matches, scanned = _scan()

    return _tool_response({
        "collection_id": resolved_collection,
        "pattern": pattern,
        "literal": bool(literal),
        "case_sensitive": bool(case_sensitive),
        "total_matches": len(matches),
        "chunks_scanned": scanned,
        "truncated": len(matches) >= capped,
        "matches": matches,
    }, "find_in_documents")


@_asymptote_mcp.tool()
async def find_in_documents(
    pattern: str,
    literal: bool = True,
    case_sensitive: bool = False,
    collection_id: str | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
    """Exact-substring OR regex search across a collection's indexed text chunks.

    Use this tool when the user cites an exact string that should appear
    verbatim in a document (ticker symbol, CUSIP, client-name fragment, quoted
    phrase, identifier, policy number) OR when you want a structural / pattern
    match that semantic search can't express. Unlike `search_collection` (tuned
    for semantic / BM25 ranking, drops stopwords and punctuation), this tool
    matches the literal string or regex against the raw chunk text.

    This is the right tool for code-aware lookups over indexed source files:
    finding call sites (`literal="foo("`), definitions
    (`literal=false, pattern="def\\s+handle_\\w+"`), imports, `TODO`/`FIXME`
    comments, env-var reads, or any identifier or code pattern an embedding
    query would only fuzzily match.

    Parameters:
      - pattern: The string to find. Interpreted as a literal substring when
        `literal=true` (default) or as a Python regular expression when
        `literal=false`. Leading/trailing whitespace is preserved.
      - literal: If true (default), `pattern` is matched as a plain substring.
        If false, `pattern` is compiled as a regex (Python `re` syntax) and
        matched with `re.search` against each chunk — use this for code
        patterns, alternations, anchors, character classes, etc. Invalid
        regexes raise a clear error.
      - case_sensitive: If true, the match is case-exact. If false (default),
        matching ignores ASCII case (regex compiled with `re.IGNORECASE`). Use
        case_sensitive=true for ticker symbols or other case-bearing identifiers.
      - collection_id: Optional. If omitted, uses the server's default
        collection.
      - max_results: Max matches to return (default 20, cap 100). One chunk
        can match at most once — if a chunk contains the pattern multiple
        times it still counts as one result.

    Returns a list of matches, each with:
      - filename, document_id, chunk_id, page_number
      - excerpt: the chunk text around the match, with the matched span
        wrapped in « » so it's easy to spot
      - offset: character offset of the match within the chunk
      - match: the exact text the pattern matched (useful with regex, where the
        matched span differs from the pattern)

    When NOT to use this tool:
      - For conceptual / paraphrased questions — use `search_collection`
        with mode="semantic" or "hybrid" instead.
      - For numeric or aggregation questions about CSV / Excel data — use
        `get_table_rows`, `aggregate_table`, or `query_table` instead;
        tabular chunks are truncated and an exact match may miss rows.
    """
    # The corpus scan is synchronous CPU work; run it off the event loop.
    return await asyncio.to_thread(
        find_in_documents_sync,
        pattern,
        literal=literal,
        case_sensitive=case_sensitive,
        collection_id=collection_id,
        max_results=max_results,
    )


def _get_structured_store(collection_id: str) -> StructuredStore:
    indexer = indexer_manager.get_indexer(collection_id)
    return indexer.vector_store.structured_store


def _no_table_error(
    store: StructuredStore, identifier: str, collection_id: str
) -> ValueError:
    """Build a `No structured table found` error with a did_you_mean suggestion.

    The chat model occasionally constructs a mangled identifier (e.g. mashing
    the filename stem and the full filename together). Rather than force a
    full `list_tables` round-trip we include up to three likely matches so the
    model can self-correct on the next turn.
    """
    base = (
        f"No structured table found for '{identifier}' in collection "
        f"'{collection_id}'."
    )
    try:
        suggestions = store.suggest_identifiers(identifier, limit=3)
    except Exception:
        suggestions = []
    if suggestions:
        hint_parts = [
            f"'{s['table_name']}' (source: {s['filename']})"
            for s in suggestions
            if s.get('table_name')
        ]
        if hint_parts:
            base += " Did you mean: " + "; ".join(hint_parts) + "?"
    return ValueError(
        base + " Call list_tables() to see every available table."
    )


def _format_schema_summary(schema: dict[str, Any]) -> dict[str, Any]:
    """Trim a full schema payload down to what an LLM needs to write a query."""
    columns = []
    detected_roles: dict[str, str] = {}
    for c in schema["columns"]:
        role = c.get("role")
        if role:
            detected_roles[c["sql_name"]] = role
        raw_stats = c.get("stats") or {}
        col_stats = (
            {k: v for k, v in raw_stats.items()
             if k in ("min", "max", "mean", "sum", "count", "p25", "p50", "p75")}
            if raw_stats else None
        )
        columns.append({
            "sql_name": c["sql_name"],
            "original_name": c["name"],
            "type": c["type"],
            "role": role,
            "raw_sql_name": c.get("raw_sql_name"),
            "samples": c.get("samples", [])[:3],
            "stats": col_stats,
        })

    result: dict[str, Any] = {
        "filename": schema["filename"],
        "sheet_name": schema.get("sheet_name") or None,
        "table_name": schema["table_name"],
        "row_count": schema["row_count"],
        "column_count": schema["column_count"],
        "columns": columns,
    }
    if detected_roles:
        result["financial_roles"] = detected_roles
    return result


@_asymptote_mcp.tool()
def list_tables(collection_id: str | None = None) -> dict[str, Any]:
    """List every CSV / Excel sheet ingested as a typed SQL table.

    Each entry returns the physical `table_name` to use in SQL queries, the
    original filename, and row/column counts. Call this FIRST whenever a
    user asks a numeric / aggregation / filter / ranking question about CSV
    or Excel data — `search_collection` truncates tabular data via chunk
    retrieval and leads to hallucinated totals.

    Parameters:
      - collection_id: Optional. If omitted, uses the server's default
        collection. When the user asks about a specific client / project,
        first call `list_collections` and then pass the right id here.

    Identifier forms accepted by other table tools:
      - `table_name`  — the SQL table identifier (e.g. "csv_data_abc123")
      - `filename`    — original source filename (e.g. "portfolio.csv")
      - `document_id` — the document's UUID from `get_collection_info`

    Recommended workflow after calling this:
      - Inspect schema + samples first → `get_table_schema(identifier)`.
      - Small table, just want the data → `get_table_rows(identifier)`.
      - Ad-hoc SQL aggregation → `query_table(sql)`.
      - Generic groupBy/aggregate → `aggregate_table(identifier, ...)`.
    """
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    raw_tables = store.list_tables()
    # Strip column-role detail from the listing — it belongs in get_table_schema.
    tables = [
        {k: v for k, v in t.items() if k != "financial_roles"}
        for t in raw_tables
    ]
    return _tool_response({
        "collection_id": resolved_collection,
        "total_tables": len(tables),
        "tables": tables,
    }, "list_tables")


@_asymptote_mcp.tool()
def get_table_schema(
    identifier: str,
    collection_id: str | None = None,
    identifier_type: Literal["table_name", "filename", "document_id"] | None = None,
) -> dict[str, Any]:
    """Return the full typed schema of an ingested CSV / Excel sheet.

    Parameters:
      - identifier: a `table_name`, a `filename`, or a `document_id` —
        whichever is more convenient.
      - collection_id: Optional. If omitted, uses the server's default
        collection. Pass an explicit id (from `list_collections`) when the
        table lives in a specific client / portfolio collection.
      - identifier_type: Optional. Restrict the lookup to exactly one of
        "table_name", "filename", or "document_id". When omitted (default),
        all three are searched — the auto-detect behavior. Pass this only
        when you've seen the identifier match the wrong record.

    The response lists every column with its inferred type, sample values,
    and stats (min/max/mean/sum/p25/p50/p75 for numeric columns). Use this
    before writing SQL so you know exact column names and types.

    When semantic column roles are detected, the response includes a
    top-level `financial_roles` map of sql_name → role. Use it as a hint when
    writing SQL or choosing an `aggregate_table` column; when the field is
    absent, treat every column as plain typed data.
    """
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    schema = store.get_schema(identifier, identifier_type=identifier_type)
    if not schema:
        raise _no_table_error(store, identifier, resolved_collection)
    return _tool_response(_format_schema_summary(schema), "get_table_schema")


@_asymptote_mcp.tool()
def get_table_rows(
    identifier: str,
    limit: int = 200,
    collection_id: str | None = None,
    identifier_type: Literal["table_name", "filename", "document_id"] | None = None,
) -> dict[str, Any]:
    """Return the full rows of an ingested CSV / Excel table in one call.

    This is the happy-path tool for "show me the data" and any
    numeric / aggregation question about a small-to-medium table — you get
    every column and every row without writing SQL.

    Parameters:
      - identifier: a table_name, filename (e.g. "sales.csv"), or
        document_id — whichever is most convenient. See `list_tables` for
        the available forms.
      - limit: max rows to return (default 200, cap 2000).
      - collection_id: Optional. If omitted, uses the server's default
        collection. Pass an explicit id (from `list_collections`) when the
        table lives in a specific client / project collection.
      - identifier_type: Optional. Restrict the lookup to exactly one of
        "table_name", "filename", or "document_id". When omitted (default),
        all three are searched.

    Returns:
      - columns: original column headers from the source file (display names)
      - rows: list of lists, one per row, in source order
      - row_count: rows returned in this response
      - total_row_count: total rows in the underlying table
      - truncated: true if there are more rows beyond `limit`

    If the table is large and you need aggregates, use `aggregate_table` or
    write SQL via `query_table` instead of pulling every row.
    """
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    schema = store.get_schema(identifier, identifier_type=identifier_type)
    if not schema:
        raise _no_table_error(store, identifier, resolved_collection)
    table_name = schema["table_name"]
    capped = max(1, min(int(limit), 2000))
    select_cols = ['"__row_number"'] + [f'"{c["sql_name"]}"' for c in schema.get("columns", [])]
    sql = f'SELECT {", ".join(select_cols)} FROM "{table_name}" ORDER BY __row_number'
    try:
        result = store.execute_query(sql, max_rows=capped)
    except SQLValidationError as e:
        raise ValueError(str(e))

    sql_to_orig = {
        c["sql_name"]: c["name"]
        for c in schema.get("columns", [])
        if c.get("sql_name") and c.get("name")
    }
    raw_columns: list[str] = result.get("columns") or []
    raw_rows: list[list[Any]] = result.get("rows") or []
    keep_idx = [i for i, c in enumerate(raw_columns) if c != "__row_number"]
    display_columns = [sql_to_orig.get(raw_columns[i], raw_columns[i]) for i in keep_idx]
    display_rows = [[row[i] for i in keep_idx] for row in raw_rows]

    return _tool_response({
        "collection_id": resolved_collection,
        "table_name": table_name,
        "filename": schema.get("filename"),
        "sheet_name": schema.get("sheet_name"),
        "total_row_count": schema.get("row_count"),
        "columns": display_columns,
        "rows": display_rows,
        "row_count": len(display_rows),
        "truncated": result.get("truncated", False),
    }, "get_table_rows")


@_asymptote_mcp.tool()
def query_table(
    sql: str,
    max_rows: int = 500,
    collection_id: str | None = None,
) -> dict[str, Any]:
    """Run a read-only SQL SELECT against the typed CSV / Excel tables.

    Use this for any ad-hoc analytical question that doesn't fit the canned
    tools — complex multi-table joins, window functions, custom WHERE clauses,
    etc. Works for ANY domain (sales, medical, financial, ...).

    Parameters:
      - sql: a single SELECT / WITH statement. INSERT / UPDATE / DELETE / DDL
        are rejected. Semicolons mid-query are rejected.
      - max_rows: response cap (default 500, max 2000).
      - collection_id: Optional. If omitted, uses the server's default
        collection. Pass an explicit id (from `list_collections`) when the
        table lives in a specific collection.

    Workflow: `list_tables(collection_id)` → `get_table_schema(identifier)`
    → issue SQL with the same collection_id. Column names are case-sensitive
    — always quote them in double-quotes:
      `SELECT "region", SUM("revenue") FROM "csv_data_abc123" GROUP BY "region"`

    For simple group-by aggregations prefer `aggregate_table` — no SQL to
    write. For whole-table reads prefer `get_table_rows(identifier)`.
    """
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    capped_rows = max(1, min(int(max_rows), 2000))
    try:
        result = store.execute_query(sql, max_rows=capped_rows)
    except SQLValidationError as e:
        raise ValueError(str(e))
    return _tool_response({
        "collection_id": resolved_collection,
        "sql": sql,
        **result,
    }, "query_table")


_AGG_FN_SQL = {
    "sum": "SUM",
    "mean": "AVG",
    "count": "COUNT",
    "min": "MIN",
    "max": "MAX",
}


@_asymptote_mcp.tool()
def aggregate_table(
    identifier: str,
    aggregate_col: str,
    agg_fn: Literal["sum", "mean", "count", "min", "max"],
    group_by: str | None = None,
    sort_by: Literal["value_asc", "value_desc", "group_asc", "group_desc"] | None = None,
    limit: int | None = None,
    collection_id: str | None = None,
    identifier_type: Literal["table_name", "filename", "document_id"] | None = None,
) -> dict[str, Any]:
    """Group-by / aggregate a table column — no SQL required.

    A convenience wrapper around `query_table` for the most common analytical
    pattern: group by one column, aggregate another. Works for ANY domain
    (sales, medical, financial, logistics, ...) — no financial role detection
    required.

    Parameters:
      - identifier: table_name, filename (e.g. "sales.csv"), or document_id.
        See `list_tables` for the available forms.
      - aggregate_col: column to aggregate (use the sql_name from
        `get_table_schema`).
      - agg_fn: aggregation function — sum | mean | count | min | max.
      - group_by: optional column to group by. If omitted the aggregation
        runs across the whole table (single-row result).
      - sort_by: optional sort order — "value_asc", "value_desc" (sort by the
        aggregate result), "group_asc", "group_desc" (sort by the group key).
        Ignored when group_by is omitted.
      - limit: optional row cap on the result (useful for "top N" queries).
      - collection_id: Optional. If omitted, uses the server's default
        collection. Pass an explicit id from `list_collections` when needed.
      - identifier_type: Optional. Restrict the lookup to exactly one of
        "table_name", "filename", or "document_id". When omitted (default),
        all three are searched.

    Examples:
      - Total revenue by region:
          aggregate_table("sales.csv", "revenue", "sum", group_by="region", sort_by="value_desc")
      - Average age by department:
          aggregate_table("employees.csv", "age", "mean", group_by="department")
      - Overall max temperature:
          aggregate_table("sensors.csv", "temperature", "max")
      - Top 10 products by quantity sold:
          aggregate_table("orders.csv", "quantity", "sum", group_by="product", sort_by="value_desc", limit=10)
    """
    if agg_fn not in _AGG_FN_SQL:
        raise ValueError(f"agg_fn must be one of: {sorted(_AGG_FN_SQL)}")

    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    schema = store.get_schema(identifier, identifier_type=identifier_type)
    if not schema:
        raise _no_table_error(store, identifier, resolved_collection)
    table_name = schema["table_name"]
    sql_fn = _AGG_FN_SQL[agg_fn]
    agg_expr = f'{sql_fn}("{aggregate_col}")'

    if group_by:
        select = f'SELECT "{group_by}", {agg_expr} AS result FROM "{table_name}" GROUP BY "{group_by}"'
        if sort_by == "value_desc":
            select += " ORDER BY result DESC"
        elif sort_by == "value_asc":
            select += " ORDER BY result ASC"
        elif sort_by == "group_desc":
            select += f' ORDER BY "{group_by}" DESC'
        elif sort_by == "group_asc":
            select += f' ORDER BY "{group_by}" ASC'
    else:
        select = f'SELECT {agg_expr} AS result FROM "{table_name}"'

    if limit is not None:
        capped = max(1, min(int(limit), 2000))
        select += f" LIMIT {capped}"

    try:
        result = store.execute_query(select, max_rows=2000)
    except SQLValidationError as e:
        raise ValueError(str(e))

    return _tool_response({
        "collection_id": resolved_collection,
        "identifier": identifier,
        "table_name": table_name,
        "agg_fn": agg_fn,
        "aggregate_col": aggregate_col,
        "group_by": group_by,
        "sql": select,
        **result,
    }, "aggregate_table")


@_asymptote_mcp.tool()
def get_document_metadata(
    document_id: str,
    collection_id: str | None = None,
) -> dict[str, Any]:
    """Return the full metadata record for an indexed document.

    Exposes fields that are buried in the indexing pipeline and not returned
    by `search_collection` or `get_collection_info`: source format, extraction
    method, embedding model, chunk parameters, and schema version.

    Parameters:
      - document_id: The id from `get_collection_info` or a search result.
      - collection_id: Optional. If omitted, uses the server's default
        collection.

    Returns:
      - document_id, filename
      - source_format: e.g. "pdf", "csv", "xlsx", "docx"
      - source_type: "upload", "local_path", "repo", etc.
      - source_path: original file path (if indexed from local disk)
      - extraction_method: OCR strategy used (e.g. "pdfminer", "vision", "csv")
      - embedding_model: model used to embed this document's chunks
      - chunk_size / chunk_overlap: chunking parameters
      - schema_version: metadata schema version for compatibility checks
      - num_pages / num_chunks: document sizing
      - upload_timestamp: when it was indexed
    """
    resolved_collection = _resolve_collection_id(collection_id)
    indexer = indexer_manager.get_indexer(resolved_collection)
    metadata_store = indexer.vector_store.metadata_store
    doc_info = metadata_store.get_document_info(document_id)
    if not doc_info or _is_hidden_doc(doc_info):
        raise ValueError(
            f"Document '{document_id}' not found in collection "
            f"'{resolved_collection}'. Call get_collection_info() to see "
            f"available document_ids."
        )
    return _tool_response({
        "collection_id": resolved_collection,
        "document_id": doc_info.get("document_id"),
        "filename": doc_info.get("filename"),
        "source_format": doc_info.get("source_format"),
        "source_type": doc_info.get("source_type"),
        "source_path": doc_info.get("source_path"),
        "extraction_method": doc_info.get("extraction_method"),
        "embedding_model": doc_info.get("embedding_model"),
        "chunk_size": doc_info.get("chunk_size"),
        "chunk_overlap": doc_info.get("chunk_overlap"),
        "schema_version": doc_info.get("schema_version"),
        "num_pages": doc_info.get("num_pages"),
        "num_chunks": doc_info.get("num_chunks"),
        "upload_timestamp": doc_info.get("upload_timestamp"),
    }, "get_document_metadata")


# ---------------------------------------------------------------------------
# Writing sources — agents adding or updating documents in a collection
# ---------------------------------------------------------------------------

# Text formats an agent may author directly. Binary formats (PDF, DOCX,
# images, audio) need a real file and go through the upload endpoints.
from services.code_extractor import SUPPORTED_CODE_EXTENSIONS as _CODE_EXTS

_WRITABLE_EXTENSIONS = {".md", ".txt", ".json", ".jsonl", ".csv", ".html", ".htm"} | set(_CODE_EXTS)
_WRITE_MAX_CHARS = 2_000_000
_WRITE_MODES = ("create", "replace", "append")


def _safe_source_filename(filename: str) -> str:
    """Normalize an agent-supplied filename to what the upload path would store.

    Folder separators flatten to underscores (same as folder uploads), path
    traversal and control characters are refused, and a bare name gets `.md`
    — agents that say "write my notes" usually mean markdown.
    """
    raw = (filename or "").strip().replace("\\", "/").strip("/")
    if not raw:
        raise ValueError("filename must not be empty")
    if any(ord(ch) < 32 for ch in raw):
        raise ValueError("filename must not contain control characters")
    safe = raw.replace("/", "_")
    if safe in (".", "..") or safe.startswith(".."):
        raise ValueError("filename must not be a relative path")
    from pathlib import PurePosixPath

    suffix = PurePosixPath(safe).suffix.lower()
    if not suffix:
        safe += ".md"
        suffix = ".md"
    if suffix not in _WRITABLE_EXTENSIONS:
        raise ValueError(
            f"'{suffix}' is not a text format this tool can write. Use one of: "
            ".md, .txt, .json, .jsonl, .csv, .html, or a source-code extension. "
            "Binary formats (PDF, DOCX, images, audio) must be uploaded as files."
        )
    return safe


def _require_mcp_write(collection_id: str | None) -> str:
    """Resolve the target collection and check every write gate.

    Mirrors api/documents._require_ingest for the agent surface: collection
    visibility, the token's write flag, the collection's write permission
    under private collections, and the acceptable-use acknowledgement.
    """
    resolved = _resolve_collection_id(collection_id)

    if not get_request_mcp_can_write():
        raise ValueError(
            "This MCP token is read-only. Generate a token with 'Allow adding "
            "and updating sources' enabled (Settings → MCP) to write to collections."
        )

    from middleware.user_context import get_request_user
    from services.sharing_service import sharing_service
    from services import governance
    from fastapi import HTTPException

    user = get_request_user()
    if sharing_service.check_collection_access(resolved, user) == "read":
        raise ValueError(
            f"Collection '{resolved}' is shared with you read-only; its owner "
            "manages the sources."
        )
    try:
        governance.require_aup(user)
    except HTTPException as e:
        detail = e.detail
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
        raise ValueError(f"{message} Open the app and accept it, then retry.")
    return resolved


def _find_document_by_filename(indexer, filename: str) -> dict[str, Any] | None:
    """Exact-filename lookup within one collection (newest first if several)."""
    store = indexer.vector_store.metadata_store
    for row in store.list_documents_page(limit=50, offset=0, q=filename):
        if row.get("filename") == filename:
            return row
    return None


def write_document_sync(
    filename: str,
    content: str,
    collection_id: str | None = None,
    mode: Literal["create", "replace", "append"] = "create",
) -> dict[str, Any]:
    """Synchronous implementation of write_document; docs on the MCP wrapper."""
    from middleware.user_context import get_request_user
    from services import audit, governance
    from services.content_policy import BlockedContentError, ContentRejectedError

    if mode not in _WRITE_MODES:
        raise ValueError(f"mode must be one of: {', '.join(_WRITE_MODES)}")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if not content.strip():
        raise ValueError("content must not be empty")
    if len(content) > _WRITE_MAX_CHARS:
        raise ValueError(
            f"content is {len(content):,} characters; the limit per write is "
            f"{_WRITE_MAX_CHARS:,}. Split it into several documents."
        )

    safe_filename = _safe_source_filename(filename)
    resolved = _require_mcp_write(collection_id)
    indexer = indexer_manager.get_indexer(resolved)
    store = indexer.vector_store.metadata_store
    document_dir = indexer_manager.get_documents_path(resolved)
    uploaded_by = get_request_user()

    existing = _find_document_by_filename(indexer, safe_filename)
    replaced_id: str | None = None
    final_content = content

    if existing:
        existing_id = existing.get("document_id")
        if mode == "create":
            raise ValueError(
                f"A source named '{safe_filename}' already exists in collection "
                f"'{resolved}' (document_id {existing_id}). Pass mode='replace' to "
                "overwrite it or mode='append' to add to the end of it."
            )
        if mode == "append":
            if existing.get("source_type") == "local_reference":
                raise ValueError(
                    f"'{safe_filename}' is indexed in place from "
                    f"{existing.get('source_path')}; edit that file directly and "
                    "re-index it rather than appending over MCP."
                )
            current_path = document_dir / safe_filename
            if not current_path.exists():
                raise ValueError(
                    f"The stored file for '{safe_filename}' is missing on disk; use "
                    "mode='replace' to write it fresh."
                )
            previous = current_path.read_text(encoding="utf-8", errors="replace")
            separator = "" if previous.endswith("\n\n") else ("\n" if previous.endswith("\n") else "\n\n")
            final_content = previous + separator + content
        # replace and append both retire the old record; its chunks, file and
        # collection membership go through the same path the delete button uses.
        governance.remove_document(resolved, existing_id, actor=uploaded_by)
        replaced_id = existing_id

    file_path = document_dir / safe_filename
    # newline="\n": store exactly the bytes the agent sent (no CRLF
    # translation on Windows), so the content hash is platform-independent.
    file_path.write_text(final_content, encoding="utf-8", newline="\n")

    # Identical bytes already indexed under another name: the document id is
    # the content hash, so indexing again would collide. Say so instead.
    content_hash = governance.content_hash_of(file_path)
    duplicate = store.get_document_info(content_hash[:16])
    if duplicate and duplicate.get("filename") != safe_filename:
        file_path.unlink(missing_ok=True)
        return _tool_response({
            "status": "duplicate",
            "collection_id": resolved,
            "filename": safe_filename,
            "document_id": duplicate.get("document_id"),
            "existing_filename": duplicate.get("filename"),
            "note": (
                f"Identical content is already indexed as '{duplicate.get('filename')}'. "
                "Nothing was written."
            ),
        }, "write_document")

    try:
        meta = indexer.index_document(
            file_path, safe_filename, collection_id=resolved, uploaded_by=uploaded_by,
        )
    except (BlockedContentError, ContentRejectedError) as e:
        file_path.unlink(missing_ok=True)
        raise ValueError(str(e))
    except Exception as e:
        file_path.unlink(missing_ok=True)
        logger.error("write_document: failed to index %s: %s", safe_filename, e)
        raise ValueError(f"Failed to index '{safe_filename}': {e}")

    collection_service.add_document(resolved, meta.document_id)
    indexer.save_index()
    audit.record(
        "document.upload", collection_id=resolved, document_id=meta.document_id,
        detail={
            "files": [safe_filename], "document_ids": [meta.document_id],
            "indexed": 1, "failed": 0, "via": "mcp", "mode": mode,
            "replaced_document_id": replaced_id,
        },
    )

    status = {"create": "created", "replace": "replaced", "append": "appended"}[mode]
    if mode != "create" and replaced_id is None:
        status = "created"  # replace/append with nothing there yet
    payload: dict[str, Any] = {
        "status": status,
        "collection_id": resolved,
        "collection_name": _mcp_safe_name(collection_service.get_collection(resolved), resolved),
        "document_id": meta.document_id,
        "filename": safe_filename,
        "total_pages": meta.total_pages,
        "total_chunks": meta.total_chunks,
        "policy_status": meta.policy_status,
        "characters": len(final_content),
    }
    if replaced_id:
        payload["replaced_document_id"] = replaced_id
    if meta.policy_status and meta.policy_status != "clear":
        payload["note"] = (
            "The content scan flagged this source; it may be held from search "
            "until an administrator reviews it."
        )
    return _tool_response(payload, "write_document")


@_asymptote_mcp.tool()
async def write_document(
    filename: str,
    content: str,
    collection_id: str | None = None,
    mode: Literal["create", "replace", "append"] = "create",
) -> dict[str, Any]:
    """Add a new text source to a collection, or update one you added before.

    The content is stored as a file in the collection and indexed exactly
    like an upload — searchable by `search_collection`, readable by
    `get_document_context`, visible in the app's Sources panel. Use it to
    save research notes, summaries, meeting minutes, generated reports,
    structured data, or any markdown you want the collection to remember.

    Parameters:
      - filename: Name for the source, e.g. "meeting-notes-2026-09.md". A
        name without an extension gets ".md". Only text formats are
        accepted: .md, .txt, .json, .jsonl, .csv, .html, and source-code
        extensions. Folder separators are flattened to underscores.
      - content: The full text to store (UTF-8). For CSV, include the
        header row — the file is also loaded as a queryable table.
      - collection_id: Optional. If omitted, uses the server's default
        collection. Call `list_collections` to pick the right target; a
        name is accepted in place of the id.
      - mode:
          "create"  (default) — fails if a source with this filename exists.
          "replace" — overwrite the existing source with `content`
                      (re-indexed from scratch; the old document_id retires).
          "append"  — add `content` to the end of the existing source and
                      re-index it. Creates the source if it doesn't exist.

    Returns:
      - status: "created", "replaced", "appended", or "duplicate" (identical
        content already indexed under another filename — nothing written)
      - document_id, filename, total_pages, total_chunks, policy_status
      - replaced_document_id: the retired id, for replace/append

    Rules and limits:
      - Writes need a token minted with writes enabled (Settings → MCP), or a
        signed-in session. A collection shared read-only refuses writes.
      - Content is scanned by the deployment's content policy and recorded
        against the caller's identity, like every upload.
      - One write holds at most 2,000,000 characters; split larger material.
      - Sources indexed in place from a local path cannot be appended to.

    To remove a source, use the app; there is deliberately no delete tool.
    """
    return await asyncio.to_thread(
        write_document_sync,
        filename,
        content,
        collection_id=collection_id,
        mode=mode,
    )


# ---------------------------------------------------------------------------
# MCP Resources — passive context the host LLM can load without tool calls
# ---------------------------------------------------------------------------


def _find_collection_for_document(document_id: str) -> tuple[str, dict[str, Any]] | None:
    """Search all collections for a document_id. Returns (collection_id, doc_info) or None."""
    for c in _visible_collections():
        cid = c.get("id")
        if not cid:
            continue
        try:
            indexer = indexer_manager.get_indexer(cid)
            doc_info = indexer.vector_store.metadata_store.get_document_info(document_id)
            if doc_info:
                return cid, doc_info
        except Exception:
            continue
    return None


def _find_collection_for_table(identifier: str) -> tuple[str, dict[str, Any]] | None:
    """Search all collections for a table identifier. Returns (collection_id, schema) or None."""
    for c in _visible_collections():
        cid = c.get("id")
        if not cid:
            continue
        try:
            store = _get_structured_store(cid)
            schema = store.get_schema(identifier)
            if schema:
                return cid, schema
        except Exception:
            continue
    return None


@_asymptote_mcp.resource("collection://{id}")
def resource_collection(id: str) -> dict[str, Any]:
    """Collection metadata and document inventory.

    URI: collection://{collection_id}

    Returns the same payload as get_collection_info(detail="with_documents")
    so the host LLM can load a complete collection overview — name,
    description, document list with chunk/page counts — without issuing a
    tool call. Useful for priming context at session start.
    """
    resolved = _resolve_collection_id(id)
    collection = collection_service.get_collection(resolved)
    stats = indexer_manager.get_collection_stats(resolved)
    try:
        indexer = indexer_manager.get_indexer(resolved)
        documents = _visible_documents(indexer.list_documents())
    except Exception:
        documents = []

    doc_list = []
    for doc in documents:
        entry: dict[str, Any] = {
            "filename": doc.get("filename", "unknown"),
            "document_id": doc.get("document_id"),
            "chunks": doc.get("num_chunks", 0),
            "pages": doc.get("num_pages", 0),
            "source_type": doc.get("source_type", "upload"),
        }
        if doc.get("source_path"):
            entry["source_path"] = doc["source_path"]
        doc_list.append(entry)

    return _tool_response({
        "collection_id": resolved,
        "collection_name": _mcp_safe_name(collection, resolved),
        "description": _mcp_safe_description(collection, ""),
        "total_documents": stats.get("total_documents", 0),
        "total_chunks": stats.get("total_chunks", 0),
        "total_pages": stats.get("total_pages", 0),
        "documents": doc_list,
    }, "resource_collection")


@_asymptote_mcp.resource("collection://{id}/schema")
def resource_collection_schema(id: str) -> dict[str, Any]:
    """All table schemas in a collection.

    URI: collection://{collection_id}/schema

    Returns the full typed schema for every CSV / Excel sheet in the
    collection, including column types, sample values, stats (with
    p25/p50/p75), and financial roles where detected. Load this once to
    understand all structured data in a collection without calling
    get_table_schema() for each table individually.
    """
    resolved = _resolve_collection_id(id)
    store = _get_structured_store(resolved)
    raw_tables = store.list_tables()
    schemas = []
    for t in raw_tables:
        full = store.get_schema(t["table_name"])
        if full:
            schemas.append(_format_schema_summary(full))
    return _tool_response({
        "collection_id": resolved,
        "total_tables": len(schemas),
        "schemas": schemas,
    }, "resource_collection_schema")


@_asymptote_mcp.resource("collections://all")
def resource_all_collections() -> dict[str, Any]:
    """Complete workspace map — every collection with stats and document inventory.

    URI: collections://all

    Load this ONCE at the start of a session to understand everything that is
    indexed: every collection's name, description, document list, and chunk/
    page counts. Eliminates the need for list_collections() + repeated
    get_collection_info() calls when getting oriented.
    """
    all_cols = _visible_collections()
    default_id = (settings.mcp_default_collection or "").strip() or None

    entries = []
    for c in all_cols:
        cid = c.get("id")
        if not cid:
            continue
        stats = indexer_manager.get_collection_stats(cid)
        try:
            indexer = indexer_manager.get_indexer(cid)
            raw_docs = _visible_documents(indexer.list_documents())
        except Exception:
            raw_docs = []

        doc_list = []
        for doc in raw_docs:
            entry: dict[str, Any] = {
                "filename": doc.get("filename", "unknown"),
                "document_id": doc.get("document_id"),
                "chunks": doc.get("num_chunks", 0),
                "pages": doc.get("num_pages", 0),
                "source_type": doc.get("source_type", "upload"),
                "upload_timestamp": doc.get("upload_timestamp") or "",
            }
            if doc.get("source_path"):
                entry["source_path"] = doc["source_path"]
            doc_list.append(entry)

        guide_text = c.get("guide") or ""
        entries.append({
            "collection_id": cid,
            "name": _mcp_safe_name(c, cid),
            "description": _mcp_safe_description(c, ""),
            "is_default": cid == default_id,
            "has_guide": bool(guide_text.strip()),
            "total_documents": stats.get("total_documents", 0),
            "total_chunks": stats.get("total_chunks", 0),
            "total_pages": stats.get("total_pages", 0),
            "documents": doc_list,
        })

    return _tool_response({
        "total_collections": len(entries),
        "default_collection_id": default_id,
        "tip": (
            "To read a collection's guide: collection://{id}/guide  |  "
            "To see its tables: collection://{id}/schema  |  "
            "To search everything: search_all_collections(query)"
        ),
        "collections": entries,
    }, "resource_all_collections")


@_asymptote_mcp.resource("collection://{id}/guide")
def resource_collection_guide(id: str) -> dict[str, Any]:
    """The full user-authored guide for a collection.

    URI: collection://{collection_id}/guide

    Guides encode durable context that can't be recovered from source files
    alone: entity aliases, date conventions, currency assumptions, column
    meanings, known quirks. Read this at the start of any session that
    touches this collection — it's the most important orientation document.
    """
    resolved = _resolve_collection_id(id)
    collection = collection_service.get_collection(resolved)
    guide = (collection or {}).get("guide") or ""
    return _tool_response({
        "collection_id": resolved,
        "collection_name": _mcp_safe_name(collection, resolved),
        "has_guide": bool(guide.strip()),
        "guide": guide,
    }, "resource_collection_guide")


@_asymptote_mcp.resource("collection://{id}/tables")
def resource_collection_tables(id: str) -> dict[str, Any]:
    """All structured table listings for a collection.

    URI: collection://{collection_id}/tables

    Returns every CSV / Excel sheet ingested as a typed SQL table —
    table_name, filename, sheet_name, and row/column counts. Use this as a
    fast orientation resource before running list_tables() or writing SQL.
    For full column schemas load collection://{id}/schema instead.
    """
    resolved = _resolve_collection_id(id)
    store = _get_structured_store(resolved)
    raw_tables = store.list_tables()
    tables = [
        {k: v for k, v in t.items() if k != "financial_roles"}
        for t in raw_tables
    ]
    return _tool_response({
        "collection_id": resolved,
        "total_tables": len(tables),
        "tables": tables,
    }, "resource_collection_tables")


@_asymptote_mcp.resource("document://{id}")
def resource_document(id: str) -> dict[str, Any]:
    """Full document metadata record — searched across ALL collections.

    URI: document://{document_id}

    Returns the complete metadata for a single document — source format,
    extraction method, embedding model, chunk parameters, schema version,
    page/chunk counts, and upload timestamp. Searches every collection
    automatically so you don't need to know which collection owns the document.
    """
    found = _find_collection_for_document(id)
    if not found:
        raise ValueError(
            f"Document '{id}' not found in any collection. "
            f"Call get_collection_info() to see available document_ids."
        )
    resolved, doc_info = found
    return _tool_response({
        "collection_id": resolved,
        "document_id": doc_info.get("document_id"),
        "filename": doc_info.get("filename"),
        "source_format": doc_info.get("source_format"),
        "source_type": doc_info.get("source_type"),
        "source_path": doc_info.get("source_path"),
        "extraction_method": doc_info.get("extraction_method"),
        "embedding_model": doc_info.get("embedding_model"),
        "chunk_size": doc_info.get("chunk_size"),
        "chunk_overlap": doc_info.get("chunk_overlap"),
        "schema_version": doc_info.get("schema_version"),
        "num_pages": doc_info.get("num_pages"),
        "num_chunks": doc_info.get("num_chunks"),
        "upload_timestamp": doc_info.get("upload_timestamp"),
    }, "resource_document")


@_asymptote_mcp.resource("table://{id}")
def resource_table(id: str) -> dict[str, Any]:
    """Table schema and sample rows in one fetch — searched across ALL collections.

    URI: table://{identifier}   where identifier is a table_name, filename,
    or document_id.

    Combines get_table_schema() + the first 10 rows in a single resource
    load, giving the host LLM enough context to write queries or answer
    basic questions without any tool calls. Searches every collection
    automatically so you don't need to know which collection owns the table.
    For the full row set use the get_table_rows() tool.
    """
    found = _find_collection_for_table(id)
    if not found:
        raise ValueError(
            f"No structured table found for '{id}' in any collection. "
            f"Call list_tables() to see available tables."
        )
    resolved, schema = found
    store = _get_structured_store(resolved)
    summary = _format_schema_summary(schema)

    table_name = schema["table_name"]
    sql = f'SELECT * FROM "{table_name}" ORDER BY __row_number LIMIT 10'
    try:
        result = store.execute_query(sql, max_rows=10)
    except Exception:
        result = {}

    sql_to_orig = {
        c["sql_name"]: c["name"]
        for c in schema.get("columns", [])
        if c.get("sql_name") and c.get("name")
    }
    raw_columns: list[str] = result.get("columns") or []
    raw_rows: list[list[Any]] = result.get("rows") or []
    keep_idx = [i for i, c in enumerate(raw_columns) if c != "__row_number"]
    display_columns = [sql_to_orig.get(raw_columns[i], raw_columns[i]) for i in keep_idx]
    display_rows = [[row[i] for i in keep_idx] for row in raw_rows]

    return _tool_response({
        "collection_id": resolved,
        "schema": summary,
        "sample_columns": display_columns,
        "sample_rows": display_rows,
        "sample_row_count": len(display_rows),
    }, "resource_table")


class ToggleableMCPApp:
    """Return HTTP 503 when MCP is disabled instead of exposing tools."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Handle ASGI lifespan here instead of forwarding to the inner Starlette app.
        # The session manager lifecycle is owned by mcp_server_lifespan() in the outer app;
        # forwarding lifespan would cause a second session_manager.run() call and raise
        # RuntimeError("can only be called once per instance").
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        if scope["type"] == "http" and not settings.enable_mcp:
            response = JSONResponse(
                {"detail": "Asymptote MCP is disabled in Settings."},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        profile = {}
        if scope["type"] == "http":
            query = parse_qs(scope.get("query_string", b"").decode("utf-8"))
            raw_profile = {
                key: values[-1]
                for key, values in query.items()
                if values and key in MCP_PROFILE_FIELDS
            }
            if raw_profile:
                profile = _normalize_profile(raw_profile)

        token = _request_mcp_profile.set(profile)
        # The personal-token collection scope (restricted-collection grant)
        # and write flag ride on request.state from main.py's auth middleware.
        state = scope.get("state", {}) if scope["type"] == "http" else {}
        scope_token = _request_mcp_token_scope.set(state.get("mcp_token_scope") or None)
        write_token = _request_mcp_can_write.set(bool(state.get("mcp_token_can_write", True)))
        # Bind the verified identity for private-collections scoping. The auth
        # middleware in main.py verifies the Access JWT and records the result
        # on the ASGI scope's state before this mount runs; MCP service tokens
        # carry their common_name as identity, so collections can be shared to
        # a client by that name. Password/bearer clients have no identity and
        # see team collections only.
        user_token = None
        if settings.private_collections:
            from middleware.user_context import set_request_user

            user_token = set_request_user(state.get("auth_identity"))
        try:
            await self.app(scope, receive, send)
        finally:
            if user_token is not None:
                from middleware.user_context import reset_request_user

                reset_request_user(user_token)
            _request_mcp_can_write.reset(write_token)
            _request_mcp_token_scope.reset(scope_token)
            _request_mcp_profile.reset(token)


embedded_mcp_app = ToggleableMCPApp(_asymptote_mcp.streamable_http_app())


@asynccontextmanager
async def mcp_server_lifespan():
    async with _asymptote_mcp.session_manager.run():
        yield
