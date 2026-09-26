"""Semantic answer cache for chat.

Repeated questions are the normal case for a team FAQ appliance: many people
asking near-identical things of a mostly-static corpus. Generating each answer
costs provider tokens; this cache returns the stored answer instead when a new
question is a matching candidate. The chat layer additionally requires the
same question text (ignoring whitespace) and revalidates source permissions,
quarantine status, content hashes, and effective sensitivity before serving it.
Embeddings use the configured provider, which can be local or hosted.

Scope and safety rules:

- Only single-turn questions are cached or served. Follow-ups depend on
  conversation history; a cached answer to "what date was that?" is nonsense.
- Entries are keyed by scope (one collection, or the caller's visible set for
  scope=all) and by the embedding model that produced the vector — vectors
  from different models are not comparable.
- Every entry records a fingerprint (filename, upload timestamp, chunk count)
  of each source document the answer cited. The chat layer revalidates those
  at hit time: any source that changed or disappeared kills the entry. This is
  deliberately narrower than "anything in the collection changed" — new,
  unrelated documents don't evict existing answers, but they also mean a
  cached answer won't reflect newly added material until its own sources
  change or it is explicitly regenerated (use_cache=false).

Storage is a standalone SQLite file in the data directory — cache entries are
disposable and deleting the file is always safe.
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from config import settings
from services.sqlite_utils import sqlite_connect

logger = logging.getLogger(__name__)

# Similarity above which a stored question is considered the SAME question and
# gets replaced on store (rather than accumulating near-duplicates).
_DUPLICATE_THRESHOLD = 0.99


class AnswerCache:
    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path
        self._initialized = False

    @property
    def db_path(self) -> Path:
        # Resolved lazily so tests (and config reloads) pointing data_dir
        # elsewhere get the right location.
        return self._db_path or (Path(settings.data_dir) / "answer_cache.db")

    def _conn(self):
        if not self._initialized:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite_connect(self.db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS answer_cache (
                        id TEXT PRIMARY KEY,
                        scope_key TEXT NOT NULL,
                        embedding_model TEXT NOT NULL,
                        question TEXT NOT NULL,
                        embedding BLOB NOT NULL,
                        answer TEXT NOT NULL,
                        sources_json TEXT NOT NULL,
                        fingerprints_json TEXT NOT NULL,
                        provider TEXT,
                        created_at TEXT NOT NULL,
                        hits INTEGER DEFAULT 0,
                        last_used_at TEXT NOT NULL,
                        related_json TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_answer_cache_scope ON answer_cache (scope_key, embedding_model)"
                )
                # Related questions ride along with the answer they were
                # written for (added 2026-09); older databases gain the column.
                columns = {row[1] for row in conn.execute("PRAGMA table_info(answer_cache)")}
                if "related_json" not in columns:
                    conn.execute("ALTER TABLE answer_cache ADD COLUMN related_json TEXT")
                conn.commit()
            self._initialized = True
        return sqlite_connect(self.db_path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_blob(vec: np.ndarray) -> bytes:
        return np.asarray(vec, dtype=np.float32).tobytes()

    @staticmethod
    def _similarity(a: np.ndarray, b: np.ndarray) -> float:
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom == 0.0:
            return 0.0
        return float(np.dot(a, b) / denom)

    def find_best(
        self,
        scope_key: str,
        embedding_model: str,
        query_vec: np.ndarray,
        threshold: float,
    ) -> Optional[Dict[str, Any]]:
        """Best entry at or above the similarity threshold, or None.

        Source freshness is NOT checked here — the chat layer owns document
        metadata and calls mark_hit()/delete() after validating fingerprints.
        """
        query_vec = np.asarray(query_vec, dtype=np.float32)
        best_row = None
        # float32 cosine of a vector with itself can land at 0.9999999, so a
        # threshold of exactly 1.0 ("identical question only") gets a hair of
        # tolerance rather than never matching.
        best_sim = threshold - 1e-6
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM answer_cache WHERE scope_key = ? AND embedding_model = ?",
                (scope_key, embedding_model),
            ).fetchall()
        for row in rows:
            vec = np.frombuffer(row["embedding"], dtype=np.float32)
            if vec.shape != query_vec.shape:
                continue
            sim = self._similarity(query_vec, vec)
            if sim >= best_sim:
                best_sim = sim
                best_row = row
        if best_row is None:
            return None
        return {
            "id": best_row["id"],
            "question": best_row["question"],
            "answer": best_row["answer"],
            "sources": json.loads(best_row["sources_json"]),
            "fingerprints": json.loads(best_row["fingerprints_json"]),
            "provider": best_row["provider"],
            "created_at": best_row["created_at"],
            "similarity": round(best_sim, 4),
            "related": json.loads(best_row["related_json"]) if best_row["related_json"] else [],
        }

    def mark_hit(self, entry_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE answer_cache SET hits = hits + 1, last_used_at = ? WHERE id = ?",
                (self._now(), entry_id),
            )
            conn.commit()

    def delete(self, entry_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM answer_cache WHERE id = ?", (entry_id,))
            conn.commit()

    def store(
        self,
        scope_key: str,
        embedding_model: str,
        question: str,
        query_vec: np.ndarray,
        answer: str,
        sources: List[Dict[str, Any]],
        fingerprints: List[Dict[str, Any]],
        provider: str = "",
        related: Optional[List[str]] = None,
    ) -> str:
        """Insert an entry, replacing any near-duplicate question in the scope
        (a regenerated answer supersedes the stale one) and evicting the least
        recently used entries beyond the per-scope cap."""
        query_vec = np.asarray(query_vec, dtype=np.float32)

        existing = self.find_best(scope_key, embedding_model, query_vec, _DUPLICATE_THRESHOLD)
        if existing:
            self.delete(existing["id"])

        entry_id = str(uuid.uuid4())[:12]
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO answer_cache
                (id, scope_key, embedding_model, question, embedding, answer,
                 sources_json, fingerprints_json, provider, created_at, hits, last_used_at,
                 related_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    entry_id, scope_key, embedding_model, question,
                    self._to_blob(query_vec), answer,
                    json.dumps(sources), json.dumps(fingerprints),
                    provider, now, now,
                    json.dumps(list(related or [])),
                ),
            )
            cap = max(1, settings.answer_cache_max_per_scope)
            conn.execute(
                """
                DELETE FROM answer_cache WHERE id IN (
                    SELECT id FROM answer_cache WHERE scope_key = ?
                    ORDER BY last_used_at DESC LIMIT -1 OFFSET ?
                )
                """,
                (scope_key, cap),
            )
            conn.commit()
        return entry_id

    def clear(self, scope_key: Optional[str] = None) -> int:
        """Drop entries for one scope (every request fingerprint under it) or all."""
        with self._conn() as conn:
            if scope_key:
                # Keys are "<scope>#<request fingerprint>": clearing a scope
                # must take every fingerprint with it.
                cur = conn.execute(
                    "DELETE FROM answer_cache WHERE scope_key = ? OR scope_key LIKE ? ESCAPE '\\'",
                    (scope_key, scope_key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "#%"),
                )
            else:
                cur = conn.execute("DELETE FROM answer_cache")
            conn.commit()
            return cur.rowcount

    def stats(self) -> Dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM answer_cache"
            ).fetchone()
        return {"entries": row[0], "total_hits": row[1]}


answer_cache = AnswerCache()
