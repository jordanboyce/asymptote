"""Multi-signal column-role resolution.

Two properties matter and they pull against each other. The resolver has to
identify columns in exports it has never seen — otherwise Finn only works for
the five profiled vendors — while never labelling a column confidently and
wrongly, because a wrong ``market_value`` produces a total that looks fine and
isn't.

The tests are organised around that tension: what it should now recognise, and
what it must still refuse.
"""

from __future__ import annotations

import pytest

from services.tabular.column_resolver import (
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_LIKELY,
    ColumnResolution,
    detect_value_shape,
    is_valid_cusip,
    is_valid_isin,
    normalize_tokens,
    resolve_table,
    score_name,
)


def _resolve(columns, rows, **kwargs):
    values = {c: [r.get(c) for r in rows] for c in columns}
    return resolve_table(columns, values, **kwargs)


def _role_of(resolutions, column):
    return resolutions[column].role


# A small book of holdings where every identity balances exactly. Used as the
# substrate for the naming tests so arithmetic can corroborate.
_BOOK = [
    ('AAPL', 'Apple Inc',         150, 232.40, 34860.00, 21000.00, 13860.00),
    ('MSFT', 'Microsoft Corp',     80, 418.75, 33500.00, 24800.00,  8700.00),
    ('JNJ',  'Johnson & Johnson', 320, 152.10, 48672.00, 51200.00, -2528.00),
    ('XOM',  'Exxon Mobil',       600, 108.35, 65010.00, 43500.00, 21510.00),
    ('KO',   'Coca-Cola Co',     1100,  62.80, 69080.00, 58300.00, 10780.00),
    ('PG',   'Procter & Gamble',  240, 167.45, 40188.00, 38400.00,  1788.00),
]


def _book_rows(names):
    """Build rows under caller-supplied column names, in _BOOK's field order."""
    return [dict(zip(names, record)) for record in _BOOK]


# --------------------------------------------------------------------------
# Name normalisation
# --------------------------------------------------------------------------

@pytest.mark.parametrize('name,expected', [
    ('Market Value',            ['market', 'value']),
    ('Mkt Val',                 ['market', 'value']),
    ('Mkt Val (USD)',           ['market', 'value']),
    ('Ending Market Value',     ['market', 'value']),
    ('MarketValue',             ['market', 'value']),
    ('MARKET_VALUE',            ['market', 'value']),
    ('% of Portfolio',          ['percent', 'portfolio']),
    ('Qty',                     ['quantity']),
    ('Acct #',                  ['account']),
])
def test_header_variants_normalise_to_the_same_tokens(name, expected):
    assert normalize_tokens(name) == expected


# --------------------------------------------------------------------------
# Name scoring — the variants the old anchored regex returned None for
# --------------------------------------------------------------------------

@pytest.mark.parametrize('column,role', [
    ('Market Value',            'market_value'),
    ('Ending Market Value',     'market_value'),
    ('Mkt Val (USD)',           'market_value'),
    ('Total Market Value',      'market_value'),
    ('Position Value',          'market_value'),
    ('Qty',                     'quantity'),
    ('Shares Held',             'quantity'),
    ('Number of Units',         'quantity'),
    ('Last Price',              'price'),
    ('Market Price',            'price'),
    ('Adjusted Cost Basis',     'cost_basis'),
    ('Book Value',              'cost_basis'),
    ('Unrealized Gain',         'pnl'),
    ('% of Portfolio',          'weight'),
    ('Sector',                  'sector'),
    ('CUSIP',                   'cusip'),
])
def test_real_world_header_variants_are_recognised(column, role):
    scores = score_name(column)
    assert scores, f'{column!r} matched no pattern at all'
    assert scores[0][0] == role


def test_percentage_gain_is_a_return_not_a_pnl_amount():
    """A gain expressed as a percent must not be summed as dollars."""
    assert score_name('Total Gain/Loss %')[0][0] == 'return'
    assert score_name('Total Gain/Loss $')[0][0] == 'pnl'


# --------------------------------------------------------------------------
# Value shapes
# --------------------------------------------------------------------------

def test_cusip_check_digit_is_actually_verified():
    assert is_valid_cusip('037833100')      # Apple
    assert is_valid_cusip('594918104')      # Microsoft
    assert not is_valid_cusip('037833101')  # same body, wrong check digit
    assert not is_valid_cusip('03783310')   # too short


