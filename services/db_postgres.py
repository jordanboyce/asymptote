"""PostgreSQL database backend for multi-user / enterprise deployments.

Drop-in replacement for SQLiteBackend when settings.db_backend == "postgresql".
Requires psycopg2-binary.
"""

import json
import logging
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from config import settings
from services.db_backend import DatabaseBackend

logger = logging.getLogger(__name__)

# Reuse the same helper for document counts (reads per-collection SQLite metadata.db)
from services.app_database import _get_document_count_from_metadata


class PostgresBackend(DatabaseBackend):
    """Application database backed by PostgreSQL."""

    def __init__(self, dsn: str, min_conn: int = 2, max_conn: int = 10):
        self.dsn = dsn
        self.pool = ThreadedConnectionPool(min_conn, max_conn, dsn)
        self._init_db()

    def _conn(self):
        return self.pool.getconn()

    def _put(self, conn):
        self.pool.putconn(conn)

    # ── Schema ───────────────────────────────────────────────

    def _init_db(self):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS config (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS reindex_jobs (
                        id SERIAL PRIMARY KEY,
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
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ai_preferences (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        selected_providers TEXT,
                        rerank_enabled INTEGER DEFAULT 1,
                        synthesize_enabled INTEGER DEFAULT 1,
                        default_provider TEXT,
                        updated_at TEXT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS search_history (
                        id SERIAL PRIMARY KEY,
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
                cur.execute("CREATE INDEX IF NOT EXISTS idx_search_timestamp ON search_history(timestamp DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_search_query ON search_history(query)")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_preferences (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        display_name TEXT,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL
                    )
                """)
                cur.execute("""
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
                cur.execute("CREATE INDEX IF NOT EXISTS idx_collections_owner ON collections(owner_id)")
                # v4.3: per-collection guide (markdown, surfaced to calling LLM)
                try:
                    cur.execute("ALTER TABLE collections ADD COLUMN IF NOT EXISTS guide TEXT")
                except Exception:
                    pass
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS collection_documents (
                        collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                        document_id TEXT NOT NULL,
                        added_at TEXT NOT NULL,
                        PRIMARY KEY (collection_id, document_id)
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_collection_documents ON collection_documents(collection_id)")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS upload_jobs (
                        id SERIAL PRIMARY KEY,
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
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS mcp_resources (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        collection_id TEXT NOT NULL,
                        repo_url TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS collection_shares (
                        id TEXT PRIMARY KEY,
                        collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                        owner_id TEXT NOT NULL,
                        permission TEXT NOT NULL DEFAULT 'read',
                        created_at TEXT NOT NULL,
                        expires_at TEXT,
                        is_active INTEGER DEFAULT 1
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_shares_collection ON collection_shares(collection_id)")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS collection_share_users (
                        share_id TEXT NOT NULL REFERENCES collection_shares(id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL,
                        accepted_at TEXT NOT NULL,
                        PRIMARY KEY (share_id, user_id)
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_share_users_user ON collection_share_users(user_id)")

                # Ensure default user
                cur.execute("SELECT id FROM users WHERE id = 'default'")
                if not cur.fetchone():
                    ts = datetime.utcnow().isoformat()
                    cur.execute("INSERT INTO users (id, display_name, first_seen_at, last_seen_at) VALUES (%s, %s, %s, %s)",
                                ("default", "Default User", ts, ts))

                # Ensure default collection
                cur.execute("SELECT id FROM collections WHERE id = 'default'")
                if not cur.fetchone():
                    ts = datetime.utcnow().isoformat()
                    cur.execute(
                        """INSERT INTO collections (id, name, description, color, embedding_model, owner_id, created_at, updated_at)
                           VALUES ('default', 'Default', 'Default document collection', '#3b82f6', %s, 'default', %s, %s)""",
                        (settings.embedding_model, ts, ts)
                    )

            conn.commit()
            logger.info("PostgreSQL database initialized")
        finally:
            self._put(conn)

    def _fetchone_dict(self, cur) -> Optional[Dict[str, Any]]:
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def _fetchall_dict(self, cur) -> List[Dict[str, Any]]:
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    # ── Configuration ────────────────────────────────────────

    def get_config(self, key: str, default: Any = None) -> Any:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM config WHERE key = %s", (key,))
                row = cur.fetchone()
                if row:
                    try:
                        return json.loads(row[0])
                    except json.JSONDecodeError:
                        return row[0]
                return default
        finally:
            self._put(conn)

    def set_config(self, key: str, value: Any):
        value_str = json.dumps(value) if not isinstance(value, str) else value
        ts = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO config (key, value, updated_at) VALUES (%s, %s, %s)
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
                    (key, value_str, ts)
                )
            conn.commit()
        finally:
            self._put(conn)

    def get_all_config(self) -> Dict[str, Any]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value FROM config")
                config = {}
                for key, value in cur.fetchall():
                    try:
                        config[key] = json.loads(value)
                    except json.JSONDecodeError:
                        config[key] = value
                return config
        finally:
            self._put(conn)

    def delete_config(self, key: str):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM config WHERE key = %s", (key,))
            conn.commit()
        finally:
            self._put(conn)

    # ── Users ────────────────────────────────────────────────

    def upsert_user(self, user_id: str, display_name: Optional[str] = None) -> Dict[str, Any]:
        ts = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, display_name, first_seen_at FROM users WHERE id = %s", (user_id,))
                existing = cur.fetchone()
                if existing:
                    dname = display_name or existing[1]
                    cur.execute("UPDATE users SET display_name = %s, last_seen_at = %s WHERE id = %s", (dname, ts, user_id))
                    conn.commit()
                    return {"id": user_id, "display_name": dname, "first_seen_at": existing[2], "last_seen_at": ts}
                else:
                    dname = display_name or user_id
                    cur.execute("INSERT INTO users (id, display_name, first_seen_at, last_seen_at) VALUES (%s, %s, %s, %s)",
                                (user_id, dname, ts, ts))
                    conn.commit()
                    return {"id": user_id, "display_name": dname, "first_seen_at": ts, "last_seen_at": ts}
        finally:
            self._put(conn)

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                return self._fetchone_dict(cur)
        finally:
            self._put(conn)

    def get_all_users(self) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users ORDER BY first_seen_at ASC")
                return self._fetchall_dict(cur)
        finally:
            self._put(conn)

    # ── Collections ──────────────────────────────────────────

    def create_collection(self, name: str, description: str = "", color: str = "#3b82f6",
                          chunk_size: int = 500, chunk_overlap: int = 50,
                          embedding_model: str = None, owner_id: str = "default") -> str:
        if embedding_model is None:
            embedding_model = settings.embedding_model
        cid = str(uuid.uuid4())[:8]
        ts = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO collections (id, name, description, color, chunk_size, chunk_overlap, embedding_model, owner_id, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (cid, name, description, color, chunk_size, chunk_overlap, embedding_model, owner_id, ts, ts)
                )
            conn.commit()
            return cid
        finally:
            self._put(conn)

    def get_collection(self, collection_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM collections WHERE id = %s", (collection_id,))
                coll = self._fetchone_dict(cur)
                if coll:
                    coll['document_count'] = _get_document_count_from_metadata(collection_id)
                return coll
        finally:
            self._put(conn)

    def get_all_collections(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                if owner_id:
                    cur.execute("SELECT * FROM collections WHERE owner_id = %s ORDER BY created_at ASC", (owner_id,))
                else:
                    cur.execute("SELECT * FROM collections ORDER BY created_at ASC")
                colls = self._fetchall_dict(cur)
                for c in colls:
                    c['document_count'] = _get_document_count_from_metadata(c['id'])
                return colls
        finally:
            self._put(conn)

    def update_collection(self, collection_id: str, name=None, description=None, color=None,
                          chunk_size=None, chunk_overlap=None, embedding_model=None,
                          guide=None):
        updates, params = [], []
        for field, val in [("name", name), ("description", description), ("color", color),
                           ("chunk_size", chunk_size), ("chunk_overlap", chunk_overlap),
                           ("embedding_model", embedding_model),
                           ("guide", guide)]:
            if val is not None:
                updates.append(f"{field} = %s")
                params.append(val)
        if not updates:
            return
        updates.append("updated_at = %s")
        params.append(datetime.utcnow().isoformat())
        params.append(collection_id)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE collections SET {', '.join(updates)} WHERE id = %s", params)
            conn.commit()
        finally:
            self._put(conn)

    def delete_collection(self, collection_id: str) -> bool:
        if collection_id == "default":
            return False
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM collections WHERE id = %s", (collection_id,))
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        finally:
            self._put(conn)

    # ── Collection Documents ─────────────────────────────────

    def add_document_to_collection(self, collection_id: str, document_id: str):
        ts = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO collection_documents (collection_id, document_id, added_at) VALUES (%s, %s, %s)
                       ON CONFLICT (collection_id, document_id) DO UPDATE SET added_at = EXCLUDED.added_at""",
                    (collection_id, document_id, ts)
                )
            conn.commit()
        finally:
            self._put(conn)

    def remove_document_from_collection(self, collection_id: str, document_id: str):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM collection_documents WHERE collection_id = %s AND document_id = %s",
                            (collection_id, document_id))
            conn.commit()
        finally:
            self._put(conn)

    def get_collection_documents(self, collection_id: str) -> List[str]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT document_id FROM collection_documents WHERE collection_id = %s", (collection_id,))
                return [r[0] for r in cur.fetchall()]
        finally:
            self._put(conn)

    def get_document_collections(self, document_id: str) -> List[str]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT collection_id FROM collection_documents WHERE document_id = %s", (document_id,))
                return [r[0] for r in cur.fetchall()]
        finally:
            self._put(conn)

    # ── Collection Sharing ───────────────────────────────────

    def create_share(self, collection_id: str, owner_id: str, permission: str = "read", expires_at: Optional[str] = None) -> str:
        sid = str(uuid.uuid4())
        ts = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO collection_shares (id, collection_id, owner_id, permission, created_at, expires_at, is_active)
                       VALUES (%s, %s, %s, %s, %s, %s, 1)""",
                    (sid, collection_id, owner_id, permission, ts, expires_at)
                )
            conn.commit()
            return sid
        finally:
            self._put(conn)

    def get_share(self, share_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM collection_shares WHERE id = %s", (share_id,))
                share = self._fetchone_dict(cur)
                if share and share.get("expires_at"):
                    try:
                        if datetime.fromisoformat(share["expires_at"]) < datetime.utcnow():
                            share["is_active"] = 0
                    except ValueError:
                        pass
                return share
        finally:
            self._put(conn)

    def get_shares_for_collection(self, collection_id: str) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM collection_shares WHERE collection_id = %s AND is_active = 1 ORDER BY created_at DESC",
                            (collection_id,))
                shares = self._fetchall_dict(cur)
                for s in shares:
                    cur.execute("SELECT user_id, accepted_at FROM collection_share_users WHERE share_id = %s", (s["id"],))
                    s["accepted_users"] = [{"user_id": r[0], "accepted_at": r[1]} for r in cur.fetchall()]
                return shares
        finally:
            self._put(conn)

    def accept_share(self, share_id: str, user_id: str):
        ts = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO collection_share_users (share_id, user_id, accepted_at) VALUES (%s, %s, %s)
                       ON CONFLICT (share_id, user_id) DO UPDATE SET accepted_at = EXCLUDED.accepted_at""",
                    (share_id, user_id, ts)
                )
            conn.commit()
        finally:
            self._put(conn)

    def get_shared_collections(self, user_id: str) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT c.*, cs.permission, cs.id as share_id
                       FROM collections c
                       JOIN collection_shares cs ON cs.collection_id = c.id
                       JOIN collection_share_users csu ON csu.share_id = cs.id
                       WHERE csu.user_id = %s AND cs.is_active = 1
                       ORDER BY c.created_at ASC""",
                    (user_id,)
                )
                results = self._fetchall_dict(cur)
                for c in results:
                    c['document_count'] = _get_document_count_from_metadata(c['id'])
                    c['shared'] = True
                return results
        finally:
            self._put(conn)

    def revoke_share(self, share_id: str):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE collection_shares SET is_active = 0 WHERE id = %s", (share_id,))
            conn.commit()
        finally:
            self._put(conn)

    def check_share_access(self, collection_id: str, user_id: str) -> Optional[str]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT cs.permission, cs.expires_at FROM collection_shares cs
                       JOIN collection_share_users csu ON csu.share_id = cs.id
                       WHERE cs.collection_id = %s AND csu.user_id = %s AND cs.is_active = 1
                       ORDER BY CASE cs.permission WHEN 'readwrite' THEN 0 ELSE 1 END LIMIT 1""",
                    (collection_id, user_id)
                )
                row = cur.fetchone()
                if row:
                    if row[1]:
                        try:
                            if datetime.fromisoformat(row[1]) < datetime.utcnow():
                                return None
                        except ValueError:
                            pass
                    return row[0]
                return None
        finally:
            self._put(conn)

    # ── Upload Jobs ──────────────────────────────────────────

    def create_upload_job(self, collection_id: str, total_files: int, job_type: str = "upload") -> int:
        ts = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO upload_jobs (collection_id, status, total_files, started_at, job_type) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (collection_id, "pending", total_files, ts, job_type)
                )
                job_id = cur.fetchone()[0]
            conn.commit()
            return job_id
        finally:
            self._put(conn)

    def update_upload_job(self, job_id: int, **kwargs):
        updates, params = [], []
        for field in ["status", "processed_files", "current_file", "error", "result_summary",
                       "phase", "phase_progress", "phase_detail", "chunks_processed", "chunks_total"]:
            if field in kwargs and kwargs[field] is not None:
                updates.append(f"{field} = %s")
                params.append(kwargs[field])
        if kwargs.get("status") in ("completed", "failed", "cancelled"):
            updates.append("completed_at = %s")
            params.append(datetime.utcnow().isoformat())
        if not updates:
            return
        params.append(job_id)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE upload_jobs SET {', '.join(updates)} WHERE id = %s", params)
            conn.commit()
        finally:
            self._put(conn)

    def _upload_job_query(self):
        return """SELECT id, collection_id, status, total_files, processed_files,
                         current_file, error, started_at, completed_at, result_summary,
                         phase, phase_progress, phase_detail, chunks_processed, chunks_total, job_type
                  FROM upload_jobs"""

    def get_upload_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"{self._upload_job_query()} WHERE id = %s", (job_id,))
                return self._fetchone_dict(cur)
        finally:
            self._put(conn)

    def get_active_upload_job(self, collection_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                if collection_id:
                    cur.execute(f"{self._upload_job_query()} WHERE status IN ('pending','running') AND collection_id = %s ORDER BY id DESC LIMIT 1",
                                (collection_id,))
                else:
                    cur.execute(f"{self._upload_job_query()} WHERE status IN ('pending','running') ORDER BY id DESC LIMIT 1")
                return self._fetchone_dict(cur)
        finally:
            self._put(conn)

    def get_all_active_upload_jobs(self) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"{self._upload_job_query()} WHERE status IN ('pending','running') ORDER BY id DESC")
                return self._fetchall_dict(cur)
        finally:
            self._put(conn)

    # ── Reindex Jobs ─────────────────────────────────────────

    def create_reindex_job(self, config_snapshot: Dict[str, Any]) -> int:
        ts = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO reindex_jobs (status, started_at, config_snapshot) VALUES (%s, %s, %s) RETURNING id",
                            ("pending", ts, json.dumps(config_snapshot)))
                job_id = cur.fetchone()[0]
            conn.commit()
            return job_id
        finally:
            self._put(conn)

    def update_reindex_job(self, job_id: int, **kwargs):
        updates, params = [], []
        for field in ["status", "total_documents", "processed_documents", "current_file", "error"]:
            if field in kwargs and kwargs[field] is not None:
                updates.append(f"{field} = %s")
                params.append(kwargs[field])
        if kwargs.get("status") in ("completed", "failed", "cancelled"):
            updates.append("completed_at = %s")
            params.append(datetime.utcnow().isoformat())
        if not updates:
            return
        params.append(job_id)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE reindex_jobs SET {', '.join(updates)} WHERE id = %s", params)
            conn.commit()
        finally:
            self._put(conn)

    def _reindex_query(self):
        # config_snapshot included so callers can recover collection_id.
        return (
            "SELECT id, status, started_at, completed_at, total_documents, "
            "processed_documents, current_file, error, config_snapshot "
            "FROM reindex_jobs"
        )

    def get_reindex_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"{self._reindex_query()} WHERE id = %s", (job_id,))
                return self._fetchone_dict(cur)
        finally:
            self._put(conn)

    def get_latest_reindex_job(self) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"{self._reindex_query()} ORDER BY id DESC LIMIT 1")
                return self._fetchone_dict(cur)
        finally:
            self._put(conn)

    def get_active_reindex_job(self) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"{self._reindex_query()} WHERE status IN ('pending','running') ORDER BY id DESC LIMIT 1")
                return self._fetchone_dict(cur)
        finally:
            self._put(conn)

    def mark_stale_jobs_as_orphaned(
        self, reason: str = "Orphaned: backend restarted before job completed"
    ) -> Dict[str, List[int]]:
        """Sweep pending/running rows in upload_jobs + reindex_jobs at startup."""
        timestamp = datetime.utcnow().isoformat()
        cleaned: Dict[str, List[int]] = {"upload_jobs": [], "reindex_jobs": []}
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE upload_jobs SET status = 'failed', error = %s, completed_at = %s "
                    "WHERE status IN ('pending', 'running') RETURNING id",
                    (reason, timestamp),
                )
                cleaned["upload_jobs"] = [r[0] for r in cur.fetchall()]

                cur.execute(
                    "UPDATE reindex_jobs SET status = 'failed', error = %s, completed_at = %s "
                    "WHERE status IN ('pending', 'running') RETURNING id",
                    (reason, timestamp),
                )
                cleaned["reindex_jobs"] = [r[0] for r in cur.fetchall()]
            conn.commit()
        finally:
            self._put(conn)
        return cleaned

    # ── AI Preferences ───────────────────────────────────────

    def get_ai_preferences(self) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM ai_preferences WHERE id = 1")
                prefs = self._fetchone_dict(cur)
                if prefs and prefs.get('selected_providers'):
                    try:
                        prefs['selected_providers'] = json.loads(prefs['selected_providers'])
                    except json.JSONDecodeError:
                        prefs['selected_providers'] = []
                return prefs
        finally:
            self._put(conn)

    def set_ai_preferences(self, **kwargs):
        ts = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM ai_preferences WHERE id = 1")
                if cur.fetchone():
                    updates, params = [], []
                    if kwargs.get("selected_providers") is not None:
                        updates.append("selected_providers = %s")
                        params.append(json.dumps(kwargs["selected_providers"]))
                    if kwargs.get("rerank_enabled") is not None:
                        updates.append("rerank_enabled = %s")
                        params.append(1 if kwargs["rerank_enabled"] else 0)
                    if kwargs.get("synthesize_enabled") is not None:
                        updates.append("synthesize_enabled = %s")
                        params.append(1 if kwargs["synthesize_enabled"] else 0)
                    if kwargs.get("default_provider") is not None:
                        updates.append("default_provider = %s")
                        params.append(kwargs["default_provider"])
                    if updates:
                        updates.append("updated_at = %s")
                        params.append(ts)
                        params.append(1)
                        cur.execute(f"UPDATE ai_preferences SET {', '.join(updates)} WHERE id = %s", params)
                else:
                    cur.execute(
                        """INSERT INTO ai_preferences (id, selected_providers, rerank_enabled, synthesize_enabled, default_provider, updated_at)
                           VALUES (1, %s, %s, %s, %s, %s)""",
                        (json.dumps(kwargs.get("selected_providers") or []),
                         1 if kwargs.get("rerank_enabled") else 0,
                         1 if kwargs.get("synthesize_enabled") else 0,
                         kwargs.get("default_provider"), ts)
                    )
            conn.commit()
        finally:
            self._put(conn)

    # ── Search History ───────────────────────────────────────

    def add_search_history(self, query: str, top_k: int, results_count: int, **kwargs) -> int:
        ts = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO search_history (query, timestamp, top_k, results_count, ai_provider, ai_used, results_json, execution_time_ms)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (query, ts, top_k, results_count,
                     kwargs.get("ai_provider"), 1 if kwargs.get("ai_used") else 0,
                     kwargs.get("results_json"), kwargs.get("execution_time_ms"))
                )
                sid = cur.fetchone()[0]
            conn.commit()
            return sid
        finally:
            self._put(conn)

    def get_search_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, query, timestamp, top_k, results_count, ai_provider, ai_used, execution_time_ms
                       FROM search_history ORDER BY timestamp DESC LIMIT %s""", (limit,))
                return self._fetchall_dict(cur)
        finally:
            self._put(conn)

    def get_search_by_id(self, search_id: int) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM search_history WHERE id = %s", (search_id,))
                result = self._fetchone_dict(cur)
                if result and result.get('results_json'):
                    try:
                        result['results'] = json.loads(result['results_json'])
                    except json.JSONDecodeError:
                        result['results'] = None
                return result
        finally:
            self._put(conn)

    def delete_old_search_history(self, days: int = 30):
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM search_history WHERE timestamp < %s", (cutoff,))
                deleted = cur.rowcount
            conn.commit()
            return deleted
        finally:
            self._put(conn)

    # ── User Preferences ─────────────────────────────────────

    def get_user_preference(self, key: str, default: Any = None) -> Any:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM user_preferences WHERE key = %s", (key,))
                row = cur.fetchone()
                if row:
                    try:
                        return json.loads(row[0])
                    except json.JSONDecodeError:
                        return row[0]
                return default
        finally:
            self._put(conn)

    def set_user_preference(self, key: str, value: Any):
        value_str = json.dumps(value) if not isinstance(value, str) else value
        ts = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO user_preferences (key, value, updated_at) VALUES (%s, %s, %s)
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
                    (key, value_str, ts)
                )
            conn.commit()
        finally:
            self._put(conn)

    def get_all_user_preferences(self) -> Dict[str, Any]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value FROM user_preferences")
                prefs = {}
                for key, value in cur.fetchall():
                    try:
                        prefs[key] = json.loads(value)
                    except json.JSONDecodeError:
                        prefs[key] = value
                return prefs
        finally:
            self._put(conn)

    # ── Agent API Keys ───────────────────────────────────────

    def set_agent_api_key(self, provider: str, api_key: str):
        self.set_user_preference(f"agent_api_key_{provider}", api_key)

    def get_agent_api_key(self, provider: str) -> Optional[str]:
        return self.get_user_preference(f"agent_api_key_{provider}")

    def delete_agent_api_key(self, provider: str):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_preferences WHERE key = %s", (f"agent_api_key_{provider}",))
            conn.commit()
        finally:
            self._put(conn)

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
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM mcp_resources ORDER BY created_at ASC")
                return self._fetchall_dict(cur)
        finally:
            self._put(conn)

    def get_mcp_resource(self, resource_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM mcp_resources WHERE id = %s", (resource_id,))
                return self._fetchone_dict(cur)
        finally:
            self._put(conn)

    def create_mcp_resource(self, resource_id: str, name: str, collection_id: str, repo_url: Optional[str] = None) -> Dict[str, Any]:
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO mcp_resources (id, name, collection_id, repo_url, created_at) VALUES (%s, %s, %s, %s, %s)",
                            (resource_id, name, collection_id, repo_url, now))
            conn.commit()
            return {"id": resource_id, "name": name, "collection_id": collection_id, "repo_url": repo_url, "created_at": now}
        finally:
            self._put(conn)

    def update_mcp_resource(self, resource_id: str, name=None, collection_id=None, repo_url=None) -> Optional[Dict[str, Any]]:
        resource = self.get_mcp_resource(resource_id)
        if not resource:
            return None
        updated = {
            "name": name if name is not None else resource["name"],
            "collection_id": collection_id if collection_id is not None else resource["collection_id"],
            "repo_url": repo_url if repo_url is not None else resource.get("repo_url"),
        }
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE mcp_resources SET name = %s, collection_id = %s, repo_url = %s WHERE id = %s",
                            (updated["name"], updated["collection_id"], updated["repo_url"], resource_id))
            conn.commit()
            return {**resource, **updated}
        finally:
            self._put(conn)

    def delete_mcp_resource(self, resource_id: str) -> bool:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM mcp_resources WHERE id = %s", (resource_id,))
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        finally:
            self._put(conn)
