"""Tests for services.brief_generator.generate_meeting_brief."""

import pytest

from services.brief_generator import generate_meeting_brief
from services.financial.holdings_store import HoldingsStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_HOLDINGS = [
    {
        "Ticker": "AAPL",
        "Name": "Apple Inc",
        "Market Value": "$150,000.00",
        "Cost Basis": "$100,000.00",
        "Sector": "Technology",
        "Account": "Taxable",
    },
    {
        "Ticker": "MSFT",
        "Name": "Microsoft Corp",
        "Market Value": "$80,000.00",
        "Cost Basis": "$60,000.00",
        "Sector": "Technology",
        "Account": "Taxable",
    },
    {
        "Ticker": "JNJ",
        "Name": "Johnson & Johnson",
        "Market Value": "$40,000.00",
        "Cost Basis": "$55,000.00",  # underwater
        "Sector": "Healthcare",
        "Account": "IRA",
    },
    {
        "Ticker": "XOM",
        "Name": "Exxon Mobil",
        "Market Value": "$30,000.00",
        "Cost Basis": "$28,000.00",
        "Sector": "Energy",
        "Account": "IRA",
    },
    {
        "Ticker": "CASH",
        "Name": "Money Market Fund",
        "Market Value": "$75,000.00",
        "Cost Basis": "$75,000.00",
        "Sector": "Cash",
        "Account": "Taxable",
    },
]

_COLUMNS = ["Ticker", "Name", "Market Value", "Cost Basis", "Sector", "Account"]


@pytest.fixture
def store(tmp_path):
    s = HoldingsStore(tmp_path / "metadata.db")
    s.create_table(
        document_id="doc-holdings",
        filename="portfolio.csv",
        columns=_COLUMNS,
        rows=_HOLDINGS,
    )
    return s


@pytest.fixture
def brief(store):
    return generate_meeting_brief(store, collection_id="test-collection")


# ---------------------------------------------------------------------------
# household_summary
# ---------------------------------------------------------------------------


def test_brief_has_all_sections(brief):
    for key in (
        "household_summary",
        "accounts",
        "top_positions",
        "tax_loss_candidates",
        "concentration_alerts",
        "cash_drag_alerts",
        "sector_allocation",
        "generated_at",
        "tables_scanned",
        "collection_id",
    ):
        assert key in brief, f"Missing section: {key}"


def test_household_total_market_value(brief):
    # 150k + 80k + 40k + 30k + 75k = 375k
    assert brief["household_summary"]["total_market_value"] == pytest.approx(375_000, rel=0.01)


def test_household_cost_basis(brief):
    # 100k + 60k + 55k + 28k + 75k = 318k
    assert brief["household_summary"]["total_cost_basis"] == pytest.approx(318_000, rel=0.01)


def test_household_unrealized_pnl(brief):
    # 375k - 318k = 57k
    assert brief["household_summary"]["total_unrealized_pnl"] == pytest.approx(57_000, rel=0.01)


def test_tables_scanned_is_one(brief):
    assert brief["tables_scanned"] == 1


def test_collection_id_passed_through(brief):
    assert brief["collection_id"] == "test-collection"


def test_generated_at_is_iso(brief):
    ts = brief["generated_at"]
    from datetime import datetime
    # Should parse without error
    datetime.fromisoformat(ts)


# ---------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------


def test_accounts_present(brief):
    assert len(brief["accounts"]) == 2


def test_accounts_by_market_value_desc(brief):
    mvs = [a["market_value"] for a in brief["accounts"]]
    assert mvs == sorted(mvs, reverse=True)


def test_taxable_account_value(brief):
    taxable = next(a for a in brief["accounts"] if a["account"] == "Taxable")
    # AAPL 150k + MSFT 80k + Money Market 75k = 305k
    assert taxable["market_value"] == pytest.approx(305_000, rel=0.01)


# ---------------------------------------------------------------------------
# top_positions
# ---------------------------------------------------------------------------


def test_top_positions_ordered_desc(brief):
    mvs = [p["market_value"] for p in brief["top_positions"]]
    assert mvs == sorted(mvs, reverse=True)


def test_top_positions_first_is_aapl(brief):
    assert brief["top_positions"][0]["name"] in ("Apple Inc", "AAPL")


def test_top_n_limit(store):
    b = generate_meeting_brief(store, thresholds={"top_n": 2})
    assert len(b["top_positions"]) == 2


# ---------------------------------------------------------------------------
# tax_loss_candidates
# ---------------------------------------------------------------------------


def test_jnj_is_tax_loss_candidate(brief):
    names = [c.get("name") or c.get("ticker") for c in brief["tax_loss_candidates"]]
    assert any("Johnson" in str(n) or n == "JNJ" for n in names)


def test_tax_loss_unrealized_loss_positive(brief):
    for c in brief["tax_loss_candidates"]:
        assert c["unrealized_loss"] > 0


def test_tax_loss_threshold_filters(store):
    # JNJ loss is 55k - 40k = 15k. A threshold of 20k should exclude it.
    b = generate_meeting_brief(store, thresholds={"tax_loss_min": 20_000})
    assert len(b["tax_loss_candidates"]) == 0


