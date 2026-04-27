"""Company profile + recent news feeds backed by yfinance.

Two complementary tools for answering advisor questions like
"who's the CEO of X?", "any recent news on Y?", or "did Z just have a
leadership change?" — data yfinance already exposes but we weren't
surfacing.

Profiles cache for 24h (officers / sector / business summary turn over
slowly). News caches for 30 minutes so fresh headlines show up without
hammering yfinance on every call.
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


def _truncate(text: str | None, limit: int) -> str | None:
    if not text:
        return text
    s = str(text)
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _extract_officers(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull a clean list of current company officers from yfinance info."""
    raw = info.get("companyOfficers") or []
    out: list[dict[str, Any]] = []
    for o in raw:
        if not isinstance(o, dict):
            continue
        out.append({
            "name": o.get("name"),
            "title": o.get("title"),
            "age": o.get("age"),
            "year_born": o.get("yearBorn"),
            "total_pay": o.get("totalPay"),
        })
    return out


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

        try:
            import yfinance  # type: ignore
        except ImportError:
            return {"error": "yfinance_not_installed", "message": "yfinance is not installed."}

        try:
            ticker = yfinance.Ticker(sym)
            info = ticker.info or {}
        except Exception as exc:
            logger.warning(f"yfinance profile fetch failed for {sym}: {exc}")
            return {"error": "yfinance_fetch_failed", "message": str(exc), "symbol": sym}

        if not info or not any(info.get(k) for k in ("symbol", "shortName", "longName", "quoteType")):
            return {"error": "symbol_not_found", "message": f"No profile data for {sym}.", "symbol": sym}

        officers = _extract_officers(info)
        ceo = next((o for o in officers if o.get("title") and "CEO" in o["title"].upper()), None)

        payload = {
            "symbol": sym,
            "name": info.get("longName") or info.get("shortName"),
            "quote_type": info.get("quoteType"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "country": info.get("country"),
            "website": info.get("website"),
            "ir_website": info.get("irWebsite"),
            "employees": info.get("fullTimeEmployees"),
            "business_summary": _truncate(info.get("longBusinessSummary"), 2000),
            "ceo": ceo,
            "officers": officers,
            "market_cap": info.get("marketCap"),
            "source": "yfinance",
        }
        _cache_store(conn, f"profile:{sym}", payload)
        payload["cached"] = False
        return payload
    finally:
        conn.close()


def get_company_news(symbol: str, limit: int = 10) -> dict[str, Any]:
    """Recent news headlines for a ticker from yfinance.

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

        try:
            import yfinance  # type: ignore
        except ImportError:
            return {"error": "yfinance_not_installed", "message": "yfinance is not installed."}

        try:
            ticker = yfinance.Ticker(sym)
            raw_news = ticker.news or []
        except Exception as exc:
            logger.warning(f"yfinance news fetch failed for {sym}: {exc}")
            return {"error": "yfinance_fetch_failed", "message": str(exc), "symbol": sym}

        items: list[dict[str, Any]] = []
        for entry in raw_news[:limit]:
            # yfinance wraps each item under `content` in newer versions.
            c = entry.get("content") if isinstance(entry, dict) else None
            if c:
                provider = c.get("provider") or {}
                items.append({
                    "title": c.get("title"),
                    "summary": _truncate(c.get("summary") or c.get("description"), 600),
                    "publisher": provider.get("displayName") if isinstance(provider, dict) else None,
                    "published_at": c.get("pubDate") or c.get("displayTime"),
                    "url": (c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else c.get("link"),
                    "content_type": c.get("contentType"),
                })
            elif isinstance(entry, dict):
                items.append({
                    "title": entry.get("title"),
                    "summary": _truncate(entry.get("summary"), 600),
                    "publisher": entry.get("publisher"),
                    "published_at": entry.get("providerPublishTime"),
                    "url": entry.get("link"),
                    "content_type": entry.get("type"),
                })

        payload = {
            "symbol": sym,
            "count": len(items),
            "news": items,
            "source": "yfinance",
        }
        _cache_store(conn, cache_key, payload)
        payload["cached"] = False
        return payload
    finally:
        conn.close()
