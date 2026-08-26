"""Document upload, indexing, and management endpoints."""

import logging
import json
import asyncio
from typing import List
from pathlib import Path
import shutil

from fastapi import HTTPException, UploadFile, File, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from config import settings
from services.document_extractor import DocumentExtractor
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from models.schemas import (
    UploadResponse,
    UploadJobResponse,
    DocumentListResponse,
    DocumentMetadata,
    DocumentChunksResponse,
    DocumentChunkView,
    RepoUploadRequest,
    RepoUploadResponse,
)
from services.upload_service import upload_service
from services.form_field_extractor import (
    extract_form_fields,
    estimate_form_likelihood,
    is_form_like_text,
)

from fastapi import APIRouter
from api.deps import (
    get_indexer,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/documents/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and index documents (PDF, TXT, DOCX, CSV)",
    tags=["documents"],
)
def upload_documents(  # sync: extraction+embedding run in FastAPI's threadpool, not on the event loop
    files: List[UploadFile] = File(...),
    collection_id: str = "default",
) -> UploadResponse:
    """
    Upload one or more documents and automatically index their contents.

    Supported formats: PDF, TXT, DOCX, CSV

    Args:
        files: List of files to upload
        collection_id: Collection to add documents to (default: "default")

    Returns metadata about the indexed documents including page and chunk counts.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided",
        )

    # Get indexer for the collection
    try:
        indexer = get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    # Get document directory for this collection
    document_dir = indexer_manager.get_documents_path(collection_id)

    # Validate all files have supported extensions and safe names
    SUPPORTED_EXTENSIONS = DocumentExtractor.SUPPORTED_EXTENSIONS
    for file in files:
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Every uploaded file must have a filename",
            )
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File {file.filename} has unsupported type. Supported: PDF, TXT, DOCX, CSV, XLSX, XLS, MD, JSON, JSONL, and source code files",
            )

    indexed_docs = []
    failed_docs = []
    total_pages = 0
    total_chunks = 0

    for file in files:
        file_path = None  # per-iteration: the except block must never see a previous file's path
        try:
            # Save uploaded file to collection's document directory
            # Preserve relative path context by replacing separators with underscores
            # This handles folder uploads where file.filename may be "src/utils/helper.py"
            # Converting to "src_utils_helper.py" prevents collisions and preserves context
            raw_filename = file.filename.replace('\\', '/').lstrip('/')
            if '/' in raw_filename:
                # Folder upload - flatten path to safe filename
                safe_filename = raw_filename.replace('/', '_')
            else:
                # Single file upload - use as-is
                safe_filename = raw_filename
            # Neutralize path traversal / reserved names that survive flattening
            if safe_filename in ("..", ".") or safe_filename.startswith(".."):
                safe_filename = safe_filename.replace("..", "_")
            file_path = document_dir / safe_filename
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            logger.info(f"Saved uploaded file: {safe_filename} to collection {collection_id}")

            # Index the document
            doc_metadata = indexer.index_document(file_path, safe_filename)

            # Register document with collection
            collection_service.add_document(collection_id, doc_metadata.document_id)

            indexed_docs.append(doc_metadata.document_id)
            total_pages += doc_metadata.total_pages
            total_chunks += doc_metadata.total_chunks

        except Exception as e:
            logger.error(f"Failed to index {file.filename}: {e}")
            failed_docs.append({"filename": file.filename, "error": str(e)})
            # Clean up this file's partial save if indexing failed
            if file_path is not None:
                try:
                    file_path.unlink(missing_ok=True)
                except Exception as cleanup_err:
                    logger.warning(f"Could not remove partial upload {file_path}: {cleanup_err}")
            # Continue processing remaining files

    # Persist the index if any documents were successfully indexed
    if indexed_docs:
        indexer.save_index()

    # Build response message
    if failed_docs and not indexed_docs:
        # All files failed
        error_details = "; ".join([f"{f['filename']}: {f['error']}" for f in failed_docs])
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"All files failed to index. Errors: {error_details}",
        )

    if failed_docs:
        # Partial success
        failed_names = [f["filename"] for f in failed_docs]
        message = f"Indexed {len(indexed_docs)} document(s) to collection '{collection_id}'. Failed: {', '.join(failed_names)}"
    else:
        # Full success
        message = f"Successfully indexed {len(indexed_docs)} document(s) to collection '{collection_id}'"

    return UploadResponse(
        message=message,
        documents_processed=len(indexed_docs),
        total_pages=total_pages,
        total_chunks=total_chunks,
        document_ids=indexed_docs,
    )


@router.post(
    "/documents/upload-staged",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Stage uploads for background indexing (save only, no indexing)",
    tags=["documents"],
)
def stage_uploads(  # sync: disk writes run in FastAPI's threadpool
    files: List[UploadFile] = File(...),
    collection_id: str = "default",
) -> dict:
    """
    Save uploaded files into the collection's documents directory WITHOUT
    indexing them, and return the stored paths.

    This is the transfer half of background uploads: the browser streams
    batches here (fast — no embedding in the request), then submits ONE
    /documents/index-local-async job over the returned paths. The browser
    only has to stay open for the transfer; indexing continues server-side.

    Filename handling matches /documents/upload (folder paths flattened,
    traversal neutralized); a re-staged filename overwrites the previous
    staged copy, mirroring the sync endpoint.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided",
        )
    try:
        get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    document_dir = indexer_manager.get_documents_path(collection_id)
    SUPPORTED_EXTENSIONS = DocumentExtractor.SUPPORTED_EXTENSIONS

    staged = []
    failed = []
    for file in files:
        if not file.filename:
            failed.append({"filename": "", "error": "missing filename"})
            continue
        if Path(file.filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
            failed.append({"filename": file.filename, "error": "unsupported type"})
            continue
        raw_filename = file.filename.replace('\\', '/').lstrip('/')
        safe_filename = raw_filename.replace('/', '_') if '/' in raw_filename else raw_filename
        if safe_filename in ("..", ".") or safe_filename.startswith(".."):
            safe_filename = safe_filename.replace("..", "_")
        file_path = document_dir / safe_filename
        try:
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
            staged.append({"filename": safe_filename, "path": str(file_path)})
        except Exception as e:
            logger.error(f"Failed to stage {file.filename}: {e}")
            failed.append({"filename": file.filename, "error": str(e)})

    return {"staged": staged, "failed": failed, "collection_id": collection_id}


@router.get(
    "/documents/upload/{job_id}/status",
    response_model=UploadJobResponse,
    summary="Get upload job status",
    tags=["documents"],
)
async def get_upload_status(job_id: int) -> UploadJobResponse:
    """
    Get the status of a background upload job.

    Poll this endpoint every 1-2 seconds while status is 'pending' or 'running'.

    Args:
        job_id: The job ID returned from /documents/upload-async

    Returns:
        Current job status including progress percentage
    """
    job_status = upload_service.get_job_status(job_id)

    if not job_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload job {job_id} not found",
        )

    return UploadJobResponse(
        job_id=job_status["job_id"],
        collection_id=job_status["collection_id"],
        status=job_status["status"],
        total_files=job_status["total_files"],
        processed_files=job_status["processed_files"],
        current_file=job_status["current_file"],
        progress_percent=job_status["progress_percent"],
        error=job_status["error"],
        result_summary=job_status["result_summary"],
        started_at=job_status["started_at"],
        completed_at=job_status["completed_at"],
        # v4.0: Granular progress
        phase=job_status.get("phase"),
        phase_progress=job_status.get("phase_progress"),
        phase_detail=job_status.get("phase_detail"),
        chunks_processed=job_status.get("chunks_processed"),
        chunks_total=job_status.get("chunks_total"),
    )


