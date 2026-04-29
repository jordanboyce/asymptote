"""Security classification feed — sector, industry, market cap, asset class.

Cached aggressively on disk because classification data changes rarely
(sector reassignments are quarterly at most). The actual fetch is
delegated to the active `MarketDataProvider` (see
`services/market_data/provider.py`) so swapping yfinance for OpenBB or
a paid feed is a one-line config change.

Used by the `get_security_classification` MCP tool. Unblocks advisor
questions like "growth vs value?", sector concentration, asset-class
breakdown without relying on the source file having a sector column.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from config import settings
from services.market_data.provider import MarketDataFetchError

logger = logging.getLogger(__name__)

# Classification is stable — cache for a week by default.
_TTL_SECONDS = 7 * 24 * 60 * 60


def _cache_db_path() -> Path:
    path = settings.data_dir / "market_data_cache.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _init_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(_cache_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS classification_cache (
            symbol      TEXT PRIMARY KEY,
            payload     TEXT NOT NULL,
            fetched_at  INTEGER NOT NULL
        )
        """
    )
    return conn


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _market_cap_bucket(market_cap: float | int | None) -> str | None:
    """Re-exported for tests; canonical impl lives in the yfinance provider."""
    from services.market_data.providers.yfinance_impl import _market_cap_bucket as _impl
    return _impl(market_cap)


def _asset_class_from_quote_type(quote_type: str | None) -> str | None:
    """Re-exported for tests; canonical impl lives in the yfinance provider."""
    from services.market_data.providers.yfinance_impl import _asset_class_from_quote_type as _impl
    return _impl(quote_type)


@dataclass
class ClassificationError(Exception):
    code: str
    message: str


def _cache_lookup(conn: sqlite3.Connection, symbol: str, ttl: int) -> dict[str, Any] | None:
    cutoff = int(time.time()) - ttl
    row = conn.execute(
        "SELECT payload, fetched_at FROM classification_cache WHERE symbol=? AND fetched_at>=?",
        (symbol, cutoff),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    payload["cached"] = True
    payload["cached_at"] = datetime.fromtimestamp(row[1], tz=timezone.utc).isoformat()
    return payload


def _cache_store(conn: sqlite3.Connection, symbol: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO classification_cache (symbol, payload, fetched_at) VALUES (?, ?, ?)",
        (symbol, json.dumps(payload), int(time.time())),
    )
    conn.commit()


def _fetch_from_yfinance(symbol: str) -> dict[str, Any]:
    """Thin shim that delegates to the active MarketDataProvider.

    Kept under this name (rather than e.g. `_fetch_from_provider`) so
    existing tests that monkey-patch `_fetch_from_yfinance` keep working
    while the real call routes through the protocol.
    """
    from services.market_data.providers import get_provider
    try:
        return get_provider().fetch_classification(symbol)
    except MarketDataFetchError as exc:
        raise ClassificationError(code=exc.code, message=exc.message) from exc


def get_security_classification(symbol: str) -> dict[str, Any]:
    """Return classification metadata for a security.

    Fields: symbol, name, asset_class, sector, industry, country,
    currency, exchange, market_cap, market_cap_bucket, dividend_yield,
    beta, isin, category (ETFs), fund_family (ETFs), source, cached,
    cached_at.

    On lookup failure returns `{"error": <code>, "message": <text>, "symbol": <sym>}`.
    """
    if not symbol or not str(symbol).strip():
        return {"error": "missing_symbol", "message": "symbol is required"}

    sym = _normalize_symbol(symbol)
    conn = _init_cache()
    try:
        cached = _cache_lookup(conn, sym, _TTL_SECONDS)
        if cached is not None:
            return cached

        try:
            payload = _fetch_from_yfinance(sym)
        except ClassificationError as exc:
            return {"error": exc.code, "message": exc.message, "symbol": sym}

        payload["cached"] = False
        payload["cached_at"] = datetime.now(tz=timezone.utc).isoformat()
        _cache_store(conn, sym, payload)
        return payload
    finally:
        conn.close()


def clear_cache(symbols: Iterable[str] | None = None) -> int:
    conn = _init_cache()
    try:
        if symbols is None:
            cur = conn.execute("DELETE FROM classification_cache")
        else:
            syms = [_normalize_symbol(s) for s in symbols]
            placeholders = ",".join(["?"] * len(syms))
            cur = conn.execute(
                f"DELETE FROM classification_cache WHERE symbol IN ({placeholders})",
                syms,
            )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()
