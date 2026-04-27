"""Tests for market_data.enrich (v4.2 composite enrichment tool).

Run with:
    pytest tests/test_enrich_holdings.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeStore:
    def __init__(self, schema, rows):
        self._schema = schema
        self._rows = rows

    def list_tables(self):
        roles = {c["sql_name"]: c["role"] for c in (self._schema or {}).get("columns", []) if c.get("role")}
        return [{
            "table_name": self._schema["table_name"],
            "financial_roles": roles,
        }] if self._schema else []

    def get_schema(self, identifier):
        return self._schema

    def execute_query(self, sql, max_rows=5000):
        return {"columns": ["sym"], "rows": [[s] for s in self._rows], "row_count": len(self._rows), "truncated": False}


def _holdings_schema():
    return {
        "table_name": "csv_data_abc",
        "columns": [
            {"sql_name": "symbol", "name": "Symbol", "role": "ticker", "type": "TEXT"},
            {"sql_name": "mkt_val", "name": "Market Value", "role": "market_value", "type": "REAL"},
        ],
    }


def test_no_holdings_table():
    from services.market_data.enrich import enrich_holdings
    store = _FakeStore(schema=None, rows=[])
    result = enrich_holdings(
        collection_id="c1",
        store_factory=lambda cid: store,
        classification_fn=lambda s: {},
        profile_fn=lambda s: {},
    )
    assert result["error"] == "no_holdings_table"


def test_no_symbols():
    from services.market_data.enrich import enrich_holdings
    store = _FakeStore(schema=_holdings_schema(), rows=[])
    result = enrich_holdings(
        collection_id="c1",
        store_factory=lambda cid: store,
        classification_fn=lambda s: {},
        profile_fn=lambda s: {},
    )
    assert result["error"] == "no_symbols"


def test_invalid_include():
    from services.market_data.enrich import enrich_holdings
    result = enrich_holdings(
        collection_id="c1",
        include=["bogus"],
        store_factory=lambda cid: _FakeStore(schema=_holdings_schema(), rows=["AAPL"]),
        classification_fn=lambda s: {},
        profile_fn=lambda s: {},
    )
    assert result["error"] == "invalid_include"


def test_default_include_is_classification_and_profile():
    from services.market_data.enrich import enrich_holdings

    calls = {"classification": [], "profile": [], "events": [], "price": []}
    store = _FakeStore(schema=_holdings_schema(), rows=["AAPL", "MSFT", "AAPL"])

    result = enrich_holdings(
        collection_id="c1",
        store_factory=lambda cid: store,
        classification_fn=lambda s: (calls["classification"].append(s) or {"sector": "Tech"}),
        profile_fn=lambda s: (calls["profile"].append(s) or {"ceo": {"name": "Tim"}}),
        events_fn=lambda s, **kw: (calls["events"].append(s) or {}),
        price_fn=lambda s, **kw: (calls["price"].append(s) or {}),
    )

    assert result["symbol_count"] == 2  # dedup
    assert sorted(calls["classification"]) == ["AAPL", "MSFT"]
    assert sorted(calls["profile"]) == ["AAPL", "MSFT"]
    assert calls["events"] == []
    assert calls["price"] == []
    assert result["holdings"][0]["classification"]["sector"] == "Tech"


def test_opt_in_events_and_price():
    from services.market_data.enrich import enrich_holdings

    calls = {"events": [], "price": []}
    store = _FakeStore(schema=_holdings_schema(), rows=["AAPL"])

    enrich_holdings(
        collection_id="c1",
        include=["events", "price_1y"],
        store_factory=lambda cid: store,
        classification_fn=lambda s: {},
        profile_fn=lambda s: {},
        events_fn=lambda s, **kw: (calls["events"].append((s, kw.get("since"))) or {}),
        price_fn=lambda s, **kw: (calls["price"].append((s, kw.get("start"))) or {}),
    )

    assert len(calls["events"]) == 1
    assert calls["events"][0][0] == "AAPL"
    assert calls["events"][0][1] is not None  # since set to ~1y ago
    assert len(calls["price"]) == 1


def test_max_symbols_truncates():
    from services.market_data.enrich import enrich_holdings

    rows = [f"SYM{i}" for i in range(10)]
    store = _FakeStore(schema=_holdings_schema(), rows=rows)

    result = enrich_holdings(
        collection_id="c1",
        max_symbols=3,
        store_factory=lambda cid: store,
        classification_fn=lambda s: {},
        profile_fn=lambda s: {},
    )

    assert result["symbol_count"] == 3
    assert result["truncated"] is True
