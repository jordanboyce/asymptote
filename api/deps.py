"""Shared FastAPI dependencies for both `main.py` and the `api/` routers.

These lived in `main.py`. They moved here so router modules can use them
without importing `main` (which imports the routers — a cycle). `main.py`
re-imports them from this module, so `monkeypatch.setattr(main, ...)` in
existing tests keeps working: those routes still resolve the name through
`main`'s namespace at call time.

New tests that exercise `api/` routers should patch `api.deps` instead —
or, better, patch the router module's own namespace.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from services.indexer_manager import indexer_manager
from services.sharing_service import sharing_service

# Permission levels returned by sharing_service.check_collection_access.
# "owner" > "readwrite" > "read".
_WRITE_LEVELS = ("owner", "readwrite")

# Set once by the lifespan handler in main.py. Kept in a dict rather than a
# module global so `mark_initialized()` mutates state every importer sees.
_state: dict[str, bool] = {"initialized": False}


def mark_initialized(value: bool = True) -> None:
    """Flip the readiness flag. Called from the app lifespan."""
    _state["initialized"] = value


def is_initialized() -> bool:
    return _state["initialized"]


def get_indexer(collection_id: str = "default"):
    """Get the indexer for a collection, or 503 while services are starting."""
    if not _state["initialized"]:
        raise HTTPException(
            status_code=503,
            detail="Service is still initializing. Please wait a moment and try again."
        )
    return indexer_manager.get_indexer(collection_id)


def require_collection_access(collection_id: str, user_id: str, required: str = "read") -> str:
    """Raise 403/404 unless ``user_id`` has at least ``required`` access on ``collection_id``.

    In single-user mode (``enable_multi_user=False``) the sharing service always
    returns "owner", so this is effectively a no-op. In multi-user mode it
    enforces that the user owns the collection or has been granted a share at
    the required permission level.

    Returns the granted permission level so callers can branch on it if
    needed (e.g. read-only views vs. owner-only settings).
    """
    access = sharing_service.check_collection_access(collection_id, user_id)
    if not access:
        # 404 rather than 403 so we don't leak the existence of collection
        # IDs owned by other users.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{collection_id}' not found",
        )
    if required in _WRITE_LEVELS and access not in _WRITE_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Write access required",
        )
    return access
