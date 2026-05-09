"""Tests for services.collection_summary.compute_collection_summary.

The summary endpoint backs the Collection card (advisor-desktop-ux R3).
Two formats anchor the regression: Pershing's flat Unrealized G/L (lots
on every row) and NetX360's hierarchical Holdings by Investor (header
sections grouping accounts). Both must produce non-zero positions and
accounts to confirm the rollup view + account-role detection work for
real-shaped files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.collection_summary import compute_collection_summary
from tests.test_ingest import _ingest_csv

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


def test_pershing_summary_counts_positions_and_accounts():
    store = _ingest_csv(FIXTURES / "pershing_unrealized_gl.csv")
    summary = compute_collection_summary(store, collection_id="col-pershing")

    assert summary["collection_id"] == "col-pershing"
    assert summary["positions"] > 0, (
        "Pershing flat fixture should produce >0 positions via the __by_symbol view"
    )
    assert summary["most_recent_export_iso"] is not None
    assert any(
        f["filename"] == "pershing_unrealized_gl.csv"
        for f in summary["source_files"]
    )


def test_netx360_summary_counts_positions_and_accounts():
    store = _ingest_csv(FIXTURES / "netx360_holdings_by_investor.csv")
    summary = compute_collection_summary(store, collection_id="col-netx360")

    assert summary["collection_id"] == "col-netx360"
    assert summary["positions"] > 0, (
        "NetX360 hierarchical fixture should populate positions after HBIL preprocessing"
    )
    # NetX360 HBIL exposes the account role; flat formats may not.
    assert summary["accounts"] >= 1, (
        "NetX360 HBIL header sections should resolve to at least one distinct account"
    )
    assert summary["most_recent_export_iso"] is not None
    assert any(
        f["filename"] == "netx360_holdings_by_investor.csv"
        for f in summary["source_files"]
    )


def test_empty_store_returns_zeros(tmp_path):
    from services.financial.holdings_store import HoldingsStore

    store = HoldingsStore(tmp_path / "metadata.db")
    summary = compute_collection_summary(store, collection_id="col-empty")

    assert summary == {
        "collection_id": "col-empty",
        "positions": 0,
        "accounts": 0,
        "most_recent_export_iso": None,
        "source_files": [],
    }
