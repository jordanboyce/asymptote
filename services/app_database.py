"""Application database for persistent configuration and state.

Stores:
- Configuration settings (overrides .env)
- Re-indexing progress/status
- Application state
- User accounts and collection sharing
"""

import sqlite3
import json
import logging
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from config import settings
from services.db_backend import DatabaseBackend

logger = logging.getLogger(__name__)


def _get_document_count_from_metadata(collection_id: str, data_dir: Path = None) -> int:
    """Get the actual document count from the collection's metadata.db.

    This is the source of truth for document counts, avoiding sync issues
    between app.db and the actual indexed data.

    Args:
        collection_id: Collection ID
        data_dir: Base data directory

    Returns:
        Number of unique documents in the collection's index
    """
    if data_dir is None:
        data_dir = settings.data_dir
    metadata_db_path = data_dir / "collections" / collection_id / "indexes" / "metadata.db"

    if not metadata_db_path.exists():
        return 0

    try:
        with sqlite3.connect(metadata_db_path) as conn:
            cursor = conn.execute("SELECT COUNT(DISTINCT document_id) FROM chunks")
            return cursor.fetchone()[0]
    except Exception as e:
        logger.warning(f"Could not read metadata.db for collection {collection_id}: {e}")
        return 0


class SQLiteBackend(DatabaseBackend):
    """Manages application-level persistent data in SQLite."""

    def __init__(self, db_path: Path = Path("data/app.db")):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS reindex_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    total_documents INTEGER DEFAULT 0,
                    processed_documents INTEGER DEFAULT 0,
                    current_file TEXT,
                    error TEXT,
                    config_snapshot TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_preferences (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    selected_providers TEXT,
                    rerank_enabled INTEGER DEFAULT 1,
                    synthesize_enabled INTEGER DEFAULT 1,
                    default_provider TEXT,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    top_k INTEGER,
                    results_count INTEGER,
                    ai_provider TEXT,
                    ai_used INTEGER DEFAULT 0,
                    results_json TEXT,
                    execution_time_ms INTEGER
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_search_timestamp
                ON search_history(timestamp DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_search_query
                ON search_history(query)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    color TEXT DEFAULT '#3b82f6',
                    chunk_size INTEGER DEFAULT 500,
                    chunk_overlap INTEGER DEFAULT 50,
                    embedding_model TEXT DEFAULT 'all-MiniLM-L6-v2',
                    owner_id TEXT DEFAULT 'default',
                    guide TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS collection_documents (
                    collection_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY (collection_id, document_id),
                    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_collection_documents
                ON collection_documents(collection_id)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS upload_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_files INTEGER DEFAULT 0,
                    processed_files INTEGER DEFAULT 0,
                    current_file TEXT,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    result_summary TEXT,
                    phase TEXT,
                    phase_progress INTEGER DEFAULT 0,
                    phase_detail TEXT,
                    chunks_processed INTEGER DEFAULT 0,
                    chunks_total INTEGER DEFAULT 0,
                    job_type TEXT DEFAULT 'upload'
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS mcp_resources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    collection_id TEXT NOT NULL UNIQUE,
                    repo_url TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # ── v4: Users table ──────────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    display_name TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
            """)

            # ── v4: Collection shares ────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS collection_shares (
                    id TEXT PRIMARY KEY,
                    collection_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    permission TEXT NOT NULL DEFAULT 'read',
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    is_active INTEGER DEFAULT 1,
                    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS collection_share_users (
                    share_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    accepted_at TEXT NOT NULL,
                    PRIMARY KEY (share_id, user_id),
                    FOREIGN KEY (share_id) REFERENCES collection_shares(id) ON DELETE CASCADE
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_shares_collection
                ON collection_shares(collection_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_share_users_user
                ON collection_share_users(user_id)
            """)

            # ── Migrations for existing databases ────────────
            # MUST run before any index that references a migrated column
            # (e.g. idx_collections_owner below depends on owner_id).
            # Add owner_id to collections if missing
            try:
                conn.execute("ALTER TABLE collections ADD COLUMN owner_id TEXT DEFAULT 'default'")
            except sqlite3.OperationalError:
                pass

            # Add MCP display name/description (PII-safe aliases)
            for col in ["mcp_display_name TEXT", "mcp_display_description TEXT"]:
                try:
                    conn.execute(f"ALTER TABLE collections ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass

            # v4.3: per-collection guide (markdown, surfaced to calling LLM)
            try:
                conn.execute("ALTER TABLE collections ADD COLUMN guide TEXT")
            except sqlite3.OperationalError:
                pass

            # Add phase columns to upload_jobs if missing
            for col, default in [
                ("phase", "TEXT"),
                ("phase_progress", "INTEGER DEFAULT 0"),
                ("phase_detail", "TEXT"),
                ("chunks_processed", "INTEGER DEFAULT 0"),
                ("chunks_total", "INTEGER DEFAULT 0"),
                ("job_type", "TEXT DEFAULT 'upload'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE upload_jobs ADD COLUMN {col} {default}")
                except sqlite3.OperationalError:
                    pass

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_collections_owner
                ON collections(owner_id)
            """)

            # ── v5: Expertise Library ─────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS expertise_packs (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    description TEXT,
                    body        TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS collection_expertise (
                    collection_id TEXT NOT NULL,
                    pack_id       TEXT NOT NULL,
                    attached_at   TEXT NOT NULL,
                    PRIMARY KEY (collection_id, pack_id),
                    FOREIGN KEY (pack_id) REFERENCES expertise_packs(id) ON DELETE CASCADE
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_collection_expertise_collection
                ON collection_expertise(collection_id)
            """)

            # Ensure default collection exists
            cursor = conn.execute("SELECT id FROM collections WHERE id = 'default'")
            if not cursor.fetchone():
                timestamp = datetime.utcnow().isoformat()
                conn.execute(
                    """
                    INSERT INTO collections (id, name, description, color, embedding_model, owner_id, created_at, updated_at)
                    VALUES ('default', 'Default', 'Default document collection', '#3b82f6', ?, 'default', ?, ?)
                    """,
                    (settings.embedding_model, timestamp, timestamp)
                )
            else:
                conn.execute(
                    """
                    UPDATE collections SET embedding_model = ?
                    WHERE id = 'default' AND embedding_model = 'all-MiniLM-L6-v2'
                    """,
                    (settings.embedding_model,)
                )

            # Ensure default user exists
            cursor = conn.execute("SELECT id FROM users WHERE id = 'default'")
            if not cursor.fetchone():
                timestamp = datetime.utcnow().isoformat()
                conn.execute(
                    "INSERT INTO users (id, display_name, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
                    ("default", "Default User", timestamp, timestamp)
                )

            conn.commit()
            logger.info(f"Application database initialized at {self.db_path}")

    # ── Configuration ────────────────────────────────────────

    def get_config(self, key: str, default: Any = None) -> Any:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT value FROM config WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                try:
                    return json.loads(row[0])
                except json.JSONDecodeError:
                    return row[0]
            return default

    def set_config(self, key: str, value: Any):
        value_str = json.dumps(value) if not isinstance(value, str) else value
        timestamp = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value_str, timestamp)
            )
            conn.commit()
            logger.info(f"Config updated: {key} = {value}")

    def get_all_config(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT key, value FROM config")
            config = {}
            for key, value in cursor.fetchall():
                try:
                    config[key] = json.loads(value)
                except json.JSONDecodeError:
                    config[key] = value
            return config

    def delete_config(self, key: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM config WHERE key = ?", (key,))
            conn.commit()

    # ── Users ────────────────────────────────────────────────

    def upsert_user(self, user_id: str, display_name: Optional[str] = None) -> Dict[str, Any]:
        timestamp = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute("SELECT id, display_name, first_seen_at FROM users WHERE id = ?", (user_id,)).fetchone()
            if existing:
                if display_name:
                    conn.execute("UPDATE users SET display_name = ?, last_seen_at = ? WHERE id = ?", (display_name, timestamp, user_id))
                else:
                    conn.execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (timestamp, user_id))
                conn.commit()
                result = {"id": user_id, "display_name": display_name or existing[1], "first_seen_at": existing[2], "last_seen_at": timestamp}
            else:
                dname = display_name or user_id
                conn.execute(
                    "INSERT INTO users (id, display_name, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
                    (user_id, dname, timestamp, timestamp)
                )
                conn.commit()
                result = {"id": user_id, "display_name": dname, "first_seen_at": timestamp, "last_seen_at": timestamp}

            # Multi-user mode: every authenticated user needs at least one
            # owned collection. The bootstrap "default" collection is owned
            # by "default" (single-user sentinel), so in multi-user mode each
            # user gets their own starter collection on first sight.
            # Cheap idempotent check — runs once per session in practice.
            if settings.enable_multi_user:
                owned = conn.execute(
                    "SELECT 1 FROM collections WHERE owner_id = ? LIMIT 1",
                    (user_id,),
                ).fetchone()
                if not owned:
                    import uuid
                    starter_id = uuid.uuid4().hex[:8]
                    conn.execute(
                        """
                        INSERT INTO collections (id, name, description, color, embedding_model, owner_id, created_at, updated_at)
                        VALUES (?, 'Default', 'Your starter collection', '#3b82f6', ?, ?, ?, ?)
                        """,
                        (starter_id, settings.embedding_model, user_id, timestamp, timestamp),
                    )
                    conn.commit()

            return result

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_all_users(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY first_seen_at ASC").fetchall()]

    # ── Collections ──────────────────────────────────────────

    def create_collection(
        self,
        name: str,
        description: str = "",
        color: str = "#3b82f6",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        embedding_model: str = None,
        owner_id: str = "default",
        mcp_display_name: str | None = None,
        mcp_display_description: str | None = None,
    ) -> str:
        if embedding_model is None:
            embedding_model = settings.embedding_model
        collection_id = str(uuid.uuid4())[:8]
        timestamp = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO collections
                (id, name, description, color, chunk_size, chunk_overlap, embedding_model, owner_id, mcp_display_name, mcp_display_description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (collection_id, name, description, color, chunk_size, chunk_overlap, embedding_model, owner_id, mcp_display_name, mcp_display_description, timestamp, timestamp)
            )
            conn.commit()
            logger.info(f"Created collection: {name} ({collection_id}) owner={owner_id}")
            return collection_id

    def get_collection(self, collection_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
            if row:
                collection = dict(row)
                collection['document_count'] = _get_document_count_from_metadata(collection_id)
                return collection
            return None

    def get_all_collections(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if owner_id:
                cursor = conn.execute(
                    "SELECT * FROM collections WHERE owner_id = ? ORDER BY created_at ASC",
                    (owner_id,)
                )
            else:
                cursor = conn.execute("SELECT * FROM collections ORDER BY created_at ASC")
            collections = []
            for row in cursor.fetchall():
                collection = dict(row)
                collection['document_count'] = _get_document_count_from_metadata(collection['id'])
                collections.append(collection)
            return collections

    def update_collection(
        self,
        collection_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        embedding_model: Optional[str] = None,
        mcp_display_name: Optional[str] = None,
        mcp_display_description: Optional[str] = None,
        guide: Optional[str] = None,
    ):
        updates = []
        params = []
        for field, value in [("name", name), ("description", description), ("color", color),
                             ("chunk_size", chunk_size), ("chunk_overlap", chunk_overlap),
                             ("embedding_model", embedding_model),
                             ("mcp_display_name", mcp_display_name),
                             ("mcp_display_description", mcp_display_description),
                             ("guide", guide)]:
            if value is not None:
                updates.append(f"{field} = ?")
                params.append(value)
        if not updates:
            return
        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(collection_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"UPDATE collections SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

    def delete_collection(self, collection_id: str) -> bool:
        if collection_id == "default":
            return False
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ── Collection Documents ─────────────────────────────────

    def add_document_to_collection(self, collection_id: str, document_id: str):
        timestamp = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO collection_documents (collection_id, document_id, added_at) VALUES (?, ?, ?)",
                (collection_id, document_id, timestamp)
            )
            conn.commit()

    def remove_document_from_collection(self, collection_id: str, document_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM collection_documents WHERE collection_id = ? AND document_id = ?", (collection_id, document_id))
            conn.commit()

    def get_collection_documents(self, collection_id: str) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            return [r[0] for r in conn.execute(
                "SELECT document_id FROM collection_documents WHERE collection_id = ?", (collection_id,)
            ).fetchall()]

    def get_document_collections(self, document_id: str) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            return [r[0] for r in conn.execute(
                "SELECT collection_id FROM collection_documents WHERE document_id = ?", (document_id,)
            ).fetchall()]

    # ── Collection Sharing ───────────────────────────────────

    def create_share(self, collection_id: str, owner_id: str, permission: str = "read", expires_at: Optional[str] = None) -> str:
        share_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO collection_shares (id, collection_id, owner_id, permission, created_at, expires_at, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (share_id, collection_id, owner_id, permission, timestamp, expires_at)
            )
            conn.commit()
        return share_id

    def get_share(self, share_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM collection_shares WHERE id = ?", (share_id,)).fetchone()
            if row:
                share = dict(row)
                # Check expiry
                if share.get("expires_at"):
                    try:
                        if datetime.fromisoformat(share["expires_at"]) < datetime.utcnow():
                            share["is_active"] = 0
                    except ValueError:
                        pass
                return share
            return None

    def get_shares_for_collection(self, collection_id: str) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM collection_shares WHERE collection_id = ? AND is_active = 1 ORDER BY created_at DESC",
                (collection_id,)
            ).fetchall()
            shares = []
            for row in rows:
                share = dict(row)
                # Get accepted users
                users = conn.execute(
                    "SELECT user_id, accepted_at FROM collection_share_users WHERE share_id = ?",
                    (share["id"],)
                ).fetchall()
                share["accepted_users"] = [{"user_id": u[0], "accepted_at": u[1]} for u in users]
                shares.append(share)
            return shares

    def accept_share(self, share_id: str, user_id: str):
        timestamp = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO collection_share_users (share_id, user_id, accepted_at) VALUES (?, ?, ?)",
                (share_id, user_id, timestamp)
            )
            conn.commit()

    def get_shared_collections(self, user_id: str) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT c.*, cs.permission, cs.id as share_id
                FROM collections c
                JOIN collection_shares cs ON cs.collection_id = c.id
                JOIN collection_share_users csu ON csu.share_id = cs.id
                WHERE csu.user_id = ? AND cs.is_active = 1
                ORDER BY c.created_at ASC
                """,
                (user_id,)
            ).fetchall()
            results = []
            for row in rows:
                coll = dict(row)
                coll['document_count'] = _get_document_count_from_metadata(coll['id'])
                coll['shared'] = True
                results.append(coll)
            return results

    def revoke_share(self, share_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE collection_shares SET is_active = 0 WHERE id = ?", (share_id,))
            conn.commit()

    def check_share_access(self, collection_id: str, user_id: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT cs.permission FROM collection_shares cs
                JOIN collection_share_users csu ON csu.share_id = cs.id
                WHERE cs.collection_id = ? AND csu.user_id = ? AND cs.is_active = 1
                ORDER BY CASE cs.permission WHEN 'readwrite' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (collection_id, user_id)
            ).fetchone()
            if row:
                # Check expiry
                share = conn.execute(
                    """SELECT cs.expires_at FROM collection_shares cs
                       JOIN collection_share_users csu ON csu.share_id = cs.id
                       WHERE cs.collection_id = ? AND csu.user_id = ? AND cs.is_active = 1
                       ORDER BY CASE cs.permission WHEN 'readwrite' THEN 0 ELSE 1 END LIMIT 1""",
                    (collection_id, user_id)
                ).fetchone()
                if share and share[0]:
                    try:
                        if datetime.fromisoformat(share[0]) < datetime.utcnow():
                            return None
                    except ValueError:
                        pass
                return row[0]
            return None

    # ── Upload Jobs ──────────────────────────────────────────

    def create_upload_job(self, collection_id: str, total_files: int, job_type: str = "upload") -> int:
        timestamp = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO upload_jobs (collection_id, status, total_files, started_at, job_type) VALUES (?, ?, ?, ?, ?)",
                (collection_id, "pending", total_files, timestamp, job_type)
            )
            conn.commit()
            return cursor.lastrowid

    def update_upload_job(self, job_id: int, **kwargs):
        updates = []
        params = []
        field_map = {
            "status": "status", "processed_files": "processed_files", "current_file": "current_file",
            "error": "error", "result_summary": "result_summary", "phase": "phase",
            "phase_progress": "phase_progress", "phase_detail": "phase_detail",
            "chunks_processed": "chunks_processed", "chunks_total": "chunks_total",
        }
        for kwarg, col in field_map.items():
            if kwarg in kwargs and kwargs[kwarg] is not None:
                updates.append(f"{col} = ?")
                params.append(kwargs[kwarg])

        status = kwargs.get("status")
        if status in ("completed", "failed", "cancelled"):
            updates.append("completed_at = ?")
            params.append(datetime.utcnow().isoformat())

        if not updates:
            return
        params.append(job_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"UPDATE upload_jobs SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

    def get_upload_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT id, collection_id, status, total_files, processed_files,
                          current_file, error, started_at, completed_at, result_summary,
                          phase, phase_progress, phase_detail, chunks_processed, chunks_total, job_type
                   FROM upload_jobs WHERE id = ?""", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_active_upload_job(self, collection_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if collection_id:
                row = conn.execute(
                    """SELECT id, collection_id, status, total_files, processed_files,
                              current_file, error, started_at, completed_at, result_summary,
                              phase, phase_progress, phase_detail, chunks_processed, chunks_total, job_type
                       FROM upload_jobs WHERE status IN ('pending', 'running') AND collection_id = ?
                       ORDER BY id DESC LIMIT 1""", (collection_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT id, collection_id, status, total_files, processed_files,
                              current_file, error, started_at, completed_at, result_summary,
                              phase, phase_progress, phase_detail, chunks_processed, chunks_total, job_type
                       FROM upload_jobs WHERE status IN ('pending', 'running')
                       ORDER BY id DESC LIMIT 1"""
                ).fetchone()
            return dict(row) if row else None

    def get_all_active_upload_jobs(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                """SELECT id, collection_id, status, total_files, processed_files,
                          current_file, error, started_at, completed_at, result_summary,
                          phase, phase_progress, phase_detail, chunks_processed, chunks_total, job_type
                   FROM upload_jobs WHERE status IN ('pending', 'running') ORDER BY id DESC"""
            ).fetchall()]

    # ── Reindex Jobs ─────────────────────────────────────────

    def create_reindex_job(self, config_snapshot: Dict[str, Any]) -> int:
        timestamp = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO reindex_jobs (status, started_at, config_snapshot) VALUES (?, ?, ?)",
                ("pending", timestamp, json.dumps(config_snapshot))
            )
            conn.commit()
            return cursor.lastrowid

    def update_reindex_job(self, job_id: int, **kwargs):
        updates = []
        params = []
        for field in ["status", "total_documents", "processed_documents", "current_file", "error"]:
            if field in kwargs and kwargs[field] is not None:
                updates.append(f"{field} = ?")
                params.append(kwargs[field])
        status = kwargs.get("status")
        if status in ("completed", "failed", "cancelled"):
            updates.append("completed_at = ?")
            params.append(datetime.utcnow().isoformat())
        if not updates:
            return
        params.append(job_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"UPDATE reindex_jobs SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

    def _reindex_row_to_dict(self, conn, query, params=None) -> Optional[Dict[str, Any]]:
        conn.row_factory = sqlite3.Row
        row = conn.execute(query, params or ()).fetchone()
        return dict(row) if row else None

    # config_snapshot is included so callers (indexing_lock, orphan recovery,
    # /api/jobs/active) can recover the collection_id without a second query.
    _REINDEX_COLS = (
        "id, status, started_at, completed_at, total_documents, "
        "processed_documents, current_file, error, config_snapshot"
    )

    def get_reindex_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            return self._reindex_row_to_dict(
                conn,
                f"SELECT {self._REINDEX_COLS} FROM reindex_jobs WHERE id = ?",
                (job_id,)
            )

    def get_latest_reindex_job(self) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            return self._reindex_row_to_dict(
                conn,
                f"SELECT {self._REINDEX_COLS} FROM reindex_jobs ORDER BY id DESC LIMIT 1"
            )

    def get_active_reindex_job(self) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            return self._reindex_row_to_dict(
                conn,
                f"SELECT {self._REINDEX_COLS} FROM reindex_jobs "
                "WHERE status IN ('pending', 'running') ORDER BY id DESC LIMIT 1"
            )

    # ── Orphan recovery ──────────────────────────────────────
    # On backend startup, sweep any rows still marked pending/running. Upload
    # threads die with the process (daemon=False keeps them only as long as
    # the interpreter lives); reindex asyncio tasks die outright. Either way,
    # the DB row would otherwise be stuck in 'running' forever.

    def mark_stale_jobs_as_orphaned(
        self, reason: str = "Orphaned: backend restarted before job completed"
    ) -> Dict[str, List[int]]:
        """Mark every still-pending/running upload + reindex job as failed.

        Returns the IDs of jobs that were cleaned up, keyed by table, so the
        caller can log a single summary line.
        """
        timestamp = datetime.utcnow().isoformat()
        cleaned: Dict[str, List[int]] = {"upload_jobs": [], "reindex_jobs": []}
        with sqlite3.connect(self.db_path) as conn:
            upload_ids = [r[0] for r in conn.execute(
                "SELECT id FROM upload_jobs WHERE status IN ('pending', 'running')"
            ).fetchall()]
            if upload_ids:
                conn.execute(
                    "UPDATE upload_jobs SET status = 'failed', error = ?, completed_at = ? "
                    "WHERE status IN ('pending', 'running')",
                    (reason, timestamp)
                )
                cleaned["upload_jobs"] = upload_ids

            reindex_ids = [r[0] for r in conn.execute(
                "SELECT id FROM reindex_jobs WHERE status IN ('pending', 'running')"
            ).fetchall()]
            if reindex_ids:
                conn.execute(
                    "UPDATE reindex_jobs SET status = 'failed', error = ?, completed_at = ? "
                    "WHERE status IN ('pending', 'running')",
                    (reason, timestamp)
                )
                cleaned["reindex_jobs"] = reindex_ids

            conn.commit()
        return cleaned

    # ── AI Preferences ───────────────────────────────────────

    def get_ai_preferences(self) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM ai_preferences WHERE id = 1").fetchone()
            if row:
                prefs = dict(row)
                if prefs.get('selected_providers'):
                    try:
                        prefs['selected_providers'] = json.loads(prefs['selected_providers'])
                    except json.JSONDecodeError:
                        prefs['selected_providers'] = []
                return prefs
            return None

    def set_ai_preferences(self, **kwargs):
        selected_providers = kwargs.get("selected_providers")
        rerank_enabled = kwargs.get("rerank_enabled")
        synthesize_enabled = kwargs.get("synthesize_enabled")
        default_provider = kwargs.get("default_provider")
        timestamp = datetime.utcnow().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM ai_preferences WHERE id = 1").fetchone()
            if row:
                updates = []
                params = []
                if selected_providers is not None:
                    updates.append("selected_providers = ?")
                    params.append(json.dumps(selected_providers))
                if rerank_enabled is not None:
                    updates.append("rerank_enabled = ?")
                    params.append(1 if rerank_enabled else 0)
                if synthesize_enabled is not None:
                    updates.append("synthesize_enabled = ?")
                    params.append(1 if synthesize_enabled else 0)
                if default_provider is not None:
                    updates.append("default_provider = ?")
                    params.append(default_provider)
                if updates:
                    updates.append("updated_at = ?")
                    params.append(timestamp)
                    params.append(1)
                    conn.execute(f"UPDATE ai_preferences SET {', '.join(updates)} WHERE id = ?", params)
            else:
                conn.execute(
                    """INSERT INTO ai_preferences (id, selected_providers, rerank_enabled, synthesize_enabled, default_provider, updated_at)
                       VALUES (1, ?, ?, ?, ?, ?)""",
                    (json.dumps(selected_providers or []), 1 if rerank_enabled else 0,
                     1 if synthesize_enabled else 0, default_provider, timestamp)
                )
            conn.commit()

    # ── Search History ───────────────────────────────────────

    def add_search_history(self, query: str, top_k: int, results_count: int, **kwargs) -> int:
        timestamp = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO search_history (query, timestamp, top_k, results_count, ai_provider, ai_used, results_json, execution_time_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (query, timestamp, top_k, results_count,
                 kwargs.get("ai_provider"), 1 if kwargs.get("ai_used") else 0,
                 kwargs.get("results_json"), kwargs.get("execution_time_ms"))
            )
            conn.commit()
            return cursor.lastrowid

    def get_search_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                """SELECT id, query, timestamp, top_k, results_count, ai_provider, ai_used, execution_time_ms
                   FROM search_history ORDER BY timestamp DESC LIMIT ?""", (limit,)
            ).fetchall()]

    def get_search_by_id(self, search_id: int) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM search_history WHERE id = ?", (search_id,)).fetchone()
            if row:
                result = dict(row)
                if result.get('results_json'):
                    try:
                        result['results'] = json.loads(result['results_json'])
                    except json.JSONDecodeError:
                        result['results'] = None
                return result
            return None

    def delete_old_search_history(self, days: int = 30):
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM search_history WHERE timestamp < ?", (cutoff,))
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    # ── User Preferences ─────────────────────────────────────

    def get_user_preference(self, key: str, default: Any = None) -> Any:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM user_preferences WHERE key = ?", (key,)).fetchone()
            if row:
                try:
                    return json.loads(row[0])
                except json.JSONDecodeError:
                    return row[0]
            return default

    def set_user_preference(self, key: str, value: Any):
        value_str = json.dumps(value) if not isinstance(value, str) else value
        timestamp = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO user_preferences (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value_str, timestamp)
            )
            conn.commit()

    def get_all_user_preferences(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            prefs = {}
            for key, value in conn.execute("SELECT key, value FROM user_preferences").fetchall():
                try:
                    prefs[key] = json.loads(value)
                except json.JSONDecodeError:
                    prefs[key] = value
            return prefs

    # ── Weekly Digest Preferences ────────────────────────────
    #
    # Per-user opt-in for the weekly advisor digest (see services.digest).
    # Stored as a JSON blob under user_preferences key
    # ``digest_prefs:<user_id>`` — same namespacing pattern used by the
    # agent API keys. Defaults (enabled=False, day=Fri, hour=16) keep the
    # feature opt-in; the advisor flips it on from Settings.

    DEFAULT_DIGEST_PREFERENCES: Dict[str, Any] = {
        "enabled": False,
        "weekday": 4,           # Mon=0, Sun=6 — default Friday
        "hour": 16,             # 24h, scheduler-local TZ (default America/New_York)
        "email_override": None, # None = use user_id (which is the email in hosted multi-user mode)
    }

    def get_digest_preferences(self, user_id: str) -> Dict[str, Any]:
        stored = self.get_user_preference(f"digest_prefs:{user_id}") or {}
        return {**self.DEFAULT_DIGEST_PREFERENCES, **stored}

    def set_digest_preferences(self, user_id: str, prefs: Dict[str, Any]) -> Dict[str, Any]:
        merged = {**self.get_digest_preferences(user_id), **prefs}
        self.set_user_preference(f"digest_prefs:{user_id}", merged)
        return merged

    def list_digest_optins(self) -> List[Dict[str, Any]]:
        """Every user with digest enabled. Returns merged-prefs dicts including user_id.

        Enumerates user_preferences directly rather than iterating the users
        table — that way prefs that exist for a user_id are honoured even if
        the auth-middleware upsert is somehow missing.
        """
        result: List[Dict[str, Any]] = []
        prefix = "digest_prefs:"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT key FROM user_preferences WHERE key LIKE ?",
                (prefix + "%",),
            ).fetchall()
        for (key,) in rows:
            user_id = key[len(prefix):]
            if not user_id:
                continue
            prefs = self.get_digest_preferences(user_id)
            if prefs.get("enabled"):
                result.append({"user_id": user_id, **prefs})
        return result

    # ── Morning Brief Preferences ────────────────────────────
    #
    # Per-user opt-in for the daily morning brief (see services.morning_brief).
    # Same storage shape as DIGEST_PREFERENCES, distinct namespace so the two
    # opt-ins don't interfere — an advisor can enable one, both, or neither.

    DEFAULT_MORNING_BRIEF_PREFERENCES: Dict[str, Any] = {
        "enabled": False,
        "hour": 7,               # 24h, scheduler-local TZ — advisors check email in their commute
        "weekdays_only": True,   # Skip Sat/Sun by default
        "email_override": None,
    }

    def get_morning_brief_preferences(self, user_id: str) -> Dict[str, Any]:
        stored = self.get_user_preference(f"morning_brief_prefs:{user_id}") or {}
        return {**self.DEFAULT_MORNING_BRIEF_PREFERENCES, **stored}

    def set_morning_brief_preferences(self, user_id: str, prefs: Dict[str, Any]) -> Dict[str, Any]:
        merged = {**self.get_morning_brief_preferences(user_id), **prefs}
        self.set_user_preference(f"morning_brief_prefs:{user_id}", merged)
        return merged

    def list_morning_brief_optins(self) -> List[Dict[str, Any]]:
        """Every user with morning-brief enabled. Same enumeration pattern as digest."""
        result: List[Dict[str, Any]] = []
        prefix = "morning_brief_prefs:"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT key FROM user_preferences WHERE key LIKE ?",
                (prefix + "%",),
            ).fetchall()
        for (key,) in rows:
            user_id = key[len(prefix):]
            if not user_id:
                continue
            prefs = self.get_morning_brief_preferences(user_id)
            if prefs.get("enabled"):
                result.append({"user_id": user_id, **prefs})
        return result

    # ── Agent API Keys ───────────────────────────────────────

    def set_agent_api_key(self, provider: str, api_key: str):
        self.set_user_preference(f"agent_api_key_{provider}", api_key)

    def get_agent_api_key(self, provider: str) -> Optional[str]:
        return self.get_user_preference(f"agent_api_key_{provider}")

    def delete_agent_api_key(self, provider: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM user_preferences WHERE key = ?", (f"agent_api_key_{provider}",))
            conn.commit()

    def get_agent_config(self) -> Dict[str, Any]:
        config = {"providers": {}}
        for provider in ["anthropic", "openai"]:
            key = self.get_agent_api_key(provider)
            if key:
                masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
                config["providers"][provider] = {"configured": True, "key_preview": masked}
            else:
                config["providers"][provider] = {"configured": False, "key_preview": None}
        return config

    # ── MCP Resources ────────────────────────────────────────

    def get_all_mcp_resources(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM mcp_resources ORDER BY created_at ASC").fetchall()]

    def get_mcp_resource(self, resource_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM mcp_resources WHERE id = ?", (resource_id,)).fetchone()
            return dict(row) if row else None

    def create_mcp_resource(self, resource_id: str, name: str, collection_id: str, repo_url: Optional[str] = None) -> Dict[str, Any]:
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO mcp_resources (id, name, collection_id, repo_url, created_at) VALUES (?, ?, ?, ?, ?)",
                (resource_id, name, collection_id, repo_url, now)
            )
            conn.commit()
        return {"id": resource_id, "name": name, "collection_id": collection_id, "repo_url": repo_url, "created_at": now}

    def update_mcp_resource(self, resource_id: str, name: Optional[str] = None, collection_id: Optional[str] = None, repo_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
        resource = self.get_mcp_resource(resource_id)
        if not resource:
            return None
        updated = {
            "name": name if name is not None else resource["name"],
            "collection_id": collection_id if collection_id is not None else resource["collection_id"],
            "repo_url": repo_url if repo_url is not None else resource.get("repo_url"),
        }
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE mcp_resources SET name = ?, collection_id = ?, repo_url = ? WHERE id = ?",
                (updated["name"], updated["collection_id"], updated["repo_url"], resource_id)
            )
            conn.commit()
        return {**resource, **updated}

    def delete_mcp_resource(self, resource_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM mcp_resources WHERE id = ?", (resource_id,))
            conn.commit()
            return cursor.rowcount > 0


def create_app_db() -> DatabaseBackend:
    """Factory: create the appropriate database backend based on configuration."""
    if settings.db_backend == "postgresql":
        from services.db_postgres import PostgresBackend
        return PostgresBackend(settings.postgres_url)
    return SQLiteBackend(Path(settings.data_dir) / "app.db")


# Global instance — import this everywhere
app_db = create_app_db()

# Backward compatibility alias
AppDatabase = SQLiteBackend
