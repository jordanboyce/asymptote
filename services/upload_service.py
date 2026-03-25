"""Background upload service for processing large files in a separate thread.

Handles:
- File staging
- Background document indexing (in separate thread that survives page refreshes)
- Progress tracking via upload_jobs table
- Real-time progress events via SSE (v4.0)
"""

import logging
import threading
import shutil
import json
import asyncio
import queue
from pathlib import Path
from typing import List, Optional, Dict, Callable, Any
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum

from services.app_database import app_db
from services.indexer_manager import indexer_manager
from services.collection_service import collection_service

logger = logging.getLogger(__name__)


class UploadPhase(str, Enum):
    """Upload processing phases for granular progress tracking."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    SAVING = "saving"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ProgressEvent:
    """Real-time progress event for SSE streaming."""

    job_id: int
    event_type: str  # phase_start, phase_progress, file_complete, job_complete, error
    phase: str
    current_file: Optional[str] = None
    file_index: int = 0
    total_files: int = 0
    phase_progress: int = 0  # 0-100 within phase
    phase_detail: Optional[str] = None
    chunks_processed: int = 0
    chunks_total: int = 0
    overall_percent: float = 0.0
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_sse(self) -> str:
        """Format as SSE event string."""
        data = json.dumps(asdict(self))
        return f"event: {self.event_type}\ndata: {data}\n\n"


class UploadService:
    """Manages background document upload and indexing.

    Uses threading to ensure uploads continue even when the client
    disconnects or refreshes the page.

    v4.0: Adds real-time progress events via SSE.
    """

    def __init__(self):
        self._active_threads: Dict[int, threading.Thread] = {}
        self._lock = threading.Lock()
        # SSE event queues per job (for real-time streaming)
        self._event_queues: Dict[int, List[queue.Queue]] = {}
        self._queues_lock = threading.Lock()
        # Cancellation flags
        self._cancel_flags: Dict[int, bool] = {}

    def subscribe_to_events(self, job_id: int) -> queue.Queue:
        """Subscribe to real-time progress events for a job.

        Returns a queue that will receive ProgressEvent objects.
        """
        event_queue = queue.Queue()
        with self._queues_lock:
            if job_id not in self._event_queues:
                self._event_queues[job_id] = []
            self._event_queues[job_id].append(event_queue)
        return event_queue

    def unsubscribe_from_events(self, job_id: int, event_queue: queue.Queue):
        """Unsubscribe from progress events."""
        with self._queues_lock:
            if job_id in self._event_queues:
                try:
                    self._event_queues[job_id].remove(event_queue)
                    if not self._event_queues[job_id]:
                        del self._event_queues[job_id]
                except ValueError:
                    pass

    def _broadcast_event(self, event: ProgressEvent):
        """Broadcast progress event to all subscribers."""
        with self._queues_lock:
            queues = self._event_queues.get(event.job_id, [])
            for q in queues:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass  # Skip if queue is full

    def cancel_job(self, job_id: int, force: bool = False) -> bool:
        """Request cancellation of a running job.

        Args:
            job_id: Job ID to cancel
            force: If True, mark job as cancelled in DB even if thread not found
                   (useful for orphaned jobs where thread crashed/exited)

        Returns True if cancellation was requested/completed, False if job not found.
        """
        with self._lock:
            if job_id in self._active_threads:
                self._cancel_flags[job_id] = True
                logger.info(f"Cancellation requested for job {job_id}")
                return True

        # Thread not found - if force=True, mark as cancelled directly in DB
        if force:
            logger.info(f"Force-cancelling orphaned job {job_id} (thread not active)")
            app_db.update_upload_job(
                job_id,
                status="cancelled",
                error="Force cancelled (job thread was not active)",
            )
            # Broadcast cancellation event for any listeners
            self._broadcast_event(ProgressEvent(
                job_id=job_id,
                event_type="job_cancelled",
                phase="cancelled",
                error="Force cancelled (job thread was not active)",
            ))
            return True

        return False

    def _is_cancelled(self, job_id: int) -> bool:
        """Check if job has been cancelled."""
        return self._cancel_flags.get(job_id, False)

    def start_upload(
        self,
        staged_files: List[dict],
        collection_id: str = "default",
    ) -> int:
        """Start a background upload job in a separate thread.

        Args:
            staged_files: List of dicts with 'temp_path' and 'filename' keys
            collection_id: Target collection ID

        Returns:
            Job ID for tracking progress

        Raises:
            RuntimeError: If an upload job is already running for this collection
        """
        # Check if already running for this collection
        active_job = app_db.get_active_upload_job(collection_id)
        if active_job:
            raise RuntimeError(
                f"Upload job {active_job['id']} is already running for collection '{collection_id}'"
            )

        # Create job record with job_type='upload' for browser uploads
        job_id = app_db.create_upload_job(collection_id, len(staged_files), job_type="upload")

        # Initialize cancellation flag
        self._cancel_flags[job_id] = False

        # Start background thread (daemon=False so it survives even if main thread ends)
        thread = threading.Thread(
            target=self._run_upload,
            args=(job_id, staged_files, collection_id),
            name=f"upload-job-{job_id}",
            daemon=False,  # Keep running even if main thread ends
        )

        with self._lock:
            self._active_threads[job_id] = thread

        thread.start()
        logger.info(f"Started upload thread for job {job_id}")

        return job_id

    def _run_upload(
        self,
        job_id: int,
        staged_files: List[dict],
        collection_id: str,
    ):
        """Run the upload/indexing process in background thread.

        Args:
            job_id: Job ID for tracking
            staged_files: List of staged files to process
            collection_id: Target collection
        """
        results = {
            "documents_processed": 0,
            "total_pages": 0,
            "total_chunks": 0,
            "document_ids": [],
            "failed_files": [],
        }

        try:
            # Update job status to running
            app_db.update_upload_job(job_id, status="running")

            # Get indexer and documents path
            indexer = indexer_manager.get_indexer(collection_id)
            documents_dir = indexer_manager.get_documents_path(collection_id)

            total_files = len(staged_files)
            logger.info(f"Starting upload job {job_id}: {total_files} files for collection '{collection_id}'")

            for idx, file_info in enumerate(staged_files, 1):
                # Check for cancellation
                if self._is_cancelled(job_id):
                    logger.info(f"Job {job_id} cancelled by user")
                    app_db.update_upload_job(
                        job_id,
                        status="cancelled",
                        error="Cancelled by user",
                        result_summary=json.dumps(results),
                    )
                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="job_cancelled",
                        phase="cancelled",
                        error="Cancelled by user",
                    ))
                    return

                temp_path = Path(file_info["temp_path"])
                filename = file_info["filename"]

                try:
                    # Broadcast file start
                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="file_start",
                        phase=UploadPhase.EXTRACTING.value,
                        current_file=filename,
                        file_index=idx,
                        total_files=total_files,
                        overall_percent=((idx - 1) / total_files) * 100,
                    ))

                    # Update progress before processing
                    app_db.update_upload_job(
                        job_id,
                        current_file=filename,
                        processed_files=idx - 1,
                        phase=UploadPhase.EXTRACTING.value,
                    )

                    logger.info(f"Processing ({idx}/{total_files}): {filename}")

                    # Move file from temp to documents directory
                    final_path = documents_dir / filename
                    shutil.move(str(temp_path), str(final_path))

                    # Create progress callback for granular updates
                    def progress_callback(
                        phase: str,
                        progress: int,
                        detail: str = None,
                        chunks_done: int = 0,
                        chunks_total: int = 0,
                    ):
                        # Calculate overall progress
                        # Each file contributes equally; within file, phases contribute:
                        # extracting: 20%, chunking: 10%, embedding: 60%, saving: 10%
                        phase_weights = {
                            "extracting": 0.2,
                            "chunking": 0.1,
                            "embedding": 0.6,
                            "saving": 0.1,
                        }
                        phase_starts = {
                            "extracting": 0,
                            "chunking": 0.2,
                            "embedding": 0.3,
                            "saving": 0.9,
                        }

                        file_progress = phase_starts.get(phase, 0) + (
                            phase_weights.get(phase, 0) * progress / 100
                        )
                        overall = ((idx - 1) + file_progress) / total_files * 100

                        # Update database (throttled - every 5%)
                        if progress % 5 == 0 or progress == 100:
                            app_db.update_upload_job(
                                job_id,
                                phase=phase,
                                phase_progress=progress,
                                phase_detail=detail,
                                chunks_processed=chunks_done,
                                chunks_total=chunks_total,
                            )

                        # Broadcast event
                        self._broadcast_event(ProgressEvent(
                            job_id=job_id,
                            event_type="phase_progress",
                            phase=phase,
                            current_file=filename,
                            file_index=idx,
                            total_files=total_files,
                            phase_progress=progress,
                            phase_detail=detail,
                            chunks_processed=chunks_done,
                            chunks_total=chunks_total,
                            overall_percent=overall,
                        ))

                    # Index the document with progress callbacks
                    doc_metadata = indexer.index_document_with_progress(
                        final_path,
                        filename,
                        progress_callback=progress_callback,
                    )

                    # Add document to collection
                    collection_service.add_document(collection_id, doc_metadata.document_id)

                    # Update results
                    results["documents_processed"] += 1
                    results["total_pages"] += doc_metadata.total_pages
                    results["total_chunks"] += doc_metadata.total_chunks
                    results["document_ids"].append(doc_metadata.document_id)

                    # Update progress after each file
                    app_db.update_upload_job(
                        job_id,
                        processed_files=idx,
                        phase=UploadPhase.COMPLETED.value,
                    )

                    # Broadcast file complete
                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="file_complete",
                        phase=UploadPhase.COMPLETED.value,
                        current_file=filename,
                        file_index=idx,
                        total_files=total_files,
                        overall_percent=(idx / total_files) * 100,
                        chunks_total=doc_metadata.total_chunks,
                    ))

                    logger.info(
                        f"Indexed {filename}: {doc_metadata.total_pages} pages, "
                        f"{doc_metadata.total_chunks} chunks"
                    )

                except Exception as e:
                    logger.error(f"Failed to process {filename}: {e}")
                    results["failed_files"].append({
                        "filename": filename,
                        "error": str(e),
                    })

                    # Broadcast file error
                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="file_error",
                        phase=UploadPhase.FAILED.value,
                        current_file=filename,
                        file_index=idx,
                        total_files=total_files,
                        error=str(e),
                    ))

                    # Clean up temp file if it still exists
                    if temp_path.exists():
                        try:
                            temp_path.unlink()
                        except Exception:
                            pass

            # Save index after all files processed
            if results["documents_processed"] > 0:
                indexer.save_index()

            # Mark job as completed
            app_db.update_upload_job(
                job_id,
                status="completed",
                processed_files=total_files,
                current_file=None,
                phase=UploadPhase.COMPLETED.value,
                result_summary=json.dumps(results),
            )

            # Broadcast job complete
            self._broadcast_event(ProgressEvent(
                job_id=job_id,
                event_type="job_complete",
                phase=UploadPhase.COMPLETED.value,
                total_files=total_files,
                overall_percent=100.0,
            ))

            logger.info(
                f"Upload job {job_id} completed: {results['documents_processed']}/{total_files} files, "
                f"{results['total_chunks']} chunks"
            )

        except Exception as e:
            logger.error(f"Upload job {job_id} failed: {e}")
            app_db.update_upload_job(
                job_id,
                status="failed",
                phase=UploadPhase.FAILED.value,
                error=str(e),
                result_summary=json.dumps(results),
            )

            # Broadcast job error
            self._broadcast_event(ProgressEvent(
                job_id=job_id,
                event_type="job_error",
                phase=UploadPhase.FAILED.value,
                error=str(e),
            ))

        finally:
            # Clean up thread reference and cancel flag
            with self._lock:
                if job_id in self._active_threads:
                    del self._active_threads[job_id]
                if job_id in self._cancel_flags:
                    del self._cancel_flags[job_id]

    def get_job_status(self, job_id: int) -> Optional[dict]:
        """Get upload job status with progress percentage.

        Args:
            job_id: Job ID

        Returns:
            Job details with progress_percent, or None if not found
        """
        job = app_db.get_upload_job(job_id)
        if not job:
            return None

        # Calculate progress percentage
        if job["total_files"] > 0:
            progress = (job["processed_files"] / job["total_files"]) * 100
        else:
            progress = 0

        # Parse result_summary if present
        result_summary = None
        if job.get("result_summary"):
            try:
                result_summary = json.loads(job["result_summary"])
            except json.JSONDecodeError:
                pass

        return {
            "job_id": job["id"],
            "collection_id": job["collection_id"],
            "status": job["status"],
            "total_files": job["total_files"],
            "processed_files": job["processed_files"],
            "current_file": job["current_file"],
            "progress_percent": round(progress, 1),
            "error": job["error"],
            "result_summary": result_summary,
            "started_at": job["started_at"],
            "completed_at": job["completed_at"],
            # v4.0: Granular progress
            "phase": job.get("phase"),
            "phase_progress": job.get("phase_progress"),
            "phase_detail": job.get("phase_detail"),
            "chunks_processed": job.get("chunks_processed"),
            "chunks_total": job.get("chunks_total"),
            # Job type: 'upload' for browser uploads, 'index' for local file indexing
            "job_type": job.get("job_type", "upload"),
        }


    def start_local_index(
        self,
        file_paths: List[str],
        collection_id: str = "default",
        copy_to_library: bool = False,
    ) -> int:
        """Start a background job to index local files.

        Args:
            file_paths: List of absolute file paths to index
            collection_id: Target collection ID
            copy_to_library: If True, copy files to library; otherwise index in-place

        Returns:
            Job ID for tracking progress
        """
        # Check if already running for this collection
        active_job = app_db.get_active_upload_job(collection_id)
        if active_job:
            raise RuntimeError(
                f"Upload job {active_job['id']} is already running for collection '{collection_id}'"
            )

        # Create job record with job_type='index' for local file indexing
        job_id = app_db.create_upload_job(collection_id, len(file_paths), job_type="index")

        # Initialize cancellation flag
        self._cancel_flags[job_id] = False

        # Start background thread
        thread = threading.Thread(
            target=self._run_local_index,
            args=(job_id, file_paths, collection_id, copy_to_library),
            name=f"local-index-job-{job_id}",
            daemon=False,
        )

        with self._lock:
            self._active_threads[job_id] = thread

        thread.start()
        logger.info(f"Started local index thread for job {job_id}: {len(file_paths)} files")

        return job_id

    def _run_local_index(
        self,
        job_id: int,
        file_paths: List[str],
        collection_id: str,
        copy_to_library: bool,
    ):
        """Run local file indexing in background thread."""
        results = {
            "documents_processed": 0,
            "total_pages": 0,
            "total_chunks": 0,
            "document_ids": [],
            "failed_files": [],
        }

        try:
            app_db.update_upload_job(job_id, status="running")

            indexer = indexer_manager.get_indexer(collection_id)
            documents_dir = indexer_manager.get_documents_path(collection_id) if copy_to_library else None

            total_files = len(file_paths)
            logger.info(f"Starting local index job {job_id}: {total_files} files, copy={copy_to_library}")

            for idx, file_path in enumerate(file_paths, 1):
                if self._is_cancelled(job_id):
                    logger.info(f"Job {job_id} cancelled by user")
                    app_db.update_upload_job(
                        job_id,
                        status="cancelled",
                        error="Cancelled by user",
                        result_summary=json.dumps(results),
                    )
                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="job_cancelled",
                        phase="cancelled",
                        error="Cancelled by user",
                    ))
                    return

                source_path = Path(file_path)
                filename = source_path.name

                try:
                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="file_start",
                        phase=UploadPhase.EXTRACTING.value,
                        current_file=filename,
                        file_index=idx,
                        total_files=total_files,
                        overall_percent=((idx - 1) / total_files) * 100,
                    ))

                    app_db.update_upload_job(
                        job_id,
                        current_file=filename,
                        processed_files=idx - 1,
                        phase=UploadPhase.EXTRACTING.value,
                    )

                    logger.info(f"Indexing ({idx}/{total_files}): {filename}")

                    # Determine index path based on copy mode
                    if copy_to_library:
                        # Copy file to library
                        final_path = documents_dir / filename
                        counter = 1
                        while final_path.exists():
                            stem = source_path.stem
                            suffix = source_path.suffix
                            final_path = documents_dir / f"{stem}_{counter}{suffix}"
                            counter += 1
                        shutil.copy2(str(source_path), str(final_path))
                        index_path = final_path
                        source_type = "upload"
                        stored_source_path = None
                    else:
                        # Index in-place
                        index_path = source_path
                        source_type = "local_reference"
                        stored_source_path = str(source_path.absolute())

                    # Create progress callback
                    def progress_callback(
                        phase: str,
                        progress: int,
                        detail: str = None,
                        chunks_done: int = 0,
                        chunks_total: int = 0,
                    ):
                        phase_weights = {
                            "extracting": 0.2,
                            "chunking": 0.1,
                            "embedding": 0.6,
                            "saving": 0.1,
                        }
                        phase_starts = {
                            "extracting": 0,
                            "chunking": 0.2,
                            "embedding": 0.3,
                            "saving": 0.9,
                        }

                        file_progress = phase_starts.get(phase, 0) + (
                            phase_weights.get(phase, 0) * progress / 100
                        )
                        overall = ((idx - 1) + file_progress) / total_files * 100

                        if progress % 5 == 0 or progress == 100:
                            app_db.update_upload_job(
                                job_id,
                                phase=phase,
                                phase_progress=progress,
                                phase_detail=detail,
                                chunks_processed=chunks_done,
                                chunks_total=chunks_total,
                            )

                        self._broadcast_event(ProgressEvent(
                            job_id=job_id,
                            event_type="phase_progress",
                            phase=phase,
                            current_file=filename,
                            file_index=idx,
                            total_files=total_files,
                            phase_progress=progress,
                            phase_detail=detail,
                            chunks_processed=chunks_done,
                            chunks_total=chunks_total,
                            overall_percent=overall,
                        ))

                    # Index the document
                    doc_metadata = indexer.index_document_with_progress(
                        index_path,
                        index_path.name,
                        progress_callback=progress_callback,
                    )

                    # Update source info if indexing in-place
                    if source_type == "local_reference":
                        indexer.vector_store.metadata_store.update_document_source(
                            doc_metadata.document_id,
                            source_path=stored_source_path,
                            source_type=source_type,
                        )

                    collection_service.add_document(collection_id, doc_metadata.document_id)

                    results["documents_processed"] += 1
                    results["total_pages"] += doc_metadata.total_pages
                    results["total_chunks"] += doc_metadata.total_chunks
                    results["document_ids"].append(doc_metadata.document_id)

                    app_db.update_upload_job(
                        job_id,
                        processed_files=idx,
                        phase=UploadPhase.COMPLETED.value,
                    )

                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="file_complete",
                        phase=UploadPhase.COMPLETED.value,
                        current_file=filename,
                        file_index=idx,
                        total_files=total_files,
                        overall_percent=(idx / total_files) * 100,
                        chunks_total=doc_metadata.total_chunks,
                    ))

                    logger.info(
                        f"Indexed {filename}: {doc_metadata.total_pages} pages, "
                        f"{doc_metadata.total_chunks} chunks"
                    )

                except Exception as e:
                    logger.error(f"Failed to index {filename}: {e}")
                    results["failed_files"].append({
                        "filename": filename,
                        "error": str(e),
                    })

                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="file_error",
                        phase=UploadPhase.FAILED.value,
                        current_file=filename,
                        file_index=idx,
                        total_files=total_files,
                        error=str(e),
                    ))

            if results["documents_processed"] > 0:
                indexer.save_index()

            app_db.update_upload_job(
                job_id,
                status="completed",
                processed_files=total_files,
                current_file=None,
                phase=UploadPhase.COMPLETED.value,
                result_summary=json.dumps(results),
            )

            self._broadcast_event(ProgressEvent(
                job_id=job_id,
                event_type="job_complete",
                phase=UploadPhase.COMPLETED.value,
                total_files=total_files,
                overall_percent=100.0,
            ))

            logger.info(
                f"Local index job {job_id} completed: {results['documents_processed']}/{total_files} files"
            )

        except Exception as e:
            logger.error(f"Local index job {job_id} failed: {e}")
            app_db.update_upload_job(
                job_id,
                status="failed",
                phase=UploadPhase.FAILED.value,
                error=str(e),
                result_summary=json.dumps(results),
            )

            self._broadcast_event(ProgressEvent(
                job_id=job_id,
                event_type="job_error",
                phase=UploadPhase.FAILED.value,
                error=str(e),
            ))

        finally:
            with self._lock:
                if job_id in self._active_threads:
                    del self._active_threads[job_id]
                if job_id in self._cancel_flags:
                    del self._cancel_flags[job_id]


    def start_repo_index(
        self,
        repo_path: str,
        collection_id: str = "default",
        recursive: bool = True,
        file_extensions: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> int:
        """Start a background job to index a repository/folder.

        Args:
            repo_path: Path to the repository/folder to index
            collection_id: Target collection ID
            recursive: Whether to scan subdirectories
            file_extensions: List of extensions to include (e.g., ['.py', '.js'])
            exclude_patterns: Glob patterns to exclude

        Returns:
            Job ID for tracking progress
        """
        import os
        import fnmatch

        repo = Path(repo_path)
        if not repo.exists() or not repo.is_dir():
            raise ValueError(f"Invalid repository path: {repo_path}")

        # Default exclude patterns for common non-code directories
        default_excludes = [
            '**/node_modules/**', '**/.git/**', '**/__pycache__/**',
            '**/venv/**', '**/.venv/**', '**/dist/**', '**/build/**',
            '**/*.pyc', '**/.DS_Store', '**/Thumbs.db'
        ]
        excludes = (exclude_patterns or []) + default_excludes

        # Collect files to index
        files_to_index = []

        def should_exclude(path: Path) -> bool:
            rel = str(path.relative_to(repo))
            for pattern in excludes:
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel.replace('\\', '/'), pattern):
                    return True
            return False

        def should_include(path: Path) -> bool:
            if file_extensions:
                return path.suffix.lower() in file_extensions
            return True  # Include all if no filter

        if recursive:
            for root, dirs, files in os.walk(repo):
                dirs[:] = [d for d in dirs if not should_exclude(Path(root) / d)]
                for f in files:
                    fp = Path(root) / f
                    if not should_exclude(fp) and should_include(fp):
                        files_to_index.append(str(fp))
        else:
            for fp in repo.iterdir():
                if fp.is_file() and not should_exclude(fp) and should_include(fp):
                    files_to_index.append(str(fp))

        if not files_to_index:
            raise ValueError(f"No matching files found in {repo_path}")

        # Check if already running
        active_job = app_db.get_active_upload_job(collection_id)
        if active_job:
            raise RuntimeError(
                f"Job {active_job['id']} is already running for collection '{collection_id}'"
            )

        # Create job record
        job_id = app_db.create_upload_job(collection_id, len(files_to_index), job_type="index")
        self._cancel_flags[job_id] = False

        # Start background thread
        thread = threading.Thread(
            target=self._run_repo_index,
            args=(job_id, files_to_index, repo_path, collection_id),
            name=f"repo-index-job-{job_id}",
            daemon=False,
        )

        with self._lock:
            self._active_threads[job_id] = thread

        thread.start()
        logger.info(f"Started repo index job {job_id}: {len(files_to_index)} files from {repo_path}")

        return job_id

    def _run_repo_index(
        self,
        job_id: int,
        file_paths: List[str],
        repo_path: str,
        collection_id: str,
    ):
        """Run repository indexing in background thread."""
        results = {
            "documents_processed": 0,
            "total_pages": 0,
            "total_chunks": 0,
            "document_ids": [],
            "failed_files": [],
        }

        try:
            app_db.update_upload_job(job_id, status="running")

            indexer = indexer_manager.get_indexer(collection_id)
            documents_dir = indexer_manager.get_documents_path(collection_id)
            repo = Path(repo_path)

            total_files = len(file_paths)
            logger.info(f"Starting repo index job {job_id}: {total_files} files from {repo_path}")

            for idx, file_path in enumerate(file_paths, 1):
                if self._is_cancelled(job_id):
                    logger.info(f"Job {job_id} cancelled")
                    app_db.update_upload_job(
                        job_id,
                        status="cancelled",
                        error="Cancelled by user",
                        result_summary=json.dumps(results),
                    )
                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="job_cancelled",
                        phase="cancelled",
                    ))
                    return

                source_path = Path(file_path)
                # Create safe filename preserving relative path structure
                try:
                    rel_path = source_path.relative_to(repo)
                    safe_filename = str(rel_path).replace('/', '_').replace('\\', '_')
                except ValueError:
                    safe_filename = source_path.name

                try:
                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="file_start",
                        phase=UploadPhase.EXTRACTING.value,
                        current_file=safe_filename,
                        file_index=idx,
                        total_files=total_files,
                        overall_percent=((idx - 1) / total_files) * 100,
                    ))

                    app_db.update_upload_job(
                        job_id,
                        current_file=safe_filename,
                        processed_files=idx - 1,
                        phase=UploadPhase.EXTRACTING.value,
                    )

                    logger.info(f"Indexing ({idx}/{total_files}): {safe_filename}")

                    # Copy file to library
                    dest_path = documents_dir / safe_filename
                    shutil.copy2(str(source_path), str(dest_path))

                    # Create progress callback
                    def progress_callback(
                        phase: str,
                        progress: int,
                        detail: str = None,
                        chunks_done: int = 0,
                        chunks_total: int = 0,
                    ):
                        phase_weights = {"extracting": 0.2, "chunking": 0.1, "embedding": 0.6, "saving": 0.1}
                        phase_starts = {"extracting": 0, "chunking": 0.2, "embedding": 0.3, "saving": 0.9}

                        file_progress = phase_starts.get(phase, 0) + (phase_weights.get(phase, 0) * progress / 100)
                        overall = ((idx - 1) + file_progress) / total_files * 100

                        if progress % 5 == 0 or progress == 100:
                            app_db.update_upload_job(
                                job_id, phase=phase, phase_progress=progress,
                                phase_detail=detail, chunks_processed=chunks_done, chunks_total=chunks_total,
                            )

                        self._broadcast_event(ProgressEvent(
                            job_id=job_id, event_type="phase_progress", phase=phase,
                            current_file=safe_filename, file_index=idx, total_files=total_files,
                            phase_progress=progress, phase_detail=detail,
                            chunks_processed=chunks_done, chunks_total=chunks_total, overall_percent=overall,
                        ))

                    # Index the document
                    doc_metadata = indexer.index_document_with_progress(
                        dest_path, safe_filename, progress_callback=progress_callback,
                    )

                    collection_service.add_document(collection_id, doc_metadata.document_id)

                    results["documents_processed"] += 1
                    results["total_pages"] += doc_metadata.total_pages
                    results["total_chunks"] += doc_metadata.total_chunks
                    results["document_ids"].append(doc_metadata.document_id)

                    app_db.update_upload_job(job_id, processed_files=idx, phase=UploadPhase.COMPLETED.value)

                    self._broadcast_event(ProgressEvent(
                        job_id=job_id, event_type="file_complete", phase=UploadPhase.COMPLETED.value,
                        current_file=safe_filename, file_index=idx, total_files=total_files,
                        overall_percent=(idx / total_files) * 100, chunks_total=doc_metadata.total_chunks,
                    ))

                except Exception as e:
                    logger.error(f"Failed to index {safe_filename}: {e}")
                    results["failed_files"].append({"filename": safe_filename, "error": str(e)})
                    self._broadcast_event(ProgressEvent(
                        job_id=job_id, event_type="file_error", phase=UploadPhase.FAILED.value,
                        current_file=safe_filename, file_index=idx, total_files=total_files, error=str(e),
                    ))
                    # Clean up copied file
                    try:
                        if dest_path.exists():
                            dest_path.unlink()
                    except Exception:
                        pass

            if results["documents_processed"] > 0:
                indexer.save_index()

            app_db.update_upload_job(
                job_id, status="completed", processed_files=total_files,
                current_file=None, phase=UploadPhase.COMPLETED.value,
                result_summary=json.dumps(results),
            )

            self._broadcast_event(ProgressEvent(
                job_id=job_id, event_type="job_complete", phase=UploadPhase.COMPLETED.value,
                total_files=total_files, overall_percent=100.0,
            ))

            logger.info(f"Repo index job {job_id} completed: {results['documents_processed']}/{total_files} files")

        except Exception as e:
            logger.error(f"Repo index job {job_id} failed: {e}")
            app_db.update_upload_job(
                job_id, status="failed", phase=UploadPhase.FAILED.value,
                error=str(e), result_summary=json.dumps(results),
            )
            self._broadcast_event(ProgressEvent(
                job_id=job_id, event_type="job_error", phase=UploadPhase.FAILED.value, error=str(e),
            ))

        finally:
            with self._lock:
                if job_id in self._active_threads:
                    del self._active_threads[job_id]
                if job_id in self._cancel_flags:
                    del self._cancel_flags[job_id]


# Global instance
upload_service = UploadService()
