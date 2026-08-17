"""IPS drift — allocation vs targets, concentration, prohibited holdings, cash.

Two things are under test here beyond the arithmetic:

  * label matching between an IPS target and an export's asset-class values
    is a guess, and every guess must be auditable (`matched_by`) or reported
    as unmatched — never silently dropped;
  * partial coverage is not drift. If a third of the book has no asset-class
    label, the percentages are computed against a denominator the advisor
    didn't fully see, and `authoritative` must say so.
"""

from __future__ import annotations

import pytest

from models.schemas import (
    AllocationTarget,
    ClientProfile,
    IPSTargets,
    LiquidityNeeds,
)
from services.financial.ips_drift import (
    check_cash_policy,
    check_concentration,
    check_prohibited_holdings,
    compute_allocation_drift,
)


def _profile(**ips_kwargs) -> ClientProfile:
    return ClientProfile(ips=IPSTargets(**ips_kwargs))


def _60_40() -> ClientProfile:
    return _profile(
        allocation_targets=[
            AllocationTarget(asset_class="Equities", target_pct=60),
            AllocationTarget(asset_class="Fixed Income", target_pct=40),
        ],
        rebalance_band_pct=5,
    )


def _line(result, asset_class):
    return next(ln for ln in result["lines"] if ln["asset_class"] == asset_class)


# ── allocation drift ──────────────────────────────────────────────────────


def test_portfolio_on_target_is_in_band():
    result = compute_allocation_drift(_60_40(), [
        {"label": "Equities", "market_value": 600_000},
        {"label": "Fixed Income", "market_value": 400_000},
    ])

    assert result["authoritative"] is True
    assert result["totals"]["lines_out_of_band"] == 0
    eq = _line(result, "Equities")
    assert eq["actual_pct"] == 60.0
    assert eq["drift_pct"] == 0.0
    assert eq["status"] == "in_band"
    assert eq["to_target_dollars"] == 0.0


def test_drift_inside_the_band_is_not_flagged():
    """63% against a 60% target with a 5-point band is fine — don't cry wolf."""
    result = compute_allocation_drift(_60_40(), [
        {"label": "Equities", "market_value": 630_000},
        {"label": "Fixed Income", "market_value": 370_000},
    ])
    assert _line(result, "Equities")["status"] == "in_band"
    assert result["totals"]["lines_out_of_band"] == 0


def test_drift_outside_the_band_is_flagged_with_dollars_to_trade():
    result = compute_allocation_drift(_60_40(), [
        {"label": "Equities", "market_value": 700_000},
        {"label": "Fixed Income", "market_value": 300_000},
    ])

    eq = _line(result, "Equities")
    assert eq["actual_pct"] == 70.0
    assert eq["drift_pct"] == 10.0
    assert eq["status"] == "over"
    # 10 points over on a $1M book = sell $100k to return to target.
    assert eq["to_target_dollars"] == -100_000.0

    fi = _line(result, "Fixed Income")
    assert fi["status"] == "under"
    assert fi["to_target_dollars"] == 100_000.0
    assert result["totals"]["lines_out_of_band"] == 2


def test_explicit_min_max_overrides_the_default_band():
    profile = _profile(
        allocation_targets=[
            AllocationTarget(asset_class="Equities", target_pct=60, min_pct=55, max_pct=75),
        ],
        rebalance_band_pct=1,
    )
    result = compute_allocation_drift(profile, [{"label": "Equities", "market_value": 700_000}], 1_000_000)
    eq = _line(result, "Equities")
    assert eq["band_min_pct"] == 55.0
    assert eq["band_max_pct"] == 75.0
    assert eq["status"] == "in_band"


