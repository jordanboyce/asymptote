"""
Asymptote — Self-hosted document search API.

Supports PDF, TXT, DOCX, and CSV files.
"""

import logging
import json
import tempfile
import asyncio
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any
from pathlib import Path
import shutil

from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import services.financial  # registers currency/percent types and financial role detector
from config import settings
from services.document_extractor import DocumentExtractor, is_code_file
from services.code_extractor import SUPPORTED_CODE_EXTENSIONS
from services.chunker import TextChunker
from services.embedder import EmbeddingService
from services.vector_store import VectorStore
from services.indexing import DocumentIndexer
from services.ai_service import AIService, AnthropicProvider, OpenAIProvider, create_provider, detect_ollama
from services.agent_tools import anthropic_tools, openai_tools
from services.config_manager import config_manager
from services.reindex_service import reindex_service
from services.collection_service import collection_service
from services.sharing_service import sharing_service
from services.indexer_manager import indexer_manager
from services.expertise_store import ExpertiseStore
expertise_store = ExpertiseStore()
from services.structured_chat import (
    _summarize_args as _summarize_for_log,
    build_structured_context,
    build_tool_use_instructions,
    collect_structured_tables,
    describe_tables_for_prompt,
    execute_tool_calls,
    format_results_for_prompt,
    parse_tool_calls,
    strip_tool_calls,
)
from middleware.user_context import get_current_user_id
from services.mcp_server import (
    MCP_CONFIG_FIELDS,
    build_mcp_export_payload,
    embedded_mcp_app,
    get_collection_mcp_profile,
    get_mcp_settings_payload,
    mcp_server_lifespan,
    save_collection_mcp_profile,
)
from services.privacy.redaction_middleware import redact_text_for_ai
from models.schemas import (
    UploadResponse,
    UploadJobResponse,
    SearchRequest,
    SearchResponse,
    DocumentListResponse,
    DocumentMetadata,
    DocumentChunksResponse,
    DocumentChunkView,
    AskRequest,
    AskResponse,
    AskSource,
    AIUsage,
    AIUsageDetail,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSource,
    RepoUploadRequest,
    RepoUploadResponse,
    ExpertisePack,
    ExpertisePackCreate,
    ExpertisePackUpdate,
    CollectionExpertiseResponse,
    SetCollectionExpertiseRequest,
)
from services.upload_service import upload_service
from services.form_field_extractor import (
    extract_form_fields,
    estimate_form_likelihood,
    is_form_like_text,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

CLOUD_AI_PROVIDERS = ("anthropic", "openai", "grok", "google", "github", "openai_compatible")
ALL_AI_PROVIDERS = CLOUD_AI_PROVIDERS + ("ollama",)


# Global services flag
_initialized = False


def get_indexer(collection_id: str = "default") -> DocumentIndexer:
    """Get indexer for a collection."""
    if not _initialized:
        raise HTTPException(
            status_code=503,
            detail="Service is still initializing. Please wait a moment and try again."
        )
    return indexer_manager.get_indexer(collection_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - initialize and cleanup services."""
    global _initialized

    async with mcp_server_lifespan():
        logger.info("Initializing Asymptote API...")

        # Initialize default collection's indexer to pre-load embedding model
        logger.info("Loading default collection indexer...")
        try:
            default_indexer = indexer_manager.get_indexer("default")
            total_chunks = default_indexer.vector_store.get_total_chunks()
            logger.info(f"Default collection indexed chunks: {total_chunks}")
        except Exception as e:
            logger.warning(f"Could not load default indexer: {e}")

        # Set up reload callback for re-indexing service (collection-aware)
        def reload_indexer(collection_id: str = "default"):
            """Reload an indexer's vector store from disk after re-indexing."""
            try:
                logger.info("=" * 60)
                logger.info(f"RELOAD CALLBACK TRIGGERED for collection: {collection_id}")
                logger.info("=" * 60)

                indexer_manager.reload_indexer(collection_id)

                stats = indexer_manager.get_collection_stats(collection_id)
                logger.info(f"Reload complete. Collection {collection_id} indexed chunks: {stats['total_chunks']}")
                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"RELOAD FAILED for collection {collection_id}: {e}", exc_info=True)

        reindex_service.reload_callback = reload_indexer

        _initialized = True

        logger.info("Asymptote API ready")
        logger.info(f"Data directory: {settings.data_dir}")
        logger.info(f"Embedded MCP server: {'enabled' if settings.enable_mcp else 'disabled'}")

        yield

        # Cleanup on shutdown
        logger.info("Shutting down Asymptote API...")
        indexer_manager.save_all()
        logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Asymptote API",
    description="Self-hosted semantic search for documents (PDF, TXT, DOCX, CSV)",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prepare static directory
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)


# Web Interface

@app.get("/", response_class=HTMLResponse, tags=["ui"])
async def web_interface():
    """Serve the web interface."""
    index_path = static_dir / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"), status_code=200)
    else:
        return HTMLResponse(
            content="<h1>Asymptote API</h1><p>Web interface not found. Visit <a href='/docs'>/docs</a> for API documentation.</p>",
            status_code=200
        )


# API Endpoints


class EmbedRequest(BaseModel):
    text: str


