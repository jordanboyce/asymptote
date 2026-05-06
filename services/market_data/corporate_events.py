"""Corporate events feed — SEC filings, dividends, splits, earnings.

Backed by SEC EDGAR (free, no key) for filings and yfinance for
dividends/splits/earnings calendar. Used by the `get_corporate_events`
MCP tool to answer "any CEO changes?", "recent 8-Ks?", "M&A exposure?",
"dividend cut?" in a single call rather than a 40-ticker web search.

All lookups are cached on disk for 12 hours. SEC requests send a
User-Agent identifying the app per EDGAR access rules.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config import settings

logger = logging.getLogger(__name__)

_TTL_SECONDS = 12 * 60 * 60

_SEC_USER_AGENT = "Finn Research finn@example.com"
_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

_DEFAULT_FORM_TYPES = ("8-K", "10-K", "10-Q", "S-4", "DEFM14A")
_MERGER_FORMS = {"S-4", "DEFM14A", "SC 14D9", "SC TO-T"}

_SUPPORTED_TYPES = {"filing", "8-K", "10-K", "10-Q", "dividend", "split", "merger", "earnings"}


def _cache_db_path() -> Path:
    path = settings.data_dir / "market_data_cache.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _init_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(_cache_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS corporate_events_cache (
            cache_key   TEXT PRIMARY KEY,
            payload     TEXT NOT NULL,
            fetched_at  INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sec_ticker_cik_cache (
            symbol      TEXT PRIMARY KEY,
            cik         TEXT NOT NULL,
            company     TEXT,
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
        "SELECT payload, fetched_at FROM corporate_events_cache WHERE cache_key=? AND fetched_at>=?",
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
        "INSERT OR REPLACE INTO corporate_events_cache (cache_key, payload, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(payload, default=str), int(time.time())),
    )
    conn.commit()


@dataclass
class CorporateEventsError(Exception):
    code: str
    message: str


def _sec_get(url: str) -> Any:
    req = Request(url, headers={"User-Agent": _SEC_USER_AGENT, "Accept": "application/json"})
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resolve_cik(symbol: str) -> str | None:
    """Look up a zero-padded 10-digit CIK for a ticker from EDGAR."""
    sym = _normalize_symbol(symbol)
    conn = _init_cache()
    try:
        # Cache CIK lookups forever — they don't change.
        row = conn.execute(
            "SELECT cik FROM sec_ticker_cik_cache WHERE symbol=?",
            (sym,),
        ).fetchone()
        if row:
            return row[0]

        try:
            data = _sec_get(_SEC_TICKERS_URL)
        except Exception as exc:
            logger.warning(f"SEC ticker map fetch failed: {exc}")
            return None

        # company_tickers.json schema: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        found_cik: str | None = None
        found_company: str | None = None
        for entry in (data.values() if isinstance(data, dict) else []):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("ticker", "")).upper() == sym:
                found_cik = str(entry.get("cik_str", "")).zfill(10)
                found_company = entry.get("title")
                break

        if found_cik:
            conn.execute(
                "INSERT OR REPLACE INTO sec_ticker_cik_cache (symbol, cik, company, fetched_at) VALUES (?, ?, ?, ?)",
                (sym, found_cik, found_company, int(time.time())),
            )
            conn.commit()
        return found_cik
    finally:
        conn.close()


def _fetch_sec_filings(
    cik: str,
    since: date,
    form_types: Iterable[str],
    limit: int,
) -> list[dict[str, Any]]:
    try:
        data = _sec_get(_SEC_SUBMISSIONS_URL.format(cik=cik))
    except Exception as exc:
        raise CorporateEventsError(
            code="sec_fetch_failed",
            message=f"SEC EDGAR request failed for CIK {cik}: {exc}",
        ) from exc

    recent = (data or {}).get("filings", {}).get("recent", {}) or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []
    primary_descs = recent.get("primaryDocDescription") or []
    report_dates = recent.get("reportDate") or []

    wanted = {f.upper() for f in form_types}
    out: list[dict[str, Any]] = []
    for i, form in enumerate(forms):
        if form.upper() not in wanted:
            continue
        try:
            filed = datetime.fromisoformat(dates[i]).date()
        except (ValueError, IndexError):
            continue
        if filed < since:
            continue
        accession = accessions[i] if i < len(accessions) else ""
        accession_nodash = accession.replace("-", "")
        primary = primary_docs[i] if i < len(primary_docs) else ""
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{primary}"
            if accession_nodash and primary else None
        )
        out.append({
            "form": form,
            "filed": filed.isoformat(),
            "report_date": report_dates[i] if i < len(report_dates) else None,
            "accession": accession,
            "description": primary_descs[i] if i < len(primary_descs) else None,
            "url": url,
        })
        if len(out) >= limit:
            break
    return out


def _fetch_yfinance_events(symbol: str, since: date) -> dict[str, Any]:
    """Return dividends, splits, and earnings via the active MarketDataProvider.

    Name preserved (rather than `_fetch_provider_events`) so tests that
    monkey-patch this function continue to work. SEC EDGAR filings are
    fetched separately in `_fetch_sec_filings` — they are not part of
    the provider protocol.
    """
    from services.market_data.provider import MarketDataFetchError
    from services.market_data.providers import get_provider
    try:
        return get_provider().fetch_market_events(symbol, since)
    except MarketDataFetchError as exc:
        return {
            "dividends": [],
            "splits": [],
            "earnings": [],
            "warning": exc.message,
        }


def _default_since(years: int = 1) -> date:
    return (datetime.now(tz=timezone.utc).date() - timedelta(days=365 * years))


def _normalize_since(value: str | date | datetime | None) -> date:
    if value is None or value == "":
        return _default_since()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return _default_since()


def get_corporate_events(
    symbol: str,
    since: str | date | datetime | None = None,
    types: Iterable[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return recent corporate events for `symbol` since a given date.

    `types` accepts: "filing", "8-K", "10-K", "10-Q", "dividend", "split",
    "merger", "earnings". Default covers everything.

    Returns a dict with filings (list), dividends (list), splits (list),
    earnings (list), and summary counts. On lookup failure returns
    `{"error": <code>, "message": <text>, "symbol": <sym>}`.
    """
    if not symbol or not str(symbol).strip():
        return {"error": "missing_symbol", "message": "symbol is required"}

    sym = _normalize_symbol(symbol)
    since_date = _normalize_since(since)
    requested = {t.strip().lower() for t in (types or _SUPPORTED_TYPES) if t and t.strip()}
    unknown = requested - {t.lower() for t in _SUPPORTED_TYPES}
    if unknown:
        return {
            "error": "invalid_type",
            "message": f"Unknown event types: {sorted(unknown)}. Supported: {sorted(_SUPPORTED_TYPES)}",
        }

    key = f"{sym}|{since_date.isoformat()}|{','.join(sorted(requested))}|{limit}"
    conn = _init_cache()
    try:
        cached = _cache_lookup(conn, key, _TTL_SECONDS)
        if cached is not None:
            return cached

        want_filings = any(t in requested for t in ("filing", "8-k", "10-k", "10-q", "merger"))
        want_dividends = "dividend" in requested
        want_splits = "split" in requested
        want_earnings = "earnings" in requested

        form_types: set[str] = set()
        if "filing" in requested:
            form_types.update(_DEFAULT_FORM_TYPES)
        if "8-k" in requested:
            form_types.add("8-K")
        if "10-k" in requested:
            form_types.add("10-K")
        if "10-q" in requested:
            form_types.add("10-Q")
        if "merger" in requested:
            form_types.update(_MERGER_FORMS)

        filings: list[dict[str, Any]] = []
        sec_warning: str | None = None
        if want_filings and form_types:
            cik = _resolve_cik(sym)
            if cik is None:
                sec_warning = (
                    f"No SEC CIK mapping for {sym} — filings unavailable. "
                    "Non-US symbols and ETFs are typically not in EDGAR."
                )
            else:
                try:
                    filings = _fetch_sec_filings(cik, since_date, form_types, limit)
                except CorporateEventsError as exc:
                    sec_warning = exc.message

        yf_payload: dict[str, Any] = {"dividends": [], "splits": [], "earnings": []}
        if want_dividends or want_splits or want_earnings:
            yf_payload = _fetch_yfinance_events(sym, since_date)

        dividends = yf_payload.get("dividends", []) if want_dividends else []
        splits = yf_payload.get("splits", []) if want_splits else []
        earnings = yf_payload.get("earnings", []) if want_earnings else []

        payload: dict[str, Any] = {
            "symbol": sym,
            "since": since_date.isoformat(),
            "types": sorted(requested),
            "filings": filings,
            "dividends": dividends,
            "splits": splits,
            "earnings": earnings,
            "counts": {
                "filings": len(filings),
                "dividends": len(dividends),
                "splits": len(splits),
                "earnings": len(earnings),
            },
            "source": "sec_edgar+yfinance",
        }
        warnings: list[str] = []
        if sec_warning:
            warnings.append(sec_warning)
        if yf_payload.get("warning"):
            warnings.append(yf_payload["warning"])
        if warnings:
            payload["warnings"] = warnings

        payload["cached"] = False
        payload["cached_at"] = datetime.now(tz=timezone.utc).isoformat()
        _cache_store(conn, key, payload)
        return payload
    finally:
        conn.close()


def clear_cache() -> int:
    conn = _init_cache()
    try:
        cur = conn.execute("DELETE FROM corporate_events_cache")
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()
