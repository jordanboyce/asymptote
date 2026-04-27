"""Manages DocumentIndexer instances for each collection.

Each collection has its own:
- Vector store (FAISS index + metadata)
- Document directory
- Settings (chunk size, overlap, embedding model)
"""

import logging
from pathlib import Path
from typing import Dict, Optional

from services.collection_service import collection_service
from services.document_extractor import DocumentExtractor
from services.chunker import TextChunker
from services.embedder import EmbeddingService
from services.vector_store import VectorStore
from services.indexing import DocumentIndexer
from config import settings

logger = logging.getLogger(__name__)


class IndexerManager:
    """Manages DocumentIndexer instances for multiple collections."""

    def __init__(self):
        """Initialize the indexer manager."""
        self._indexers: Dict[str, DocumentIndexer] = {}
        self._embedding_services: Dict[str, EmbeddingService] = {}

        # Apply any DB config overrides to settings before creating the extractor
        self._apply_db_config()

        # v3.0: Initialize document extractor with OCR settings
        self._document_extractor = self._create_document_extractor()

        if settings.enable_ocr:
            ocr_available = self._document_extractor.is_ocr_available()
            engine_name = self._document_extractor.get_ocr_engine_name()
            logger.info(f"OCR enabled: {ocr_available} (engine: {engine_name or 'none'})")

    def _apply_db_config(self):
        """Apply DB config overrides to in-memory settings on startup."""
        try:
            from services.app_database import app_db
            db_config = app_db.get_all_config()
            ocr_fields = {
                "enable_ocr", "ocr_max_pages", "ocr_max_file_mb",
                "vision_ocr_provider", "vision_ocr_model", "vision_ocr_api_key",
                "vision_ocr_dpi", "vision_ocr_enhance_image",
                "vision_ocr_cleanup_pass", "vision_ocr_cleanup_model",
                "vision_ocr_ollama_url",
            }
            mcp_fields = {
                "enable_mcp", "mcp_server_id", "mcp_default_collection",
                "mcp_top_k", "mcp_mode", "mcp_semantic_weight",
                "mcp_include_sources", "mcp_max_source_length",
                "mcp_ai_provider", "mcp_ollama_model",
            }
            for key in ocr_fields | mcp_fields:
                if key in db_config:
                    try:
                        setattr(settings, key, db_config[key])
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Could not apply DB config on startup: {e}")

    def _create_document_extractor(self) -> DocumentExtractor:
        """Create a DocumentExtractor with current settings."""
        return DocumentExtractor(
            enable_ocr=settings.enable_ocr,
            ocr_max_pages=settings.ocr_max_pages,
            ocr_max_file_mb=settings.ocr_max_file_mb,
            vision_ocr_provider=settings.vision_ocr_provider,
            vision_ocr_model=settings.vision_ocr_model,
            vision_ocr_api_key=settings.vision_ocr_api_key,
            vision_ocr_dpi=settings.vision_ocr_dpi,
            vision_ocr_enhance_image=settings.vision_ocr_enhance_image,
            vision_ocr_cleanup_pass=settings.vision_ocr_cleanup_pass,
            vision_ocr_cleanup_model=settings.vision_ocr_cleanup_model,
            vision_ocr_ollama_url=settings.vision_ocr_ollama_url,
            vision_ocr_form_mode=settings.vision_ocr_form_mode,
        )

    def reload_document_extractor(self):
        """Recreate the document extractor with current settings and update all cached indexers."""
        self._document_extractor = self._create_document_extractor()
        for indexer in self._indexers.values():
            indexer.document_extractor = self._document_extractor
        if settings.enable_ocr:
            ocr_available = self._document_extractor.is_ocr_available()
            engine_name = self._document_extractor.get_ocr_engine_name()
            logger.info(f"OCR settings reloaded: available={ocr_available}, engine={engine_name or 'none'}")
        else:
            logger.info("OCR settings reloaded: OCR disabled")

    def get_indexer(self, collection_id: str = "default") -> DocumentIndexer:
        """Get or create an indexer for a collection.

        Args:
            collection_id: Collection ID (default: "default")

        Returns:
            DocumentIndexer instance for the collection
        """
        if collection_id in self._indexers:
            return self._indexers[collection_id]

        # Get collection settings
        collection = collection_service.get_collection(collection_id)
        if not collection:
            raise ValueError(f"Collection '{collection_id}' not found")

        # Create indexer for this collection
        indexer = self._create_indexer(collection)
        self._indexers[collection_id] = indexer

        logger.info(f"Created indexer for collection '{collection_id}'")
        return indexer

    def _create_indexer(self, collection: dict) -> DocumentIndexer:
        """Create a DocumentIndexer for a collection.

        Args:
            collection: Collection settings dict

        Returns:
            DocumentIndexer instance
        """
        collection_id = collection["id"]
        embedding_model = collection.get("embedding_model", settings.embedding_model)
        chunk_size = collection.get("chunk_size", settings.chunk_size)
        chunk_overlap = collection.get("chunk_overlap", settings.chunk_overlap)

        # Get or create embedding service for this model
        if embedding_model not in self._embedding_services:
            logger.info(f"Loading embedding model: {embedding_model}")
            self._embedding_services[embedding_model] = EmbeddingService(
                model_name=embedding_model
            )
        embedding_service = self._embedding_services[embedding_model]

        # Get paths for this collection
        indexes_dir = collection_service.get_indexes_path(collection_id)

        # Create vector store
        vector_store = VectorStore(
            index_dir=indexes_dir,
            embedding_dim=embedding_service.embedding_dim,
        )

        # Create text chunker with collection settings
        text_chunker = TextChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        # Create indexer
        return DocumentIndexer(
            vector_store=vector_store,
            embedding_service=embedding_service,
            document_extractor=self._document_extractor,
            text_chunker=text_chunker,
        )

    def reload_indexer(self, collection_id: str = "default"):
        """Reload an indexer's vector store from disk.

        Args:
            collection_id: Collection ID
        """
        if collection_id in self._indexers:
            self._indexers[collection_id].vector_store.load()
            logger.info(f"Reloaded indexer for collection '{collection_id}'")

    def invalidate_indexer(self, collection_id: str):
        """Remove a cached indexer (e.g., after settings change).

        Args:
            collection_id: Collection ID
        """
        if collection_id in self._indexers:
            # Save before removing
            self._indexers[collection_id].save_index()
            del self._indexers[collection_id]
            logger.info(f"Invalidated indexer for collection '{collection_id}'")

    def remove_indexer(self, collection_id: str):
        """Remove a cached indexer without saving (e.g., after collection deletion).

        Args:
            collection_id: Collection ID
        """
        if collection_id in self._indexers:
            del self._indexers[collection_id]
            logger.info(f"Removed indexer for deleted collection '{collection_id}'")

    def close_indexer(self, collection_id: str):
        """Close an indexer and release all resources (e.g., before restore).

        This properly closes database connections and clears references
        to allow file deletion on Windows.

        Args:
            collection_id: Collection ID
        """
        if collection_id in self._indexers:
            indexer = self._indexers[collection_id]
            # Close the vector store to release file handles
            if hasattr(indexer.vector_store, 'close'):
                indexer.vector_store.close()
            del self._indexers[collection_id]
            logger.info(f"Closed indexer for collection '{collection_id}'")

    def save_all(self):
        """Save all indexers to disk."""
        for collection_id, indexer in self._indexers.items():
            try:
                indexer.save_index()
                logger.info(f"Saved index for collection '{collection_id}'")
            except Exception as e:
                logger.error(f"Failed to save index for '{collection_id}': {e}")

    def get_documents_path(self, collection_id: str = "default") -> Path:
        """Get the documents directory for a collection.

        Args:
            collection_id: Collection ID

        Returns:
            Path to documents directory
        """
        return collection_service.get_documents_path(collection_id)

    def get_indexes_path(self, collection_id: str = "default") -> Path:
        """Get the indexes directory for a collection.

        Args:
            collection_id: Collection ID

        Returns:
            Path to indexes directory
        """
        return collection_service.get_indexes_path(collection_id)

    def get_collection_stats(self, collection_id: str = "default") -> dict:
        """Get statistics for a collection.

        Args:
            collection_id: Collection ID

        Returns:
            Dict with document count, chunk count, etc.
        """
        try:
            indexer = self.get_indexer(collection_id)
            total_chunks = indexer.vector_store.get_total_chunks()
            documents = indexer.list_documents()

            return {
                "collection_id": collection_id,
                "total_documents": len(documents),
                "total_chunks": total_chunks,
                "total_pages": sum(d.get("total_pages", 0) or d.get("num_pages", 0) for d in documents),
            }
        except Exception as e:
            logger.error(f"Failed to get stats for '{collection_id}': {e}")
            return {
                "collection_id": collection_id,
                "total_documents": 0,
                "total_chunks": 0,
                "total_pages": 0,
            }


# Global instance
indexer_manager = IndexerManager()

