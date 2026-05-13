"""
Finn — Self-hosted document search API.

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

from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File, status, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import settings
from services.document_extractor import DocumentExtractor, is_code_file
from services.code_extractor import SUPPORTED_CODE_EXTENSIONS
from services.chunker import TextChunker
from services.embedder import EmbeddingService
from services.vector_store import VectorStore
from services.indexing import DocumentIndexer
from services.ai_service import AIService, create_provider, detect_ollama
from services.config_manager import config_manager
from services.reindex_service import reindex_service
from services.collection_service import collection_service
from services.sharing_service import sharing_service
from services.indexer_manager import indexer_manager
from services.expertise_store import ExpertiseStore
expertise_store = ExpertiseStore()
from services.structured_chat import (
    build_structured_context,
    collect_structured_tables,
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
    ExpertisePackGenerateRequest,
    CollectionExpertiseResponse,
    SetCollectionExpertiseRequest,
    NotesRequest,
    FollowupRequest,
    NoteResponse,
    ExtractMeetingNotesRequest,
    SaveNoteRequest,
    SaveNoteResponse,
    RedactionDryRunRequest,
    RedactionDryRunResponse,
    RedactionSummaryResponse,
    RedactionLogResponse,
    RedactionLogEvent,
    RedactionEntity,
    FeedbackRequest,
    FeedbackResponse,
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

# Install the in-memory diagnostics buffer so the /api/diagnostics endpoints
# can serve recent log records to the frontend Diagnostics tab. Additive to
# the stdout stream handler — does not reroute or suppress normal logs.
from services.diagnostics import install as _install_diagnostics
_install_diagnostics(level=logging.INFO)

CLOUD_AI_PROVIDERS = ("anthropic", "openai", "grok", "google", "github", "ollama_cloud", "openai_compatible")
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


# Permission levels returned by sharing_service.check_collection_access.
# "owner" > "readwrite" > "read".
_WRITE_LEVELS = ("owner", "readwrite")


def require_collection_access(collection_id: str, user_id: str, required: str = "read") -> str:
    """Raise 403/404 unless ``user_id`` has at least ``required`` access on ``collection_id``.

    In single-user mode (``enable_multi_user=False``) the sharing service always
    returns "owner", so this is effectively a no-op. In multi-user mode it
    enforces that the user owns the collection or has been granted a share at
    the required permission level.

    Returns the granted permission level so callers can branch on it if
    needed (e.g. read-only views vs. owner-only settings).
    """
    access = sharing_service.check_collection_access(collection_id, user_id)
    if not access:
        # 404 rather than 403 so we don't leak the existence of collection
        # IDs owned by other users.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{collection_id}' not found",
        )
    if required in _WRITE_LEVELS and access not in _WRITE_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Write access required",
        )
    return access


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - initialize and cleanup services."""
    global _initialized

    async with mcp_server_lifespan():
        logger.info("Initializing Finn API...")

        # Sweep abandoned indexing jobs from the last process. Upload threads
        # die with the interpreter (daemon=False keeps them only as long as
        # the process lives) and reindex asyncio tasks die outright, so any
        # row still marked pending/running at startup is orphaned. Without
        # this, the UI would forever show a phantom job that has no worker.
        try:
            from services.app_database import app_db as _startup_app_db
            cleaned = _startup_app_db.mark_stale_jobs_as_orphaned()
            upload_orphans = cleaned.get("upload_jobs", [])
            reindex_orphans = cleaned.get("reindex_jobs", [])
            if upload_orphans or reindex_orphans:
                logger.warning(
                    "Orphan recovery: marked %d upload job(s) %s and %d reindex job(s) %s "
                    "as failed (backend restarted before they completed)",
                    len(upload_orphans), upload_orphans,
                    len(reindex_orphans), reindex_orphans,
                )
        except Exception as e:
            logger.error(f"Failed to sweep orphaned jobs on startup: {e}")

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

        logger.info("Finn API ready")
        logger.info(f"Data directory: {settings.data_dir}")
        logger.info(f"Embedded MCP server: {'enabled' if settings.enable_mcp else 'disabled'}")

        yield

        # Cleanup on shutdown
        logger.info("Shutting down Finn API...")
        indexer_manager.save_all()
        logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Finn API",
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
            content="<h1>Finn API</h1><p>Web interface not found. Visit <a href='/docs'>/docs</a> for API documentation.</p>",
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
    # Stamp last-active on every successful health ping so the welcome-back
    # card (R10.7) can compute days-since-last-launch on next mount. Best-effort
    # — a write failure must never break /health, which the desktop launcher
    # polls during boot.
    try:
        from services.app_database import app_db
        from datetime import datetime as _dt
        app_db.set_config("last_active_at", _dt.utcnow().isoformat())
    except Exception:
        pass

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


