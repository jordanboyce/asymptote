"""Per-Collection chat session + message persistence.

Mirrors the per-Collection storage pattern already used by
:class:`services.metadata_store.MetadataStore`,
:class:`services.financial.holdings_store.HoldingsStore`, and
:class:`services.meeting_notes.MeetingNotesStore` — all share the same
``metadata.db`` SQLite file, each store owning its own tables.

Chat lived in browser localStorage through v4.x. That worked for solo
sessions but breaks the three things Slice B is meant to unlock:

  * multi-device continuity (advisor at desk → phone);
  * recoverability (clearing browser data shouldn't lose a client's chat);
  * cross-feature reuse (the Brief now wants to surface "recent advisor
    questions about this household" — see :func:`build_meeting_context`).

The message payload includes the structured-results envelope the chat
engine already emits (sources, citations, tool calls, aiUsage), JSON-blob
encoded so the frontend can re-hydrate sessions verbatim.

PII redaction note: the chat surface itself runs *advisor → server*, so
the messages here may contain client names/identifiers. This store does
not redact; the privacy boundary continues to live where data leaves the
box (:mod:`services.privacy.redaction_middleware` over the MCP exit).
Sessions stored here are server-side but in-process — no third-party
transit. Down the line, if we expose a sessions-export endpoint that
goes off-box, the redaction middleware should be applied to its output.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Mirrors the MAX_MESSAGES_PER_SESSION cap used in the frontend's
# chatStore.js so server- and browser-side storage agree on the budget.
# Older messages roll off when the cap is exceeded; they remain in the
# updated_at-sorted session list, just trimmed from the tail of any
# single session's message history.
DEFAULT_MAX_MESSAGES_PER_SESSION = 100


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_session_id() -> str:
    # Short, URL-safe, sortable-ish. Format mirrors the frontend's
    # `s_<base36 ts>_<random>` shape so server-generated IDs are
    # indistinguishable from client-generated ones — sessions created
    # offline on the client and replayed up later will not collide.
    return f"s_{int(time.time() * 1000):x}_{uuid.uuid4().hex[:8]}"


def _derive_title(text: Optional[str]) -> str:
    if not text:
        return "New chat"
    cleaned = " ".join(text.split())
    if len(cleaned) <= 60:
        return cleaned
    return cleaned[:57] + "…"


class ChatSessionStore:
    """SQLite-backed per-Collection chat session + message store.

    Schema:
      chat_sessions  — one row per session, keyed by string id
      chat_messages  — one row per message, foreign-keyed to chat_sessions

    Cascading delete on session removal is enforced via PRAGMA per
    connection (SQLite default is off). The store opens short-lived
    connections per operation rather than holding one open; this matches
    the surrounding stores and avoids cross-thread connection issues.
    """

    SESSIONS_TABLE = "chat_sessions"
    MESSAGES_TABLE = "chat_messages"

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        # ON DELETE CASCADE is off by default in SQLite. Without this
        # `DELETE FROM chat_sessions WHERE id=?` would leave orphan rows
        # in chat_messages — re-creating the same session id would then
        # resurrect old messages. Enabled per-connection.
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.SESSIONS_TABLE} (
                    id TEXT PRIMARY KEY,
                    collection_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT 'New chat',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.SESSIONS_TABLE}_collection "
                f"ON {self.SESSIONS_TABLE}(collection_id, updated_at DESC)"
            )
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.MESSAGES_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id)
                        REFERENCES {self.SESSIONS_TABLE}(id) ON DELETE CASCADE
                )
            """)
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.MESSAGES_TABLE}_session "
                f"ON {self.MESSAGES_TABLE}(session_id, id)"
            )
            conn.commit()

    # ─── Session CRUD ──────────────────────────────────────────────────────

    def create_session(
        self,
        *,
        collection_id: str,
        title: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Insert a new session and return its row.

        Accepts a caller-supplied ``session_id`` so the frontend can mint an
        ID locally (optimistic UI) and replay it up to the server later
        without round-tripping just to learn the ID. Idempotent on
        ``session_id``: re-calling with an existing id returns the stored
        row instead of raising — lets the frontend's "ensure session on
        server" path fire on every mutation without bookkeeping.
        """
        sid = session_id or _new_session_id()
        now = _now_iso()
        clean_title = (title or "New chat").strip() or "New chat"
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                f"SELECT id, collection_id, title, created_at, updated_at, archived "
                f"FROM {self.SESSIONS_TABLE} WHERE id = ?",
                (sid,),
            ).fetchone()
            if existing is not None:
                return _session_row_to_dict(existing)

            conn.execute(
                f"INSERT INTO {self.SESSIONS_TABLE} "
                f"(id, collection_id, title, created_at, updated_at) "
                f"VALUES (?, ?, ?, ?, ?)",
                (sid, collection_id, clean_title, now, now),
            )
            conn.commit()
        return {
            "id": sid,
            "collection_id": collection_id,
            "title": clean_title,
            "created_at": now,
            "updated_at": now,
            "archived": False,
        }

    def list_sessions(
        self,
        collection_id: str,
        *,
        include_archived: bool = False,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        clauses = ["collection_id = ?"]
        params: list[Any] = [collection_id]
        if not include_archived:
            clauses.append("archived = 0")
        sql = (
            f"SELECT id, collection_id, title, created_at, updated_at, archived "
            f"FROM {self.SESSIONS_TABLE} "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY updated_at DESC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [_session_row_to_dict(r) for r in rows]

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"SELECT id, collection_id, title, created_at, updated_at, archived "
                f"FROM {self.SESSIONS_TABLE} WHERE id = ?",
                (session_id,),
            ).fetchone()
        return _session_row_to_dict(row) if row else None

    def rename_session(self, session_id: str, title: str) -> bool:
        clean_title = _derive_title(title) or "Untitled"
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE {self.SESSIONS_TABLE} SET title = ?, updated_at = ? "
                f"WHERE id = ?",
                (clean_title, _now_iso(), session_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                f"DELETE FROM {self.SESSIONS_TABLE} WHERE id = ?",
                (session_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    # ─── Messages ──────────────────────────────────────────────────────────

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
        max_messages_per_session: int = DEFAULT_MAX_MESSAGES_PER_SESSION,
    ) -> dict[str, Any]:
        """Append a message to a session.

        Also updates the session's ``updated_at`` and, when the message is
        the first user turn in a fresh session, derives the session title
        from the user's content (matching the frontend behaviour). Older
        messages beyond ``max_messages_per_session`` are pruned to keep the
        session row under a known size — same cap the frontend uses.
        """
        now = _now_iso()
        meta_json = json.dumps(metadata) if metadata else None
        with self._connect() as conn:
            session_row = conn.execute(
                f"SELECT title FROM {self.SESSIONS_TABLE} WHERE id = ?",
                (session_id,),
            ).fetchone()
            if session_row is None:
                raise ValueError(f"Unknown session_id: {session_id}")

            cur = conn.execute(
                f"INSERT INTO {self.MESSAGES_TABLE} "
                f"(session_id, role, content, metadata, created_at) "
                f"VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, meta_json, now),
            )
            new_message_id = cur.lastrowid

            current_title = session_row[0] or "New chat"
            new_title = current_title
            if role == "user" and current_title in ("", "New chat"):
                new_title = _derive_title(content)

            # Cap message history. Drop the oldest rows past the cap so the
            # latest N survive — matches the frontend tail-slice behaviour.
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM {self.MESSAGES_TABLE} WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            total = int(count_row[0] or 0)
            if total > max_messages_per_session:
                excess = total - max_messages_per_session
                conn.execute(
                    f"DELETE FROM {self.MESSAGES_TABLE} WHERE id IN ("
                    f"  SELECT id FROM {self.MESSAGES_TABLE} "
                    f"  WHERE session_id = ? ORDER BY id ASC LIMIT ?"
                    f")",
                    (session_id, excess),
                )

            conn.execute(
                f"UPDATE {self.SESSIONS_TABLE} SET title = ?, updated_at = ? "
                f"WHERE id = ?",
                (new_title, now, session_id),
            )
            conn.commit()

        return {
            "id": new_message_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "metadata": metadata or None,
            "created_at": now,
        }

    def list_messages(
        self,
        session_id: str,
        *,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        sql = (
            f"SELECT id, session_id, role, content, metadata, created_at "
            f"FROM {self.MESSAGES_TABLE} WHERE session_id = ? ORDER BY id ASC"
        )
        params: list[Any] = [session_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [_message_row_to_dict(r) for r in rows]


def _session_row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {
        "id": row["id"],
        "collection_id": row["collection_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "archived": bool(row["archived"]),
    }


def _message_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    metadata_raw = row["metadata"]
    metadata: Optional[dict[str, Any]] = None
    if metadata_raw:
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            # The column is server-written JSON, so a decode failure means
            # a corruption — surface as None rather than crash the listing.
            logger.warning(
                "chat_messages row %s has invalid JSON metadata; returning null",
                row["id"],
            )
            metadata = None
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "metadata": metadata,
        "created_at": row["created_at"],
    }
