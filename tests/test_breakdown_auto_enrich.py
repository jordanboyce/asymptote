"""Regression test for the sector-breakdown auto-enrich fallback in
compute_portfolio_metric.

When a Pershing UGL CSV lands without a sector column but with ticker
symbols, `breakdown_by_sector` should fall through to
`get_security_classification` and aggregate by the resulting sector,
rather than failing with "No column with role 'sector' detected".
"""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.financial.metrics import _breakdown_via_classification


def _build_holdings(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.execute(
        'CREATE TABLE "holdings" ('
        '"Symbol" TEXT, "Market_Value" REAL'
        ')'
    )
    conn.executemany(
        'INSERT INTO "holdings" ("Symbol", "Market_Value") VALUES (?, ?)',
        [
            ("TXN", 1_030_704),
            ("AXP", 783_750),
            ("GLW", 684_960),
            ("GLW", 513_720),
            ("GLW", 171_240),
            ("PKST", 250_000),
        ],
    )
    conn.commit()
    return {
        "filename": "Unrealized+Gain+Loss.csv",
        "table_name": "holdings",
        "columns": [
            {"sql_name": "Symbol", "role": "ticker"},
            {"sql_name": "Market_Value", "role": "market_value"},
        ],
    }


def _classify(symbol: str) -> dict[str, Any]:
    sectors = {
        "TXN": "Technology",
        "AXP": "Financial Services",
        "GLW": "Technology",
    }
    if symbol == "PKST":
        return {"error": "symbol_not_found", "message": "nope", "symbol": symbol}
    return {"sector": sectors.get(symbol), "asset_class": "equity"}


def test_sector_breakdown_via_classification_groups_correctly():
    conn = sqlite3.connect(":memory:")
    try:
        schema = _build_holdings(conn)
        role_to_col = {"ticker": "Symbol", "market_value": "Market_Value"}
        result = _breakdown_via_classification(
            conn, schema, "holdings", "sector", role_to_col,
            classify_fn=_classify,
        )
    finally:
        conn.close()

    assert result["metric"] == "breakdown_by_sector"
    assert result["enriched_via"] == "classification"
    groups = {g["group"]: g for g in result["groups"]}

    # GLW lots roll up: 684_960 + 513_720 + 171_240 = 1,369,920 → Technology
    # TXN: 1,030,704 → Technology, total Technology = 2,400,624
    assert groups["Technology"]["total"] == pytest.approx(2_400_624)
    assert groups["Financial Services"]["total"] == pytest.approx(783_750)
    # PKST is unclassified, lands in fallback bucket
    assert groups["Unclassified"]["total"] == pytest.approx(250_000)
    assert result["classified_symbols"] == 3  # TXN, AXP, GLW (each distinct symbol)
    assert result["unclassified_symbols"] == 1


def test_asset_class_breakdown_via_classification():
    conn = sqlite3.connect(":memory:")
    try:
        schema = _build_holdings(conn)
        role_to_col = {"ticker": "Symbol", "market_value": "Market_Value"}
        result = _breakdown_via_classification(
            conn, schema, "holdings", "asset_class", role_to_col,
            classify_fn=_classify,
        )
    finally:
        conn.close()

    assert result["metric"] == "breakdown_by_asset_class"
    groups = {g["group"]: g for g in result["groups"]}
    # TXN, AXP, and the three GLW rows all classified as 'equity'
    assert "equity" in groups
    assert groups["equity"]["count"] == 3
    # PKST falls into Unclassified
    assert groups["Unclassified"]["total"] == pytest.approx(250_000)


def test_breakdown_note_explains_provenance():
    conn = sqlite3.connect(":memory:")
    try:
        schema = _build_holdings(conn)
        role_to_col = {"ticker": "Symbol", "market_value": "Market_Value"}
        result = _breakdown_via_classification(
            conn, schema, "holdings", "sector", role_to_col,
            classify_fn=_classify,
        )
    finally:
        conn.close()

    assert "note" in result
    assert "get_security_classification" in result["note"]
