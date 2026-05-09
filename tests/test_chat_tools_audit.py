"""Audit the chat agent toolset against the five "no special syntax" question
types listed in R5.5 of the advisor-desktop-ux spec.

The chat orchestrator (services.chat.context + services.chat.engine) hands
the LLM a tool catalog and a fixture-shaped portfolio. R5.5 says an advisor
should be able to ask any of these questions in plain language and get an
answer:

    1. position lookup by ticker or name
    2. account balance queries
    3. sector / asset-class allocation breakdown
    4. unrealized gain/loss for a position or account
    5. cash position summary

This file does not test the LLM's tool selection — that's a runtime concern.
It pins the underlying tools in place so a refactor can't silently remove the
piece each question type relies on. Each test calls the same tool the agent
would call (compute_financial_metric, store.execute_query, etc.) against the
Pershing snapshot (or NetX360 where account roles are required) and asserts
the result is plausibly an answer to that question.

If any of these tests breaks the chat layer for advisors loses one of the
five canonical workflows — investigate before "fixing" the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.financial.metrics import compute_financial_metric
from tests.test_ingest import _ingest_csv

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


@pytest.fixture(scope="module")
def pershing_store():
    return _ingest_csv(FIXTURES / "pershing_unrealized_gl.csv")


@pytest.fixture(scope="module")
def netx360_store():
    return _ingest_csv(FIXTURES / "netx360_holdings_by_investor.csv")


# ---------------------------------------------------------------------------
# 1. Position lookup by ticker/name → query_table on the __by_symbol view
# ---------------------------------------------------------------------------

def test_position_lookup_by_ticker(pershing_store):
    """'How much TXN do we hold?' — query_table against the rolled-up view.

    The __by_symbol view exposes the rolled-up identifier under
    `Security_Identifier` (Pershing populates that column with the ticker
    when there's no separate CUSIP). The agent's get_table_schema /
    list_tables output advertises this column for the by_symbol view, so a
    "find TXN" query lands on it.
    """
    result = pershing_store.execute_query(
        'SELECT "Security_Identifier", "Quantity", "Market_Value" '
        'FROM "csv_data_test_doc__by_symbol" '
        'WHERE "Security_Identifier" = \'TXN\''
    )
    assert result["row_count"] == 1, "expected exactly one rolled-up TXN position"
    [identifier, quantity, market_value] = result["rows"][0]
    assert identifier == "TXN"
    assert quantity == 4800.0
    assert market_value == pytest.approx(1_030_704.0)


def test_position_lookup_rolls_up_lots(pershing_store):
    """PFE has multiple lots in the source file. Position lookup should return
    the rolled-up market value, not just one lot's row."""
    result = pershing_store.execute_query(
        'SELECT "Security_Identifier", "Market_Value", "lot_count" '
        'FROM "csv_data_test_doc__by_symbol" '
        'WHERE "Security_Identifier" = \'PFE\''
    )
    assert result["row_count"] == 1
    [_identifier, market_value, lot_count] = result["rows"][0]
    assert lot_count >= 2, "PFE should be rolled up from multiple lots"
    # Two PFE lots survive the view's Original_Quantity filter
    # (4800 @ 26.92 + 1200 @ 26.92 ≈ $161,520).
    assert market_value > 100_000


# ---------------------------------------------------------------------------
# 2. Account balance → aggregate_table on the account-role column
# ---------------------------------------------------------------------------

def test_account_balance_breakdown(netx360_store):
    """'What's the balance in each account?' — aggregate_table sums market
    value grouped by Account_Name. NetX360 HBIL is the realistic shape; the
    Pershing flat fixture has no account column."""
    schema = netx360_store.get_schema("netx360_holdings_by_investor.csv")
    table = schema["table_name"]
    # The agent's aggregate_table tool emits exactly this SQL shape.
    result = netx360_store.execute_query(
        f'SELECT "Account_Name", SUM("Value") AS result '
        f'FROM "{table}" GROUP BY "Account_Name" ORDER BY result DESC'
    )
    assert result["row_count"] >= 2, "NetX360 fixture has two accounts"
    accounts = {row[0]: row[1] for row in result["rows"]}
    assert "John Q. Sample" in accounts
    assert "Jane Q. Sample" in accounts
    assert accounts["John Q. Sample"] == pytest.approx(766_852.50)
    assert accounts["Jane Q. Sample"] == pytest.approx(387_794.80)


# ---------------------------------------------------------------------------
# 3. Sector / asset-class allocation → compute_portfolio_metric
# ---------------------------------------------------------------------------

def test_asset_class_allocation_breakdown(pershing_store):
    """'How is the portfolio allocated?' — Pershing carries an asset_class
    column (Security Type), so breakdown_by_asset_class returns groups
    directly from the file without classification enrichment."""
    result = compute_financial_metric(
        pershing_store, "pershing_unrealized_gl.csv", "breakdown_by_asset_class",
    )
    groups = {g["group"]: g for g in result["groups"]}
    # Fixture has Common Stocks (TXN+PFE lots) and Money Market Funds (AIGI).
    assert "Common Stocks" in groups
    assert "Money Market Funds" in groups
    common_total = groups["Common Stocks"]["total"]
    cash_total = groups["Money Market Funds"]["total"]
    assert common_total > cash_total > 0
    # Percentages should add up roughly to 100%.
    pct_sum = sum(g["pct"] for g in result["groups"] if g.get("pct") is not None)
    assert 99 <= pct_sum <= 101


def test_sector_allocation_falls_back_when_no_sector_column(pershing_store):
    """Pershing has no sector column, only asset_class. compute_portfolio_metric
    with breakdown_by_sector should attempt classification and fall back
    gracefully — it must not raise. (The classifier is offline in tests, so
    we accept any of the three documented outcomes: a result with groups, a
    `fallback_from`-marked asset_class breakdown, or a clear ValueError that
    instructs the agent to use enrich_holdings.)"""
    try:
        result = compute_financial_metric(
            pershing_store, "pershing_unrealized_gl.csv", "breakdown_by_sector",
        )
    except ValueError as e:
        # Acceptable — the metric raises with an instruction the agent can read.
        assert "sector" in str(e).lower() or "classif" in str(e).lower()
        return
    # If it didn't raise, it returned either a real breakdown or the
    # asset_class fallback. Either way `groups` must be a list.
    assert isinstance(result.get("groups"), list)


# ---------------------------------------------------------------------------
# 4. Unrealized P&L → compute_portfolio_metric largest_gains / total_pnl
# ---------------------------------------------------------------------------

def test_largest_gains(pershing_store):
    """'Which positions have the biggest gains?' — largest_gains returns a
    ranked list ordered by pnl DESC."""
    result = compute_financial_metric(
        pershing_store, "pershing_unrealized_gl.csv", "largest_gains", limit=5,
    )
    rows = result["rows"]
    assert len(rows) > 0, "fixture has positive G/L positions; expected non-empty"
    # Rows are [name_or_ticker, pnl] — pnl is the last column.
    pnls = [r[-1] for r in rows]
    assert all(pnls[i] >= pnls[i + 1] for i in range(len(pnls) - 1)), (
        "largest_gains must be sorted DESC by pnl"
    )
    assert pnls[0] > 0, "top gain should be positive"


def test_total_unrealized_pnl(pershing_store):
    """Aggregate unrealized P&L across the full portfolio."""
    result = compute_financial_metric(
        pershing_store, "pershing_unrealized_gl.csv", "total_pnl",
    )
    # The Pershing fixture has all-positive G/L on the by_symbol rollup —
    # fixture realism aside, the assertion is "we got a number, not None".
    assert result["value"] is not None
    assert isinstance(result["value"], (int, float))


# ---------------------------------------------------------------------------
# 5. Cash position summary → query_table filtered on asset_class
# ---------------------------------------------------------------------------

def test_cash_position_summary(pershing_store):
    """'How much cash is in the portfolio?' — query_table filters the
    rolled-up view by Money Market Funds. The Pershing fixture has one MMF
    sleeve (AIGI FUND, $359,712.72) which is the canonical "cash" answer."""
    result = pershing_store.execute_query(
        'SELECT "Security_Description", "Market_Value" '
        'FROM "csv_data_test_doc__by_symbol" '
        'WHERE "Security_Type" = \'Money Market Funds\''
    )
    assert result["row_count"] >= 1, "expected at least one cash sleeve"
    total_cash = sum(r[1] for r in result["rows"])
    assert total_cash == pytest.approx(359_712.72)


def test_cash_position_in_netx360(netx360_store):
    """NetX360 marks cash with asset_class 'MM'; same query pattern works."""
    schema = netx360_store.get_schema("netx360_holdings_by_investor.csv")
    table = schema["table_name"]
    result = netx360_store.execute_query(
        f'SELECT "Asset", "Value" FROM "{table}" WHERE "Asset_Type" = \'MM\''
    )
    assert result["row_count"] >= 1
    total_cash = sum(r[1] for r in result["rows"])
    assert total_cash == pytest.approx(52_340.0)
