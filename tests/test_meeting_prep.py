"""`prep_for_meeting` — the §7 keystone composite.

The arithmetic is tested in test_ips_drift.py. What matters here is the
composition contract:

  * every section degrades alone — a dead sub-service removes one section
    and records a gap, it never fails the page;
  * `gaps` names what could not be determined, so the advisor is never
    told a section is clean when it was skipped;
  * `talking_points` is ordered by consequence and derived only from
    findings that actually exist — no LLM, no invention.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from models.schemas import (
    AllocationTarget,
    ClientProfile,
    IPSTargets,
    LiquidityNeeds,
    RiskTolerance,
    TaxProfile,
)
from services.meeting_prep import build_meeting_prep

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


# ── stubs ─────────────────────────────────────────────────────────────────


class _StubHoldingsStore:
    """Minimal HoldingsStore surface: enough for brief_generator to be stubbed out."""

    def __init__(self, tables=None):
        self._tables = tables if tables is not None else [{"financial_roles": ["market_value"], "table_name": "t1", "filename": "holdings.csv"}]
        self.db_path = ":memory:"

    def list_tables(self):
        return self._tables


class _StubNotesStore:
    def __init__(self, records=None, action_items=None):
        self._records = records or []
        self._items = action_items or []

    def list_for_collection(self, collection_id, since=None, until=None):
        return self._records

    def list_action_items(self, collection_id, status=None, assignee=None):
        return self._items


class _StubMetadataStore:
    def __init__(self, documents=None):
        self._documents = documents or []

    def list_documents(self):
        return self._documents


class _StubProfileStore:
    def __init__(self, profile=None, raises=False):
        self._profile = profile
        self._raises = raises

    def get_model(self, collection_id):
        if self._raises:
            raise RuntimeError("profile store exploded")
        return self._profile


def _brief(**overrides):
    base = {
        "collection_id": "c1",
        "household_summary": {
            "total_market_value": 1_000_000.0,
            "total_cost_basis": 800_000.0,
            "total_unrealized_pnl": 200_000.0,
            "sources": [],
        },
        "accounts": [{"account": "Joint Brokerage", "market_value": 1_000_000.0}],
        "top_positions": [
            {"name": "Apple Inc", "ticker": "AAPL", "market_value": 300_000.0},
            {"name": "Vanguard Total Bond", "ticker": "BND", "market_value": 400_000.0},
            {"name": "Cash & Equivalents", "ticker": None, "market_value": 300_000.0},
        ],
        "tax_loss_candidates": [],
        "concentration_alerts": [],
        "cash_drag_alerts": [],
        "sector_allocation": [],
        "tables_scanned": 1,
        "generated_at": NOW.isoformat(),
    }
    base.update(overrides)
    return base


def _allocation_fn(rows):
    """Return a `_collect_allocation`-shaped callable with fixed rows (no network)."""
    def _fn(store, gaps):
        return rows, ["holdings.csv"]
    return _fn


def _no_events(**kwargs):
    return {"filings": [], "dividends": [], "splits": [], "earnings": []}


def _prep(monkeypatch, *, profile=None, brief=None, notes=None, items=None,
          allocation=None, profile_raises=False, **kwargs):
    """Build a prep page with every external dependency stubbed."""
    import services.meeting_prep as mp

    monkeypatch.setattr(mp, "generate_meeting_brief", lambda *a, **k: brief if brief is not None else _brief())

    defaults = dict(
        holdings_store=_StubHoldingsStore(),
        meeting_notes_store=_StubNotesStore(records=notes or [], action_items=items or []),
        metadata_store=_StubMetadataStore(),
        client_profile_store=_StubProfileStore(profile, raises=profile_raises),
        collection_id="c1",
        when=NOW.isoformat(),
        include_tlh=False,
        include_market_context=False,
        allocation_fn=_allocation_fn(allocation or []),
    )
    defaults.update(kwargs)
    return build_meeting_prep(**defaults)


def _full_profile() -> ClientProfile:
    return ClientProfile(
        display_name="Henderson Household",
        risk_tolerance=RiskTolerance.moderate,
        time_horizon_years=12,
        ips=IPSTargets(
            allocation_targets=[
                AllocationTarget(asset_class="Equities", target_pct=60),
                AllocationTarget(asset_class="Fixed Income", target_pct=40),
            ],
            rebalance_band_pct=5,
            max_single_position_pct=10,
            prohibited_holdings=["tobacco"],
        ),
        tax=TaxProfile(federal_bracket_pct=32),
        liquidity=LiquidityNeeds(cash_reserve_target=100_000),
    )


# ── shape ─────────────────────────────────────────────────────────────────


def test_page_always_has_every_section(monkeypatch):
    page = _prep(monkeypatch)
    for key in (
        "header", "since_last_meeting", "open_items", "portfolio",
        "policy", "opportunities", "market_context", "talking_points", "gaps",
    ):
        assert key in page, f"missing section: {key}"


def test_header_declares_it_was_computed_not_generated(monkeypatch):
    page = _prep(monkeypatch)
    assert "no LLM" in page["header"]["generated_by"]
    assert page["header"]["collection_id"] == "c1"
    assert page["header"]["total_market_value"] == 1_000_000.0


# ── the no-profile path ───────────────────────────────────────────────────


def test_without_a_profile_policy_is_absent_and_every_blocked_judgment_is_named(monkeypatch):
    page = _prep(monkeypatch)

    assert page["policy"]["has_profile"] is False
    assert page["policy"]["allocation_drift"] is None

    reasons = " ".join(g["reason"] for g in page["gaps"]).lower()
    assert "allocation drift" in reasons
    assert "concentration" in reasons
    assert "prohibited" in reasons

    # Nothing may claim the portfolio is compliant.
    assert not any(p["category"] == "allocation" for p in page["talking_points"])


def test_profile_store_failure_degrades_to_a_gap_not_a_crash(monkeypatch):
    page = _prep(monkeypatch, profile_raises=True)
    assert page["policy"]["has_profile"] is False
    assert any("could not be loaded" in g["reason"] for g in page["gaps"])


# ── the with-profile path ─────────────────────────────────────────────────


def test_allocation_drift_appears_once_a_profile_exists(monkeypatch):
    page = _prep(
        monkeypatch,
        profile=_full_profile(),
        allocation=[
            {"label": "Equities", "market_value": 700_000},
            {"label": "Fixed Income", "market_value": 300_000},
        ],
    )

    drift = page["policy"]["allocation_drift"]
    assert drift is not None
    equities = next(ln for ln in drift["lines"] if ln["asset_class"] == "Equities")
    assert equities["drift_pct"] == 10.0
    assert equities["status"] == "over"

    point = next(p for p in page["talking_points"] if p["category"] == "allocation")
    assert "Equities" in point["headline"]
    assert "over target" in point["headline"]
    assert point["priority"] == "high"


def test_concentration_breach_becomes_a_high_priority_talking_point(monkeypatch):
    page = _prep(monkeypatch, profile=_full_profile())

    concentration = page["policy"]["concentration"]
    assert {b["ticker"] for b in concentration["breaches"]} == {"AAPL", "BND"}

    point = next(p for p in page["talking_points"] if p["category"] == "concentration")
    assert point["priority"] == "high"
    assert "above the 10.0% limit" in point["headline"]


def test_cash_is_not_double_reported_as_a_concentration_breach(monkeypatch):
    """The sweep balance is a liquidity question, not a single-security breach."""
    page = _prep(monkeypatch, profile=_full_profile())

    concentration = page["policy"]["concentration"]
    assert "Cash & Equivalents" not in {b["name"] for b in concentration["breaches"]}
    assert concentration["excluded_cash_positions"] == ["Cash & Equivalents"]
    assert "reserve target" in concentration["exclusion_note"]

    # Still measured as a share of the whole portfolio, cash included.
    aapl = next(b for b in concentration["breaches"] if b["ticker"] == "AAPL")
    assert aapl["pct_of_portfolio"] == 30.0


def test_prohibited_match_is_the_top_talking_point(monkeypatch):
    """Compliance outranks everything — it's the one item that cannot wait."""
    brief = _brief(top_positions=[
        {"name": "Altria (tobacco holding)", "ticker": "MO", "market_value": 50_000.0},
        {"name": "Vanguard Total", "ticker": "VTI", "market_value": 950_000.0},
    ])
    page = _prep(monkeypatch, profile=_full_profile(), brief=brief)

    assert page["talking_points"][0]["category"] == "compliance"
    assert page["talking_points"][0]["priority"] == "high"
    assert page["policy"]["prohibited"]["count"] == 1


