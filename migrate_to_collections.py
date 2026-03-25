"""Migrate existing data to collection-based storage.

This script moves existing documents and indexes from the legacy location
to the new collection-based directory structure.

Run this once after updating to the collections-aware version:
    python migrate_to_collections.py
"""

import shutil
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def migrate_to_collections():
    """Migrate legacy data to default collection."""
    data_dir = Path("data")
    legacy_docs_dir = data_dir / "documents"
    legacy_indexes_dir = data_dir / "indexes"

    collections_dir = data_dir / "collections"
    default_collection_dir = collections_dir / "default"
    new_docs_dir = default_collection_dir / "documents"
    new_indexes_dir = default_collection_dir / "indexes"

    # Check if migration is needed
    if not legacy_docs_dir.exists() and not legacy_indexes_dir.exists():
        logger.info("No legacy data found. Nothing to migrate.")
        return

    if default_collection_dir.exists():
        # Check if default collection already has data
        if (new_docs_dir.exists() and any(new_docs_dir.iterdir())) or \
           (new_indexes_dir.exists() and any(new_indexes_dir.iterdir())):
            logger.warning("Default collection already has data.")
            logger.warning("Skipping migration to avoid data loss.")
            logger.warning("If you want to re-migrate, delete data/collections/default first.")
            return

    # Create collection directories
    logger.info("Creating default collection directories...")
    new_docs_dir.mkdir(parents=True, exist_ok=True)
    new_indexes_dir.mkdir(parents=True, exist_ok=True)

    # Migrate documents
    if legacy_docs_dir.exists():
        doc_count = 0
        for doc_file in legacy_docs_dir.iterdir():
            if doc_file.is_file():
                dest = new_docs_dir / doc_file.name
                if not dest.exists():
                    shutil.copy2(doc_file, dest)
                    logger.info(f"  Copied: {doc_file.name}")
                    doc_count += 1
                else:
                    logger.info(f"  Skipped (already exists): {doc_file.name}")
        logger.info(f"Migrated {doc_count} documents to default collection.")

    # Migrate indexes
    if legacy_indexes_dir.exists():
        # Check for JSON storage
        json_dir = legacy_indexes_dir / "json"
        sqlite_dir = legacy_indexes_dir / "sqlite"

        # Prefer SQLite if it exists, otherwise use JSON
        source_dir = None
        if sqlite_dir.exists() and any(sqlite_dir.iterdir()):
            source_dir = sqlite_dir
            logger.info("Found SQLite indexes...")
        elif json_dir.exists() and any(json_dir.iterdir()):
            source_dir = json_dir
            logger.info("Found JSON indexes...")
        elif any(f for f in legacy_indexes_dir.iterdir() if f.is_file()):
            # Legacy: files directly in indexes dir
            source_dir = legacy_indexes_dir
            logger.info("Found legacy indexes (no subdirectory)...")

        if source_dir:
            file_count = 0
            for index_file in source_dir.iterdir():
                if index_file.is_file():
                    dest = new_indexes_dir / index_file.name
                    if not dest.exists():
                        shutil.copy2(index_file, dest)
                        logger.info(f"  Copied: {index_file.name}")
                        file_count += 1
                    else:
                        logger.info(f"  Skipped (already exists): {index_file.name}")
            logger.info(f"Migrated {file_count} index files to default collection.")
        else:
            logger.info("No index files found to migrate.")

    logger.info("")
    logger.info("=" * 50)
    logger.info("Migration complete!")
    logger.info("")
    logger.info("Your data has been COPIED (not moved) to:")
    logger.info(f"  Documents: {new_docs_dir}")
    logger.info(f"  Indexes: {new_indexes_dir}")
    logger.info("")
    logger.info("The original data is still in:")
    logger.info(f"  Documents: {legacy_docs_dir}")
    logger.info(f"  Indexes: {legacy_indexes_dir}")
    logger.info("")
    logger.info("Once you've verified everything works, you can delete")
    logger.info("the legacy directories to free up space.")
    logger.info("=" * 50)


if __name__ == "__main__":
    migrate_to_collections()
