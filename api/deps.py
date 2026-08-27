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


def require_collection_access(
    collection_id: str, user_id: str | None, write: bool = False
) -> str:
    """Raise unless the user can access the collection; return the permission.

    404 both for a missing collection and for one the user cannot see, so
    collection ids can't be probed. 403 when a read-only share tries to write.
    No-ops (returns 'owner') when private collections mode is off.
    """
    from services.sharing_service import sharing_service

    access = sharing_service.check_collection_access(collection_id, user_id)
    if access is None:
        raise HTTPException(
            status_code=404, detail=f"Collection '{collection_id}' not found"
        )
    if write and access == "read":
        raise HTTPException(
            status_code=403,
            detail="This collection is shared with you read-only",
        )
    return access


def get_indexer(collection_id: str = "default") -> DocumentIndexer:
    """Get indexer for a collection.

    Every read path in the API — search, chat retrieval, document listing,
    PDF/chunk serving — comes through here, so this is where private-
    collections read access is enforced. Write endpoints additionally call
    require_collection_access(write=True) themselves.
    """
    if not _initialized:
        raise HTTPException(
            status_code=503,
            detail="Service is still initializing. Please wait a moment and try again."
        )
    from config import settings
    if settings.private_collections:
        from middleware.user_context import get_request_user
        require_collection_access(collection_id, get_request_user())
    return indexer_manager.get_indexer(collection_id)