def test_prohibited_screen_declares_it_only_saw_the_top_positions(monkeypatch):
    """Scope honesty: the screen ran on 3 rows, not the whole book."""
    page = _prep(monkeypatch, profile=_full_profile())
    prohibited = page["policy"]["prohibited"]
    assert prohibited["scope"] == "top_positions"
    assert "not the full holdings list" in prohibited["scope_note"]


def test_cash_is_judged_against_the_reserve_target(monkeypatch):
    page = _prep(monkeypatch, profile=_full_profile())
    cash = page["policy"]["cash"]
    assert cash["checked"] is True
    assert cash["cash_market_value"] == 300_000.0
    assert any("200,000 above" in f for f in cash["findings"])


# ── meeting history ───────────────────────────────────────────────────────


def test_days_since_last_meeting_is_computed(monkeypatch):
    last = (NOW - timedelta(days=97)).isoformat()
    notes = [{
        "document_id": "d1",
        "extracted_at": last,
        "client_concerns": ["Worried about tech exposure"],
        "decisions": ["Hold the bond ladder"],
        "follow_up_questions": ["Model an early retirement at 62"],
        "sentiment_notes": "Constructive",
        "action_items": [],
    }]
    page = _prep(monkeypatch, notes=notes)

    since = page["since_last_meeting"]
    assert since["has_prior_meeting"] is True
    assert since["days_since"] == 97
    assert page["header"]["days_since_last_meeting"] == 97