def test_isin_checksum_is_actually_verified():
    assert is_valid_isin('US0378331005')    # Apple
    assert is_valid_isin('GB0002634946')    # BAE Systems
    assert not is_valid_isin('US0378331006')


def test_cusip_column_is_identified_with_no_usable_header():
    values = ['037833100', '594918104', '478160104', '30231G102', '191216100']
    shapes = dict((role, conf) for role, conf, _ in detect_value_shape(values))
    assert shapes.get('cusip', 0) > 0.9


def test_repeated_account_number_is_not_mistaken_for_a_cusip():
    """The collision that matters: "X12345678" is a valid CUSIP by check digit.

    What distinguishes an identifier column is that it identifies *different*
    things. One value repeated on every row does not.
    """
    assert is_valid_cusip('X12345678')
    values = ['X12345678'] * 12
    assert not any(role == 'cusip' for role, _, _ in detect_value_shape(values))


def test_account_number_column_keeps_its_name_over_the_shape_heuristic():
    columns = ['Account Number', 'Symbol', 'Market Value']
    rows = [
        {'Account Number': 'X12345678', 'Symbol': s, 'Market Value': v}
        for s, v in [('AAPL', 100), ('MSFT', 200), ('JNJ', 300),
                     ('XOM', 400), ('KO', 500), ('PG', 600)]
    ]
    resolutions, _ = _resolve(columns, rows)
    assert _role_of(resolutions, 'Account Number') == 'account'


# --------------------------------------------------------------------------
# Arithmetic coherence — the part that identifies what names cannot
# --------------------------------------------------------------------------

def test_opaque_column_names_are_resolved_by_arithmetic_alone():
    """No header carries any meaning; only the numbers can say what they are."""
    names = ['Col_1', 'Col_2', 'Col_3', 'Col_4', 'Col_5', 'Col_6', 'Col_7']
    resolutions, notes = _resolve(names, _book_rows(names))

    assert _role_of(resolutions, 'Col_3') == 'quantity'
    assert _role_of(resolutions, 'Col_4') == 'price'
    assert _role_of(resolutions, 'Col_5') == 'market_value'
    assert _role_of(resolutions, 'Col_6') == 'cost_basis'
    assert _role_of(resolutions, 'Col_7') == 'pnl'

    for column in ('Col_3', 'Col_4', 'Col_5'):
        assert resolutions[column].confidence >= CONFIDENCE_CONFIRMED
        assert resolutions[column].source == 'arithmetic'
    assert notes, 'a resolution made by arithmetic must be explained'


def test_foreign_language_headers_are_resolved_by_arithmetic():
    """Nothing in the pattern tables is German. The numbers still identify."""
    names = ['Symbol', 'Wertpapier', 'Stück', 'Kurs', 'Kurswert',
             'Einstandswert', 'Gewinn']
    resolutions, _ = _resolve(names, _book_rows(names))

    assert _role_of(resolutions, 'Stück') == 'quantity'
    assert _role_of(resolutions, 'Kurs') == 'price'
    assert _role_of(resolutions, 'Kurswert') == 'market_value'


def test_arithmetic_corrects_a_misleading_header():
    """A column named "Amount" holding market values is read as market value."""
    names = ['Symbol', 'Description', 'Quantity', 'Price', 'Amount',
             'Basis', 'Difference']
    resolutions, notes = _resolve(names, _book_rows(names))

    assert _role_of(resolutions, 'Amount') == 'market_value'
    assert any('Amount' in note for note in notes)


def test_arithmetic_never_overrides_a_confidently_named_column():
    """The constraint that keeps the search from doing harm.

    A flat Pershing export carries Quantity, Unit Cost, and Current Total Cost,
    and quantity x unit cost = total cost balances perfectly. An unconstrained
    search would seize on that and relabel "Unit Cost" as the price and
    "Current Total Cost" as the market value.
    """
    columns = ['Symbol', 'Quantity', 'Unit Cost', 'Current Total Cost',
               'Last Price', 'Market Value']
    rows = []
    for symbol, _, qty, price, mv, basis, _ in _BOOK:
        rows.append({
            'Symbol': symbol,
            'Quantity': qty,
            'Unit Cost': round(basis / qty, 4),
            'Current Total Cost': basis,
            'Last Price': price,
            'Market Value': mv,
        })
    resolutions, _ = _resolve(columns, rows)

    assert _role_of(resolutions, 'Last Price') == 'price'
    assert _role_of(resolutions, 'Market Value') == 'market_value'
    assert _role_of(resolutions, 'Unit Cost') != 'price'
    assert _role_of(resolutions, 'Current Total Cost') != 'market_value'


