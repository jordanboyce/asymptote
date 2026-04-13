"""Collection service for managing document collections.

Each collection has:
- Its own directory for documents
- Its own FAISS index
- Its own settings (chunk size, overlap, etc.)
- An owner (user_id) for multi-user isolation
"""

import logging
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List

from services.app_database import app_db
from config import settings

logger = logging.getLogger(__name__)


def _normalize_for_pii_check(text: str) -> str:
    """Normalize underscores/hyphens to spaces so Presidio can detect
    person names embedded in identifiers like 'margarett_sullivan'."""
    import re
    return re.sub(r"[_\-]+", " ", text)


def _generate_mcp_aliases(
    name: str,
    description: str,
) -> tuple[str | None, str | None]:
    """Generate PII-safe display names for MCP output.

    Runs the collection name and description through the redaction engine.
    If PII is detected, returns a sanitized alias; otherwise returns None
    (meaning the real name is safe to use in MCP responses).

    Also normalizes underscores/hyphens to spaces before checking, since
    Presidio can't detect person names in 'margarett_sullivan' form.
    """
    if not getattr(settings, "enable_pii_redaction", False):
        return None, None

    try:
        from services.privacy.redaction_engine import redaction_engine
        if not redaction_engine.available:
            return None, None

        display_name = None
        # Check both the raw name and a normalized form
        result = redaction_engine.redact_text(name)
        if not result.had_pii:
            result = redaction_engine.redact_text(_normalize_for_pii_check(name))
        if result.had_pii:
            display_name = result.redacted_text

        display_desc = None
        if description:
            result = redaction_engine.redact_text(description)
            if not result.had_pii:
                result = redaction_engine.redact_text(_normalize_for_pii_check(description))
            if result.had_pii:
                display_desc = result.redacted_text

        return display_name, display_desc
    except Exception:
        logger.debug("PII check skipped for collection name", exc_info=True)
        return None, None


class CollectionService:
    """Manages document collections and their associated data."""

    def __init__(self, base_dir: Path = None):
        self.base_dir = base_dir or Path(settings.data_dir) / "collections"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_collection_dirs("default")

    def _get_collection_dir(self, collection_id: str) -> Path:
        return self.base_dir / collection_id

    def _get_documents_dir(self, collection_id: str) -> Path:
        return self._get_collection_dir(collection_id) / "documents"

    def _get_indexes_dir(self, collection_id: str) -> Path:
        return self._get_collection_dir(collection_id) / "indexes"

    def _ensure_collection_dirs(self, collection_id: str):
        self._get_documents_dir(collection_id).mkdir(parents=True, exist_ok=True)
        self._get_indexes_dir(collection_id).mkdir(parents=True, exist_ok=True)

    def create_collection(
        self,
        name: str,
        description: str = "",
        color: str = "#3b82f6",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        embedding_model: str = None,
        owner_id: str = "default",
    ) -> Dict[str, Any]:
        """Create a new collection.

        Args:
            name: Collection name
            description: Collection description
            color: Hex color for UI
            chunk_size: Chunk size for text splitting
            chunk_overlap: Overlap between chunks
            embedding_model: Embedding model to use
            owner_id: User who owns this collection

        Returns:
            Collection details
        """
        mcp_display_name, mcp_display_description = _generate_mcp_aliases(
            name, description,
        )
        collection_id = app_db.create_collection(
            name=name,
            description=description,
            color=color,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_model=embedding_model,
            owner_id=owner_id,
            mcp_display_name=mcp_display_name,
            mcp_display_description=mcp_display_description,
        )
        self._ensure_collection_dirs(collection_id)
        logger.info(f"Created collection '{name}' with ID {collection_id} owner={owner_id}")
        return app_db.get_collection(collection_id)

    def get_collection(self, collection_id: str) -> Optional[Dict[str, Any]]:
        return app_db.get_collection(collection_id)

    def get_all_collections(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all collections visible to a user.

        In single-user mode (user_id=None or multi_user disabled): returns all.
        In multi-user mode: returns owned + shared collections.
        """
        if not settings.enable_multi_user or not user_id:
            return app_db.get_all_collections()

        # User's own collections
        owned = app_db.get_all_collections(owner_id=user_id)
        for c in owned:
            c['permission'] = 'owner'

        # Collections shared with user
        shared = app_db.get_shared_collections(user_id)
        for c in shared:
            c['permission'] = c.get('permission', 'read')

        # Deduplicate (in case of multiple shares to same collection)
        seen = {c['id'] for c in owned}
        for c in shared:
            if c['id'] not in seen:
                owned.append(c)
                seen.add(c['id'])

        return owned

    def update_collection(
        self,
        collection_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        embedding_model: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        app_db.update_collection(
            collection_id=collection_id,
            name=name,
            description=description,
            color=color,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_model=embedding_model,
        )
        return app_db.get_collection(collection_id)

    def delete_collection(self, collection_id: str) -> bool:
        if collection_id == "default":
            logger.warning("Cannot delete default collection")
            return False

        if not app_db.delete_collection(collection_id):
            return False

        collection_dir = self._get_collection_dir(collection_id)
        if collection_dir.exists():
            try:
                shutil.rmtree(collection_dir)
                logger.info(f"Deleted collection directory: {collection_dir}")
            except PermissionError as e:
                logger.warning(f"Could not delete collection directory (files may be locked): {e}")
            except Exception as e:
                logger.error(f"Error deleting collection directory: {e}")

        return True

    def get_documents_path(self, collection_id: str) -> Path:
        self._ensure_collection_dirs(collection_id)
        return self._get_documents_dir(collection_id)

    def get_indexes_path(self, collection_id: str) -> Path:
        self._ensure_collection_dirs(collection_id)
        return self._get_indexes_dir(collection_id)

    def add_document(self, collection_id: str, document_id: str):
        app_db.add_document_to_collection(collection_id, document_id)

    def remove_document(self, collection_id: str, document_id: str):
        app_db.remove_document_from_collection(collection_id, document_id)

    def get_collection_document_ids(self, collection_id: str) -> List[str]:
        return app_db.get_collection_documents(collection_id)

    def clear_collection_index(self, collection_id: str):
        indexes_dir = self._get_indexes_dir(collection_id)
        if indexes_dir.exists():
            shutil.rmtree(indexes_dir)
            indexes_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Cleared index for collection: {collection_id}")


# Global instance
collection_service = CollectionService()