def test_lines_are_ordered_by_absolute_drift():
    profile = _profile(allocation_targets=[
        AllocationTarget(asset_class="Equities", target_pct=50),
        AllocationTarget(asset_class="Fixed Income", target_pct=30),
        AllocationTarget(asset_class="Cash", target_pct=20),
    ])
    result = compute_allocation_drift(profile, [
        {"label": "Equities", "market_value": 520_000},
        {"label": "Fixed Income", "market_value": 130_000},
        {"label": "Cash", "market_value": 350_000},
    ])
    drifts = [abs(ln["drift_pct"]) for ln in result["lines"]]
    assert drifts == sorted(drifts, reverse=True)


# ── label matching ────────────────────────────────────────────────────────


def test_alias_match_is_recorded_as_an_alias_not_an_exact_match():
    """'Bonds' matching 'Fixed Income' is a claim — it has to be auditable."""
    result = compute_allocation_drift(_60_40(), [
        {"label": "Stocks", "market_value": 600_000},
        {"label": "Bonds", "market_value": 400_000},
    ])

    eq = _line(result, "Equities")
    assert eq["matched_by"] == "alias"
    assert eq["matched_labels"] == ["Stocks"]
    assert eq["actual_pct"] == 60.0
    assert _line(result, "Fixed Income")["matched_by"] == "alias"


def test_exact_match_is_labeled_exact():
    result = compute_allocation_drift(_60_40(), [
        {"label": "Equities", "market_value": 600_000},
        {"label": "Fixed Income", "market_value": 400_000},
    ])
    assert _line(result, "Equities")["matched_by"] == "exact"


def test_unmatched_target_is_reported_not_silently_zeroed():
    result = compute_allocation_drift(_60_40(), [
        {"label": "Equities", "market_value": 1_000_000},
    ])

    assert "Fixed Income" in result["unmatched_targets"]
    assert result["authoritative"] is False
    assert any("Fixed Income" in w for w in result["warnings"])
    fi = _line(result, "Fixed Income")
    assert fi["actual_pct"] == 0.0
    assert fi["matched_by"] is None


def test_holdings_with_no_matching_target_are_surfaced():
    """A bucket the IPS never mentioned still counts against the denominator."""
    result = compute_allocation_drift(_60_40(), [
        {"label": "Equities", "market_value": 550_000},
        {"label": "Fixed Income", "market_value": 350_000},
        {"label": "Crypto", "market_value": 100_000},
    ])

    assert result["unexpected_classes"] == ["Crypto"]
    assert any("Crypto" in w for w in result["warnings"])
    assert _line(result, "Equities")["actual_pct"] == 55.0


def test_matching_is_case_and_punctuation_insensitive():
    result = compute_allocation_drift(_60_40(), [
        {"label": "  EQUITIES  ", "market_value": 600_000},
        {"label": "fixed-income", "market_value": 400_000},
    ])
    assert result["unmatched_targets"] == []
    assert _line(result, "Equities")["actual_pct"] == 60.0


# ── coverage ──────────────────────────────────────────────────────────────


def test_unclassified_holdings_make_drift_non_authoritative():
    result = compute_allocation_drift(_60_40(), [
        {"label": "Equities", "market_value": 400_000},
        {"label": "Fixed Income", "market_value": 200_000},
        {"label": "Unclassified", "market_value": 400_000},
    ])

    assert result["coverage"]["classified_pct"] == 60.0
    assert result["coverage"]["unclassified_market_value"] == 400_000.0
    assert result["authoritative"] is False
    assert any("unclassified" in w.lower() for w in result["warnings"])


def test_partial_coverage_warning_names_the_dollar_amount():
    result = compute_allocation_drift(_60_40(), [
        {"label": "Equities", "market_value": 500_000},
        {"label": "Fixed Income", "market_value": 200_000},
        {"label": "Unknown", "market_value": 300_000},
    ])
    warning = next(w for w in result["warnings"] if "unclassified" in w.lower())
    assert "300,000" in warning


