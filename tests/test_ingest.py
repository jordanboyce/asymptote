"""Regression tests for the ingestion-fidelity pipeline (v4.1 P0.x).

Each test loads a real-shaped anonymized fixture through the same path used
in production (DocumentExtractor → StructuredStore) and asserts:

  - Header detection: correct column names, expected column count
  - Type inference:   numeric columns land as REAL/currency, not TEXT
  - Role detection:   ticker, market_value, cost_basis, pnl roles present
  - Numeric accuracy: key aggregate within expected bounds
  - Lot rollup:       __by_symbol view exists when ticker role present
  - Sanity guards:    no spurious warnings on well-formed data

Run with:
    pytest tests/test_ingest.py -v
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

import services.financial  # noqa: F401 - registers currency/percent type extensions

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_tabular_sheets(csv_path: Path) -> List[Dict[str, Any]]:
    """Call extract_tabular_sheets without importing DocumentExtractor at module level.

    DocumentExtractor has top-level imports of pdfplumber/pypdf which may not
    be installed in the test environment.  We import the module lazily and, if
    the import fails, fall back to a minimal pandas-only extractor so the
    ingestion-specific tests can still run.
    """
    try:
        # Lazy import to avoid triggering pdfplumber at collection time
        import importlib
        extractor_mod = importlib.import_module("services.document_extractor")
        extractor = extractor_mod.DocumentExtractor()
        return extractor.extract_tabular_sheets(csv_path)
    except ImportError:
        # Fallback: minimal CSV reader + header sniffing + profile detection.
        # Also handles NetX360 HBIL hierarchical format via the preprocessor.
        import pandas as pd
        from services.ingest_profiles import detect_profile, apply_profile

        # NetX360 HBIL: hierarchical format — bypass normal pandas read entirely
        try:
            from services.ingest_profiles.netx360 import is_netx360_hbil, preprocess_hbil
            if is_netx360_hbil(csv_path):
                columns, rows = preprocess_hbil(csv_path)
                profile = detect_profile(csv_path.name, columns, csv_path)
                role_overrides: Dict[str, str] = {}
                type_overrides: Dict[str, str] = {}
                if profile:
                    _, rows, role_overrides, type_overrides = apply_profile(profile, columns, rows)
                return [{
                    "sheet_name": "",
                    "columns": columns,
                    "rows": rows,
                    "row_texts": [],
                    "role_overrides": role_overrides,
                    "type_overrides": type_overrides,
                    "vendor_profile": profile.get("display_name") if profile else None,
                }]
        except Exception:
            pass  # fall through to normal CSV path

        # Header sniffing: parse CSV line-by-line, pad to max width, find header
        import csv as _csv
        import io as _io
        import re as _re
        _NUM_RE = _re.compile(r'^[\s$€£¥₹(]?-?[\d,]+\.?\d*\s*[%KMBkmb]?\s*[)%]?$')

        raw_rows: List[List[str]] = []
        with open(csv_path, encoding='utf-8', errors='replace') as _fh:
            for i, row in enumerate(_csv.reader(_fh)):
                if i >= 30:
                    break
                raw_rows.append([str(v).strip() for v in row])

        max_cols = max((len(r) for r in raw_rows), default=1)
        padded = [r + [''] * (max_cols - len(r)) for r in raw_rows]

        # Score each row as potential header
        best_row = 0
        best_score = -1.0
        for i in range(min(len(padded) - 1, 29)):
            cells = [c for c in padded[i] if c]
            if not cells:
                continue
            non_num = sum(1 for c in cells if not _NUM_RE.match(c)) / len(cells)
            next_cells = [c for c in padded[i + 1] if c]
            next_num = sum(1 for c in next_cells if _NUM_RE.match(c)) / max(len(next_cells), 1)
            score = non_num * (1 + next_num)
            if score > best_score:
                best_score = score
                best_row = i

        header_row = best_row if best_score >= 0.3 else 0
        if header_row > 0:
            df = pd.read_csv(csv_path, skiprows=list(range(header_row)), header=0, on_bad_lines='skip')
        else:
            df = pd.read_csv(csv_path, on_bad_lines='skip')
        columns = [str(c) for c in df.columns]
        rows: List[Dict[str, Any]] = []
        import pandas as _pd
        for _, row in df.iterrows():
            row_dict: Dict[str, Any] = {}
            for col in columns:
                val = row[col]
                row_dict[col] = None if _pd.isna(val) else (
                    val if isinstance(val, (int, float, bool)) else str(val)
                )
            rows.append(row_dict)

        profile = detect_profile(csv_path.name, columns, csv_path)
        role_overrides: Dict[str, str] = {}
        type_overrides: Dict[str, str] = {}
        if profile:
            _, rows, role_overrides, type_overrides = apply_profile(profile, columns, rows)

        return [{
            "sheet_name": "",
            "columns": columns,
            "rows": rows,
            "row_texts": [],
            "role_overrides": role_overrides,
            "type_overrides": type_overrides,
            "vendor_profile": profile.get("display_name") if profile else None,
        }]


def _ingest_csv(csv_path: Path) -> "StructuredStore":
    """Run the full extract → structured-store pipeline and return the store."""
    from services.structured_store import StructuredStore

    sheets = _extract_tabular_sheets(csv_path)
    assert sheets, f"No sheets extracted from {csv_path.name}"

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    store = StructuredStore(db_path)
    for sheet in sheets:
        store.create_table(
            document_id="test_doc",
            filename=csv_path.name,
            columns=sheet["columns"],
            rows=sheet["rows"],
            sheet_name=sheet["sheet_name"],
            role_overrides=sheet.get("role_overrides") or {},
            type_overrides=sheet.get("type_overrides") or {},
        )
    return store


def _get_schema(store: "StructuredStore", filename: str) -> Dict[str, Any]:
    schema = store.get_schema(filename)
    assert schema is not None, f"No schema found for {filename}"
    return schema


def _col_by_role(schema: Dict[str, Any], role: str) -> str | None:
    for c in schema["columns"]:
        if c.get("role") == role:
            return c["sql_name"]
    return None


def _col_type(schema: Dict[str, Any], sql_name: str) -> str | None:
    for c in schema["columns"]:
        if c["sql_name"] == sql_name:
            return c["type"]
    return None


def _view_exists(store: "StructuredStore", view_name: str) -> bool:
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",
            (view_name,),
        ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Schwab Unrealized G/L
# ---------------------------------------------------------------------------

class TestSchwabUnrealizedGL:
    """Fixture: schwab_unrealized_gl.csv

    8 data rows (7 positions + 1 cash row; subtotal/header rows should be
    dropped or handled gracefully).  Key checks: currency strings with $ and
    commas land as REAL, accounting negatives (1,234) coerce correctly.
    """

    @pytest.fixture(scope="class")
    def store(self):
        return _ingest_csv(FIXTURES / "schwab_unrealized_gl.csv")

    @pytest.fixture(scope="class")
    def schema(self, store):
        return _get_schema(store, "schwab_unrealized_gl.csv")

    def test_has_rows(self, schema):
        # 8 data rows; header + preamble + subtotal row excluded
        assert schema["row_count"] >= 7

    def test_column_count(self, schema):
        assert schema["column_count"] >= 9

    def test_ticker_role(self, schema):
        assert _col_by_role(schema, "ticker") is not None

    def test_market_value_role(self, schema):
        assert _col_by_role(schema, "market_value") is not None

    def test_market_value_is_numeric(self, schema):
        mv_col = _col_by_role(schema, "market_value")
        assert mv_col is not None
        col_type = _col_type(schema, mv_col)
        assert col_type in ("real", "currency", "integer"), (
            f"market_value column '{mv_col}' has type '{col_type}', expected numeric. "
            "Currency strings like '$28,686.00' are not being coerced."
        )

    def test_cost_basis_role(self, schema):
        assert _col_by_role(schema, "cost_basis") is not None

    def test_pnl_role(self, schema):
        assert _col_by_role(schema, "pnl") is not None

    def test_pnl_is_numeric(self, schema):
        pnl_col = _col_by_role(schema, "pnl")
        assert pnl_col is not None
        col_type = _col_type(schema, pnl_col)
        assert col_type in ("real", "currency", "integer"), (
            f"pnl column '{pnl_col}' has type '{col_type}', expected numeric. "
            "Accounting negatives like '($3,868.50)' may not be coerced."
        )

    def test_total_market_value_plausible(self, store, schema):
        from services.financial.metrics import compute_financial_metric
        mv_col = _col_by_role(schema, "market_value")
        assert mv_col is not None
        result = compute_financial_metric(store, "schwab_unrealized_gl.csv", "total_market_value")
        val = result["value"]
        assert val is not None, "total_market_value returned NULL — column likely stored as TEXT"
        assert val > 1_000, f"total_market_value = {val}, expected > $1,000 for this fixture"
        assert "warnings" not in result or not any(
            "near_zero" in w for w in result.get("warnings", [])
        )

    def test_accounting_negative_coerces(self, store, schema):
        """TSLA position has ($3,868.50) gain/loss — must store as -3868.50."""
        pnl_col = _col_by_role(schema, "pnl")
        if pnl_col is None:
            pytest.skip("no pnl role detected")
        table = schema["table_name"]
        with sqlite3.connect(store.db_path) as conn:
            row = conn.execute(
                f'SELECT MIN("{pnl_col}") FROM "{table}"'
            ).fetchone()
        min_val = row[0]
        assert min_val is not None
        assert min_val < 0, (
            f"Min pnl = {min_val}; TSLA has ($3,868.50) loss which should be negative"
        )

    def test_by_symbol_view_exists(self, store, schema):
        view_name = schema["table_name"] + "__by_symbol"
        assert _view_exists(store, view_name), (
            f"Rollup view '{view_name}' was not created"
        )

    def test_top_holdings_uses_rollup(self, store):
        from services.financial.metrics import compute_financial_metric
        result = compute_financial_metric(store, "schwab_unrealized_gl.csv", "top_holdings", limit=5)
        assert "rows" in result
        assert len(result["rows"]) > 0


# ---------------------------------------------------------------------------
# Fidelity Positions
# ---------------------------------------------------------------------------

class TestFidelityPositions:
    """Fixture: fidelity_positions.csv

    Mix of stocks and mutual funds.  Pending Activity row with '--' values
    should not poison numeric columns.
    """

    @pytest.fixture(scope="class")
    def store(self):
        return _ingest_csv(FIXTURES / "fidelity_positions.csv")

    @pytest.fixture(scope="class")
    def schema(self, store):
        return _get_schema(store, "fidelity_positions.csv")

    def test_has_rows(self, schema):
        assert schema["row_count"] >= 5

    def test_market_value_is_numeric(self, schema):
        mv_col = _col_by_role(schema, "market_value")
        assert mv_col is not None, "No market_value role detected for Fidelity fixture"
        col_type = _col_type(schema, mv_col)
        assert col_type in ("real", "currency", "integer"), (
            f"market_value column type is '{col_type}' — expected numeric"
        )

    def test_null_markers_become_null(self, store, schema):
        """The '--' Pending Activity row should produce NULLs, not zeros or strings."""
        qty_col = _col_by_role(schema, "quantity")
        if qty_col is None:
            pytest.skip("no quantity role detected")
        table = schema["table_name"]
        with sqlite3.connect(store.db_path) as conn:
            null_count = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{qty_col}" IS NULL'
            ).fetchone()[0]
        assert null_count >= 1, (
            "Expected at least one NULL quantity for the '--' Pending Activity row"
        )

    def test_fractional_shares_preserved(self, store, schema):
        """FXAIX row has 500.123 shares — fractional part must survive."""
        qty_col = _col_by_role(schema, "quantity")
        if qty_col is None:
            pytest.skip("no quantity role detected")
        table = schema["table_name"]
        with sqlite3.connect(store.db_path) as conn:
            rows = conn.execute(
                f'SELECT "{qty_col}" FROM "{table}" WHERE "{qty_col}" > 500 AND "{qty_col}" < 501'
            ).fetchall()
        assert rows, "Fractional quantity 500.123 was not preserved (rounded or truncated)"


# ---------------------------------------------------------------------------
# Pershing Unrealized G/L
# ---------------------------------------------------------------------------

class TestPershingUnrealizedGL:
    """Fixture: pershing_unrealized_gl.csv

    Preamble header row; CUSIP column; accounting-negative pnl.
    """

    @pytest.fixture(scope="class")
    def store(self):
        return _ingest_csv(FIXTURES / "pershing_unrealized_gl.csv")

    @pytest.fixture(scope="class")
    def schema(self, store):
        return _get_schema(store, "pershing_unrealized_gl.csv")

    def test_has_rows(self, schema):
        assert schema["row_count"] >= 5

    def test_ticker_or_cusip_role(self, schema):
        ticker_col = _col_by_role(schema, "ticker")
        cusip_col = _col_by_role(schema, "cusip")
        assert ticker_col is not None or cusip_col is not None, (
            "Neither ticker nor cusip role detected for Pershing fixture"
        )

    def test_market_value_numeric(self, schema):
        mv_col = _col_by_role(schema, "market_value")
        assert mv_col is not None, "No market_value role in Pershing fixture"
        col_type = _col_type(schema, mv_col)
        assert col_type in ("real", "currency", "integer")

    def test_negative_pnl_coerces(self, store, schema):
        """BND row has ($910.00) unrealized G/L — must be stored as -910."""
        pnl_col = _col_by_role(schema, "pnl")
        if pnl_col is None:
            pytest.skip("no pnl role detected")
        table = schema["table_name"]
        with sqlite3.connect(store.db_path) as conn:
            min_val = conn.execute(
                f'SELECT MIN("{pnl_col}") FROM "{table}"'
            ).fetchone()[0]
        assert min_val is not None and min_val < 0, (
            f"Min pnl = {min_val}; BND row has ($910.00) which should be negative"
        )

    def test_by_symbol_view_exists(self, store, schema):
        view_name = schema["table_name"] + "__by_symbol"
        assert _view_exists(store, view_name)

    def test_total_market_value_plausible(self, store):
        from services.financial.metrics import compute_financial_metric
        result = compute_financial_metric(store, "pershing_unrealized_gl.csv", "total_market_value")
        val = result["value"]
        assert val is not None and val > 100_000, (
            f"total_market_value = {val}, expected > $100,000 for this fixture"
        )


# ---------------------------------------------------------------------------
# Vanguard Holdings
# ---------------------------------------------------------------------------

class TestVanguardHoldings:
    """Fixture: vanguard_holdings.csv

    Preamble row; shares with 3 decimal places; cash row with '--' symbol.
    """

    @pytest.fixture(scope="class")
    def store(self):
        return _ingest_csv(FIXTURES / "vanguard_holdings.csv")

    @pytest.fixture(scope="class")
    def schema(self, store):
        return _get_schema(store, "vanguard_holdings.csv")

    def test_has_rows(self, schema):
        assert schema["row_count"] >= 5

    def test_market_value_numeric(self, schema):
        mv_col = _col_by_role(schema, "market_value")
        assert mv_col is not None, "No market_value role in Vanguard fixture"
        col_type = _col_type(schema, mv_col)
        assert col_type in ("real", "currency", "integer")

    def test_by_symbol_view_exists(self, store, schema):
        view_name = schema["table_name"] + "__by_symbol"
        assert _view_exists(store, view_name)

    def test_negative_gain_coerces(self, store, schema):
        """VBTLX row has ($190.00) unrealized gain/loss — must store as -190."""
        pnl_col = _col_by_role(schema, "pnl")
        if pnl_col is None:
            pytest.skip("no pnl role detected")
        table = schema["table_name"]
        with sqlite3.connect(store.db_path) as conn:
            min_val = conn.execute(
                f'SELECT MIN("{pnl_col}") FROM "{table}"'
            ).fetchone()[0]
        assert min_val is not None and min_val < 0


# ---------------------------------------------------------------------------
# NetX360 Holdings by Investor (HBIL) — hierarchical format
# ---------------------------------------------------------------------------

class TestNetX360HoldingsByInvestor:
    """Fixture: netx360_holdings_by_investor.csv

    Hierarchical multi-account export: multiple ASSET header rows interspersed
    with account metadata.  The preprocessor must flatten it into a single
    table with Account Name / Account Number / Account Type added to each row.
    """

    FIXTURE = FIXTURES / "netx360_holdings_by_investor.csv"

    @pytest.fixture(scope="class")
    def store(self):
        return _ingest_csv(self.FIXTURE)

    @pytest.fixture(scope="class")
    def schema(self, store):
        return _get_schema(store, "netx360_holdings_by_investor.csv")

    def test_file_detected_as_hbil(self):
        """Signature detection: is_netx360_hbil must return True for the fixture."""
        from services.ingest_profiles.netx360 import is_netx360_hbil
        assert is_netx360_hbil(self.FIXTURE)

    def test_preprocessor_emits_expected_columns(self):
        """Preprocessor must produce the canonical FLAT_COLUMNS."""
        from services.ingest_profiles.netx360 import preprocess_hbil, FLAT_COLUMNS
        columns, rows = preprocess_hbil(self.FIXTURE)
        assert set(columns) == set(FLAT_COLUMNS)

    def test_preprocessor_strips_account_headers_and_totals(self):
        """No Account Total or structural rows should appear in output."""
        from services.ingest_profiles.netx360 import preprocess_hbil
        _, rows = preprocess_hbil(self.FIXTURE)
        for row in rows:
            asset = (row.get("Asset") or "").lower()
            assert not asset.startswith("account total"), (
                f"Account Total row leaked into output: {row}"
            )
            assert asset not in ("total", ""), (
                f"Structural row leaked into output: {row}"
            )

    def test_preprocessor_row_count(self):
        """Fixture has 7 position rows (4 in account 1, 3 in account 2)."""
        from services.ingest_profiles.netx360 import preprocess_hbil
        _, rows = preprocess_hbil(self.FIXTURE)
        assert len(rows) == 7, f"Expected 7 position rows, got {len(rows)}"

    def test_account_name_propagated(self):
        """Account Name must be non-null on every row."""
        from services.ingest_profiles.netx360 import preprocess_hbil
        _, rows = preprocess_hbil(self.FIXTURE)
        for row in rows:
            assert row.get("Account Name"), f"Missing Account Name on row: {row}"

    def test_multiple_accounts_represented(self):
        """Both accounts from the fixture must appear."""
        from services.ingest_profiles.netx360 import preprocess_hbil
        _, rows = preprocess_hbil(self.FIXTURE)
        account_numbers = {row.get("Account Number") for row in rows}
        assert "SMP-001234" in account_numbers
        assert "SMP-005678" in account_numbers

    def test_has_rows(self, schema):
        assert schema["row_count"] >= 5

    def test_market_value_role_present(self, schema):
        mv_col = _col_by_role(schema, "market_value")
        assert mv_col is not None, "No market_value role detected for NetX360 fixture"

    def test_market_value_numeric(self, schema):
        mv_col = _col_by_role(schema, "market_value")
        if mv_col is None:
            pytest.skip("market_value role not detected")
        col_type = _col_type(schema, mv_col)
        assert col_type in ("real", "currency", "integer"), (
            f"market_value column type = '{col_type}', expected numeric"
        )

    def test_ticker_role_present(self, schema):
        ticker_col = _col_by_role(schema, "ticker")
        assert ticker_col is not None, "No ticker role detected for NetX360 fixture"

    def test_vendor_profile_matched(self, store):
        """The NetX360 HBIL YAML profile must be matched at ingest time."""
        sheets = _extract_tabular_sheets(self.FIXTURE)
        assert sheets, "No sheets returned"
        vp = sheets[0].get("vendor_profile")
        assert vp is not None and "NetX360" in vp, (
            f"Expected NetX360 vendor_profile, got: {vp!r}"
        )

    def test_by_symbol_view_exists(self, store, schema):
        view_name = schema["table_name"] + "__by_symbol"
        assert _view_exists(store, view_name)

    def test_total_market_value_plausible(self, store):
        """Fixture total value is $1,154,647.30 — metric must be in the right ballpark."""
        from services.financial.metrics import compute_financial_metric
        result = compute_financial_metric(
            store, "netx360_holdings_by_investor.csv", "total_market_value"
        )
        val = result["value"]
        assert val is not None and val > 500_000, (
            f"total_market_value = {val}, expected > $500,000 for this fixture"
        )


# ---------------------------------------------------------------------------
# Unit tests for _parse_number
# ---------------------------------------------------------------------------

class TestParseNumber:
    """Direct tests of the numeric coercion helper."""

    @pytest.fixture(autouse=True)
    def import_fn(self):
        from services.financial.type_hints import _parse_number
        self.parse = _parse_number

    def test_plain_float(self):
        assert self.parse("1591.20") == pytest.approx(1591.20)

    def test_comma_separated(self):
        assert self.parse("1,591.20") == pytest.approx(1591.20)

    def test_dollar_comma(self):
        assert self.parse("$1,591.20") == pytest.approx(1591.20)

    def test_accounting_negative(self):
        assert self.parse("(3,868.50)") == pytest.approx(-3868.50)

    def test_accounting_negative_with_dollar(self):
        assert self.parse("($3,868.50)") == pytest.approx(-3868.50)

    def test_k_suffix(self):
        assert self.parse("1.5K") == pytest.approx(1500.0)

    def test_m_suffix(self):
        assert self.parse("2.3M") == pytest.approx(2_300_000.0)

    def test_b_suffix(self):
        assert self.parse("1.2B") == pytest.approx(1_200_000_000.0)

    def test_percent(self):
        assert self.parse("27.49%") == pytest.approx(27.49)

    def test_null_markers(self):
        for marker in ("--", "-", "N/A", "n/a", "N.A.", "nan", "NULL", ""):
            assert self.parse(marker) is None, f"Expected None for {marker!r}"

    def test_currency_code_suffix(self):
        assert self.parse("100 USD") == pytest.approx(100.0)

    def test_negative_plain(self):
        assert self.parse("-42.5") == pytest.approx(-42.5)


# ---------------------------------------------------------------------------
# Unit tests for infer_column_type (80% threshold)
# ---------------------------------------------------------------------------

class TestInferColumnType:
    """Verify the 80% threshold allows minority non-numeric values."""

    @pytest.fixture(autouse=True)
    def import_fn(self):
        from services.structured_store import infer_column_type
        self.infer = infer_column_type

    def test_pure_numeric_is_real(self):
        vals = ["1591.20", "2340.50", "875.00", "12045.60"]
        result = self.infer(vals)
        assert result["type"] in ("real", "currency", "integer")

    def test_currency_strings_are_numeric(self):
        vals = ["$1,591.20", "$2,340.50", "$875.00", "$12,045.60"]
        result = self.infer(vals)
        assert result["type"] in ("real", "currency", "integer"), (
            f"Expected numeric type, got '{result['type']}'. "
            "Currency strings should be coerced."
        )

    def test_minority_dash_does_not_poison_column(self):
        """9 numeric + 1 '--' = 90% numeric → should still be real/currency."""
        vals = ["$1,000.00"] * 9 + ["--"]
        result = self.infer(vals)
        assert result["type"] in ("real", "currency", "integer"), (
            f"Got type '{result['type']}' — minority '--' poisoned a numeric column"
        )

    def test_mostly_text_is_text(self):
        vals = ["AAPL", "MSFT", "AMZN", "NVDA", "123.45"]
        result = self.infer(vals)
        assert result["type"] == "text"

    def test_accounting_negatives_column(self):
        vals = ["$1,000.00", "($500.00)", "$2,500.00", "($100.00)", "$3,000.00"]
        result = self.infer(vals)
        assert result["type"] in ("real", "currency", "integer"), (
            f"Accounting-negative column should be numeric, got '{result['type']}'"
        )

    def test_all_null_is_text(self):
        vals = [None, None, None]
        result = self.infer(vals)
        assert result["type"] == "text"
