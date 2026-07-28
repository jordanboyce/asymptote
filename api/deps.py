"""Shared state and helpers used across API router modules."""

from fastapi import HTTPException

from config import ALL_AI_PROVIDERS, CLOUD_AI_PROVIDERS  # noqa: F401 — re-exported for routers
from services.expertise_store import ExpertiseStore
from services.indexer_manager import indexer_manager
from services.indexing import DocumentIndexer

expertise_store = ExpertiseStore()

# Set to True by main.py's lifespan once services are ready.
_initialized = False


def mark_initialized() -> None:
    global _initialized
    _initialized = True


MAX_TOOL_RESULT_CHARS = 10000


def _truncate_tool_result(payload_json: str) -> str:
    """Cap a serialized tool-result payload so a fan-out of tool calls on one
    turn cannot inflate the next request past the provider's per-minute input
    budget. When truncated, append a marker so the model knows to narrow the
    next call rather than assume the data simply ended."""
    if len(payload_json) <= MAX_TOOL_RESULT_CHARS:
        return payload_json
    return (
        payload_json[:MAX_TOOL_RESULT_CHARS]
        + '\n\n… [truncated — result exceeded size limit; re-call with narrower filters, smaller limit, or a more specific query]'
    )


def resolve_ai_key(provider: str, header_key: str | None) -> str:
    """Resolve the API key for a cloud provider request.

    A per-request X-AI-Key header always wins (user brought their own key).
    Otherwise fall back to the server-stored team key configured via
    /api/agent/config — this is what lets a hosted instance serve coworkers
    who never enter a key themselves.
    """
    if header_key:
        return header_key
    if provider in CLOUD_AI_PROVIDERS:
        try:
            from services.app_database import app_db
            return app_db.get_agent_api_key(provider) or ""
        except Exception:
            return ""
    return ""


def get_indexer(collection_id: str = "default") -> DocumentIndexer:
    """Get indexer for a collection."""
    if not _initialized:
        raise HTTPException(
            status_code=503,
            detail="Service is still initializing. Please wait a moment and try again."
        )
    return indexer_manager.get_indexer(collection_id)
