"""SQLite-based metadata storage for document chunks."""

import sqlite3
from pathlib import Path
from typing import List, Optional, Dict, Any
import json
import logging

logger = logging.getLogger(__name__)

# Current schema version - increment when making breaking changes
SCHEMA_VERSION = "3.2"


class MetadataStore:
    """SQLite-based metadata storage for scalability."""

    def __init__(self, db_path: Path):
        """
        Initialize the metadata store.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()
        self._migrate_schema()

    def _init_db(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            # Schema version tracking table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_info (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id TEXT UNIQUE NOT NULL,
                    document_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    -- v3.0: Format-aware metadata
                    source_format TEXT DEFAULT NULL,
                    extraction_method TEXT DEFAULT 'text',
                    -- v3.0: CSV-specific metadata (JSON-encoded)
                    csv_row_number INTEGER DEFAULT NULL,
                    csv_columns TEXT DEFAULT NULL,
                    csv_values TEXT DEFAULT NULL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_document_id
                ON chunks(document_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunk_id
                ON chunks(chunk_id)
            """)

            # Note: idx_source_format is created in _migrate_schema after columns are added

            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    num_pages INTEGER NOT NULL,
                    num_chunks INTEGER NOT NULL,
                    upload_timestamp TEXT NOT NULL,
                    -- v3.0: Document-level versioning
                    source_format TEXT DEFAULT NULL,
                    extraction_method TEXT DEFAULT 'text',
                    embedding_model TEXT DEFAULT NULL,
                    chunk_size INTEGER DEFAULT NULL,
                    chunk_overlap INTEGER DEFAULT NULL,
                    schema_version TEXT DEFAULT '3.0',
                    injection_warnings TEXT DEFAULT NULL
                )
            """)

            # Check if this is a new database by looking for v3.0 columns
            # Only set schema version to 3.0 if the table was just created with new columns
            chunk_columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
            is_new_database = "source_format" in chunk_columns

            if is_new_database:
                # Set initial schema version for new databases
                conn.execute("""
                    INSERT OR IGNORE INTO schema_info (key, value)
                    VALUES ('version', ?)
                """, (SCHEMA_VERSION,))

                # Create index on source_format
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_source_format
                    ON chunks(source_format)
                """)

            conn.commit()
            logger.info(f"Initialized metadata database at {self.db_path}")

    def _migrate_schema(self):
        """Run schema migrations for existing databases."""
        with sqlite3.connect(self.db_path) as conn:
            # Check current schema version
            try:
                cursor = conn.execute(
                    "SELECT value FROM schema_info WHERE key = 'version'"
                )
                row = cursor.fetchone()
                current_version = row[0] if row else "1.0"
            except sqlite3.OperationalError:
                # schema_info table doesn't exist, very old schema
                current_version = "1.0"

            if current_version == SCHEMA_VERSION:
                return  # Already up to date

            logger.info(f"Migrating schema from {current_version} to {SCHEMA_VERSION}")

            # Migration from 1.0/2.0 to 3.0
            if current_version in ("1.0", "2.0"):
                self._migrate_to_v3(conn)
                current_version = "3.0"

            # Migration from 3.0 to 3.1
            if current_version == "3.0":
                self._migrate_to_v3_1(conn)
                current_version = "3.1"

            # Migration from 3.1 to 3.2
            if current_version == "3.1":
                self._migrate_to_v3_2(conn)

            # Update schema version
            conn.execute("""
                INSERT OR REPLACE INTO schema_info (key, value)
                VALUES ('version', ?)
            """, (SCHEMA_VERSION,))
            conn.commit()

            logger.info(f"Schema migration to {SCHEMA_VERSION} completed")

    def _migrate_to_v3(self, conn: sqlite3.Connection):
        """Migrate from v1.0/v2.0 to v3.0 schema."""
        # Add new columns to chunks table if they don't exist
        chunk_columns = self._get_table_columns(conn, "chunks")

        new_chunk_columns = [
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("source_format", "TEXT DEFAULT NULL"),
            ("extraction_method", "TEXT DEFAULT 'text'"),
            ("csv_row_number", "INTEGER DEFAULT NULL"),
            ("csv_columns", "TEXT DEFAULT NULL"),
            ("csv_values", "TEXT DEFAULT NULL"),
        ]

        for col_name, col_def in new_chunk_columns:
            if col_name not in chunk_columns:
                conn.execute(f"ALTER TABLE chunks ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added column {col_name} to chunks table")

        # Add new columns to documents table if they don't exist
        doc_columns = self._get_table_columns(conn, "documents")

        new_doc_columns = [
            ("source_format", "TEXT DEFAULT NULL"),
            ("extraction_method", "TEXT DEFAULT 'text'"),
            ("embedding_model", "TEXT DEFAULT NULL"),
            ("chunk_size", "INTEGER DEFAULT NULL"),
            ("chunk_overlap", "INTEGER DEFAULT NULL"),
            ("schema_version", "TEXT DEFAULT '3.0'"),
        ]

        for col_name, col_def in new_doc_columns:
            if col_name not in doc_columns:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added column {col_name} to documents table")

        # Create new indexes
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_source_format
            ON chunks(source_format)
        """)

        # Infer source_format from filename for existing documents
        conn.execute("""
            UPDATE documents SET source_format =
                CASE
                    WHEN LOWER(filename) LIKE '%.pdf' THEN 'pdf'
                    WHEN LOWER(filename) LIKE '%.txt' THEN 'txt'
                    WHEN LOWER(filename) LIKE '%.docx' THEN 'docx'
                    WHEN LOWER(filename) LIKE '%.csv' THEN 'csv'
                    WHEN LOWER(filename) LIKE '%.md' THEN 'md'
                    WHEN LOWER(filename) LIKE '%.json' THEN 'json'
                    ELSE 'unknown'
                END
            WHERE source_format IS NULL
        """)

        # Update chunks with source_format from their documents
        conn.execute("""
            UPDATE chunks SET source_format = (
                SELECT d.source_format FROM documents d
                WHERE d.document_id = chunks.document_id
            )
            WHERE source_format IS NULL
        """)

    def _migrate_to_v3_1(self, conn: sqlite3.Connection):
        """Migrate from v3.0 to v3.1 schema (local file reference support)."""
        doc_columns = self._get_table_columns(conn, "documents")

        new_doc_columns = [
            ("source_path", "TEXT DEFAULT NULL"),      # Original filesystem path for local references
            ("source_type", "TEXT DEFAULT 'upload'"),  # 'upload' or 'local_reference'
        ]

        for col_name, col_def in new_doc_columns:
            if col_name not in doc_columns:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added column {col_name} to documents table")

    def _migrate_to_v3_2(self, conn: sqlite3.Connection):
        """Migrate from v3.1 to v3.2 schema (injection warnings storage)."""
        doc_columns = self._get_table_columns(conn, "documents")
        if "injection_warnings" not in doc_columns:
            conn.execute("ALTER TABLE documents ADD COLUMN injection_warnings TEXT DEFAULT NULL")
            logger.info("Added column injection_warnings to documents table")

    def _ensure_v3_1_columns(self, conn: sqlite3.Connection):
        """Ensure v3.1+ columns exist (defensive migration for runtime checks)."""
        doc_columns = self._get_table_columns(conn, "documents")
        migrated = False
        if "source_path" not in doc_columns:
            logger.warning("Running defensive v3.1 migration - columns were missing")
            self._migrate_to_v3_1(conn)
            migrated = True
        if "injection_warnings" not in doc_columns:
            logger.warning("Running defensive v3.2 migration - injection_warnings column missing")
            self._migrate_to_v3_2(conn)
            migrated = True
        if migrated:
            conn.execute("""
                INSERT OR REPLACE INTO schema_info (key, value)
                VALUES ('version', ?)
            """, (SCHEMA_VERSION,))
            conn.commit()

    def _get_table_columns(self, conn: sqlite3.Connection, table_name: str) -> set:
        """Get set of column names for a table."""
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cursor.fetchall()}

    def add_chunks(self, chunks: List[dict]):
        """
        Add chunk metadata to the database.

        Args:
            chunks: List of chunk metadata dictionaries
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO chunks
                (chunk_id, document_id, filename, page_number, chunk_index, text,
                 source_format, extraction_method, csv_row_number, csv_columns, csv_values)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    chunk["chunk_id"],
                    chunk["document_id"],
                    chunk["filename"],
                    chunk["page_number"],
                    chunk["chunk_index"],
                    chunk["text"],
                    chunk.get("source_format"),
                    chunk.get("extraction_method", "text"),
                    chunk.get("csv_row_number"),
                    json.dumps(chunk["csv_columns"]) if chunk.get("csv_columns") else None,
                    json.dumps(chunk["csv_values"]) if chunk.get("csv_values") else None,
                )
                for chunk in chunks
            ])
            conn.commit()

        logger.debug(f"Added {len(chunks)} chunks to metadata store")

    def get_chunk_by_index(self, index: int) -> Optional[dict]:
        """
        Get chunk metadata by its sequential index.

        Args:
            index: Sequential index (0-based, matches FAISS index)

        Returns:
            Chunk metadata dictionary or None
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT chunk_id, document_id, filename, page_number, chunk_index, text,
                       source_format, extraction_method, csv_row_number, csv_columns, csv_values
                FROM chunks
                ORDER BY id
                LIMIT 1 OFFSET ?
            """, (index,))

            row = cursor.fetchone()
            if row:
                result = dict(row)
                # Decode JSON fields
                if result.get("csv_columns"):
                    result["csv_columns"] = json.loads(result["csv_columns"])
                if result.get("csv_values"):
                    result["csv_values"] = json.loads(result["csv_values"])
                return result
            return None

    def get_chunks_by_document(self, document_id: str) -> List[dict]:
        """
        Get all chunks for a specific document.

        Args:
            document_id: Document identifier

        Returns:
            List of chunk metadata dictionaries
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT chunk_id, document_id, filename, page_number, chunk_index, text,
                       source_format, extraction_method, csv_row_number, csv_columns, csv_values
                FROM chunks
                WHERE document_id = ?
                ORDER BY page_number, chunk_index
            """, (document_id,))

            results = []
            for row in cursor.fetchall():
                result = dict(row)
                # Decode JSON fields
                if result.get("csv_columns"):
                    result["csv_columns"] = json.loads(result["csv_columns"])
                if result.get("csv_values"):
                    result["csv_values"] = json.loads(result["csv_values"])
                results.append(result)
            return results

    def delete_document(self, document_id: str) -> int:
        """
        Delete all chunks for a specific document.

        Args:
            document_id: Document identifier

        Returns:
            Number of chunks deleted
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                DELETE FROM chunks
                WHERE document_id = ?
            """, (document_id,))

            deleted = cursor.rowcount

            conn.execute("""
                DELETE FROM documents
                WHERE document_id = ?
            """, (document_id,))

            conn.commit()

        logger.info(f"Deleted {deleted} chunks for document {document_id}")
        return deleted

    def get_total_chunks(self) -> int:
        """Get total number of chunks in the store."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM chunks")
            return cursor.fetchone()[0]

    def list_documents(self) -> List[dict]:
        """
        List all documents with their statistics.

        Returns:
            List of document metadata dictionaries including source_type and source_path
        """
        with sqlite3.connect(self.db_path) as conn:
            # Ensure v3.1 columns exist
            self._ensure_v3_1_columns(conn)

            conn.row_factory = sqlite3.Row
            # Join with documents table to get source_type and source_path
            cursor = conn.execute("""
                SELECT
                    c.document_id,
                    c.filename,
                    COUNT(*) as num_chunks,
                    COUNT(DISTINCT c.page_number) as num_pages,
                    d.source_type,
                    d.source_path,
                    d.upload_timestamp,
                    d.injection_warnings
                FROM chunks c
                LEFT JOIN documents d ON c.document_id = d.document_id
                GROUP BY c.document_id, c.filename
                ORDER BY COALESCE(MAX(c.created_at), '1970-01-01') DESC
            """)

            rows = []
            for row in cursor.fetchall():
                r = dict(row)
                if r.get("injection_warnings"):
                    r["injection_warnings"] = json.loads(r["injection_warnings"])
                rows.append(r)
            return rows

    def add_document(self, document_id: str, filename: str, num_pages: int,
                     num_chunks: int, upload_timestamp: str,
                     source_format: str = None, extraction_method: str = "text",
                     embedding_model: str = None, chunk_size: int = None,
                     chunk_overlap: int = None, source_path: str = None,
                     source_type: str = "upload", injection_warnings: dict = None):
        """
        Add document metadata.

        Args:
            document_id: Document identifier
            filename: Original filename
            num_pages: Number of pages
            num_chunks: Number of chunks
            upload_timestamp: ISO timestamp of upload
            source_format: Source file format (pdf, txt, etc.)
            extraction_method: How text was extracted (text, ocr, hybrid)
            embedding_model: Embedding model used
            chunk_size: Chunk size used during indexing
            chunk_overlap: Chunk overlap used during indexing
            source_path: Original filesystem path (for local references)
            source_type: Source type: 'upload' or 'local_reference'
        """
        with sqlite3.connect(self.db_path) as conn:
            # Ensure v3.1 columns exist (defensive migration for cached instances)
            self._ensure_v3_1_columns(conn)

            injection_warnings_json = json.dumps(injection_warnings) if injection_warnings else None
            conn.execute("""
                INSERT OR REPLACE INTO documents
                (document_id, filename, num_pages, num_chunks, upload_timestamp,
                 source_format, extraction_method, embedding_model, chunk_size,
                 chunk_overlap, schema_version, source_path, source_type, injection_warnings)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (document_id, filename, num_pages, num_chunks, upload_timestamp,
                  source_format, extraction_method, embedding_model, chunk_size,
                  chunk_overlap, SCHEMA_VERSION, source_path, source_type, injection_warnings_json))
            conn.commit()

    def get_all_chunks_ordered(self) -> List[dict]:
        """
        Get all chunks in order (for migration/initialization).

        Returns:
            List of all chunk metadata dictionaries
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT chunk_id, document_id, filename, page_number, chunk_index, text,
                       source_format, extraction_method, csv_row_number, csv_columns, csv_values
                FROM chunks
                ORDER BY id
            """)

            results = []
            for row in cursor.fetchall():
                result = dict(row)
                # Decode JSON fields
                if result.get("csv_columns"):
                    result["csv_columns"] = json.loads(result["csv_columns"])
                if result.get("csv_values"):
                    result["csv_values"] = json.loads(result["csv_values"])
                results.append(result)
            return results

    def get_schema_version(self) -> str:
        """Get the current schema version."""
        with sqlite3.connect(self.db_path) as conn:
            try:
                cursor = conn.execute(
                    "SELECT value FROM schema_info WHERE key = 'version'"
                )
                row = cursor.fetchone()
                return row[0] if row else "1.0"
            except sqlite3.OperationalError:
                return "1.0"

    def get_document_info(self, document_id: str) -> Optional[dict]:
        """
        Get document metadata by ID.

        Args:
            document_id: Document identifier

        Returns:
            Document metadata dictionary or None
        """
        with sqlite3.connect(self.db_path) as conn:
            # Ensure v3.1 columns exist (defensive migration for cached instances)
            self._ensure_v3_1_columns(conn)

            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT document_id, filename, num_pages, num_chunks, upload_timestamp,
                       source_format, extraction_method, embedding_model, chunk_size,
                       chunk_overlap, schema_version, source_path, source_type
                FROM documents
                WHERE document_id = ?
            """, (document_id,))

            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def document_exists(self, document_id: str) -> bool:
        """
        Check if a document exists in the store.

        Args:
            document_id: Document identifier

        Returns:
            True if document exists
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT 1 FROM chunks WHERE document_id = ? LIMIT 1
            """, (document_id,))
            return cursor.fetchone() is not None

    def get_document_chunk_indices(self, document_id: str) -> List[int]:
        """
        Get the FAISS indices for all chunks of a document.

        Args:
            document_id: Document identifier

        Returns:
            List of chunk indices (0-based, ordered by insertion)
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT (ROW_NUMBER() OVER (ORDER BY id) - 1) as idx
                FROM chunks
                WHERE document_id = ?
                ORDER BY id
            """, (document_id,))

            return [row[0] for row in cursor.fetchall()]

    def update_document_source(self, document_id: str, source_path: str,
                                source_type: str = "local_reference"):
        """
        Update the source path and type for a document.

        Args:
            document_id: Document identifier
            source_path: Original filesystem path
            source_type: Source type: 'upload' or 'local_reference'
        """
        with sqlite3.connect(self.db_path) as conn:
            # Ensure v3.1 columns exist (defensive migration for cached instances)
            self._ensure_v3_1_columns(conn)

            conn.execute("""
                UPDATE documents
                SET source_path = ?, source_type = ?
                WHERE document_id = ?
            """, (source_path, source_type, document_id))
            conn.commit()
            logger.info(f"Updated source for document {document_id}: {source_type} -> {source_path}")

    def clear_all(self):
        """Clear all chunks and documents from the metadata store."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            conn.commit()
            logger.info("Cleared all metadata from database")