@router.get(
    "/documents/upload/active",
    response_model=List[UploadJobResponse],
    summary="Get all active upload jobs",
    tags=["documents"],
)
async def get_active_upload_jobs() -> List[UploadJobResponse]:
    """
    Get all active (pending/running) upload jobs.

    Use this endpoint on page load to restore the notification bell state.

    Returns:
        List of active upload jobs
    """
    from services.app_database import app_db

    active_jobs = app_db.get_all_active_upload_jobs()

    result = []
    for job in active_jobs:
        # Calculate progress
        progress = 0
        if job["total_files"] > 0:
            progress = round((job["processed_files"] / job["total_files"]) * 100, 1)

        result.append(UploadJobResponse(
            job_id=job["id"],
            collection_id=job["collection_id"],
            status=job["status"],
            total_files=job["total_files"],
            processed_files=job["processed_files"],
            current_file=job["current_file"],
            progress_percent=progress,
            error=job["error"],
            result_summary=None,  # Don't parse JSON here for list view
            started_at=job["started_at"],
            completed_at=job["completed_at"],
            # v4.0: Granular progress
            phase=job.get("phase"),
            phase_progress=job.get("phase_progress"),
            phase_detail=job.get("phase_detail"),
            chunks_processed=job.get("chunks_processed"),
            chunks_total=job.get("chunks_total"),
        ))

    return result


