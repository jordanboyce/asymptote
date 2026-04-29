"""Price history feed with on-disk TTL caching.

The actual fetch is delegated to the active `MarketDataProvider`
(see `services/market_data/provider.py`). Used by the
`get_price_history` MCP tool.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from config import settings
from services.market_data.provider import MarketDataFetchError

logger = logging.getLogger(__name__)

_VALID_INTERVALS = {
    "1m", "2m", "5m", "15m", "30m", "60m", "90m",
    "1h", "1d", "5d", "1wk", "1mo", "3mo",
}

# Cache TTL per interval bucket. Intraday bars change often; daily bars
# are stable intraday and only update at close.
_INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}
_TTL_INTRADAY = 30 * 60          # 30 minutes
_TTL_DAILY = 12 * 60 * 60        # 12 hours
_TTL_WEEKLY = 24 * 60 * 60       # 24 hours


def _cache_db_path() -> Path:
    path = settings.data_dir / "market_data_cache.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _ttl_for_interval(interval: str) -> int:
    if interval in _INTRADAY_INTERVALS:
        return _TTL_INTRADAY
    if interval == "1d" or interval == "5d":
        return _TTL_DAILY
    return _TTL_WEEKLY


def _init_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(_cache_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_history_cache (
            cache_key   TEXT PRIMARY KEY,
            symbol      TEXT NOT NULL,
            start_date  TEXT NOT NULL,
            end_date    TEXT NOT NULL,
            interval    TEXT NOT NULL,
            payload     TEXT NOT NULL,
            fetched_at  INTEGER NOT NULL
        )
        """
    )
    return conn


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _normalize_date(value: str | date | datetime | None, *, fallback: str) -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return text


def _cache_key(symbol: str, start: str, end: str, interval: str) -> str:
    return f"{symbol}|{start}|{end}|{interval}"


def _cache_lookup(conn: sqlite3.Connection, key: str, ttl: int) -> dict[str, Any] | None:
    cutoff = int(time.time()) - ttl
    row = conn.execute(
        "SELECT payload, fetched_at FROM price_history_cache WHERE cache_key=? AND fetched_at>=?",
        (key, cutoff),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    payload["cached"] = True
    payload["cached_at"] = datetime.fromtimestamp(row[1], tz=timezone.utc).isoformat()
    return payload


def _cache_store(
    conn: sqlite3.Connection,
    key: str,
    symbol: str,
    start: str,
    end: str,
    interval: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO price_history_cache
          (cache_key, symbol, start_date, end_date, interval, payload, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (key, symbol, start, end, interval, json.dumps(payload), int(time.time())),
    )
    conn.commit()


def _default_start_for_interval(interval: str, end: str) -> str:
    """Sensible default lookback window when caller omits `start`."""
    end_dt = datetime.fromisoformat(end).date()
    if interval in _INTRADAY_INTERVALS:
        delta_days = 7
    elif interval in {"1d", "5d"}:
        delta_days = 365
    elif interval == "1wk":
        delta_days = 365 * 3
    else:
        delta_days = 365 * 10
    from datetime import timedelta
    return (end_dt - timedelta(days=delta_days)).isoformat()


@dataclass
class PriceHistoryError(Exception):
    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.code}: {self.message}"


def _fetch_from_yfinance(symbol: str, start: str, end: str, interval: str) -> dict[str, Any]:
    """Thin shim that delegates to the active MarketDataProvider.

    Name preserved so existing tests that monkey-patch
    `_fetch_from_yfinance` keep working.
    """
    from services.market_data.providers import get_provider
    try:
        return get_provider().fetch_price_history(symbol, start, end, interval)
    except MarketDataFetchError as exc:
        raise PriceHistoryError(code=exc.code, message=exc.message) from exc


def get_price_history(
    symbol: str,
    start: str | date | datetime | None = None,
    end: str | date | datetime | None = None,
    interval: str = "1d",
) -> dict[str, Any]:
    """Return OHLCV price history for `symbol` between `start` and `end`.

    Results are cached on disk keyed by (symbol, start, end, interval)
    with a TTL that depends on the interval.

    Returns a dict with keys: symbol, interval, start, end, currency,
    points, point_count, source, cached, cached_at. On lookup failure,
    returns `{"error": <code>, "message": <human-readable>}`.
    """
    if not symbol or not str(symbol).strip():
        return {"error": "missing_symbol", "message": "symbol is required"}

    interval = interval.strip().lower()
    if interval not in _VALID_INTERVALS:
        return {
            "error": "invalid_interval",
            "message": f"interval must be one of {sorted(_VALID_INTERVALS)}",
        }

    sym = _normalize_symbol(symbol)
    today = datetime.now(tz=timezone.utc).date().isoformat()
    end_norm = _normalize_date(end, fallback=today)
    start_norm = _normalize_date(start, fallback=_default_start_for_interval(interval, end_norm))

    key = _cache_key(sym, start_norm, end_norm, interval)
    ttl = _ttl_for_interval(interval)

    conn = _init_cache()
    try:
        cached = _cache_lookup(conn, key, ttl)
        if cached is not None:
            return cached

        try:
            payload = _fetch_from_yfinance(sym, start_norm, end_norm, interval)
        except PriceHistoryError as exc:
            return {"error": exc.code, "message": exc.message, "symbol": sym}

        payload["cached"] = False
        payload["cached_at"] = datetime.now(tz=timezone.utc).isoformat()
        _cache_store(conn, key, sym, start_norm, end_norm, interval, payload)
        return payload
    finally:
        conn.close()


def clear_cache(symbols: Iterable[str] | None = None) -> int:
    """Drop cached rows. Returns the number of rows deleted."""
    conn = _init_cache()
    try:
        if symbols is None:
            cur = conn.execute("DELETE FROM price_history_cache")
        else:
            syms = [_normalize_symbol(s) for s in symbols]
            placeholders = ",".join(["?"] * len(syms))
            cur = conn.execute(
                f"DELETE FROM price_history_cache WHERE symbol IN ({placeholders})",
                syms,
            )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()