def test_targets_that_do_not_sum_to_100_are_reported_not_normalized():
    profile = _profile(allocation_targets=[
        AllocationTarget(asset_class="Equities", target_pct=60),
        AllocationTarget(asset_class="Fixed Income", target_pct=27),
    ])
    result = compute_allocation_drift(profile, [
        {"label": "Equities", "market_value": 600_000},
        {"label": "Fixed Income", "market_value": 400_000},
    ])

    assert result["totals"]["target_pct_sum"] == 87.0
    assert any("87" in w and "100" in w for w in result["warnings"])


# ── degenerate input ──────────────────────────────────────────────────────


def test_no_targets_returns_a_well_formed_cannot_compute():
    result = compute_allocation_drift(ClientProfile(), [{"label": "Equities", "market_value": 100}])
    assert result["lines"] == []
    assert result["authoritative"] is False
    assert any("no ips target" in w.lower() for w in result["warnings"])


def test_zero_market_value_refuses_rather_than_dividing_by_zero():
    result = compute_allocation_drift(_60_40(), [])
    assert result["lines"] == []
    assert result["authoritative"] is False
    assert any("zero or unavailable" in w.lower() for w in result["warnings"])


def test_non_numeric_market_values_are_skipped_not_coerced():
    result = compute_allocation_drift(_60_40(), [
        {"label": "Equities", "market_value": 600_000},
        {"label": "Fixed Income", "market_value": "See contract"},
    ], total_market_value=600_000)
    assert _line(result, "Fixed Income")["actual_pct"] == 0.0
    assert "Fixed Income" in result["unmatched_targets"]


# ── concentration ─────────────────────────────────────────────────────────


def test_concentration_is_not_checked_without_a_ceiling():
    result = check_concentration(ClientProfile(), [{"name": "AAPL", "market_value": 900_000}])
    assert result["checked"] is False
    assert result["breaches"] == []
    assert "no max single position" in result["note"].lower()


def test_concentration_breach_reports_the_trim_amount():
    profile = _profile(max_single_position_pct=10)
    result = check_concentration(profile, [
        {"name": "Apple Inc", "ticker": "AAPL", "market_value": 250_000},
        {"name": "Vanguard Total", "ticker": "VTI", "market_value": 750_000},
    ])

    assert result["checked"] is True
    assert result["count"] == 2
    top = result["breaches"][0]
    assert top["ticker"] == "VTI"
    assert top["pct_of_portfolio"] == 75.0
    assert top["excess_pct"] == 65.0
    assert top["trim_dollars"] == 650_000.0


def test_money_market_is_excluded_from_concentration_by_asset_class():
    """A cash sweep named "AIGI FUND" carries no cash keyword in its name.

    Real Pershing exports name the money-market sweep something like
    "AIGI FUND" and put "Money Market Funds" in the Security Type column.
    Cash detection used to read the name only, so the sweep was measured
    against the single-position ceiling and reported as a concentration
    breach -- a reading that is not meaningful for a cash vehicle.
    """
    profile = _profile(max_single_position_pct=15)
    result = check_concentration(profile, [
        {"name": "AIGI FUND", "ticker": "MMFAIGI", "market_value": 497_000,
         "asset_class": "Money Market Funds"},
        {"name": "Texas Instruments", "ticker": "TXN", "market_value": 520_000,
         "asset_class": "Common Stocks"},
        {"name": "Coca Cola", "ticker": "KO", "market_value": 1_363_000,
         "asset_class": "Common Stocks"},
    ])

    assert "MMFAIGI" not in [b["ticker"] for b in result["breaches"]]
    assert "AIGI FUND" in result["excluded_cash_positions"]


def test_position_under_the_ceiling_is_not_a_breach():
    profile = _profile(max_single_position_pct=30)
    result = check_concentration(profile, [
        {"name": "Apple", "ticker": "AAPL", "market_value": 250_000},
        {"name": "Other", "ticker": "VTI", "market_value": 750_000},
    ])
    assert [b["ticker"] for b in result["breaches"]] == ["VTI"]


# ── prohibited holdings ───────────────────────────────────────────────────


