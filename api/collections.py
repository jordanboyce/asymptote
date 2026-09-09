"""Collection CRUD and re-indexing endpoints."""

import logging

from fastapi import Depends, HTTPException, status, Request

from config import settings
from services.config_manager import config_manager
from services.reindex_service import reindex_service
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from middleware.user_context import get_current_user_id

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/api/reindex",
    summary="Start re-indexing all documents",
    tags=["admin"],
)
async def start_reindex():
    """
    Start a background re-indexing job for all documents.

    Uses current configuration from database or settings.
    Returns immediately with job ID for progress tracking.

    Returns:
        - job_id: ID for tracking re-indexing progress
        - status: Initial job status
    """
    try:
        # Get current config (from database with .env fallback)
        current_config = config_manager.get_current_config()

        # The job builds its own embedding service through the shared
        # factory and reads the dimension from it, so no model is loaded
        # here (a local model instantiated on the request thread doubled
        # memory and ignored the configured provider).
        job_id = await reindex_service.start_reindex(
            documents_dir=settings.data_dir / "documents",
            embedding_model=current_config["embedding_model"],
            chunk_size=current_config["chunk_size"],
            chunk_overlap=current_config["chunk_overlap"],
        )

        return {
            "job_id": job_id,
            "status": "started",
            "message": "Re-indexing job started in background"
        }

    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to start re-indexing: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start re-indexing: {str(e)}"
        )


@router.post(
    "/api/collections/{collection_id}/reindex",
    summary="Start re-indexing a collection",
    tags=["collections"],
)
async def start_collection_reindex(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """
    Start a background re-indexing job for a specific collection.

    Re-indexes all documents in the collection using the collection's
    current settings (chunk_size, chunk_overlap, embedding_model).

    This is useful after changing collection settings to apply
    the new settings to existing documents.

    Args:
        collection_id: Collection to reindex

    Returns:
        - job_id: ID for tracking re-indexing progress
        - status: Initial job status
        - collection_id: Collection being reindexed
    """
    try:
        from api.deps import require_collection_access
        require_collection_access(collection_id, user_id, write=True)

        # Get collection settings
        collection = collection_service.get_collection(collection_id)
        if not collection:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Collection '{collection_id}' not found"
            )

        # Get collection paths
        documents_dir = indexer_manager.get_documents_path(collection_id)
        indexes_dir = indexer_manager.get_indexes_path(collection_id)

        # Start re-indexing (the job resolves provider + dimension itself)
        job_id = await reindex_service.start_collection_reindex(
            collection_id=collection_id,
            documents_dir=documents_dir,
            indexes_dir=indexes_dir,
            embedding_model=collection["embedding_model"],
            chunk_size=collection["chunk_size"],
            chunk_overlap=collection["chunk_overlap"],
        )

        return {
            "job_id": job_id,
            "status": "started",
            "collection_id": collection_id,
            "message": f"Re-indexing job started for collection '{collection_id}'"
        }

    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start re-indexing for collection {collection_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start re-indexing: {str(e)}"
        )


@router.get(
    "/api/reindex/status",
    summary="Get re-indexing job status",
    tags=["admin"],
)
async def get_reindex_status(job_id: int = None):
    """
    Get status of a re-indexing job.

    Args:
        job_id: Optional job ID. If not provided, returns latest job.

    Returns:
        Job details including:
        - id: Job ID
        - status: pending, running, completed, or failed
        - total_documents: Total documents to process
        - processed_documents: Documents processed so far
        - current_file: Currently processing file
        - started_at: Job start timestamp
        - completed_at: Job completion timestamp (if finished)
        - error: Error message (if failed)
    """
    from services.app_database import app_db

    if job_id:
        job = reindex_service.get_job_status(job_id)
    else:
        # Get latest job (including completed ones), not just currently active
        job = app_db.get_latest_reindex_job()

    if not job:
        return None

    # Calculate progress percentage
    if job["total_documents"] > 0:
        progress = (job["processed_documents"] / job["total_documents"]) * 100
    else:
        progress = 0

    return {
        **job,
        "progress_percent": round(progress, 1)
    }


# AI Preferences endpoints
# Collection endpoints
@router.get(
    "/api/collections",
    summary="List all collections",
    tags=["collections"],
)
async def list_collections(request: Request, user_id: str = Depends(get_current_user_id)):
    """Get all document collections visible to the current user."""
    collections = collection_service.get_all_collections(user_id=user_id)
    return {
        "collections": collections,
        "user_id": user_id,
        "private_collections": settings.private_collections,
    }


