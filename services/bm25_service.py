"""BM25 keyword search service for hybrid search support.

Backed by SQLite's FTS5 full-text index and its built-in bm25() ranking.
Documents and queries are pre-tokenized with the same normalizer the previous
hand-rolled implementation used (lowercase, alphanumeric tokens, stopword and
short-token removal), so search semantics are unchanged while scoring and
storage are handled natively by FTS5 — far faster and smaller than the old
Python-side term-frequency tables.

Legacy databases (bm25_term_freq / bm25_doc_freq tables) are migrated in place
on first open: BM25 is bag-of-words, so each chunk is reconstructed by
repeating every stored term `term_freq` times, which preserves rankings
without needing the original text.
"""

import re
import sqlite3
from pathlib import Path
from typing import List, Dict, Tuple
import logging

logger = logging.getLogger(__name__)

# Common English stopwords (kept identical to the previous implementation so
# indexed corpora and queries keep matching the same way).
_STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
    'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on', 'that', 'the',
    'to', 'was', 'were', 'will', 'with', 'the', 'this', 'but', 'they',
    'have', 'had', 'what', 'when', 'where', 'who', 'which', 'why', 'how',
    'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
    'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so',
    'than', 'too', 'very', 'can', 'just', 'should', 'now'
}

_TOKEN_RE = re.compile(r'\b[a-z0-9]+\b')

# SQLite's default host-parameter limit is 999; stay under it for IN (...).
_IN_CLAUSE_BATCH = 900


