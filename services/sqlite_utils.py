"""Shared SQLite connection helper.

Every store in the app (metadata, BM25, structured tables, app database,
expertise packs) opens plain sqlite3 connections from both request threads and
background indexing threads. Without WAL and a busy timeout, any real
concurrency produces "database is locked" errors. All connections go through
here so the pragmas are applied uniformly.
"""

import sqlite3


def sqlite_connect(database, **kwargs) -> sqlite3.Connection:
    """Open a sqlite3 connection with WAL journaling and a busy timeout.

    Drop-in replacement for sqlite3.connect — extra args pass through.
    busy_timeout is set first so the journal_mode upgrade itself waits
    instead of failing if another connection holds the lock.
    """
    conn = sqlite3.connect(database, **kwargs)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