def test_constant_columns_do_not_satisfy_an_identity():
    """3 - 2 = 1 on every row is arithmetic trivia, not evidence."""
    columns = ['Symbol', 'Widget A', 'Widget B', 'Widget C']
    rows = [
        {'Symbol': f'S{i}', 'Widget A': 1.0, 'Widget B': 2.0, 'Widget C': 3.0}
        for i in range(10)
    ]
    resolutions, notes = _resolve(columns, rows)

    for widget in ('Widget A', 'Widget B', 'Widget C'):
        assert resolutions[widget].role is None
    assert not notes


# --------------------------------------------------------------------------
# Refusals and precedence
# --------------------------------------------------------------------------

def test_a_column_with_no_evidence_gets_no_role():
    """Silence is a valid answer, and distinct from a low-confidence guess."""
    columns = ['Notes', 'Reference']
    rows = [{'Notes': f'see attachment {i}', 'Reference': f'REF-{i}'}
            for i in range(8)]
    resolutions, _ = _resolve(columns, rows)

    assert resolutions['Notes'].role is None
    assert resolutions['Notes'].band == 'unidentified'


def test_vendor_profile_overrides_every_inference():
    columns = ['Col_1', 'Col_2', 'Col_3', 'Col_4', 'Col_5', 'Col_6', 'Col_7']
    resolutions, _ = _resolve(
        columns, _book_rows(columns),
        role_overrides={'Col_5': 'cost_basis'},
    )
    assert _role_of(resolutions, 'Col_5') == 'cost_basis'
    assert resolutions['Col_5'].source == 'profile'
    assert resolutions['Col_5'].confidence == 1.0


def test_two_columns_do_not_both_claim_a_single_valued_role():
    """Guards a double-counted total."""
    columns = ['Symbol', 'Market Value', 'Ending Market Value']
    rows = [
        {'Symbol': s, 'Market Value': mv, 'Ending Market Value': mv}
        for s, _, _, _, mv, _, _ in _BOOK
    ]
    resolutions, _ = _resolve(columns, rows)
    claims = [c for c in columns if resolutions[c].role == 'market_value']
    assert len(claims) == 1


def test_profile_may_map_two_columns_to_one_role():
    """A profile is a human assertion, so it is allowed to do this on purpose."""
    columns = ['Account Number', 'Account Name', 'Market Value']
    rows = [{'Account Number': 'X1', 'Account Name': 'Smith', 'Market Value': 100}]
    resolutions, _ = _resolve(
        columns, rows,
        role_overrides={'Account Number': 'account', 'Account Name': 'account'},
    )
    assert _role_of(resolutions, 'Account Number') == 'account'
    assert _role_of(resolutions, 'Account Name') == 'account'


def test_money_role_over_a_text_column_is_discounted():
    """"Value Category" is a label, not a number, whatever its name suggests."""
    columns = ['Symbol', 'Value Category']
    rows = [{'Symbol': f'S{i}', 'Value Category': 'Growth'} for i in range(8)]
    resolutions, _ = _resolve(columns, rows, column_types={'Value Category': 'text'})
    assert resolutions['Value Category'].role != 'market_value'


def test_resolution_carries_its_evidence():
    """Every assignment must be explainable — the report renders these."""
    names = ['Col_1', 'Col_2', 'Col_3', 'Col_4', 'Col_5', 'Col_6', 'Col_7']
    resolutions, _ = _resolve(names, _book_rows(names))
    assert resolutions['Col_5'].signals
    assert resolutions['Col_5'].to_dict()['band'] == 'confirmed'


def test_needs_review_flags_the_thin_matches_only():
    confident = ColumnResolution('a', role='market_value', confidence=0.95)
    thin = ColumnResolution('b', role='market_value', confidence=0.45)
    assert not confident.needs_review
    assert thin.needs_review
    assert thin.band == 'uncertain'
