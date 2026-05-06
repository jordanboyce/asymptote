"""FakeProvider for unit tests against services.market_data.

Use the `use_fake_provider` fixture below (or instantiate directly + call
`set_provider_for_testing`) instead of monkey-patching the per-feed
`_fetch_from_yfinance` / `_fetch_yfinance_events` shims.

Example
-------
    def test_sector_classified(use_fake_provider):
        use_fake_provider.set_classification("AAPL", {"sector": "Technology"})
        result = get_security_classification("AAPL")
        assert result["sector"] == "Technology"
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

import pytest

from services.market_data.provider import MarketDataFetchError, MarketDataProvider
from services.market_data.providers import reset_provider, set_provider_for_testing


class FakeProvider(MarketDataProvider):
    """In-memory MarketDataProvider for tests.

    Each `fetch_*` method returns whatever was registered via the
    `set_*` helpers, or raises a `MarketDataFetchError` configured via
    `set_*_error`. Tracks call counts so tests can assert caching
    behavior without monkey-patching internals.
    """

    name = "fake"

    def __init__(self) -> None:
        self._classification: dict[str, dict[str, Any]] = {}
        self._classification_err: Optional[MarketDataFetchError] = None
        self._price: dict[str, dict[str, Any]] = {}
        self._price_err: Optional[MarketDataFetchError] = None
        self._events: dict[str, dict[str, Any]] = {}
        self._events_err: Optional[MarketDataFetchError] = None
        self._profile: dict[str, dict[str, Any]] = {}
        self._profile_err: Optional[MarketDataFetchError] = None
        self._news: dict[str, dict[str, Any]] = {}
        self._news_err: Optional[MarketDataFetchError] = None
        self.classification_calls: list[str] = []
        self.price_calls: list[tuple[str, str, str, str]] = []
        self.events_calls: list[tuple[str, date]] = []
        self.profile_calls: list[str] = []
        self.news_calls: list[tuple[str, int]] = []

    def set_classification(self, symbol: str, payload: dict[str, Any]) -> None:
        self._classification[symbol.upper()] = payload

    def set_classification_error(self, code: str, message: str) -> None:
        self._classification_err = MarketDataFetchError(code, message)

    def set_price_history(self, symbol: str, payload: dict[str, Any]) -> None:
        self._price[symbol.upper()] = payload

    def set_price_history_error(self, code: str, message: str) -> None:
        self._price_err = MarketDataFetchError(code, message)

    def set_market_events(self, symbol: str, payload: dict[str, Any]) -> None:
        self._events[symbol.upper()] = payload

    def set_company_profile(self, symbol: str, payload: dict[str, Any]) -> None:
        self._profile[symbol.upper()] = payload

    def set_company_news(self, symbol: str, payload: dict[str, Any]) -> None:
        self._news[symbol.upper()] = payload

    def fetch_classification(self, symbol: str) -> dict[str, Any]:
        self.classification_calls.append(symbol)
        if self._classification_err is not None:
            raise self._classification_err
        if symbol.upper() in self._classification:
            return self._classification[symbol.upper()]
        raise MarketDataFetchError(
            code="symbol_not_found",
            message=f"FakeProvider has no classification registered for {symbol}",
        )

    def fetch_price_history(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str,
    ) -> dict[str, Any]:
        self.price_calls.append((symbol, start, end, interval))
        if self._price_err is not None:
            raise self._price_err
        if symbol.upper() in self._price:
            return self._price[symbol.upper()]
        raise MarketDataFetchError(
            code="symbol_not_found_or_no_data",
            message=f"FakeProvider has no price history registered for {symbol}",
        )

    def fetch_market_events(self, symbol: str, since: date) -> dict[str, Any]:
        self.events_calls.append((symbol, since))
        if self._events_err is not None:
            raise self._events_err
        # Events endpoint returns an empty payload by default rather than
        # raising — matches the yfinance impl's "best-effort" semantics.
        return self._events.get(symbol.upper(), {"dividends": [], "splits": [], "earnings": []})

    def fetch_company_profile(self, symbol: str) -> dict[str, Any]:
        self.profile_calls.append(symbol)
        if self._profile_err is not None:
            raise self._profile_err
        if symbol.upper() in self._profile:
            return self._profile[symbol.upper()]
        raise MarketDataFetchError(
            code="symbol_not_found",
            message=f"FakeProvider has no company profile registered for {symbol}",
        )

    def fetch_company_news(self, symbol: str, limit: int) -> dict[str, Any]:
        self.news_calls.append((symbol, limit))
        if self._news_err is not None:
            raise self._news_err
        return self._news.get(symbol.upper(), {"symbol": symbol, "count": 0, "news": []})


@pytest.fixture
def use_fake_provider():
    """Install a FakeProvider as the active MarketDataProvider for the
    duration of the test, then reset back to settings-driven default."""
    fake = FakeProvider()
    set_provider_for_testing(fake)
    try:
        yield fake
    finally:
        reset_provider()
