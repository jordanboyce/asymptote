"""P0.6 — regression tests for silent aggregate coercion.

The scenario these defend against, in full: a prospect drops their own
brokerage export on Finn during a demo. One market-value column doesn't match
any vendor profile and numeric coercion declines it, so it lands as TEXT. The
model asks for a household total, SQLite coerces "1,234.56" to 1.0 per row, and
Finn reports a $10 portfolio as if it were fact.

Wrong-but-silent is the failure mode that ends a pilot conversation, so these
tests pin both halves: that the wrong number is still detected as wrong, and
that correctly-typed data does NOT produce a warning (a guard that cries wolf
gets ignored, which is the same as not having one).
"""

import sqlite3

import pytest

from services.financial.holdings_store import HoldingsStore
from services.tabular.aggregate_guard import (
    check_aggregate_coercion,
    check_null_aggregates,
    extract_aggregated_columns,
    extract_source_tables,
    human_numeric_value,
    is_sum_hazard,
)


# --------------------------------------------------------------------------
# Coercion model — the core of the detector's precision
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1,234.56", 1234.56),
    ("$8,900.00", 8900.0),
    ("(408.80)", -408.8),
    ("10", 10.0),
    ("-42.5", -42.5),
    ("12.5%", 12.5),
    ("1,000,000", 1000000.0),
])
def test_human_numeric_value_reads_spreadsheet_formats(text, expected):
    assert human_numeric_value(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["AAPL", "", "2026-04-11", "N/A", "--", "Total:"])
def test_human_numeric_value_rejects_non_numbers(text):
    assert human_numeric_value(text) is None


@pytest.mark.parametrize("text", ["1,234.56", "$8,900.00", "(408.80)", "1,000,000"])
def test_sum_hazard_flags_values_sqlite_mangles(text):
    assert is_sum_hazard(text) is True


@pytest.mark.parametrize("text", ["10", "1234.56", "-42.5", "AAPL", "12.5%"])
def test_sum_hazard_ignores_values_sqlite_sums_correctly(text):
    """Plain numeric TEXT sums correctly in SQLite — warning here would be noise."""
    assert is_sum_hazard(text) is False


# --------------------------------------------------------------------------
# SQL parsing
# --------------------------------------------------------------------------

def test_extract_aggregated_columns_handles_quoting_styles():
    sql = ('SELECT SUM("Market Value"), AVG([Cost Basis]), MAX(quantity) '
           'FROM "csv_data_x"')
    found = extract_aggregated_columns(sql)
    assert ("SUM", "Market Value") in found
    assert ("AVG", "Cost Basis") in found
    assert ("MAX", "quantity") in found


def test_extract_aggregated_columns_skips_already_cast_operands():
    """A caller who has already CAST is doing the right thing — don't nag."""
    sql = 'SELECT SUM(CAST("Market_Value" AS REAL)) FROM "csv_data_x"'
    assert extract_aggregated_columns(sql) == []


def test_extract_aggregated_columns_skips_expressions():
    sql = 'SELECT SUM("qty" * "price") FROM "csv_data_x"'
    assert extract_aggregated_columns(sql) == []


def test_extract_source_tables_covers_from_and_join():
    sql = 'SELECT * FROM "a" JOIN b ON a.id = b.id JOIN [c d] ON 1=1'
    assert extract_source_tables(sql) == ["a", "b", "c d"]


# --------------------------------------------------------------------------
# Integration against a real store
# --------------------------------------------------------------------------

def _text_money_table(tmp_path):
    """A table whose market-value column landed as TEXT with separators.

    Built through raw sqlite3 rather than HoldingsStore.create_table, because
    the ingest pipeline would correctly coerce these — the whole point is to
    reproduce the state where coercion *failed*.
    """
    db = tmp_path / "metadata.db"
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE "csv_data_bad" ("Symbol" TEXT, "Market_Value" TEXT)')
    conn.executemany(
        'INSERT INTO "csv_data_bad" VALUES (?, ?)',
        [("AAPL", "1,234.56"), ("MSFT", "2,500.00"), ("VTI", "8,611.04")],
    )
    conn.commit()
    conn.close()
    return db


