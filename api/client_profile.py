"""Client profile / IPS routes (v4.6).

CRUD over the per-Collection :class:`ClientProfileStore`. The profile is
advisor-authored — nothing here is extracted or inferred — so these are
plain reads and writes with one twist: every response carries a
``completeness`` block naming what meeting prep still cannot say. That is
what turns a half-filled form into an actionable one.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_indexer, require_collection_access
from middleware.user_context import get_current_user_id
from models.schemas import ClientProfile, ClientProfileResponse
from services.financial.client_profile import (
    allocation_targets_sum,
    profile_completeness,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/collections", tags=["client-profile"])


def _profile_store(collection_id: str):
    try:
        indexer = get_indexer(collection_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        return indexer.vector_store.client_profile_store
    except AttributeError:
        raise HTTPException(
            status_code=422,
            detail="No client profile store for this collection.",
        )


def _response(collection_id: str, store, profile: ClientProfile | None, exists: bool) -> ClientProfileResponse:
    completeness = profile_completeness(profile)
    if profile is not None:
        target_sum = allocation_targets_sum(profile)
        if target_sum is not None and abs(target_sum - 100.0) > 0.5:
            completeness = {
                **completeness,
                "target_allocation_warning": (
                    f"Target allocation sums to {target_sum}%, not 100%. Drift will be "
                    f"computed as entered."
                ),
            }

    updated_at = None
    try:
        raw = store.get(collection_id)
        updated_at = (raw or {}).get("updated_at")
    except Exception:  # a missing timestamp must not fail the read
        pass

    return ClientProfileResponse(
        collection_id=collection_id,
        profile=profile if profile is not None else ClientProfile(),
        exists=exists,
        completeness=completeness,
        updated_at=updated_at,
    )


@router.get(
    "/{collection_id}/profile",
    response_model=ClientProfileResponse,
    summary="Get this client's profile / IPS",
)
async def get_client_profile(
    collection_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Return the saved profile, or an all-empty one with ``exists: false``.

    Never 404s on a missing profile — the form needs a shape to bind to
    whether or not anything has been saved yet.
    """
    require_collection_access(collection_id, user_id, required="read")
    store = _profile_store(collection_id)
    profile = store.get_model(collection_id)
    return _response(collection_id, store, profile, exists=profile is not None)


@router.put(
    "/{collection_id}/profile",
    response_model=ClientProfileResponse,
    summary="Create or replace this client's profile / IPS",
)
async def put_client_profile(
    collection_id: str,
    profile: ClientProfile,
    user_id: str = Depends(get_current_user_id),
):
    """Full replace — send the whole profile, not a patch.

    The form is small and always loaded in full, so replace semantics avoid
    the merge ambiguity of "did they clear this field or omit it?"
    """
    require_collection_access(collection_id, user_id, required="readwrite")
    store = _profile_store(collection_id)
    try:
        store.save(collection_id, profile)
    except Exception as exc:
        logger.error("Failed to save client profile for %s: %s", collection_id, exc)
        raise HTTPException(status_code=500, detail=f"Could not save profile: {exc}")
    return _response(collection_id, store, profile, exists=True)


@router.delete(
    "/{collection_id}/profile",
    summary="Delete this client's profile / IPS",
)
async def delete_client_profile(
    collection_id: str,
    user_id: str = Depends(get_current_user_id),
):
    require_collection_access(collection_id, user_id, required="readwrite")
    store = _profile_store(collection_id)
    deleted = store.delete(collection_id)
    return {"collection_id": collection_id, "deleted": deleted}
