"""Regression tests for v4.1 P0.8 — PDF table extraction.

Builds a small PDF programmatically (via reportlab), runs it through
``DocumentExtractor.extract_pdf_tables``, and asserts that the resulting
sheet flows through ``HoldingsStore`` with the same role / type fidelity
the equivalent CSV gets. Also covers the "no tables = no regression"
contract for prose-only PDFs.

Run with:
    pytest tests/test_pdf_table_extraction.py -v
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import List

import pytest

reportlab = pytest.importorskip(
    "reportlab",
    reason="reportlab is required to programmatically build the PDF fixtures.",
)
pdfplumber = pytest.importorskip(
    "pdfplumber",
    reason="pdfplumber is required to extract tables from the generated PDFs.",
)

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# A Schwab-shaped Unrealized Gain/Loss snapshot. Column names match
# `services/financial/roles.py` heuristics so the test does not depend on
# vendor-profile filename matching — just the core role detection +
# numeric coercion path.
HOLDINGS_HEADER = ["Symbol", "Description", "Qty", "Price", "Mkt Val", "Cost Basis", "Gain/Loss $"]
HOLDINGS_ROWS = [
    ["AAPL",  "Apple Inc",            "120",  "$182.50",  "$21,900.00",  "$15,000.00",  "$6,900.00"],
    ["MSFT",  "Microsoft Corp",       "75",   "$415.00",  "$31,125.00",  "$18,750.00",  "$12,375.00"],
    ["NVDA",  "NVIDIA Corp",          "40",   "$910.00",  "$36,400.00",  "$24,000.00",  "$12,400.00"],
    ["TSLA",  "Tesla Inc",            "30",   "$245.50",  "$7,365.00",   "$11,250.00",  "($3,885.00)"],
    ["VTI",   "Vanguard Total Mkt",   "200",  "$255.00",  "$51,000.00",  "$48,000.00",  "$3,000.00"],
    ["BND",   "Vanguard Total Bond",  "150",  "$72.50",   "$10,875.00",  "$11,250.00",  "($375.00)"],
]


def _build_holdings_pdf(path: Path, *, with_prose: bool = False) -> None:
    """Render a one-page PDF whose page-1 content is a holdings table.

    When ``with_prose`` is set, also include a short paragraph above the
    table to exercise the "tables AND text" acceptance criterion.

    The title paragraph "Unrealized Gain/Loss Report" plus the Schwab-shaped
    column names mean the Schwab Unrealized profile activates against this
    fixture (filename glob + ``required_strings`` + column hints), which is
    how PDFs need to behave: vendor profiles apply identically to the CSV
    equivalent.
    """
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    styles = getSampleStyleSheet()
    flowables: list = []

    flowables.append(Paragraph(
        "Unrealized Gain/Loss Report", styles["Title"],
    ))
    flowables.append(Spacer(1, 6))

    if with_prose:
        flowables.append(Paragraph(
            "As of 2026-04-30. Unrealized Gain/Loss snapshot for the Henderson "
            "household across taxable and tax-deferred accounts.",
            styles["BodyText"],
        ))
        flowables.append(Spacer(1, 12))

    table = Table([HOLDINGS_HEADER] + HOLDINGS_ROWS)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
    ]))
    flowables.append(table)
    doc.build(flowables)


def _build_prose_only_pdf(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    styles = getSampleStyleSheet()
    doc.build([
        Paragraph(
            "This is a notes document with no tabular content. The advisor "
            "discussed retirement timing and Roth conversions but did not "
            "include any holdings data.",
            styles["BodyText"],
        ),
    ])


# ---------------------------------------------------------------------------
# extract_pdf_tables — direct API
# ---------------------------------------------------------------------------

class TestExtractPdfTables:
    """The public ``DocumentExtractor.extract_pdf_tables`` contract."""

    @pytest.fixture(scope="class")
    def holdings_pdf(self, tmp_path_factory) -> Path:
        path = tmp_path_factory.mktemp("p08") / "holdings.pdf"
        _build_holdings_pdf(path)
        return path

    @pytest.fixture(scope="class")
    def holdings_with_prose_pdf(self, tmp_path_factory) -> Path:
        path = tmp_path_factory.mktemp("p08") / "holdings_with_prose.pdf"
        _build_holdings_pdf(path, with_prose=True)
        return path

    @pytest.fixture(scope="class")
    def prose_only_pdf(self, tmp_path_factory) -> Path:
        path = tmp_path_factory.mktemp("p08") / "prose_only.pdf"
        _build_prose_only_pdf(path)
        return path

    @pytest.fixture(scope="class")
    def extractor(self):
        from services.document_extractor import DocumentExtractor
        return DocumentExtractor()

    def test_returns_one_sheet_for_single_table_pdf(self, extractor, holdings_pdf):
        sheets = extractor.extract_pdf_tables(holdings_pdf)
        assert len(sheets) == 1
        sheet = sheets[0]
        assert "page_1_table_" in sheet["sheet_name"]

    def test_columns_match_input_header(self, extractor, holdings_pdf):
        sheet = extractor.extract_pdf_tables(holdings_pdf)[0]
        # Order preserved; pdfplumber should hand us the header row verbatim.
        assert sheet["columns"] == HOLDINGS_HEADER

    def test_row_count_matches_input(self, extractor, holdings_pdf):
        sheet = extractor.extract_pdf_tables(holdings_pdf)[0]
        assert len(sheet["rows"]) == len(HOLDINGS_ROWS)

    def test_row_values_round_trip_as_strings(self, extractor, holdings_pdf):
        sheet = extractor.extract_pdf_tables(holdings_pdf)[0]
        # Symbols round-trip exactly; numeric strings stay as strings (the
        # HoldingsStore inference handles currency coercion downstream).
        symbols = [r["Symbol"] for r in sheet["rows"]]
        assert symbols == ["AAPL", "MSFT", "NVDA", "TSLA", "VTI", "BND"]

    def test_row_texts_include_column_keys(self, extractor, holdings_pdf):
        sheet = extractor.extract_pdf_tables(holdings_pdf)[0]
        first = sheet["row_texts"][0]
        assert "Symbol: AAPL" in first
        assert "Mkt Val: $21,900.00" in first

    def test_prose_above_table_does_not_block_extraction(
        self, extractor, holdings_with_prose_pdf,
    ):
        sheets = extractor.extract_pdf_tables(holdings_with_prose_pdf)
        assert len(sheets) == 1
        assert sheets[0]["columns"] == HOLDINGS_HEADER
        assert len(sheets[0]["rows"]) == len(HOLDINGS_ROWS)

    def test_prose_only_pdf_returns_no_sheets(self, extractor, prose_only_pdf):
        # No tables => empty list. Critical: the indexer must fall through to
        # the regular text-extraction path without error.
        assert extractor.extract_pdf_tables(prose_only_pdf) == []

    def test_extract_text_still_works_alongside(self, extractor, holdings_with_prose_pdf):
        # Prose text path is independent of the table path — both must work
        # against the same PDF (acceptance: "two outputs … not mutually
        # exclusive").
        result = extractor.extract_text(holdings_with_prose_pdf)
        page_1 = result.page_texts[1]
        assert "Henderson" in page_1


# ---------------------------------------------------------------------------
# Integration: PDF table → HoldingsStore.create_table
# ---------------------------------------------------------------------------

class TestPdfTableHoldingsStoreRoundTrip:
    """A PDF-extracted sheet must land in HoldingsStore identically to a CSV."""

    @pytest.fixture(scope="class")
    def store(self, tmp_path_factory):
        from services.document_extractor import DocumentExtractor
        from services.financial.holdings_store import HoldingsStore

        pdf_path = tmp_path_factory.mktemp("p08") / "schwab_unrealized_gl.pdf"
        _build_holdings_pdf(pdf_path)

        sheets = DocumentExtractor().extract_pdf_tables(pdf_path)
        assert sheets, "extract_pdf_tables should return at least one sheet"

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        store = HoldingsStore(db_path)
        for sheet in sheets:
            store.create_table(
                document_id="pdf_test_doc",
                filename=pdf_path.name,
                columns=sheet["columns"],
                rows=sheet["rows"],
                sheet_name=sheet["sheet_name"],
                role_overrides=sheet.get("role_overrides") or {},
                type_overrides=sheet.get("type_overrides") or {},
            )
        return store

    @pytest.fixture(scope="class")
    def schema(self, store):
        schema = store.get_schema("schwab_unrealized_gl.pdf")
        assert schema is not None
        return schema

    def _col_by_role(self, schema: dict, role: str) -> str | None:
        for c in schema["columns"]:
            if c.get("role") == role:
                return c["sql_name"]
        return None

    def _col_type(self, schema: dict, sql_name: str) -> str | None:
        for c in schema["columns"]:
            if c["sql_name"] == sql_name:
                return c["type"]
        return None

    def test_row_count_matches_pdf(self, schema):
        assert schema["row_count"] == len(HOLDINGS_ROWS)

    def test_column_count_matches_pdf(self, schema):
        assert schema["column_count"] == len(HOLDINGS_HEADER)

    def test_ticker_role_detected(self, schema):
        assert self._col_by_role(schema, "ticker") is not None

    def test_market_value_role_detected(self, schema):
        assert self._col_by_role(schema, "market_value") is not None

    def test_cost_basis_role_detected(self, schema):
        assert self._col_by_role(schema, "cost_basis") is not None

    def test_market_value_is_numeric(self, schema):
        mv_col = self._col_by_role(schema, "market_value")
        assert mv_col is not None
        col_type = self._col_type(schema, mv_col)
        assert col_type in ("real", "currency", "integer"), (
            f"market_value column from PDF '{mv_col}' has type '{col_type}', "
            "expected numeric — currency strings like '$21,900.00' must coerce."
        )

    def test_total_market_value_plausible(self, store, schema):
        from services.financial.metrics import compute_financial_metric
        result = compute_financial_metric(
            store, "schwab_unrealized_gl.pdf", "total_market_value"
        )
        val = result["value"]
        # Sum of mkt vals: 21900 + 31125 + 36400 + 7365 + 51000 + 10875 = 158,665
        assert val is not None, (
            "total_market_value returned NULL — column likely stored as TEXT"
        )
        assert 150_000 < val < 170_000, (
            f"total_market_value = {val}, expected ~$158,665 from PDF round-trip"
        )

    def test_accounting_negative_pnl_coerces(self, store, schema):
        """TSLA and BND have parenthesized losses — must round-trip negative."""
        pnl_col = self._col_by_role(schema, "pnl")
        if pnl_col is None:
            pytest.skip("no pnl role detected")
        table = schema["table_name"]
        with sqlite3.connect(store.db_path) as conn:
            row = conn.execute(
                f'SELECT MIN("{pnl_col}") FROM "{table}"'
            ).fetchone()
        min_val = row[0]
        assert min_val is not None and min_val < 0, (
            f"Min pnl from PDF = {min_val}; expected a negative loss "
            "(TSLA ($3,885.00), BND ($375.00))."
        )

    def test_by_symbol_view_exists(self, store, schema):
        view = schema["table_name"] + "__by_symbol"
        with sqlite3.connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",
                (view,),
            ).fetchone()
        assert row is not None, (
            "Lot-rollup VIEW must be auto-created when ticker role is present, "
            "even when the source is a PDF."
        )


# ---------------------------------------------------------------------------
# Indexer wiring — PDF route registers tables in HoldingsStore
# ---------------------------------------------------------------------------

class TestIndexerPdfRouting:
    """``index_document_with_progress`` must call extract_pdf_tables for .pdf
    inputs and surface the structured tables alongside the prose chunks."""

    def test_pdf_with_table_registers_holdings_store_table(self, tmp_path):
        from services.document_extractor import DocumentExtractor
        from services.financial.holdings_store import HoldingsStore

        pdf_path = tmp_path / "schwab_unrealized_gl.pdf"
        _build_holdings_pdf(pdf_path, with_prose=True)

        # Mimic the indexer's PDF branch directly: extract → register.
        # This validates the helper that the indexer wires in without
        # spinning up the full vector_store / embedding stack.
        extractor = DocumentExtractor()
        sheets = extractor.extract_pdf_tables(pdf_path)
        assert sheets, "expected the indexer's extract_pdf_tables call to succeed"

        db_path = tmp_path / "holdings.db"
        store = HoldingsStore(db_path)
        for sheet in sheets:
            store.create_table(
                document_id="indexer_route_doc",
                filename=pdf_path.name,
                columns=sheet["columns"],
                rows=sheet["rows"],
                sheet_name=sheet["sheet_name"],
                role_overrides=sheet.get("role_overrides") or {},
                type_overrides=sheet.get("type_overrides") or {},
            )

        schema = store.get_schema(pdf_path.name)
        assert schema is not None and schema["row_count"] == len(HOLDINGS_ROWS)

    def test_register_pdf_helper_is_no_op_for_empty_sheets(self, tmp_path):
        """An indexer call on a prose-only PDF must not crash the helper."""
        from services.document_extractor import DocumentExtractor
        from services.indexing.indexer import DocumentIndexer

        prose_path = tmp_path / "notes.pdf"
        _build_prose_only_pdf(prose_path)

        # Only the helper is exercised — no vector_store / embedding service
        # required, since the helper short-circuits on an empty sheet list.
        indexer = DocumentIndexer.__new__(DocumentIndexer)
        indexer.collection_id = "test"
        # The helper logs and returns 0 without touching vector_store when
        # sheets is empty, so we don't need to wire one up.
        registered = DocumentIndexer._register_pdf_tables_in_holdings_store(
            indexer, [], document_id="d", filename=prose_path.name,
        )
        assert registered == 0


# ---------------------------------------------------------------------------
# Vendor profile activation on PDF — the Schwab profile must apply identically
# whether the source is CSV or PDF (P0.8 acceptance criterion).
# ---------------------------------------------------------------------------

class TestPdfVendorProfileActivation:
    """A Schwab-shaped PDF should pick up the Schwab Unrealized profile via
    filename glob + ``required_strings`` scan + column hints."""

    def test_schwab_unrealized_profile_matches_pdf(self, tmp_path):
        from services.document_extractor import DocumentExtractor

        pdf_path = tmp_path / "schwab_unrealized_gl.pdf"
        _build_holdings_pdf(pdf_path)

        sheets = DocumentExtractor().extract_pdf_tables(pdf_path)
        assert sheets, "expected at least one extracted sheet"
        sheet = sheets[0]
        assert sheet.get("vendor_profile") == "Schwab — Unrealized Gain/Loss", (
            f"Schwab Unrealized profile should activate on PDFs with the "
            f"matching filename + body text + columns; got "
            f"{sheet.get('vendor_profile')!r}."
        )

    def test_head_lines_returns_pdf_text_for_required_strings_check(self, tmp_path):
        """``required_strings`` scanning must read PDF body text, not bytes."""
        from services.ingest_profiles import _head_lines

        pdf_path = tmp_path / "schwab_unrealized_gl.pdf"
        _build_holdings_pdf(pdf_path)

        head = _head_lines(pdf_path, n=40)
        assert "unrealized gain" in head, (
            "PDF body text must be readable so vendor profiles can match on "
            "required_strings — found body: " + head[:200]
        )
