"""Provider registry + selection for the MarketDataProvider protocol.

`get_provider()` returns the active provider instance based on
`settings.market_data_provider`. A test override is available via
`set_provider_for_testing()` / `reset_provider()` so unit tests can
inject a `FakeProvider` without monkey-patching individual functions.
"""

from __future__ import annotations

from threading import Lock
from typing import Optional

from services.market_data.provider import MarketDataProvider

_provider: Optional[MarketDataProvider] = None
_lock = Lock()


def get_provider() -> MarketDataProvider:
    """Return the active provider, instantiating on first call."""
    global _provider
    with _lock:
        if _provider is not None:
            return _provider
        from config import settings
        name = getattr(settings, "market_data_provider", "yfinance")
        if name == "yfinance":
            from services.market_data.providers.yfinance_impl import YFinanceProvider
            _provider = YFinanceProvider()
        else:
            raise ValueError(
                f"Unknown market_data_provider: {name!r}. "
                f"Supported: 'yfinance'."
            )
        return _provider


def set_provider_for_testing(provider: MarketDataProvider) -> None:
    """Inject a provider (e.g. a FakeProvider) for tests. Pair with
    `reset_provider()` in teardown."""
    global _provider
    with _lock:
        _provider = provider


def reset_provider() -> None:
    """Clear the cached provider so the next `get_provider()` re-reads
    settings. Used in test teardown."""
    global _provider
    with _lock:
        _provider = None
