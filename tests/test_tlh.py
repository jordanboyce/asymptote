"""Tests for services.financial.tlh — TLH primitives + harvest plan composer."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from services.financial.holdings_store import HoldingsStore
from services.financial.tlh import (
    Budget,
    Candidate,
    HarvestPlan,
    Replacement,
    WashSaleStatus,
    _holding_period,
    build_harvest_plan,
    check_wash_sale,
    gain_loss_budget,
    is_tax_deferred,
    scan_unrealized_losses,
    suggest_replacements,
)


# ---------------------------------------------------------------------------
# Fixtures — household with one taxable account and one IRA
# ---------------------------------------------------------------------------

# JNJ is a 15k loss in a TAXABLE account → harvest candidate.
# AAPL is a 50k gain in TAXABLE → not a candidate.
# XOM is a 10k loss but in an IRA → no offset, must be filtered out.
# CASH and bond yields are tiny test rows for negative cases.
_HOLDINGS = [
    {
        'Ticker': 'AAPL',
        'Name': 'Apple Inc',
        'Market Value': '$150,000.00',
        'Cost Basis': '$100,000.00',
        'Sector': 'Technology',
        'Account': 'Taxable Joint Brokerage',
        'Acquisition Date': '2020-01-15',
    },
    {
        'Ticker': 'JNJ',
        'Name': 'Johnson & Johnson',
        'Market Value': '$40,000.00',
        'Cost Basis': '$55,000.00',
        'Sector': 'Healthcare',
        'Account': 'Taxable Joint Brokerage',
        'Acquisition Date': '2021-06-01',          # > 1 yr ago — long-term
    },
    {
        'Ticker': 'TSLA',
        'Name': 'Tesla Inc',
        'Market Value': '$10,000.00',
        'Cost Basis': '$18,000.00',
        'Sector': 'Consumer Cyclical',
        'Account': 'Taxable Joint Brokerage',
        # Acquisition within the last year — short-term loss
        'Acquisition Date': (date.today() - timedelta(days=120)).isoformat(),
    },
    {
        'Ticker': 'XOM',
        'Name': 'Exxon Mobil',
        'Market Value': '$20,000.00',
        'Cost Basis': '$30,000.00',                # 10k loss but in IRA
        'Sector': 'Energy',
        'Account': 'Rollover IRA',
        'Acquisition Date': '2019-04-12',
    },
    {
        'Ticker': 'CASH',
        'Name': 'Money Market Fund',
        'Market Value': '$50,000.00',
        'Cost Basis': '$50,000.00',                # No loss
        'Sector': 'Cash',
        'Account': 'Taxable Joint Brokerage',
        'Acquisition Date': '2024-01-01',
    },
]

_COLUMNS = ['Ticker', 'Name', 'Market Value', 'Cost Basis', 'Sector', 'Account', 'Acquisition Date']


@pytest.fixture
def store(tmp_path):
    s = HoldingsStore(tmp_path / 'metadata.db')
    s.create_table(
        document_id='doc-holdings',
        filename='household.csv',
        columns=_COLUMNS,
        rows=_HOLDINGS,
    )
    return s


@pytest.fixture
def spouse_store(tmp_path):
    """A second collection with JNJ also held — used to assert household wash-sale detection."""
    s = HoldingsStore(tmp_path / 'spouse_metadata.db')
    s.create_table(
        document_id='doc-spouse',
        filename='spouse.csv',
        columns=['Ticker', 'Name', 'Market Value', 'Cost Basis', 'Account'],
        rows=[
            {
                'Ticker': 'JNJ',
                'Name': 'Johnson & Johnson',
                'Market Value': '$5,000.00',
                'Cost Basis': '$5,200.00',
                'Account': "Spouse Roth IRA",
            },
        ],
    )
    return s


# ---------------------------------------------------------------------------
# is_tax_deferred — taxable detection is the lever for taxable_only=True
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('name,expected', [
    ('Taxable Joint Brokerage', False),
    ('Individual Brokerage Account', False),
    ('Rollover IRA', True),
    ('Traditional IRA', True),
    ('Roth IRA', True),
    ('SEP-IRA', True),
    ('401(k) Plan', True),
    ('401K Plan', True),
    ('HSA - Fidelity', True),
    ('529 College Savings', True),
    ('Annuity Account', True),
    ('Inherited IRA', True),
    (None, False),
    ('', False),
    # Ambiguous → treated as taxable so the harvest plan over-includes
    ('JOINT WROS', False),
])
def test_is_tax_deferred(name, expected):
    assert is_tax_deferred(name) is expected


# ---------------------------------------------------------------------------
# _holding_period
# ---------------------------------------------------------------------------


def test_holding_period_long_term():
    old = (date.today() - timedelta(days=400)).isoformat()
    assert _holding_period(old) == 'long_term'


def test_holding_period_short_term():
    new = (date.today() - timedelta(days=90)).isoformat()
    assert _holding_period(new) == 'short_term'


def test_holding_period_boundary_one_year():
    # exactly 365 days = still short-term per IRS (must be > 1 year).
    # Pin the reference date so this isn't a UTC-vs-local boundary flake.
    ref = date(2025, 5, 9)
    boundary = (ref - timedelta(days=365)).isoformat()
    assert _holding_period(boundary, reference=ref) == 'short_term'

    over_boundary = (ref - timedelta(days=366)).isoformat()
    assert _holding_period(over_boundary, reference=ref) == 'long_term'


def test_holding_period_unknown_when_missing():
    assert _holding_period(None) == 'unknown'
    assert _holding_period('') == 'unknown'
    assert _holding_period('not-a-date') == 'unknown'


def test_holding_period_handles_us_format():
    old = '01/15/2020'  # US-style date
    assert _holding_period(old) == 'long_term'


# ---------------------------------------------------------------------------
# scan_unrealized_losses
# ---------------------------------------------------------------------------


def test_scan_finds_taxable_losses(store):
    cands = scan_unrealized_losses(store)
    symbols = [c.symbol for c in cands]
    assert 'JNJ' in symbols
    assert 'TSLA' in symbols


def test_scan_excludes_ira_losses_by_default(store):
    cands = scan_unrealized_losses(store)
    symbols = [c.symbol for c in cands]
    # XOM is in a Rollover IRA — losses there cannot be harvested
    assert 'XOM' not in symbols


def test_scan_excludes_winning_positions(store):
    cands = scan_unrealized_losses(store)
    symbols = [c.symbol for c in cands]
    assert 'AAPL' not in symbols  # gain, not loss
    assert 'CASH' not in symbols  # break-even


def test_scan_includes_ira_when_taxable_only_false(store):
    cands = scan_unrealized_losses(store, taxable_only=False)
    symbols = [c.symbol for c in cands]
    assert 'XOM' in symbols


def test_scan_min_loss_filter(store):
    # JNJ loss is 15k, TSLA loss is 8k. min_loss=10k should exclude TSLA.
    cands = scan_unrealized_losses(store, min_loss=10_000)
    symbols = [c.symbol for c in cands]
    assert 'JNJ' in symbols
    assert 'TSLA' not in symbols


def test_scan_min_loss_pct_filter(store):
    # JNJ: 15k/55k = 27%. TSLA: 8k/18k = 44%.
    # min_loss_pct=40 should keep TSLA but drop JNJ.
    cands = scan_unrealized_losses(store, min_loss_pct=40.0)
    symbols = [c.symbol for c in cands]
    assert 'TSLA' in symbols
    assert 'JNJ' not in symbols


def test_scan_orders_short_term_first(store):
    # TSLA = ST, JNJ = LT. ST should appear before LT regardless of loss size.
    cands = scan_unrealized_losses(store)
    period_order = [c.holding_period for c in cands]
    # Strip 'unknown' if present — we only care about the relative ST/LT order.
    known = [p for p in period_order if p in ('short_term', 'long_term')]
    if 'short_term' in known and 'long_term' in known:
        assert known.index('short_term') < known.index('long_term')


def test_scan_loss_pct_computed(store):
    cands = scan_unrealized_losses(store)
    jnj = next(c for c in cands if c.symbol == 'JNJ')
    # 15k / 55k ≈ 27.27%
    assert jnj.loss_pct == pytest.approx(27.27, rel=0.01)


def test_scan_unrealized_loss_is_positive(store):
    cands = scan_unrealized_losses(store)
    for c in cands:
        assert c.unrealized_loss > 0


def test_scan_returns_dataclasses(store):
    cands = scan_unrealized_losses(store)
    assert all(isinstance(c, Candidate) for c in cands)


# ---------------------------------------------------------------------------
# gain_loss_budget — degrades to 'unknown' without realized columns
# ---------------------------------------------------------------------------


def test_budget_degrades_when_no_realized_column(store):
    budget = gain_loss_budget(store)
    assert isinstance(budget, Budget)
    assert budget.source == 'unknown'
    assert budget.realized_short_term is None
    assert budget.realized_long_term is None
    assert '1099-B' in budget.note or 'transaction' in budget.note.lower()


def test_budget_year_defaults_to_current(store):
    budget = gain_loss_budget(store)
    assert budget.year == datetime.now(tz=timezone.utc).year


def test_budget_with_realized_column(tmp_path):
    s = HoldingsStore(tmp_path / 'metadata.db')
    s.create_table(
        document_id='doc-realized',
        filename='realized.csv',
        columns=['Ticker', 'Realized Short-Term Gain', 'Realized Long-Term Gain'],
        rows=[
            {'Ticker': 'AAPL', 'Realized Short-Term Gain': '$5,000', 'Realized Long-Term Gain': '$15,000'},
            {'Ticker': 'MSFT', 'Realized Short-Term Gain': '$2,000', 'Realized Long-Term Gain': '$8,000'},
        ],
    )
    budget = gain_loss_budget(s)
    assert budget.source == 'transactions'
    assert budget.realized_short_term == pytest.approx(7_000)
    assert budget.realized_long_term == pytest.approx(23_000)
    assert budget.available_to_offset == pytest.approx(30_000)


# ---------------------------------------------------------------------------
# suggest_replacements
# ---------------------------------------------------------------------------


def test_replacements_for_spy_are_different_issuer():
    repls = suggest_replacements('SPY')
    assert repls
    symbols = [r.symbol for r in repls]
    # Crucially, NEVER suggest IVV or VOO — they're substantially identical to SPY
    # (all three track the same S&P 500 index, fellow practitioners treat as wash).
    assert 'IVV' not in symbols
    assert 'VOO' not in symbols
    assert all(r.similarity == 'different_issuer_index' for r in repls)


def test_replacements_for_qqq():
    repls = suggest_replacements('QQQ')
    assert repls
    # First suggestion should hold tech exposure without being Nasdaq-100
    assert repls[0].symbol in ('VGT', 'XLK')


def test_replacements_handles_lowercase():
    repls = suggest_replacements('spy')
    assert repls and repls[0].symbol == 'VTI'


def test_replacements_falls_back_to_sector_proxy():
    # Made-up symbol, classified as Healthcare → should suggest XLV
    def fake_classify(_sym):
        return {'asset_class': 'equity', 'sector': 'Healthcare'}

    repls = suggest_replacements('FAKEHC', classify_fn=fake_classify)
    assert len(repls) == 1
    assert repls[0].symbol == 'XLV'
    assert repls[0].similarity == 'sector_proxy'


def test_replacements_falls_back_to_asset_class_proxy():
    def fake_classify(_sym):
        # No sector match → fall through to asset class
        return {'asset_class': 'equity', 'sector': 'Made-up Sector'}

    repls = suggest_replacements('FAKEEQ', classify_fn=fake_classify)
    assert len(repls) == 1
    assert repls[0].symbol == 'VTI'
    assert repls[0].similarity == 'asset_class_proxy'


def test_replacements_empty_when_classification_fails():
    def fake_classify(_sym):
        return {'error': 'symbol_not_found', 'message': '—'}

    repls = suggest_replacements('NOPE', classify_fn=fake_classify)
    assert repls == []


def test_replacements_empty_when_symbol_blank():
    assert suggest_replacements('') == []
    assert suggest_replacements(None) == []


# ---------------------------------------------------------------------------
# check_wash_sale — household scope is the moat
# ---------------------------------------------------------------------------


def test_wash_sale_clear_when_symbol_not_held(store):
    status = check_wash_sale('NVDA', stores=[store])
    assert isinstance(status, WashSaleStatus)
    assert status.status == 'clear'
    assert status.accounts_holding == []


def test_wash_sale_potential_when_symbol_in_same_collection(store):
    # JNJ is held in the Taxable Joint account; treating that as 'sell JNJ in
    # one lot, also held in another' is the canonical 'potential' case.
    status = check_wash_sale('JNJ', stores=[store])
    assert status.status == 'potential'
    assert any('Taxable' in a for a in status.accounts_holding)


def test_wash_sale_household_scope_finds_spouse_holding(store, spouse_store):
    # JNJ is held in spouse's Roth IRA — exactly the case single-account
    # tools miss.  Wash-sale check across the household must surface this.
    status = check_wash_sale('JNJ', stores=[store, spouse_store])
    assert status.status == 'potential'
    assert any('Roth' in a or 'Spouse' in a for a in status.accounts_holding)


def test_wash_sale_confirmed_with_recent_transaction(store):
    # Inject a synthetic transaction lookup that returns a date inside the window
    today = date.today()
    in_window_date = today - timedelta(days=10)

    def fake_lookup(_sym, _store):
        return [in_window_date]

    status = check_wash_sale('JNJ', stores=[store], transaction_lookup=fake_lookup)
    assert status.status == 'confirmed'
    assert 'TRIGGERED' in status.note


def test_wash_sale_not_confirmed_when_transaction_outside_window(store):
    today = date.today()
    out_of_window_date = today - timedelta(days=120)  # > 30 days before

    def fake_lookup(_sym, _store):
        return [out_of_window_date]

    status = check_wash_sale('JNJ', stores=[store], transaction_lookup=fake_lookup)
    # JNJ is also held → status stays 'potential', not promoted to confirmed
    assert status.status == 'potential'


def test_wash_sale_handles_blank_symbol():
    status = check_wash_sale('', stores=[])
    assert status.status == 'clear'


# ---------------------------------------------------------------------------
# build_harvest_plan — composer
# ---------------------------------------------------------------------------


@pytest.fixture
def plan(store, spouse_store):
    return build_harvest_plan(
        store,
        collection_id='test-household',
        household_stores=[store, spouse_store],
    )


def test_plan_has_all_sections(plan):
    assert isinstance(plan, HarvestPlan)
    for key in ('candidates', 'budget', 'wash_sale_warnings', 'guardrails', 'totals'):
        assert hasattr(plan, key)


def test_plan_collection_id_carried(plan):
    assert plan.collection_id == 'test-household'


def test_plan_includes_jnj_with_replacement_and_wash_sale(plan):
    jnj = next(c for c in plan.candidates if c['symbol'] == 'JNJ')
    # JNJ is in healthcare → expect XLV as the sector-proxy replacement
    # (no curated entry for JNJ in the table).
    assert jnj['replacements']
    # Wash-sale must flag potential because JNJ is also held in spouse's Roth
    assert jnj['wash_sale']['status'] == 'potential'
    assert any('Roth' in a or 'Spouse' in a for a in jnj['wash_sale']['accounts_holding'])


def test_plan_excludes_ira_losses(plan):
    symbols = [c['symbol'] for c in plan.candidates]
    assert 'XOM' not in symbols


def test_plan_totals_sum_correctly(plan):
    summed = sum(c['unrealized_loss'] for c in plan.candidates)
    assert plan.totals['total_unrealized_loss'] == pytest.approx(summed, rel=0.001)


def test_plan_guardrails_include_household_wash_sale_rule(plan):
    text = ' '.join(plan.guardrails).lower()
    assert 'wash-sale' in text or 'wash sale' in text
    assert 'household' in text
    assert 'spousal' in text or 'spouse' in text


def test_plan_guardrails_include_basis_reset(plan):
    text = ' '.join(plan.guardrails).lower()
    assert 'basis' in text
    assert 'defer' in text  # 'defers' / 'deferred'


def test_plan_guardrails_include_3k_ordinary_income_cap(plan):
    text = ' '.join(plan.guardrails)
    assert '$3,000' in text or '$3k' in text or '3,000' in text


def test_plan_max_candidates_caps_results(store):
    plan = build_harvest_plan(store, collection_id='c', max_candidates=1)
    assert len(plan.candidates) <= 1


def test_plan_wash_sale_warnings_populated(plan):
    # JNJ held in spouse's Roth → must appear in consolidated warnings
    symbols = [w['symbol'] for w in plan.wash_sale_warnings]
    assert 'JNJ' in symbols


def test_plan_generated_at_is_iso(plan):
    datetime.fromisoformat(plan.generated_at)
