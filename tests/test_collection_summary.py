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


def test_non_financial_table_does_not_inflate_positions(tmp_path):
    """Generic tabular data — PDF tables, log dumps, anything without a
    ticker/account/holdings-measure column — also lives in HoldingsStore but
    must not count toward Position totals.

    Regression: a 16-document technical-PDF collection (engineering documents
    with extracted rule-keyword and lookup tables) was reporting "407 positions"
    in the Collection card, because compute_collection_summary fell back to
    raw row_count for every typed table regardless of whether it carried
    holdings roles. The Studio's Portfolio snapshot then offered to
    "Generate Meeting Brief" against a Collection that had no holdings at all.
    """
    from services.financial.holdings_store import HoldingsStore

    store = HoldingsStore(tmp_path / "metadata.db")

    # Engineering-style table: looks tabular, has many rows, but no column
    # the role detector recognises as a holdings field.
    store.create_table(
        document_id="doc-generic-1",
        filename="engineering_rule_keywords.csv",
        columns=["rule_id", "keyword", "category"],
        rows=[
            {"rule_id": f"R{i}", "keyword": f"kw_{i}", "category": "logic"}
            for i in range(50)
        ],
        sheet_name="",
    )

    summary = compute_collection_summary(store, collection_id="col-generic")

    assert summary["positions"] == 0, (
        "Non-holdings tables (no ticker/account/value role) must not "
        "contribute to the Position count"
    )
    assert summary["accounts"] == 0
    # most_recent_export_iso is meant to answer 'when was the last brokerage
    # export?' — a generic PDF table ingest shouldn't set it.
    assert summary["most_recent_export_iso"] is None
    # The file should still appear in source_files so downstream UI knows
    # the collection has tabular data, just not financial tabular data.
    assert any(
        f["filename"] == "engineering_rule_keywords.csv"
        for f in summary["source_files"]
    )
