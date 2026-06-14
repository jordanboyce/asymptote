"""Regression tests for v4.3 MCP surface polish.

Covers:
  - Per-collection guide memory (DB column + helper truncation)
  - find_in_documents literal-excerpt matcher
  - Structured rows renderer (render_table_as_rows)
  - identifier_type parameter on StructuredStore.get_schema
  - Numeric / exact-match intent detection in search_collection
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.app_database import SQLiteBackend
from services.mcp_server import (
    _detect_exact_match_intent,
    _detect_numeric_intent,
    _find_literal_excerpt,
    _guide_summary,
)
from services.structured_chat import render_table_as_rows
from services.structured_store import StructuredStore


def test_guide_column_roundtrip(tmp_path: Path):
    db = SQLiteBackend(tmp_path / "app.db")
    cid = db.create_collection(name="Jane Smith Portfolio")

    db.update_collection(cid, guide="Currency: USD. 'Jane' = Jane Smith.")
    assert db.get_collection(cid)["guide"] == "Currency: USD. 'Jane' = Jane Smith."

    # Unrelated update leaves guide intact
    db.update_collection(cid, description="edited")
    coll = db.get_collection(cid)
    assert coll["guide"] == "Currency: USD. 'Jane' = Jane Smith."
    assert coll["description"] == "edited"


def test_guide_summary_truncates_and_rejects_empty():
    assert _guide_summary(None) is None
    assert _guide_summary("   ") is None
    assert _guide_summary("short note") == "short note"

    long = "a" * 600
    summary = _guide_summary(long)
    assert summary is not None
    assert len(summary) <= 500
    assert summary.endswith("...")


def test_find_literal_excerpt_case_handling():
    hit = _find_literal_excerpt("Hello World", "WORLD", case_insensitive=True)
    assert hit is not None
    offset, excerpt = hit
    assert offset == 6
    assert "«World»" in excerpt

    assert _find_literal_excerpt("Hello World", "WORLD", case_insensitive=False) is None

    long_text = "x" * 200 + "NEEDLE" + "y" * 200
    hit = _find_literal_excerpt(long_text, "NEEDLE", case_insensitive=False)
    assert hit is not None
    _, excerpt = hit
    assert excerpt.startswith("...") and excerpt.endswith("...")
    assert "«NEEDLE»" in excerpt


def test_render_table_as_rows_returns_structured_output(tmp_path: Path):
    store = StructuredStore(tmp_path / "structured.db")
    store.create_table(
        document_id="doc1",
        filename="portfolio.csv",
        columns=["Ticker", "Value"],
        rows=[
            {"Ticker": "AAPL", "Value": 100},
            {"Ticker": "MSFT", "Value": 200},
        ],
    )

    info = store.list_tables()[0]
    rendered = render_table_as_rows(store, info)
    assert rendered is not None
    assert "__row_number" not in rendered["columns"]
    assert "Ticker" in rendered["columns"]
    assert len(rendered["rows"]) == 2
    ticker_idx = rendered["columns"].index("Ticker")
    tickers = sorted(row[ticker_idx] for row in rendered["rows"])
    assert tickers == ["AAPL", "MSFT"]


def test_identifier_type_restricts_lookup(tmp_path: Path):
    store = StructuredStore(tmp_path / "structured.db")
    store.create_table(
        document_id="doc-abc",
        filename="portfolio.csv",
        columns=["Ticker"],
        rows=[{"Ticker": "AAPL"}],
    )

    assert store.get_schema("portfolio.csv") is not None
    assert store.get_schema("portfolio.csv", identifier_type="filename") is not None
    # Same literal but wrong interpretation → no match
    assert store.get_schema("portfolio.csv", identifier_type="document_id") is None
    # Correct type for document_id
    assert store.get_schema("doc-abc", identifier_type="document_id") is not None

    with pytest.raises(ValueError):
        store.get_schema("anything", identifier_type="bogus")


@pytest.mark.parametrize(
    "query",
    [
        "what is the total market value",
        "average price per share",
        "top holdings by value",
        "how many positions do we have",
        "breakdown by sector",
    ],
)
def test_numeric_intent_detected(query: str):
    assert _detect_numeric_intent(query)


@pytest.mark.parametrize(
    "query",
    [
        "what does this document say about risk",
        "summarize the client's goals",
        "explain how dividends work",
    ],
)
def test_narrative_queries_not_flagged_as_numeric(query: str):
    assert not _detect_numeric_intent(query)


@pytest.mark.parametrize(
    "query",
    [
        'search for "Hendersons"',
        "find the verbatim phrase",
        "find the exact string AAPL",
    ],
)
def test_exact_match_intent_detected(query: str):
    assert _detect_exact_match_intent(query)