def test_sum_over_text_column_is_actually_wrong(tmp_path):
    """Document the bug itself: SQLite returns 11.0 for a $12,345.60 portfolio."""
    db = _text_money_table(tmp_path)
    conn = sqlite3.connect(db)
    total = conn.execute('SELECT SUM("Market_Value") FROM "csv_data_bad"').fetchone()[0]
    conn.close()
    # 1 + 2 + 8 — each string truncated at its thousands separator.
    assert total == pytest.approx(11.0)
    assert total != pytest.approx(12345.60)


def test_execute_query_warns_on_text_sum(tmp_path):
    db = _text_money_table(tmp_path)
    store = HoldingsStore(db)

    result = store.execute_query('SELECT SUM("Market_Value") FROM "csv_data_bad"')

    assert "warnings" in result, "silent wrong total — the exact P0.6 failure"
    joined = " ".join(result["warnings"])
    assert "aggregation_text_operand" in joined
    assert "Market_Value" in joined
    assert "1,234.56" in joined, "warning should quote a real offending value"
    assert "DO NOT report this number" in joined


def test_execute_query_is_silent_on_properly_typed_column(tmp_path):
    """No warning when ingest did its job — guards must not cry wolf."""
    store = HoldingsStore(tmp_path / "metadata.db")
    store.create_table(
        document_id="doc-clean",
        filename="portfolio.csv",
        columns=["Symbol", "Market Value"],
        rows=[
            {"Symbol": "AAPL", "Market Value": "$1,234.56"},
            {"Symbol": "MSFT", "Market Value": "$2,500.00"},
        ],
    )
    schema = store.get_schema("doc-clean")
    table = schema["table_name"]
    col = next(c["sql_name"] for c in schema["columns"] if c["name"] == "Market Value")

    result = store.execute_query(f'SELECT SUM("{col}") FROM "{table}"')

    assert result["rows"][0][0] == pytest.approx(3734.56)
    assert "warnings" not in result


def test_execute_query_is_silent_when_caller_casts(tmp_path):
    db = _text_money_table(tmp_path)
    store = HoldingsStore(db)

    result = store.execute_query(
        'SELECT SUM(CAST(REPLACE("Market_Value", \',\', \'\') AS REAL)) '
        'FROM "csv_data_bad"'
    )

    assert result["rows"][0][0] == pytest.approx(12345.60)
    assert "warnings" not in result


def test_no_warning_for_aggregate_over_text_labels(tmp_path):
    """MAX over a ticker column is TEXT but not a coercion bug."""
    db = _text_money_table(tmp_path)
    store = HoldingsStore(db)

    result = store.execute_query('SELECT MAX("Symbol") FROM "csv_data_bad"')

    assert "warnings" not in result


def test_min_over_ragged_numeric_text_warns(tmp_path):
    """Lexicographic comparison puts "9" above "10" — flag it."""
    db = tmp_path / "metadata.db"
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE "csv_data_q" ("Quantity" TEXT)')
    conn.executemany('INSERT INTO "csv_data_q" VALUES (?)',
                     [("9",), ("10",), ("100",)])
    conn.commit()
    conn.close()

    result = HoldingsStore(db).execute_query('SELECT MIN("Quantity") FROM "csv_data_q"')

    assert result["rows"][0][0] == "10", "lexicographic MIN, not numeric"
    assert "aggregation_text_comparison" in " ".join(result["warnings"])


def test_guard_survives_unknown_column(tmp_path):
    """A column the guard can't resolve must not break the query path."""
    db = _text_money_table(tmp_path)
    conn = sqlite3.connect(db)
    warnings = check_aggregate_coercion(conn, 'SELECT SUM("nope") FROM "csv_data_bad"')
    conn.close()
    assert warnings == []


# --------------------------------------------------------------------------
# NULL backstop
# --------------------------------------------------------------------------

def test_null_aggregate_backstop_flags_all_null_result():
    warnings = check_null_aggregates(
        'SELECT SUM("x") FROM t', ['SUM("x")'], [[None]],
    )
    assert warnings and "aggregation_null" in warnings[0]


def test_null_aggregate_backstop_ignores_real_values():
    assert check_null_aggregates(
        'SELECT SUM("x") FROM t', ['SUM("x")'], [[42.0]],
    ) == []


def test_null_aggregate_backstop_ignores_non_aggregate_queries():
    assert check_null_aggregates(
        'SELECT "x" FROM t', ['x'], [[None]],
    ) == []