@router.get(
    "/documents/upload/{job_id}/stream",
    summary="Stream real-time upload progress via SSE",
    tags=["documents"],
)
async def stream_upload_progress(job_id: int):
    """
    Stream real-time progress updates for an upload job via Server-Sent Events (SSE).

    This endpoint provides instant updates without polling. Connect using EventSource
    in JavaScript to receive progress events as they happen.

    Event types:
    - file_start: A new file is starting to process
    - phase_progress: Progress within a phase (extracting, chunking, embedding, saving)
    - file_complete: A file finished processing
    - file_error: A file failed to process
    - job_complete: All files processed successfully
    - job_error: Job failed with error
    - job_cancelled: Job was cancelled by user

    Example JavaScript:
    ```javascript
    const eventSource = new EventSource(`/documents/upload/${jobId}/stream`);
    eventSource.addEventListener('phase_progress', (e) => {
        const data = JSON.parse(e.data);
        console.log(`${data.phase}: ${data.phase_progress}%`);
    });
    eventSource.addEventListener('job_complete', () => {
        eventSource.close();
    });
    ```

    Args:
        job_id: The job ID returned from /documents/upload-async

    Returns:
        SSE stream of progress events
    """
    import queue

    # Check job exists
    job_status = upload_service.get_job_status(job_id)
    if not job_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload job {job_id} not found",
        )

    # Subscribe to events
    event_queue = upload_service.subscribe_to_events(job_id)

    async def event_generator():
        """Generate SSE events from the queue."""
        try:
            # Send initial status
            initial_data = {
                "job_id": job_id,
                "event_type": "connected",
                "phase": job_status.get("phase") or "pending",
                "overall_percent": job_status.get("progress_percent", 0),
                "current_file": job_status.get("current_file"),
                "file_index": job_status.get("processed_files", 0),
                "total_files": job_status.get("total_files", 0),
            }
            yield f"event: connected\ndata: {json.dumps(initial_data)}\n\n"

            # If job is already complete, send final event and close
            if job_status["status"] in ("completed", "failed", "cancelled"):
                final_data = {
                    "job_id": job_id,
                    "event_type": f"job_{job_status['status']}",
                    "phase": job_status["status"],
                }
                yield f"event: job_{job_status['status']}\ndata: {json.dumps(final_data)}\n\n"
                return

            # Stream events from queue
            while True:
                try:
                    # Use asyncio to check queue with timeout
                    event = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: event_queue.get(timeout=30)
                    )

                    # Send SSE event
                    yield event.to_sse()

                    # Check for terminal events
                    if event.event_type in ("job_complete", "job_error", "job_cancelled"):
                        break

                except queue.Empty:
                    # Send keepalive ping every 30 seconds
                    yield ": keepalive\n\n"

        except asyncio.CancelledError:
            # Client disconnected
            pass
        finally:
            # Unsubscribe from events
            upload_service.unsubscribe_from_events(job_id, event_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.post(
    "/documents/upload/{job_id}/cancel",
    summary="Cancel a running upload job",
    tags=["documents"],
)
async def cancel_upload_job(job_id: int):
    """
    Request cancellation of a running upload job.

    The job will be marked as cancelled after the current file finishes processing.
    Already-indexed files will remain in the collection.

    Args:
        job_id: The job ID to cancel

    Returns:
        Cancellation status
    """
    # Check job exists and is active
    job_status = upload_service.get_job_status(job_id)
    if not job_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload job {job_id} not found",
        )

    if job_status["status"] not in ("pending", "running"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job {job_id} is not active (status: {job_status['status']})",
        )

    # Request cancellation - try normal cancellation first, then force if thread not found
    cancelled = upload_service.cancel_job(job_id)

    if cancelled:
        return {
            "message": f"Cancellation requested for job {job_id}",
            "job_id": job_id,
            "status": "cancelling",
        }

    # Thread not in active list - try force cancellation for orphaned jobs
    logger.warning(f"Job {job_id} thread not found, attempting force cancellation")
    force_cancelled = upload_service.cancel_job(job_id, force=True)

    if force_cancelled:
        return {
            "message": f"Job {job_id} force-cancelled (thread was not active)",
            "job_id": job_id,
            "status": "cancelled",
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel job {job_id}",
        )