def test_tax_loss_default_threshold_includes_jnj(brief):
    # Default 500 — JNJ 15k loss should appear
    assert len(brief["tax_loss_candidates"]) >= 1


# ---------------------------------------------------------------------------
# concentration_alerts
# ---------------------------------------------------------------------------


def test_aapl_concentration_alert(brief):
    # AAPL is 150k / 375k = 40%, well above 10%
    names = [a["name"] for a in brief["concentration_alerts"]]
    assert any("Apple" in str(n) or n == "AAPL" for n in names)


def test_concentration_pct_computed(brief):
    for alert in brief["concentration_alerts"]:
        assert "pct_of_portfolio" in alert
        assert alert["pct_of_portfolio"] > 0


def test_concentration_threshold_respected(store):
    # With 50% threshold only AAPL (40%) should not trigger
    b = generate_meeting_brief(store, thresholds={"concentration_pct": 50.0})
    assert len(b["concentration_alerts"]) == 0


# ---------------------------------------------------------------------------
# cash_drag_alerts
# ---------------------------------------------------------------------------


def test_money_market_cash_drag(brief):
    names = [a["name"] for a in brief["cash_drag_alerts"]]
    assert any("Money Market" in str(n) for n in names)


def test_cash_drag_threshold_filters(store):
    # Money Market is 75k. Threshold 100k should exclude it.
    b = generate_meeting_brief(store, thresholds={"cash_drag_min": 100_000})
    assert len(b["cash_drag_alerts"]) == 0


# ---------------------------------------------------------------------------
# sector_allocation
# ---------------------------------------------------------------------------


def test_sector_allocation_non_empty(brief):
    assert len(brief["sector_allocation"]) >= 1


def test_technology_sector_largest(brief):
    # AAPL + MSFT = 230k > everything else
    tech = next(
        (s for s in brief["sector_allocation"] if s["sector"] == "Technology"), None
    )
    assert tech is not None
    assert tech["market_value"] == pytest.approx(230_000, rel=0.01)


def test_sector_pct_of_portfolio_set(brief):
    for s in brief["sector_allocation"]:
        assert "pct_of_portfolio" in s
        assert 0 < s["pct_of_portfolio"] <= 100


def test_sector_pcts_sum_to_100(brief):
    total_pct = sum(s["pct_of_portfolio"] for s in brief["sector_allocation"])
    assert total_pct == pytest.approx(100.0, abs=0.5)


# ---------------------------------------------------------------------------
# Empty store
# ---------------------------------------------------------------------------


def test_empty_store_returns_valid_brief(tmp_path):
    store = HoldingsStore(tmp_path / "empty.db")
    brief = generate_meeting_brief(store)
    assert brief["tables_scanned"] == 0
    assert brief["household_summary"]["total_market_value"] == 0.0
    assert brief["top_positions"] == []
    assert brief["tax_loss_candidates"] == []
    assert brief["concentration_alerts"] == []
    assert brief["cash_drag_alerts"] == []


# ---------------------------------------------------------------------------
# Multi-table household merge
# ---------------------------------------------------------------------------


def test_multi_table_household_merge(tmp_path):
    store = HoldingsStore(tmp_path / "multi.db")

    store.create_table(
        document_id="doc-a",
        filename="acct_a.csv",
        columns=["Name", "Market Value", "Cost Basis"],
        rows=[{"Name": "AAPL", "Market Value": "$100,000", "Cost Basis": "$80,000"}],
    )
    store.create_table(
        document_id="doc-b",
        filename="acct_b.csv",
        columns=["Name", "Market Value", "Cost Basis"],
        rows=[{"Name": "MSFT", "Market Value": "$50,000", "Cost Basis": "$40,000"}],
    )

    brief = generate_meeting_brief(store)
    assert brief["tables_scanned"] == 2
    assert brief["household_summary"]["total_market_value"] == pytest.approx(150_000, rel=0.01)
    assert brief["household_summary"]["total_cost_basis"] == pytest.approx(120_000, rel=0.01)


# ── cash detection ────────────────────────────────────────────────────────


class TestCashDetection:
    """``_is_cash_like`` has to read the asset class, not just the name.

    Pershing names its money-market sweep "AIGI FUND" and puts
    "Money Market Funds" in Security Type. Matching on the name alone missed
    it on every such export, which silently disabled the client's cash bands
    and reported the sweep as a concentration breach.
    """

    def test_name_alone_still_matches(self):
        from services.brief_generator import _is_cash_like
        assert _is_cash_like("Schwab Cash Sweep")
        assert _is_cash_like("FIDELITY MONEY MARKET FUND")

    def test_asset_class_matches_when_the_name_does_not(self):
        from services.brief_generator import _is_cash_like
        assert not _is_cash_like("AIGI FUND")
        assert _is_cash_like("AIGI FUND", "Money Market Funds")

    def test_ordinary_equity_is_not_cash(self):
        from services.brief_generator import _is_cash_like
        assert not _is_cash_like("Texas Instruments Inc", "Common Stocks")
        assert not _is_cash_like("Vanguard Total Bond Market ETF", "Corporate Bonds")

    def test_missing_asset_class_is_tolerated(self):
        from services.brief_generator import _is_cash_like
        assert not _is_cash_like("AIGI FUND", None)
        assert not _is_cash_like("", None)
