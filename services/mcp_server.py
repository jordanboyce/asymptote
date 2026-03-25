"""Embedded MCP server for Asymptote."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import parse_qs, urlencode

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from config import settings
from models.schemas import SearchMode
from services.app_database import app_db
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager

MCP_PROFILE_FIELDS = (
    "collection_id",
    "top_k",
    "mode",
    "semantic_weight",
    "include_sources",
    "max_source_length",
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
    return {
        "server_id": server_id,
        "server_url": server_url,
        "claude_json": claude_json,
        "codex_toml": codex_toml,
        "claude_filename": f".mcp-{collection_id}.json",
        "codex_filename": f"config-{collection_id}.toml",
        "profile": normalized_profile,
    }


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
def search_collection(query: str) -> dict[str, Any]:
    """Search the project-specific Asymptote collection configured for this MCP endpoint.

    Use this tool when you need to look up documentation, code references, or any
    indexed content for the current project. The collection is fixed by the MCP URL
    profile — no collection selection needed. Just provide your search query.
    """
    _ensure_enabled()

    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty")

    request_profile = get_request_mcp_profile()
    resolved_collection = request_profile.get("collection_id") or settings.mcp_default_collection
    resolved_top_k = max(1, min(int(request_profile.get("top_k") or settings.mcp_top_k), 20))
    resolved_mode = SearchMode(request_profile.get("mode") or settings.mcp_mode)
    resolved_weight = max(0.0, min(float(request_profile.get("semantic_weight", settings.mcp_semantic_weight)), 1.0))
    resolved_include_sources = request_profile.get("include_sources", settings.mcp_include_sources)
    resolved_max_source_length = max(100, min(int(request_profile.get("max_source_length") or settings.mcp_max_source_length), 2000))

    collection = collection_service.get_collection(resolved_collection)
    collection_name = collection.get("name", resolved_collection) if collection else resolved_collection

    indexer = indexer_manager.get_indexer(resolved_collection)
    search_result = indexer.search(
        query=normalized_query,
        top_k=resolved_top_k,
        mode=resolved_mode,
        semantic_weight=resolved_weight,
    )

    response: dict[str, Any] = {
        "query": normalized_query,
        "collection_id": resolved_collection,
        "collection_name": collection_name,
        "mode": resolved_mode.value,
        "top_k": resolved_top_k,
        "total_results": len(search_result.get("results", [])),
    }

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
