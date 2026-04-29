"""yfinance implementation of the MarketDataProvider protocol.

This module owns *all* yfinance imports and call sites. The per-feed
modules above (classification.py, price_history.py, corporate_events.py,
company.py) call into this provider through the protocol — no direct
yfinance dependency anywhere else.

Caching does NOT live here. Each per-feed module wraps the provider call
with its own on-disk cache, so swapping providers later does not
invalidate cached data.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from services.market_data.provider import MarketDataFetchError, MarketDataProvider

logger = logging.getLogger(__name__)


def _import_yf():
    try:
        import yfinance  # type: ignore
        return yfinance
    except ImportError as exc:  # pragma: no cover
        raise MarketDataFetchError(
            code="yfinance_not_installed",
            message="yfinance is not installed. Run: pip install yfinance",
        ) from exc


def _market_cap_bucket(market_cap: float | int | None) -> str | None:
    if market_cap is None:
        return None
    try:
        mc = float(market_cap)
    except (TypeError, ValueError):
        return None
    if mc <= 0:
        return None
    if mc >= 200_000_000_000:
        return "mega"
    if mc >= 10_000_000_000:
        return "large"
    if mc >= 2_000_000_000:
        return "mid"
    if mc >= 300_000_000:
        return "small"
    if mc >= 50_000_000:
        return "micro"
    return "nano"


def _asset_class_from_quote_type(quote_type: str | None) -> str | None:
    if not quote_type:
        return None
    qt = str(quote_type).strip().lower()
    mapping = {
        "equity": "equity",
        "etf": "etf",
        "mutualfund": "mutual_fund",
        "index": "index",
        "currency": "currency",
        "cryptocurrency": "crypto",
        "future": "future",
        "option": "option",
    }
    return mapping.get(qt, qt)


def _truncate(text: str | None, limit: int) -> str | None:
    if not text:
        return text
    s = str(text)
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _extract_officers(info: dict[str, Any]) -> list[dict[str, Any]]:
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


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

    def fetch_classification(self, symbol: str) -> dict[str, Any]:
        yfinance = _import_yf()
        ticker = yfinance.Ticker(symbol)
        try:
            info = ticker.info or {}
        except Exception as exc:
            raise MarketDataFetchError(
                code="yfinance_fetch_failed",
                message=f"yfinance request failed for {symbol}: {exc}",
            ) from exc

        if not info or not any(info.get(k) for k in ("symbol", "shortName", "longName", "quoteType")):
            raise MarketDataFetchError(
                code="symbol_not_found",
                message=f"No classification data returned for {symbol}. Symbol may be invalid.",
            )

        quote_type = info.get("quoteType")
        market_cap = info.get("marketCap")
        return {
            "symbol": symbol,
            "name": info.get("longName") or info.get("shortName"),
            "asset_class": _asset_class_from_quote_type(quote_type),
            "quote_type": quote_type,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "country": info.get("country"),
            "currency": info.get("currency"),
            "exchange": info.get("exchange") or info.get("fullExchangeName"),
            "market_cap": market_cap,
            "market_cap_bucket": _market_cap_bucket(market_cap),
            "dividend_yield": info.get("dividendYield"),
            "beta": info.get("beta"),
            "isin": info.get("isin"),
            "category": info.get("category"),
            "fund_family": info.get("fundFamily"),
            "source": "yfinance",
        }

    def fetch_price_history(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str,
    ) -> dict[str, Any]:
        yfinance = _import_yf()
        ticker = yfinance.Ticker(symbol)
        try:
            df = ticker.history(
                start=start,
                end=end,
                interval=interval,
                auto_adjust=False,
                actions=False,
            )
        except Exception as exc:
            raise MarketDataFetchError(
                code="yfinance_fetch_failed",
                message=f"yfinance request failed for {symbol}: {exc}",
            ) from exc

        if df is None or df.empty:
            raise MarketDataFetchError(
                code="symbol_not_found_or_no_data",
                message=(
                    f"No price data returned for {symbol} between {start} and {end} "
                    f"at interval {interval}. Symbol may be invalid or outside the "
                    f"data window yfinance supports for that interval."
                ),
            )

        currency: str | None = None
        try:
            currency = ticker.fast_info.get("currency") if hasattr(ticker, "fast_info") else None
        except Exception:
            currency = None

        def _coerce(value: Any) -> float | None:
            try:
                if value is None:
                    return None
                fv = float(value)
                if fv != fv:  # NaN
                    return None
                return round(fv, 4)
            except (TypeError, ValueError):
                return None

        points: list[dict[str, Any]] = []
        for ts, row in df.iterrows():
            if hasattr(ts, "to_pydatetime"):
                dt = ts.to_pydatetime()
            else:
                dt = ts
            if isinstance(dt, datetime):
                iso = dt.date().isoformat() if interval in {"1d", "5d", "1wk", "1mo", "3mo"} else dt.isoformat()
            else:
                iso = str(dt)
            points.append({
                "date": iso,
                "open": _coerce(row.get("Open")),
                "high": _coerce(row.get("High")),
                "low": _coerce(row.get("Low")),
                "close": _coerce(row.get("Close")),
                "volume": int(row["Volume"]) if row.get("Volume") is not None and row.get("Volume") == row.get("Volume") else None,
            })

        return {
            "symbol": symbol,
            "interval": interval,
            "start": start,
            "end": end,
            "currency": currency,
            "points": points,
            "point_count": len(points),
            "source": "yfinance",
        }

    def fetch_market_events(self, symbol: str, since: date) -> dict[str, Any]:
        try:
            yfinance = _import_yf()
        except MarketDataFetchError:
            return {
                "dividends": [],
                "splits": [],
                "earnings": [],
                "warning": "yfinance not installed — dividend/split/earnings data unavailable.",
            }

        try:
            ticker = yfinance.Ticker(symbol)
        except Exception as exc:
            logger.warning(f"yfinance ticker init failed for {symbol}: {exc}")
            return {"dividends": [], "splits": [], "earnings": [],
                    "warning": f"yfinance init failed: {exc}"}

        dividends: list[dict[str, Any]] = []
        splits: list[dict[str, Any]] = []
        earnings: list[dict[str, Any]] = []

        try:
            for ts, amount in (ticker.dividends or {}).items():
                try:
                    d = ts.date() if hasattr(ts, "date") else ts
                except Exception:
                    continue
                if d >= since:
                    dividends.append({"date": d.isoformat(), "amount": float(amount)})
        except Exception as exc:
            logger.debug(f"dividends fetch failed for {symbol}: {exc}")

        try:
            for ts, ratio in (ticker.splits or {}).items():
                try:
                    d = ts.date() if hasattr(ts, "date") else ts
                except Exception:
                    continue
                if d >= since:
                    splits.append({"date": d.isoformat(), "ratio": float(ratio)})
        except Exception as exc:
            logger.debug(f"splits fetch failed for {symbol}: {exc}")

        try:
            cal = ticker.calendar
            if cal is not None and isinstance(cal, dict):
                earn_date = cal.get("Earnings Date")
                if earn_date:
                    if isinstance(earn_date, list):
                        for d in earn_date:
                            earnings.append({"date": str(d), "type": "earnings"})
                    else:
                        earnings.append({"date": str(earn_date), "type": "earnings"})
        except Exception as exc:
            logger.debug(f"calendar fetch failed for {symbol}: {exc}")

        return {"dividends": dividends, "splits": splits, "earnings": earnings}

    def fetch_company_profile(self, symbol: str) -> dict[str, Any]:
        yfinance = _import_yf()
        try:
            ticker = yfinance.Ticker(symbol)
            info = ticker.info or {}
        except Exception as exc:
            raise MarketDataFetchError(
                code="yfinance_fetch_failed",
                message=str(exc),
            ) from exc

        if not info or not any(info.get(k) for k in ("symbol", "shortName", "longName", "quoteType")):
            raise MarketDataFetchError(
                code="symbol_not_found",
                message=f"No profile data for {symbol}.",
            )

        officers = _extract_officers(info)
        ceo = next((o for o in officers if o.get("title") and "CEO" in o["title"].upper()), None)
        return {
            "symbol": symbol,
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

    def fetch_company_news(self, symbol: str, limit: int) -> dict[str, Any]:
        yfinance = _import_yf()
        try:
            ticker = yfinance.Ticker(symbol)
            raw_news = ticker.news or []
        except Exception as exc:
            raise MarketDataFetchError(
                code="yfinance_fetch_failed",
                message=str(exc),
            ) from exc

        items: list[dict[str, Any]] = []
        for entry in raw_news[:limit]:
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
        return {
            "symbol": symbol,
            "count": len(items),
            "news": items,
            "source": "yfinance",
        }
