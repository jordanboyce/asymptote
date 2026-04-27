"""Market data enrichment feeds — price history, security classification, corporate events.

Each feed is a small, contained MCP tool backed by a free data source
(yfinance, SEC EDGAR). All feeds cache aggressively to keep repeat calls
cheap and to survive network flakiness.
"""
