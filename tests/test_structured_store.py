import pytest

import services.financial  # noqa: F401 - registers structured-store plugins
from services.financial.metrics import compute_financial_metric
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
