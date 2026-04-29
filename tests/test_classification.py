"""Tests for market_data.classification (v4.2 enrichment feeds).

Run with:
    pytest tests/test_classification.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests._fake_provider import FakeProvider, use_fake_provider  # noqa: F401


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    yield


def _sample_equity(symbol: str = "AAPL") -> dict:
    return {
        "symbol": symbol,
        "name": "Apple Inc.",
        "asset_class": "equity",
        "quote_type": "EQUITY",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "country": "United States",
        "currency": "USD",
        "exchange": "NMS",
        "market_cap": 3_000_000_000_000,
        "market_cap_bucket": "mega",
        "dividend_yield": 0.0045,
        "beta": 1.25,
        "isin": None,
        "category": None,
        "fund_family": None,
        "source": "yfinance",
    }


def test_missing_symbol():
    from services.market_data.classification import get_security_classification
    result = get_security_classification(symbol="")
    assert result["error"] == "missing_symbol"


def test_symbol_normalized_uppercase(use_fake_provider):
    use_fake_provider.set_classification("AAPL", _sample_equity("AAPL"))
    from services.market_data.classification import get_security_classification
    get_security_classification(symbol="aapl")
    assert use_fake_provider.classification_calls == ["AAPL"]


def test_cache_hit_skips_network(use_fake_provider):
    use_fake_provider.set_classification("AAPL", _sample_equity())
    from services.market_data.classification import get_security_classification
    first = get_security_classification(symbol="AAPL")
    second = get_security_classification(symbol="AAPL")
    assert len(use_fake_provider.classification_calls) == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["sector"] == "Technology"


def test_symbol_not_found_error(use_fake_provider):
    use_fake_provider.set_classification_error("symbol_not_found", "nope")
    from services.market_data.classification import get_security_classification
    result = get_security_classification(symbol="ZZZZZ")
    assert result["error"] == "symbol_not_found"
    assert result["symbol"] == "ZZZZZ"


def test_market_cap_bucketing():
    from services.market_data.classification import _market_cap_bucket
    assert _market_cap_bucket(3_000_000_000_000) == "mega"
    assert _market_cap_bucket(50_000_000_000) == "large"
    assert _market_cap_bucket(5_000_000_000) == "mid"
    assert _market_cap_bucket(500_000_000) == "small"
    assert _market_cap_bucket(100_000_000) == "micro"
    assert _market_cap_bucket(10_000_000) == "nano"
    assert _market_cap_bucket(None) is None
    assert _market_cap_bucket(0) is None


def test_asset_class_mapping():
    from services.market_data.classification import _asset_class_from_quote_type
    assert _asset_class_from_quote_type("EQUITY") == "equity"
    assert _asset_class_from_quote_type("ETF") == "etf"
    assert _asset_class_from_quote_type("MUTUALFUND") == "mutual_fund"
    assert _asset_class_from_quote_type("CRYPTOCURRENCY") == "crypto"
    assert _asset_class_from_quote_type(None) is None


def test_clear_cache(use_fake_provider):
    use_fake_provider.set_classification("AAPL", _sample_equity())
    from services.market_data import classification as c
    c.get_security_classification(symbol="AAPL")
    assert c.clear_cache(["AAPL"]) == 1


@pytest.mark.skipif(
    not os.environ.get("ASYMPTOTE_NETWORK_TESTS"),
    reason="Network test — set ASYMPTOTE_NETWORK_TESTS=1 to run",
)
def test_end_to_end_yfinance_fetch():
    from services.market_data.classification import get_security_classification
    result = get_security_classification(symbol="AAPL")
    assert "error" not in result, result
    assert result["asset_class"] == "equity"
    assert result["sector"]
    assert result["market_cap_bucket"] in {"mega", "large"}
