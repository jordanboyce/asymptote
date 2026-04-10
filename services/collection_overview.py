"""Shared helper for building collection overview text.

Used by chat, search, ask, and MCP so LLMs can answer meta-questions like
"how many sources are in this collection?" without having to retrieve chunks.
"""

import logging
from typing import List

from services.collection_service import collection_service
from services.indexer_manager import indexer_manager

logger = logging.getLogger(__name__)

MAX_FILENAMES = 50


def build_collection_overview(collection_ids: List[str]) -> str:
    """Build a compact, plain-text summary of one or more collections.

    Includes name, document count, page total, date range, and filenames so the LLM
    can answer meta-questions like "how many files are in here?" or "do you have
    anything from March?" without having to retrieve content chunks.
    """
    lines: List[str] = []

    for col_id in collection_ids:
        col = collection_service.get_collection(col_id)
        if not col:
            continue

        try:
            indexer = indexer_manager.get_indexer(col_id)
            docs = indexer.list_documents()
        except Exception as e:
            logger.warning(f"Overview: failed to list documents for '{col_id}': {e}")
            docs = []

        doc_count = len(docs)
        page_total = sum((d.get("total_pages") or d.get("num_pages") or 0) for d in docs)
        chunk_total = sum((d.get("total_chunks") or d.get("num_chunks") or 0) for d in docs)

        timestamps = [d.get("upload_timestamp") for d in docs if d.get("upload_timestamp")]
        date_range = ""
        if timestamps:
            timestamps.sort()
            date_range = f"{timestamps[0][:10]} to {timestamps[-1][:10]}"

        lines.append(f"- Collection: {col.get('name', col_id)}")
        if col.get("description"):
            lines.append(f"  Description: {col['description']}")
        lines.append(f"  Documents: {doc_count}, Pages: {page_total}, Chunks: {chunk_total}")
        if date_range:
            lines.append(f"  Indexed date range: {date_range}")

        if docs:
            filenames = [d["filename"] for d in docs[:MAX_FILENAMES]]
            files_str = ", ".join(filenames)
            if doc_count > MAX_FILENAMES:
                files_str += f", ... (+{doc_count - MAX_FILENAMES} more)"
            lines.append(f"  Filenames: {files_str}")

    return "\n".join(lines) if lines else "(No collection metadata available.)"
