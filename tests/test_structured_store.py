import pytest

import services.financial  # noqa: F401 - registers structured-store plugins
from services.financial.metrics import compute_financial_metric
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
        filename="portfolio.csv",
        columns=["Trade Date", "Market Value", "Quantity"],
        rows=[
            {"Trade Date": "Apr 11, 2026", "Market Value": "$1,591.20", "Quantity": "10"},
            {"Trade Date": "04/12/2026", "Market Value": "(408.80)", "Quantity": "12"},
        ],
    )

    schema = store.get_schema("doc-raw")
    assert schema is not None
    columns = _column_map(schema)

    trade_date = columns["Trade Date"]
    market_value = columns["Market Value"]
    quantity = columns["Quantity"]

    assert trade_date["type"] == "date"
    assert market_value["type"] in {"currency", "real"}
    assert quantity["type"] == "integer"

    assert trade_date["raw_sql_name"]
    assert market_value["raw_sql_name"]
    assert quantity["raw_sql_name"]

    result = store.execute_query(
        'SELECT "Trade_Date", "Trade_Date__raw", "Market_Value", "Market_Value__raw", '
        '"Quantity", "Quantity__raw" FROM "csv_data_doc_raw" ORDER BY "__row_number"'
    )
    assert result["rows"] == [
        ["2026-04-11", "Apr 11, 2026", 1591.2, "$1,591.20", 10, "10"],
        ["2026-04-12", "04/12/2026", -408.8, "(408.80)", 12, "12"],
    ]


def test_financial_metrics_roll_up_tax_lots_by_symbol(tmp_path):
    store = StructuredStore(tmp_path / "metadata.db")
    store.create_table(
        document_id="doc-rollup",
        filename="positions.csv",
        columns=[
            "Security Description",
            "Symbol",
            "Quantity",
            "Market Value",
            "Current Total Cost",
        ],
        rows=[
            {
                "Security Description": "PFIZER INC COM",
                "Symbol": "PFE",
                "Quantity": "4,800.0000",
                "Market Value": "129,216.00",
                "Current Total Cost": "7,474.98",
            },
            {
                "Security Description": "PFIZER INC COM",
                "Symbol": "PFE",
                "Quantity": "1,200.0000",
                "Market Value": "32,304.00",
                "Current Total Cost": "39,065.19",
            },
            {
                "Security Description": "TEXAS INSTRS INC COM",
                "Symbol": "TXN",
                "Quantity": "4,800.0000",
                "Market Value": "1,030,704.00",
                "Current Total Cost": "10,340.00",
            },
        ],
    )

    rolled_up = compute_financial_metric(store, "doc-rollup", "row_count")
    raw = compute_financial_metric(store, "doc-rollup", "row_count", group_by_symbol=False)

    assert rolled_up["value"] == 2
    assert rolled_up["table_used"].endswith("__by_symbol")
    assert raw["value"] == 3
    assert raw["table_used"] == "csv_data_doc_rollup"

    rollup_rows = store.execute_query(
        'SELECT "Symbol", "Quantity", "Current_Total_Cost", "lot_count", "unit_cost" '
        'FROM "csv_data_doc_rollup__by_symbol" ORDER BY "Symbol"'
    )
    assert rollup_rows["rows"][0][0] == "PFE"
    assert rollup_rows["rows"][0][1] == pytest.approx(6000.0)
    assert rollup_rows["rows"][0][2] == pytest.approx(46540.17)
    assert rollup_rows["rows"][0][3] == 2
    assert rollup_rows["rows"][0][4] == pytest.approx(7.756695, rel=1e-6)

    top_holdings = compute_financial_metric(store, "doc-rollup", "top_holdings", limit=5)
    assert top_holdings["table_used"].endswith("__by_symbol")
    assert len(top_holdings["rows"]) == 2


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


def test_compute_financial_metric_did_you_mean_on_bad_identifier(tmp_path):
    """The canned-metric path should surface the did-you-mean hint in its error
    so the chat model self-corrects without needing a `list_tables` round-trip."""
    store = _hbil_store(tmp_path)
    with pytest.raises(ValueError) as exc:
        compute_financial_metric(store, "HBIL178aef9d - HBIL178aef9d.csv", "row_count")
    msg = str(exc.value)
    assert "Did you mean" in msg
    assert "csv_data_HBIL178aef9d" in msg
