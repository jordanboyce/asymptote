"""Embedded MCP server for Asymptote."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode

from mcp.server.fastmcp import FastMCP
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
)
from services.financial.metrics import AVAILABLE_METRICS, compute_financial_metric
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

_asymptote_mcp = FastMCP(
    "Asymptote",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
)


def _ensure_enabled() -> None:
    if not settings.enable_mcp:
        raise RuntimeError("Asymptote MCP is disabled in Settings.")


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


def get_collection_mcp_profile(collection_id: str) -> dict[str, Any]:
    collection = collection_service.get_collection(collection_id)
    if not collection:
        raise ValueError(f"Collection '{collection_id}' not found")

    stored = app_db.get_user_preference(_mcp_profile_preference_key(collection_id), {})
    profile = _normalize_profile({
        "collection_id": collection_id,
        **(stored or {}),
    })
    profile["collection_name"] = collection.get("name", collection_id)
    return profile


def save_collection_mcp_profile(collection_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    profile = get_collection_mcp_profile(collection_id)
    merged = _normalize_profile({
        **profile,
        **{key: value for key, value in updates.items() if key in MCP_PROFILE_FIELDS},
        "collection_id": collection_id,
    })
    app_db.set_user_preference(_mcp_profile_preference_key(collection_id), {
        key: merged[key]
        for key in MCP_PROFILE_FIELDS
        if key != "collection_id"
    })
    merged["collection_name"] = profile.get("collection_name", collection_id)
    return merged


def get_request_mcp_profile() -> dict[str, Any]:
    return _request_mcp_profile.get({})


def _sanitize_server_id(value: str) -> str:
    cleaned = ''.join(ch if ch.isalnum() or ch in {'-', '_'} else '-' for ch in value.strip().lower())
    return cleaned.strip('-_') or 'asymptote'


def build_mcp_export_payload(base_url: str, profile: dict[str, Any] | None = None) -> dict[str, str]:
    base_server_id = (settings.mcp_server_id or "asymptote").strip() or "asymptote"
    normalized_profile = _normalize_profile(profile or {"collection_id": settings.mcp_default_collection})
    collection_id = normalized_profile["collection_id"]
    server_id = _sanitize_server_id(f"{base_server_id}-{collection_id}")

    query_params = {
        "collection_id": normalized_profile["collection_id"],
        "top_k": str(normalized_profile["top_k"]),
        "mode": normalized_profile["mode"],
        "semantic_weight": str(normalized_profile["semantic_weight"]),
        "include_sources": str(normalized_profile["include_sources"]).lower(),
        "max_source_length": str(normalized_profile["max_source_length"]),
        "inline_row_threshold": str(normalized_profile["inline_row_threshold"]),
    }

    encoded_query = urlencode(query_params)
    server_url = f"{base_url.rstrip('/')}/mcp/?{encoded_query}"
    claude_json = json.dumps(
        {
            "mcpServers": {
                server_id: {
                    "type": "http",
                    "url": server_url,
                }
            }
        },
        indent=2,
    )
    codex_toml = f'[mcp_servers.{server_id}]\nurl = "{server_url}"\n'
    copilot_json = json.dumps(
        {
            "servers": {
                server_id: {
                    "type": "http",
                    "url": server_url,
                }
            }
        },
        indent=2,
    )
    return {
        "server_id": server_id,
        "server_url": server_url,
        "claude_json": claude_json,
        "codex_toml": codex_toml,
        "copilot_json": copilot_json,
        "claude_filename": f".mcp-{collection_id}.json",
        "codex_filename": f"config-{collection_id}.toml",
        "copilot_filename": ".vscode/mcp.json",
        "profile": normalized_profile,
    }


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
        raise ValueError(
            f"Collection '{candidate}' not found. Call list_collections() to "
            f"see available collections."
        )
    return candidate


def _serialize_result(result: Any, rank: int, max_source_length: int) -> dict[str, Any]:
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
    `query_table`, `aggregate_table`, `compute_portfolio_metric`, etc. If
    you omit `collection_id`, those tools fall back to the server's default
    collection, which may not be what the user asked about.
    """
    _ensure_enabled()

    collections = collection_service.get_all_collections()
    default_id = (settings.mcp_default_collection or "").strip() or None

    entries = []
    for c in collections:
        cid = c.get("id")
        if not cid:
            continue
        entries.append({
            "collection_id": cid,
            "name": c.get("name", cid),
            "description": c.get("description", "") or "",
            "document_count": c.get("document_count", 0),
            "is_default": cid == default_id,
        })

    return {
        "total_collections": len(entries),
        "default_collection_id": default_id,
        "collections": entries,
    }


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
    """
    _ensure_enabled()

    resolved_collection = _resolve_collection_id(collection_id)
    collection = collection_service.get_collection(resolved_collection)

    stats = indexer_manager.get_collection_stats(resolved_collection)

    try:
        indexer = indexer_manager.get_indexer(resolved_collection)
        documents = indexer.list_documents()
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

    payload: dict[str, Any] = {
        "collection_id": resolved_collection,
        "collection_name": collection.get("name", resolved_collection),
        "description": collection.get("description", ""),
        "total_documents": stats.get("total_documents", 0),
        "total_chunks": stats.get("total_chunks", 0),
        "total_pages": stats.get("total_pages", 0),
        "file_types": file_types,
        "search_modes": ["semantic", "keyword", "hybrid"],
    }
    if detail == "with_documents":
        payload["documents"] = doc_list
    return payload


@_asymptote_mcp.tool()
def search_collection(
    query: str,
    collection_id: str | None = None,
    mode: Literal["semantic", "keyword", "hybrid"] | None = None,
    top_k: int | None = None,
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

    When NOT to use this tool:
    For any numeric, aggregation, sum, count, average, ranking, filter, or
    date-range question about CSV / Excel data, DO NOT use search_collection.
    Chunk retrieval truncates tabular data and the excerpts you get back will
    be a subset of rows — answering numeric questions from them leads to
    hallucinated totals. Instead use:
      - `list_tables` → `get_table_rows` (small tables, read full data)
      - `list_tables` → `aggregate_table` (group-by / aggregation, no SQL)
      - `list_tables` → `query_table` (ad-hoc SQL for any domain)
      - `compute_portfolio_metric` for canned financial portfolio metrics.

    When this tool IS the right choice:
    - Narrative / prose / conceptual questions about PDFs, text, code, notes.
    - "How does X work", "where is Y described", "what does the doc say about Z".

    Convenience: the response auto-inlines the full contents of any small
    CSV/XLSX table in the collection as `structured_tables` (one JSONL
    payload per table). Any question involving numbers from those files must
    be answered DIRECTLY and EXCLUSIVELY from `structured_tables` — not from
    the `results` excerpts. Chunks for fully-inlined files are dropped from
    `results` so you aren't tempted to use them.

    The response always includes a `collection_summary` field with document,
    page, and chunk counts so you can answer meta-questions about the
    collection without a separate get_collection_info() call.
    """
    _ensure_enabled()

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
    collection_name = collection.get("name", resolved_collection) if collection else resolved_collection

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
                    jsonl = render_table_as_jsonl(
                        store, t, max_rows=resolved_inline_row_threshold
                    )
                    if not jsonl:
                        continue
                    structured_tables_payload.append({
                        "filename": t.get("filename"),
                        "sheet_name": t.get("sheet_name"),
                        "table_name": t.get("table_name"),
                        "row_count": t.get("row_count"),
                        "column_count": t.get("column_count"),
                        "rows_jsonl": jsonl,
                    })
        except Exception as e:
            logger.warning(f"MCP search_collection: structured context failed: {e}")

    indexer = indexer_manager.get_indexer(resolved_collection)
    search_result = indexer.search(
        query=normalized_query,
        top_k=resolved_top_k,
        mode=resolved_mode,
        semantic_weight=resolved_weight,
        skip_filenames=inlined_filenames or None,
    )

    # Always include collection stats inline so the MCP client can answer
    # meta-questions like "how many sources are in this collection?" without
    # having to call get_collection_info() as a separate round-trip.
    stats = indexer_manager.get_collection_stats(resolved_collection)
    collection_summary = {
        "collection_id": resolved_collection,
        "collection_name": collection_name,
        "description": collection.get("description", "") if collection else "",
        "total_documents": stats.get("total_documents", 0),
        "total_chunks": stats.get("total_chunks", 0),
        "total_pages": stats.get("total_pages", 0),
    }

    response: dict[str, Any] = {
        "query": normalized_query,
        "collection_id": resolved_collection,
        "collection_name": collection_name,
        "collection_summary": collection_summary,
        "mode": resolved_mode.value,
        "top_k": resolved_top_k,
        "total_results": len(search_result.get("results", [])),
    }

    if structured_tables_payload:
        response["structured_tables"] = structured_tables_payload
        response["structured_tables_hint"] = (
            "The collection contains CSV/XLSX tables whose FULL contents are "
            "included above in `structured_tables` as JSONL (one JSON object "
            "per row, every row present). For any numeric, aggregation, sum, "
            "count, average, filter, ranking, or date-range question about "
            "these files, answer DIRECTLY and EXCLUSIVELY from those rows — "
            "the chunk excerpts in `results` are truncated and must not be "
            "used for numeric reasoning. Chunks for fully-inlined files have "
            "already been removed from `results`."
        )

    if resolved_include_sources:
        response["results"] = [
            _serialize_result(result, rank, resolved_max_source_length)
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

    return response


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
    _ensure_enabled()

    resolved_collection = _resolve_collection_id(collection_id)
    indexer = indexer_manager.get_indexer(resolved_collection)
    metadata_store = indexer.vector_store.metadata_store

    doc_info = metadata_store.get_document_info(document_id)
    if not doc_info:
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

    return {
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
    }


def _get_structured_store(collection_id: str) -> StructuredStore:
    indexer = indexer_manager.get_indexer(collection_id)
    return indexer.vector_store.structured_store


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
      - Canned financial metrics (top_holdings, breakdown_by_sector, etc.) →
        `compute_portfolio_metric(identifier, metric)` — only useful when
        `get_table_schema` returns a `financial_roles` field.
    """
    _ensure_enabled()
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    raw_tables = store.list_tables()
    # Strip financial_roles from the listing — that detail belongs in get_table_schema.
    tables = [
        {k: v for k, v in t.items() if k != "financial_roles"}
        for t in raw_tables
    ]
    return {
        "collection_id": resolved_collection,
        "total_tables": len(tables),
        "tables": tables,
    }


@_asymptote_mcp.tool()
def get_table_schema(identifier: str, collection_id: str | None = None) -> dict[str, Any]:
    """Return the full typed schema of an ingested CSV / Excel sheet.

    Parameters:
      - identifier: a `table_name`, a `filename`, or a `document_id` —
        whichever is more convenient.
      - collection_id: Optional. If omitted, uses the server's default
        collection. Pass an explicit id (from `list_collections`) when the
        table lives in a specific client / portfolio collection.

    The response lists every column with its inferred type, sample values,
    and stats (min/max/mean/sum/p25/p50/p75 for numeric columns). Use this
    before writing SQL so you know exact column names and types.

    When semantic financial roles are detected (ticker, market_value, sector,
    pnl, cost_basis, etc.) the response includes a top-level `financial_roles`
    map of sql_name → role. A non-empty `financial_roles` means
    `compute_portfolio_metric` will work for this table; if the field is
    absent the table has no recognized financial structure and you should use
    `aggregate_table` or `query_table` instead.
    """
    _ensure_enabled()
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    schema = store.get_schema(identifier)
    if not schema:
        raise ValueError(
            f"No structured table found for '{identifier}' in collection "
            f"'{resolved_collection}'. Call list_tables() to see what's available."
        )
    return _format_schema_summary(schema)


@_asymptote_mcp.tool()
def get_table_rows(
    identifier: str,
    limit: int = 200,
    collection_id: str | None = None,
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

    Returns:
      - columns: original column headers from the source file (display names)
      - rows: list of lists, one per row, in source order
      - row_count: rows returned in this response
      - total_row_count: total rows in the underlying table
      - truncated: true if there are more rows beyond `limit`

    If the table is large and you need aggregates, use `aggregate_table` or
    write SQL via `query_table` instead of pulling every row.
    """
    _ensure_enabled()
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    schema = store.get_schema(identifier)
    if not schema:
        raise ValueError(
            f"No structured table found for '{identifier}' in collection "
            f"'{resolved_collection}'. Call list_tables() to see what's available."
        )
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

    return {
        "collection_id": resolved_collection,
        "table_name": table_name,
        "filename": schema.get("filename"),
        "sheet_name": schema.get("sheet_name"),
        "total_row_count": schema.get("row_count"),
        "columns": display_columns,
        "rows": display_rows,
        "row_count": len(display_rows),
        "truncated": result.get("truncated", False),
    }


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
    _ensure_enabled()
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    capped_rows = max(1, min(int(max_rows), 2000))
    try:
        result = store.execute_query(sql, max_rows=capped_rows)
    except SQLValidationError as e:
        raise ValueError(str(e))
    return {
        "collection_id": resolved_collection,
        "sql": sql,
        **result,
    }


PortfolioMetric = Literal[
    "row_count",
    "total_market_value",
    "total_cost_basis",
    "total_pnl",
    "top_holdings",
    "bottom_holdings",
    "largest_gains",
    "largest_losses",
    "concentration",
    "breakdown_by_sector",
    "breakdown_by_asset_class",
    "breakdown_by_region",
    "breakdown_by_currency",
    "weighted_return",
    "summary_statistics",
]


@_asymptote_mcp.tool()
def compute_portfolio_metric(
    identifier: str,
    metric: PortfolioMetric,
    limit: int = 10,
    group_by_symbol: bool = True,
    collection_id: str | None = None,
) -> dict[str, Any]:
    """Compute a canned financial portfolio metric on an ingested table.

    FINANCIAL CONTEXT ONLY — only call this tool when `get_table_schema`
    returned a non-empty `financial_roles` field (market_value, pnl,
    cost_basis, ticker, sector, etc.). For generic tabular data without
    financial roles use `aggregate_table` or `query_table` instead.

    Safer and more predictable than hand-written SQL for the 15 common
    portfolio metrics listed below.

    Parameters:
      - identifier: filename, table_name, or document_id (from `list_tables`).
      - metric: one of the supported metrics listed below.
      - limit: for ranking metrics (top_holdings, bottom_holdings, etc.).
      - group_by_symbol: when true (default), use the auto-generated
        `__by_symbol` rollup view when available so tax lots are aggregated
        into positions before computing the metric.
      - collection_id: Optional. If omitted, uses the server's default
        collection. Pass an explicit id (from `list_collections`) when the
        portfolio lives in a specific client collection.

    Supported metrics:
      - row_count
      - total_market_value
      - total_cost_basis
      - total_pnl
      - top_holdings       (uses limit)
      - bottom_holdings    (uses limit)
      - largest_gains      (uses limit)
      - largest_losses     (uses limit)
      - concentration      (share of total held by top N, uses limit)
      - breakdown_by_sector
      - breakdown_by_asset_class
      - breakdown_by_region
      - breakdown_by_currency
      - weighted_return
      - summary_statistics

    Each metric requires specific column roles; if the required role isn't
    found you'll get an error listing which roles were detected — fall back
    to `query_table` with hand-written SQL in that case.
    """
    _ensure_enabled()
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    result = compute_financial_metric(
        store,
        identifier,
        metric,
        limit=limit,
        group_by_symbol=group_by_symbol,
    )
    return {"collection_id": resolved_collection, **result}


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
    _ensure_enabled()
    if agg_fn not in _AGG_FN_SQL:
        raise ValueError(f"agg_fn must be one of: {sorted(_AGG_FN_SQL)}")

    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    schema = store.get_schema(identifier)
    if not schema:
        raise ValueError(
            f"No structured table found for '{identifier}' in collection "
            f"'{resolved_collection}'. Call list_tables() to see what's available."
        )
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

    return {
        "collection_id": resolved_collection,
        "identifier": identifier,
        "table_name": table_name,
        "agg_fn": agg_fn,
        "aggregate_col": aggregate_col,
        "group_by": group_by,
        "sql": select,
        **result,
    }


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
    _ensure_enabled()
    resolved_collection = _resolve_collection_id(collection_id)
    indexer = indexer_manager.get_indexer(resolved_collection)
    metadata_store = indexer.vector_store.metadata_store
    doc_info = metadata_store.get_document_info(document_id)
    if not doc_info:
        raise ValueError(
            f"Document '{document_id}' not found in collection "
            f"'{resolved_collection}'. Call get_collection_info() to see "
            f"available document_ids."
        )
    return {
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
    }


# ---------------------------------------------------------------------------
# MCP Resources — passive context the host LLM can load without tool calls
# ---------------------------------------------------------------------------


@_asymptote_mcp.resource("collection://{id}")
def resource_collection(id: str) -> dict[str, Any]:
    """Collection metadata and document inventory.

    URI: collection://{collection_id}

    Returns the same payload as get_collection_info(detail="with_documents")
    so the host LLM can load a complete collection overview — name,
    description, document list with chunk/page counts — without issuing a
    tool call. Useful for priming context at session start.
    """
    _ensure_enabled()
    resolved = _resolve_collection_id(id)
    collection = collection_service.get_collection(resolved)
    stats = indexer_manager.get_collection_stats(resolved)
    try:
        indexer = indexer_manager.get_indexer(resolved)
        documents = indexer.list_documents()
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

    return {
        "collection_id": resolved,
        "collection_name": collection.get("name", resolved) if collection else resolved,
        "description": collection.get("description", "") if collection else "",
        "total_documents": stats.get("total_documents", 0),
        "total_chunks": stats.get("total_chunks", 0),
        "total_pages": stats.get("total_pages", 0),
        "documents": doc_list,
    }


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
    _ensure_enabled()
    resolved = _resolve_collection_id(id)
    store = _get_structured_store(resolved)
    raw_tables = store.list_tables()
    schemas = []
    for t in raw_tables:
        full = store.get_schema(t["table_name"])
        if full:
            schemas.append(_format_schema_summary(full))
    return {
        "collection_id": resolved,
        "total_tables": len(schemas),
        "schemas": schemas,
    }


@_asymptote_mcp.resource("document://{id}")
def resource_document(id: str) -> dict[str, Any]:
    """Full document metadata record.

    URI: document://{document_id}

    Returns the complete metadata for a single document — source format,
    extraction method, embedding model, chunk parameters, schema version,
    page/chunk counts, and upload timestamp. This is the same data exposed
    by get_document_metadata() but accessible as a passive resource.

    Note: document_id must be resolvable against the server's default
    collection. For documents in other collections, use
    get_document_metadata(document_id, collection_id) instead.
    """
    _ensure_enabled()
    resolved = _resolve_collection_id(None)
    indexer = indexer_manager.get_indexer(resolved)
    metadata_store = indexer.vector_store.metadata_store
    doc_info = metadata_store.get_document_info(id)
    if not doc_info:
        raise ValueError(
            f"Document '{id}' not found in collection '{resolved}'. "
            f"Call get_collection_info() to see available document_ids."
        )
    return {
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
    }


@_asymptote_mcp.resource("table://{id}")
def resource_table(id: str) -> dict[str, Any]:
    """Table schema and sample rows in one fetch.

    URI: table://{identifier}   where identifier is a table_name, filename,
    or document_id.

    Combines get_table_schema() + the first 10 rows in a single resource
    load, giving the host LLM enough context to write queries or answer
    basic questions without any tool calls. For the full row set use the
    get_table_rows() tool.

    Note: resolves against the server's default collection.
    """
    _ensure_enabled()
    resolved = _resolve_collection_id(None)
    store = _get_structured_store(resolved)
    schema = store.get_schema(id)
    if not schema:
        raise ValueError(
            f"No structured table found for '{id}' in collection '{resolved}'. "
            f"Call list_tables() to see what's available."
        )
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

    return {
        "collection_id": resolved,
        "schema": summary,
        "sample_columns": display_columns,
        "sample_rows": display_rows,
        "sample_row_count": len(display_rows),
    }


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
        try:
            await self.app(scope, receive, send)
        finally:
            _request_mcp_profile.reset(token)


embedded_mcp_app = ToggleableMCPApp(_asymptote_mcp.streamable_http_app())


@asynccontextmanager
async def mcp_server_lifespan():
    async with _asymptote_mcp.session_manager.run():
        yield
