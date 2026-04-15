"""Tests for market_data.corporate_events (v4.2 enrichment feeds).

Run with:
    pytest tests/test_corporate_events.py -v
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    yield


def _sample_sec_submissions():
    today = date.today()
    return {
        "filings": {
            "recent": {
                "form": ["8-K", "10-Q", "8-K", "S-4"],
                "filingDate": [
                    today.isoformat(),
                    (today - timedelta(days=10)).isoformat(),
                    (today - timedelta(days=400)).isoformat(),  # outside since
                    (today - timedelta(days=5)).isoformat(),
                ],
                "accessionNumber": ["0000320193-25-000100", "0000320193-25-000099", "0000320193-24-000050", "0000320193-25-000101"],
                "primaryDocument": ["a.htm", "b.htm", "c.htm", "s4.htm"],
                "primaryDocDescription": ["Current report", "Quarterly report", "Current report", "Merger prospectus"],
                "reportDate": [today.isoformat(), (today - timedelta(days=10)).isoformat(), "", ""],
            }
        }
    }


def test_missing_symbol():
    from services.market_data.corporate_events import get_corporate_events
    assert get_corporate_events(symbol="")["error"] == "missing_symbol"


def test_invalid_type():
    from services.market_data.corporate_events import get_corporate_events
    result = get_corporate_events(symbol="AAPL", types=["not-a-real-type"])
    assert result["error"] == "invalid_type"


def test_sec_filtering_by_since_and_types():
    from services.market_data import corporate_events as ce

    with patch.object(ce, "_resolve_cik", return_value="0000320193"), \
         patch.object(ce, "_sec_get", return_value=_sample_sec_submissions()), \
         patch.object(ce, "_fetch_yfinance_events", return_value={"dividends": [], "splits": [], "earnings": []}):
        result = ce.get_corporate_events(
            symbol="AAPL",
            since=(date.today() - timedelta(days=30)).isoformat(),
            types=["8-K"],
        )

    assert "error" not in result, result
    forms = [f["form"] for f in result["filings"]]
    assert forms == ["8-K"]  # only the recent 8-K; old 8-K filtered by `since`, S-4 filtered by type
    assert result["counts"]["filings"] == 1


def test_merger_type_includes_s4():
    from services.market_data import corporate_events as ce

    with patch.object(ce, "_resolve_cik", return_value="0000320193"), \
         patch.object(ce, "_sec_get", return_value=_sample_sec_submissions()), \
         patch.object(ce, "_fetch_yfinance_events", return_value={"dividends": [], "splits": [], "earnings": []}):
        result = ce.get_corporate_events(
            symbol="AAPL",
            since=(date.today() - timedelta(days=30)).isoformat(),
            types=["merger"],
        )

    forms = [f["form"] for f in result["filings"]]
    assert "S-4" in forms


def test_no_cik_surfaces_warning():
    from services.market_data import corporate_events as ce

    with patch.object(ce, "_resolve_cik", return_value=None), \
         patch.object(ce, "_fetch_yfinance_events", return_value={
             "dividends": [{"date": "2025-01-01", "amount": 0.25}],
             "splits": [], "earnings": [],
         }):
        result = ce.get_corporate_events(
            symbol="VWRL.L",
            types=["filing", "dividend"],
        )

    assert "error" not in result
    assert result["filings"] == []
    assert any("CIK" in w for w in result.get("warnings", []))
    assert result["counts"]["dividends"] == 1


def test_dividends_and_splits_filtered_by_since():
    from services.market_data import corporate_events as ce

    today = date.today()
    yf = {
        "dividends": [
            {"date": (today - timedelta(days=10)).isoformat(), "amount": 0.25},
        ],
        "splits": [
            {"date": (today - timedelta(days=5)).isoformat(), "ratio": 4.0},
        ],
        "earnings": [],
    }
    with patch.object(ce, "_resolve_cik", return_value=None), \
         patch.object(ce, "_fetch_yfinance_events", return_value=yf):
        result = ce.get_corporate_events(
            symbol="AAPL",
            since=(today - timedelta(days=30)).isoformat(),
            types=["dividend", "split"],
        )

    assert result["counts"]["dividends"] == 1
    assert result["counts"]["splits"] == 1


def test_cache_hit_skips_network():
    from services.market_data import corporate_events as ce

    with patch.object(ce, "_resolve_cik", return_value="0000320193") as cik_fake, \
         patch.object(ce, "_sec_get", return_value=_sample_sec_submissions()) as sec_fake, \
         patch.object(ce, "_fetch_yfinance_events", return_value={"dividends": [], "splits": [], "earnings": []}):
        first = ce.get_corporate_events(symbol="AAPL", types=["8-K"])
        second = ce.get_corporate_events(symbol="AAPL", types=["8-K"])

    assert sec_fake.call_count == 1
    assert cik_fake.call_count == 1
    assert first["cached"] is False
    assert second["cached"] is True