@app.post("/api/embed", tags=["search"])
async def embed_text(request: EmbedRequest):
    """Return a normalized embedding vector for text (used for client-side semantic cache comparison)."""
    import numpy as np
    try:
        indexer = indexer_manager.get_indexer("default")
        embedding = indexer.embedding_service.embed_query(request.text)
        norm = float(np.linalg.norm(embedding))
        if norm > 0:
            embedding = embedding / norm
        return {
            "embedding": embedding.tolist(),
            "dim": len(embedding),
            "model": settings.embedding_model,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding failed: {str(e)}")


@app.get("/health", tags=["health"])
async def health(collection_id: str = "default"):
    """Health check endpoint."""
    try:
        stats = indexer_manager.get_collection_stats(collection_id)
        return {
            "status": "healthy",
            "collection_id": collection_id,
            "indexed_chunks": stats["total_chunks"],
            "total_documents": stats["total_documents"],
            "total_pages": stats["total_pages"],
        }
    except Exception as e:
        return {
            "status": "healthy",
            "indexed_chunks": 0,
            "error": str(e),
        }


@app.post(
    "/documents/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and index documents (PDF, TXT, DOCX, CSV)",
    tags=["documents"],
)
async def upload_documents(
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

    # Validate all files have supported extensions
    SUPPORTED_EXTENSIONS = DocumentExtractor.SUPPORTED_EXTENSIONS
    for file in files:
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File {file.filename} has unsupported type. Supported: PDF, TXT, DOCX, CSV, XLSX, XLS, MD, JSON, JSONL, and code files (Pascal, Delphi, Modula-2, Assembly)",
            )

    indexed_docs = []
    failed_docs = []
    total_pages = 0
    total_chunks = 0

    for file in files:
        try:
            # Save uploaded file to collection's document directory
            # Preserve relative path context by replacing separators with underscores
            # This handles folder uploads where file.filename may be "src/utils/helper.pas"
            # Converting to "src_utils_helper.pas" prevents collisions and preserves context
            raw_filename = file.filename.replace('\\', '/').lstrip('/')
            if '/' in raw_filename:
                # Folder upload - flatten path to safe filename
                safe_filename = raw_filename.replace('/', '_')
            else:
                # Single file upload - use as-is
                safe_filename = raw_filename
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
            # Clean up the saved file if indexing failed
            try:
                if file_path.exists():
                    file_path.unlink()
            except Exception:
                pass
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


@app.post(
    "/documents/upload-async",
    response_model=UploadJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload documents asynchronously (for large files)",
    tags=["documents"],
)
async def upload_documents_async(
    files: List[UploadFile] = File(...),
    collection_id: str = "default",
) -> UploadJobResponse:
    """
    Upload documents for background processing. Returns immediately with a job ID.

    Use this endpoint for large files to avoid blocking the API.
    Poll /documents/upload/{job_id}/status to check progress.

    Supported formats: PDF, TXT, DOCX, CSV, MD, JSON, and code files.

    Args:
        files: List of files to upload
        collection_id: Collection to add documents to (default: "default")

    Returns:
        Job details including job_id for tracking progress
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided",
        )

    # Validate collection exists
    try:
        get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    # Validate all files have supported extensions
    SUPPORTED_EXTENSIONS = DocumentExtractor.SUPPORTED_EXTENSIONS
    for file in files:
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File {file.filename} has unsupported type. Supported: PDF, TXT, DOCX, CSV, XLSX, XLS, MD, JSON, JSONL, and code files",
            )

    # Create temp directory for staging files
    temp_dir = Path(tempfile.mkdtemp(prefix="asymptote_upload_"))

    staged_files = []
    try:
        for file in files:
            # Preserve relative path context by replacing separators
            raw_filename = file.filename.replace('\\', '/').lstrip('/')
            if '/' in raw_filename:
                safe_filename = raw_filename.replace('/', '_')
            else:
                safe_filename = raw_filename

            # Save to temp location (use thread to avoid blocking)
            temp_path = temp_dir / safe_filename

            async def save_file(file_obj, path):
                def _save():
                    with open(path, "wb") as f:
                        shutil.copyfileobj(file_obj.file, f)
                await asyncio.to_thread(_save)

            await save_file(file, temp_path)

            staged_files.append({
                "temp_path": str(temp_path),
                "filename": safe_filename,
            })

        # Start background upload job (runs in separate thread)
        job_id = upload_service.start_upload(staged_files, collection_id)

        # Get initial job status
        job_status = upload_service.get_job_status(job_id)

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
        )

    except RuntimeError as e:
        # Clean up temp files if job couldn't start
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except Exception as e:
        # Clean up temp files on any error
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start upload: {str(e)}",
        )


@app.get(
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


@app.get(
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


@app.get(
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


@app.post(
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


@app.post(
    "/documents/upload-repo",
    response_model=RepoUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload and index a code repository or folder (async)",
    tags=["documents"],
)
async def upload_repository(
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


@app.post(
    "/search",
    response_model=SearchResponse,
    summary="Search indexed documents",
    tags=["documents"],
)
async def search_documents(
    search_request: SearchRequest,
    request: Request,
    collection_id: str = "default",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
) -> SearchResponse:
    """
    Perform semantic similarity search over indexed documents.

    Args:
        search_request: Search query and options
        collection_id: Collection to search (default: "default")

    Optionally enable AI enhancements by including 'ai' options in the request body.
    Supports Anthropic, OpenAI, and Ollama providers.

    For cloud providers (Anthropic, OpenAI):
    - Pass API key via X-AI-Key header
    - Set provider in request body ai.provider

    For Ollama:
    - Pass model name via X-Ollama-Model header (e.g., llama3.2)
    - Set provider to 'ollama' in request body ai.provider
    - No API key required
    """
    try:
        import time
        start_time = time.time()

        # Get indexer for the collection
        try:
            indexer = get_indexer(collection_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

        # Build AI service if AI options requested
        ai_service = None
        ai_options = search_request.ai
        ai_provider_used = None

        if ai_options:
            try:
                if ai_options.provider == "ollama":
                    model = x_ai_model or x_ollama_model or "llama3.2"
                    extra: dict = {"model": model}
                    if x_ai_base_url:
                        extra["base_url"] = x_ai_base_url
                    provider = create_provider("ollama", **extra)
                    ai_service = AIService(provider=provider)
                    ai_provider_used = "ollama"
                else:
                    if not x_ai_key and ai_options.provider != "openai_compatible":
                        logger.warning(f"AI key required for provider: {ai_options.provider}")
                    else:
                        extra = {}
                        model = x_ai_model or x_anthropic_model or x_openai_model
                        if model:
                            extra["model"] = model
                        if x_ai_base_url:
                            extra["base_url"] = x_ai_base_url
                        provider = create_provider(ai_options.provider, x_ai_key, **extra)
                        ai_service = AIService(provider=provider)
                        ai_provider_used = ai_options.provider
            except Exception as e:
                logger.warning(f"Failed to create AI service: {e}")

        # Build collection overview so AI synthesis can answer meta-questions
        # like "how many sources are in this collection?"
        collection_overview = None
        structured_context_str = None
        skip_filenames: set = set()
        if ai_service and ai_options and ai_options.synthesize:
            try:
                collection_overview = _build_collection_overview([collection_id])
            except Exception as e:
                logger.warning(f"Failed to build collection overview for search: {e}")

            # Inline small CSV/XLSX tables in full so synthesis never has to
            # rely on top-K chunk recall for numeric questions. Large tables
            # stay out of synthesis (users should use the Chat tab which runs
            # the SQL tool loop for those).
            try:
                s_tables, s_stores = collect_structured_tables([collection_id])
                if s_tables:
                    ctx = build_structured_context(s_tables, s_stores)
                    if ctx["inline_block"]:
                        structured_context_str = ctx["inline_block"]
                        skip_filenames = ctx["inlined_filenames"]
            except Exception as e:
                logger.warning(f"Failed to build structured context for search: {e}")

        search_result = indexer.search(
            query=search_request.query,
            top_k=search_request.top_k,
            ai_service=ai_service,
            ai_options=ai_options,
            mode=search_request.mode,
            semantic_weight=search_request.semantic_weight,
            collection_overview=collection_overview,
            structured_context=structured_context_str,
            skip_filenames=skip_filenames or None,
        )

        results = search_result["results"]

        # Add URLs to each result (include collection_id for proper routing)
        base_url = str(request.base_url).rstrip('/')
        for result in results:
            result.pdf_url = f"{base_url}/documents/{result.document_id}/pdf?collection_id={collection_id}"
            result.page_url = f"{base_url}/documents/{result.document_id}/pdf?collection_id={collection_id}#page={result.page_number}"

        execution_time_ms = int((time.time() - start_time) * 1000)

        # Save to search history
        from services.app_database import app_db
        try:
            results_json = json.dumps([{
                "document_id": r.document_id,
                "filename": r.filename,
                "page_number": r.page_number,
                "chunk_index": r.chunk_index,
                "text": r.text,
                "similarity": r.similarity,
                "pdf_url": r.pdf_url,
                "page_url": r.page_url
            } for r in results])

            app_db.add_search_history(
                query=search_request.query,
                top_k=search_request.top_k,
                results_count=len(results),
                ai_provider=ai_provider_used,
                ai_used=ai_service is not None,
                results_json=results_json,
                execution_time_ms=execution_time_ms
            )
        except Exception as e:
            logger.warning(f"Failed to save search history: {e}")

        return SearchResponse(
            query=search_request.query,
            results=results,
            total_results=len(results),
            synthesis=search_result.get("synthesis"),
            ai_usage=search_result.get("ai_usage"),
        )

    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
        )


from services.collection_overview import build_collection_overview as _build_collection_overview


@app.post("/api/chat", response_model=ChatResponse)
async def chat_with_documents(
    chat_request: ChatRequest,
    request: Request,
    collection_id: str = "default",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
) -> ChatResponse:
    """
    Chat with your indexed documents using conversational AI.

    Retrieves relevant document chunks based on the latest user message,
    then passes the context + conversation history to the selected AI provider.

    Supports:
    - scope='current': search only the specified collection
    - scope='all': search all collections and merge results by relevance
    - rerank=True: use AI to rerank retrieved context before generating response
    """
    try:
        # Extract the latest user message for context retrieval
        user_messages = [m for m in chat_request.messages if m.role == "user"]
        if not user_messages:
            raise HTTPException(status_code=400, detail="No user messages in conversation")

        latest_query = user_messages[-1].content

        # --- Build AI provider (needed before search for query reformulation) ---
        try:
            if chat_request.provider == "ollama":
                model = x_ai_model or x_ollama_model or "llama3.2"
                extra: dict = {"model": model}
                if x_ai_base_url:
                    extra["base_url"] = x_ai_base_url
                provider = create_provider("ollama", **extra)
            else:
                if not x_ai_key and chat_request.provider != "openai_compatible":
                    raise HTTPException(
                        status_code=400,
                        detail=f"API key required for {chat_request.provider}",
                    )
                extra = {}
                model = x_ai_model or x_anthropic_model or x_openai_model
                if model:
                    extra["model"] = model
                if x_ai_base_url:
                    extra["base_url"] = x_ai_base_url
                provider = create_provider(chat_request.provider, x_ai_key, **extra)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to initialize AI provider: {str(e)}")

        ai_service = AIService(provider=provider)

        # --- Reformulate the query using conversation history ---
        # Follow-up questions ("what date was that?") reference prior context that the
        # vector index has no access to. Rewriting them into self-contained queries
        # (e.g. "Maverick Adventure First Stop receipt purchase date") dramatically
        # improves retrieval accuracy on multi-turn conversations. Skip on single-turn
        # queries to avoid an unnecessary LLM call (and the failure mode when the
        # provider is unreachable).
        prior_messages = chat_request.messages[:-1]
        if prior_messages:
            history_for_reformulation = [
                {"role": m.role, "content": m.content} for m in prior_messages
            ]
            search_query = ai_service.reformulate_query(history_for_reformulation, latest_query)
        else:
            search_query = latest_query

        # --- Retrieve context chunks ---
        # Track which collection each result came from so URLs are correct
        context_results = []
        result_collection_ids = []  # parallel list to context_results

        if chat_request.scope == "all":
            # Search every collection and merge results by similarity score
            all_collections = collection_service.get_all_collections()
            for col in all_collections:
                col_id = col["id"]
                try:
                    col_indexer = get_indexer(col_id)
                    col_search = col_indexer.search(
                        query=search_query,
                        top_k=chat_request.top_k,
                        mode=chat_request.mode,
                    )
                    for r in col_search["results"]:
                        context_results.append(r)
                        result_collection_ids.append(col_id)
                except Exception as e:
                    logger.warning(f"Chat: search failed for collection '{col_id}': {e}")

            # Sort merged results by similarity score, keep top_k
            paired = sorted(
                zip(context_results, result_collection_ids),
                key=lambda x: x[0].similarity_score,
                reverse=True,
            )[:chat_request.top_k]
            context_results = [p[0] for p in paired]
            result_collection_ids = [p[1] for p in paired]
        else:
            # Single collection
            try:
                indexer = get_indexer(collection_id)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))

            search_result = indexer.search(
                query=search_query,
                top_k=chat_request.top_k,
                mode=chat_request.mode,
            )
            context_results = search_result["results"]
            result_collection_ids = [collection_id] * len(context_results)

        # --- Optionally rerank context chunks ---
        rerank_usage = None
        if chat_request.rerank and context_results:
            try:
                rerank_input = [
                    {
                        "index": i,
                        "filename": r.filename,
                        # Redact PII from snippets sent to the AI reranker.
                        "text_snippet": redact_text_for_ai(
                            r.text_snippet,
                            collection_id=result_collection_ids[i] if i < len(result_collection_ids) else None,
                            source_label="chat_rerank",
                        ),
                        "similarity_score": r.similarity_score,
                    }
                    for i, r in enumerate(context_results)
                ]
                rerank_result = ai_service.rerank_results(latest_query, rerank_input, chat_request.top_k)
                valid_indices = [
                    i for i in rerank_result["reranked_indices"]
                    if 0 <= i < len(context_results)
                ]
                if valid_indices:
                    context_results = [context_results[i] for i in valid_indices]
                    result_collection_ids = [result_collection_ids[i] for i in valid_indices]
                rerank_usage = rerank_result.get("usage")
            except Exception as e:
                logger.warning(f"Chat context reranking failed, using original order: {e}")

        # --- Collect structured CSV/XLSX tables early so we can both inline
        # the small ones as JSONL AND know which files to drop from chunks. ---
        if chat_request.scope == "all":
            overview_ids = [c["id"] for c in collection_service.get_all_collections()]
        else:
            overview_ids = [collection_id]
        structured_tables, structured_stores = collect_structured_tables(overview_ids)
        structured_ctx = build_structured_context(structured_tables, structured_stores) \
            if structured_tables else {
                "inline_block": "",
                "tool_tables": [],
                "inlined_filenames": set(),
                "inlined_document_ids": set(),
            }
        inlined_filenames = structured_ctx["inlined_filenames"]
        tool_tables = structured_ctx["tool_tables"]
        # Redact PII from the inlined JSONL table block before it enters the AI prompt.
        _raw_inline = structured_ctx["inline_block"]
        inline_block = (
            redact_text_for_ai(_raw_inline, collection_id=collection_id, source_label="chat_inline_tables")
            if _raw_inline
            else _raw_inline
        )

        # --- Format context and history ---
        # Drop chunks from files that are already fully inlined as JSONL: the
        # JSONL is authoritative, keeping duplicated (and possibly truncated)
        # chunk snippets would just confuse the model.
        filtered_results = [
            (r, cid) for (r, cid) in zip(context_results, result_collection_ids)
            if r.filename not in inlined_filenames
        ]
        context_parts = []
        for i, (result, _cid) in enumerate(filtered_results):
            # Redact PII from each snippet before it enters the AI system prompt.
            safe_snippet = redact_text_for_ai(
                result.text_snippet,
                collection_id=_cid,
                source_label="chat_context",
            )
            context_parts.append(
                f"[Source {i + 1}: {result.filename}, page {result.page_number}]\n{safe_snippet}"
            )
        context_text = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant context found."

        history_parts = []
        for msg in chat_request.messages[:-1]:
            prefix = "User" if msg.role == "user" else "Assistant"
            history_parts.append(f"{prefix}: {msg.content}")
        history_text = "\n\n".join(history_parts) if history_parts else "(Start of conversation)"

        # --- Build collection overview (handles meta-questions like "how many files") ---
        try:
            collection_overview = _build_collection_overview(overview_ids)
        except Exception as e:
            logger.warning(f"Failed to build collection overview: {e}")
            collection_overview = "(Collection overview unavailable.)"

        scope_note = "all collections" if chat_request.scope == "all" else "the current collection"

        # The agent always has the full toolkit available (search, table tools,
        # market data, etc.). Only the LARGE-table schema block is conditional
        # on tool_tables — small tables are already inlined as JSONL.
        tables_block = describe_tables_for_prompt(tool_tables) if tool_tables else ""
        tools_block = build_tool_use_instructions()
        agent_context = {
            "collection_id": collection_id,
            "scope": chat_request.scope,
        }

        base_system_parts = [
            f"You are a helpful assistant with access to a private document knowledge base ({scope_note}).",
            "Answer the user's question using the COLLECTION OVERVIEW, STRUCTURED TABLES "
            "(when provided), and RETRIEVED CONTEXT below.",
            "Use the COLLECTION OVERVIEW for meta-questions about the knowledge base itself "
            "(file counts, available documents, date ranges).",
        ]
        if inline_block:
            base_system_parts.append(
                "When STRUCTURED TABLES are included below, they are the FULL contents "
                "of CSV/XLSX files as JSONL — every row is present. For any numeric, "
                "sum, count, average, filter, date-range, or ranking question about "
                "those files, answer DIRECTLY from the JSONL rows and show your arithmetic. "
                "Do NOT guess from chunk snippets and do NOT assume data is missing."
            )
        if tool_tables:
            base_system_parts.append(
                "For questions about the LARGE tables listed under TOOL USE PROTOCOL, "
                "call the structured-query tools — do not estimate from row text."
            )
        base_system_parts.append(
            "Use RETRIEVED CONTEXT for prose/document questions, citing [Source N] as needed."
        )

        # --- Expertise Library injection (single-collection scope only) ---
        # Load packs attached to this collection and build the guidance block.
        # Skipped for "all" scope to avoid conflicting guidance across clients.
        expertise_block: str = ""
        if chat_request.scope != "all":
            try:
                attached_packs = expertise_store.get_packs_for_collection(collection_id)
                if attached_packs:
                    base_system_parts.append(
                        "When ADVISOR EXPERTISE is provided, follow its guidance, rules, and "
                        "frameworks as authoritative instructions for this analysis."
                    )
                    guidance_sections = "\n\n".join(
                        f"## {p.name}\n{(p.description + chr(10)) if p.description else ''}{p.body}".strip()
                        for p in attached_packs
                    )
                    expertise_block = (
                        "ADVISOR EXPERTISE (apply these frameworks when analyzing this portfolio):\n"
                        + guidance_sections
                    )
            except Exception as _ep_err:
                logger.warning("Failed to load expertise packs for collection %s: %s", collection_id, _ep_err)

        base_system = " ".join(base_system_parts)

        def compose_prompt(extra_suffix: str = "") -> str:
            parts = [
                base_system,
                "",
                f"COLLECTION OVERVIEW:\n{collection_overview}",
            ]
            if expertise_block:
                parts.extend(["", expertise_block])
            if inline_block:
                parts.extend(["", inline_block])
            parts.extend(["", f"RETRIEVED CONTEXT:\n{context_text}"])
            if tables_block:
                parts.extend(["", f"LARGE TABLES (use SQL tool calls):\n{tables_block}"])
            if tools_block:
                parts.extend(["", tools_block])
            parts.extend([
                "",
                f"CONVERSATION HISTORY:\n{history_text}",
                "",
                f"User: {latest_query}",
            ])
            if extra_suffix:
                parts.append(extra_suffix)
            parts.append("Assistant:")
            return "\n\n".join(parts)

        # --- Agentic tool-use loop ---
        # When the provider supports native tool-calling (Anthropic, OpenAI
        # family) we drive the messages API directly — the model can't
        # respond with prose-only once it's decided to call a tool, which
        # eliminates the "let me query that for you" dead-end failure mode.
        # Ollama and other providers without native tools fall back to the
        # ReAct-over-prose path below.
        executed_results: list[dict] = []
        total_input_tokens = 0
        total_output_tokens = 0
        model_used = ai_service.quality_model
        response_text = ""
        max_iterations = 8

        # Build system prompt (no conversation history/user — those go into
        # messages natively) for the native-tools path.
        system_parts_native = [base_system, f"COLLECTION OVERVIEW:\n{collection_overview}"]
        if expertise_block:
            system_parts_native.append(expertise_block)
        if inline_block:
            system_parts_native.append(inline_block)
        if context_text and context_text != "No relevant context found.":
            system_parts_native.append(f"PRE-RETRIEVED CONTEXT (optional primer):\n{context_text}")
        if tables_block:
            system_parts_native.append(f"LARGE TABLES AVAILABLE:\n{tables_block}")
        system_text_native = "\n\n".join(system_parts_native)

        if provider.supports_native_tools():
            is_anthropic = isinstance(provider, AnthropicProvider)
            tools_spec = anthropic_tools() if is_anthropic else openai_tools()

            # Seed messages with prior conversation + latest user query.
            messages: list[dict] = [
                {"role": m.role, "content": m.content}
                for m in chat_request.messages
            ]

            for iteration in range(max_iterations):
                logger.info("[agent] iter=%d provider=%s msgs=%d", iteration,
                            provider.__class__.__name__, len(messages))
                turn = provider.complete_with_tools(
                    messages=messages,
                    tools=tools_spec,
                    max_tokens=2048,
                    model=ai_service.quality_model,
                    system=system_text_native,
                )
                usage_iter = turn.get("usage", {}) or {}
                total_input_tokens += usage_iter.get("input_tokens", 0)
                total_output_tokens += usage_iter.get("output_tokens", 0)
                model_used = usage_iter.get("model", model_used)

                tool_calls = turn.get("tool_calls") or []
                thinking = turn.get("text") or ""
                logger.info(
                    "[agent] iter=%d stop=%s tool_calls=%d thinking=%r",
                    iteration, turn.get("stop_reason"), len(tool_calls),
                    thinking[:200] if thinking else "",
                )
                for tc in tool_calls:
                    logger.info("[agent]   -> %s %s", tc["name"], _summarize_for_log(tc.get("input") or {}))

                # Record any narration the model emitted alongside tool calls
                # so the UI can show the chain of thought.
                if tool_calls and thinking:
                    executed_results.append({
                        "tool": "_thinking",
                        "args": {"iteration": iteration},
                        "result": {"text": thinking},
                    })

                if not tool_calls:
                    response_text = thinking.strip()
                    break

                # Append the assistant turn verbatim (provider-native shape)
                # so the next call has the tool_use history.
                messages.append(turn["assistant_message"])

                # Execute each tool call and append tool results.
                adapted_calls = [
                    {"tool": tc["name"], **(tc.get("input") or {})}
                    for tc in tool_calls
                ]
                iter_results = execute_tool_calls(adapted_calls, agent_context=agent_context)
                executed_results.extend(iter_results)

                # Pair each call id with its result payload for the provider.
                import json as _json
                if is_anthropic:
                    tool_result_content = []
                    for tc, res in zip(tool_calls, iter_results):
                        payload = res.get("error") or res.get("result") or {}
                        tool_result_content.append({
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": _json.dumps(payload, default=str)[:60000],
                            **({"is_error": True} if res.get("error") else {}),
                        })
                    messages.append({"role": "user", "content": tool_result_content})
                else:
                    for tc, res in zip(tool_calls, iter_results):
                        payload = res.get("error") or res.get("result") or {}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": _json.dumps(payload, default=str)[:60000],
                        })

            if not response_text:
                # Hit iteration cap without a clean final turn — force one
                # more pass with no tools so we always return an answer.
                try:
                    turn = provider.complete_with_tools(
                        messages=messages,
                        tools=[],
                        max_tokens=2048,
                        model=ai_service.quality_model,
                        system=system_text_native
                        + "\n\nDo not call any more tools. Summarize the final answer.",
                    )
                    response_text = (turn.get("text") or "").strip()
                    usage_iter = turn.get("usage", {}) or {}
                    total_input_tokens += usage_iter.get("input_tokens", 0)
                    total_output_tokens += usage_iter.get("output_tokens", 0)
                    model_used = usage_iter.get("model", model_used)
                except Exception as e:
                    logger.warning(f"Agent final-answer pass failed: {e}")
                    response_text = "I ran several tool calls but couldn't settle on a final answer — please rephrase or narrow the question."

        else:
            # --- ReAct fallback for providers without native tools (Ollama, etc.) ---
            suffix = ""
            for iteration in range(5):
                prompt = compose_prompt(suffix)
                result = provider.complete(prompt=prompt, max_tokens=2048, model=ai_service.quality_model)
                raw_text = result["text"]
                usage_iter = result.get("usage", {}) or {}
                total_input_tokens += usage_iter.get("input_tokens", 0)
                total_output_tokens += usage_iter.get("output_tokens", 0)
                model_used = usage_iter.get("model", model_used)

                calls = parse_tool_calls(raw_text)
                thinking = strip_tool_calls(raw_text).strip()
                if calls and thinking:
                    executed_results.append({
                        "tool": "_thinking",
                        "args": {"iteration": iteration},
                        "result": {"text": thinking},
                    })
                if not calls:
                    response_text = raw_text.strip()
                    break
                iter_results = execute_tool_calls(calls, agent_context=agent_context)
                executed_results.extend(iter_results)
                suffix = (
                    (suffix + "\n\n" if suffix else "")
                    + f"Assistant (previous turn):\n{raw_text.strip()}\n\n"
                    + format_results_for_prompt(iter_results)
                    + "\n\nYou may call more tools, or produce the final answer. "
                    + "When done, write the answer with no <tool_call> blocks."
                )
            response_text = strip_tool_calls(response_text).strip()
        usage = {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "model": model_used,
        }

        features_used = ["chat"]
        if chat_request.rerank and rerank_usage:
            features_used.append("reranking")
        if executed_results:
            features_used.append("structured_tools")

        reranking_detail = None
        extra_input = 0
        extra_output = 0
        if rerank_usage:
            reranking_detail = AIUsageDetail(
                input_tokens=rerank_usage.get("input_tokens", 0),
                output_tokens=rerank_usage.get("output_tokens", 0),
                model=rerank_usage.get("model", ai_service.fast_model),
            )
            extra_input = rerank_usage.get("input_tokens", 0)
            extra_output = rerank_usage.get("output_tokens", 0)

        ai_usage = AIUsage(
            features_used=features_used,
            reranking=reranking_detail,
            synthesis=AIUsageDetail(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                model=usage.get("model", ai_service.quality_model),
            ),
            total_input_tokens=usage.get("input_tokens", 0) + extra_input,
            total_output_tokens=usage.get("output_tokens", 0) + extra_output,
        )

        # --- Build source list with URLs ---
        # Only show sources whose chunks actually made it into the prompt —
        # i.e. skip files that were inlined as authoritative JSONL.
        base_url = str(request.base_url).rstrip("/")
        sources = [
            ChatSource(
                filename=r.filename,
                page_number=r.page_number,
                text_snippet=r.text_snippet,
                similarity_score=r.similarity_score,
                document_id=r.document_id,
                pdf_url=f"{base_url}/documents/{r.document_id}/pdf?collection_id={col_id}",
                page_url=f"{base_url}/documents/{r.document_id}/pdf?collection_id={col_id}#page={r.page_number}",
            )
            for r, col_id in filtered_results
        ]

        return ChatResponse(
            message=ChatMessage(role="assistant", content=response_text),
            sources=sources,
            ai_usage=ai_usage,
            structured_results=executed_results or None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat failed: {str(e)}",
        )


# ---------------------------------------------------------------------------
# v4.4 — Streaming chat endpoint (SSE)
# ---------------------------------------------------------------------------
# Mirrors /api/chat but emits Server-Sent Events so the frontend can display
# live tool-call indicators and stream the final text token-by-token.
#
# Event types (each line: "data: <json>\n\n"):
#   tool_start  — a tool call is about to execute
#   tool_end    — tool call finished (result included)
#   thinking    — prose the model emitted between tool calls
#   text_delta  — one word/chunk of the final response
#   sources     — final source list
#   done        — completion marker + usage stats
#   error       — something went wrong

@app.post("/api/chat/stream", tags=["chat"])
async def chat_stream_endpoint(
    chat_request: ChatRequest,
    request: Request,
    collection_id: str = "default",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
):
    """
    Streaming version of /api/chat. Returns text/event-stream (SSE).

    Events are emitted as the agent works so the frontend can show live
    tool-call indicators and stream text as it arrives.
    """
    import json as _json

    async def generate():  # noqa: C901 (complexity fine for one function)
        try:
            # ---------- provider -------------------------------------------------
            try:
                if chat_request.provider == "ollama":
                    model_name = x_ai_model or x_ollama_model or "llama3.2"
                    extra: dict = {"model": model_name}
                    if x_ai_base_url:
                        extra["base_url"] = x_ai_base_url
                    provider = create_provider("ollama", **extra)
                else:
                    if not x_ai_key and chat_request.provider != "openai_compatible":
                        yield f"data: {_json.dumps({'type':'error','message':f'API key required for {chat_request.provider}'})}\n\n"
                        return
                    extra = {}
                    model_name = x_ai_model or x_anthropic_model or x_openai_model
                    if model_name:
                        extra["model"] = model_name
                    if x_ai_base_url:
                        extra["base_url"] = x_ai_base_url
                    provider = create_provider(chat_request.provider, x_ai_key, **extra)
            except Exception as e:
                yield f"data: {_json.dumps({'type':'error','message':f'Failed to initialize AI provider: {e}'})}\n\n"
                return

            ai_service = AIService(provider=provider)

            # ---------- context retrieval ----------------------------------------
            user_messages = [m for m in chat_request.messages if m.role == "user"]
            if not user_messages:
                yield f"data: {_json.dumps({'type':'error','message':'No user messages in conversation'})}\n\n"
                return
            latest_query = user_messages[-1].content

            prior_messages = chat_request.messages[:-1]
            if prior_messages:
                history_for_reformulation = [
                    {"role": m.role, "content": m.content} for m in prior_messages
                ]
                search_query = ai_service.reformulate_query(history_for_reformulation, latest_query)
            else:
                search_query = latest_query

            context_results = []
            result_collection_ids = []

            if chat_request.scope == "all":
                all_collections = collection_service.get_all_collections()
                for col in all_collections:
                    col_id = col["id"]
                    try:
                        col_indexer = get_indexer(col_id)
                        col_search = col_indexer.search(
                            query=search_query, top_k=chat_request.top_k, mode=chat_request.mode
                        )
                        for r in col_search["results"]:
                            context_results.append(r)
                            result_collection_ids.append(col_id)
                    except Exception as e:
                        logger.warning(f"Stream chat: search failed for collection '{col_id}': {e}")
                paired = sorted(
                    zip(context_results, result_collection_ids),
                    key=lambda x: x[0].similarity_score, reverse=True,
                )[:chat_request.top_k]
                context_results = [p[0] for p in paired]
                result_collection_ids = [p[1] for p in paired]
            else:
                try:
                    indexer = get_indexer(collection_id)
                except ValueError as e:
                    yield f"data: {_json.dumps({'type':'error','message':str(e)})}\n\n"
                    return
                sr = indexer.search(query=search_query, top_k=chat_request.top_k, mode=chat_request.mode)
                context_results = sr["results"]
                result_collection_ids = [collection_id] * len(context_results)

            # ---------- structured tables ----------------------------------------
            overview_ids = (
                [c["id"] for c in collection_service.get_all_collections()]
                if chat_request.scope == "all"
                else [collection_id]
            )
            structured_tables, structured_stores = collect_structured_tables(overview_ids)
            structured_ctx = build_structured_context(structured_tables, structured_stores) if structured_tables else {
                "inline_block": "", "tool_tables": [], "inlined_filenames": set(), "inlined_document_ids": set()
            }
            inlined_filenames = structured_ctx["inlined_filenames"]
            tool_tables = structured_ctx["tool_tables"]
            inline_block = structured_ctx["inline_block"]

            filtered_results = [
                (r, cid) for (r, cid) in zip(context_results, result_collection_ids)
                if r.filename not in inlined_filenames
            ]
            context_text = "\n\n---\n\n".join(
                f"[Source {i+1}: {r.filename}, page {r.page_number}]\n{r.text_snippet}"
                for i, (r, _) in enumerate(filtered_results)
            ) or "No relevant context found."

            try:
                collection_overview = _build_collection_overview(overview_ids)
            except Exception as e:
                logger.warning(f"Stream chat: failed to build collection overview: {e}")
                collection_overview = "(Collection overview unavailable.)"

            tables_block = describe_tables_for_prompt(tool_tables) if tool_tables else ""

            # ---------- system prompt --------------------------------------------
            base_system_parts = [
                f"You are a helpful assistant with access to a private document knowledge base ({'all collections' if chat_request.scope == 'all' else 'the current collection'}).",
                "Answer the user's question using the COLLECTION OVERVIEW, STRUCTURED TABLES (when provided), and RETRIEVED CONTEXT below.",
                "Use the COLLECTION OVERVIEW for meta-questions about the knowledge base itself (file counts, available documents, date ranges).",
            ]
            if inline_block:
                base_system_parts.append(
                    "When STRUCTURED TABLES are included below, they are the FULL contents of CSV/XLSX files as JSONL — every row is present. "
                    "For any numeric, sum, count, average, filter, date-range, or ranking question about those files, answer DIRECTLY from the JSONL rows and show your arithmetic. "
                    "Do NOT guess from chunk snippets and do NOT assume data is missing."
                )
            if tool_tables:
                base_system_parts.append(
                    "For questions about the LARGE tables listed under TOOL USE PROTOCOL, call the structured-query tools — do not estimate from row text."
                )
            base_system_parts.append("Use RETRIEVED CONTEXT for prose/document questions, citing [Source N] as needed.")

            expertise_block = ""
            if chat_request.scope != "all":
                try:
                    attached_packs = expertise_store.get_packs_for_collection(collection_id)
                    if attached_packs:
                        base_system_parts.append(
                            "When ADVISOR EXPERTISE is provided, follow its guidance, rules, and frameworks as authoritative instructions for this analysis."
                        )
                        expertise_block = (
                            "ADVISOR EXPERTISE (apply these frameworks when analyzing this portfolio):\n"
                            + "\n\n".join(
                                f"## {p.name}\n{(p.description + chr(10)) if p.description else ''}{p.body}".strip()
                                for p in attached_packs
                            )
                        )
                except Exception as _ep:
                    logger.warning("Stream chat: failed to load expertise packs: %s", _ep)

            system_parts = [base_system_parts[0]]
            if len(base_system_parts) > 1:
                system_parts = base_system_parts
            system_text = "\n\n".join(
                [" ".join(base_system_parts),
                 f"COLLECTION OVERVIEW:\n{collection_overview}"]
                + ([expertise_block] if expertise_block else [])
                + ([inline_block] if inline_block else [])
                + ([f"PRE-RETRIEVED CONTEXT (optional primer):\n{context_text}"] if context_text and context_text != "No relevant context found." else [])
                + ([f"LARGE TABLES AVAILABLE:\n{tables_block}"] if tables_block else [])
            )

            # ---------- agent loop -----------------------------------------------
            agent_context = {"collection_id": collection_id, "scope": chat_request.scope}
            executed_results: list[dict] = []
            total_input_tokens = 0
            total_output_tokens = 0
            model_used = ai_service.quality_model
            response_text = ""
            max_iterations = 8

            if provider.supports_native_tools():
                is_anthropic = isinstance(provider, AnthropicProvider)
                tools_spec = anthropic_tools() if is_anthropic else openai_tools()

                messages: list[dict] = [
                    {"role": m.role, "content": m.content} for m in chat_request.messages
                ]

                for iteration in range(max_iterations):
                    turn = provider.complete_with_tools(
                        messages=messages,
                        tools=tools_spec,
                        max_tokens=4096,
                        model=ai_service.quality_model,
                        system=system_text,
                    )
                    usage_iter = turn.get("usage", {}) or {}
                    total_input_tokens += usage_iter.get("input_tokens", 0)
                    total_output_tokens += usage_iter.get("output_tokens", 0)
                    model_used = usage_iter.get("model", model_used)

                    tool_calls = turn.get("tool_calls") or []
                    thinking = turn.get("text") or ""

                    if not tool_calls:
                        response_text = thinking.strip()
                        break

                    # Emit any narration the model produced alongside tool calls
                    if thinking:
                        executed_results.append({"tool": "_thinking", "args": {"iteration": iteration}, "result": {"text": thinking}})
                        yield f"data: {_json.dumps({'type':'thinking','text':thinking})}\n\n"

                    # Emit tool_start for each call
                    for tc in tool_calls:
                        yield f"data: {_json.dumps({'type':'tool_start','tool':tc['name'],'args':tc.get('input') or {}})}\n\n"

                    messages.append(turn["assistant_message"])

                    # Execute all tool calls
                    adapted_calls = [{"tool": tc["name"], **(tc.get("input") or {})} for tc in tool_calls]
                    iter_results = execute_tool_calls(adapted_calls, agent_context=agent_context)
                    executed_results.extend(iter_results)

                    # Emit tool_end for each result
                    for tc, res in zip(tool_calls, iter_results):
                        yield f"data: {_json.dumps({'type':'tool_end','tool':tc['name'],'result':res})}\n\n"

                    # Append results to conversation
                    if is_anthropic:
                        tool_result_content = []
                        for tc, res in zip(tool_calls, iter_results):
                            payload = res.get("error") or res.get("result") or {}
                            tool_result_content.append({
                                "type": "tool_result",
                                "tool_use_id": tc["id"],
                                "content": _json.dumps(payload, default=str)[:60000],
                                **({"is_error": True} if res.get("error") else {}),
                            })
                        messages.append({"role": "user", "content": tool_result_content})
                    else:
                        for tc, res in zip(tool_calls, iter_results):
                            payload = res.get("error") or res.get("result") or {}
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": _json.dumps(payload, default=str)[:60000],
                            })

                if not response_text:
                    try:
                        turn = provider.complete_with_tools(
                            messages=messages, tools=[], max_tokens=2048,
                            model=ai_service.quality_model,
                            system=system_text + "\n\nDo not call any more tools. Summarize the final answer.",
                        )
                        response_text = (turn.get("text") or "").strip()
                        usage_iter = turn.get("usage", {}) or {}
                        total_input_tokens += usage_iter.get("input_tokens", 0)
                        total_output_tokens += usage_iter.get("output_tokens", 0)
                    except Exception as e:
                        logger.warning("Stream chat: final-answer pass failed: %s", e)
                        response_text = "I ran several tool calls but couldn't settle on a final answer — please rephrase or narrow the question."

            else:
                # ReAct fallback for Ollama — run synchronously then stream the text
                suffix = ""
                base_system = " ".join(base_system_parts)
                history_parts = [
                    f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
                    for m in chat_request.messages[:-1]
                ]
                history_text = "\n\n".join(history_parts) if history_parts else "(Start of conversation)"
                tools_block = build_tool_use_instructions()

                def _compose(extra=""):
                    parts = [base_system, "", f"COLLECTION OVERVIEW:\n{collection_overview}"]
                    if expertise_block:
                        parts.extend(["", expertise_block])
                    if inline_block:
                        parts.extend(["", inline_block])
                    parts.extend(["", f"RETRIEVED CONTEXT:\n{context_text}"])
                    if tables_block:
                        parts.extend(["", f"LARGE TABLES (use SQL tool calls):\n{tables_block}"])
                    if tools_block:
                        parts.extend(["", f"TOOL USE PROTOCOL:\n{tools_block}"])
                    parts.extend(["", f"CONVERSATION HISTORY:\n{history_text}", "", f"User: {latest_query}"])
                    if extra:
                        parts.append(extra)
                    parts.append("Assistant:")
                    return "\n\n".join(parts)

                for iteration in range(5):
                    prompt = _compose(suffix)
                    result = provider.complete(prompt=prompt, max_tokens=2048, model=ai_service.quality_model)
                    raw_text = result["text"]
                    usage_iter = result.get("usage", {}) or {}
                    total_input_tokens += usage_iter.get("input_tokens", 0)
                    total_output_tokens += usage_iter.get("output_tokens", 0)
                    model_used = usage_iter.get("model", model_used)

                    from services.structured_chat import parse_tool_calls
                    tool_calls_react = parse_tool_calls(raw_text)
                    if not tool_calls_react:
                        response_text = raw_text.strip()
                        break
                    for tc in tool_calls_react:
                        yield f"data: {_json.dumps({'type':'tool_start','tool':tc.get('tool','unknown'),'args':{}})}\n\n"
                    iter_results = execute_tool_calls(tool_calls_react, agent_context=agent_context)
                    executed_results.extend(iter_results)
                    for tc, res in zip(tool_calls_react, iter_results):
                        yield f"data: {_json.dumps({'type':'tool_end','tool':tc.get('tool','unknown'),'result':res})}\n\n"
                    result_text = _json.dumps([r.get("result") or r.get("error") for r in iter_results], default=str)
                    suffix = f"\nTool results: {result_text}\nContinue:"

            # ---------- stream the final text word-by-word -----------------------
            if response_text:
                words = response_text.split(" ")
                for i, word in enumerate(words):
                    chunk = word + (" " if i < len(words) - 1 else "")
                    yield f"data: {_json.dumps({'type':'text_delta','delta':chunk})}\n\n"
                    await asyncio.sleep(0.008)

            # ---------- sources --------------------------------------------------
            base_url = str(request.base_url).rstrip("/")
            sources_data = [
                {
                    "filename": r.filename,
                    "page_number": r.page_number,
                    "text_snippet": r.text_snippet,
                    "similarity_score": r.similarity_score,
                    "document_id": r.document_id,
                    "pdf_url": f"{base_url}/documents/{r.document_id}/pdf?collection_id={col_id}",
                    "page_url": f"{base_url}/documents/{r.document_id}/pdf?collection_id={col_id}#page={r.page_number}",
                }
                for r, col_id in filtered_results
            ]
            yield f"data: {_json.dumps({'type':'sources','sources':sources_data})}\n\n"

            # ---------- done -----------------------------------------------------
            yield f"data: {_json.dumps({'type':'done','usage':{'input_tokens':total_input_tokens,'output_tokens':total_output_tokens,'model':model_used},'structured_results':executed_results})}\n\n"

        except Exception as e:
            logger.exception("Stream chat failed")
            yield f"data: {_json.dumps({'type':'error','message':str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# Local File Reference endpoints (v3.1 feature)

@app.post(
    "/api/file-picker",
    summary="Open native file picker dialog",
    tags=["desktop"],
)
async def open_file_picker(multiple: bool = True, include_sizes: bool = False):
    """
    Open a native OS file picker dialog.

    This endpoint is designed for desktop app usage where the server
    runs locally on the user's machine.

    Args:
        multiple: If True, allow selecting multiple files (default: True)
        include_sizes: If True, include file sizes in response (default: False)

    Returns:
        {"paths": ["C:/path/to/file1.pdf", ...], "sizes": {"C:/path/to/file1.pdf": 12345, ...}}
    """
    from services.file_picker import open_file_dialog
    import os

    try:
        paths = open_file_dialog(multiple=multiple)
        result = {"paths": paths}

        if include_sizes and paths:
            sizes = {}
            for path in paths:
                try:
                    sizes[path] = os.path.getsize(path)
                except OSError:
                    sizes[path] = 0
            result["sizes"] = sizes

        return result
    except Exception as e:
        logger.error(f"File picker error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to open file picker: {str(e)}",
        )


@app.post(
    "/api/folder-picker",
    summary="Open native folder picker dialog",
    tags=["desktop"],
)
async def open_folder_picker_endpoint():
    """
    Open a native OS folder picker dialog.

    This endpoint is designed for desktop app usage where the server
    runs locally on the user's machine.

    Returns:
        {"path": "C:/path/to/folder"} or {"path": null} if cancelled
    """
    from services.file_picker import open_folder_dialog

    try:
        path = open_folder_dialog()
        return {"path": path}
    except Exception as e:
        logger.error(f"Folder picker error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to open folder picker: {str(e)}",
        )


class ScanFolderRequest(BaseModel):
    """Request body for scanning a folder."""
    path: str
    recursive: bool = True
    file_extensions: Optional[List[str]] = None


@app.post(
    "/api/scan-folder",
    summary="Scan folder and return list of supported files",
    tags=["desktop"],
)
async def scan_folder(request: ScanFolderRequest):
    """
    Scan a folder and return the list of supported files found.

    This is a lightweight operation that just lists files - no indexing.
    Use this to preview what files will be indexed before starting.
    """
    import os
    import fnmatch

    folder_path = Path(request.path)

    if not folder_path.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path does not exist: {request.path}",
        )

    if not folder_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path is not a directory: {request.path}",
        )

    # Default exclude patterns
    default_excludes = [
        'node_modules', '.git', '__pycache__', 'venv', '.venv',
        'dist', 'build', '.idea', '.vscode', 'target', 'bin', 'obj'
    ]

    # Determine which extensions to look for
    all_supported = DocumentExtractor.SUPPORTED_EXTENSIONS
    extensions_filter = set(request.file_extensions) if request.file_extensions else all_supported

    files_found = []

    def should_skip_dir(dirname: str) -> bool:
        return dirname in default_excludes or dirname.startswith('.')

    if request.recursive:
        for root, dirs, files in os.walk(folder_path):
            # Filter out excluded directories
            dirs[:] = [d for d in dirs if not should_skip_dir(d)]

            for filename in files:
                file_path = Path(root) / filename
                if file_path.suffix.lower() in extensions_filter:
                    files_found.append({
                        "path": str(file_path),
                        "name": filename,
                        "relative_path": str(file_path.relative_to(folder_path)),
                        "size": file_path.stat().st_size if file_path.exists() else 0
                    })
    else:
        for file_path in folder_path.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in extensions_filter:
                files_found.append({
                    "path": str(file_path),
                    "name": file_path.name,
                    "relative_path": file_path.name,
                    "size": file_path.stat().st_size if file_path.exists() else 0
                })

    # Sort by relative path for consistent display
    files_found.sort(key=lambda f: f["relative_path"])

    return {
        "folder": request.path,
        "files": files_found,
        "total": len(files_found)
    }


class OCRPlaygroundRequest(BaseModel):
    """Request body for OCR playground preview."""

    file_path: str
    enable_ocr: bool = True
    ocr_max_pages: int = 0
    ocr_max_file_mb: int = 0
    vision_ocr_provider: str = "none"
    vision_ocr_model: str = ""
    vision_ocr_api_key: str = ""
    vision_ocr_dpi: int = 150
    vision_ocr_enhance_image: bool = True
    vision_ocr_cleanup_pass: bool = True
    vision_ocr_cleanup_model: str = ""
    vision_ocr_ollama_url: str = "http://localhost:11434"
    vision_ocr_form_mode: bool = False
    force_ocr: bool = False
    max_chars_per_page: int = 6000
    include_fields: bool = True
    normalize_preview_text: bool = False


@app.post(
    "/api/ocr/playground",
    summary="Run OCR preview on a PDF without indexing",
    tags=["ocr"],
)
async def ocr_playground_preview(request: OCRPlaygroundRequest):
    """
    Run OCR/text extraction preview on a local PDF and return page-level output.

    This endpoint does not index or store the result.
    """
    path = Path(request.file_path)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {request.file_path}",
        )
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path is not a file: {request.file_path}",
        )
    if path.suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OCR playground currently supports PDF files only",
        )

    try:
        extractor = DocumentExtractor(
            enable_ocr=request.enable_ocr,
            ocr_max_pages=request.ocr_max_pages,
            ocr_max_file_mb=request.ocr_max_file_mb,
            vision_ocr_provider=request.vision_ocr_provider,
            vision_ocr_model=request.vision_ocr_model,
            vision_ocr_api_key=request.vision_ocr_api_key,
            vision_ocr_dpi=request.vision_ocr_dpi,
            vision_ocr_enhance_image=request.vision_ocr_enhance_image,
            vision_ocr_cleanup_pass=request.vision_ocr_cleanup_pass,
            vision_ocr_cleanup_model=request.vision_ocr_cleanup_model,
            vision_ocr_ollama_url=request.vision_ocr_ollama_url,
            vision_ocr_form_mode=request.vision_ocr_form_mode,
        )

        extraction_result = extractor.extract_text(path, force_ocr=request.force_ocr)
        page_texts = extraction_result.page_texts
        extraction_method = extraction_result.method
        ocr_pages = extraction_result.ocr_pages
        cleanup_pages = extraction_result.cleanup_pages

        pages: List[Dict[str, Any]] = []
        total_chars = 0
        pages_with_fields = 0
        pages_form_like = 0
        pages_field_extraction_skipped = 0
        max_chars = max(200, min(request.max_chars_per_page, 20000))

        injection_warnings = extraction_result.injection_warnings  # page_num -> InjectionScanResult
        flagged_pages = [p for p, r in injection_warnings.items() if r.is_flagged]

        for page_num in sorted(page_texts.keys()):
            raw_page_text = (page_texts.get(page_num) or "").strip()
            page_text = DocumentExtractor.normalize_ocr_text(raw_page_text) if request.normalize_preview_text else raw_page_text
            total_chars += len(page_text)
            text_preview = page_text[:max_chars]
            extracted_fields = None
            form_score = 0.0
            form_like = False
            field_extraction_applied = False

            if request.include_fields and text_preview:
                form_score = estimate_form_likelihood(text_preview)
                form_like = is_form_like_text(text_preview)

                if form_like:
                    field_extraction_applied = True
                    pages_form_like += 1
                    extracted_fields = extract_form_fields(text_preview)
                    if extracted_fields:
                        pages_with_fields += 1
                else:
                    pages_field_extraction_skipped += 1

            page_injection = injection_warnings.get(page_num)
            pages.append({
                "page_number": page_num,
                "char_count": len(page_text),
                "raw_char_count": len(raw_page_text),
                "text_preview": text_preview,
                "extracted_fields": extracted_fields,
                "form_score": round(form_score, 3),
                "form_like": form_like,
                "field_extraction_applied": field_extraction_applied,
                "cleanup_applied": request.normalize_preview_text,
                "injection_scan": page_injection.to_dict() if page_injection else None,
            })

        avg_chars_per_page = round(total_chars / max(len(page_texts), 1), 1) if page_texts else 0

        overall_risk = max(
            (r.risk_score for r in injection_warnings.values()), default=0.0
        )

        return {
            "file_path": str(path),
            "filename": path.name,
            "enable_ocr_requested": request.enable_ocr,
            "force_ocr": request.force_ocr,
            "normalize_preview_text": request.normalize_preview_text,
            "extraction_method": extraction_method,
            "total_pages": len(page_texts),
            "ocr_pages": ocr_pages,
            "cleanup_pages": cleanup_pages,
            "avg_chars_per_page": avg_chars_per_page,
            "total_chars": total_chars,
            "pages_with_fields": pages_with_fields,
            "pages_form_like": pages_form_like,
            "pages_field_extraction_skipped": pages_field_extraction_skipped,
            "injection_scan": {
                "overall_flagged": bool(flagged_pages),
                "overall_risk_score": round(overall_risk, 3),
                "flagged_pages": sorted(flagged_pages),
            },
            "pages": pages,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OCR playground preview failed for {request.file_path}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"OCR preview failed: {str(e)}",
        )



class VisionOCRRequest(BaseModel):
    """Request body for vision-AI OCR playground."""
    file_path: str
    provider: str = "openai"        # anthropic | openai | ollama
    model: str = "gpt-4o"           # Vision model for image reading
    cleanup_pass: bool = True       # Run a second LLM pass to clean up raw OCR output
    cleanup_model: str = ""         # Model for cleanup (empty = same as vision model)
    cleanup_provider: str = ""      # Provider for cleanup (empty = same as vision provider)
    dpi: int = 200                  # PDF render DPI (higher = better quality but slower)
    enhance_image: bool = True      # Boost contrast + sharpness before sending to model
    max_pages: int = 0              # 0 = no limit
    max_chars_per_page: int = 6000
    ollama_base_url: str = "http://localhost:11434"


@app.post(
    "/api/ocr/vision",
    summary="Run vision-AI OCR on a PDF (playground, no indexing)",
    tags=["ocr"],
)
async def ocr_vision_playground(
    request: VisionOCRRequest,
    x_ai_key: Optional[str] = Header(default=None, alias="X-AI-Key"),
):
    """
    Convert each PDF page to an image and extract text using a vision-capable AI model.
    Optionally run a second LLM cleanup pass over the raw output.
    This endpoint does not index or store the result.
    """
    from services.ai_service import create_provider
    from services.ocr_engine import VisionOCREngine

    path = Path(request.file_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {request.file_path}")
    if path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Vision OCR currently supports PDF files only")

    # Build the vision provider
    try:
        provider_kwargs: dict = {}
        if request.provider == "ollama":
            provider_kwargs["base_url"] = request.ollama_base_url
            provider_kwargs["model"] = request.model
        provider = create_provider(
            provider_name=request.provider,
            api_key=x_ai_key or "",
            **provider_kwargs,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Build the cleanup provider (may differ from vision provider)
    cleanup_provider = provider
    cleanup_model = request.cleanup_model or request.model
    if request.cleanup_provider and request.cleanup_provider != request.provider:
        try:
            cp_kwargs: dict = {}
            if request.cleanup_provider == "ollama":
                cp_kwargs["base_url"] = request.ollama_base_url
                cp_kwargs["model"] = cleanup_model
            cleanup_provider = create_provider(
                provider_name=request.cleanup_provider,
                api_key=x_ai_key or "",
                **cp_kwargs,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Cleanup provider error: {e}")

    try:
        engine = VisionOCREngine(
            provider=provider,
            model=request.model,
            cleanup_pass=request.cleanup_pass,
            cleanup_provider=cleanup_provider,
            cleanup_model=cleanup_model,
            dpi=request.dpi,
            enhance_image=request.enhance_image,
            max_pages=request.max_pages or 0,
        )

        ocr_result = engine.extract_text(str(path))

        max_chars = max(200, min(request.max_chars_per_page, 20000))
        pages = []
        total_chars = 0
        for ocr_page in ocr_result.pages:
            text = ocr_page.text or ""
            total_chars += len(text)
            pages.append({
                "page_number": ocr_page.page_number,
                "char_count": len(text),
                "text_preview": text[:max_chars],
                "cleanup_applied": ocr_page.metadata.get("cleanup_applied", False),
            })

        return {
            "file_path": str(path),
            "filename": path.name,
            "provider": request.provider,
            "model": request.model,
            "cleanup_pass": request.cleanup_pass,
            "dpi": request.dpi,
            "total_pages": len(pages),
            "total_chars": total_chars,
            "pages": pages,
        }

    except NotImplementedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Vision OCR failed for {request.file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Vision OCR failed: {str(e)}")


class IndexLocalRequest(BaseModel):
    """Request body for indexing local files."""
    file_path: str
    collection_id: str = "default"
    copy_to_library: bool = False  # If True, copy file to data/documents/ instead of indexing in-place


@app.post(
    "/documents/index-local",
    response_model=DocumentMetadata,
    status_code=status.HTTP_201_CREATED,
    summary="Index a local file without uploading",
    tags=["documents"],
)
async def index_local_file(request: IndexLocalRequest) -> DocumentMetadata:
    """
    Index a file directly from the local filesystem without copying it.

    The file remains in its original location and is read directly
    during indexing and when serving to users.

    This is useful for:
    - Large files that would be slow to upload
    - Files that shouldn't be duplicated
    - Desktop app usage where files are already local

    Args:
        request: Contains file_path and collection_id

    Returns:
        DocumentMetadata for the indexed document
    """
    from services.code_extractor import SUPPORTED_CODE_EXTENSIONS

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


@app.post(
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
    from services.code_extractor import SUPPORTED_CODE_EXTENSIONS

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


@app.post(
    "/api/ai/validate-key",
    summary="Validate an AI provider API key or Ollama model",
    tags=["ai"],
)
async def validate_api_key(
    x_ai_key: str = Header(None),
    x_ai_provider: str = Header("anthropic"),
    x_ollama_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
):
    """
    Validate an API key or Ollama model availability.

    For cloud providers (Anthropic, OpenAI):
    - Pass the key via X-AI-Key header
    - Pass provider via X-AI-Provider header

    For Ollama:
    - Pass 'ollama' via X-AI-Provider header
    - Pass model name via X-Ollama-Model header
    - X-AI-Key is not required
    """
    try:
        if x_ai_provider not in ALL_AI_PROVIDERS:
            return {
                "valid": False,
                "error": f"Invalid provider: {x_ai_provider}. "
                         f"Supported providers: {', '.join(ALL_AI_PROVIDERS)}"
            }

        if x_ai_provider == "ollama":
            model = x_ai_model or x_ollama_model or "llama3.2"
            extra: dict = {"model": model}
            if x_ai_base_url:
                extra["base_url"] = x_ai_base_url
            provider = create_provider("ollama", **extra)
        elif x_ai_provider == "openai_compatible":
            if not x_ai_base_url:
                return {"valid": False, "error": "base_url required for openai_compatible provider"}
            provider = create_provider(
                "openai_compatible",
                api_key=x_ai_key,
                base_url=x_ai_base_url,
                model=x_ai_model or "default",
            )
        else:
            if not x_ai_key:
                return {"valid": False, "error": "API key is required for cloud providers"}
            extra = {}
            if x_ai_model:
                extra["model"] = x_ai_model
            provider = create_provider(x_ai_provider, x_ai_key, **extra)

        valid = provider.validate()
        return {"valid": valid, "error": None}
    except Exception as e:
        error_str = str(e)
        logger.error(f"API key validation error for {x_ai_provider}: {e}")

        # Provide more helpful error messages
        if "quota" in error_str.lower() or "insufficient_quota" in error_str.lower():
            return {"valid": False, "error": "Your API key has exceeded its quota. Please add credits to your account."}
        elif "rate" in error_str.lower() and "limit" in error_str.lower():
            return {"valid": False, "error": "Rate limit exceeded. Please wait a moment and try again."}
        else:
            return {"valid": False, "error": error_str}


@app.post(
    "/api/ask",
    response_model=AskResponse,
    summary="Ask a question about indexed documents",
    tags=["agent"],
)
async def ask_question(
    request: AskRequest,
    x_ai_key: str = Header(None),
    x_ai_provider: str = Header("anthropic"),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
):
    """
    Ask a question and get a synthesized answer from indexed documents.

    This endpoint is optimized for coding agents and AI assistants.

    **Processing Flow**:
    1. **Search**: Vector similarity search finds relevant document chunks
    2. **Rerank** (optional, default=true): AI reorders results by actual relevance
    3. **Synthesis**: AI generates coherent answer with citations
    4. **Response**: Clean, structured response returned to agent

    **API Key Options** (in order of precedence):
    1. Pass via X-AI-Key header (per-request)
    2. Use server-stored key (configured via /api/agent/config)
    3. For cloud providers (Anthropic/OpenAI), set X-AI-Provider accordingly
    4. For Ollama: no key needed, just set X-AI-Provider: ollama

    **Response Formats**:
    - `markdown`: Answer with markdown formatting and source citations
    - `text`: Plain text answer with inline citations
    - `json`: Structured JSON with answer and sources array

    **Token Usage**:
    - Rerank uses a fast model (lower cost) to judge relevance
    - Synthesis uses a quality model for answer generation
    - `tokens_used` in response is the total across both operations

    Example usage with curl:
    ```bash
    # Using header key
    curl -X POST http://localhost:8000/api/ask \\
      -H "Content-Type: application/json" \\
      -H "X-AI-Key: your-api-key" \\
      -d '{"question": "How do I configure the API?", "collection_id": "docs"}'

    # Using server-stored key (no header needed)
    curl -X POST http://localhost:8000/api/ask \\
      -H "Content-Type: application/json" \\
      -d '{"question": "How do I configure the API?"}'

    # Skip reranking for faster response (lower quality)
    curl -X POST http://localhost:8000/api/ask \\
      -H "Content-Type: application/json" \\
      -d '{"question": "How do I configure the API?", "rerank": false}'
    ```
    """
    from services.app_database import app_db
    import time
    start_time = time.time()

    if x_ai_provider not in ALL_AI_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {x_ai_provider}. "
                   f"Supported providers: {', '.join(ALL_AI_PROVIDERS)}",
        )

    # Validate AI provider is configured
    if x_ai_provider == "ollama":
        model = x_ai_model or x_ollama_model or "llama3.2"
        try:
            extra_ollama: dict = {"model": model}
            if x_ai_base_url:
                extra_ollama["base_url"] = x_ai_base_url
            provider = create_provider("ollama", **extra_ollama)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Ollama not available: {e}",
            )
    else:
        api_key = x_ai_key or app_db.get_agent_api_key(x_ai_provider)
        if not api_key and x_ai_provider != "openai_compatible":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"No API key configured for {x_ai_provider}. "
                       f"Either pass X-AI-Key header or configure via /api/agent/config.",
            )
        try:
            extra = {}
            model = x_ai_model or x_anthropic_model or x_openai_model
            if model:
                extra["model"] = model
            if x_ai_base_url:
                extra["base_url"] = x_ai_base_url
            provider = create_provider(x_ai_provider, api_key, **extra)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    # Get indexer for the collection
    try:
        indexer = get_indexer(request.collection_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    # Search for relevant chunks based on mode
    from models.schemas import SearchMode
    try:
        if request.mode == SearchMode.KEYWORD:
            # Pure BM25 keyword search
            bm25_results = indexer.vector_store.bm25_index.search(request.question, request.top_k)
            search_results = indexer._bm25_to_search_results(bm25_results)
        elif request.mode == SearchMode.HYBRID:
            # Combined semantic + keyword search
            query_embedding = indexer.embedding_service.embed_query(request.question)
            search_results = indexer.vector_store.search_hybrid(
                query=request.question,
                query_embedding=query_embedding,
                top_k=request.top_k,
                semantic_weight=request.semantic_weight,
            )
        else:
            # Default: pure semantic search
            search_results = indexer.vector_store.search(
                indexer.embedding_service.embed_query(request.question),
                top_k=request.top_k,
            )
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {e}",
        )

    if not search_results:
        return AskResponse(
            answer="No relevant documents found for your question.",
            sources=[],
            collection_id=request.collection_id,
            model=provider.QUALITY_MODEL,
            tokens_used=0,
        )

    # Track total token usage
    total_tokens = 0
    ai_service = AIService(provider=provider)

    # Rerank results if enabled (improves answer quality)
    if request.rerank:
        try:
            # Convert search results to format expected by rerank
            results_for_rerank = [
                {
                    "index": i,
                    "filename": r.filename,
                    "text_snippet": r.text_snippet,
                    "page_number": r.page_number,
                    "similarity_score": r.similarity_score,
                }
                for i, r in enumerate(search_results)
            ]

            rerank_result = ai_service.rerank_results(
                request.question, results_for_rerank, request.top_k
            )

            if rerank_result["usage"]:
                total_tokens += (
                    rerank_result["usage"]["input_tokens"] +
                    rerank_result["usage"]["output_tokens"]
                )

            # Reorder search results based on reranking
            reranked_indices = rerank_result["reranked_indices"]
            if reranked_indices:
                search_results = [
                    search_results[i]
                    for i in reranked_indices
                    if i < len(search_results)
                ]
                logger.info(f"Reranked to {len(search_results)} results")

        except Exception as e:
            logger.warning(f"Reranking failed, using original order: {e}")

    # Build context for AI synthesis
    context_parts = []
    sources = []
    for i, result in enumerate(search_results):
        # Truncate excerpt if needed
        excerpt = result.text_snippet
        if len(excerpt) > request.max_source_length:
            excerpt = excerpt[:request.max_source_length] + "..."

        context_parts.append(
            f"[Source {i + 1}: {result.filename}, page {result.page_number}]\n"
            f"{result.text_snippet}"
        )

        if request.include_sources:
            sources.append(AskSource(
                filename=result.filename,
                page=result.page_number,
                excerpt=excerpt,
                relevance=round(result.similarity_score, 3),
            ))

    context = "\n\n---\n\n".join(context_parts)

    # Build the synthesis prompt based on format
    if request.format == "json":
        format_instruction = (
            "Provide your answer as valid JSON with the structure: "
            '{"answer": "your answer here", "key_points": ["point 1", "point 2"]}'
        )
    elif request.format == "text":
        format_instruction = (
            "Provide your answer as plain text. Use inline citations like "
            "[Source 1] when referencing information."
        )
    else:  # markdown
        format_instruction = (
            "Format your answer using markdown. Use headers, lists, and code blocks "
            "as appropriate. Cite sources using [Source N] notation."
        )

    # Build collection overview so the model can answer meta-questions
    # ("how many sources?", "what documents are indexed?") without needing
    # retrieved chunks to contain that information.
    try:
        collection_overview = _build_collection_overview([request.collection_id])
    except Exception as e:
        logger.warning(f"Failed to build collection overview for ask: {e}")
        collection_overview = "(Collection overview unavailable.)"

    prompt = (
        "You are a knowledgeable assistant helping answer questions based on "
        "indexed documentation. Answer the question using the COLLECTION OVERVIEW "
        "and RETRIEVED SOURCES below.\n\n"
        "Rules:\n"
        "- Use the COLLECTION OVERVIEW for meta-questions about the knowledge base itself "
        "(file counts, available documents, date ranges, what's in the collection).\n"
        "- Use the RETRIEVED SOURCES for questions about document content, and cite "
        "specific sources with [Source N].\n"
        "- If neither the overview nor the sources contain enough information, say so.\n"
        "- Be concise but thorough.\n"
        f"- {format_instruction}\n\n"
        f"COLLECTION OVERVIEW:\n{collection_overview}\n\n"
        f"Question: {request.question}\n\n"
        f"RETRIEVED SOURCES:\n{context}"
    )

    # Generate answer
    try:
        response = provider.complete(prompt, max_tokens=1500, model=provider.QUALITY_MODEL)
        answer = response["text"]
        total_tokens += response["usage"]["input_tokens"] + response["usage"]["output_tokens"]
        model_used = response["usage"]["model"]
    except Exception as e:
        logger.error(f"AI synthesis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI synthesis failed: {e}",
        )

    elapsed = time.time() - start_time
    logger.info(f"Ask query completed in {elapsed:.2f}s using {model_used}")

    return AskResponse(
        answer=answer,
        sources=sources,
        collection_id=request.collection_id,
        model=model_used,
        tokens_used=total_tokens,
    )


@app.get(
    "/api/agent/config",
    summary="Get agent API configuration",
    tags=["agent"],
)
async def get_agent_config():
    """
    Get the current agent API configuration.

    Returns which providers have API keys configured (keys are masked for security).
    """
    from services.app_database import app_db
    return app_db.get_agent_config()


@app.post(
    "/api/agent/config",
    summary="Configure agent API key",
    tags=["agent"],
)
async def set_agent_config(
    provider: str,
    api_key: str,
):
    """
    Store an API key for agent use.

    This allows the /api/ask endpoint to work without requiring
    the X-AI-Key header on every request.

    Args:
        provider: AI provider name (anthropic, openai)
        api_key: The API key to store

    Note: Keys are stored server-side. For security, ensure your
    Asymptote instance is properly secured.
    """
    from services.app_database import app_db

    if provider not in CLOUD_AI_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {provider}. Use one of {', '.join(CLOUD_AI_PROVIDERS)}.",
        )

    # Validate the key before storing
    try:
        test_provider = create_provider(provider, api_key)
        valid = test_provider.validate()
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"API key validation failed for {provider}",
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"API key validation failed: {e}",
        )

    app_db.set_agent_api_key(provider, api_key)
    return {"success": True, "message": f"API key configured for {provider}"}


@app.delete(
    "/api/agent/config/{provider}",
    summary="Remove agent API key",
    tags=["agent"],
)
async def delete_agent_config(provider: str):
    """
    Remove a stored API key.

    Args:
        provider: AI provider name (anthropic, openai)
    """
    from services.app_database import app_db

    if provider not in CLOUD_AI_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {provider}",
        )

    app_db.delete_agent_api_key(provider)
    return {"success": True, "message": f"API key removed for {provider}"}



@app.get(
    "/api/mcp/config",
    summary="Get MCP configuration",
    tags=["mcp"],
)
async def get_mcp_config():
    """Get the embedded MCP server configuration."""
    return get_mcp_settings_payload()


@app.post(
    "/api/mcp/config",
    summary="Update MCP configuration",
    tags=["mcp"],
)
async def update_mcp_config(updates: dict):
    """Update embedded MCP settings without restarting the app."""
    filtered_updates = {
        key: value
        for key, value in updates.items()
        if key in MCP_CONFIG_FIELDS
    }
    if not filtered_updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No MCP configuration fields were provided.",
        )

    return config_manager.update_config(filtered_updates)


@app.get(
    "/api/mcp/export",
    summary="Get Claude and Codex MCP config exports",
    tags=["mcp"],
)
async def get_mcp_export(request: Request, collection_id: str = None):
    """Return copy/download-ready MCP config snippets for Claude Code and Codex."""
    base_url = str(request.base_url).rstrip('/')
    profile = get_collection_mcp_profile(collection_id) if collection_id else None
    payload = build_mcp_export_payload(base_url, profile=profile)
    payload["enabled"] = settings.enable_mcp
    return payload


@app.get(
    "/api/mcp/collections/{collection_id}/profile",
    summary="Get collection-specific MCP export profile",
    tags=["mcp"],
)
async def get_collection_mcp_export_profile(collection_id: str):
    """Get the saved MCP export profile for a collection."""
    try:
        return get_collection_mcp_profile(collection_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@app.post(
    "/api/mcp/collections/{collection_id}/profile",
    summary="Update collection-specific MCP export profile",
    tags=["mcp"],
)
async def update_collection_mcp_export_profile(collection_id: str, updates: dict):
    """Save MCP export defaults for a specific collection."""
    try:
        return save_collection_mcp_profile(collection_id, updates)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


def _enrich_mcp_resource(resource: dict, base_url: str) -> dict:
    """Add server_url, export configs, and collection_name to a resource dict."""
    collection_id = resource["collection_id"]
    collection = collection_service.get_collection(collection_id)
    collection_name = collection.get("name", collection_id) if collection else collection_id
    export = build_mcp_export_payload(base_url, {"collection_id": collection_id})
    return {
        **resource,
        "collection_name": collection_name,
        "server_url": export["server_url"],
        "claude_json": export["claude_json"],
        "codex_toml": export["codex_toml"],
        "copilot_json": export["copilot_json"],
    }


@app.get(
    "/api/mcp/resources",
    summary="List MCP project resources",
    tags=["mcp"],
)
async def list_mcp_resources(request: Request):
    """Return all saved MCP project→collection mappings with export configs."""
    from services.app_database import app_db
    base_url = str(request.base_url).rstrip('/')
    return [_enrich_mcp_resource(r, base_url) for r in app_db.get_all_mcp_resources()]


@app.post(
    "/api/mcp/resources",
    summary="Create MCP project resource",
    tags=["mcp"],
)
async def create_mcp_resource(request: Request, body: dict):
    """Create a named mapping from a project to an Asymptote collection."""
    from services.app_database import app_db
    import uuid
    name = (body.get("name") or "").strip()
    collection_id = (body.get("collection_id") or "").strip()
    repo_url = (body.get("repo_url") or "").strip() or None
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    if not collection_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="collection_id is required")
    existing = [r for r in app_db.get_all_mcp_resources() if r["collection_id"] == collection_id]
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A connection for this collection already exists")
    resource = app_db.create_mcp_resource(str(uuid.uuid4())[:8], name, collection_id, repo_url)
    return _enrich_mcp_resource(resource, str(request.base_url).rstrip('/'))


@app.put(
    "/api/mcp/resources/{resource_id}",
    summary="Update MCP project resource",
    tags=["mcp"],
)
async def update_mcp_resource(request: Request, resource_id: str, body: dict):
    from services.app_database import app_db
    new_collection = body.get("collection_id")
    if new_collection:
        existing = [r for r in app_db.get_all_mcp_resources() if r["collection_id"] == new_collection and r["id"] != resource_id]
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A connection for this collection already exists")
    resource = app_db.update_mcp_resource(
        resource_id,
        name=body.get("name"),
        collection_id=new_collection,
        repo_url=body.get("repo_url"),
    )
    if not resource:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return _enrich_mcp_resource(resource, str(request.base_url).rstrip('/'))


@app.delete(
    "/api/mcp/resources/{resource_id}",
    summary="Delete MCP project resource",
    tags=["mcp"],
)
async def delete_mcp_resource(resource_id: str):
    from services.app_database import app_db
    if not app_db.delete_mcp_resource(resource_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return {"ok": True}


@app.get(
    "/api/config",
    summary="Get current configuration",
    tags=["config"],
)
async def get_config():
    """
    Get current configuration settings.

    Returns all configurable settings including embedding model,
    chunking parameters, and storage type.
    """
    return config_manager.get_current_config()


@app.post(
    "/api/config",
    summary="Update configuration",
    tags=["config"],
)
async def update_config(updates: dict):
    """
    Update configuration settings.

    Args:
        updates: Dictionary of configuration key-value pairs

    Returns:
        Object with success status, whether restart/reindex needed,
        and list of updated fields.

    Note: Some changes (like embedding model) require server restart
    and re-indexing all documents.
    """
    result = config_manager.update_config(updates)
    return result



@app.get(
    "/api/ollama/status",
    summary="Check Ollama availability",
    tags=["ai"],
)
async def get_ollama_status():
    """
    Check if Ollama is running and list available models.

    Returns:
        - available: Whether Ollama is accessible
        - models: List of available models with names and sizes
        - error: Error message if detection failed
    """
    return detect_ollama()


@app.get(
    "/api/ollama/vision-models",
    summary="List Ollama models that support vision/image input",
    tags=["ai"],
)
async def get_ollama_vision_models():
    """
    Return only Ollama models that support vision by checking each model's
    architecture families via /api/show. Vision models include 'clip' in their
    families list (CLIP is the vision encoder used by all multimodal Ollama models).
    Falls back to name-based detection if /api/show is unavailable.
    """
    import httpx
    import asyncio

    status = detect_ollama()
    if not status.get("available"):
        return {"available": False, "models": [], "error": status.get("error")}

    base_url = status.get("base_url", "http://localhost:11434").rstrip("/")
    all_models = status.get("models", [])

    # Known vision model name patterns as a fallback
    VISION_NAME_PATTERNS = [
        "llava", "vl", "vision", "minicpm-v", "moondream",
        "bakllava", "cogvlm", "internvl", "clip",
    ]

    def name_looks_like_vision(name: str) -> bool:
        name_lower = name.lower()
        return any(p in name_lower for p in VISION_NAME_PATTERNS)

    async def check_model_vision(client: httpx.AsyncClient, model: dict) -> Optional[dict]:
        try:
            resp = await client.post(
                f"{base_url}/api/show",
                json={"name": model["name"]},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                families = data.get("details", {}).get("families") or []
                if "clip" in families or "mllama" in families:
                    return {**model, "vision_detected_by": "families"}
            # Fall back to name heuristic
            if name_looks_like_vision(model["name"]):
                return {**model, "vision_detected_by": "name"}
            return None
        except Exception:
            # If /api/show fails entirely, use name heuristic
            if name_looks_like_vision(model["name"]):
                return {**model, "vision_detected_by": "name"}
            return None

    async def gather_vision_models():
        async with httpx.AsyncClient() as client:
            tasks = [check_model_vision(client, m) for m in all_models]
            results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    vision_models = await gather_vision_models()
    return {
        "available": True,
        "models": vision_models,
        "total_models": len(all_models),
    }



@app.post(
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

        # Get embedding dimensions
        embedding_service = EmbeddingService(model_name=current_config["embedding_model"])
        embedding_dim = embedding_service.embedding_dim

        # Start re-indexing
        job_id = await reindex_service.start_reindex(
            documents_dir=settings.data_dir / "documents",
            embedding_model=current_config["embedding_model"],
            chunk_size=current_config["chunk_size"],
            chunk_overlap=current_config["chunk_overlap"],
            embedding_dim=embedding_dim,
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


@app.post(
    "/api/collections/{collection_id}/reindex",
    summary="Start re-indexing a collection",
    tags=["collections"],
)
async def start_collection_reindex(collection_id: str):
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

        # Get embedding dimensions for the collection's model
        embedding_service = EmbeddingService(model_name=collection["embedding_model"])
        embedding_dim = embedding_service.embedding_dim

        # Start re-indexing
        job_id = await reindex_service.start_collection_reindex(
            collection_id=collection_id,
            documents_dir=documents_dir,
            indexes_dir=indexes_dir,
            embedding_model=collection["embedding_model"],
            chunk_size=collection["chunk_size"],
            chunk_overlap=collection["chunk_overlap"],
            embedding_dim=embedding_dim,
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


@app.get(
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
@app.get(
    "/api/collections",
    summary="List all collections",
    tags=["collections"],
)
async def list_collections(request: Request, user_id: str = Depends(get_current_user_id)):
    """Get all document collections visible to the current user."""
    collections = collection_service.get_all_collections(user_id=user_id)
    return {"collections": collections, "user_id": user_id, "multi_user": settings.enable_multi_user}


@app.post(
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
        owner_id=user_id,
    )

    return collection


@app.get(
    "/api/collections/{collection_id}",
    summary="Get collection details",
    tags=["collections"],
)
async def get_collection(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """Get details for a specific collection."""
    collection = collection_service.get_collection(collection_id)
    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{collection_id}' not found"
        )
    access = sharing_service.check_collection_access(collection_id, user_id)
    if not access:
        raise HTTPException(status_code=403, detail="You do not have access to this collection")
    collection['permission'] = access
    return collection


@app.put(
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
    access = sharing_service.check_collection_access(collection_id, user_id)
    if access not in ("owner", "readwrite"):
        raise HTTPException(status_code=403, detail="You need owner or write access to update this collection")
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


@app.delete(
    "/api/collections/{collection_id}",
    summary="Delete a collection",
    tags=["collections"],
)
async def delete_collection(collection_id: str, user_id: str = Depends(get_current_user_id)):
    """
    Delete a collection and all its documents. Requires owner access.

    Note: The 'default' collection cannot be deleted.
    """
    access = sharing_service.check_collection_access(collection_id, user_id)
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


@app.get(
    "/documents",
    response_model=DocumentListResponse,
    summary="List all indexed documents",
    tags=["documents"],
)
async def list_documents(
    collection_id: str = "default",
) -> DocumentListResponse:
    """
    List all indexed documents with their metadata.

    Args:
        collection_id: Collection to list documents from (default: "default")

    Returns filename, page count, and chunk count for each document.
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

        documents = indexer.list_documents()

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
            # Handle None values by defaulting to empty string
            indexed_at = doc.get("upload_timestamp") or ""
            if not indexed_at and doc_path.exists():
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
            total_documents=len(doc_metadata_list),
        )

    except Exception as e:
        logger.error(f"Failed to list documents: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list documents: {str(e)}",
        )


