"""Client profile / IPS store (v4.6).

The store itself is unremarkable SQLite. What earns tests is the
completeness report — it is what stops a half-filled profile from producing
a prep page that looks complete.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from models.schemas import (
    AllocationTarget,
    ClientGoal,
    ClientProfile,
    HouseholdMember,
    IPSTargets,
    LiquidityNeeds,
    RiskTolerance,
    TaxProfile,
)
from services.financial.client_profile import (
    ClientProfileStore,
    allocation_targets_sum,
    profile_completeness,
)


@pytest.fixture
def store(tmp_path):
    return ClientProfileStore(tmp_path / "metadata.db")


def _full_profile() -> ClientProfile:
    return ClientProfile(
        display_name="Henderson Household",
        household_members=[
            HouseholdMember(name="Dana Henderson", relationship="primary", birth_year=1968),
            HouseholdMember(name="Rowan Henderson", relationship="spouse", birth_year=1971),
        ],
        risk_tolerance=RiskTolerance.moderate,
        time_horizon_years=12,
        goals=[ClientGoal(label="Retirement", target_amount=2_500_000, target_date="2038")],
        ips=IPSTargets(
            allocation_targets=[
                AllocationTarget(asset_class="Equities", target_pct=60),
                AllocationTarget(asset_class="Fixed Income", target_pct=35),
                AllocationTarget(asset_class="Cash", target_pct=5),
            ],
            rebalance_band_pct=5,
            max_single_position_pct=8,
            prohibited_holdings=["XOM", "tobacco"],
        ),
        tax=TaxProfile(filing_status="married_joint", federal_bracket_pct=32, state="CA"),
        liquidity=LiquidityNeeds(cash_reserve_target=150_000),
    )


# ── storage ───────────────────────────────────────────────────────────────


def test_get_returns_none_before_anything_is_saved(store):
    assert store.get("c1") is None
    assert store.get_model("c1") is None
    assert store.exists("c1") is False


def test_roundtrip_preserves_every_section(store):
    store.save("c1", _full_profile())

    loaded = store.get_model("c1")
    assert loaded is not None
    assert loaded.display_name == "Henderson Household"
    assert loaded.risk_tolerance is RiskTolerance.moderate
    assert loaded.time_horizon_years == 12
    assert [m.name for m in loaded.household_members] == ["Dana Henderson", "Rowan Henderson"]
    assert [t.asset_class for t in loaded.ips.allocation_targets] == ["Equities", "Fixed Income", "Cash"]
    assert loaded.ips.max_single_position_pct == 8
    assert loaded.ips.prohibited_holdings == ["XOM", "tobacco"]
    assert loaded.tax.federal_bracket_pct == 32
    assert loaded.liquidity.cash_reserve_target == 150_000


def test_save_is_replace_not_insert(store):
    store.save("c1", _full_profile())
    store.save("c1", ClientProfile(display_name="Renamed"))

    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM client_profile WHERE collection_id = 'c1'").fetchone()
    assert rows[0] == 1

    loaded = store.get_model("c1")
    assert loaded.display_name == "Renamed"
    # Replace semantics: the old IPS is gone, not merged.
    assert loaded.ips.allocation_targets == []


def test_profiles_are_isolated_per_collection(store):
    store.save("c1", ClientProfile(display_name="One"))
    store.save("c2", ClientProfile(display_name="Two"))

    assert store.get_model("c1").display_name == "One"
    assert store.get_model("c2").display_name == "Two"


def test_delete_removes_the_row(store):
    store.save("c1", _full_profile())
    assert store.delete("c1") is True
    assert store.get("c1") is None
    assert store.delete("c1") is False


def test_unreadable_payload_degrades_to_absent_rather_than_raising(store):
    """Schema drift across an upgrade must not 500 the prep page."""
    store.save("c1", _full_profile())
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE client_profile SET raw_json = ? WHERE collection_id = 'c1'", ("{not json",))
        conn.commit()

    assert store.get("c1") is None
    assert store.get_model("c1") is None


def test_payload_that_no_longer_validates_degrades_to_absent(store):
    store.save("c1", _full_profile())
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE client_profile SET raw_json = ? WHERE collection_id = 'c1'",
            (json.dumps({"time_horizon_years": "not a number", "ips": "not an object"}),),
        )
        conn.commit()

    assert store.get("c1") is not None       # raw read still works
    assert store.get_model("c1") is None     # typed read refuses


# ── completeness ──────────────────────────────────────────────────────────


def test_no_profile_blocks_every_judgment():
    report = profile_completeness(None)
    assert report["score"] == 0.0
    assert report["populated"] == []
    assert len(report["blocked"]) == len(report["missing"]) > 0
    assert "cannot" in report["note"].lower()


def test_full_profile_blocks_nothing():
    report = profile_completeness(_full_profile())
    assert report["score"] == 1.0
    assert report["missing"] == []
    assert report["blocked"] == []


def test_partial_profile_names_what_prep_cannot_say():
    """The point of the module: the gap is stated, not silently skipped."""
    partial = ClientProfile(
        risk_tolerance=RiskTolerance.aggressive,
        ips=IPSTargets(allocation_targets=[AllocationTarget(asset_class="Equities", target_pct=100)]),
    )
    report = profile_completeness(partial)

    assert "allocation_targets" in report["populated"]
    assert "risk_tolerance" in report["populated"]
    assert "concentration_limit" in report["missing"]
    assert "prohibited_holdings" in report["missing"]

    blocked = " ".join(report["blocked"]).lower()
    assert "concentration" in blocked
    assert "prohibited" in blocked
    # And it must NOT claim drift is blocked — that section is populated.
    assert "allocation drift" not in blocked


def test_completeness_score_is_a_fraction_of_sections():
    profile = ClientProfile(risk_tolerance=RiskTolerance.moderate)
    report = profile_completeness(profile)
    assert 0.0 < report["score"] < 1.0


# ── target sum ────────────────────────────────────────────────────────────


def test_allocation_targets_sum_is_none_without_targets():
    assert allocation_targets_sum(ClientProfile()) is None


def test_allocation_targets_sum_reports_error_rather_than_normalizing():
    """87% is a data-entry error the advisor should see, not something to fix silently."""
    profile = ClientProfile(ips=IPSTargets(allocation_targets=[
        AllocationTarget(asset_class="Equities", target_pct=60),
        AllocationTarget(asset_class="Bonds", target_pct=27),
    ]))
    assert allocation_targets_sum(profile) == 87.0
