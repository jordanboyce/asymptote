"""Market data enrichment feeds — price history, security classification, corporate events.

Each feed is a small, contained MCP tool. The data fetch is delegated
to a `MarketDataProvider` (`services/market_data/provider.py`) — yfinance
today, swappable to OpenBB / Polygon / Tiingo without touching the
per-feed module. SEC EDGAR is fetched directly inside `corporate_events.py`
since it's a separate official source independent of the equity-data provider.

All feeds cache aggressively to keep repeat calls cheap and to survive
network flakiness. Caches sit above the provider so swapping providers
does not invalidate cached data.
"""
