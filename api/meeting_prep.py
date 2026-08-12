"""`prep_for_meeting` route — the §7 keystone, over HTTP.

One POST returns the whole pre-meeting page. No LLM call, so it is fast,
free, and identical on every run against the same data — which is what makes
it safe to demo live.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from api.deps import get_indexer, require_collection_access
from middleware.user_context import get_current_user_id
from services.meeting_prep import build_meeting_prep
from services.sharing_service import sharing_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/collections", tags=["chat"])


@router.post(
    "/{collection_id}/prep",
    summary="Assemble the full pre-meeting page for a client",
)
async def prep_for_meeting_endpoint(
    collection_id: str,
    when: str | None = Body(default=None),
    household_collection_ids: list[str] | None = Body(default=None),
    include_tlh: bool = Body(default=True),
    include_market_context: bool = Body(default=True),
    top_n: int = Body(default=10),
    user_id: str = Depends(get_current_user_id),
):
    """Compose meeting history, portfolio, IPS policy, TLH, and market events
    into one page, plus an ordered agenda and an explicit list of what could
    not be determined.

    ``when`` is the meeting time (ISO); it drives "days since last meeting"
    and overdue arithmetic. ``household_collection_ids`` widens the
    wash-sale scan only, same semantics as the TLH endpoint — each one is
    access-checked independently so a caller cannot pull holdings from a
    collection they can't read.
    """
    require_collection_access(collection_id, user_id, required="read")

    try:
        indexer = get_indexer(collection_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    vector_store = indexer.vector_store
    try:
        holdings_store = vector_store.holdings_store
        meeting_notes_store = vector_store.meeting_notes_store
        metadata_store = vector_store.metadata_store
        client_profile_store = vector_store.client_profile_store
    except AttributeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Collection is missing a required store: {exc}",
        )

    household_stores = [holdings_store]
    seen = {collection_id}
    for cid in (household_collection_ids or []):
        if cid in seen:
            continue
        seen.add(cid)
        if not sharing_service.check_collection_access(cid, user_id):
            logger.warning("prep: skipping household collection %s — no access", cid)
            continue
        try:
            household_stores.append(get_indexer(cid).vector_store.holdings_store)
        except (ValueError, AttributeError) as exc:
            logger.warning("prep: skipping household collection %s: %s", cid, exc)

    try:
        return build_meeting_prep(
            holdings_store=holdings_store,
            meeting_notes_store=meeting_notes_store,
            metadata_store=metadata_store,
            client_profile_store=client_profile_store,
            collection_id=collection_id,
            when=when,
            household_stores=household_stores,
            include_tlh=include_tlh,
            include_market_context=include_market_context,
            top_n=top_n,
        )
    except Exception as exc:
        # build_meeting_prep degrades section-by-section internally, so
        # reaching here means something structural broke.
        logger.error("Meeting prep failed for collection %s: %s", collection_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Meeting prep failed: {exc}")
