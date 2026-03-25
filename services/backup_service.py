"""Backup and restore service for Asymptote indexes (v3.0 feature)."""

import json
import shutil
import zipfile
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

from config import settings
from services.collection_service import collection_service

logger = logging.getLogger(__name__)

# Backup format version for compatibility checking
BACKUP_FORMAT_VERSION = "1.0"


class BackupService:
    """Service for creating and restoring backups of document indexes."""

    def __init__(self, data_dir: Optional[Path] = None):
        """
        Initialize the backup service.

        Args:
            data_dir: Base data directory (defaults to settings.data_dir)
        """
        self.data_dir = data_dir or settings.data_dir
        self.backup_dir = self.data_dir / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(
        self,
        collection_id: str = "default",
        description: str = "",
        include_documents: bool = True
    ) -> Path:
        """
        Create a backup of a collection's index and optionally its documents.

        Args:
            collection_id: ID of the collection to backup
            description: Optional description for the backup
            include_documents: Whether to include source document files

        Returns:
            Path to the created backup file
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_name = f"asymptote_backup_{collection_id}_{timestamp}.zip"
        backup_path = self.backup_dir / backup_name

        logger.info(f"Creating backup: {backup_name}")

        # Get paths from collection service (correct path structure)
        index_dir = collection_service.get_indexes_path(collection_id)
        documents_dir = collection_service.get_documents_path(collection_id)

        # Get collection info for metadata
        collection_info = collection_service.get_collection(collection_id)
        collection_name = collection_info.get("name", collection_id) if collection_info else collection_id
        collection_color = collection_info.get("color", "#3b82f6") if collection_info else "#3b82f6"

        # Create backup metadata
        metadata = {
            "backup_format_version": BACKUP_FORMAT_VERSION,
            "schema_version": settings.schema_version,
            "created_at": datetime.utcnow().isoformat(),
            "collection_id": collection_id,
            "collection_name": collection_name,
            "collection_color": collection_color,
            "description": description,
            "embedding_model": settings.embedding_model,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "includes_documents": include_documents,
        }

        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Write backup metadata
            zf.writestr("backup_metadata.json", json.dumps(metadata, indent=2))

            # Backup index files
            if index_dir.exists():
                for file_path in index_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = f"indexes/{file_path.relative_to(index_dir)}"
                        zf.write(file_path, arcname)
                        logger.debug(f"Added to backup: {arcname}")

            # Optionally backup document files
            if include_documents and documents_dir.exists():
                for file_path in documents_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = f"documents/{file_path.relative_to(documents_dir)}"
                        zf.write(file_path, arcname)
                        logger.debug(f"Added to backup: {arcname}")

        backup_size = backup_path.stat().st_size
        logger.info(f"Backup created: {backup_path} ({backup_size / 1024 / 1024:.2f} MB)")

        return backup_path

    def restore_backup(
        self,
        backup_path: Path,
        target_collection_id: Optional[str] = None,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        Restore a collection from a backup file.

        Args:
            backup_path: Path to the backup zip file
            target_collection_id: Collection ID to restore to (None = use original ID)
            overwrite: Whether to overwrite existing data

        Returns:
            Dictionary with restore results
        """
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        logger.info(f"Restoring backup: {backup_path}")

        with zipfile.ZipFile(backup_path, 'r') as zf:
            # Read and validate backup metadata
            try:
                metadata_content = zf.read("backup_metadata.json")
                metadata = json.loads(metadata_content)
            except (KeyError, json.JSONDecodeError) as e:
                raise ValueError(f"Invalid backup file: {e}")

            # Check format compatibility
            backup_version = metadata.get("backup_format_version", "0.0")
            if backup_version != BACKUP_FORMAT_VERSION:
                logger.warning(
                    f"Backup format version mismatch: {backup_version} vs {BACKUP_FORMAT_VERSION}"
                )

            # Determine target collection
            collection_id = target_collection_id or metadata.get("collection_id", "default")

            # Check if collection exists in database
            existing_collection = collection_service.get_collection(collection_id)

            # Get paths - but don't create directories yet
            collection_dir = collection_service.base_dir / collection_id
            index_dir = collection_dir / "indexes"
            documents_dir = collection_dir / "documents"

            # Check if there's actual data (not just empty directories)
            has_index_data = index_dir.exists() and any(f for f in index_dir.rglob("*") if f.is_file())
            has_document_data = documents_dir.exists() and any(f for f in documents_dir.rglob("*") if f.is_file())

            # If collection doesn't exist in database but has orphaned files,
            # clean them up automatically (they're from a deleted collection)
            if not existing_collection and (has_index_data or has_document_data):
                logger.warning(f"Found orphaned files for deleted collection '{collection_id}', cleaning up...")
                if has_index_data:
                    for f in index_dir.rglob("*"):
                        if f.is_file():
                            f.unlink()
                if has_document_data:
                    for f in documents_dir.rglob("*"):
                        if f.is_file():
                            f.unlink()
                has_index_data = False
                has_document_data = False

            # Only require overwrite if collection exists AND has actual data
            if existing_collection and (has_index_data or has_document_data) and not overwrite:
                raise ValueError(
                    f"Collection '{collection_id}' already has data. "
                    "Use overwrite=True to replace it."
                )

            # If collection doesn't exist in database, create it with backup metadata
            if not existing_collection and collection_id != "default":
                # Use the stored name if available, otherwise fall back to collection_id
                collection_name = metadata.get("collection_name", metadata.get("collection_id", collection_id))
                collection_color = metadata.get("collection_color", "#3b82f6")
                backup_desc = metadata.get("description", "")
                restore_desc = f"Restored from backup" + (f": {backup_desc}" if backup_desc else "")

                logger.info(f"Creating collection '{collection_name}' (ID: {collection_id}) from backup metadata")
                collection_service.create_collection(
                    name=collection_name,
                    description=restore_desc,
                    color=collection_color,
                    chunk_size=metadata.get("chunk_size", settings.chunk_size),
                    chunk_overlap=metadata.get("chunk_overlap", settings.chunk_overlap),
                    embedding_model=metadata.get("embedding_model", settings.embedding_model),
                )

            # Clear existing data if overwriting an existing collection
            if existing_collection and has_index_data and overwrite:
                logger.warning(f"Removing existing index files in: {index_dir}")
                for f in index_dir.rglob("*"):
                    if f.is_file():
                        f.unlink()

            if existing_collection and has_document_data and overwrite and metadata.get("includes_documents"):
                logger.warning(f"Removing existing documents in: {documents_dir}")
                for f in documents_dir.rglob("*"):
                    if f.is_file():
                        f.unlink()

            # Ensure target directories exist
            index_dir.mkdir(parents=True, exist_ok=True)

            # Extract files
            files_restored = {"indexes": 0, "documents": 0}

            all_items = zf.namelist()
            logger.info(f"Backup contains {len(all_items)} items: {all_items}")

            for item in all_items:
                if item == "backup_metadata.json":
                    continue

                # Skip directory entries (end with /)
                if item.endswith("/"):
                    continue

                if item.startswith("indexes/"):
                    # Extract index files
                    relative_path = item[len("indexes/"):]
                    if relative_path:
                        target_path = index_dir / relative_path
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(item) as src, open(target_path, 'wb') as dst:
                            dst.write(src.read())
                        files_restored["indexes"] += 1
                        logger.info(f"Restored index file: {target_path}")

                elif item.startswith("documents/"):
                    # Extract document files
                    relative_path = item[len("documents/"):]
                    if relative_path:
                        documents_dir.mkdir(parents=True, exist_ok=True)
                        target_path = documents_dir / relative_path
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(item) as src, open(target_path, 'wb') as dst:
                            dst.write(src.read())
                        files_restored["documents"] += 1
                        logger.info(f"Restored document file: {target_path}")

        result = {
            "success": True,
            "collection_id": collection_id,
            "storage_type": storage_type,
            "files_restored": files_restored,
            "backup_metadata": metadata,
        }

        logger.info(
            f"Restore complete: {files_restored['indexes']} index files, "
            f"{files_restored['documents']} document files"
        )

        return result

    def list_backups(self) -> List[Dict[str, Any]]:
        """
        List all available backups.

        Returns:
            List of backup information dictionaries
        """
        backups = []

        for backup_file in self.backup_dir.glob("asymptote_backup_*.zip"):
            try:
                with zipfile.ZipFile(backup_file, 'r') as zf:
                    metadata_content = zf.read("backup_metadata.json")
                    metadata = json.loads(metadata_content)

                    backups.append({
                        "filename": backup_file.name,
                        "path": str(backup_file),
                        "size_mb": backup_file.stat().st_size / 1024 / 1024,
                        "created_at": metadata.get("created_at"),
                        "collection_id": metadata.get("collection_id"),
                        "description": metadata.get("description"),
                        "includes_documents": metadata.get("includes_documents", False),
                        "storage_type": metadata.get("storage_type"),
                        "embedding_model": metadata.get("embedding_model"),
                    })
            except Exception as e:
                logger.warning(f"Failed to read backup {backup_file}: {e}")
                backups.append({
                    "filename": backup_file.name,
                    "path": str(backup_file),
                    "size_mb": backup_file.stat().st_size / 1024 / 1024,
                    "error": str(e),
                })

        # Sort by creation time (newest first)
        backups.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        return backups

    def delete_backup(self, backup_filename: str) -> bool:
        """
        Delete a backup file.

        Args:
            backup_filename: Name of the backup file to delete

        Returns:
            True if deleted, False if not found
        """
        backup_path = self.backup_dir / backup_filename

        if not backup_path.exists():
            return False

        if not backup_path.name.startswith("asymptote_backup_"):
            raise ValueError("Invalid backup filename")

        backup_path.unlink()
        logger.info(f"Deleted backup: {backup_filename}")
        return True

    def get_backup_info(self, backup_filename: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific backup.

        Args:
            backup_filename: Name of the backup file

        Returns:
            Backup metadata dictionary or None if not found
        """
        backup_path = self.backup_dir / backup_filename

        if not backup_path.exists():
            return None

        try:
            with zipfile.ZipFile(backup_path, 'r') as zf:
                metadata_content = zf.read("backup_metadata.json")
                metadata = json.loads(metadata_content)

                # Count files in backup
                file_list = zf.namelist()
                index_files = [f for f in file_list if f.startswith("indexes/")]
                doc_files = [f for f in file_list if f.startswith("documents/")]

                return {
                    **metadata,
                    "filename": backup_filename,
                    "path": str(backup_path),
                    "size_mb": backup_path.stat().st_size / 1024 / 1024,
                    "index_file_count": len(index_files),
                    "document_file_count": len(doc_files),
                    "file_list": file_list,
                }
        except Exception as e:
            logger.error(f"Failed to read backup info: {e}")
            return None


# Convenience functions for CLI usage
def create_backup(collection_id: str = "default", description: str = "",
                  include_documents: bool = True) -> Path:
    """Create a backup of a collection."""
    service = BackupService()
    return service.create_backup(collection_id, description, include_documents)


def restore_backup(backup_path: str, target_collection_id: Optional[str] = None,
                   overwrite: bool = False) -> Dict[str, Any]:
    """Restore a collection from a backup."""
    service = BackupService()
    return service.restore_backup(Path(backup_path), target_collection_id, overwrite)


def list_backups() -> List[Dict[str, Any]]:
    """List all available backups."""
    service = BackupService()
    return service.list_backups()
