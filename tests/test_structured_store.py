import pytest

from services.structured_chat import (
    build_structured_context,
    describe_tables_for_prompt,
)
from services.structured_store import StructuredStore


def _column_map(schema):
    return {column["name"]: column for column in schema["columns"]}


def test_structured_ingest_preserves_raw_numeric_and_date_values(tmp_path):
    store = StructuredStore(tmp_path / "metadata.db")
    store.create_table(
        document_id="doc-raw",
        filename="data.csv",
        columns=["Trade Date", "Amount", "Quantity"],
        rows=[
            {"Trade Date": "Apr 11, 2026", "Amount": "1591.20", "Quantity": "10"},
            {"Trade Date": "04/12/2026", "Amount": "-408.80", "Quantity": "12"},
        ],
    )

    schema = store.get_schema("doc-raw")
    assert schema is not None
    columns = _column_map(schema)

    trade_date = columns["Trade Date"]
    amount = columns["Amount"]
    quantity = columns["Quantity"]

    assert trade_date["type"] == "date"
    assert amount["type"] == "real"
    assert quantity["type"] == "integer"

    assert trade_date["raw_sql_name"]
    assert amount["raw_sql_name"]
    assert quantity["raw_sql_name"]

    result = store.execute_query(
        'SELECT "Trade_Date", "Trade_Date__raw", "Amount", "Amount__raw", '
        '"Quantity", "Quantity__raw" FROM "csv_data_doc_raw" ORDER BY "__row_number"'
    )
    assert result["rows"] == [
        ["2026-04-11", "Apr 11, 2026", 1591.2, "1591.20", 10, "10"],
        ["2026-04-12", "04/12/2026", -408.8, "-408.80", 12, "12"],
    ]


def _hbil_store(tmp_path):
    """Build a store with a NetX360-shaped table used across the fix tests below."""
    store = StructuredStore(tmp_path / "metadata.db")
    store.create_table(
        document_id="HBIL178aef9d",
        filename="HBIL178aef9d.csv",
        columns=["Account Name", "Ticker", "Value"],
        rows=[
            {"Account Name": "Smith TOD", "Ticker": "AAPL", "Value": "10000"},
            {"Account Name": "Smith TOD", "Ticker": "MSFT", "Value": "20000"},
        ],
    )
    return store


def test_large_tables_block_labels_sql_identifier_unambiguously(tmp_path):
    """The LARGE-TABLES prompt block must call out the SQL identifier explicitly.

    Regression: the model was constructing `"<stem> - <filename>"` as a table
    name when the tables block used the em-dash format `TABLE "X"  — source: Y`.
    The new format must (a) label the SQL identifier with a `sql_table_name:`
    field, (b) include an explicit instruction to not combine fields.
    """
    store = _hbil_store(tmp_path)
    tables = store.list_tables()

    block = describe_tables_for_prompt(tables)

    assert "sql_table_name: csv_data_HBIL178aef9d" in block
    assert "source_file: HBIL178aef9d.csv" in block
    assert "IDENTIFIER RULE" in block
    # No em-dash concatenation of table_name + filename — that's what tripped
    # the model into writing "HBIL178aef9d - HBIL178aef9d.csv".
    assert 'TABLE "csv_data_HBIL178aef9d"  — source' not in block


def test_inline_jsonl_header_surfaces_sql_table_name(tmp_path):
    """Small tables get inlined as JSONL; the header must still tell the model
    the exact SQL identifier so it can fall through to SQL without mangling
    the filename into a pseudo table name."""
    store = _hbil_store(tmp_path)
    tables = store.list_tables()
    for t in tables:
        t["collection_id"] = "hbil-test"

    ctx = build_structured_context(tables, {"hbil-test": store})

    assert ctx["inline_block"], "expected inline JSONL block for small table"
    assert "CSV FILE: HBIL178aef9d.csv" in ctx["inline_block"]
    assert "sql_table_name: csv_data_HBIL178aef9d" in ctx["inline_block"]


def test_suggest_identifiers_recovers_from_stem_hyphen_filename_mangling(tmp_path):
    """The exact bug from the roadmap: model guessed `"X - X.csv"`.

    `suggest_identifiers` must return the real table when the guess is a
    lightly-mangled form of the actual filename/table_name (extra whitespace,
    dashes, filename joined to stem, etc.).
    """
    store = _hbil_store(tmp_path)

    # The exact mangled shape from the observed chat screenshot.
    suggestions = store.suggest_identifiers("HBIL178aef9d - HBIL178aef9d.csv")
    assert suggestions, "expected at least one did-you-mean candidate"
    assert suggestions[0]["table_name"] == "csv_data_HBIL178aef9d"
    assert suggestions[0]["filename"] == "HBIL178aef9d.csv"

    # Other light-mangling shapes the model can plausibly produce.
    for guess in (
        "HBIL178aef9d.csv",
        "'HBIL178aef9d.csv'",
        "HBIL178aef9d",
        "csv_data_HBIL178aef9d",
    ):
        assert store.suggest_identifiers(guess), f"no suggestion for guess={guess!r}"


def test_suggest_identifiers_returns_empty_for_unrelated_name(tmp_path):
    """Confirm the fuzzy matcher is not a free-for-all — unrelated names
    shouldn't trigger false positive did-you-mean hints."""
    store = _hbil_store(tmp_path)
    assert store.suggest_identifiers("completely_unrelated_xyz") == []