@router.post(
    "/documents/upload-repo",
    response_model=RepoUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload and index a code repository or folder (async)",
    tags=["documents"],
)
def upload_repository(  # sync: extraction+embedding run in FastAPI's threadpool, not on the event loop
    request: RepoUploadRequest,
) -> RepoUploadResponse:
    """
    Scan a local repository or folder and start indexing in the background.

    This endpoint returns immediately with a job_id. Use GET /upload-jobs/{job_id}
    to track progress.

    **Supported file types:**
    - Documents: PDF, TXT, DOCX, CSV, MD, JSON
    - Code: Python, JavaScript, TypeScript, C#, Java, Go, Rust, C/C++, PHP, Ruby, Swift, Kotlin, Scala
    - Legacy: Pascal/Delphi, Modula-2, Assembly

    **Code-aware chunking:**
    Code files are intelligently chunked to preserve symbol boundaries
    (procedures, functions, classes, records, etc.) for better RAG performance.

    Args:
        request: Repository upload configuration including:
            - path: Local filesystem path to scan
            - collection_id: Target collection (default: "default")
            - recursive: Whether to scan subdirectories (default: True)
            - file_extensions: List of extensions to include (optional)
            - exclude_patterns: Glob patterns to exclude

    Returns:
        Response with job_id for tracking progress
    """
    repo_path = Path(request.path)

    if not repo_path.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path does not exist: {request.path}",
        )

    if not repo_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path is not a directory: {request.path}",
        )

    # Verify collection exists
    try:
        get_indexer(request.collection_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    # Build file extensions list from include_patterns or use defaults
    file_extensions = None
    if request.include_patterns:
        # Convert glob patterns like "*.py" to extensions like ".py"
        file_extensions = []
        for pattern in request.include_patterns:
            if pattern.startswith("*."):
                file_extensions.append("." + pattern[2:].lower())
    elif hasattr(request, 'file_extensions') and request.file_extensions:
        file_extensions = request.file_extensions

    try:
        # Start background job
        job_id = upload_service.start_repo_index(
            repo_path=request.path,
            collection_id=request.collection_id,
            recursive=request.recursive,
            file_extensions=file_extensions,
            exclude_patterns=request.exclude_patterns,
        )

        return RepoUploadResponse(
            message=f"Repository indexing started. Use GET /upload-jobs/{job_id} to track progress.",
            job_id=job_id,
            files_found=0,  # Will be updated as job progresses
        )

    except ValueError as e:
        # No files found
        return RepoUploadResponse(
            message=str(e),
            files_found=0,
        )
    except RuntimeError as e:
        # Job already running
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

class IndexLocalRequest(BaseModel):
    """Request body for indexing local files."""
    file_path: str
    collection_id: str = "default"
    copy_to_library: bool = False  # If True, copy file to data/documents/ instead of indexing in-place


@router.post(
    "/documents/index-local",
    response_model=DocumentMetadata,
    status_code=status.HTTP_201_CREATED,
    summary="Index a local file without uploading",
    tags=["documents"],
)
def index_local_file(request: IndexLocalRequest) -> DocumentMetadata:  # sync: indexing runs in the threadpool
    """
    Index a file directly from the local filesystem without copying it.

    The file remains in its original location and is read directly
    during indexing and when serving to users.

    This is useful for:
    - Large files that would be slow to upload
    - Files that shouldn't be duplicated
    - Local installs and mounted volumes where files are already on the server

    Args:
        request: Contains file_path and collection_id

    Returns:
        DocumentMetadata for the indexed document
    """

    SUPPORTED_EXTENSIONS = DocumentExtractor.SUPPORTED_EXTENSIONS

    path = Path(request.file_path)

    # Validate path exists
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {request.file_path}",
        )

    # Validate it's a file
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not a file: {request.file_path}",
        )

    # Validate extension
    file_ext = path.suffix.lower()
    if file_ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {file_ext}. Supported: PDF, TXT, DOCX, CSV, XLSX, XLS, MD, JSON, JSONL, and code files",
        )

    # Get indexer for the collection
    try:
        indexer = get_indexer(request.collection_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    try:
        if request.copy_to_library:
            # Copy file to data/documents/ directory first, then index
            document_dir = settings.data_dir / "documents"
            document_dir.mkdir(parents=True, exist_ok=True)
            dest_path = document_dir / path.name

            # Handle duplicate filenames
            counter = 1
            while dest_path.exists():
                stem = path.stem
                suffix = path.suffix
                dest_path = document_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            shutil.copy2(path, dest_path)
            logger.info(f"Copied file to library: {path} -> {dest_path}")

            # Index from the copied location
            doc_metadata = indexer.index_document(dest_path, dest_path.name)

            # Standard upload behavior - no source_path reference
            doc_metadata.source_type = "upload"

        else:
            # Index directly from source path (no copy)
            doc_metadata = indexer.index_document(path, path.name)

            # Update metadata with source path info
            indexer.vector_store.metadata_store.update_document_source(
                doc_metadata.document_id,
                source_path=str(path.absolute()),
                source_type="local_reference"
            )

            # Return updated metadata
            doc_metadata.source_path = str(path.absolute())
            doc_metadata.source_type = "local_reference"

        # Register document with collection
        collection_service.add_document(request.collection_id, doc_metadata.document_id)

        # Persist the index
        indexer.save_index()

        logger.info(f"Indexed local file: {path} -> {doc_metadata.document_id} (copy={request.copy_to_library})")
        return doc_metadata

    except Exception as e:
        logger.error(f"Failed to index local file {request.file_path}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to index file: {str(e)}",
        )


class IndexLocalAsyncRequest(BaseModel):
    """Request body for async local file indexing."""
    file_paths: List[str]
    collection_id: str = "default"
    copy_to_library: bool = False


@router.post(
    "/documents/index-local-async",
    response_model=UploadJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Index local files in background",
    tags=["documents"],
)
async def index_local_files_async(request: IndexLocalAsyncRequest) -> UploadJobResponse:
    """
    Start a background job to index local files.

    This is useful for indexing multiple files or large files without
    blocking the UI. Progress can be tracked via /documents/upload-status/{job_id}.

    Args:
        request: Contains list of file paths, collection_id, and copy_to_library flag

    Returns:
        UploadJobResponse with job_id for tracking progress
    """

    SUPPORTED_EXTENSIONS = DocumentExtractor.SUPPORTED_EXTENSIONS

    # Validate all paths exist and are supported
    valid_paths = []
    for file_path in request.file_paths:
        path = Path(file_path)
        if not path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {file_path}",
            )
        if not path.is_file():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Not a file: {file_path}",
            )
        file_ext = path.suffix.lower()
        if file_ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported file type: {file_ext} for {path.name}",
            )
        valid_paths.append(file_path)

    if not valid_paths:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid files to index",
        )

    try:
        job_id = upload_service.start_local_index(
            file_paths=valid_paths,
            collection_id=request.collection_id,
            copy_to_library=request.copy_to_library,
        )

        return UploadJobResponse(
            job_id=job_id,
            collection_id=request.collection_id,
            status="pending",
            total_files=len(valid_paths),
            processed_files=0,
            progress_percent=0.0,
        )

    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to start local index job: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start indexing: {str(e)}",
        )

