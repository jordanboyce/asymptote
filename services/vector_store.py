"""FAISS-based vector store with SQLite metadata for scalability."""

from pathlib import Path
from typing import List, Optional, Dict
import logging
import numpy as np
import faiss

from models.schemas import ChunkMetadata, SearchResult
from services.metadata_store import MetadataStore
from services.bm25_service import BM25Index

logger = logging.getLogger(__name__)


class VectorStore:
    """
    FAISS-based vector store with SQLite metadata storage.

    Uses SQLite for metadata, providing:
    - Constant memory usage regardless of document count
    - Faster lookups with indexed queries
    - Better scalability for large collections
    """

    def __init__(self, index_dir: Path, embedding_dim: int = 384):
        """
        Initialize the vector store.

        Args:
            index_dir: Directory to store FAISS index and metadata
            embedding_dim: Dimension of embedding vectors
        """
        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_dim = embedding_dim

        self.index_path = self.index_dir / "faiss.index"
        self.metadata_db_path = self.index_dir / "metadata.db"
        self.embeddings_path = self.index_dir / "embeddings.npy"
        self.bm25_db_path = self.index_dir / "bm25.db"

        # FAISS index
        self.index: Optional[faiss.Index] = None

        # Embeddings storage: NumPy array of embeddings
        self.embeddings: Optional[np.ndarray] = None

        # SQLite metadata store
        self.metadata_store = MetadataStore(self.metadata_db_path)

        # BM25 keyword search index
        self.bm25_index = BM25Index(self.bm25_db_path)

        self._load_or_create_index()

    def _load_or_create_index(self):
        """Load existing index from disk or create a new one."""
        if self.index_path.exists():
            logger.info("Loading existing FAISS index")
            self.index = faiss.read_index(str(self.index_path))

            # Load embeddings if they exist
            if self.embeddings_path.exists():
                self.embeddings = np.load(str(self.embeddings_path))
                logger.info(f"Loaded embeddings array with shape {self.embeddings.shape}")
            else:
                logger.warning("Embeddings file not found - deletion will not work properly")
                self.embeddings = None

            total_chunks = self.metadata_store.get_total_chunks()
            logger.info(f"Loaded index with {total_chunks} chunks")
        else:
            logger.info("Creating new FAISS index")
            # Use IndexFlatIP for cosine similarity (inner product)
            # Vectors must be L2 normalized before adding
            self.index = faiss.IndexFlatIP(self.embedding_dim)
            self.embeddings = None
            logger.info("Created new index")

    def load(self):
        """Reload the index from disk (public method for external reload)."""
        logger.info("Reloading index from disk...")
        self._load_or_create_index()

    def close(self):
        """Close the vector store and release resources."""
        logger.info("Closing vector store...")
        # Clear references to allow garbage collection
        self.index = None
        self.embeddings = None
        self.metadata_store = None

    def add_chunks(self, chunks: List[ChunkMetadata], embeddings: np.ndarray):
        """
        Add chunks and their embeddings to the index.

        Args:
            chunks: List of ChunkMetadata objects
            embeddings: NumPy array of shape (len(chunks), embedding_dim)
        """
        if len(chunks) != len(embeddings):
            raise ValueError("Number of chunks must match number of embeddings")

        if len(chunks) == 0:
            return

        # Normalize embeddings for cosine similarity
        embeddings_normalized = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

        # Add to FAISS index
        self.index.add(embeddings_normalized.astype(np.float32))

        # Store embeddings
        if self.embeddings is None:
            self.embeddings = embeddings_normalized.astype(np.float32)
        else:
            self.embeddings = np.vstack([self.embeddings, embeddings_normalized.astype(np.float32)])

        # Add metadata to SQLite
        chunk_dicts = [chunk.model_dump() for chunk in chunks]
        self.metadata_store.add_chunks(chunk_dicts)

        # Add to BM25 index for keyword search
        bm25_docs = [(chunk.chunk_id, chunk.text) for chunk in chunks]
        self.bm25_index.add_documents_batch(bm25_docs)

        logger.info(f"Added {len(chunks)} chunks to index")

    def search(self, query_embedding: np.ndarray, top_k: int = 10) -> List[SearchResult]:
        """
        Search for similar chunks.

        Args:
            query_embedding: Query embedding vector
            top_k: Number of results to return

        Returns:
            List of SearchResult objects, sorted by similarity (highest first)
        """
        if self.index.ntotal == 0:
            logger.warning("Index is empty, returning no results")
            return []

        # Normalize query for cosine similarity
        query_normalized = query_embedding / np.linalg.norm(query_embedding)
        query_normalized = query_normalized.reshape(1, -1).astype(np.float32)

        # Search
        k = min(top_k, self.index.ntotal)
        similarities, indices = self.index.search(query_normalized, k)

        # Convert to SearchResult objects
        results = []
        for similarity, idx in zip(similarities[0], indices[0]):
            if idx == -1:  # FAISS returns -1 for empty results
                continue

            # Fetch metadata from SQLite by index
            chunk = self.metadata_store.get_chunk_by_index(int(idx))
            if not chunk:
                logger.warning(f"No metadata found for index {idx}")
                continue

            # Get document info for source_type
            doc_info = self.metadata_store.get_document_info(chunk["document_id"])
            source_type = doc_info.get("source_type") if doc_info else None
            source_path = doc_info.get("source_path") if doc_info else None

            result = SearchResult(
                filename=chunk["filename"],
                page_number=chunk["page_number"],
                text_snippet=chunk["text"],
                similarity_score=float(similarity),
                document_id=chunk["document_id"],
                chunk_id=chunk["chunk_id"],
                pdf_url="",  # Will be populated by the API endpoint
                page_url="",  # Will be populated by the API endpoint
                # v3.0: Format-aware metadata
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

    def search_hybrid(
        self,
        query: str,
        query_embedding: np.ndarray,
        top_k: int = 10,
        semantic_weight: float = 0.7
    ) -> List[SearchResult]:
        """
        Perform hybrid search combining semantic similarity and BM25 keyword matching.

        The final score is: semantic_weight * semantic_score + (1 - semantic_weight) * bm25_score

        Args:
            query: Original query text for BM25
            query_embedding: Query embedding vector for semantic search
            top_k: Number of results to return
            semantic_weight: Weight for semantic scores (0-1), default 0.7
                            Higher = more semantic, Lower = more keyword-focused

        Returns:
            List of SearchResult objects, sorted by combined score (highest first)
        """
        if self.index.ntotal == 0:
            logger.warning("Index is empty, returning no results")
            return []

        # Get more candidates than top_k to allow for re-ranking
        candidate_k = min(top_k * 3, self.index.ntotal)

        # 1. Semantic search
        query_normalized = query_embedding / np.linalg.norm(query_embedding)
        query_normalized = query_normalized.reshape(1, -1).astype(np.float32)
        semantic_sims, semantic_indices = self.index.search(query_normalized, candidate_k)

        # Build semantic scores dict (chunk_id -> score)
        semantic_scores: Dict[str, float] = {}
        chunk_data: Dict[str, dict] = {}

        for similarity, idx in zip(semantic_sims[0], semantic_indices[0]):
            if idx == -1:
                continue
            chunk = self.metadata_store.get_chunk_by_index(int(idx))
            if chunk:
                chunk_id = chunk["chunk_id"]
                semantic_scores[chunk_id] = float(similarity)
                chunk_data[chunk_id] = chunk

        # 2. BM25 keyword search
        bm25_results = self.bm25_index.search(query, candidate_k)

        # Normalize BM25 scores to 0-1 range
        bm25_scores: Dict[str, float] = {}
        if bm25_results:
            max_bm25 = max(score for _, score in bm25_results)
            if max_bm25 > 0:
                for chunk_id, score in bm25_results:
                    bm25_scores[chunk_id] = score / max_bm25
                    # Fetch chunk data if not already in semantic results
                    if chunk_id not in chunk_data:
                        chunk = self._get_chunk_by_id(chunk_id)
                        if chunk:
                            chunk_data[chunk_id] = chunk

        # 3. Combine scores
        all_chunk_ids = set(semantic_scores.keys()) | set(bm25_scores.keys())
        combined_scores: Dict[str, float] = {}

        for chunk_id in all_chunk_ids:
            sem_score = semantic_scores.get(chunk_id, 0)
            bm25_score = bm25_scores.get(chunk_id, 0)
            combined_scores[chunk_id] = (
                semantic_weight * sem_score +
                (1 - semantic_weight) * bm25_score
            )

        # 4. Sort by combined score and build results
        sorted_chunks = sorted(combined_scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for chunk_id, combined_score in sorted_chunks[:top_k]:
            chunk = chunk_data.get(chunk_id)
            if not chunk:
                continue

            # Get document info for source_type
            doc_info = self.metadata_store.get_document_info(chunk["document_id"])
            source_type = doc_info.get("source_type") if doc_info else None
            source_path = doc_info.get("source_path") if doc_info else None

            result = SearchResult(
                filename=chunk["filename"],
                page_number=chunk["page_number"],
                text_snippet=chunk["text"],
                similarity_score=combined_score,
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

        logger.info(f"Hybrid search returned {len(results)} results "
                   f"(semantic_weight={semantic_weight})")
        return results

    def _get_chunk_by_id(self, chunk_id: str) -> Optional[dict]:
        """Get chunk metadata by chunk_id."""
        # This is a slower lookup but needed for BM25-only matches
        chunks = self.metadata_store.get_all_chunks_ordered()
        for chunk in chunks:
            if chunk["chunk_id"] == chunk_id:
                return chunk
        return None

    def delete_document(self, document_id: str) -> int:
        """
        Delete all chunks belonging to a document.

        Note: FAISS doesn't support efficient deletion, so we rebuild the index.

        Args:
            document_id: Document ID to delete

        Returns:
            Number of chunks deleted
        """
        # Get indices to delete from metadata store
        indices_to_delete = set(self.metadata_store.get_document_chunk_indices(document_id))

        if len(indices_to_delete) == 0:
            logger.warning(f"Document {document_id} not found in index")
            return 0

        num_deleted = len(indices_to_delete)
        logger.info(f"Deleting {num_deleted} chunks for document {document_id}")

        # Delete from BM25 index
        self.bm25_index.remove_documents_by_document_id(document_id, self.metadata_db_path)

        # Delete from metadata (SQLite)
        self.metadata_store.delete_document(document_id)

        # Rebuild FAISS index with remaining embeddings
        if self.embeddings is not None:
            new_embeddings = []
            for idx in range(len(self.embeddings)):
                if idx not in indices_to_delete:
                    new_embeddings.append(self.embeddings[idx])

            # Rebuild FAISS index
            logger.info(f"Rebuilding index with {len(new_embeddings)} remaining chunks")
            self.index = faiss.IndexFlatIP(self.embedding_dim)

            if len(new_embeddings) > 0:
                self.embeddings = np.array(new_embeddings, dtype=np.float32)
                self.index.add(self.embeddings)
                logger.info(f"Re-added {len(new_embeddings)} vectors to index")
            else:
                self.embeddings = None
                logger.info("Index is now empty")
        else:
            logger.warning("No embeddings stored - cannot rebuild index properly")

        logger.info(f"Successfully deleted document {document_id}")

        return num_deleted

    def list_documents(self) -> List[dict]:
        """
        List all indexed documents with their metadata.

        Returns:
            List of document metadata dictionaries
        """
        return self.metadata_store.list_documents()

    def save(self):
        """Persist the FAISS index, embeddings, and metadata to disk."""
        logger.info(f"Saving FAISS index with {self.index.ntotal} vectors")
        faiss.write_index(self.index, str(self.index_path))

        # Save embeddings
        if self.embeddings is not None:
            np.save(str(self.embeddings_path), self.embeddings)
            logger.info(f"Saved embeddings array with shape {self.embeddings.shape}")

        # Metadata is already persisted in SQLite
        logger.info("Index saved successfully")

    def get_total_chunks(self) -> int:
        """Get the total number of chunks in the index."""
        return self.metadata_store.get_total_chunks()

    def clear_index(self):
        """Clear all data from the index and metadata store."""
        logger.info("Clearing vector store index and metadata")

        # Create new empty FAISS index
        self.index = faiss.IndexFlatIP(self.embedding_dim)
        self.embeddings = None

        # Clear all metadata from database
        self.metadata_store.clear_all()

        # Clear BM25 index
        self.bm25_index.clear()

        # Save the empty index
        self.save()

        logger.info("Vector store cleared successfully")

    def get_bm25_stats(self) -> Dict[str, any]:
        """Get BM25 index statistics."""
        return self.bm25_index.get_stats()

