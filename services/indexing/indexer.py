"""Document indexing orchestration service."""

from pathlib import Path
from typing import List, Optional, Callable
import hashlib
import logging
from datetime import datetime

from config import settings
from models.schemas import (
    DocumentMetadata,
    AIOptions,
    AIUsage,
    AIUsageDetail,
    SearchMode,
)
from services.document_extractor import DocumentExtractor, ExtractionResult
from services.chunker import TextChunker
from services.embedder import EmbeddingService
from services.vector_store import VectorStore

logger = logging.getLogger(__name__)

# Type alias for progress callback
# Signature: (phase: str, progress: int, detail: str, chunks_done: int, chunks_total: int)
ProgressCallback = Callable[[str, int, Optional[str], int, int], None]


class DocumentIndexer:
    """Orchestrates the document indexing pipeline."""

    def __init__(
        self,
        vector_store: VectorStore,
        embedding_service: EmbeddingService,
        document_extractor: DocumentExtractor,
        text_chunker: TextChunker,
    ):
        """
        Initialize the document indexer.

        Args:
            vector_store: Vector store instance
            embedding_service: Embedding service instance
            document_extractor: Document extractor instance (supports PDF, TXT, DOCX, CSV)
            text_chunker: Text chunker instance
        """
        self.vector_store = vector_store
        self.embedding_service = embedding_service
        self.document_extractor = document_extractor
        self.text_chunker = text_chunker

    def index_document(self, document_path: Path, filename: str) -> DocumentMetadata:
        """
        Index a single document (PDF, TXT, DOCX, CSV, MD, or JSON).

        Args:
            document_path: Path to the document file
            filename: Original filename

        Returns:
            DocumentMetadata object
        """
        # Delegate to progress-aware version with no-op callback
        return self.index_document_with_progress(document_path, filename, progress_callback=None)

    def index_document_with_progress(
        self,
        document_path: Path,
        filename: str,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> DocumentMetadata:
        """
        Index a single document with granular progress reporting (v4.0).

        Args:
            document_path: Path to the document file
            filename: Original filename
            progress_callback: Optional callback for progress updates
                Signature: (phase, progress, detail, chunks_done, chunks_total)

        Returns:
            DocumentMetadata object
        """
        logger.info(f"Indexing document: {filename}")

        def report(phase: str, progress: int, detail: str = None,
                   chunks_done: int = 0, chunks_total: int = 0):
            """Helper to safely call progress callback."""
            if progress_callback:
                try:
                    progress_callback(phase, progress, detail, chunks_done, chunks_total)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

        # Generate document ID from file content hash
        document_id = self._generate_document_id(document_path)

        # Determine source format from file extension
        source_format = document_path.suffix.lower().lstrip(".")

        # Handle CSV with row-level indexing (v3.0 feature)
        if source_format == "csv" and settings.csv_row_level_indexing:
            return self._index_csv_rows(document_path, filename, document_id)

        # Handle code files with symbol-aware chunking
        if self.document_extractor.is_code_file(document_path):
            return self._index_code_file(
                document_path, filename, document_id, source_format,
                progress_callback=progress_callback,
            )

        # Phase 1: Extract text from document
        report("extracting", 0, f"Extracting text from {filename}")
        logger.debug(f"Extracting text from {filename}")
        extraction_result = self.document_extractor.extract_text(document_path)

        # Handle ExtractionResult object
        if isinstance(extraction_result, ExtractionResult):
            page_texts = extraction_result.page_texts
            extraction_method = extraction_result.method
            injection_warnings = {
                page: scan.to_dict()
                for page, scan in extraction_result.injection_warnings.items()
                if scan.is_flagged
            } or None
        else:
            # Backward compatibility: plain dict
            page_texts = extraction_result
            extraction_method = "text"
            injection_warnings = None

        num_pages = len(page_texts)
        report("extracting", 100, f"Extracted {num_pages} pages")

        if num_pages == 0:
            logger.warning(f"No pages extracted from {filename}")
            raise ValueError(f"Could not extract any pages from {filename}")

        # Phase 2: Chunk the text with format metadata
        report("chunking", 0, f"Chunking {num_pages} pages")
        logger.debug(f"Chunking text from {filename}")
        chunks = self.text_chunker.chunk_document(
            page_texts=page_texts,
            document_id=document_id,
            filename=filename,
            source_format=source_format,
            extraction_method=extraction_method,
        )

        num_chunks = len(chunks)
        report("chunking", 100, f"Created {num_chunks} chunks", 0, num_chunks)

        if num_chunks == 0:
            logger.warning(f"No chunks created from {filename}")
            raise ValueError(f"Could not create any chunks from {filename}")

        # Phase 3: Generate embeddings (with progress for large files)
        report("embedding", 0, f"Generating embeddings for {num_chunks} chunks", 0, num_chunks)
        logger.debug(f"Generating embeddings for {num_chunks} chunks")

        chunk_texts = [chunk.text for chunk in chunks]

        # For large files, embed in batches with progress updates
        batch_size = 32  # Match embedding service batch size
        if num_chunks > batch_size and progress_callback:
            embeddings = []
            for i in range(0, num_chunks, batch_size):
                batch = chunk_texts[i:i + batch_size]
                batch_embeddings = self.embedding_service.embed_texts(batch)
                embeddings.extend(batch_embeddings)

                progress = min(100, int((i + len(batch)) / num_chunks * 100))
                report("embedding", progress,
                       f"Embedded {min(i + len(batch), num_chunks)}/{num_chunks} chunks",
                       min(i + len(batch), num_chunks), num_chunks)
        else:
            embeddings = self.embedding_service.embed_texts(chunk_texts)
            report("embedding", 100, f"Embedded {num_chunks} chunks", num_chunks, num_chunks)

        # Phase 4: Add to vector store
        report("saving", 0, f"Saving {num_chunks} chunks to index")
        logger.debug(f"Adding {num_chunks} chunks to vector store")
        self.vector_store.add_chunks(chunks, embeddings)

        # Add document record to metadata store
        indexed_at = datetime.utcnow().isoformat()
        self.vector_store.metadata_store.add_document(
            document_id=document_id,
            filename=filename,
            num_pages=num_pages,
            num_chunks=num_chunks,
            upload_timestamp=indexed_at,
            source_format=source_format,
            extraction_method=extraction_method,
            embedding_model=settings.embedding_model,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            injection_warnings=injection_warnings,
        )
        report("saving", 100, f"Saved {num_chunks} chunks")

        # Create metadata with v3.0 fields
        metadata = DocumentMetadata(
            document_id=document_id,
            filename=filename,
            total_pages=num_pages,
            total_chunks=num_chunks,
            indexed_at=indexed_at,
            source_format=source_format,
            extraction_method=extraction_method,
            embedding_model=settings.embedding_model,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )

        logger.info(
            f"Successfully indexed {filename}: {num_pages} pages, {num_chunks} chunks "
            f"(format={source_format}, method={extraction_method})"
        )
        return metadata

    def _index_csv_rows(self, document_path: Path, filename: str,
                        document_id: str) -> DocumentMetadata:
        """
        Index a CSV file with row-level chunking (v3.0 feature).

        Args:
            document_path: Path to CSV file
            filename: Original filename
            document_id: Generated document ID

        Returns:
            DocumentMetadata object
        """
        logger.info(f"Indexing CSV with row-level chunking: {filename}")

        # Extract CSV rows with column metadata
        csv_rows = self.document_extractor.extract_csv_rows(document_path)

        if not csv_rows:
            logger.warning(f"No rows extracted from {filename}")
            raise ValueError(f"Could not extract any rows from {filename}")

        num_rows = len(csv_rows)

        # Create chunks from CSV rows
        chunks = self.text_chunker.chunk_csv_rows(
            csv_rows=csv_rows,
            document_id=document_id,
            filename=filename,
            rows_per_chunk=settings.csv_rows_per_chunk if not settings.csv_row_level_indexing else 1,
        )

        num_chunks = len(chunks)

        # Generate embeddings
        logger.debug(f"Generating embeddings for {num_chunks} CSV row chunks")
        chunk_texts = [chunk.text for chunk in chunks]
        embeddings = self.embedding_service.embed_texts(chunk_texts)

        # Add to vector store
        logger.debug(f"Adding {num_chunks} CSV chunks to vector store")
        self.vector_store.add_chunks(chunks, embeddings)

        # Add document record to metadata store
        indexed_at = datetime.utcnow().isoformat()
        self.vector_store.metadata_store.add_document(
            document_id=document_id,
            filename=filename,
            num_pages=num_rows,  # For CSV, "pages" = rows
            num_chunks=num_chunks,
            upload_timestamp=indexed_at,
            source_format="csv",
            extraction_method="text",
            embedding_model=settings.embedding_model,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )

        # Create metadata
        metadata = DocumentMetadata(
            document_id=document_id,
            filename=filename,
            total_pages=num_rows,  # For CSV, "pages" = rows
            total_chunks=num_chunks,
            indexed_at=indexed_at,
            source_format="csv",
            extraction_method="text",
            embedding_model=settings.embedding_model,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )

        logger.info(f"Successfully indexed CSV {filename}: {num_rows} rows, {num_chunks} chunks")
        return metadata

    def _index_code_file(
        self,
        document_path: Path,
        filename: str,
        document_id: str,
        source_format: str,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> DocumentMetadata:
        """
        Index a source code file using symbol-aware chunking.

        Extracts functions, classes, procedures etc. as individual chunks
        so semantic search preserves symbol boundaries.
        """
        from models.schemas import ChunkMetadata

        def report(phase: str, progress: int, detail: str = None,
                   chunks_done: int = 0, chunks_total: int = 0):
            if progress_callback:
                try:
                    progress_callback(phase, progress, detail, chunks_done, chunks_total)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

        logger.info(f"Indexing code file with symbol-aware chunking: {filename}")

        report("extracting", 0, f"Extracting symbols from {filename}")
        code_chunks = self.document_extractor.extract_code_chunks(document_path, document_id)

        if not code_chunks:
            logger.warning(f"No chunks extracted from code file {filename}")
            raise ValueError(f"Could not extract any chunks from {filename}")

        # Convert CodeChunk → ChunkMetadata
        chunks = []
        for cc in code_chunks:
            chunks.append(ChunkMetadata(
                chunk_id=cc.chunk_id,
                document_id=cc.document_id,
                filename=cc.filename,
                page_number=cc.line_start,   # line number as logical "page"
                chunk_index=cc.chunk_index,
                text=cc.text,
                source_format=source_format,
                extraction_method="text",
                language=cc.language,
                unit_name=cc.unit_name,
                symbol_name=cc.symbol_name,
                symbol_type=cc.symbol_type,
                line_start=cc.line_start,
                line_end=cc.line_end,
                parent_symbol=cc.parent_symbol,
            ))

        num_chunks = len(chunks)
        report("extracting", 100, f"Extracted {num_chunks} symbol chunks")

        report("embedding", 0, f"Generating embeddings for {num_chunks} chunks", 0, num_chunks)
        chunk_texts = [c.text for c in chunks]
        batch_size = 32
        if num_chunks > batch_size and progress_callback:
            embeddings = []
            for i in range(0, num_chunks, batch_size):
                batch = chunk_texts[i:i + batch_size]
                batch_embeddings = self.embedding_service.embed_texts(batch)
                embeddings.extend(batch_embeddings)
                progress = min(100, int((i + len(batch)) / num_chunks * 100))
                report("embedding", progress,
                       f"Embedded {min(i + len(batch), num_chunks)}/{num_chunks} chunks",
                       min(i + len(batch), num_chunks), num_chunks)
        else:
            embeddings = self.embedding_service.embed_texts(chunk_texts)
            report("embedding", 100, f"Embedded {num_chunks} chunks", num_chunks, num_chunks)

        report("saving", 0, f"Saving {num_chunks} chunks to index")
        self.vector_store.add_chunks(chunks, embeddings)

        indexed_at = datetime.utcnow().isoformat()
        self.vector_store.metadata_store.add_document(
            document_id=document_id,
            filename=filename,
            num_pages=num_chunks,   # for code, "pages" = symbol chunks
            num_chunks=num_chunks,
            upload_timestamp=indexed_at,
            source_format=source_format,
            extraction_method="text",
            embedding_model=settings.embedding_model,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
        report("saving", 100, f"Saved {num_chunks} chunks")

        metadata = DocumentMetadata(
            document_id=document_id,
            filename=filename,
            total_pages=num_chunks,
            total_chunks=num_chunks,
            indexed_at=indexed_at,
            source_format=source_format,
            extraction_method="text",
            embedding_model=settings.embedding_model,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )

        logger.info(f"Successfully indexed code file {filename}: {num_chunks} symbol chunks")
        return metadata

    def search(
        self,
        query: str,
        top_k: int = 10,
        ai_service=None,
        ai_options: Optional[AIOptions] = None,
        mode: SearchMode = SearchMode.SEMANTIC,
        semantic_weight: float = 0.7,
    ) -> dict:
        """
        Search for documents matching the query, with optional AI enhancements.

        Args:
            query: Search query text
            top_k: Number of results to return
            ai_service: Optional AIService instance (created from user's API key)
            ai_options: Optional AI feature flags
            mode: Search mode (semantic, keyword, or hybrid)
            semantic_weight: Weight for semantic search in hybrid mode (0-1)

        Returns:
            Dict with 'results', and optionally 'enhanced_query', 'synthesis', 'ai_usage'
        """
        logger.info(f"Searching for: {query[:100]} (mode={mode.value})")

        ai_active = ai_service and ai_options
        ai_usage = AIUsage() if ai_active else None
        synthesis = None

        # Fetch extra results if reranking (so the LLM has a bigger pool)
        fetch_k = min(top_k * 5, 50) if (ai_active and ai_options.rerank) else top_k

        # Perform search based on mode
        if mode == SearchMode.KEYWORD:
            # Pure BM25 keyword search
            bm25_results = self.vector_store.bm25_index.search(query, fetch_k)
            results = self._bm25_to_search_results(bm25_results)
        elif mode == SearchMode.HYBRID:
            # Combined semantic + keyword search
            query_embedding = self.embedding_service.embed_query(query)
            results = self.vector_store.search_hybrid(
                query=query,
                query_embedding=query_embedding,
                top_k=fetch_k,
                semantic_weight=semantic_weight,
            )
        else:
            # Default: pure semantic search
            query_embedding = self.embedding_service.embed_query(query)
            results = self.vector_store.search(query_embedding, top_k=fetch_k)

        # Step 3: Optionally rerank results
        if ai_active and ai_options.rerank and len(results) > 0:
            try:
                rerank_input = [
                    {
                        "index": i,
                        "filename": r.filename,
                        "text_snippet": r.text_snippet,
                        "similarity_score": r.similarity_score,
                    }
                    for i, r in enumerate(results)
                ]
                rerank_result = ai_service.rerank_results(query, rerank_input, top_k)
                usage = rerank_result["usage"]

                if usage:
                    ai_usage.features_used.append("reranking")
                    ai_usage.reranking = AIUsageDetail(**usage)
                    ai_usage.total_input_tokens += usage["input_tokens"]
                    ai_usage.total_output_tokens += usage["output_tokens"]

                # Reorder results based on AI ranking
                valid_indices = [
                    i for i in rerank_result["reranked_indices"]
                    if 0 <= i < len(results)
                ]
                results = [results[i] for i in valid_indices] if valid_indices else results[:top_k]
            except Exception as e:
                logger.warning(f"Reranking failed, using original order: {e}")
                results = results[:top_k]
        else:
            results = results[:top_k]

        # Step 4: Optionally synthesize an answer from results
        if ai_active and ai_options.synthesize and len(results) > 0:
            try:
                synth_input = [
                    {
                        "filename": r.filename,
                        "page_number": r.page_number,
                        "text_snippet": r.text_snippet,
                    }
                    for r in results
                ]
                synth_result = ai_service.synthesize_results(query, synth_input)
                synthesis = synth_result["synthesis"]
                usage = synth_result["usage"]

                if usage:
                    ai_usage.features_used.append("synthesis")
                    ai_usage.synthesis = AIUsageDetail(**usage)
                    ai_usage.total_input_tokens += usage["input_tokens"]
                    ai_usage.total_output_tokens += usage["output_tokens"]
            except Exception as e:
                logger.warning(f"Synthesis failed: {e}")

        logger.info(f"Found {len(results)} results")

        return {
            "results": results,
            "synthesis": synthesis,
            "ai_usage": ai_usage,
        }

    def list_documents(self) -> List[dict]:
        """
        List all indexed documents.

        Returns:
            List of document metadata dictionaries
        """
        return self.vector_store.list_documents()

    def delete_document(self, document_id: str) -> int:
        """
        Delete a document from the index.

        Args:
            document_id: Document ID to delete

        Returns:
            Number of chunks deleted
        """
        logger.info(f"Deleting document: {document_id}")
        num_deleted = self.vector_store.delete_document(document_id)
        logger.info(f"Deleted {num_deleted} chunks")
        return num_deleted

    def save_index(self):
        """Persist the vector store to disk."""
        self.vector_store.save()

    def _generate_document_id(self, document_path: Path) -> str:
        """
        Generate a unique document ID based on file content.

        Args:
            document_path: Path to the document file

        Returns:
            Document ID (SHA256 hash)
        """
        hasher = hashlib.sha256()
        with open(document_path, "rb") as f:
            # Read file in chunks to handle large files
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)

        return hasher.hexdigest()[:16]  # Use first 16 characters

    def _bm25_to_search_results(self, bm25_results: list) -> list:
        """
        Convert BM25 results (chunk_id, score) to SearchResult objects.

        Args:
            bm25_results: List of (chunk_id, score) tuples from BM25 search

        Returns:
            List of SearchResult objects
        """
        from models.schemas import SearchResult

        if not bm25_results:
            return []

        # Normalize scores to 0-1 range
        max_score = max(score for _, score in bm25_results) if bm25_results else 1
        if max_score == 0:
            max_score = 1

        results = []
        # Get all chunks to find by chunk_id
        all_chunks = self.vector_store.metadata_store.get_all_chunks_ordered()
        chunk_lookup = {chunk["chunk_id"]: chunk for chunk in all_chunks}

        for chunk_id, score in bm25_results:
            chunk = chunk_lookup.get(chunk_id)
            if not chunk:
                continue

            # Get document info for source_type
            doc_info = self.vector_store.metadata_store.get_document_info(chunk["document_id"])
            source_type = doc_info.get("source_type") if doc_info else None
            source_path = doc_info.get("source_path") if doc_info else None

            result = SearchResult(
                filename=chunk["filename"],
                page_number=chunk["page_number"],
                text_snippet=chunk["text"],
                similarity_score=score / max_score,  # Normalize to 0-1
                document_id=chunk["document_id"],
                chunk_id=chunk["chunk_id"],
                pdf_url="",
                page_url="",
                source_format=chunk.get("source_format"),
                extraction_method=chunk.get("extraction_method"),
                csv_row_number=chunk.get("csv_row_number"),
                csv_columns=chunk.get("csv_columns"),
                csv_values=chunk.get("csv_values"),
                # v3.1: Local file reference support
                source_type=source_type,
                source_path=source_path,
            )
            results.append(result)

        return results