@router.get(
    "/documents",
    response_model=DocumentListResponse,
    summary="List all indexed documents",
    tags=["documents"],
)
async def list_documents(
    collection_id: str = "default",
    limit: int = 0,
    offset: int = 0,
    q: str = "",
) -> DocumentListResponse:
    """
    List indexed documents with their metadata.

    Args:
        collection_id: Collection to list documents from (default: "default")
        limit: Page size. 0 (the default) returns everything — the
            backwards-compatible behavior for existing callers. Pass a
            positive limit for large collections: pagination happens in SQL
            and the per-document filesystem timestamp fallback is skipped,
            so a page over a 75k-document collection stays fast.
        offset: Page start (only meaningful with limit > 0).
        q: Optional filename substring filter (applies to paged mode; the
            total_documents in the response honors it).

    Returns filename, page count, and chunk count for each document, plus
    total_documents (the full filtered count, not the page size).
    """
    try:
        # Get indexer for the collection
        try:
            indexer = get_indexer(collection_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

        # Get document directory for this collection
        document_dir = indexer_manager.get_documents_path(collection_id)

        paged = limit > 0
        if paged:
            documents = indexer.list_documents_page(limit, offset=offset, q=q)
            total_documents = indexer.count_documents(q=q)
        else:
            documents = indexer.list_documents()
            total_documents = len(documents)

        # Convert to DocumentMetadata objects
        doc_metadata_list = []
        for doc in documents:
            # Determine file path based on source type
            # Handle None values from database by defaulting to "upload"
            source_type = doc.get("source_type") or "upload"
            source_path = doc.get("source_path")

            if source_type == "local_reference" and source_path:
                # Local reference - use source_path
                doc_path = Path(source_path)
            else:
                # Uploaded file - use documents directory
                doc_path = document_dir / doc["filename"]

            # Get timestamp from file modification time or upload_timestamp
            # Handle None values by defaulting to empty string. The stat()
            # fallback is skipped in paged mode: one filesystem stat per
            # document is exactly the kind of per-row cost pagination exists
            # to avoid, and rows missing upload_timestamp are legacy-rare.
            indexed_at = doc.get("upload_timestamp") or ""
            if not indexed_at and not paged and doc_path.exists():
                from datetime import datetime
                mtime = doc_path.stat().st_mtime
                indexed_at = datetime.fromtimestamp(mtime).isoformat()

            # Handle both field naming conventions (total_pages/num_pages, total_chunks/num_chunks)
            total_pages = doc.get("total_pages") or doc.get("num_pages") or 0
            total_chunks = doc.get("total_chunks") or doc.get("num_chunks") or 0

            doc_metadata = DocumentMetadata(
                document_id=doc["document_id"],
                filename=doc["filename"],
                total_pages=total_pages,
                total_chunks=total_chunks,
                indexed_at=indexed_at,
                source_type=source_type,
                source_path=source_path,
                injection_warnings=doc.get("injection_warnings"),
            )
            doc_metadata_list.append(doc_metadata)

        return DocumentListResponse(
            documents=doc_metadata_list,
            total_documents=total_documents,
        )

    except Exception as e:
        logger.error(f"Failed to list documents: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list documents: {str(e)}",
        )


@router.get(
    "/documents/{document_id}/pdf",
    summary="Download document file",
    tags=["documents"],
    response_class=FileResponse,
)
async def get_pdf(
    document_id: str,
    collection_id: str = "default",
):
    """
    Download the document file for a specific document.

    Args:
        document_id: Document ID
        collection_id: Collection containing the document (default: "default")

    For PDFs, the URL can include #page=N to open at a specific page in the browser.
    """
    try:
        # Get indexer for the collection
        try:
            indexer = get_indexer(collection_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

        # Get document directory for this collection
        document_dir = indexer_manager.get_documents_path(collection_id)

        # Get document metadata to find filename and source info
        doc_info = indexer.vector_store.metadata_store.get_document_info(document_id)

        if not doc_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document {document_id} not found",
            )

        # Determine file path based on source type
        if doc_info.get("source_type") == "local_reference" and doc_info.get("source_path"):
            # Local reference - serve from original location
            doc_path = Path(doc_info["source_path"])
            if not doc_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Source file no longer exists at: {doc_info['source_path']}. The file may have been moved or deleted.",
                )
        else:
            # Uploaded file - serve from collection's documents directory
            doc_path = document_dir / doc_info["filename"]
            if not doc_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Document file not found: {doc_info['filename']}",
                )

        # Determine media type based on file extension
        file_ext = Path(doc_info["filename"]).suffix.lower()
        media_types = {
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.csv': 'text/csv',
            '.md': 'text/markdown',
            '.json': 'application/json',
            # Code files - serve as plain text for browser preview
            '.pas': 'text/plain',
            '.dpr': 'text/plain',
            '.dpk': 'text/plain',
            '.pp': 'text/plain',
            '.inc': 'text/plain',
            '.dfm': 'text/plain',
            '.mod': 'text/plain',
            '.def': 'text/plain',
            '.mi': 'text/plain',
            '.asm': 'text/plain',
            '.s': 'text/plain',
        }
        media_type = media_types.get(file_ext, 'application/octet-stream')

        # Files that can be previewed inline in the browser
        inline_extensions = {'.pdf', '.txt', '.md', '.json', '.csv',
                            '.pas', '.dpr', '.dpk', '.pp', '.inc', '.dfm',
                            '.mod', '.def', '.mi', '.asm', '.s'}
        disposition = 'inline' if file_ext in inline_extensions else 'attachment'
        return FileResponse(
            path=doc_path,
            media_type=media_type,
            headers={
                "Content-Disposition": f'{disposition}; filename="{doc_info["filename"]}"'
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve PDF: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve PDF: {str(e)}",
        )


@router.get(
    "/documents/{document_id}/chunks",
    response_model=DocumentChunksResponse,
    summary="List indexed chunks for a document",
    tags=["documents"],
)
async def get_document_chunks(
    document_id: str,
    collection_id: str = "default",
    include_fields: bool = True,
    max_chunks: int = 1000,
) -> DocumentChunksResponse:
    """
    Return indexed chunks for a document, with optional OCR field/value extraction.

    Args:
        document_id: Document ID
        collection_id: Collection containing the document
        include_fields: If true, extract key/value fields for OCR/hybrid chunks
        max_chunks: Maximum number of chunks to return (safety limit)
    """
    try:
        # Get indexer for the collection
        try:
            indexer = get_indexer(collection_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

        metadata_store = indexer.vector_store.metadata_store
        doc_info = metadata_store.get_document_info(document_id)
        if not doc_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document {document_id} not found",
            )

        all_chunks = metadata_store.get_chunks_by_document(document_id)
        if max_chunks > 0:
            all_chunks = all_chunks[:max_chunks]

        chunk_views: List[DocumentChunkView] = []
        for chunk in all_chunks:
            chunk_method = chunk.get("extraction_method") or doc_info.get("extraction_method")
            extracted_fields = None
            form_score = None
            form_like = None
            field_extraction_applied = None

            if include_fields and chunk_method in {"ocr", "hybrid"}:
                text = chunk.get("text", "")
                form_score = estimate_form_likelihood(text)
                form_like = is_form_like_text(text)
                if form_like:
                    field_extraction_applied = True
                    extracted = extract_form_fields(text)
                    if extracted:
                        extracted_fields = extracted
                else:
                    field_extraction_applied = False

            chunk_views.append(
                DocumentChunkView(
                    chunk_id=chunk["chunk_id"],
                    page_number=chunk["page_number"],
                    chunk_index=chunk["chunk_index"],
                    text=chunk["text"],
                    source_format=chunk.get("source_format"),
                    extraction_method=chunk_method,
                    extracted_fields=extracted_fields,
                    form_score=round(form_score, 3) if form_score is not None else None,
                    form_like=form_like,
                    field_extraction_applied=field_extraction_applied,
                )
            )

        total_chunks = doc_info.get("num_chunks", len(chunk_views))
        extraction_method = doc_info.get("extraction_method")

        return DocumentChunksResponse(
            document_id=document_id,
            filename=doc_info.get("filename", ""),
            extraction_method=extraction_method,
            total_chunks=total_chunks,
            returned_chunks=len(chunk_views),
            chunks=chunk_views,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list chunks for document {document_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list document chunks: {str(e)}",
        )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a document from the index",
    tags=["documents"],
)
def delete_document(  # sync: FAISS rebuild on delete runs in the threadpool
    document_id: str,
    collection_id: str = "default",
):
    """
    Remove a document and all its chunks from the index.

    Args:
        document_id: Document ID to delete
        collection_id: Collection containing the document (default: "default")

    This will delete:
    - The document's metadata from the index
    - All chunks associated with the document
    - For uploaded files: the document file from the collection's documents directory
    - For local references: only the index (original file is NOT deleted)
    """
    try:
        # Get indexer for the collection
        try:
            indexer = get_indexer(collection_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

        # Get document directory for this collection
        document_dir = indexer_manager.get_documents_path(collection_id)

        # Get document metadata before deletion (including source info)
        doc_info = indexer.vector_store.metadata_store.get_document_info(document_id)

        if not doc_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document {document_id} not found",
            )

        # Delete from index
        num_deleted = indexer.delete_document(document_id)

        # Remove document from collection tracking
        collection_service.remove_document(collection_id, document_id)

        # Only delete file from filesystem if it was uploaded (not a local reference)
        file_deleted = False
        is_local_reference = doc_info.get("source_type") == "local_reference"

        if not is_local_reference:
            doc_path = document_dir / doc_info["filename"]
            if doc_path.exists():
                doc_path.unlink()
                file_deleted = True
                logger.info(f"Deleted document file: {doc_info['filename']}")
        else:
            logger.info(f"Skipping file deletion for local reference: {doc_info.get('source_path')}")

        # Persist the changes
        indexer.save_index()

        return {
            "message": f"Deleted document {document_id}",
            "filename": doc_info["filename"],
            "chunks_deleted": num_deleted,
            "file_deleted": file_deleted,
            "was_local_reference": is_local_reference,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete document: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete document: {str(e)}",
        )