@app.get(
    "/api/version",
    summary="App version + last-known-available version + last-active timestamp",
    tags=["health"],
)
async def get_version():
    """Boot-time metadata for the frontend update banner (R10.4) and welcome-back
    card (R10.7).

    - ``version`` — the running build's user-visible version, from settings.
    - ``latest_known`` — the latest version the desktop launcher recorded at
      startup in ``data/latest_known.json``. ``None`` when the file is missing
      or unparseable; the frontend treats that as "no update known".
    - ``last_active_at`` / ``days_since_last_active`` — read from the app DB.
      Read-only; the value is updated by the next ``/health`` ping, so this
      response reflects the *previous* session's last-active timestamp on the
      first mount of a new session. Frontend uses this to decide whether to
      show the welcome-back card.
    """
    from services.app_database import app_db
    from datetime import datetime as _dt
    import json as _json

    latest_known = None
    try:
        latest_path = settings.data_dir / "latest_known.json"
        if latest_path.exists():
            with open(latest_path, "r", encoding="utf-8") as f:
                payload = _json.load(f)
            v = payload.get("version") if isinstance(payload, dict) else None
            if isinstance(v, str) and v.strip():
                latest_known = v.strip()
    except Exception as e:
        logger.debug(f"Could not read latest_known.json: {e}")

    last_active_at = app_db.get_config("last_active_at")
    days_since = None
    if isinstance(last_active_at, str) and last_active_at:
        try:
            prev = _dt.fromisoformat(last_active_at)
            days_since = (_dt.utcnow() - prev).total_seconds() / 86400.0
        except ValueError:
            last_active_at = None

    return {
        "version": settings.app_version,
        "latest_known": latest_known,
        "last_active_at": last_active_at,
        "days_since_last_active": days_since,
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
    user_id: str = Depends(get_current_user_id),
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

    require_collection_access(collection_id, user_id, required="readwrite")

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
    transcript_saved = False
    transcript_filename: Optional[str] = None

    for file in files:
        file_path = None
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

            # Audio files: transcribe → save .md → index the .md (not the raw audio)
            from services.audio_transcriber import is_audio_file as _is_audio
            if _is_audio(file_path):
                from services.audio_transcriber import get_transcriber, format_transcript_with_timestamps
                from datetime import datetime as _dt
                transcriber = get_transcriber(
                    model_size=settings.whisper_model,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute_type,
                )
                language = settings.whisper_language or None
                tr = transcriber.transcribe(file_path, language=language)
                now = _dt.now()
                duration_str = f"{int(tr.duration // 60)}:{int(tr.duration % 60):02d}"
                md_name = f"Meeting Notes - {now.strftime('%Y-%m-%d %H-%M')}.md"
                transcript_body = format_transcript_with_timestamps(tr)
                md_content = (
                    f"# Meeting Notes\n\n"
                    f"**Date:** {now.strftime('%Y-%m-%d %H:%M')}\n"
                    f"**Duration:** {duration_str}\n"
                    f"**Language:** {tr.language}\n"
                    f"**Source file:** {safe_filename}\n\n"
                    f"---\n\n"
                    f"{transcript_body}\n"
                )
                md_path = document_dir / md_name
                md_path.write_text(md_content, encoding="utf-8")
                logger.info(f"Saved transcript: {md_name}")
                # Index the .md, not the audio
                doc_metadata = indexer.index_document(md_path, md_name)
                transcript_saved = True
                transcript_filename = md_name
            else:
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
                if file_path and file_path.exists():
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
        transcript_saved=transcript_saved,
        transcript_filename=transcript_filename,
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
    user_id: str = Depends(get_current_user_id),
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

    require_collection_access(collection_id, user_id, required="readwrite")

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
    temp_dir = Path(tempfile.mkdtemp(prefix="finn_upload_"))

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
    user_id: str = Depends(get_current_user_id),
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

    require_collection_access(request.collection_id, user_id, required="readwrite")

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
    scope: str = "current",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
    user_id: str = Depends(get_current_user_id),
) -> SearchResponse:
    """
    Perform semantic similarity search over indexed documents.

    Args:
        search_request: Search query and options
        collection_id: Collection to search when scope='current' (default: "default")
        scope: 'current' searches one collection; 'all' fans out across every
            collection visible to the caller and merges by similarity score.
            Cross-collection scope is search's job; chat is always
            single-collection.

    Optionally enable AI enhancements by including 'ai' options in the request body.
    AI synthesis is only run in scope='current' — synthesizing across many
    households at once isn't useful and risks blending client data.
    """
    try:
        import time
        start_time = time.time()

        cross_collection = scope == "all"

        if not cross_collection:
            require_collection_access(collection_id, user_id, required="read")
            # Validate the single-collection target up front; no-op for scope=all.
            try:
                get_indexer(collection_id)
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

        base_url = str(request.base_url).rstrip('/')
        synthesis_text: Optional[str] = None
        ai_usage_data: Optional[Dict[str, Any]] = None

        if cross_collection:
            # Fan out across every collection visible to the caller. Each
            # result is attributed to its source Collection so the UI can
            # group/badge. Synthesis runs once over the merged top-K — same
            # privacy posture as single-collection synthesis (the AI
            # provider sees redacted chunk text), just over more collections.
            # Filter by caller in multi-user mode so cross-collection search
            # never spans households the user does not own.
            all_cols = collection_service.get_all_collections(user_id=user_id)
            collection_names_by_id: Dict[str, str] = {}
            merged: list = []
            for col in all_cols:
                col_id = col["id"]
                collection_names_by_id[col_id] = col.get("name") or col_id
                try:
                    col_indexer = get_indexer(col_id)
                except Exception as e:
                    logger.warning(f"Search: skipping collection '{col_id}': {e}")
                    continue
                try:
                    col_search = col_indexer.search(
                        query=search_request.query,
                        top_k=search_request.top_k,
                        mode=search_request.mode,
                        semantic_weight=search_request.semantic_weight,
                    )
                except Exception as e:
                    logger.warning(f"Search failed for collection '{col_id}': {e}")
                    continue
                for r in col_search["results"]:
                    r.collection_id = col_id
                    r.collection_name = collection_names_by_id[col_id]
                    r.pdf_url = f"{base_url}/documents/{r.document_id}/pdf?collection_id={col_id}"
                    r.page_url = f"{base_url}/documents/{r.document_id}/pdf?collection_id={col_id}#page={r.page_number}"
                    merged.append(r)
            merged.sort(key=lambda r: r.similarity_score, reverse=True)
            results = merged[:search_request.top_k]

            if ai_service and ai_options and ai_options.synthesize and results:
                # Only build overviews of collections that contributed at
                # least one hit — keeps the prompt focused and short. Each
                # collection's overview is wrapped in a header so the model
                # knows which client a fact belongs to. Structured tables
                # are intentionally not inlined in cross-collection
                # synthesis — they're a single-client analysis tool.
                contributing_ids = list({r.collection_id for r in results})
                overview_sections = []
                for cid in contributing_ids:
                    cname = collection_names_by_id.get(cid, cid)
                    try:
                        per_col_overview = _build_collection_overview([cid])
                    except Exception as e:
                        logger.warning(f"Failed overview for {cid}: {e}")
                        continue
                    overview_sections.append(
                        f"### Collection: {cname}\n{per_col_overview}"
                    )
                cross_overview = (
                    "Search ran across multiple collections (one per client "
                    "household). Each [Source N] citation below carries its "
                    "originating collection. When summarizing, name the "
                    "collection a fact came from so the advisor can tell "
                    "which client it relates to.\n\n"
                    + "\n\n".join(overview_sections)
                ) if overview_sections else None

                # Annotate each chunk with its collection so synthesis can
                # cite "[Source 3, Henderson Household]" instead of bare
                # filenames that could collide across clients.
                synth_input = [
                    {
                        "filename": f"{r.filename} ({r.collection_name})",
                        "page_number": r.page_number,
                        "text_snippet": r.text_snippet,
                    }
                    for r in results
                ]
                try:
                    synth_result = ai_service.synthesize_results(
                        search_request.query,
                        synth_input,
                        collection_overview=cross_overview,
                        structured_context=None,
                    )
                    synthesis_text = synth_result.get("synthesis")
                    usage = synth_result.get("usage")
                    if usage:
                        from models.schemas import AIUsage, AIUsageDetail
                        ai_usage_data = AIUsage(
                            features_used=["synthesis"],
                            synthesis=AIUsageDetail(**usage),
                            total_input_tokens=usage["input_tokens"],
                            total_output_tokens=usage["output_tokens"],
                        )
                except Exception as e:
                    logger.warning(f"Cross-collection synthesis failed: {e}")
        else:
            indexer = get_indexer(collection_id)

            # Build collection overview so AI synthesis can answer
            # meta-questions like "how many sources are in this collection?"
            collection_overview = None
            structured_context_str = None
            skip_filenames: set = set()
            if ai_service and ai_options and ai_options.synthesize:
                try:
                    collection_overview = _build_collection_overview([collection_id])
                except Exception as e:
                    logger.warning(f"Failed to build collection overview for search: {e}")
                # Inline small CSV/XLSX tables in full so synthesis never has
                # to rely on top-K chunk recall for numeric questions.
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
            synthesis_text = search_result.get("synthesis")
            ai_usage_data = search_result.get("ai_usage")

            # Resolve the active collection's display name once so the UI can
            # show the same attribution regardless of scope.
            try:
                col_meta = collection_service.get_collection(collection_id)
                col_name = col_meta.get("name") if col_meta else collection_id
            except Exception:
                col_name = collection_id
            for result in results:
                result.collection_id = collection_id
                result.collection_name = col_name
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
            synthesis=synthesis_text,
            ai_usage=ai_usage_data,
        )

    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
        )


