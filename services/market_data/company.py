"""Company profile + recent news feeds.

Two complementary tools for answering advisor questions like
"who's the CEO of X?", "any recent news on Y?", or "did Z just have a
leadership change?". Both fetches delegate to the active
`MarketDataProvider` (see `services/market_data/provider.py`).

Profiles cache for 24h (officers / sector / business summary turn over
slowly). News caches for 30 minutes so fresh headlines show up without
hammering the provider on every call.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings
from services.market_data.provider import MarketDataFetchError

logger = logging.getLogger(__name__)

_PROFILE_TTL = 24 * 60 * 60
_NEWS_TTL = 30 * 60


def _cache_db_path() -> Path:
    path = settings.data_dir / "market_data_cache.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _init_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(_cache_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_cache (
            cache_key   TEXT PRIMARY KEY,
            payload     TEXT NOT NULL,
            fetched_at  INTEGER NOT NULL
        )
        """
    )
    return conn


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _cache_lookup(conn: sqlite3.Connection, key: str, ttl: int) -> dict[str, Any] | None:
    cutoff = int(time.time()) - ttl
    row = conn.execute(
        "SELECT payload, fetched_at FROM company_cache WHERE cache_key=? AND fetched_at>=?",
        (key, cutoff),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    payload["cached"] = True
    payload["cached_at"] = datetime.fromtimestamp(row[1], tz=timezone.utc).isoformat()
    return payload


def _cache_store(conn: sqlite3.Connection, key: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO company_cache (cache_key, payload, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(payload, default=str), int(time.time())),
    )
    conn.commit()


def get_company_profile(symbol: str) -> dict[str, Any]:
    """Company-level metadata for a ticker: current officers, business summary, key stats.

    Useful for "who is the CEO of X?", "what does X do?", "where are they
    headquartered?" — anything that needs the current state of the company
    rather than the price series or a prose document.
    """
    if not symbol or not str(symbol).strip():
        return {"error": "missing_symbol", "message": "symbol is required"}

    sym = _normalize_symbol(symbol)
    conn = _init_cache()
    try:
        cached = _cache_lookup(conn, f"profile:{sym}", _PROFILE_TTL)
        if cached is not None:
            return cached

        from services.market_data.providers import get_provider
        try:
            payload = get_provider().fetch_company_profile(sym)
        except MarketDataFetchError as exc:
            logger.warning(f"profile fetch failed for {sym}: {exc.message}")
            return {"error": exc.code, "message": exc.message, "symbol": sym}

        _cache_store(conn, f"profile:{sym}", payload)
        payload["cached"] = False
        return payload
    finally:
        conn.close()


def get_company_news(symbol: str, limit: int = 10) -> dict[str, Any]:
    """Recent news headlines for a ticker.

    Returns a list of items with title, publisher, publish time, link, and
    a short summary when available. Surfaces press releases like CEO
    transitions, earnings announcements, M&A, guidance changes.
    """
    if not symbol or not str(symbol).strip():
        return {"error": "missing_symbol", "message": "symbol is required"}

    sym = _normalize_symbol(symbol)
    limit = max(1, min(int(limit), 30))
    conn = _init_cache()
    try:
        cache_key = f"news:{sym}:{limit}"
        cached = _cache_lookup(conn, cache_key, _NEWS_TTL)
        if cached is not None:
            return cached

        from services.market_data.providers import get_provider
        try:
            payload = get_provider().fetch_company_news(sym, limit)
        except MarketDataFetchError as exc:
            logger.warning(f"news fetch failed for {sym}: {exc.message}")
            return {"error": exc.code, "message": exc.message, "symbol": sym}

        _cache_store(conn, cache_key, payload)
        payload["cached"] = False
        return payload
    finally:
        conn.close()
