"""Index repair utility for fixing FAISS-metadata synchronization issues."""

import logging
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
import numpy as np
import faiss

from services.metadata_store import MetadataStore
from services.embedder import EmbeddingService

logger = logging.getLogger(__name__)


class IndexRepairService:
    """Service for diagnosing and repairing FAISS-metadata sync issues."""

    def __init__(self, index_dir: Path, embedding_service: EmbeddingService = None):
        """
        Initialize the repair service.

        Args:
            index_dir: Directory containing the index files
            embedding_service: Optional embedding service for rebuilding
        """
        self.index_dir = index_dir / "sqlite"
        self.index_path = self.index_dir / "faiss.index"
        self.embeddings_path = self.index_dir / "embeddings.npy"
        self.metadata_db_path = self.index_dir / "metadata.db"
        self.embedding_service = embedding_service

    def diagnose(self) -> Dict[str, Any]:
        """
        Diagnose the sync status between FAISS index and metadata.

        Returns:
            Dictionary with diagnostic information
        """
        result = {
            "status": "unknown",
            "faiss_count": 0,
            "metadata_count": 0,
            "embeddings_count": 0,
            "documents_count": 0,
            "orphaned_chunks_count": 0,
            "issues": [],
            "recommendations": []
        }

        # Check if files exist
        if not self.index_path.exists():
            result["issues"].append("FAISS index file not found")
            result["status"] = "missing_index"
            result["recommendations"].append("Re-index all documents")
            return result

        if not self.metadata_db_path.exists():
            result["issues"].append("Metadata database not found")
            result["status"] = "missing_metadata"
            result["recommendations"].append("Re-index all documents")
            return result

        # Load FAISS index
        try:
            index = faiss.read_index(str(self.index_path))
            result["faiss_count"] = index.ntotal
        except Exception as e:
            result["issues"].append(f"Failed to load FAISS index: {e}")
            result["status"] = "corrupted_index"
            result["recommendations"].append("Rebuild FAISS index from metadata")
            return result

        # Load embeddings if they exist
        if self.embeddings_path.exists():
            try:
                embeddings = np.load(str(self.embeddings_path))
                result["embeddings_count"] = len(embeddings)
            except Exception as e:
                result["issues"].append(f"Failed to load embeddings file: {e}")
                result["embeddings_count"] = 0

        # Load metadata
        try:
            metadata_store = MetadataStore(self.metadata_db_path)
            result["metadata_count"] = metadata_store.get_total_chunks()

            # Check documents table integrity
            docs_info = self._check_documents_table()
            result["documents_count"] = docs_info["documents_count"]
            result["orphaned_chunks_count"] = docs_info["orphaned_chunks_count"]
            result["unique_doc_ids_in_chunks"] = docs_info["unique_doc_ids"]

        except Exception as e:
            result["issues"].append(f"Failed to load metadata: {e}")
            result["status"] = "corrupted_metadata"
            result["recommendations"].append("Re-index all documents")
            return result

        # Compare counts
        faiss_count = result["faiss_count"]
        metadata_count = result["metadata_count"]
        embeddings_count = result["embeddings_count"]

        if faiss_count == metadata_count == embeddings_count:
            result["status"] = "synced"
            return result

        # Identify specific issues
        if faiss_count > metadata_count:
            orphaned = faiss_count - metadata_count
            result["issues"].append(
                f"FAISS has {orphaned} orphaned entries (no metadata)"
            )
            result["recommendations"].append(
                "Run 'rebuild_from_metadata' to rebuild FAISS index"
            )

        if metadata_count > faiss_count:
            missing = metadata_count - faiss_count
            result["issues"].append(
                f"Metadata has {missing} entries not in FAISS index"
            )
            result["recommendations"].append(
                "Run 'rebuild_from_metadata' to re-embed missing chunks"
            )

        if embeddings_count != faiss_count:
            result["issues"].append(
                f"Embeddings file count ({embeddings_count}) doesn't match FAISS ({faiss_count})"
            )

        # Check for documents table issues
        if result["orphaned_chunks_count"] > 0:
            result["issues"].append(
                f"Found {result['orphaned_chunks_count']} chunks with no corresponding document record"
            )
            result["recommendations"].append(
                "Run 'repair_documents_table' to reconstruct missing document records"
            )

        if result["documents_count"] == 0 and result["metadata_count"] > 0:
            result["issues"].append(
                "Documents table is empty but chunks exist - document records were not saved"
            )
            result["recommendations"].append(
                "Run 'repair_documents_table' to reconstruct document records from chunks"
            )

        result["status"] = "out_of_sync"
        return result

    def rebuild_from_metadata(self, embedding_dim: int = 384) -> Dict[str, Any]:
        """
        Rebuild the FAISS index from metadata by re-embedding all chunks.

        This is the safest repair option as it uses the authoritative
        metadata store to regenerate all embeddings.

        Args:
            embedding_dim: Dimension of embedding vectors

        Returns:
            Dictionary with rebuild results
        """
        if self.embedding_service is None:
            return {
                "success": False,
                "error": "Embedding service not provided - cannot rebuild"
            }

        result = {
            "success": False,
            "chunks_processed": 0,
            "errors": []
        }

        try:
            # Load metadata
            metadata_store = MetadataStore(self.metadata_db_path)
            all_chunks = metadata_store.get_all_chunks_ordered()

            if not all_chunks:
                result["success"] = True
                result["message"] = "No chunks in metadata - nothing to rebuild"
                return result

            logger.info(f"Rebuilding index from {len(all_chunks)} chunks")

            # Create new FAISS index
            new_index = faiss.IndexFlatIP(embedding_dim)
            new_embeddings = []

            # Process in batches for efficiency
            batch_size = 100
            for i in range(0, len(all_chunks), batch_size):
                batch = all_chunks[i:i + batch_size]
                texts = [chunk["text"] for chunk in batch]

                try:
                    # Generate embeddings
                    embeddings = self.embedding_service.embed_texts(texts)

                    # Normalize for cosine similarity
                    embeddings_normalized = embeddings / np.linalg.norm(
                        embeddings, axis=1, keepdims=True
                    )

                    # Add to index
                    new_index.add(embeddings_normalized.astype(np.float32))
                    new_embeddings.extend(embeddings_normalized)

                    result["chunks_processed"] += len(batch)
                    logger.info(
                        f"Processed {result['chunks_processed']}/{len(all_chunks)} chunks"
                    )

                except Exception as e:
                    error_msg = f"Failed to embed batch starting at {i}: {e}"
                    logger.error(error_msg)
                    result["errors"].append(error_msg)

            # Save the rebuilt index
            faiss.write_index(new_index, str(self.index_path))
            logger.info(f"Saved rebuilt FAISS index with {new_index.ntotal} vectors")

            # Save embeddings
            embeddings_array = np.array(new_embeddings, dtype=np.float32)
            np.save(str(self.embeddings_path), embeddings_array)
            logger.info(f"Saved embeddings array with shape {embeddings_array.shape}")

            result["success"] = True
            result["message"] = (
                f"Successfully rebuilt index with {result['chunks_processed']} chunks"
            )

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Index rebuild failed: {e}")

        return result

    def truncate_faiss_to_metadata(self, embedding_dim: int = 384) -> Dict[str, Any]:
        """
        Truncate FAISS index to match metadata count.

        This is a faster but less safe option - it simply removes
        excess entries from FAISS without re-embedding.

        Args:
            embedding_dim: Dimension of embedding vectors

        Returns:
            Dictionary with truncation results
        """
        result = {
            "success": False,
            "faiss_before": 0,
            "faiss_after": 0,
            "removed": 0
        }

        try:
            # Load current state
            index = faiss.read_index(str(self.index_path))
            result["faiss_before"] = index.ntotal

            metadata_store = MetadataStore(self.metadata_db_path)
            metadata_count = metadata_store.get_total_chunks()

            if index.ntotal <= metadata_count:
                result["success"] = True
                result["faiss_after"] = index.ntotal
                result["message"] = "FAISS index is not larger than metadata - no truncation needed"
                return result

            # Load embeddings
            if not self.embeddings_path.exists():
                result["error"] = "Embeddings file not found - cannot truncate safely"
                return result

            embeddings = np.load(str(self.embeddings_path))

            if len(embeddings) < metadata_count:
                result["error"] = (
                    f"Embeddings file has fewer entries ({len(embeddings)}) "
                    f"than metadata ({metadata_count}) - cannot truncate safely"
                )
                return result

            # Truncate embeddings to match metadata
            truncated_embeddings = embeddings[:metadata_count]

            # Rebuild FAISS index with truncated embeddings
            new_index = faiss.IndexFlatIP(embedding_dim)
            new_index.add(truncated_embeddings.astype(np.float32))

            # Save
            faiss.write_index(new_index, str(self.index_path))
            np.save(str(self.embeddings_path), truncated_embeddings)

            result["success"] = True
            result["faiss_after"] = new_index.ntotal
            result["removed"] = result["faiss_before"] - result["faiss_after"]
            result["message"] = f"Truncated FAISS index from {result['faiss_before']} to {result['faiss_after']} entries"

            logger.info(result["message"])

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Truncation failed: {e}")

        return result

    def get_orphaned_indices(self) -> List[int]:
        """
        Get list of FAISS indices that have no corresponding metadata.

        Returns:
            List of orphaned index positions
        """
        orphaned = []

        try:
            index = faiss.read_index(str(self.index_path))
            metadata_store = MetadataStore(self.metadata_db_path)
            metadata_count = metadata_store.get_total_chunks()

            # Any index >= metadata_count is orphaned
            for i in range(metadata_count, index.ntotal):
                orphaned.append(i)

        except Exception as e:
            logger.error(f"Failed to get orphaned indices: {e}")

        return orphaned

    def _check_documents_table(self) -> Dict[str, Any]:
        """
        Check the integrity of the documents table.

        Returns:
            Dictionary with documents table status
        """
        result = {
            "documents_count": 0,
            "unique_doc_ids": 0,
            "orphaned_chunks_count": 0
        }

        try:
            with sqlite3.connect(self.metadata_db_path) as conn:
                # Count documents in documents table
                cursor = conn.execute("SELECT COUNT(*) FROM documents")
                result["documents_count"] = cursor.fetchone()[0]

                # Count unique document_ids in chunks table
                cursor = conn.execute("SELECT COUNT(DISTINCT document_id) FROM chunks")
                result["unique_doc_ids"] = cursor.fetchone()[0]

                # Find chunks with no corresponding document
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM chunks c
                    WHERE NOT EXISTS (
                        SELECT 1 FROM documents d
                        WHERE d.document_id = c.document_id
                    )
                """)
                result["orphaned_chunks_count"] = cursor.fetchone()[0]

        except Exception as e:
            logger.error(f"Failed to check documents table: {e}")

        return result

    def repair_documents_table(self) -> Dict[str, Any]:
        """
        Repair the documents table by reconstructing records from chunks data.

        This fixes the issue where chunks exist but document records were never
        created (e.g., due to a bug in the indexing process or corrupted backup).

        Returns:
            Dictionary with repair results
        """
        result = {
            "success": False,
            "documents_repaired": 0,
            "chunks_processed": 0,
            "errors": []
        }

        try:
            with sqlite3.connect(self.metadata_db_path) as conn:
                # Find all unique documents from chunks that don't have a documents record
                cursor = conn.execute("""
                    SELECT
                        c.document_id,
                        c.filename,
                        MAX(c.page_number) as max_page,
                        COUNT(*) as num_chunks,
                        MAX(c.source_format) as source_format,
                        MAX(c.extraction_method) as extraction_method,
                        MIN(c.created_at) as first_created
                    FROM chunks c
                    WHERE NOT EXISTS (
                        SELECT 1 FROM documents d
                        WHERE d.document_id = c.document_id
                    )
                    GROUP BY c.document_id, c.filename
                """)

                missing_docs = cursor.fetchall()

                if not missing_docs:
                    result["success"] = True
                    result["message"] = "No missing document records found"
                    return result

                logger.info(f"Found {len(missing_docs)} documents to repair")

                for doc in missing_docs:
                    document_id = doc[0]
                    filename = doc[1]
                    num_pages = doc[2] if doc[2] else 1
                    num_chunks = doc[3]
                    source_format = doc[4]
                    extraction_method = doc[5] or "text"
                    first_created = doc[6]

                    # Use the chunk creation time or current time as upload timestamp
                    upload_timestamp = first_created or datetime.utcnow().isoformat()

                    try:
                        conn.execute("""
                            INSERT INTO documents
                            (document_id, filename, num_pages, num_chunks, upload_timestamp,
                             source_format, extraction_method, schema_version)
                            VALUES (?, ?, ?, ?, ?, ?, ?, '3.0')
                        """, (document_id, filename, num_pages, num_chunks,
                              upload_timestamp, source_format, extraction_method))

                        result["documents_repaired"] += 1
                        result["chunks_processed"] += num_chunks
                        logger.info(f"Repaired document: {filename} ({document_id})")

                    except Exception as e:
                        error_msg = f"Failed to repair document {document_id}: {e}"
                        logger.error(error_msg)
                        result["errors"].append(error_msg)

                conn.commit()

            result["success"] = True
            result["message"] = (
                f"Repaired {result['documents_repaired']} document records "
                f"covering {result['chunks_processed']} chunks"
            )
            logger.info(result["message"])

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Documents table repair failed: {e}")

        return result