from services.collection_overview import build_collection_overview as _build_collection_overview


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
    user_id: str = Depends(get_current_user_id),
):
    """Streaming chat endpoint (SSE).

    Drives the AgenticEngine and forwards its event stream to the client.
    See services.chat.engine for the event vocabulary; the wire shape
    matches what the frontend has been parsing all along (tool_start,
    tool_end, thinking, text_delta, sources, done, error).
    """
    require_collection_access(collection_id, user_id, required="read")
    import json as _json
    import uuid as _uuid
    from services.chat.context import build_chat_turn
    from services.chat.engine import run_agentic, run_rag_synthesis
    from services.diagnostics import record_chat_event as _record_diag

    # Stable per-turn id so the diagnostics view can group all events that
    # belong to the same chat turn. Generated server-side; the client never
    # sees it.
    turn_id = _uuid.uuid4().hex[:12]

    def _diag(type_: str, payload: dict) -> None:
        _record_diag(
            turn_id=turn_id,
            collection_id=collection_id,
            type_=type_,
            payload=payload,
        )

    async def generate():
        try:
            try:
                provider = _build_ai_provider_from_headers(
                    chat_request.provider, x_ai_key,
                    x_ai_model or x_anthropic_model or x_openai_model or x_ollama_model,
                    x_ai_base_url,
                    user_id=user_id,
                )
            except Exception as e:
                _diag("error", {"stage": "provider_init", "message": str(e)})
                yield f"data: {_json.dumps({'type':'error','message':f'Failed to initialize AI provider: {e}'})}\n\n"
                return

            try:
                prepared = build_chat_turn(
                    provider=provider,
                    chat_request=chat_request,
                    collection_id=collection_id,
                    expertise_store=expertise_store,
                    max_iterations=8,
                    max_tokens=4096,
                )
            except ValueError as e:
                _diag("error", {"stage": "context_build", "message": str(e)})
                yield f"data: {_json.dumps({'type':'error','message':str(e)})}\n\n"
                return
            except Exception as e:
                logger.exception("Stream chat: context build failed")
                _diag("error", {"stage": "context_build", "message": str(e)})
                yield f"data: {_json.dumps({'type':'error','message':f'Failed to prepare chat context: {e}'})}\n\n"
                return

            _diag("turn_start", {
                "provider": chat_request.provider,
                "messages": len(chat_request.messages or []),
                "collection_id": collection_id,
                "engine": "rag_synthesis" if prepared.use_simple_rag else "agentic",
            })

            executed_results: list[dict] = []
            usage = {"input_tokens": 0, "output_tokens": 0, "model": None}
            pending_args: dict[str, dict] = {}

            # Engine selection: general/meetings collections with no large
            # structured tables skip the tool loop and stream a single
            # completion over the chunks ChatContext already retrieved. The
            # event stream below handles both shapes — run_rag_synthesis
            # simply never yields tool_start/tool_end/thinking, so those
            # branches stay dormant on the simple path.
            engine_fn = run_rag_synthesis if prepared.use_simple_rag else run_agentic

            async for ev in engine_fn(prepared.turn):
                t = ev.get("type")
                if t == "thinking":
                    executed_results.append({
                        "tool": "_thinking",
                        "args": {"iteration": ev.get("iteration", 0)},
                        "result": {"text": ev.get("text", "")},
                    })
                    _diag("thinking", {"text": ev.get("text", ""), "iteration": ev.get("iteration", 0)})
                    yield f"data: {_json.dumps({'type':'thinking','text':ev.get('text','')})}\n\n"
                elif t == "tool_start":
                    pending_args[ev["tool"]] = ev.get("args") or {}
                    _diag("tool_start", {"tool": ev["tool"], "args": ev.get("args") or {}})
                    yield f"data: {_json.dumps({'type':'tool_start','tool':ev['tool'],'args':ev.get('args') or {}})}\n\n"
                elif t == "tool_end":
                    executed_results.append({
                        "tool": ev["tool"],
                        "args": pending_args.pop(ev["tool"], {}),
                        "result": ev.get("result", {}),
                    })
                    _diag("tool_end", {"tool": ev["tool"], "result": ev.get("result") or {}})
                    yield f"data: {_json.dumps({'type':'tool_end','tool':ev['tool'],'result':ev.get('result') or {}})}\n\n"
                elif t == "text_delta":
                    yield f"data: {_json.dumps({'type':'text_delta','delta':ev.get('delta','')})}\n\n"
                elif t == "citation":
                    citation_payload = {
                        "type": "citation",
                        "document_id": ev.get("document_id"),
                        "chunk_id": ev.get("chunk_id"),
                        "page_number": ev.get("page_number"),
                        "filename": ev.get("filename"),
                        "cited_text": ev.get("cited_text"),
                        "start_char": ev.get("start_char"),
                        "end_char": ev.get("end_char"),
                    }
                    yield f"data: {_json.dumps(citation_payload)}\n\n"
                elif t == "done":
                    usage = ev.get("usage", usage)
                elif t == "error":
                    _diag("error", {"stage": "engine", "message": ev.get("message", "Unknown error")})
                    yield f"data: {_json.dumps({'type':'error','message':ev.get('message','Unknown error')})}\n\n"
                    return

            base_url = str(request.base_url).rstrip("/")
            sources_data = [
                {
                    "filename": r.filename,
                    "page_number": r.page_number,
                    "text_snippet": r.text_snippet,
                    "similarity_score": r.similarity_score,
                    "document_id": r.document_id,
                    "pdf_url": f"{base_url}/documents/{r.document_id}/pdf?collection_id={collection_id}",
                    "page_url": f"{base_url}/documents/{r.document_id}/pdf?collection_id={collection_id}#page={r.page_number}",
                }
                for r in prepared.filtered_results
            ]
            yield f"data: {_json.dumps({'type':'sources','sources':sources_data})}\n\n"

            _diag("done", {
                "usage": usage,
                "tool_count": sum(1 for r in executed_results if r.get("tool") and not r["tool"].startswith("_")),
                "source_count": len(sources_data),
            })
            yield f"data: {_json.dumps({'type':'done','usage':usage,'structured_results':executed_results})}\n\n"

        except Exception as e:
            logger.exception("Stream chat failed")
            _diag("error", {"stage": "endpoint", "message": str(e)})
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
async def index_local_file(
    request: IndexLocalRequest,
    user_id: str = Depends(get_current_user_id),
) -> DocumentMetadata:
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

    require_collection_access(request.collection_id, user_id, required="readwrite")

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
async def index_local_files_async(
    request: IndexLocalAsyncRequest,
    user_id: str = Depends(get_current_user_id),
) -> UploadJobResponse:
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

    require_collection_access(request.collection_id, user_id, required="readwrite")

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
        capabilities: dict | None = None
        if valid:
            try:
                capabilities = provider.probe_capabilities().to_dict()
            except Exception as cap_err:
                logger.warning(
                    "Capability probe failed for %s: %s", x_ai_provider, cap_err,
                )
                # Fall back to declared (unprobed) capabilities so the UI
                # still has something useful to show.
                try:
                    capabilities = provider.capabilities().to_dict()
                except Exception:
                    capabilities = None
        return {"valid": valid, "error": None, "capabilities": capabilities}
    except Exception as e:
        error_str = str(e)
        logger.error(f"API key validation error for {x_ai_provider}: {e}")

        # Provide more helpful error messages
        if "quota" in error_str.lower() or "insufficient_quota" in error_str.lower():
            return {"valid": False, "error": "Your API key has exceeded its quota. Please add credits to your account."}
        elif "rate" in error_str.lower() and "limit" in error_str.lower():
            return {"valid": False, "error": "Rate limit exceeded. Please wait a moment and try again."}
        elif any(kw in error_str.lower() for kw in ("connection error", "connect error", "connection refused", "name or service not known", "failed to establish")):
            host = "ollama.com" if x_ai_provider == "ollama_cloud" else "the provider's API"
            return {"valid": False, "error": f"Could not reach {host}. Check your network connection and try again."}
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
    user_id: str = Depends(get_current_user_id),
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
    require_collection_access(request.collection_id, user_id, required="read")

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
        # Lookup order matches the in-app chat resolver:
        # explicit header > per-user beta-seeded key > legacy global agent key.
        # The legacy fallback stays so that single-user / pre-multi-user deployments
        # that still rely on /api/agent/config keep working until that path is retired.
        api_key = (
            x_ai_key
            or app_db.get_user_api_key(user_id, x_ai_provider)
            or app_db.get_agent_api_key(x_ai_provider)
        )
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

    # Generate answer via the OneShotEngine — same code path as /notes,
    # /followup, and any future drafting endpoint. Phase 2 streaming will
    # reach all of them at once.
    from services.chat.engine import complete_one_shot
    try:
        synthesis = await complete_one_shot(
            provider=provider, prompt=prompt,
            model=provider.QUALITY_MODEL, max_tokens=1500,
        )
    except RuntimeError as e:
        logger.error(f"AI synthesis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI synthesis failed: {e}",
        )
    answer = synthesis["text"]
    total_tokens += synthesis["usage"]["input_tokens"] + synthesis["usage"]["output_tokens"]
    model_used = synthesis["usage"]["model"]

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
    Finn instance is properly secured.
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
    "/api/user/ai-providers",
    summary="List the AI providers the operator has provisioned for the current user",
    tags=["agent"],
)
async def list_user_ai_providers(
    user_id: str = Depends(get_current_user_id),
):
    """Return provider entries the current user can call without supplying a key.

    Each entry is ``{provider, key_preview, updated_at, source}`` where
    ``source`` is ``"db"`` for keys already persisted in ``user_api_keys`` and
    ``"beta_file"`` for providers listed in ``beta_keys_file`` that haven't
    been seeded yet for this session.

    Designed for the frontend to render the chat-provider picker in closed
    beta without exposing the underlying key. **Never returns the raw
    api_key.** The endpoint is read-only and gated by the same Cf-Access /
    X-User-ID identity middleware as every other per-user route.
    """
    from services.beta_keys import list_seeded_providers_for

    entries = app_db.list_user_api_keys(user_id)
    seen = {e["provider"] for e in entries}
    for entry in entries:
        entry["source"] = "db"

    for provider in list_seeded_providers_for(user_id):
        if provider in seen:
            continue
        entries.append({
            "provider": provider,
            "key_preview": "***",
            "updated_at": None,
            "source": "beta_file",
        })

    return {"providers": entries}


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
async def get_collection_mcp_export_profile(
    collection_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Get the saved MCP export profile for a collection."""
    require_collection_access(collection_id, user_id, required="read")
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
async def update_collection_mcp_export_profile(
    collection_id: str,
    updates: dict,
    user_id: str = Depends(get_current_user_id),
):
    """Save MCP export defaults for a specific collection."""
    require_collection_access(collection_id, user_id, required="readwrite")
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
    """Create a named mapping from a project to a Finn collection."""
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
async def start_collection_reindex(
    collection_id: str,
    user_id: str = Depends(get_current_user_id),
):
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
    require_collection_access(collection_id, user_id, required="readwrite")
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


@app.post(
    "/api/reindex/{job_id}/cancel",
    summary="Cancel a running re-indexing job",
    tags=["admin"],
)
async def cancel_reindex_job(job_id: int):
    """Request cancellation of an in-flight re-indexing job.

    Best-effort: the current document finishes before cancellation takes
    effect (otherwise we'd risk leaving the metadata SQLite torn). The job
    is marked ``cancelled`` and the partial index is persisted to disk.
    """
    from services.app_database import app_db

    job = app_db.get_reindex_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Re-index job {job_id} not found",
        )
    if job["status"] not in ("pending", "running"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job {job_id} is not active (status: {job['status']})",
        )

    cancelled = reindex_service.cancel_job(job_id)
    if cancelled:
        return {
            "message": f"Cancellation requested for re-index job {job_id}",
            "job_id": job_id,
            "status": "cancelling",
        }

    # Job exists in DB as running but the in-process task is gone (likely
    # an orphan that startup recovery missed, or a race). Mark it cancelled
    # directly so the UI doesn't sit forever.
    logger.warning(f"Reindex job {job_id} active task not found, force-marking as cancelled")
    app_db.update_reindex_job(
        job_id,
        status="cancelled",
        error="Force cancelled (no active task — likely orphaned)",
    )
    return {
        "message": f"Job {job_id} force-cancelled (no active task was found)",
        "job_id": job_id,
        "status": "cancelled",
    }


@app.get(
    "/api/jobs/active",
    summary="Get every active indexing job (upload + local-index + reindex)",
    tags=["admin"],
)
async def get_active_indexing_jobs_endpoint():
    """Single source of truth for "is any indexing happening right now?"

    Returns a flat list in a uniform shape so the UI can disable both the
    upload and reindex controls based on one query.
    """
    from services.indexing_lock import get_active_indexing_jobs
    jobs = get_active_indexing_jobs()
    return {"jobs": jobs, "count": len(jobs)}


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

    # Surface collection_id at the top level — the UI uses it to decide
    # which collection's sidebar to refresh on completion.
    snapshot = job.get("config_snapshot")
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except json.JSONDecodeError:
            snapshot = {}
    snapshot = snapshot or {}

    return {
        **job,
        "config_snapshot": snapshot,
        "collection_id": snapshot.get("collection_id"),
        "progress_percent": round(progress, 1),
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
    user_id: str = Depends(get_current_user_id),
) -> DocumentListResponse:
    """
    List all indexed documents with their metadata.

    Args:
        collection_id: Collection to list documents from (default: "default")

    Returns filename, page count, and chunk count for each document.
    """
    require_collection_access(collection_id, user_id, required="read")
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
    user_id: str = Depends(get_current_user_id),
):
    """
    Download the document file for a specific document.

    Args:
        document_id: Document ID
        collection_id: Collection containing the document (default: "default")

    For PDFs, the URL can include #page=N to open at a specific page in the browser.
    """
    require_collection_access(collection_id, user_id, required="read")
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
    user_id: str = Depends(get_current_user_id),
) -> DocumentChunksResponse:
    """
    Return indexed chunks for a document, with optional OCR field/value extraction.

    Args:
        document_id: Document ID
        collection_id: Collection containing the document
        include_fields: If true, extract key/value fields for OCR/hybrid chunks
        max_chunks: Maximum number of chunks to return (safety limit)
    """
    require_collection_access(collection_id, user_id, required="read")
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
    user_id: str = Depends(get_current_user_id),
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
    require_collection_access(collection_id, user_id, required="readwrite")
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
    user_id: str = Depends(get_current_user_id),
):
    """
    Generate a pre-meeting portfolio brief for *collection_id*.

    Calls the brief_generator directly (no LLM token cost) and returns
    a structured JSON brief with sections: household_summary, accounts,
    top_positions, tax_loss_candidates, concentration_alerts,
    cash_drag_alerts, sector_allocation, generated_at.
    """
    require_collection_access(collection_id, user_id, required="read")
    from services.brief_generator import generate_meeting_brief as _gen_brief

    try:
        indexer = get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        store = indexer.vector_store.holdings_store
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


@app.post(
    "/api/collections/{collection_id}/tlh",
    tags=["chat"],
    summary="Build a household-aware Tax-Loss Harvesting plan",
)
async def generate_tlh_plan_endpoint(
    collection_id: str,
    household_collection_ids: list[str] | None = Body(default=None),
    min_loss: float = Body(default=500.0),
    min_loss_pct: float | None = Body(default=None),
    max_candidates: int = Body(default=25),
    user_id: str = Depends(get_current_user_id),
):
    """
    Build a Tax-Loss Harvesting plan for *collection_id*.

    Composes scan_unrealized_losses (taxable accounts only, ST-first ordering),
    gain_loss_budget (offset capacity), suggest_replacements (non-substantially-
    identical swaps), and check_wash_sale (household-wide — including any
    `household_collection_ids` passed in) into a single plan with explicit
    guardrails. Calls the primitives directly, no LLM token cost.
    """
    require_collection_access(collection_id, user_id, required="read")
    from services.financial.tlh import build_harvest_plan

    try:
        indexer = get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        primary_store = indexer.vector_store.holdings_store
    except AttributeError:
        raise HTTPException(status_code=422, detail="No structured store found for this collection.")

    household_stores = [primary_store]
    seen_ids = {collection_id}
    for cid in (household_collection_ids or []):
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        # Each additional household collection must also be accessible to
        # the caller — without this check, a user with access to one
        # collection could pull holdings from any UUID they guess.
        if not sharing_service.check_collection_access(cid, user_id):
            logger.warning("TLH: skipping household collection %s — no access", cid)
            continue
        try:
            extra_indexer = get_indexer(cid)
            household_stores.append(extra_indexer.vector_store.holdings_store)
        except (ValueError, AttributeError) as exc:
            logger.warning("TLH: skipping household collection %s: %s", cid, exc)

    try:
        plan = build_harvest_plan(
            primary_store,
            collection_id=collection_id,
            household_stores=household_stores,
            min_loss=min_loss,
            min_loss_pct=min_loss_pct,
            max_candidates=max_candidates,
        )
    except Exception as e:
        logger.error("TLH plan failed for collection %s: %s", collection_id, e)
        raise HTTPException(status_code=500, detail=f"TLH plan failed: {e}")

    return {
        "collection_id": plan.collection_id,
        "generated_at": plan.generated_at,
        "candidates": plan.candidates,
        "budget": plan.budget,
        "wash_sale_warnings": plan.wash_sale_warnings,
        "guardrails": plan.guardrails,
        "totals": plan.totals,
    }


@app.post(
    "/api/collections/{collection_id}/meetings/extract",
    tags=["chat"],
    summary="Run the v4.5 structured-extraction pass on a transcript",
)
async def extract_meeting_notes_endpoint(
    collection_id: str,
    body: ExtractMeetingNotesRequest,
    x_ai_key: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ollama_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
    user_id: str = Depends(get_current_user_id),
):
    """Run a single LLM extraction pass on a transcript and persist the notes.

    The background trigger in upload_service runs automatically when a stored
    agent API key is present, but BYO-key users (the default) need an explicit
    request. The frontend calls this endpoint from the post-transcription
    nudge banner so the advisor can choose when (and with which provider) to
    spend the tokens.
    """
    require_collection_access(collection_id, user_id, required="readwrite")
    from services.meeting_notes import (
        extract_meeting_notes as _extract,
        transcript_text_from_chunks,
    )

    try:
        indexer = get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        provider = _build_ai_provider_from_headers(
            body.provider, x_ai_key,
            x_ai_model or x_anthropic_model or x_openai_model or x_ollama_model,
            x_ai_base_url,
            user_id=user_id,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AI provider error: {e}")

    try:
        chunks = indexer.vector_store.metadata_store.get_chunks_by_document(body.document_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load transcript: {e}")

    if not chunks:
        raise HTTPException(status_code=404, detail=f"No chunks found for document {body.document_id}")

    transcript_text = transcript_text_from_chunks(chunks)
    if not transcript_text:
        raise HTTPException(status_code=422, detail="Transcript has no extractable text.")

    notes = _extract(
        transcript_text,
        collection_id=collection_id,
        provider=provider,
        document_id=body.document_id,
        model=getattr(provider, "QUALITY_MODEL", None),
    )

    store = indexer.vector_store.meeting_notes_store
    try:
        store.save(
            document_id=body.document_id,
            collection_id=collection_id,
            notes=notes,
        )
    except Exception as e:
        logger.error("Failed to save meeting notes for %s: %s", body.document_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to persist notes: {e}")

    record = store.get(body.document_id)
    return {
        "collection_id": collection_id,
        "document_id": body.document_id,
        "notes": record,
    }


@app.get(
    "/api/collections/{collection_id}/summary",
    tags=["collections"],
    summary="Per-collection portfolio snapshot for the Collection card",
)
async def get_collection_summary(
    collection_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Return positions/accounts/most-recent-export for a Collection.

    Counts Positions across every brokerage table (using the ``__by_symbol``
    rollup view when available, falling back to row count). Counts distinct
    Accounts from the account-role column. ``most_recent_export_iso`` is the
    most recent ``csv_schemas.created_at`` — i.e. the last brokerage export
    that was actually ingested into a typed Holdings table — so the card
    answers "how fresh is this Collection?" rather than "when was anything
    last uploaded?".
    """
    access = sharing_service.check_collection_access(collection_id, user_id)
    if not access:
        raise HTTPException(status_code=403, detail="You do not have access to this collection")

    try:
        indexer = get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        store = indexer.vector_store.holdings_store
    except AttributeError:
        return {
            "collection_id": collection_id,
            "kind": "general",
            "positions": 0,
            "accounts": 0,
            "most_recent_export_iso": None,
            "source_files": [],
            "financial_table_count": 0,
            "transcript_count": 0,
            "document_count": 0,
        }

    from services.collection_summary import compute_collection_summary
    from services.collection_context import detect_collection_kind

    summary = compute_collection_summary(store, collection_id)
    ctx = detect_collection_kind(indexer, collection_id)
    summary.update({
        "kind": ctx.kind,
        "financial_table_count": ctx.financial_table_count,
        "transcript_count": ctx.transcript_count,
        "document_count": ctx.document_count,
    })
    return summary


@app.get(
    "/api/collections/{collection_id}/transcript/latest",
    tags=["collections"],
    summary="Latest Meeting Notes transcript content",
)
async def get_latest_transcript(
    collection_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Return the filename and content of the most recent meeting transcript.

    Powers the Note of Record "Preview redactions" toggle (R9.7): the modal
    needs the raw transcript text to run a dry-run through the redaction
    engine *before* the AI provider call, so the advisor can see exactly
    what the LLM will receive. Reuses ``_find_recent_transcript`` so prompt
    construction and preview operate on identical text.
    """
    access = sharing_service.check_collection_access(collection_id, user_id)
    if not access:
        raise HTTPException(status_code=403, detail="You do not have access to this collection")

    try:
        indexer = get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    docs = indexer.list_documents()
    notes_docs = [d for d in docs if d.get("filename", "").startswith("Meeting Notes")]
    if not notes_docs:
        return {"filename": None, "content": ""}

    notes_docs.sort(key=lambda d: d.get("filename", ""), reverse=True)
    latest = notes_docs[0]
    return {
        "filename": latest.get("filename"),
        "document_id": latest.get("document_id"),
        "content": _find_recent_transcript(collection_id),
    }


# ── Meeting Capture endpoints (v4.5) ──────────────────────────────────────────

def _build_ai_provider_from_headers(
    provider_name: str,
    ai_key: str,
    model: str,
    base_url: str,
    user_id: Optional[str] = None,
):
    """Shared helper to construct an AI provider from request headers.

    When ``ai_key`` is empty and ``user_id`` is provided, the resolver falls
    back to the operator-provisioned key in ``user_api_keys`` for that user.
    This is what lets closed-beta users hit chat / notes / brief without ever
    touching the Settings tab — keys are seeded from ``beta_keys_file`` on
    first login (see services.beta_keys).
    """
    if provider_name == "ollama":
        extra: dict = {"model": model or "llama3.2"}
        if base_url:
            extra["base_url"] = base_url
        return create_provider("ollama", **extra)

    if not ai_key and user_id and provider_name and provider_name != "openai_compatible":
        try:
            ai_key = app_db.get_user_api_key(user_id, provider_name) or ai_key
        except Exception as exc:
            logger.debug("Per-user key lookup failed for %s/%s: %s", user_id, provider_name, exc)

    extra = {}
    if model:
        extra["model"] = model
    if base_url:
        extra["base_url"] = base_url
    return create_provider(provider_name, ai_key, **extra)


def _find_recent_transcript(collection_id: str) -> str:
    """Return the text of the most recently indexed Meeting Notes doc, or empty string.

    Pulls chunks directly from the metadata store keyed by document_id rather
    than running a keyword search — the previous implementation searched for
    the literal phrase "meeting notes transcript" against the indexer and
    almost always returned nothing because actual transcript chunks contain
    the meeting's content, not those keywords. The result was that
    /notes/stream silently fell through to "(No transcript found …)" even
    when a transcript clearly existed in the collection.
    """
    try:
        indexer = get_indexer(collection_id)
        docs = indexer.list_documents()
        notes_docs = [d for d in docs if d.get("filename", "").startswith("Meeting Notes")]
        if not notes_docs:
            return ""
        # Most recent by filename (timestamp embedded: 'Meeting Notes - YYYY-MM-DD HH-MM.md')
        notes_docs.sort(key=lambda d: d.get("filename", ""), reverse=True)
        latest_id = notes_docs[0]["document_id"]

        metadata_store = indexer.vector_store.metadata_store
        all_chunks = metadata_store.get_chunks_by_document(latest_id)
        # Preserve document order so the prompt receives a coherent narrative.
        all_chunks.sort(key=lambda c: (c.get("page_number", 0), c.get("chunk_index", 0)))
        texts = [c.get("text", "") for c in all_chunks if c.get("text")]
        return "\n\n".join(texts)
    except Exception as e:
        logger.warning(f"Could not find recent transcript for {collection_id}: {e}")
        return ""


def _get_brief_text(collection_id: str) -> str:
    """Return a text summary of the portfolio brief for use in prompts, or empty string."""
    try:
        from services.brief_generator import generate_meeting_brief as _gen_brief
        indexer = get_indexer(collection_id)
        store = indexer.vector_store.holdings_store
        brief = _gen_brief(store, collection_id=collection_id)
        hs = brief.get("household_summary", {})
        lines = [
            f"Total market value: ${hs.get('total_market_value', 0):,.0f}",
            f"Unrealized G/L: ${hs.get('total_unrealized_pnl', 0):,.0f}",
        ]
        top = brief.get("top_positions", [])[:5]
        if top:
            lines.append("Top positions: " + ", ".join(
                f"{p.get('ticker') or p.get('name', '?')} (${p.get('market_value', 0):,.0f})"
                for p in top
            ))
        return "\n".join(lines)
    except Exception:
        return ""


def _format_chat_context(messages) -> str:
    chat_lines = []
    for m in (messages or [])[-30:]:
        if m.content and not m.content.startswith("/"):
            chat_lines.append(f"{m.role.upper()}: {m.content[:500]}")
    return "\n".join(chat_lines)


def _build_notes_prompt(collection_id: str, messages) -> str:
    transcript = _find_recent_transcript(collection_id)
    portfolio_summary = _get_brief_text(collection_id)
    chat_context = _format_chat_context(messages)

    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y-%m-%d")

    return f"""You are a compliance assistant for a registered investment advisor.
Generate a structured Note of Record for today's client meeting ({today}).
Use the transcript excerpt, advisor chat, and portfolio summary below.
If information is missing, use reasonable placeholders marked [UNKNOWN].

## TRANSCRIPT EXCERPT
{transcript or "(No transcript found — summarize from chat context)"}

## ADVISOR CHAT CONTEXT
{chat_context or "(No chat history provided)"}

## PORTFOLIO SUMMARY
{portfolio_summary or "(No portfolio data available)"}

---
Draft the Note of Record with these sections:
1. **Date & Attendees** — infer names from transcript if possible
2. **Topics Discussed** — bullet list from transcript
3. **Recommendations Made** — from advisor chat/tool calls
4. **Action Items** — concrete follow-ups with owner and due date

Do NOT include any "Redaction Confirmation" or compliance-affirmation
section — a real redaction-summary footer is stamped onto the saved file
from the audit log, and a model-asserted affirmation would be misleading.

Be concise and professional. Use bullet points where appropriate.
"""


def _build_followup_prompt(collection_id: str, messages) -> str:
    transcript = _find_recent_transcript(collection_id)
    portfolio_summary = _get_brief_text(collection_id)
    chat_context = _format_chat_context(messages)

    from datetime import datetime as _dt
    today = _dt.now().strftime("%B %d, %Y")

    return f"""You are a compliance-aware assistant for a registered investment advisor.
Draft a professional follow-up email to the client after today's meeting ({today}).
The email must be client-safe — do NOT include internal compliance notes or sensitive portfolio numbers unless rounded.
Use the information below to make it specific and actionable.

## TRANSCRIPT EXCERPT
{transcript or "(No transcript found — use chat context)"}

## ADVISOR CHAT CONTEXT
{chat_context or "(No chat history provided)"}

## PORTFOLIO SUMMARY
{portfolio_summary or "(No portfolio data available)"}

---
Draft the follow-up email with:
- Subject line
- Professional greeting
- Brief recap of topics discussed
- Action items agreed upon (with any deadlines)
- Next steps / next meeting mention
- Professional sign-off

Keep it under 250 words. Do not include specific dollar amounts or account numbers.
Write [CLIENT NAME] and [ADVISOR NAME] as placeholders.
"""


@app.post(
    "/api/collections/{collection_id}/notes",
    response_model=NoteResponse,
    tags=["chat"],
    summary="Generate compliance Note of Record from recent meeting",
)
async def generate_compliance_note(
    collection_id: str,
    body: NotesRequest,
    x_ai_key: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ollama_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
    user_id: str = Depends(get_current_user_id),
):
    """Draft a structured compliance Note of Record using the most recent transcript + chat history."""
    require_collection_access(collection_id, user_id, required="read")
    try:
        provider = _build_ai_provider_from_headers(
            body.provider, x_ai_key,
            x_ai_model or x_anthropic_model or x_openai_model or x_ollama_model,
            x_ai_base_url,
            user_id=user_id,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AI provider error: {e}")

    prompt = _build_notes_prompt(collection_id, body.messages)

    from services.chat.engine import complete_one_shot
    try:
        result = await complete_one_shot(
            provider=provider, prompt=prompt,
            model=provider.QUALITY_MODEL, max_tokens=1500,
        )
        raw_note = result["text"]
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")

    from services.privacy.redaction_middleware import redact_text_for_ai
    redacted = redact_text_for_ai(raw_note, collection_id=collection_id, source_label="notes_output")
    return NoteResponse(content=redacted)


@app.post(
    "/api/collections/{collection_id}/followup",
    response_model=NoteResponse,
    tags=["chat"],
    summary="Draft a client-safe follow-up email from the recent meeting",
)
async def generate_followup_email(
    collection_id: str,
    body: FollowupRequest,
    x_ai_key: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ollama_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
    user_id: str = Depends(get_current_user_id),
):
    """Draft a PII-redacted client-safe follow-up email summarizing the meeting."""
    require_collection_access(collection_id, user_id, required="read")
    try:
        provider = _build_ai_provider_from_headers(
            body.provider, x_ai_key,
            x_ai_model or x_anthropic_model or x_openai_model or x_ollama_model,
            x_ai_base_url,
            user_id=user_id,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AI provider error: {e}")

    prompt = _build_followup_prompt(collection_id, body.messages)

    from services.chat.engine import complete_one_shot
    try:
        result = await complete_one_shot(
            provider=provider, prompt=prompt,
            model=provider.QUALITY_MODEL, max_tokens=800,
        )
        raw_email = result["text"]
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")

    from services.privacy.redaction_middleware import redact_text_for_ai
    redacted = redact_text_for_ai(raw_email, collection_id=collection_id, source_label="followup_output")
    return NoteResponse(content=redacted)


async def _stream_one_shot_with_redaction(
    *,
    provider,
    prompt: str,
    max_tokens: int,
    collection_id: str,
    source_label: str,
):
    """SSE generator for one-shot drafting endpoints (/notes/stream, /followup/stream).

    Streams raw deltas during generation so the advisor sees progress, then on
    `done` emits the redacted final text. The frontend swaps the streamed
    content for the redacted version — guaranteeing the saved note is scrubbed
    of PII even if the LLM echoed something the prompt-side redaction missed.
    """
    import json as _json
    from services.chat.engine import ChatTurn, run_one_shot
    from services.privacy.redaction_middleware import redact_text_for_ai

    turn = ChatTurn(
        provider=provider,
        system_text="",
        messages=[{"role": "user", "content": prompt}],
        model=provider.QUALITY_MODEL,
        max_tokens=max_tokens,
    )

    text_parts: list[str] = []
    try:
        async for ev in run_one_shot(turn):
            t = ev.get("type")
            if t == "text_delta":
                delta = ev.get("delta", "")
                text_parts.append(delta)
                yield f"data: {_json.dumps({'type': 'text_delta', 'delta': delta})}\n\n"
            elif t == "error":
                yield f"data: {_json.dumps({'type': 'error', 'message': ev.get('message', 'Unknown error')})}\n\n"
                return
            elif t == "done":
                raw_text = ev.get("response_text") or "".join(text_parts)
                redacted = redact_text_for_ai(
                    raw_text, collection_id=collection_id, source_label=source_label
                )
                yield f"data: {_json.dumps({'type': 'done', 'content': redacted, 'usage': ev.get('usage', {})})}\n\n"
    except Exception as e:
        logger.exception(f"Streaming {source_label} failed")
        yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"


@app.post(
    "/api/collections/{collection_id}/notes/stream",
    tags=["chat"],
    summary="Stream a compliance Note of Record (SSE)",
)
async def stream_compliance_note(
    collection_id: str,
    body: NotesRequest,
    x_ai_key: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ollama_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
    user_id: str = Depends(get_current_user_id),
):
    """SSE variant of /notes — streams text_delta events as the LLM generates,
    then emits a `done` event carrying the redacted final content."""
    require_collection_access(collection_id, user_id, required="read")
    import json as _json

    async def generate():
        try:
            provider = _build_ai_provider_from_headers(
                body.provider, x_ai_key,
                x_ai_model or x_anthropic_model or x_openai_model or x_ollama_model,
                x_ai_base_url,
                user_id=user_id,
            )
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'message': f'AI provider error: {e}'})}\n\n"
            return

        prompt = _build_notes_prompt(collection_id, body.messages)
        async for chunk in _stream_one_shot_with_redaction(
            provider=provider, prompt=prompt, max_tokens=1500,
            collection_id=collection_id, source_label="notes_output",
        ):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.post(
    "/api/collections/{collection_id}/followup/stream",
    tags=["chat"],
    summary="Stream a client-safe follow-up email (SSE)",
)
async def stream_followup_email(
    collection_id: str,
    body: FollowupRequest,
    x_ai_key: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ollama_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
    user_id: str = Depends(get_current_user_id),
):
    """SSE variant of /followup — streams text_delta events as the LLM generates,
    then emits a `done` event carrying the redacted final content."""
    require_collection_access(collection_id, user_id, required="read")
    import json as _json

    async def generate():
        try:
            provider = _build_ai_provider_from_headers(
                body.provider, x_ai_key,
                x_ai_model or x_anthropic_model or x_openai_model or x_ollama_model,
                x_ai_base_url,
                user_id=user_id,
            )
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'message': f'AI provider error: {e}'})}\n\n"
            return

        prompt = _build_followup_prompt(collection_id, body.messages)
        async for chunk in _stream_one_shot_with_redaction(
            provider=provider, prompt=prompt, max_tokens=800,
            collection_id=collection_id, source_label="followup_output",
        ):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


def _build_redaction_footer(
    *, collection_id: str, since: str | None
) -> str:
    """Compose a redaction-summary footer for a saved Note of Record.

    Returns a markdown block listing entity-type counts pulled from the audit
    log for ``collection_id`` since ``since`` (the drafting session start).
    Empty string when redaction is disabled, the engine isn't installed, or
    no redactions occurred — so the footer is honest about its own state
    rather than producing a misleading "0 redactions" line on a vanilla
    install where redaction was never running.
    """
    if not getattr(settings, "enable_pii_redaction", False):
        return ""

    try:
        from services.privacy.redaction_log import redaction_log
        summary = redaction_log.summarize(
            collection_id=collection_id,
            since=since,
        )
    except Exception as e:
        logger.warning(f"redaction footer lookup failed for {collection_id}: {e}")
        return ""

    total = int(summary.get("total_redactions") or 0)
    if total == 0:
        return ""

    by_type = summary.get("by_entity_type") or {}
    rows = sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)

    def _humanize(name: str) -> str:
        return " ".join(part.capitalize() for part in name.split("_"))

    lines = [
        "",
        "---",
        "",
        "**PII redaction summary**",
        "",
        f"- Total entities redacted before AI processing: **{total}**",
    ]
    for entity, count in rows:
        lines.append(f"- {_humanize(entity)}: {count}")
    lines.append("")
    lines.append(
        "_Counts only — original text never left this device. "
        "Full audit log retained locally._"
    )
    lines.append("")
    return "\n".join(lines)


# ── Note of Record save (R7 — Advisor Desktop UX) ────────────────────────────

@app.post(
    "/api/collections/{collection_id}/notes/save",
    response_model=SaveNoteResponse,
    tags=["chat"],
    summary="Persist an advisor-edited Note of Record into the collection",
)
async def save_note_of_record(
    collection_id: str,
    body: SaveNoteRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Write the edited Note of Record as a Markdown doc and index it.

    The advisor edits the streamed draft locally, then this endpoint commits
    the final text to the collection — same pattern as the Whisper transcript
    flow, so the saved note appears alongside other documents and is
    immediately searchable / chat-addressable.
    """
    require_collection_access(collection_id, user_id, required="readwrite")
    text = (body.content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="content is empty")

    indexer = get_indexer(collection_id)
    document_dir = indexer_manager.get_documents_path(collection_id)
    document_dir.mkdir(parents=True, exist_ok=True)

    from datetime import datetime as _dt
    now = _dt.now()
    stamp = now.strftime("%Y-%m-%d %H-%M")
    title_clean = (body.title or "").strip()
    if title_clean:
        safe = "".join(c if c.isalnum() or c in (" ", "-", "_") else "-" for c in title_clean).strip()
        filename = f"Note of Record - {stamp} - {safe}.md"[:160]
    else:
        filename = f"Note of Record - {stamp}.md"

    # Avoid clobbering an existing file (advisor saved twice in one minute)
    md_path = document_dir / filename
    counter = 2
    while md_path.exists():
        stem = md_path.stem
        md_path = document_dir / f"{stem} ({counter}).md"
        counter += 1

    header = (
        f"# Note of Record\n\n"
        f"**Date:** {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"**Collection:** {collection_id}\n\n"
        f"---\n\n"
    )

    # Stamp a real redaction-summary footer (R9.4 / R9.6) so the saved file
    # has a defensible compliance trail — counts pulled from the audit log
    # for the collection within the drafting session window. We do this here
    # rather than in the prompt so the affirmation is grounded in actual
    # redaction events, not a model-asserted "PII was reviewed" line.
    footer = _build_redaction_footer(
        collection_id=collection_id,
        since=body.redaction_since,
    )
    md_path.write_text(header + text + "\n" + footer, encoding="utf-8")

    try:
        doc_metadata = indexer.index_document(md_path, md_path.name)
    except Exception as e:
        # Clean up the file if indexing fails so the collection doesn't
        # accumulate orphan markdown when something goes wrong downstream.
        try:
            md_path.unlink(missing_ok=True)
        except Exception:
            pass
        logger.exception(f"Failed to index saved Note of Record for {collection_id}")
        raise HTTPException(status_code=500, detail=f"Failed to save note: {e}")

    collection_service.add_document(collection_id, doc_metadata.document_id)
    indexer.save_index()

    return SaveNoteResponse(
        document_id=doc_metadata.document_id,
        filename=md_path.name,
    )


# ── PII redaction surfacing (R9.1 — Advisor Desktop UX) ──────────────────────

@app.get(
    "/api/redactions/summary",
    response_model=RedactionSummaryResponse,
    tags=["privacy"],
    summary="PII redaction counts grouped by entity type",
)
async def get_redactions_summary(
    collection_id: str | None = None,
    since: str | None = None,
    session_id: str | None = None,
):
    """Count redactions for a collection / session / time-window.

    Powers the redaction pill in the chat input bar and the redaction
    summary footer on Notes of Record and Meeting Briefs. Only counts —
    never original PII text — leave the box.
    """
    from services.privacy.redaction_log import redaction_log
    summary = redaction_log.summarize(
        collection_id=collection_id,
        session_id=session_id,
        since=since,
    )
    return RedactionSummaryResponse(
        collection_id=summary.get("collection_id"),
        since=summary.get("since"),
        total_redactions=summary.get("total_redactions", 0),
        by_entity_type=summary.get("by_entity_type", {}) or {},
        by_tool=summary.get("by_tool", {}) or {},
    )


@app.get(
    "/api/redactions/log",
    response_model=RedactionLogResponse,
    tags=["privacy"],
    summary="Recent PII redaction events (no original text)",
)
async def get_redactions_log(
    collection_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
):
    """Return the most recent redaction events for the requested filter.

    Used by the in-chat "PII redaction active" viewer. Original PII text is
    stripped from the response — it stays in the local audit log only.
    """
    from services.privacy.redaction_log import redaction_log

    capped = max(1, min(int(limit), 500))
    events = redaction_log.get_recent(
        session_id=session_id,
        collection_id=collection_id,
        limit=capped,
    )

    safe_events = [
        RedactionLogEvent(
            id=e.get("id", 0),
            timestamp=e.get("timestamp") or "",
            session_id=e.get("session_id") or "",
            collection_id=e.get("collection_id"),
            tool_name=e.get("tool_name"),
            document_id=e.get("document_id"),
            entity_type=e.get("entity_type") or "",
            replacement=e.get("replacement") or "",
            score=float(e.get("score") or 0.0),
        )
        for e in events
    ]
    return RedactionLogResponse(total_returned=len(safe_events), events=safe_events)


@app.post(
    "/api/redactions/dry-run",
    response_model=RedactionDryRunResponse,
    tags=["privacy"],
    summary="Preview what PII would be redacted without writing to the audit log",
)
async def post_redactions_dry_run(body: RedactionDryRunRequest):
    """Run the redaction engine on a string without persisting anything.

    Used by the Note of Record drafting view to show the advisor exactly
    what the AI provider will see before the AI call is made.
    """
    if not getattr(settings, "enable_pii_redaction", False):
        # Mirror the runtime behavior: if redaction is disabled, dry-run
        # returns the input unchanged with no entities.
        return RedactionDryRunResponse(
            original=body.text,
            redacted=body.text,
            had_pii=False,
            entity_count=0,
            entities=[],
        )

    from services.privacy.redaction_engine import redaction_engine

    if not redaction_engine.available:
        return RedactionDryRunResponse(
            original=body.text,
            redacted=body.text,
            had_pii=False,
            entity_count=0,
            entities=[],
        )

    result = redaction_engine.redact_text(body.text, collection_id=body.collection_id)
    entities = [
        RedactionEntity(
            entity_type=d.entity_type,
            start=d.start,
            end=d.end,
            score=d.score,
            replacement=d.replacement,
        )
        for d in result.details
    ]
    return RedactionDryRunResponse(
        original=body.text,
        redacted=result.redacted_text,
        had_pii=result.had_pii,
        entity_count=len(entities),
        entities=entities,
    )


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
async def get_collection_expertise(
    collection_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """List all expertise packs attached to a collection."""
    require_collection_access(collection_id, user_id, required="read")
    packs = expertise_store.get_packs_for_collection(collection_id)
    return CollectionExpertiseResponse(collection_id=collection_id, packs=packs)


@app.put(
    "/api/collections/{collection_id}/expertise",
    response_model=CollectionExpertiseResponse,
    tags=["expertise"],
)
async def set_collection_expertise(
    collection_id: str,
    data: SetCollectionExpertiseRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Replace the full set of expertise packs attached to a collection."""
    require_collection_access(collection_id, user_id, required="readwrite")
    expertise_store.set_packs_for_collection(collection_id, data.pack_ids)
    packs = expertise_store.get_packs_for_collection(collection_id)
    return CollectionExpertiseResponse(collection_id=collection_id, packs=packs)


def _build_expertise_pack_prompt(topic: str, context: str | None, audience: str | None) -> str:
    """Meta-prompt for generating expertise pack bodies.

    The body becomes part of the chat system prompt downstream, so the model is
    told its audience is another LLM and to use prompt-engineering patterns
    (imperative directives, concrete thresholds, labeled sections, advisor
    framing) rather than essay-style prose.
    """
    audience_clean = (audience or "financial advisors analyzing a client portfolio").strip()
    extra = f"\n\nADDITIONAL CONTEXT FROM THE AUTHOR:\n{context.strip()}" if context and context.strip() else ""
    return f"""You are drafting an "expertise pack" — a block of advisor guidance that will \
be injected verbatim into the system prompt of a downstream AI assistant. The downstream \
AI uses this guidance to analyze a client's portfolio and answer the advisor's questions.

Your real audience is another LLM, not a human reader. Optimize the markdown you produce \
for prompt-effectiveness, not prose elegance.

TOPIC: {topic.strip()}
PACK AUDIENCE: {audience_clean}{extra}

WRITE THE PACK USING THESE PROMPT-ENGINEERING PATTERNS:

1. Frame the user as the advisor analyzing a client. Never write "your portfolio" or \
address the client directly — the advisor is the one reading the AI's output. Use \
phrasing like "When the advisor's client holds X, surface Y."

2. Use imperative directives, not descriptions. Write "Flag any single position over 5% \
of household NAV" not "Concentration risk is something to consider." Directives steer \
behavior; descriptions don't.

3. Prefer concrete thresholds over vague language. "Duration > 7 years", "yield spread \
> 200 bps to comparable Treasury", "expense ratio > 0.50%" beat "elevated", "long", or \
"high." Name conventional numbers when they exist.

4. Structure with labeled markdown H2 sections the LLM can latch onto. Use this \
skeleton — omit any section that doesn't apply, add others where they help:

   ## Scope
   One short paragraph: what this pack covers and what it explicitly does NOT cover.

   ## Frameworks the advisor uses
   Named analytical lenses (e.g. "4% rule", "bucket strategy", "tax-loss harvesting \
   eligibility window"), one to two lines each.

   ## Heuristics & directives
   Imperative bullets the AI must apply when the topic comes up.

   ## Red flags — surface these proactively
   Specific patterns the AI should call out without being asked.

   ## Out of scope / do not advise on
   Hard limits — things to refuse, defer, or escalate to the human advisor.

5. Include do/don't pairs where a common LLM failure mode exists. Example: "DO cite the \
specific line item from the holdings table when flagging concentration. DON'T speculate \
about the client's tax bracket — ask the advisor."

6. Be specific to the topic. Generic platitudes ("diversification matters") add no \
steering value. If you can't say something specific, leave it out.

7. No preamble, no closing summary, no meta-commentary. Output the markdown body ONLY \
— no "Here's your pack:" intro, no "Let me know if you'd like changes" outro. Begin \
with a single one-sentence summary line (no markdown heading), a blank line, then the \
H2 sections.

8. Length: 400–900 words. Tight and dense steers better than long and vague.

Begin the pack now."""


@app.post(
    "/api/expertise/packs/generate/stream",
    tags=["expertise"],
    summary="Stream a generated expertise pack body from a topic (SSE)",
)
async def stream_generate_expertise_pack(
    body: ExpertisePackGenerateRequest,
    x_ai_key: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ollama_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
    user_id: str = Depends(get_current_user_id),
):
    """Stream an AI-drafted expertise pack body using prompt-engineering patterns.

    The user provides a topic (and optional context); the downstream LLM produces
    a markdown body shaped to steer another LLM well — imperative directives,
    concrete thresholds, labeled sections, advisor framing. Returns the same SSE
    event vocabulary as /notes/stream: `text_delta`, `done` (with `content`),
    `error`. The caller saves the result through the existing pack CRUD endpoints.
    """
    import json as _json
    from services.chat.engine import ChatTurn, run_one_shot

    async def generate():
        try:
            provider = _build_ai_provider_from_headers(
                body.provider, x_ai_key,
                x_ai_model or x_anthropic_model or x_openai_model or x_ollama_model,
                x_ai_base_url,
                user_id=user_id,
            )
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'message': f'AI provider error: {e}'})}\n\n"
            return

        prompt = _build_expertise_pack_prompt(body.topic, body.context, body.audience)
        turn = ChatTurn(
            provider=provider,
            system_text="",
            messages=[{"role": "user", "content": prompt}],
            model=provider.QUALITY_MODEL,
            max_tokens=2000,
        )

        text_parts: list[str] = []
        try:
            async for ev in run_one_shot(turn):
                t = ev.get("type")
                if t == "text_delta":
                    delta = ev.get("delta", "")
                    text_parts.append(delta)
                    yield f"data: {_json.dumps({'type': 'text_delta', 'delta': delta})}\n\n"
                elif t == "error":
                    yield f"data: {_json.dumps({'type': 'error', 'message': ev.get('message', 'Unknown error')})}\n\n"
                    return
                elif t == "done":
                    raw_text = (ev.get("response_text") or "".join(text_parts)).strip()
                    yield f"data: {_json.dumps({'type': 'done', 'content': raw_text, 'usage': ev.get('usage', {})})}\n\n"
        except Exception as e:
            logger.exception("Streaming expertise pack generation failed")
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


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
    if not file_path or not file_path.strip():
        raise HTTPException(status_code=400, detail="file_path is required.")
    path = Path(file_path)
    if not path.exists() or not path.is_file():
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
async def get_pii_blacklist(
    collection_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    require_collection_access(collection_id, user_id, required="read")
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
    user_id: str = Depends(get_current_user_id),
) -> dict:
    require_collection_access(collection_id, user_id, required="readwrite")
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
    user_id: str = Depends(get_current_user_id),
) -> dict:
    require_collection_access(collection_id, user_id, required="readwrite")
    from services.privacy.collection_blacklist import remove_terms
    updated = remove_terms(collection_id, body.terms)
    return {"collection_id": collection_id, "terms": updated}


# ── Diagnostics endpoints ────────────────────────────────────────────────────
# In-memory log + chat-event ring buffers. See services.diagnostics for the
# capture mechanism. Used by the Expert-mode Diagnostics tab in the frontend
# to view recent activity and copy structured output for sharing with AI
# agents during development.

@app.get(
    "/api/diagnostics/logs",
    summary="Recent in-memory log records",
    tags=["diagnostics"],
)
async def get_diagnostics_logs(limit: int = 200, level: str = "INFO") -> dict:
    from services.diagnostics import get_buffer
    buf = get_buffer()
    return {"logs": buf.recent_logs(limit=limit, min_level=level)}


@app.get(
    "/api/diagnostics/chat-events",
    summary="Recent chat-stream events captured server-side",
    tags=["diagnostics"],
)
async def get_diagnostics_chat_events(limit: int = 100) -> dict:
    from services.diagnostics import get_buffer
    buf = get_buffer()
    return {"events": buf.recent_events(limit=limit)}


@app.get(
    "/api/diagnostics/cache",
    summary="Cumulative Anthropic prompt-cache token totals + hit rate",
    tags=["diagnostics"],
)
async def get_diagnostics_cache() -> dict:
    from services.diagnostics import get_buffer
    return get_buffer().cache_stats()


@app.post(
    "/api/diagnostics/clear",
    summary="Clear the in-memory diagnostics buffers",
    tags=["diagnostics"],
)
async def clear_diagnostics() -> dict:
    from services.diagnostics import get_buffer
    get_buffer().clear()
    return {"cleared": True}


@app.get(
    "/api/feedback/config",
    summary="Whether the in-app feedback channel is configured",
    tags=["diagnostics"],
)
async def get_feedback_config() -> dict:
    """Lets the frontend show a hint when the maintainer hasn't wired Resend yet."""
    return {
        "enabled": bool(settings.resend_api_key and settings.feedback_email_to),
        "recipient": settings.feedback_email_to if settings.feedback_email_to else None,
    }


@app.post(
    "/api/feedback",
    response_model=FeedbackResponse,
    summary="Submit a user feedback / issue report",
    tags=["diagnostics"],
)
async def submit_feedback(req: FeedbackRequest, request: Request) -> FeedbackResponse:
    from services.feedback import FeedbackContext, send_feedback
    ctx = FeedbackContext(
        description=req.description,
        include_diagnostics=req.include_diagnostics,
        app_route=req.app_route,
        collection_id=req.collection_id,
        user_agent=request.headers.get("user-agent"),
    )
    result = send_feedback(ctx)
    return FeedbackResponse(
        ok=bool(result.get("ok")),
        id=result.get("id"),
        error=result.get("error"),
    )


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

