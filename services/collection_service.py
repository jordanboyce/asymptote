"""Collection service for managing document collections.

Each collection has:
- Its own directory for documents
- Its own FAISS index
- Its own settings (chunk size, overlap, etc.)
"""

import logging
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List

from services.app_database import app_db
from config import settings

logger = logging.getLogger(__name__)


class CollectionService:
    """Manages document collections and their associated data."""

    def __init__(self, base_dir: Path = None):
        """Initialize collection service.

        Args:
            base_dir: Base directory for all collections
        """
        self.base_dir = base_dir or Path(settings.data_dir) / "collections"
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # Ensure default collection directory exists
        self._ensure_collection_dirs("default")

    def _get_collection_dir(self, collection_id: str) -> Path:
        """Get the directory path for a collection."""
        return self.base_dir / collection_id

    def _get_documents_dir(self, collection_id: str) -> Path:
        """Get the documents directory for a collection."""
        return self._get_collection_dir(collection_id) / "documents"

    def _get_indexes_dir(self, collection_id: str) -> Path:
        """Get the indexes directory for a collection."""
        return self._get_collection_dir(collection_id) / "indexes"

    def _ensure_collection_dirs(self, collection_id: str):
        """Ensure collection directories exist."""
        self._get_documents_dir(collection_id).mkdir(parents=True, exist_ok=True)
        self._get_indexes_dir(collection_id).mkdir(parents=True, exist_ok=True)

    def create_collection(
        self,
        name: str,
        description: str = "",
        color: str = "#3b82f6",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        embedding_model: str = None
    ) -> Dict[str, Any]:
        """Create a new collection.

        Args:
            name: Collection name
            description: Collection description
            color: Hex color for UI
            chunk_size: Chunk size for text splitting
            chunk_overlap: Overlap between chunks
            embedding_model: Embedding model to use

        Returns:
            Collection details
        """
        # Create in database
        collection_id = app_db.create_collection(
            name=name,
            description=description,
            color=color,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_model=embedding_model
        )

        # Create directories
        self._ensure_collection_dirs(collection_id)

        logger.info(f"Created collection '{name}' with ID {collection_id}")

        return app_db.get_collection(collection_id)

    def get_collection(self, collection_id: str) -> Optional[Dict[str, Any]]:
        """Get collection details.

        Args:
            collection_id: Collection ID

        Returns:
            Collection details or None
        """
        return app_db.get_collection(collection_id)

    def get_all_collections(self) -> List[Dict[str, Any]]:
        """Get all collections.

        Returns:
            List of collection details
        """
        return app_db.get_all_collections()

    def update_collection(
        self,
        collection_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        embedding_model: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Update collection settings.

        Args:
            collection_id: Collection ID
            name: New name
            description: New description
            color: New color
            chunk_size: New chunk size
            chunk_overlap: New chunk overlap
            embedding_model: New embedding model

        Returns:
            Updated collection details or None
        """
        app_db.update_collection(
            collection_id=collection_id,
            name=name,
            description=description,
            color=color,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_model=embedding_model
        )

        return app_db.get_collection(collection_id)

    def delete_collection(self, collection_id: str) -> bool:
        """Delete a collection and all its data.

        Args:
            collection_id: Collection ID

        Returns:
            True if deleted, False if cannot delete
        """
        if collection_id == "default":
            logger.warning("Cannot delete default collection")
            return False

        # Delete from database
        if not app_db.delete_collection(collection_id):
            return False

        # Delete directory and all contents
        collection_dir = self._get_collection_dir(collection_id)
        if collection_dir.exists():
            try:
                shutil.rmtree(collection_dir)
                logger.info(f"Deleted collection directory: {collection_dir}")
            except PermissionError as e:
                # On Windows, files may be locked by other processes
                logger.warning(f"Could not delete collection directory (files may be locked): {e}")
                # Still return True since the database entry was deleted
                # The directory can be cleaned up manually or on restart
            except Exception as e:
                logger.error(f"Error deleting collection directory: {e}")
                # Still return True since the database entry was deleted

        return True

    def get_documents_path(self, collection_id: str) -> Path:
        """Get the documents directory path for a collection.

        Args:
            collection_id: Collection ID

        Returns:
            Path to documents directory
        """
        self._ensure_collection_dirs(collection_id)
        return self._get_documents_dir(collection_id)

    def get_indexes_path(self, collection_id: str) -> Path:
        """Get the indexes directory path for a collection.

        Args:
            collection_id: Collection ID

        Returns:
            Path to indexes directory
        """
        self._ensure_collection_dirs(collection_id)
        return self._get_indexes_dir(collection_id)

    def add_document(self, collection_id: str, document_id: str):
        """Register a document as belonging to a collection.

        Args:
            collection_id: Collection ID
            document_id: Document ID
        """
        app_db.add_document_to_collection(collection_id, document_id)

    def remove_document(self, collection_id: str, document_id: str):
        """Remove a document from a collection.

        Args:
            collection_id: Collection ID
            document_id: Document ID
        """
        app_db.remove_document_from_collection(collection_id, document_id)

    def get_collection_document_ids(self, collection_id: str) -> List[str]:
        """Get all document IDs in a collection.

        Args:
            collection_id: Collection ID

        Returns:
            List of document IDs
        """
        return app_db.get_collection_documents(collection_id)

    def clear_collection_index(self, collection_id: str):
        """Clear the index for a collection (but keep documents).

        Args:
            collection_id: Collection ID
        """
        indexes_dir = self._get_indexes_dir(collection_id)
        if indexes_dir.exists():
            shutil.rmtree(indexes_dir)
            indexes_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Cleared index for collection: {collection_id}")


# Global instance
collection_service = CollectionService()