@router.post(
    "/api/collections",
    summary="Create a new collection",
    tags=["collections"],
    status_code=status.HTTP_201_CREATED,
)
async def create_collection(collection_data: dict, user_id: str = Depends(get_current_user_id)):
    """
    Create a new document collection owned by the current user.

    Body:
        name: Collection name (required)
        description: Collection description
        color: Hex color for UI (default: #3b82f6)
        chunk_size: Text chunk size (default: 500)
        chunk_overlap: Chunk overlap (default: 50)
        embedding_model: Embedding model to use
        visibility: "private" (default) or "team" — private-collections mode
            only. Team collections belong to everyone; private ones belong to
            the caller until shared.
    """
    name = collection_data.get("name")
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Collection name is required"
        )

    collection = collection_service.create_collection(
        name=name,
        description=collection_data.get("description", ""),
        color=collection_data.get("color", "#3b82f6"),
        chunk_size=collection_data.get("chunk_size", 500),
        chunk_overlap=collection_data.get("chunk_overlap", 50),
        embedding_model=collection_data.get("embedding_model", "all-MiniLM-L6-v2"),
        # The creator chooses team (owner "default" — everyone's) or private
        # (theirs until shared). Anonymous password-auth callers have no
        # identity to own anything, so their collections are always team.
        owner_id=(
            settings.default_user_id
            if collection_data.get("visibility") == "team" or not user_id
            else user_id
        ),
    )

    return collection


@router.get(
    "/api/collections/{collection_id}",
    summary="Get collection details",
    tags=["collections"],
)
async def get_collection(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """Get details for a specific collection."""
    from api.deps import require_collection_access
    access = require_collection_access(collection_id, user_id)
    collection = collection_service.get_collection(collection_id)
    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{collection_id}' not found"
        )
    collection['permission'] = access
    return collection


@router.get(
    "/api/collections/{collection_id}/stats",
    summary="Get collection stats (SQL-backed counts)",
    tags=["collections"],
)
async def get_collection_stats(collection_id: str):
    """
    Cheap canonical stats for a collection.

    Counts come from SQL aggregates over the collection's metadata store
    (COUNT/SUM on the documents table, COUNT on chunks), so the response
    stays O(1)-ish regardless of collection size. This is what the frontend
    header/footer and chat gate should poll — never the unpaginated
    /documents list.

    Returns:
        - collection_id
        - total_documents
        - total_pages
        - total_chunks
    """
    from api.deps import get_indexer

    try:
        get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    return indexer_manager.get_collection_stats(collection_id)


@router.put(
    "/api/collections/{collection_id}",
    summary="Update collection settings",
    tags=["collections"],
)
async def update_collection(collection_id: str, updates: dict, user_id: str = Depends(get_current_user_id)):
    """
    Update collection settings. Requires owner or readwrite access.

    Body:
        name: New name
        description: New description
        color: New hex color
        chunk_size: New chunk size
        chunk_overlap: New chunk overlap
        embedding_model: New embedding model

    Note: Changing chunk_size, chunk_overlap, or embedding_model
    requires re-indexing the collection's documents.
    """
    from api.deps import require_collection_access
    require_collection_access(collection_id, user_id, write=True)
    collection = collection_service.update_collection(
        collection_id=collection_id,
        name=updates.get("name"),
        description=updates.get("description"),
        color=updates.get("color"),
        chunk_size=updates.get("chunk_size"),
        chunk_overlap=updates.get("chunk_overlap"),
        embedding_model=updates.get("embedding_model"),
        guide=updates.get("guide"),
    )

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{collection_id}' not found"
        )

    return collection


@router.delete(
    "/api/collections/{collection_id}",
    summary="Delete a collection",
    tags=["collections"],
)
def delete_collection(collection_id: str, user_id: str = Depends(get_current_user_id)):  # sync: index teardown runs in the threadpool
    """
    Delete a collection and all its documents. Requires owner access.

    Note: The 'default' collection cannot be deleted.
    """
    from api.deps import require_collection_access
    access = require_collection_access(collection_id, user_id)
    if access != "owner":
        raise HTTPException(status_code=403, detail="Only the collection owner can delete it")
    if collection_id == "default":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the default collection"
        )

    try:
        # Remove cached indexer first (before files are deleted)
        indexer_manager.remove_indexer(collection_id)

        success = collection_service.delete_collection(collection_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Collection '{collection_id}' not found"
            )

        return {"message": f"Collection '{collection_id}' deleted", "success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete collection '{collection_id}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete collection: {str(e)}"
        )
