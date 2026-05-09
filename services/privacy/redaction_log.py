"""Audit log for PII redaction events.

Every redaction writes a structured entry to a local SQLite table so users
can review exactly what was redacted. The original PII text is stored
locally only and never transmitted.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings
from services.privacy.redaction_engine import RedactionDetail

logger = logging.getLogger(__name__)


class RedactionLog:
    """SQLite-backed audit log for redaction events."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = settings.data_dir / "redaction_log.db"
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS redaction_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME NOT NULL,
                    session_id TEXT NOT NULL,
                    collection_id TEXT,
                    tool_name TEXT,
                    document_id TEXT,
                    entity_type TEXT NOT NULL,
                    original_text TEXT NOT NULL,
                    replacement TEXT NOT NULL,
                    start_char INTEGER NOT NULL,
                    end_char INTEGER NOT NULL,
                    score REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_redaction_session
                ON redaction_log(session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_redaction_collection
                ON redaction_log(collection_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_redaction_timestamp
                ON redaction_log(timestamp)
            """)

    def log_redactions(
        self,
        details: list[RedactionDetail],
        session_id: str,
        collection_id: str | None = None,
        tool_name: str | None = None,
        document_id: str | None = None,
    ) -> None:
        """Write redaction events to the audit log."""
        if not details:
            return

        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                now,
                session_id,
                collection_id,
                tool_name,
                document_id,
                d.entity_type,
                d.original_text,
                d.replacement,
                d.start,
                d.end,
                d.score,
            )
            for d in details
        ]

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany(
                    """
                    INSERT INTO redaction_log
                        (timestamp, session_id, collection_id, tool_name,
                         document_id, entity_type, original_text, replacement,
                         start_char, end_char, score)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        except Exception as e:
            logger.error(f"Failed to write redaction log: {e}")

    def get_recent(
        self,
        session_id: str | None = None,
        collection_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query recent redaction events."""
        conditions: list[str] = []
        params: list[Any] = []

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if collection_id:
            conditions.append("collection_id = ?")
            params.append(collection_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        capped = max(1, min(limit, 500))

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    f"""
                    SELECT id, timestamp, session_id, collection_id, tool_name,
                           document_id, entity_type, original_text, replacement,
                           start_char, end_char, score
                    FROM redaction_log
                    {where}
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    params + [capped],
                )
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to read redaction log: {e}")
            return []

    def summarize(
        self,
        collection_id: str | None = None,
        session_id: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        """Summarize redactions filtered by collection / session / start time.

        Used by the HTTP /api/redactions/summary endpoint to power the
        "PII redaction active" panel and the per-Note-of-Record redaction
        footer. Original PII text is never read or returned — counts only.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if collection_id:
            conditions.append("collection_id = ?")
            params.append(collection_id)
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row

                cursor = conn.execute(
                    f"""
                    SELECT entity_type, COUNT(*) AS count
                    FROM redaction_log
                    {where}
                    GROUP BY entity_type
                    ORDER BY count DESC
                    """,
                    params,
                )
                by_type = {row["entity_type"]: row["count"] for row in cursor.fetchall()}

                cursor = conn.execute(
                    f"""
                    SELECT tool_name, COUNT(*) AS count
                    FROM redaction_log
                    {where}
                    GROUP BY tool_name
                    ORDER BY count DESC
                    """,
                    params,
                )
                by_tool = {
                    (row["tool_name"] or "unknown"): row["count"]
                    for row in cursor.fetchall()
                }

                total = sum(by_type.values())

                return {
                    "collection_id": collection_id,
                    "session_id": session_id,
                    "since": since,
                    "total_redactions": total,
                    "by_entity_type": by_type,
                    "by_tool": by_tool,
                }
        except Exception as e:
            logger.error(f"Failed to summarize redaction log: {e}")
            return {
                "collection_id": collection_id,
                "session_id": session_id,
                "since": since,
                "total_redactions": 0,
                "by_entity_type": {},
                "by_tool": {},
            }

    def get_session_summary(
        self, session_id: str
    ) -> dict[str, Any]:
        """Summarize redactions for a session."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row

                # Count by entity type
                cursor = conn.execute(
                    """
                    SELECT entity_type, COUNT(*) as count
                    FROM redaction_log
                    WHERE session_id = ?
                    GROUP BY entity_type
                    ORDER BY count DESC
                    """,
                    (session_id,),
                )
                by_type = {row["entity_type"]: row["count"] for row in cursor.fetchall()}

                # Count by tool
                cursor = conn.execute(
                    """
                    SELECT tool_name, COUNT(*) as count
                    FROM redaction_log
                    WHERE session_id = ?
                    GROUP BY tool_name
                    ORDER BY count DESC
                    """,
                    (session_id,),
                )
                by_tool = {
                    (row["tool_name"] or "unknown"): row["count"]
                    for row in cursor.fetchall()
                }

                total = sum(by_type.values())

                return {
                    "session_id": session_id,
                    "total_redactions": total,
                    "by_entity_type": by_type,
                    "by_tool": by_tool,
                }
        except Exception as e:
            logger.error(f"Failed to summarize redaction log: {e}")
            return {
                "session_id": session_id,
                "total_redactions": 0,
                "by_entity_type": {},
                "by_tool": {},
            }

    @staticmethod
    def new_session_id() -> str:
        return str(uuid.uuid4())


# Module-level singleton
redaction_log = RedactionLog()