@app.get(
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


@app.get(
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


@app.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a document from the index",
    tags=["documents"],
)
async def delete_document(
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


# ── User & Sharing endpoints ──────────────────────────────────

@app.get("/api/user/me", summary="Get current user info", tags=["users"])
async def get_current_user(user_id: str = Depends(get_current_user_id)):
    """Get the current user's identity and multi-user status."""
    from services.app_database import app_db
    user = app_db.get_user(user_id)
    return {
        "user_id": user_id,
        "display_name": user["display_name"] if user else user_id,
        "multi_user": settings.enable_multi_user,
        "db_backend": settings.db_backend,
    }


@app.get("/api/users", summary="List all users", tags=["users"])
async def list_users():
    """List all known users (admin view)."""
    from services.app_database import app_db
    return {"users": app_db.get_all_users()}


@app.post(
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


@app.get(
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


@app.post(
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


@app.get(
    "/api/shared-with-me",
    summary="List collections shared with me",
    tags=["sharing"],
)
async def list_shared_with_me(user_id: str = Depends(get_current_user_id)):
    """Get all collections that have been shared with the current user."""
    collections = sharing_service.get_shared_with_me(user_id)
    return {"collections": collections}


@app.delete(
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


# ── Meeting Brief REST endpoint (v4.4) ────────────────────────────────────────

@app.post(
    "/api/collections/{collection_id}/brief",
    tags=["chat"],
    summary="Generate a pre-meeting portfolio brief",
)
async def generate_brief_endpoint(
    collection_id: str,
    tax_loss_min: float = 500.0,
    concentration_pct: float = 10.0,
    cash_drag_min: float = 50000.0,
    top_n: int = 10,
):
    """
    Generate a pre-meeting portfolio brief for *collection_id*.

    Calls the brief_generator directly (no LLM token cost) and returns
    a structured JSON brief with sections: household_summary, accounts,
    top_positions, tax_loss_candidates, concentration_alerts,
    cash_drag_alerts, sector_allocation, generated_at.
    """
    from services.brief_generator import generate_meeting_brief as _gen_brief

    try:
        indexer = get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        store = indexer.vector_store.structured_store
    except AttributeError:
        raise HTTPException(status_code=422, detail="No structured store found for this collection.")

    try:
        thresholds = {
            "tax_loss_min": tax_loss_min,
            "concentration_pct": concentration_pct,
            "cash_drag_min": cash_drag_min,
            "top_n": top_n,
        }
        brief = _gen_brief(store, collection_id=collection_id, thresholds=thresholds)
    except Exception as e:
        logger.error("Brief generation failed for collection %s: %s", collection_id, e)
        raise HTTPException(status_code=500, detail=f"Brief generation failed: {e}")

    return brief


# ── Expertise Library endpoints ───────────────────────────────────────────────

@app.get("/api/expertise/packs", response_model=list[ExpertisePack], tags=["expertise"])
async def list_expertise_packs():
    """Return all expertise packs ordered by name."""
    return expertise_store.list_packs()


@app.post("/api/expertise/packs", response_model=ExpertisePack, status_code=201, tags=["expertise"])
async def create_expertise_pack(data: ExpertisePackCreate):
    """Create a new expertise pack."""
    return expertise_store.create_pack(data)


@app.get("/api/expertise/packs/{pack_id}", response_model=ExpertisePack, tags=["expertise"])
async def get_expertise_pack(pack_id: str):
    """Return a single expertise pack by ID."""
    pack = expertise_store.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Expertise pack not found")
    return pack


@app.put("/api/expertise/packs/{pack_id}", response_model=ExpertisePack, tags=["expertise"])
async def update_expertise_pack(pack_id: str, data: ExpertisePackUpdate):
    """Partially update an expertise pack."""
    pack = expertise_store.update_pack(pack_id, data)
    if pack is None:
        raise HTTPException(status_code=404, detail="Expertise pack not found")
    return pack


@app.delete("/api/expertise/packs/{pack_id}", status_code=204, tags=["expertise"])
async def delete_expertise_pack(pack_id: str):
    """Delete an expertise pack (also removes all collection attachments)."""
    deleted = expertise_store.delete_pack(pack_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Expertise pack not found")


@app.get(
    "/api/collections/{collection_id}/expertise",
    response_model=CollectionExpertiseResponse,
    tags=["expertise"],
)
async def get_collection_expertise(collection_id: str):
    """List all expertise packs attached to a collection."""
    packs = expertise_store.get_packs_for_collection(collection_id)
    return CollectionExpertiseResponse(collection_id=collection_id, packs=packs)


@app.put(
    "/api/collections/{collection_id}/expertise",
    response_model=CollectionExpertiseResponse,
    tags=["expertise"],
)
async def set_collection_expertise(collection_id: str, data: SetCollectionExpertiseRequest):
    """Replace the full set of expertise packs attached to a collection."""
    expertise_store.set_packs_for_collection(collection_id, data.pack_ids)
    packs = expertise_store.get_packs_for_collection(collection_id)
    return CollectionExpertiseResponse(collection_id=collection_id, packs=packs)


# ---------------------------------------------------------------------------
# PII pre-flight + collection blacklist endpoints
# ---------------------------------------------------------------------------

class PiiBlacklistUpdateRequest(BaseModel):
    terms: List[str]


@app.post(
    "/api/pii/preflight",
    summary="Pre-flight PII scan for a tabular file",
    tags=["privacy"],
)
async def pii_preflight(
    file_path: str,
    collection_id: str = "default",
) -> dict:
    """Scan a CSV or Excel file for PII before indexing.

    Returns per-column findings (role, action, sample values) and the
    current custom blacklist for the collection.  Does not modify the file
    or the index.
    """
    path = Path(file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    ext = path.suffix.lower()
    if ext not in (".csv", ".xlsx", ".xls"):
        raise HTTPException(
            status_code=400,
            detail="PII pre-flight only supports CSV and Excel files.",
        )
    try:
        from services.privacy.pii_preflight import scan_file
        return scan_file(path, collection_id=collection_id)
    except Exception as exc:
        logger.error("PII preflight failed for %s: %s", file_path, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    "/api/collections/{collection_id}/pii-blacklist",
    summary="Get custom PII blacklist for a collection",
    tags=["privacy"],
)
async def get_pii_blacklist(collection_id: str) -> dict:
    from services.privacy.collection_blacklist import get_blacklist
    return {"collection_id": collection_id, "terms": get_blacklist(collection_id)}


@app.post(
    "/api/collections/{collection_id}/pii-blacklist",
    summary="Add terms to the PII blacklist for a collection",
    tags=["privacy"],
)
async def add_pii_blacklist_terms(
    collection_id: str,
    body: PiiBlacklistUpdateRequest,
) -> dict:
    from services.privacy.collection_blacklist import add_terms
    updated = add_terms(collection_id, body.terms)
    return {"collection_id": collection_id, "terms": updated}


@app.delete(
    "/api/collections/{collection_id}/pii-blacklist",
    summary="Remove terms from the PII blacklist for a collection",
    tags=["privacy"],
)
async def remove_pii_blacklist_terms(
    collection_id: str,
    body: PiiBlacklistUpdateRequest,
) -> dict:
    from services.privacy.collection_blacklist import remove_terms
    updated = remove_terms(collection_id, body.terms)
    return {"collection_id": collection_id, "terms": updated}


# Redirect bare /mcp (no trailing slash) to /mcp/ so MCP clients that use the old
# exported URL still work. Uses 307 to preserve the HTTP method (POST stays POST).
@app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def mcp_trailing_slash_redirect(request: Request):
    url = str(request.url)
    redirect_url = url.replace("/mcp?", "/mcp/?", 1) if "?" in url else url.rstrip("/") + "/"
    from fastapi.responses import RedirectResponse
    return RedirectResponse(redirect_url, status_code=307)


# Mount static files at root to serve /assets/* and other static content
# This must be last so it doesn't override API routes
app.mount("/mcp", embedded_mcp_app, name="mcp")
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn_kwargs: dict = {
        "host": settings.host,
        "port": settings.port,
        "reload": True,
    }

    cert_path = Path(settings.ssl_certfile).expanduser() if settings.ssl_certfile else None
    key_path = Path(settings.ssl_keyfile).expanduser() if settings.ssl_keyfile else None
    if cert_path and key_path and cert_path.is_file() and key_path.is_file():
        uvicorn_kwargs["ssl_certfile"] = str(cert_path)
        uvicorn_kwargs["ssl_keyfile"] = str(key_path)
        logger.info(f"HTTPS enabled — serving on https://{settings.host}:{settings.port}")
    elif settings.ssl_certfile or settings.ssl_keyfile:
        logger.warning(
            "ssl_certfile / ssl_keyfile configured but one or both files are missing — "
            "falling back to plain HTTP. Run certs/generate-cert.sh to create a dev pair."
        )

    uvicorn.run("main:app", **uvicorn_kwargs)

