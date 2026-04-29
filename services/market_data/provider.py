"""MarketDataProvider protocol — the seam between Asymptote's market_data
modules and the upstream financial data source.

Each method returns a raw payload dict shaped by the provider; caching,
shaping into the public response format, and error wrapping happen one
layer up in the per-feed module (classification.py, price_history.py,
corporate_events.py, company.py).

EDGAR fan-out for SEC filings stays in `corporate_events.py` and is
*not* part of this protocol — it's a separate, free, official source
that doesn't change when the equity-data provider swaps.

Adding a new provider:
  1. Implement this protocol in `services/market_data/providers/<name>_impl.py`.
  2. Register it in `services/market_data/providers/__init__.py`.
  3. Add the literal value to `Settings.market_data_provider` in `config.py`.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol


class MarketDataFetchError(Exception):
    """Raised by a provider when an upstream fetch fails.

    Modules above the protocol catch this and convert it into the
    `{"error": <code>, "message": <text>, ...}` response shape that
    MCP tool callers expect.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class MarketDataProvider(Protocol):
    """Provider interface for equity / fund market data.

    All methods take a single canonical symbol string (already uppercased
    by the caller) and return a JSON-serializable dict. Providers should
    raise `MarketDataFetchError` on failure rather than returning sentinel
    values — the per-feed module wraps the exception into a public error
    response.
    """

    name: str  # short identifier, e.g. "yfinance"

    def fetch_classification(self, symbol: str) -> dict[str, Any]:
        """Sector / industry / asset class / market cap / classification metadata.

        Expected keys: name, asset_class, quote_type, sector, industry,
        country, currency, exchange, market_cap, dividend_yield, beta, isin,
        category, fund_family.
        """
        ...

    def fetch_price_history(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str,
    ) -> dict[str, Any]:
        """OHLCV history for `symbol` over `[start, end]` at `interval`.

        Expected keys: currency, points (list of {date, open, high, low,
        close, volume}).
        """
        ...

    def fetch_market_events(
        self,
        symbol: str,
        since: date,
    ) -> dict[str, Any]:
        """Dividends, splits, and earnings calendar entries since `since`.

        Expected keys: dividends (list of {date, amount}), splits (list of
        {date, ratio}), earnings (list of {date, type}). Optional `warning`
        string when partial data is returned.

        Note: SEC filings live in `corporate_events.py` directly via EDGAR
        — they are not part of this protocol.
        """
        ...

    def fetch_company_profile(self, symbol: str) -> dict[str, Any]:
        """Company-level metadata: officers, business summary, sector, etc.

        Expected keys: name, quote_type, sector, industry, country, website,
        ir_website, employees, business_summary, officers (list of
        {name, title, age, year_born, total_pay}), market_cap.
        """
        ...

    def fetch_company_news(self, symbol: str, limit: int) -> dict[str, Any]:
        """Recent news headlines for `symbol`, capped at `limit`.

        Expected keys: news (list of {title, summary, publisher,
        published_at, url, content_type}).
        """
        ...
