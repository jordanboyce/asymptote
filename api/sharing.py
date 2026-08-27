"""User and collection-sharing endpoints."""

import logging

from fastapi import Depends, HTTPException, status

from config import settings
from services.sharing_service import sharing_service
from middleware.user_context import get_current_user_id

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/user/me", summary="Get current user info", tags=["users"])
async def get_current_user(user_id: str = Depends(get_current_user_id)):
    """Get the current user's identity and private-collections status.

    In private-collections mode, user_id is the verified Cloudflare Access
    identity; None means an anonymous (password-authenticated) caller who can
    reach team collections only.
    """
    from services.app_database import app_db
    user = app_db.get_user(user_id) if user_id else None
    return {
        "user_id": user_id,
        "display_name": (user["display_name"] if user else user_id) or "anonymous",
        "private_collections": settings.private_collections,
        "db_backend": settings.db_backend,
    }


@router.post(
    "/api/collections/{collection_id}/share",
    summary="Create a share link for a collection",
    tags=["sharing"],
    status_code=status.HTTP_201_CREATED,
)
async def create_share(collection_id: str, body: dict, user_id: str = Depends(get_current_user_id)):
    """
    Generate a shareable link for a collection.

    Body:
        permission: 'read' or 'readwrite' (default: 'read')
        expires_days: Optional number of days until expiry (null = never)
    """
    try:
        share = sharing_service.create_share(
            collection_id=collection_id,
            owner_id=user_id,
            permission=body.get("permission", "read"),
            expires_days=body.get("expires_days"),
        )
        return share
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get(
    "/api/collections/{collection_id}/shares",
    summary="List shares for a collection",
    tags=["sharing"],
)
async def list_collection_shares(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """Get all active share links for a collection you own."""
    try:
        shares = sharing_service.get_shares_for_collection(collection_id, user_id)
        return {"shares": shares}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post(
    "/api/shares/{share_token}/accept",
    summary="Accept a share link",
    tags=["sharing"],
)
async def accept_share(share_token: str, user_id: str = Depends(get_current_user_id)):
    """Accept a share link to gain access to a collection."""
    try:
        result = sharing_service.accept_share(share_token, user_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/api/shared-with-me",
    summary="List collections shared with me",
    tags=["sharing"],
)
async def list_shared_with_me(user_id: str = Depends(get_current_user_id)):
    """Get all collections that have been shared with the current user."""
    collections = sharing_service.get_shared_with_me(user_id)
    return {"collections": collections}


@router.delete(
    "/api/shares/{share_id}",
    summary="Revoke a share link",
    tags=["sharing"],
)
async def revoke_share(share_id: str, user_id: str = Depends(get_current_user_id)):
    """Revoke a share link you created."""
    try:
        sharing_service.revoke_share(share_id, user_id)
        return {"message": "Share revoked", "success": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