def test_no_prior_meeting_is_a_named_gap(monkeypatch):
    page = _prep(monkeypatch)
    assert page["since_last_meeting"]["has_prior_meeting"] is False
    assert any("no meeting transcript" in g["reason"].lower() for g in page["gaps"])


def test_client_concerns_and_followups_become_talking_points(monkeypatch):
    notes = [{
        "document_id": "d1",
        "extracted_at": (NOW - timedelta(days=30)).isoformat(),
        "client_concerns": ["Worried about tech exposure"],
        "decisions": [],
        "follow_up_questions": ["Model an early retirement at 62"],
        "sentiment_notes": None,
        "action_items": [],
    }]
    page = _prep(monkeypatch, notes=notes)

    categories = {p["category"] for p in page["talking_points"]}
    assert "client_concern" in categories
    assert "follow_up" in categories


# ── action items ──────────────────────────────────────────────────────────


def test_overdue_items_are_flagged_and_ranked_first(monkeypatch):
    items = [
        {
            "description": "Send the updated IPS",
            "assignee": "advisor",
            "due_date": (NOW - timedelta(days=10)).date().isoformat(),
            "status": "open",
            "extracted_at": (NOW - timedelta(days=40)).isoformat(),
        },
        {
            "description": "Client to send 2025 K-1",
            "assignee": "client",
            "due_date": (NOW + timedelta(days=30)).date().isoformat(),
            "status": "open",
            "extracted_at": (NOW - timedelta(days=5)).isoformat(),
        },
    ]
    page = _prep(monkeypatch, items=items)

    assert page["open_items"]["overdue_count"] == 1
    assert page["open_items"]["items"][0]["description"] == "Send the updated IPS"
    assert page["open_items"]["items"][0]["is_overdue"] is True
    assert page["open_items"]["items"][1]["is_overdue"] is False

    point = next(p for p in page["talking_points"] if p["headline"].startswith("Overdue:"))
    assert point["priority"] == "high"


def test_stale_items_are_flagged_without_a_due_date(monkeypatch):
    items = [{
        "description": "Review the trust documents",
        "assignee": "advisor",
        "due_date": None,
        "status": "open",
        "extracted_at": (NOW - timedelta(days=120)).isoformat(),
    }]
    page = _prep(monkeypatch, items=items)

    assert page["open_items"]["stale_count"] == 1
    assert page["open_items"]["items"][0]["days_open"] == 120
    point = next(p for p in page["talking_points"] if "Still open after 120 days" in p["headline"])
    assert point["priority"] == "medium"


