"""Expertise pack endpoints."""

import logging

from fastapi import HTTPException

from models.schemas import (
    ExpertisePack,
    ExpertisePackCreate,
    ExpertisePackUpdate,
    CollectionExpertiseResponse,
    SetCollectionExpertiseRequest,
)

from fastapi import APIRouter
from api.deps import (
    expertise_store,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/expertise/packs", response_model=list[ExpertisePack], tags=["expertise"])
async def list_expertise_packs():
    """Return all expertise packs ordered by name."""
    return expertise_store.list_packs()


@router.post("/api/expertise/packs", response_model=ExpertisePack, status_code=201, tags=["expertise"])
async def create_expertise_pack(data: ExpertisePackCreate):
    """Create a new expertise pack."""
    return expertise_store.create_pack(data)


@router.get("/api/expertise/packs/{pack_id}", response_model=ExpertisePack, tags=["expertise"])
async def get_expertise_pack(pack_id: str):
    """Return a single expertise pack by ID."""
    pack = expertise_store.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Expertise pack not found")
    return pack


@router.put("/api/expertise/packs/{pack_id}", response_model=ExpertisePack, tags=["expertise"])
async def update_expertise_pack(pack_id: str, data: ExpertisePackUpdate):
    """Partially update an expertise pack."""
    pack = expertise_store.update_pack(pack_id, data)
    if pack is None:
        raise HTTPException(status_code=404, detail="Expertise pack not found")
    return pack


@router.delete("/api/expertise/packs/{pack_id}", status_code=204, tags=["expertise"])
async def delete_expertise_pack(pack_id: str):
    """Delete an expertise pack (also removes all collection attachments)."""
    deleted = expertise_store.delete_pack(pack_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Expertise pack not found")


@router.get(
    "/api/collections/{collection_id}/expertise",
    response_model=CollectionExpertiseResponse,
    tags=["expertise"],
)
async def get_collection_expertise(collection_id: str):
    """List all expertise packs attached to a collection."""
    packs = expertise_store.get_packs_for_collection(collection_id)
    return CollectionExpertiseResponse(collection_id=collection_id, packs=packs)


@router.put(
    "/api/collections/{collection_id}/expertise",
    response_model=CollectionExpertiseResponse,
    tags=["expertise"],
)
async def set_collection_expertise(collection_id: str, data: SetCollectionExpertiseRequest):
    """Replace the full set of expertise packs attached to a collection."""
    expertise_store.set_packs_for_collection(collection_id, data.pack_ids)
    packs = expertise_store.get_packs_for_collection(collection_id)
    return CollectionExpertiseResponse(collection_id=collection_id, packs=packs)
