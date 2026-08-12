"""End-to-end ingest of files matching no vendor profile.

The five fixtures under ``tests/fixtures/ingest`` that carry a vendor profile
prove Finn handles the formats it was built for. These three prove the harder
claim — the one a pilot prospect actually tests, by uploading their own export
live:

* ``euro_semicolon_holdings.csv`` — Windows-1252, semicolon-delimited, comma
  decimals, German headers, two preamble lines. Every one of those breaks a
  US-CSV assumption, and none of them announce themselves.
* ``opaque_columns.csv`` — headers carry no meaning at all (``Col_1`` …
  ``Col_7``). Only the arithmetic between columns can say what they hold.
* ``excel_unicode_text.txt`` — Excel's "Unicode Text" export: UTF-16-LE with a
  byte-order mark and tab delimiters.

Each test asserts the *total*, not just that the file parsed. A file that
loads with the wrong decimal convention still loads.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from services.document_extractor import DocumentExtractor
from services.financial.holdings_store import HoldingsStore
from services.indexing.indexer import _ingest_evidence

FIXTURES = Path(__file__).parent / 'fixtures' / 'ingest'


def _ingest(tmp_path: Path, fixture_name: str, as_name: str | None = None):
    """Run one fixture through the real extract → store path.

    Returns ``(store, reports)``. ``as_name`` copies the fixture under a
    different extension, since the extractor dispatches on suffix.
    """
    source = FIXTURES / fixture_name
    assert source.exists(), f'missing fixture {fixture_name}'
    if as_name:
        source = shutil.copyfile(source, tmp_path / as_name)

    store = HoldingsStore(tmp_path / 'metadata.db')
    for sheet in DocumentExtractor().extract_tabular_sheets(Path(source)):
        store.create_table(
            'doc1', fixture_name, sheet['columns'], sheet['rows'],
            sheet_name=sheet['sheet_name'],
            role_overrides=sheet.get('role_overrides'),
            type_overrides=sheet.get('type_overrides'),
            ingest_evidence=_ingest_evidence(sheet),
        )
    return store, store.get_ingest_reports('doc1')


def _column_with_role(report, role):
    for column in report['understood']:
        if column['role'] == role:
            return column
    raise AssertionError(f'no column resolved to {role}')


def _sum_of_role(store, report, role):
    column = _column_with_role(report, role)
    schema = store.get_schemas_for_document('doc1')[0]
    sql_name = next(
        c['sql_name'] for c in schema['columns'] if c['name'] == column['name']
    )
    result = store.execute_query(
        f'SELECT SUM("{sql_name}") AS total FROM "{report["table_name"]}"'
    )
    return result['rows'][0][0]


# --------------------------------------------------------------------------
# European export: encoding, delimiter, and decimal convention all non-default
# --------------------------------------------------------------------------

def test_european_export_lands_with_the_right_total(tmp_path):
    store, reports = _ingest(tmp_path, 'euro_semicolon_holdings.csv')
    assert len(reports) == 1
    report = reports[0]

    assert report['row_count'] == 5
    assert report['column_count'] == 6
    # Read as a US comma CSV this file is one column of text and the total is 0.
    assert _sum_of_role(store, report, 'market_value') == pytest.approx(463_516.50)


def test_european_export_preserves_accented_security_names(tmp_path):
    store, reports = _ingest(tmp_path, 'euro_semicolon_holdings.csv')
    result = store.execute_query(f'SELECT * FROM "{reports[0]["table_name"]}"')
    names = ' '.join(str(value) for row in result['rows'] for value in row)
    assert 'Nestlé' in names
    assert 'Société Générale' in names
    assert 'Zürich' in names
    assert '�' not in names, 'a replacement character means the encoding was wrong'


def test_european_export_skips_its_preamble_and_reports_doing_so(tmp_path):
    _, reports = _ingest(tmp_path, 'euro_semicolon_holdings.csv')
    codes = {f['code'] for f in reports[0]['findings']}
    assert 'header_offset' in codes
    assert 'dialect_detected' in codes

    dialect = next(f for f in reports[0]['findings'] if f['code'] == 'dialect_detected')
    assert 'Windows-1252' in dialect['detail']
    assert 'semicolon' in dialect['detail']
    assert 'European' in dialect['detail']


def test_european_export_identifies_columns_it_has_no_words_for(tmp_path):
    """No German appears anywhere in the resolver. Arithmetic carries it."""
    _, reports = _ingest(tmp_path, 'euro_semicolon_holdings.csv')
    by_name = {c['name']: c for c in reports[0]['understood']}

    assert by_name['Kurswert']['role'] == 'market_value'
    assert by_name['Kurs']['role'] == 'price'
    assert by_name['Stück']['role'] == 'quantity'
    assert by_name['Kurswert']['role_source'] == 'arithmetic'


# --------------------------------------------------------------------------
# Opaque headers
# --------------------------------------------------------------------------

def test_opaque_columns_are_identified_and_total_correctly(tmp_path):
    store, reports = _ingest(tmp_path, 'opaque_columns.csv')
    report = reports[0]

    by_name = {c['name']: c['role'] for c in report['understood']}
    assert by_name['Col_3'] == 'quantity'
    assert by_name['Col_4'] == 'price'
    assert by_name['Col_5'] == 'market_value'
    assert by_name['Col_6'] == 'cost_basis'
    assert by_name['Col_7'] == 'pnl'

    assert _sum_of_role(store, report, 'market_value') == pytest.approx(291_310.00)


def test_opaque_column_resolution_is_explained_in_the_report(tmp_path):
    """An advisor must be able to see *why* Col_5 became the market value."""
    _, reports = _ingest(tmp_path, 'opaque_columns.csv')
    arithmetic = [
        f for f in reports[0]['findings'] if f['code'] == 'arithmetic_resolution'
    ]
    assert len(arithmetic) == 1, 'the explanation must be one finding, not five'
    assert 'Col_5' in arithmetic[0]['detail']
    assert 'quantity x price = market value' in arithmetic[0]['detail']


# --------------------------------------------------------------------------
# Excel "Unicode Text"
# --------------------------------------------------------------------------

def test_utf16_tab_delimited_export_lands_with_the_right_total(tmp_path):
    store, reports = _ingest(
        tmp_path, 'excel_unicode_text.txt', as_name='excel_unicode_text.csv'
    )
    report = reports[0]

    assert report['column_count'] == 5
    assert report['row_count'] == 5
    assert _sum_of_role(store, report, 'market_value') == pytest.approx(251_122.00)

    dialect = next(f for f in report['findings'] if f['code'] == 'dialect_detected')
    assert 'UTF-16' in dialect['detail']
    assert 'tab' in dialect['detail']


# --------------------------------------------------------------------------
# The report must stay quiet when there is nothing to say
# --------------------------------------------------------------------------

@pytest.mark.parametrize('fixture', [
    'schwab_unrealized_gl.csv',
    'pershing_unrealized_gl.csv',
    'fidelity_positions.csv',
    'vanguard_holdings.csv',
])
def test_plain_utf8_exports_report_no_dialect_finding(tmp_path, fixture):
    """Precision guard.

    A finding on every file is a finding on no file. Telling an advisor their
    comma-delimited UTF-8 export was read as comma-delimited UTF-8 trains them
    to skip the report — which is where the findings that matter live.
    """
    _, reports = _ingest(tmp_path, fixture)
    for report in reports:
        codes = [f['code'] for f in report['findings']]
        assert 'dialect_detected' not in codes