class BM25Index:
    """
    BM25 keyword search index on SQLite FTS5.

    Note: FTS5's bm25() uses fixed k1=1.2 / b=0.75. The k1/b constructor
    arguments are retained for interface compatibility and reported by
    get_stats(), but do not affect FTS5 scoring.
    """

    def __init__(self, db_path: Path, k1: float = 1.2, b: float = 0.75):
        """
        Initialize the BM25 index.

        Args:
            db_path: Path to SQLite database for storing BM25 data
            k1: Retained for compatibility (FTS5 uses its built-in value)
            b: Retained for compatibility (FTS5 uses its built-in value)
        """
        self.db_path = db_path
        self.k1 = k1
        self.b = b
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        """Initialize FTS5 tables and migrate any legacy index in place."""
        with self._connect() as conn:
            # chunk_id <-> rowid mapping. FTS5 can't index a chunk_id column,
            # so deletes/upserts go through this table's UNIQUE index instead
            # of scanning the FTS content.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bm25_docs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id TEXT UNIQUE NOT NULL,
                    doc_length INTEGER NOT NULL
                )
            """)
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS bm25_fts
                    USING fts5(text, tokenize='unicode61')
                """)
                # Vocabulary view for stats (one row per distinct term)
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS bm25_fts_vocab
                    USING fts5vocab(bm25_fts, 'row')
                """)
            except sqlite3.OperationalError as e:
                raise RuntimeError(
                    "This SQLite build lacks FTS5, which Asymptote's keyword "
                    "search requires. Use a standard CPython build (3.9+)."
                ) from e

            migrated = self._migrate_legacy(conn)
            conn.commit()

        if migrated:
            # Reclaim the space freed by dropping the legacy tables (they were
            # the bulk of the file). VACUUM needs its own autocommit connection.
            vac = sqlite3.connect(self.db_path, isolation_level=None)
            try:
                vac.execute("VACUUM")
            finally:
                vac.close()

        logger.info(f"Initialized BM25 (FTS5) index at {self.db_path}")

    def _migrate_legacy(self, conn: sqlite3.Connection) -> bool:
        """Rebuild the FTS index from legacy term-frequency tables, then drop them.

        Returns True when legacy tables were found (and dropped).
        """
        legacy = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bm25_term_freq'"
        ).fetchone()
        if not legacy:
            return False

        already_populated = conn.execute("SELECT 1 FROM bm25_docs LIMIT 1").fetchone()
        if not already_populated:
            rows = conn.execute("""
                SELECT chunk_id, term, term_freq, doc_length
                FROM bm25_term_freq ORDER BY chunk_id
            """).fetchall()
            docs: Dict[str, List[str]] = {}
            lengths: Dict[str, int] = {}
            for chunk_id, term, freq, doc_length in rows:
                docs.setdefault(chunk_id, []).extend([term] * freq)
                lengths[chunk_id] = doc_length
            for chunk_id, terms in docs.items():
                cursor = conn.execute(
                    "INSERT INTO bm25_docs (chunk_id, doc_length) VALUES (?, ?)",
                    (chunk_id, lengths.get(chunk_id, len(terms))),
                )
                conn.execute(
                    "INSERT INTO bm25_fts (rowid, text) VALUES (?, ?)",
                    (cursor.lastrowid, " ".join(terms)),
                )
            logger.info(
                f"Migrated {len(docs)} chunks from legacy BM25 tables to FTS5"
            )

        conn.execute("DROP TABLE IF EXISTS bm25_term_freq")
        conn.execute("DROP TABLE IF EXISTS bm25_doc_freq")
        conn.execute("DROP TABLE IF EXISTS bm25_stats")
        return True

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Normalize text into index/query tokens.

        Lowercases, keeps alphanumeric tokens of length >= 2, and removes
        common English stopwords — identical to the pre-FTS5 behavior so
        existing search semantics are preserved.
        """
        tokens = _TOKEN_RE.findall(text.lower())
        return [t for t in tokens if len(t) >= 2 and t not in _STOPWORDS]

    def _delete_chunks(self, conn: sqlite3.Connection, chunk_ids: List[str]) -> int:
        """Delete chunks by id from both tables. Returns number deleted."""
        deleted = 0
        for start in range(0, len(chunk_ids), _IN_CLAUSE_BATCH):
            batch = chunk_ids[start:start + _IN_CLAUSE_BATCH]
            placeholders = ",".join("?" for _ in batch)
            rowids = [r[0] for r in conn.execute(
                f"SELECT id FROM bm25_docs WHERE chunk_id IN ({placeholders})", batch
            ).fetchall()]
            if not rowids:
                continue
            id_placeholders = ",".join("?" for _ in rowids)
            conn.execute(f"DELETE FROM bm25_fts WHERE rowid IN ({id_placeholders})", rowids)
            conn.execute(f"DELETE FROM bm25_docs WHERE id IN ({id_placeholders})", rowids)
            deleted += len(rowids)
        return deleted

    def _insert_documents(self, conn: sqlite3.Connection, documents: List[Tuple[str, str]]):
        """Tokenize and insert (chunk_id, text) pairs, replacing existing chunk_ids."""
        chunk_ids = [chunk_id for chunk_id, _ in documents]
        self._delete_chunks(conn, chunk_ids)
        for chunk_id, text in documents:
            tokens = self._tokenize(text)
            if not tokens:
                continue
            cursor = conn.execute(
                "INSERT INTO bm25_docs (chunk_id, doc_length) VALUES (?, ?)",
                (chunk_id, len(tokens)),
            )
            conn.execute(
                "INSERT INTO bm25_fts (rowid, text) VALUES (?, ?)",
                (cursor.lastrowid, " ".join(tokens)),
            )

    def add_document(self, chunk_id: str, text: str):
        """
        Add a document/chunk to the BM25 index (replaces any existing entry).

        Args:
            chunk_id: Unique identifier for the chunk
            text: Text content to index
        """
        with self._connect() as conn:
            self._insert_documents(conn, [(chunk_id, text)])
            conn.commit()

    def add_documents_batch(self, documents: List[Tuple[str, str]]):
        """
        Add multiple documents to the index in a batch.

        Args:
            documents: List of (chunk_id, text) tuples
        """
        with self._connect() as conn:
            self._insert_documents(conn, documents)
            conn.commit()
        logger.info(f"Added {len(documents)} documents to BM25 index")

    def remove_document(self, chunk_id: str):
        """
        Remove a document/chunk from the BM25 index.

        Args:
            chunk_id: Chunk identifier to remove
        """
        with self._connect() as conn:
            self._delete_chunks(conn, [chunk_id])
            conn.commit()

    def remove_documents_by_document_id(self, document_id: str, metadata_db_path: Path):
        """
        Remove all chunks belonging to a document.

        Args:
            document_id: Document identifier
            metadata_db_path: Path to metadata database to get chunk IDs
        """
        with sqlite3.connect(metadata_db_path) as meta_conn:
            cursor = meta_conn.execute(
                "SELECT chunk_id FROM chunks WHERE document_id = ?",
                (document_id,)
            )
            chunk_ids = [row[0] for row in cursor.fetchall()]

        if not chunk_ids:
            return

        with self._connect() as conn:
            removed = self._delete_chunks(conn, chunk_ids)
            conn.commit()

        logger.info(f"Removed {removed} chunks from BM25 index for document {document_id}")

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """
        Search the index using FTS5's built-in BM25 scoring.

        Documents matching ANY query term are scored (OR semantics, matching
        the previous implementation).

        Args:
            query: Search query
            top_k: Number of results to return

        Returns:
            List of (chunk_id, score) tuples, sorted by score descending.
            Scores are positive (negated FTS5 rank; higher = better).
        """
        query_terms = self._tokenize(query)
        if not query_terms:
            return []

        # Tokens are alphanumeric-only, but quote them anyway so nothing is
        # ever interpreted as FTS5 query syntax.
        match_expr = " OR ".join(f'"{t}"' for t in query_terms)

        with self._connect() as conn:
            rows = conn.execute("""
                SELECT d.chunk_id, -bm25(bm25_fts) AS score
                FROM bm25_fts
                JOIN bm25_docs d ON d.id = bm25_fts.rowid
                WHERE bm25_fts MATCH ?
                ORDER BY bm25(bm25_fts)
                LIMIT ?
            """, (match_expr, max(1, int(top_k)))).fetchall()

        return [(chunk_id, float(score)) for chunk_id, score in rows]

    def clear(self):
        """Clear all data from the BM25 index."""
        with self._connect() as conn:
            conn.execute("DELETE FROM bm25_fts")
            conn.execute("DELETE FROM bm25_docs")
            conn.commit()
        logger.info("Cleared BM25 index")

    def get_stats(self) -> Dict[str, any]:
        """Get index statistics."""
        with self._connect() as conn:
            num_docs = conn.execute("SELECT COUNT(*) FROM bm25_docs").fetchone()[0]
            avg_doc_len = conn.execute(
                "SELECT AVG(doc_length) FROM bm25_docs"
            ).fetchone()[0] or 0
            vocab_size = conn.execute(
                "SELECT COUNT(*) FROM bm25_fts_vocab"
            ).fetchone()[0]

        return {
            "num_documents": num_docs,
            "avg_document_length": avg_doc_len,
            "vocabulary_size": vocab_size,
            "k1": self.k1,
            "b": self.b
        }
