"""Embedded MCP server for Finn."""

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
    render_table_as_rows,
)
from services.financial.metrics import AVAILABLE_METRICS, compute_financial_metric
from services.financial.holdings_store import HoldingsStore
from services.tabular.sql_validation import SQLValidationError
from services.privacy.redaction_middleware import redact_mcp_response
from services.brief_generator import generate_meeting_brief as _generate_brief

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

_request_mcp_profile: ContextVar[dict[str, Any]] = ContextVar("finn_request_mcp_profile", default={})

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

_finn_mcp = FastMCP(
    "Finn",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
)


def _ensure_enabled() -> None:
    if not settings.enable_mcp:
        raise RuntimeError("Finn MCP is disabled in Settings.")


def _redact(response: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Apply PII redaction to an MCP tool response before it exits."""
    return redact_mcp_response(
        response,
        tool_name=tool_name,
        collection_id=response.get("collection_id"),
    )


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
    return cleaned.strip('-_') or 'finn'


def build_mcp_export_payload(base_url: str, profile: dict[str, Any] | None = None) -> dict[str, str]:
    base_server_id = (settings.mcp_server_id or "finn").strip() or "finn"
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


def _mcp_safe_name(collection: dict[str, Any] | None, fallback: str = "") -> str:
    """Return the PII-safe display name for MCP output."""
    if not collection:
        return fallback
    return collection.get("mcp_display_name") or collection.get("name", fallback)


def _mcp_safe_description(collection: dict[str, Any] | None, fallback: str = "") -> str:
    """Return the PII-safe description for MCP output."""
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


@_finn_mcp.tool()
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
            "name": c.get("mcp_display_name") or c.get("name", cid),
            "description": c.get("mcp_display_description") or c.get("description", "") or "",
            "document_count": c.get("document_count", 0),
            "is_default": cid == default_id,
        })

    return _redact({
        "total_collections": len(entries),
        "default_collection_id": default_id,
        "collections": entries,
    }, "list_collections")


@_finn_mcp.tool()
def generate_meeting_brief(
    collection_id: str | None = None,
    tax_loss_min: float = 500.0,
    concentration_pct: float = 10.0,
    cash_drag_min: float = 50000.0,
    top_n: int = 10,
) -> dict[str, Any]:
    """Generate a pre-meeting portfolio brief for a collection.

    The PRIMARY tool for advisor meeting preparation. Scans every CSV / Excel
    table in the collection that has detected financial roles and computes a
    ready-to-read brief across seven sections:

      household_summary     — total market value, cost basis, unrealized P&L
      accounts              — per-account breakdown (when an account column exists)
      top_positions         — top N holdings by market value
      tax_loss_candidates   — underwater positions with loss >= tax_loss_min
      concentration_alerts  — single positions >= concentration_pct of portfolio
      cash_drag_alerts      — cash / money-market positions >= cash_drag_min
      sector_allocation     — market value grouped by sector

    Call this FIRST at the start of any client meeting prep, portfolio review,
    or "what should I know about this portfolio?" question. It works across
    multi-sheet and multi-file collections — all tables are merged into one
    household view.

    Parameters:
      - collection_id: Optional. If omitted, uses the server's default
        collection. Pass an explicit id (from `list_collections`) to target
        a specific client portfolio.
      - tax_loss_min: Minimum unrealized loss (in dollars) to surface as a
        tax-loss harvesting candidate. Default 500.
      - concentration_pct: Single-position percentage threshold. Positions
        at or above this share of total market value appear in
        concentration_alerts. Default 10.0 (10 %).
      - cash_drag_min: Minimum cash / money-market balance (in dollars) to
        flag as cash drag. Default 50000.
      - top_n: Number of top positions by market value to return. Default 10.

    Returns a dict with:
      - collection_id, tables_scanned, generated_at
      - household_summary: total_market_value, total_cost_basis,
        total_unrealized_pnl, sources (per-file breakdown)
      - accounts: list of {account, market_value}
      - top_positions: list of {name, market_value, cost_basis?,
        unrealized_pnl?, ticker?, sector?}
      - tax_loss_candidates: list of {name, market_value, cost_basis,
        unrealized_loss, ticker?}
      - concentration_alerts: list of {name, market_value,
        pct_of_portfolio, threshold_pct}
      - cash_drag_alerts: list of {name, market_value, threshold}
      - sector_allocation: list of {sector, market_value,
        position_count, pct_of_portfolio?}

    When NOT to use this tool:
      - For detailed SQL analysis of a single table → use query_table.
      - For a specific canned metric → use compute_portfolio_metric.
      - For semantic / narrative questions → use search_collection.
    """
    _ensure_enabled()
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)

    thresholds = {
        'tax_loss_min': tax_loss_min,
        'concentration_pct': concentration_pct,
        'cash_drag_min': cash_drag_min,
        'top_n': top_n,
    }

    try:
        brief = _generate_brief(store, collection_id=resolved_collection, thresholds=thresholds)
    except Exception as exc:
        raise ValueError(f"Brief generation failed: {exc}") from exc

    return _redact(brief, "generate_meeting_brief")


@_finn_mcp.tool()
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
    return _redact(payload, "get_collection_info")


@_finn_mcp.tool()
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

    suggested_next: list[dict[str, str]] = []
    if _detect_numeric_intent(normalized_query):
        suggested_next.append({
            "tool": "list_tables",
            "reason": (
                "Query looks numeric/aggregation-shaped. If the answer lives "
                "in a CSV/XLSX table, list_tables → get_table_schema → "
                "aggregate_table / query_table / compute_portfolio_metric "
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

    return _redact(response, "search_collection")


_DOC_CONTEXT_MAX_CHARS_DEFAULT = 12000
_DOC_CONTEXT_MAX_CHARS_CAP = 40000


@_finn_mcp.tool()
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

    return _redact({
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


@_finn_mcp.tool()
def find_in_documents(
    pattern: str,
    literal: bool = True,
    case_sensitive: bool = False,
    collection_id: str | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
    """Exact substring search across a collection's indexed text chunks.

    Use this tool when the user cites an exact string that should appear
    verbatim in a document: a ticker symbol, a CUSIP, a client-name fragment,
    a quoted phrase, an identifier, a policy number, or any verbatim term.
    Unlike `search_collection` (which is tuned for semantic / BM25 ranking
    and drops stopwords and punctuation), this tool does a literal substring
    match — the pattern either appears in the chunk or it doesn't.

    Parameters:
      - pattern: The exact string to find. Not a regex. Leading/trailing
        whitespace is preserved.
      - literal: Reserved for future regex support; currently always literal.
      - case_sensitive: If true, the match is byte-exact. If false (default),
        matches ignore ASCII case. Use case_sensitive=true for ticker symbols
        or other identifiers where case matters.
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

    When NOT to use this tool:
      - For conceptual / paraphrased questions — use `search_collection`
        with mode="semantic" or "hybrid" instead.
      - For numeric or aggregation questions about CSV / Excel data — use
        `get_table_rows`, `aggregate_table`, or `query_table` instead;
        tabular chunks are truncated and an exact match may miss rows.
    """
    _ensure_enabled()
    if not pattern or not pattern.strip():
        raise ValueError("pattern must not be empty")
    if not literal:
        raise ValueError("regex / non-literal search is not yet supported; pass literal=True")

    resolved_collection = _resolve_collection_id(collection_id)
    capped = max(1, min(int(max_results), _FIND_MAX_RESULTS))

    indexer = indexer_manager.get_indexer(resolved_collection)
    metadata_store = indexer.vector_store.metadata_store

    matches: list[dict[str, Any]] = []
    scanned = 0
    for chunk in metadata_store.get_all_chunks_ordered():
        scanned += 1
        text = chunk.get("text") or ""
        hit = _find_literal_excerpt(text, pattern, case_insensitive=not case_sensitive)
        if not hit:
            continue
        offset, excerpt = hit
        matches.append({
            "filename": chunk.get("filename"),
            "document_id": chunk.get("document_id"),
            "chunk_id": chunk.get("chunk_id"),
            "page_number": chunk.get("page_number"),
            "offset": offset,
            "excerpt": excerpt,
        })
        if len(matches) >= capped:
            break

    return _redact({
        "collection_id": resolved_collection,
        "pattern": pattern,
        "case_sensitive": bool(case_sensitive),
        "total_matches": len(matches),
        "chunks_scanned": scanned,
        "truncated": len(matches) >= capped,
        "matches": matches,
    }, "find_in_documents")


def _get_structured_store(collection_id: str) -> HoldingsStore:
    indexer = indexer_manager.get_indexer(collection_id)
    return indexer.vector_store.holdings_store


def _format_schema_summary(schema: dict[str, Any]) -> dict[str, Any]:
    """Trim a full schema payload down to what an LLM needs to write a query."""
    columns = []
    detected_roles: dict[str, str] = {}
    role_sources: dict[str, str] = {}
    for c in schema["columns"]:
        role = c.get("role")
        role_source = c.get("role_source")
        if role:
            detected_roles[c["sql_name"]] = role
            if role_source:
                role_sources[c["sql_name"]] = role_source
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
            "role_source": role_source,
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
    if role_sources:
        result["financial_role_sources"] = role_sources
    return result


@_finn_mcp.tool()
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
    return _redact({
        "collection_id": resolved_collection,
        "total_tables": len(tables),
        "tables": tables,
    }, "list_tables")


@_finn_mcp.tool()
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

    When semantic financial roles are detected (ticker, market_value, sector,
    pnl, cost_basis, etc.) the response includes a top-level `financial_roles`
    map of sql_name → role. A non-empty `financial_roles` means
    `compute_portfolio_metric` will work for this table; if the field is
    absent the table has no recognized financial structure and you should use
    `aggregate_table` or `query_table` instead.

    Each role also carries a `role_source` — `"profile"` (matched a known
    vendor like Pershing/Schwab), `"heuristic"` (regex match on the column
    name), or `"llm"` (inferred by an LLM when too many columns were
    unmapped). A parallel top-level `financial_role_sources` map mirrors
    this. Treat `profile` as authoritative, `heuristic` as reliable for
    common cases, and `llm` as best-effort — when an LLM-inferred role
    drives a critical aggregation, prefer `aggregate_table` or `query_table`
    on the raw column instead.
    """
    _ensure_enabled()
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    schema = store.get_schema(identifier, identifier_type=identifier_type)
    if not schema:
        raise ValueError(
            f"No structured table found for '{identifier}' in collection "
            f"'{resolved_collection}'. Call list_tables() to see what's available."
        )
    return _redact(_format_schema_summary(schema), "get_table_schema")


@_finn_mcp.tool()
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
    _ensure_enabled()
    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    schema = store.get_schema(identifier, identifier_type=identifier_type)
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

    return _redact({
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


@_finn_mcp.tool()
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
    warnings: list[str] = []
    effective_sql = sql
    try:
        result = store.execute_query(effective_sql, max_rows=capped_rows)
    except SQLValidationError as e:
        rewrite = _maybe_rewrite_unknown_table(sql, str(e), store)
        if rewrite is None:
            raise ValueError(str(e))
        effective_sql, missing, replacement = rewrite
        warnings.append(
            f"Auto-rewrote FROM clause: '{missing}' is the source filename, "
            f"not a SQL table. Used '{replacement}' instead. Issue future "
            f"queries against the quoted table_name shown in the schema block."
        )
        try:
            result = store.execute_query(effective_sql, max_rows=capped_rows)
        except SQLValidationError as e2:
            raise ValueError(str(e2))
    payload = {
        "collection_id": resolved_collection,
        "sql": effective_sql,
        **result,
    }
    if effective_sql != sql:
        payload["original_sql"] = sql
    if warnings:
        payload["warnings"] = warnings
    return _redact(payload, "query_table")


def _maybe_rewrite_unknown_table(
    sql: str,
    error_message: str,
    store: Any,
) -> tuple[str, str, str] | None:
    """If the SQL error is a 'no such table' caused by an agent passing a
    filename / document_id where a table_name belongs, return the rewritten
    SQL plus (missing_identifier, replacement_table_name). Otherwise None.

    Conservative on purpose: we only rewrite when the missing identifier
    appears verbatim in the SQL (quoted or unquoted) and maps unambiguously
    to one ingested table via filename or document_id. Ambiguous matches
    fall through and the original error surfaces — never silently wrong.
    """
    import re

    match = re.search(r"no such table:\s*(.+?)(?:\s*$|\n)", error_message, flags=re.IGNORECASE)
    if not match:
        return None
    missing = match.group(1).strip().strip('"').strip("'")
    if not missing:
        return None

    try:
        tables = store.list_tables()
    except Exception:
        return None

    candidates = [
        t for t in tables
        if missing == t.get("filename") or missing == t.get("document_id")
    ]
    if len(candidates) != 1:
        return None
    replacement = candidates[0].get("table_name")
    if not replacement:
        return None

    pattern = re.compile(
        r'(?:"' + re.escape(missing) + r'"|\b' + re.escape(missing) + r'\b)'
    )
    new_sql, count = pattern.subn(f'"{replacement}"', sql)
    if count == 0:
        return None
    return new_sql, missing, replacement


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


@_finn_mcp.tool()
def compute_portfolio_metric(
    identifier: str,
    metric: PortfolioMetric,
    limit: int = 10,
    group_by_symbol: bool = True,
    collection_id: str | None = None,
    identifier_type: Literal["table_name", "filename", "document_id"] | None = None,
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
      - identifier_type: Optional. Restrict the lookup to exactly one of
        "table_name", "filename", or "document_id". When omitted (default),
        all three are searched.

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

    Exception: `breakdown_by_sector` and `breakdown_by_asset_class` will
    auto-enrich via `get_security_classification` when the source file lacks
    that column but has a `ticker` role. The result includes `enriched_via:
    "classification"` and a per-group `Unclassified` bucket for symbols the
    feed couldn't resolve.
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
        identifier_type=identifier_type,
    )
    return _redact({"collection_id": resolved_collection, **result}, "compute_portfolio_metric")


_AGG_FN_SQL = {
    "sum": "SUM",
    "mean": "AVG",
    "count": "COUNT",
    "min": "MIN",
    "max": "MAX",
}


@_finn_mcp.tool()
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
    _ensure_enabled()
    if agg_fn not in _AGG_FN_SQL:
        raise ValueError(f"agg_fn must be one of: {sorted(_AGG_FN_SQL)}")

    resolved_collection = _resolve_collection_id(collection_id)
    store = _get_structured_store(resolved_collection)
    schema = store.get_schema(identifier, identifier_type=identifier_type)
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

    return _redact({
        "collection_id": resolved_collection,
        "identifier": identifier,
        "table_name": table_name,
        "agg_fn": agg_fn,
        "aggregate_col": aggregate_col,
        "group_by": group_by,
        "sql": select,
        **result,
    }, "aggregate_table")


@_finn_mcp.tool()
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
    return _redact({
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
# PII Redaction — audit and configuration tools
# ---------------------------------------------------------------------------


@_finn_mcp.tool()
def get_recent_redactions(
    session_id: str | None = None,
    collection_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return recent PII redaction events from the audit log.

    Use this to self-verify what PII was removed before proceeding, or to
    answer user questions about what was redacted.

    Parameters:
      - session_id: Filter to a specific session. If omitted, returns
        redactions across all sessions.
      - collection_id: Filter to a specific collection. If omitted,
        returns redactions across all collections.
      - limit: Max entries to return (default 50, cap 500).

    Returns a list of redaction events with entity_type, replacement text,
    which tool produced the output, and the Presidio confidence score.
    The original PII text is NOT included in the response (it stays in
    the local audit log only).
    """
    _ensure_enabled()

    from services.privacy.redaction_log import redaction_log

    capped = max(1, min(int(limit), 500))
    events = redaction_log.get_recent(
        session_id=session_id,
        collection_id=collection_id,
        limit=capped,
    )

    # Strip original_text from the response — it must not leave the machine
    safe_events = [
        {
            "id": e.get("id"),
            "timestamp": e.get("timestamp"),
            "session_id": e.get("session_id"),
            "collection_id": e.get("collection_id"),
            "tool_name": e.get("tool_name"),
            "document_id": e.get("document_id"),
            "entity_type": e.get("entity_type"),
            "replacement": e.get("replacement"),
            "score": e.get("score"),
        }
        for e in events
    ]

    # Also include a summary if session_id was provided
    summary = None
    if session_id:
        summary = redaction_log.get_session_summary(session_id)

    result: dict[str, Any] = {
        "total_returned": len(safe_events),
        "redactions": safe_events,
    }
    if summary:
        result["session_summary"] = summary

    return result


_PRICE_HISTORY_PERIOD_DAYS = {
    "1d": 1,
    "5d": 5,
    "1mo": 30,
    "3mo": 90,
    "6mo": 180,
    "1y": 365,
    "2y": 730,
    "5y": 1825,
    "10y": 3650,
    "max": 18250,
}


def _period_to_start(period: str) -> str | None:
    """Translate a yfinance-style relative period ("1y", "5d", "ytd", "max")
    into an explicit ISO start date. Returns None for unrecognized values so
    the underlying default lookback kicks in."""
    from datetime import date, timedelta

    p = (period or "").strip().lower()
    if not p:
        return None
    if p == "ytd":
        return f"{date.today().year}-01-01"
    days = _PRICE_HISTORY_PERIOD_DAYS.get(p)
    if days is None:
        return None
    return (date.today() - timedelta(days=days)).isoformat()


@_finn_mcp.tool()
def get_price_history(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    interval: str = "1d",
    period: str | None = None,
) -> dict[str, Any]:
    """Return historical OHLCV price data for a security.

    Use this for any question that requires time-series price data beyond
    the snapshot in a portfolio file: trend / momentum / drawdown
    analysis, "what's in a downtrend?", "how has X performed this year?",
    "show me the chart", peak-to-trough moves, return over period.

    Parameters:
      - symbol: Ticker symbol (e.g. "AAPL", "MSFT", "^GSPC"). Required.
      - period: Convenience window ("1d", "5d", "1mo", "3mo", "6mo", "1y",
        "2y", "5y", "10y", "ytd", "max"). Translated to an explicit start
        date ending today. Ignored if `start` is given.
      - start: ISO date (YYYY-MM-DD) or omit to derive from `period`, or
        fall back to a sensible default lookback based on interval (7
        days for intraday, 1 year for daily, longer for weekly/monthly).
      - end: ISO date (YYYY-MM-DD) or omit for today.
      - interval: Bar size. One of: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h,
        1d, 5d, 1wk, 1mo, 3mo. Default "1d". Note: yfinance limits
        intraday intervals to recent windows (e.g. 1m is last 7 days).

    Returns: { symbol, interval, start, end, currency, points,
    point_count, source, cached, cached_at }. Each point has
    { date, open, high, low, close, volume }.

    On lookup failure returns { error, message, symbol } with error
    codes: missing_symbol, invalid_interval, symbol_not_found_or_no_data,
    yfinance_fetch_failed. Agents should fall back to reporting the
    error to the user rather than inventing values.

    Results are cached locally on disk; repeat calls within the TTL
    (30 min intraday, 12 h daily, 24 h weekly+) return instantly.
    """
    _ensure_enabled()

    from services.market_data.price_history import get_price_history as _fetch

    if period and not start:
        start = _period_to_start(period)

    response = _fetch(symbol=symbol, start=start, end=end, interval=interval)
    return _redact(response, tool_name="get_price_history")


@_finn_mcp.tool()
def get_security_classification(symbol: str) -> dict[str, Any]:
    """Return sector, industry, market cap bucket, asset class for a security.

    Use this for questions about portfolio composition that the source
    file doesn't directly answer: sector concentration, growth vs value,
    asset-class breakdown, market-cap exposure, geographic exposure,
    ETF category. Also use it to classify holdings when the uploaded
    file has no sector/industry columns.

    Parameters:
      - symbol: Ticker (e.g. "AAPL", "SPY", "VTI"). Required.

    Returns: symbol, name, asset_class (equity/etf/mutual_fund/…),
    sector, industry, country, currency, exchange, market_cap,
    market_cap_bucket (mega/large/mid/small/micro/nano),
    dividend_yield, beta, isin, category (ETFs),
    fund_family (ETFs), source, cached, cached_at.

    On lookup failure returns { error, message, symbol } with codes:
    missing_symbol, symbol_not_found, yfinance_fetch_failed. Classifications
    are cached locally for 7 days — sector assignments change rarely.
    """
    _ensure_enabled()

    from services.market_data.classification import get_security_classification as _fetch

    response = _fetch(symbol=symbol)
    return _redact(response, tool_name="get_security_classification")


@_finn_mcp.tool()
def get_company_profile(symbol: str) -> dict[str, Any]:
    """Return company-level metadata for a ticker: current officers, CEO,
    business summary, sector, industry, website, headcount, market cap.

    Use this for "who is the CEO of X?", "what does X do?", "where are
    they based?", "how many employees?" — anything that needs the current
    state of the company rather than price data or a prose document.

    Parameters:
      - symbol: Ticker (e.g. "AAPL", "GLW"). Required.

    Returns: symbol, name, quote_type, sector, industry, country, website,
    ir_website, employees, business_summary, ceo ({name, title, age,
    year_born, total_pay}), officers (full leadership list), market_cap,
    source, cached, cached_at.

    Profiles cache for 24 h — leadership / sector assignments change
    slowly. For fresh CEO-transition announcements combine with
    get_company_news.
    """
    _ensure_enabled()

    from services.market_data.company import get_company_profile as _fetch

    response = _fetch(symbol=symbol)
    return _redact(response, tool_name="get_company_profile")


@_finn_mcp.tool()
def get_company_news(symbol: str, limit: int = 10) -> dict[str, Any]:
    """Return recent news headlines for a ticker from yfinance.

    Use this for "any recent news on X?", "did X announce anything?",
    "recent CEO changes at X?", "earnings news", M&A coverage, guidance
    updates — surfaces press releases and news articles indexed by Yahoo
    Finance.

    Parameters:
      - symbol: Ticker. Required.
      - limit: Max headlines to return (1-30, default 10).

    Returns: symbol, count, news (list of {title, summary, publisher,
    published_at, url, content_type}), source, cached, cached_at.

    Cached for 30 minutes so fresh headlines surface without hammering
    the upstream feed.
    """
    _ensure_enabled()

    from services.market_data.company import get_company_news as _fetch

    response = _fetch(symbol=symbol, limit=limit)
    return _redact(response, tool_name="get_company_news")


@_finn_mcp.tool()
def get_corporate_events(
    symbol: str,
    since: str | None = None,
    types: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return recent corporate events for a security: SEC filings, dividends,
    splits, merger filings, and upcoming earnings.

    Use this for "any recent 8-Ks?", "CEO change?", "M&A exposure?",
    "dividend cut?", "upcoming earnings date?" — anything that requires
    scanning the filing / event timeline rather than prose news.

    Parameters:
      - symbol: Ticker. Required. US-listed issuers only for SEC filings;
        non-US symbols still return dividend/split/earnings data from
        yfinance.
      - since: ISO date (YYYY-MM-DD). Defaults to 1 year ago.
      - types: Subset of ["filing", "8-K", "10-K", "10-Q", "dividend",
        "split", "merger", "earnings"]. Default covers everything. Use
        "filing" for the default filing set, or name specific forms to
        narrow.
      - limit: Max filings to return (default 50).

    Returns: symbol, since, types, filings (form, filed, report_date,
    accession, description, url), dividends ({date, amount}), splits
    ({date, ratio}), earnings ({date, type}), counts, source, cached,
    cached_at. May include `warnings` when SEC lookup fails (e.g. non-US
    ticker) — dividends/splits/earnings still return.

    Cached for 12 hours. SEC CIK lookups cache indefinitely.
    """
    _ensure_enabled()

    from services.market_data.corporate_events import get_corporate_events as _fetch

    response = _fetch(symbol=symbol, since=since, types=types, limit=limit)
    return _redact(response, tool_name="get_corporate_events")


@_finn_mcp.tool()
def enrich_holdings(
    collection_id: str | None = None,
    identifier: str | None = None,
    include: list[str] | None = None,
    max_symbols: int = 100,
) -> dict[str, Any]:
    """Walk the holdings table for a collection and enrich every distinct
    ticker with classification, company profile, (optionally) corporate
    events and 1-year price history.

    This is the composite tool that turns a bare portfolio file into an
    answerable data structure: sector concentration, growth vs value,
    CEO changes, recent M&A, price action — all with one call. Every
    underlying feed caches aggressively; re-runs within the TTL are
    near-instant.

    Parameters:
      - collection_id: Collection to enrich. Defaults to the server's
        default collection.
      - identifier: Optional table_name / filename / document_id. If
        omitted, auto-picks the first table with a detected `ticker`
        role.
      - include: Subset of ["classification", "profile", "events",
        "price_1y"]. Default: ["classification", "profile"]. Add
        "events" for filings/dividends/splits and "price_1y" for the
        daily OHLCV series — each adds an extra round of calls per
        symbol.
      - max_symbols: Cap on distinct symbols to enrich (default 100,
        max 500).

    Returns: collection_id, table_name, include, symbol_count, truncated,
    holdings (list of {symbol, classification?, profile?, events?,
    price_1y?}), source.

    On lookup failure returns { error, message, ... } with codes:
    no_holdings_table, no_symbols, invalid_include.
    """
    _ensure_enabled()
    resolved_collection = _resolve_collection_id(collection_id)

    from services.market_data.enrich import enrich_holdings as _enrich

    response = _enrich(
        collection_id=resolved_collection,
        identifier=identifier,
        include=include,
        max_symbols=max_symbols,
    )
    return _redact(response, tool_name="enrich_holdings")


@_finn_mcp.tool()
def get_redaction_config(
    collection_id: str | None = None,
) -> dict[str, Any]:
    """Return the active PII redaction profile for a collection.

    Shows which entity types are being redacted, the redaction style
    (e.g. [ENTITY_TYPE], [REDACTED], partial_mask), the confidence
    threshold, and any allow-listed strings.

    Parameters:
      - collection_id: Optional. If omitted, returns the global default
        profile.
    """
    _ensure_enabled()

    from services.privacy.redaction_config import get_redaction_profile, _profile_to_dict

    profile = get_redaction_profile(collection_id)
    payload = _profile_to_dict(profile)
    payload["collection_id"] = collection_id
    payload["enabled"] = getattr(settings, "enable_pii_redaction", False)
    return payload


@_finn_mcp.tool()
def redaction_preview(
    text: str,
    collection_id: str | None = None,
) -> dict[str, Any]:
    """Dry-run PII detection on a string without persisting anything.

    Returns what entities would be redacted and their replacements,
    without writing to the audit log or modifying the input. Useful
    for agents to self-verify before sending context upstream to
    an LLM provider.

    Parameters:
      - text: The string to analyze.
      - collection_id: Collection whose redaction profile to apply.
        Omit to use the global default profile.

    Returns: { redacted_text, entity_count, had_pii, entities: [
      { entity_type, start, end, score, replacement } ] }
    """
    _ensure_enabled()

    from services.privacy.redaction_engine import redaction_engine

    result = redaction_engine.redact_text(text, collection_id)
    return {
        "redacted_text": result.redacted_text,
        "entity_count": len(result.details),
        "had_pii": result.had_pii,
        "entities": [
            {
                "entity_type": d.entity_type,
                "start": d.start,
                "end": d.end,
                "score": d.score,
                "replacement": d.replacement,
            }
            for d in result.details
        ],
    }


@_finn_mcp.tool()
def set_redaction_policy(
    collection_id: str | None = None,
    redaction_style: str | None = None,
    allow_list: list[str] | None = None,
    minimum_score_threshold: float | None = None,
    entity_types_enabled: list[str] | None = None,
    strict_mode: bool | None = None,
) -> dict[str, Any]:
    """Update the PII redaction policy for a collection.

    Parameters:
      - collection_id: Collection to update. Omit for global default.
      - redaction_style: "entity_type" | "redacted" |
        "consistent_pseudonym" | "partial_mask" | "synthetic_placeholder"
      - allow_list: Strings that should never be redacted
        (fund names, tickers, firm names).
      - minimum_score_threshold: Presidio confidence floor (0.0-1.0).
        Lower = more aggressive.
      - entity_types_enabled: List of entity types to enforce.
        Omit to enable all defaults.
      - strict_mode: When true, redact at minimum_score_threshold;
        when false, only high-confidence matches.

    Returns the updated profile dict plus `collection_id` and `enabled`.
    """
    _ensure_enabled()

    from services.privacy.redaction_config import (
        save_redaction_profile,
        _profile_to_dict,
        clear_profile_cache,
    )

    updates: dict[str, Any] = {}
    if redaction_style is not None:
        updates["redaction_style"] = redaction_style
    if allow_list is not None:
        updates["allow_list"] = allow_list
    if minimum_score_threshold is not None:
        updates["minimum_score_threshold"] = minimum_score_threshold
    if entity_types_enabled is not None:
        updates["entity_types_enabled"] = entity_types_enabled
    if strict_mode is not None:
        updates["strict_mode"] = strict_mode

    profile = save_redaction_profile(collection_id, updates)
    clear_profile_cache()

    payload = _profile_to_dict(profile)
    payload["collection_id"] = collection_id
    payload["enabled"] = getattr(settings, "enable_pii_redaction", False)
    return payload


# ---------------------------------------------------------------------------
# MCP Resources — passive context the host LLM can load without tool calls
# ---------------------------------------------------------------------------


@_finn_mcp.resource("collection://{id}")
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

    return _redact({
        "collection_id": resolved,
        "collection_name": _mcp_safe_name(collection, resolved),
        "description": _mcp_safe_description(collection, ""),
        "total_documents": stats.get("total_documents", 0),
        "total_chunks": stats.get("total_chunks", 0),
        "total_pages": stats.get("total_pages", 0),
        "documents": doc_list,
    }, "resource_collection")


@_finn_mcp.resource("collection://{id}/schema")
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
    return _redact({
        "collection_id": resolved,
        "total_tables": len(schemas),
        "schemas": schemas,
    }, "resource_collection_schema")


@_finn_mcp.resource("document://{id}")
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
    return _redact({
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


@_finn_mcp.resource("table://{id}")
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

    return _redact({
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
                {"detail": "Finn MCP is disabled in Settings."},
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


embedded_mcp_app = ToggleableMCPApp(_finn_mcp.streamable_http_app())


@asynccontextmanager
async def mcp_server_lifespan():
    async with _finn_mcp.session_manager.run():
        yield
