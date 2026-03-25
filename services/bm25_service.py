"""BM25 keyword search service for hybrid search support."""

import math
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class BM25Index:
    """
    BM25 (Best Matching 25) keyword search index.

    BM25 is a probabilistic retrieval function that ranks documents based on
    term frequency and inverse document frequency, with length normalization.

    Parameters:
        k1: Term frequency saturation parameter (default: 1.5)
        b: Length normalization parameter (default: 0.75)
    """

    def __init__(self, db_path: Path, k1: float = 1.5, b: float = 0.75):
        """
        Initialize the BM25 index.

        Args:
            db_path: Path to SQLite database for storing BM25 data
            k1: Controls term frequency saturation (1.2-2.0 typical)
            b: Controls length normalization (0 = no normalization, 1 = full)
        """
        self.db_path = db_path
        self.k1 = k1
        self.b = b
        self._init_db()

    def _init_db(self):
        """Initialize BM25 database tables."""
        with sqlite3.connect(self.db_path) as conn:
            # Document frequency table: term -> number of docs containing term
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bm25_doc_freq (
                    term TEXT PRIMARY KEY,
                    doc_freq INTEGER NOT NULL
                )
            """)

            # Term frequency table: (chunk_id, term) -> frequency
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bm25_term_freq (
                    chunk_id TEXT NOT NULL,
                    term TEXT NOT NULL,
                    term_freq INTEGER NOT NULL,
                    doc_length INTEGER NOT NULL,
                    PRIMARY KEY (chunk_id, term)
                )
            """)

            # Corpus statistics
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bm25_stats (
                    key TEXT PRIMARY KEY,
                    value REAL NOT NULL
                )
            """)

            # Create indexes for faster queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bm25_term
                ON bm25_term_freq(term)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bm25_chunk
                ON bm25_term_freq(chunk_id)
            """)

            conn.commit()

        logger.info(f"Initialized BM25 index at {self.db_path}")

    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenize text into terms for BM25 indexing.

        Simple tokenization that:
        - Lowercases text
        - Splits on non-alphanumeric characters
        - Removes very short tokens (< 2 chars)
        - Removes common stopwords

        Args:
            text: Text to tokenize

        Returns:
            List of tokens
        """
        # Lowercase and split on non-alphanumeric
        tokens = re.findall(r'\b[a-z0-9]+\b', text.lower())

        # Common English stopwords
        stopwords = {
            'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
            'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on', 'that', 'the',
            'to', 'was', 'were', 'will', 'with', 'the', 'this', 'but', 'they',
            'have', 'had', 'what', 'when', 'where', 'who', 'which', 'why', 'how',
            'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
            'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so',
            'than', 'too', 'very', 'can', 'just', 'should', 'now'
        }

        # Filter stopwords and short tokens
        return [t for t in tokens if len(t) >= 2 and t not in stopwords]

    def add_document(self, chunk_id: str, text: str):
        """
        Add a document/chunk to the BM25 index.

        Args:
            chunk_id: Unique identifier for the chunk
            text: Text content to index
        """
        tokens = self._tokenize(text)
        if not tokens:
            return

        doc_length = len(tokens)
        term_freqs = Counter(tokens)

        with sqlite3.connect(self.db_path) as conn:
            # Check if chunk already exists (for re-indexing)
            existing = conn.execute(
                "SELECT 1 FROM bm25_term_freq WHERE chunk_id = ? LIMIT 1",
                (chunk_id,)
            ).fetchone()

            if existing:
                # Remove old entries
                self._remove_document_internal(conn, chunk_id)

            # Add term frequencies
            conn.executemany("""
                INSERT INTO bm25_term_freq (chunk_id, term, term_freq, doc_length)
                VALUES (?, ?, ?, ?)
            """, [(chunk_id, term, freq, doc_length) for term, freq in term_freqs.items()])

            # Update document frequencies
            for term in term_freqs.keys():
                conn.execute("""
                    INSERT INTO bm25_doc_freq (term, doc_freq)
                    VALUES (?, 1)
                    ON CONFLICT(term) DO UPDATE SET doc_freq = doc_freq + 1
                """, (term,))

            # Update corpus stats
            self._update_stats(conn)
            conn.commit()

    def add_documents_batch(self, documents: List[Tuple[str, str]]):
        """
        Add multiple documents to the index in a batch.

        Args:
            documents: List of (chunk_id, text) tuples
        """
        with sqlite3.connect(self.db_path) as conn:
            all_term_freqs = []
            doc_freq_updates = Counter()

            for chunk_id, text in documents:
                tokens = self._tokenize(text)
                if not tokens:
                    continue

                doc_length = len(tokens)
                term_freqs = Counter(tokens)

                for term, freq in term_freqs.items():
                    all_term_freqs.append((chunk_id, term, freq, doc_length))
                    doc_freq_updates[term] += 1

            # Batch insert term frequencies
            conn.executemany("""
                INSERT OR REPLACE INTO bm25_term_freq (chunk_id, term, term_freq, doc_length)
                VALUES (?, ?, ?, ?)
            """, all_term_freqs)

            # Batch update document frequencies
            for term, count in doc_freq_updates.items():
                conn.execute("""
                    INSERT INTO bm25_doc_freq (term, doc_freq)
                    VALUES (?, ?)
                    ON CONFLICT(term) DO UPDATE SET doc_freq = doc_freq + ?
                """, (term, count, count))

            self._update_stats(conn)
            conn.commit()

        logger.info(f"Added {len(documents)} documents to BM25 index")

    def remove_document(self, chunk_id: str):
        """
        Remove a document/chunk from the BM25 index.

        Args:
            chunk_id: Chunk identifier to remove
        """
        with sqlite3.connect(self.db_path) as conn:
            self._remove_document_internal(conn, chunk_id)
            self._update_stats(conn)
            conn.commit()

    def _remove_document_internal(self, conn: sqlite3.Connection, chunk_id: str):
        """Internal method to remove document within a transaction."""
        # Get terms for this document to update doc_freq
        cursor = conn.execute(
            "SELECT term FROM bm25_term_freq WHERE chunk_id = ?",
            (chunk_id,)
        )
        terms = [row[0] for row in cursor.fetchall()]

        # Delete term frequencies
        conn.execute("DELETE FROM bm25_term_freq WHERE chunk_id = ?", (chunk_id,))

        # Update document frequencies
        for term in terms:
            conn.execute("""
                UPDATE bm25_doc_freq SET doc_freq = doc_freq - 1 WHERE term = ?
            """, (term,))

        # Clean up terms with zero doc_freq
        conn.execute("DELETE FROM bm25_doc_freq WHERE doc_freq <= 0")

    def remove_documents_by_document_id(self, document_id: str, metadata_db_path: Path):
        """
        Remove all chunks belonging to a document.

        Args:
            document_id: Document identifier
            metadata_db_path: Path to metadata database to get chunk IDs
        """
        # Get chunk IDs from metadata store
        with sqlite3.connect(metadata_db_path) as meta_conn:
            cursor = meta_conn.execute(
                "SELECT chunk_id FROM chunks WHERE document_id = ?",
                (document_id,)
            )
            chunk_ids = [row[0] for row in cursor.fetchall()]

        if not chunk_ids:
            return

        with sqlite3.connect(self.db_path) as conn:
            for chunk_id in chunk_ids:
                self._remove_document_internal(conn, chunk_id)
            self._update_stats(conn)
            conn.commit()

        logger.info(f"Removed {len(chunk_ids)} chunks from BM25 index for document {document_id}")

    def _update_stats(self, conn: sqlite3.Connection):
        """Update corpus statistics."""
        # Total documents
        cursor = conn.execute("SELECT COUNT(DISTINCT chunk_id) FROM bm25_term_freq")
        num_docs = cursor.fetchone()[0]

        # Average document length
        cursor = conn.execute("""
            SELECT AVG(doc_length) FROM (
                SELECT chunk_id, MAX(doc_length) as doc_length
                FROM bm25_term_freq GROUP BY chunk_id
            )
        """)
        avg_doc_len = cursor.fetchone()[0] or 0

        conn.execute("""
            INSERT OR REPLACE INTO bm25_stats (key, value) VALUES ('num_docs', ?)
        """, (num_docs,))

        conn.execute("""
            INSERT OR REPLACE INTO bm25_stats (key, value) VALUES ('avg_doc_len', ?)
        """, (avg_doc_len,))

    def _get_stats(self) -> Tuple[int, float]:
        """Get corpus statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT key, value FROM bm25_stats")
            stats = dict(cursor.fetchall())
            return int(stats.get('num_docs', 0)), float(stats.get('avg_doc_len', 0))

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """
        Search the index using BM25 scoring.

        Args:
            query: Search query
            top_k: Number of results to return

        Returns:
            List of (chunk_id, score) tuples, sorted by score descending
        """
        query_terms = self._tokenize(query)
        if not query_terms:
            return []

        num_docs, avg_doc_len = self._get_stats()
        if num_docs == 0:
            return []

        with sqlite3.connect(self.db_path) as conn:
            # Get document frequencies for query terms
            placeholders = ','.join('?' * len(query_terms))
            cursor = conn.execute(f"""
                SELECT term, doc_freq FROM bm25_doc_freq WHERE term IN ({placeholders})
            """, query_terms)
            doc_freqs = dict(cursor.fetchall())

            # Calculate IDF for each query term
            idfs = {}
            for term in query_terms:
                df = doc_freqs.get(term, 0)
                if df > 0:
                    # BM25 IDF formula
                    idfs[term] = math.log((num_docs - df + 0.5) / (df + 0.5) + 1)

            if not idfs:
                return []

            # Get term frequencies for matching documents
            idf_terms = list(idfs.keys())
            placeholders = ','.join('?' * len(idf_terms))
            cursor = conn.execute(f"""
                SELECT chunk_id, term, term_freq, doc_length
                FROM bm25_term_freq
                WHERE term IN ({placeholders})
            """, idf_terms)

            # Calculate BM25 scores
            scores: Dict[str, float] = {}
            for chunk_id, term, tf, doc_length in cursor.fetchall():
                idf = idfs.get(term, 0)

                # BM25 scoring formula
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * doc_length / avg_doc_len)
                term_score = idf * (numerator / denominator)

                scores[chunk_id] = scores.get(chunk_id, 0) + term_score

        # Sort by score and return top_k
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results[:top_k]

    def clear(self):
        """Clear all data from the BM25 index."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM bm25_term_freq")
            conn.execute("DELETE FROM bm25_doc_freq")
            conn.execute("DELETE FROM bm25_stats")
            conn.commit()
        logger.info("Cleared BM25 index")

    def get_stats(self) -> Dict[str, any]:
        """Get index statistics."""
        num_docs, avg_doc_len = self._get_stats()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM bm25_doc_freq")
            vocab_size = cursor.fetchone()[0]

        return {
            "num_documents": num_docs,
            "avg_document_length": avg_doc_len,
            "vocabulary_size": vocab_size,
            "k1": self.k1,
            "b": self.b
        }
