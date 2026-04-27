"""Tests for market_data.price_history (v4.2 enrichment feeds).

Exercises caching, TTL semantics, input normalization, and error paths
without hitting the network. Network-backed end-to-end fetch is gated
behind an env flag so CI stays offline-clean.

Run with:
    pytest tests/test_price_history.py -v
"""

from __future__ import annotations

import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Point the cache at a tmp dir so tests never touch real data/."""
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    yield


def _sample_payload(symbol: str = "AAPL") -> dict:
    return {
        "symbol": symbol,
        "interval": "1d",
        "start": "2025-01-01",
        "end": "2025-01-05",
        "currency": "USD",
        "points": [
            {"date": "2025-01-02", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000},
            {"date": "2025-01-03", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 1200},
        ],
        "point_count": 2,
        "source": "yfinance",
    }


def test_missing_symbol_returns_error():
    from services.market_data.price_history import get_price_history
    result = get_price_history(symbol="")
    assert result["error"] == "missing_symbol"


def test_invalid_interval_returns_error():
    from services.market_data.price_history import get_price_history
    result = get_price_history(symbol="AAPL", interval="not-a-bar")
    assert result["error"] == "invalid_interval"


def test_symbol_is_normalized_uppercase():
    from services.market_data import price_history as ph
    with patch.object(ph, "_fetch_from_yfinance", return_value=_sample_payload("AAPL")) as fake:
        ph.get_price_history(symbol="aapl", start="2025-01-01", end="2025-01-05")
    called_symbol = fake.call_args.args[0]
    assert called_symbol == "AAPL"


def test_cache_hit_skips_network(tmp_path):
    from services.market_data import price_history as ph
    payload = _sample_payload()
    with patch.object(ph, "_fetch_from_yfinance", return_value=payload) as fake:
        first = ph.get_price_history(symbol="AAPL", start="2025-01-01", end="2025-01-05")
        second = ph.get_price_history(symbol="AAPL", start="2025-01-01", end="2025-01-05")
    assert fake.call_count == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["point_count"] == 2


def test_cache_miss_on_different_key():
    from services.market_data import price_history as ph
    with patch.object(ph, "_fetch_from_yfinance", side_effect=lambda s, *a, **k: _sample_payload(s)) as fake:
        ph.get_price_history(symbol="AAPL", start="2025-01-01", end="2025-01-05")
        ph.get_price_history(symbol="MSFT", start="2025-01-01", end="2025-01-05")
        ph.get_price_history(symbol="AAPL", start="2025-01-01", end="2025-02-01")
    assert fake.call_count == 3


def test_symbol_not_found_surfaces_structured_error():
    from services.market_data import price_history as ph
    def _raise(*a, **k):
        raise ph.PriceHistoryError(code="symbol_not_found_or_no_data", message="nope")
    with patch.object(ph, "_fetch_from_yfinance", side_effect=_raise):
        result = ph.get_price_history(symbol="ZZZZZZ", start="2025-01-01", end="2025-01-05")
    assert result["error"] == "symbol_not_found_or_no_data"
    assert result["symbol"] == "ZZZZZZ"


def test_default_dates_fill_in_when_omitted():
    from services.market_data import price_history as ph
    with patch.object(ph, "_fetch_from_yfinance", return_value=_sample_payload()) as fake:
        ph.get_price_history(symbol="AAPL")
    args = fake.call_args.args
    sym, start, end, interval = args
    assert sym == "AAPL"
    assert interval == "1d"
    datetime.fromisoformat(start)
    datetime.fromisoformat(end)
    assert start < end


def test_clear_cache_removes_entries():
    from services.market_data import price_history as ph
    with patch.object(ph, "_fetch_from_yfinance", return_value=_sample_payload()):
        ph.get_price_history(symbol="AAPL", start="2025-01-01", end="2025-01-05")
    deleted = ph.clear_cache(["AAPL"])
    assert deleted == 1


@pytest.mark.skipif(
    not os.environ.get("ASYMPTOTE_NETWORK_TESTS"),
    reason="Network test — set ASYMPTOTE_NETWORK_TESTS=1 to run",
)
def test_end_to_end_yfinance_fetch():
    """Verifies real yfinance integration. Skipped unless opted in."""
    from services.market_data.price_history import get_price_history
    result = get_price_history(symbol="AAPL", start="2024-01-02", end="2024-01-10")
    assert "error" not in result, result
    assert result["point_count"] > 0
    assert result["points"][0]["close"] is not None