def test_unparseable_due_dates_are_declared_not_silently_ignored(monkeypatch):
    """'next meeting' can't be checked — say so rather than implying it was."""
    items = [{
        "description": "Revisit the 529 funding",
        "assignee": "advisor",
        "due_date": "next meeting",
        "status": "open",
        "extracted_at": (NOW - timedelta(days=5)).isoformat(),
    }]
    page = _prep(monkeypatch, items=items)

    assert page["open_items"]["overdue_count"] == 0
    assert any("could not be parsed" in g["reason"] for g in page["gaps"])


# ── degradation ───────────────────────────────────────────────────────────


def test_brief_failure_removes_the_portfolio_section_but_keeps_the_page(monkeypatch):
    import services.meeting_prep as mp

    def _boom(*a, **k):
        raise RuntimeError("holdings db locked")

    monkeypatch.setattr(mp, "generate_meeting_brief", _boom)

    page = build_meeting_prep(
        holdings_store=_StubHoldingsStore(),
        meeting_notes_store=_StubNotesStore(),
        metadata_store=_StubMetadataStore(),
        client_profile_store=_StubProfileStore(_full_profile()),
        collection_id="c1",
        when=NOW.isoformat(),
        include_tlh=False,
        include_market_context=False,
        allocation_fn=_allocation_fn([]),
    )

    assert page["portfolio"]["household_summary"] == {}
    assert any("holdings db locked" in g["reason"] for g in page["gaps"])
    # The rest of the page still rendered.
    assert "talking_points" in page
    assert page["header"]["collection_id"] == "c1"


def test_no_financial_tables_is_a_named_gap(monkeypatch):
    page = _prep(monkeypatch, brief=_brief(tables_scanned=0, top_positions=[]))
    assert any("no holdings table" in g["reason"].lower() for g in page["gaps"])


def test_market_context_failure_names_the_symbols_it_could_not_check(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("yfinance down")

    page = _prep(
        monkeypatch,
        include_market_context=True,
        events_fn=_boom,
    )

    assert any(
        "AAPL" in g["reason"] and "not checked" in g["reason"]
        for g in page["gaps"]
    )


def test_market_context_declares_it_only_checked_the_top_holdings(monkeypatch):
    page = _prep(monkeypatch, include_market_context=True, events_fn=_no_events)
    assert page["market_context"]["checked"] is True
    assert "not the full book" in page["market_context"]["symbols_note"]


def test_gaps_are_deduped(monkeypatch):
    page = _prep(monkeypatch)
    seen = [(g["section"], g["reason"]) for g in page["gaps"]]
    assert len(seen) == len(set(seen))


# ── ordering ──────────────────────────────────────────────────────────────


def test_talking_points_are_ordered_by_priority(monkeypatch):
    order = {"high": 0, "medium": 1, "low": 2}
    items = [{
        "description": "Send the updated IPS",
        "assignee": "advisor",
        "due_date": (NOW - timedelta(days=10)).date().isoformat(),
        "status": "open",
        "extracted_at": (NOW - timedelta(days=40)).isoformat(),
    }]
    page = _prep(
        monkeypatch,
        profile=_full_profile(),
        items=items,
        allocation=[
            {"label": "Equities", "market_value": 700_000},
            {"label": "Fixed Income", "market_value": 300_000},
        ],
    )

    priorities = [order[p["priority"]] for p in page["talking_points"]]
    assert priorities == sorted(priorities)
    assert page["header"]["talking_point_count"] == len(page["talking_points"])


def test_every_talking_point_cites_a_source(monkeypatch):
    """No point may appear without saying where it came from."""
    page = _prep(
        monkeypatch,
        profile=_full_profile(),
        allocation=[
            {"label": "Equities", "market_value": 700_000},
            {"label": "Fixed Income", "market_value": 300_000},
        ],
    )
    assert page["talking_points"]
    for point in page["talking_points"]:
        assert point["source"], point
        assert point["detail"], point
        assert point["priority"] in ("high", "medium", "low")