def test_prohibited_screen_does_not_run_without_rules():
    result = check_prohibited_holdings(ClientProfile(), [{"ticker": "XOM", "name": "Exxon"}])
    assert result["checked"] is False
    assert result["matches"] == []


def test_ticker_rule_matches_exactly_and_does_not_catch_lookalikes():
    """'XOM' must not flag 'XOMA Corp' — a screen that cries wolf stops being read."""
    profile = _profile(prohibited_holdings=["XOM"])
    result = check_prohibited_holdings(profile, [
        {"ticker": "XOM", "name": "Exxon Mobil", "market_value": 40_000},
        {"ticker": "XOMA", "name": "XOMA Corp", "market_value": 1_000},
    ])
    assert result["count"] == 1
    assert result["matches"][0]["ticker"] == "XOM"
    assert result["matches"][0]["matched_on"] == "ticker"
    assert result["matches"][0]["rule_kind"] == "symbol"


def test_symbol_rule_still_matches_a_name_on_a_word_boundary():
    """Some exports carry the symbol in the name field and nothing in ticker."""
    profile = _profile(prohibited_holdings=["XOM"])
    result = check_prohibited_holdings(profile, [
        {"ticker": None, "name": "XOM 4.5% NOTE", "market_value": 5_000},
    ])
    assert result["count"] == 1
    assert result["matches"][0]["matched_on"] == "name"


def test_name_rule_matches_as_a_substring():
    profile = _profile(prohibited_holdings=["tobacco"])
    result = check_prohibited_holdings(profile, [
        {"ticker": "MO", "name": "Altria Group (tobacco)", "market_value": 10_000},
        {"ticker": "VTI", "name": "Vanguard Total Market", "market_value": 10_000},
    ])
    assert result["count"] == 1
    assert result["matches"][0]["matched_on"] == "name"
    assert result["matches"][0]["rule"] == "tobacco"
    assert result["matches"][0]["rule_kind"] == "theme"


def test_blank_rules_are_ignored():
    profile = _profile(prohibited_holdings=["  ", ""])
    result = check_prohibited_holdings(profile, [{"ticker": "XOM", "name": "Exxon"}])
    assert result["checked"] is False


# ── cash policy ───────────────────────────────────────────────────────────


def test_cash_above_reserve_is_reported_as_excess():
    profile = ClientProfile(liquidity=LiquidityNeeds(cash_reserve_target=100_000))
    result = check_cash_policy(profile, 250_000, 1_000_000)
    assert result["checked"] is True
    assert result["cash_pct"] == 25.0
    assert any("150,000 above" in f for f in result["findings"])


def test_cash_below_reserve_flips_the_status():
    profile = ClientProfile(liquidity=LiquidityNeeds(cash_reserve_target=100_000))
    result = check_cash_policy(profile, 40_000, 1_000_000)
    assert result["status"] == "below_reserve"
    assert any("60,000 below" in f for f in result["findings"])


def test_cash_above_the_ips_ceiling_is_flagged():
    profile = ClientProfile(ips=IPSTargets(max_cash_pct=10))
    result = check_cash_policy(profile, 250_000, 1_000_000)
    assert result["status"] == "above_band"
    assert any("above the 10.0% IPS ceiling" in f for f in result["findings"])


def test_upcoming_liquidity_event_explains_the_excess():
    """A generic cash-drag alert is wrong when the client told you why."""
    profile = ClientProfile(
        liquidity=LiquidityNeeds(
            cash_reserve_target=50_000,
            next_liquidity_event="Q2 2027 tuition",
            next_liquidity_amount=120_000,
        ),
    )
    result = check_cash_policy(profile, 200_000, 1_000_000)
    joined = " ".join(result["findings"])
    assert "Q2 2027 tuition" in joined
    assert "120,000" in joined
    assert "may be intentional" in joined


def test_no_cash_position_is_reported_as_unchecked():
    result = check_cash_policy(ClientProfile(), None, 1_000_000)
    assert result["checked"] is False
    assert "no cash" in result["note"].lower()
