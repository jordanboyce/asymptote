"""Cross-cutting lock for document indexing jobs.

Both upload/local-index jobs (services.upload_service) and reindex jobs
(services.reindex_service) write into the same vector store on disk. Running
them concurrently — or even back-to-back across the upload-vs-reindex boundary
— races on the FAISS index and the metadata SQLite. We treat all indexing
(upload, local-index, repo-index, reindex) as a single class of work with at
most one active job at a time, system-wide.

This module is the source of truth for "is any indexing happening right now".
Call ``raise_if_any_active`` from inside the ``acquire_start_lock`` context
before persisting a new job row.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional

from services.app_database import app_db

logger = logging.getLogger(__name__)

# Held briefly around the "is anything running?" check + record insert.
# Both upload_service.start_*() and reindex_service.start_*() acquire it.
_start_lock = threading.Lock()


class IndexingBusyError(RuntimeError):
    """A new indexing job was requested while another is already active.

    The blocking job is attached as ``active_job`` so handlers can surface
    a structured 409 with enough info for the UI to link to it.
    """

    def __init__(self, active_job: Dict[str, Any]):
        self.active_job = active_job
        kind = active_job.get("kind", "indexing")
        scope = (
            f" for collection '{active_job['collection_id']}'"
            if active_job.get("collection_id")
            else ""
        )
        super().__init__(
            f"{kind.capitalize()} job {active_job['id']} is already running"
            f"{scope}. Wait for it to finish or cancel it before starting another."
        )


def _parse_snapshot(snapshot: Any) -> Dict[str, Any]:
    if isinstance(snapshot, dict):
        return snapshot
    if isinstance(snapshot, str):
        try:
            return json.loads(snapshot)
        except json.JSONDecodeError:
            return {}
    return {}


def get_active_indexing_jobs() -> List[Dict[str, Any]]:
    """Return every in-flight indexing job (upload, local-index, reindex).

    Uniform shape so callers don't care which table the row came from.
    """
    jobs: List[Dict[str, Any]] = []

    # Upload / local-index / repo-index jobs share upload_jobs, distinguished
    # by job_type ('upload' | 'index').
    for row in app_db.get_all_active_upload_jobs():
        jobs.append({
            "kind": row.get("job_type") or "upload",
            "id": row["id"],
            "collection_id": row.get("collection_id"),
            "status": row["status"],
            "current_file": row.get("current_file"),
            "total": row.get("total_files", 0),
            "processed": row.get("processed_files", 0),
            "started_at": row.get("started_at"),
        })

    reindex = app_db.get_active_reindex_job()
    if reindex:
        snapshot = _parse_snapshot(reindex.get("config_snapshot"))
        jobs.append({
            "kind": "reindex",
            "id": reindex["id"],
            "collection_id": snapshot.get("collection_id"),
            "status": reindex["status"],
            "current_file": reindex.get("current_file"),
            "total": reindex.get("total_documents", 0),
            "processed": reindex.get("processed_documents", 0),
            "started_at": reindex.get("started_at"),
        })

    return jobs


def acquire_start_lock() -> threading.Lock:
    """Process-local lock guarding the check-and-insert window for new jobs.

    The DB rows are the durable record; this lock just prevents two concurrent
    HTTP requests from both passing ``raise_if_any_active`` before either has
    persisted its own job row.
    """
    return _start_lock


def raise_if_any_active(intent_kind: str) -> None:
    """Reject if any indexing job is already in flight.

    Args:
        intent_kind: What the caller is trying to start (e.g. ``"upload"``,
            ``"reindex"``) — used only for logging.

    Raises:
        IndexingBusyError: if any upload/index/reindex job is pending/running.
    """
    active = get_active_indexing_jobs()
    if not active:
        return
    # Oldest first — that's the job the user is most likely watching.
    active.sort(key=lambda j: j.get("started_at") or "")
    blocker = active[0]
    logger.info(
        "Blocking %s start: %s job %s is active (collection=%s)",
        intent_kind, blocker["kind"], blocker["id"], blocker.get("collection_id"),
    )
    raise IndexingBusyError(blocker)
