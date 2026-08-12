"""Regression tests for the Trust Report.

The report is the user-facing half of Finn's "never silently wrong" promise:
the aggregate guard already knew that summing a TEXT market-value column
returns $203 instead of $207,727, but only the chat model ever heard about it.
This turns that knowledge into something the advisor reads before they quote a
number to a client.

Two properties matter, and they pull against each other:

1. **It catches the thing.** A column that would produce a wrong total is
   flagged as a blocker, in words a non-technical reader can act on.
2. **It doesn't cry wolf.** Real brokerage exports are full of blank cells,
   unmapped columns and skipped preamble — all of which are the format working
   correctly. A report that grades a clean Pershing file as "needs review" is
   one an advisor learns to dismiss, which is the same as not having one.

The fixture-level tests at the bottom pin (2) against every shipped export
format, so a future tweak to a threshold can't quietly make the report noisy.
"""

from pathlib import Path

import pytest

from services.document_extractor import DocumentExtractor
from services.financial.holdings_store import HoldingsStore
from services.indexing.indexer import _ingest_evidence
from services.tabular.ingest_report import (
    GRADE_CLEAN,
    GRADE_NEEDS_REVIEW,
    GRADE_UNRELIABLE,
    build_ingest_report,
    friendly_role,
    summarize_reports,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _ingest(tmp_path, fixture_name):
    """Run a real fixture through the real extractor into a real store."""
    store = HoldingsStore(tmp_path / "metadata.db")
    sheets = DocumentExtractor().extract_tabular_sheets(FIXTURES / fixture_name)
    for i, sheet in enumerate(sheets):
        store.create_table(
            document_id=f"doc{i}",
            filename=fixture_name,
            columns=sheet["columns"],
            rows=sheet["rows"],
            sheet_name=sheet["sheet_name"],
            role_overrides=sheet.get("role_overrides") or {},
            type_overrides=sheet.get("type_overrides") or {},
            ingest_evidence=_ingest_evidence(sheet),
        )
    return store


def _codes(report):
    return {f["code"] for f in report["findings"]}


def _finding(report, code):
    return next(f for f in report["findings"] if f["code"] == code)


def _build(tmp_path, columns, rows, evidence=None, **kwargs):
    """Create a table from literal rows and report on it."""
    import sqlite3

    store = HoldingsStore(tmp_path / "metadata.db")
    schema = store.create_table(
        document_id="d1",
        filename="test.csv",
        columns=columns,
        rows=rows,
        ingest_evidence=evidence,
        **kwargs,
    )
    with sqlite3.connect(store.db_path) as conn:
        return build_ingest_report(conn, schema, evidence or {})


# --------------------------------------------------------------------------
# The headline case — the $207k bug, made visible
# --------------------------------------------------------------------------

def test_messy_export_is_graded_unreliable(tmp_path):
    """The fixture that reproduces the silent-truncation bug must not pass."""
    store = _ingest(tmp_path, "messy_unrealized_gl.csv")
    report = store.get_ingest_reports("doc0")[0]

    assert report["grade"] == GRADE_UNRELIABLE
    assert report["blocker_count"] == 1
    assert "untrusted_totals" in _codes(report)


def test_untrusted_totals_finding_names_the_column_and_the_damage(tmp_path):
    """The advisor has to learn *which* column and *what* goes wrong with it."""
    store = _ingest(tmp_path, "messy_unrealized_gl.csv")
    report = store.get_ingest_reports("doc0")[0]
    finding = _finding(report, "untrusted_totals")

    assert finding["severity"] == "blocker"
    assert finding["column"] == "Market Value"
    assert "Market Value" in finding["headline"]
    # The detail has to contain the concrete arithmetic, not a vague caution:
    # what the value is, and what totalling it would read instead.
    assert "27,431.50" in finding["detail"]
    assert "27" in finding["detail"]
    # And it must quote the actual offending cells so the advisor can find them.
    assert finding["evidence"]["text_rows"] >= 1
    assert finding["evidence"]["example_values"]


def test_blocker_headline_is_plain_language(tmp_path):
    """No jargon in the one sentence most readers will stop at."""
    store = _ingest(tmp_path, "messy_unrealized_gl.csv")
    report = store.get_ingest_reports("doc0")[0]

    headline = report["headline"].lower()
    assert "wrong totals" in headline
    for jargon in ("coercion", "sqlite", "typeof", "cast", "text-typed"):
        assert jargon not in headline


# --------------------------------------------------------------------------
# Precision — the half that keeps the report worth reading
# --------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", [
    "pershing_unrealized_gl.csv",
    "schwab_unrealized_gl.csv",
    "fidelity_positions.csv",
    "vanguard_holdings.csv",
    "netx360_holdings_by_investor.csv",
])
def test_clean_vendor_exports_raise_no_alarms(tmp_path, fixture):
    """Every shipped vendor fixture must grade clean.

    These are the files a prospect brings to a demo. A false blocker on one of
    them is worse than a missed real one — it teaches the advisor the badge
    means nothing.
    """
    store = _ingest(tmp_path, fixture)
    for report in store.get_ingest_reports():
        assert report["blocker_count"] == 0, (
            f"{fixture} raised a false blocker: "
            f"{[f['headline'] for f in report['findings'] if f['severity'] == 'blocker']}"
        )
        assert report["grade"] == GRADE_CLEAN, (
            f"{fixture} graded {report['grade']}: "
            f"{[f['headline'] for f in report['findings'] if f['severity'] == 'warning']}"
        )


def test_half_empty_column_is_not_flagged(tmp_path):
    """A flat Pershing file blanks `Symbol` on lot rows — that's the format.

    This was a real false positive: at a 50% threshold the shipped Pershing
    fixture graded `needs_review` on three columns that were behaving exactly
    as the export intended.
    """
    report = _build(
        tmp_path,
        ["Symbol", "Market Value"],
        [{"Symbol": "AAPL" if i % 2 else None, "Market Value": 100.0} for i in range(20)],
    )
    assert "mostly_empty" not in _codes(report)


def test_entirely_empty_column_is_flagged(tmp_path):
    """A column that came through with nothing in it is worth saying."""
    report = _build(
        tmp_path,
        ["Symbol", "Cost Basis"],
        [{"Symbol": f"SYM{i}", "Cost Basis": None} for i in range(20)],
    )
    assert "mostly_empty" in _codes(report)


def test_correctly_typed_money_column_is_silent(tmp_path):
    """Real numbers stored as numbers must produce no finding at all."""
    report = _build(
        tmp_path,
        ["Symbol", "Market Value"],
        [{"Symbol": f"SYM{i}", "Market Value": 1234.56} for i in range(10)],
    )
    assert report["grade"] == GRADE_CLEAN
    assert "untrusted_totals" not in _codes(report)


def test_ticker_column_is_not_mistaken_for_broken_numbers(tmp_path):
    """Text that was never meant to be a number isn't a finding."""
    report = _build(
        tmp_path,
        ["Symbol", "Name"],
        [{"Symbol": "AAPL", "Name": "Apple Inc"} for _ in range(10)],
    )
    assert "untrusted_totals" not in _codes(report)


def test_unnamed_numeric_columns_collapse_to_one_finding(tmp_path):
    """Four unnamed columns must not become four cards competing for attention."""
    rows = [
        {"Symbol": f"S{i}", "Widget A": 1.0, "Widget B": 2.0, "Widget C": 3.0}
        for i in range(10)
    ]
    report = _build(tmp_path, ["Symbol", "Widget A", "Widget B", "Widget C"], rows)

    unmapped = [f for f in report["findings"] if f["code"] == "unmapped_numeric"]
    assert len(unmapped) == 1
    assert unmapped[0]["severity"] == "info"
    # All three still get named in the one finding.
    for name in ("Widget A", "Widget B", "Widget C"):
        assert name in unmapped[0]["detail"]


# --------------------------------------------------------------------------
# Showing the work — the evidence that the parse was real
# --------------------------------------------------------------------------

def test_skipped_preamble_is_reported(tmp_path):
    """"We skipped 9 lines of report header" is the demo-winning line."""
    store = _ingest(tmp_path, "pershing_unrealized_gl.csv")
    report = store.get_ingest_reports("doc0")[0]

    finding = _finding(report, "header_offset")
    assert report["header_row"] > 0
    # Reported as a 1-based line number, because that's what the advisor sees
    # when they open the file in Excel.
    assert f"line {report['header_row'] + 1}" in finding["detail"]


def test_vendor_profile_match_is_reported(tmp_path):
    store = _ingest(tmp_path, "schwab_unrealized_gl.csv")
    report = store.get_ingest_reports("doc0")[0]

    assert report["recognized_as"]
    assert "Schwab" in _finding(report, "vendor_matched")["headline"]


def test_hierarchical_export_reports_the_flattening(tmp_path):
    """NetX360's nested per-account layout is real work worth surfacing."""
    store = _ingest(tmp_path, "netx360_holdings_by_investor.csv")
    report = store.get_ingest_reports("doc0")[0]

    assert "preprocessed" in _codes(report)


def test_ai_guessed_roles_are_called_out(tmp_path):
    """An LLM-inferred role is the one column an advisor should double-check."""
    import sqlite3

    store = HoldingsStore(tmp_path / "metadata.db")
    schema = store.create_table(
        document_id="d1",
        filename="t.csv",
        columns=["Zztop"],
        rows=[{"Zztop": 5.0} for _ in range(5)],
    )
    # Simulate what llm_role_inference would have stamped on the column.
    schema["columns"][0]["role"] = "market_value"
    schema["columns"][0]["role_source"] = "llm"
    with sqlite3.connect(store.db_path) as conn:
        report = build_ingest_report(conn, schema, {})

    finding = _finding(report, "ai_guessed_role")
    assert finding["severity"] == "warning"
    assert "guess" in finding["headline"].lower()
    assert report["grade"] == GRADE_NEEDS_REVIEW


def test_column_map_covers_every_column(tmp_path):
    """The verify-don't-trust table has to account for all of them."""
    store = _ingest(tmp_path, "pershing_unrealized_gl.csv")
    report = store.get_ingest_reports("doc0")[0]

    assert len(report["understood"]) + len(report["unnamed"]) == report["column_count"]
    assert report["columns_understood"] == len(report["understood"])


def test_findings_are_ordered_worst_first(tmp_path):
    store = _ingest(tmp_path, "messy_unrealized_gl.csv")
    report = store.get_ingest_reports("doc0")[0]

    order = {"blocker": 0, "warning": 1, "info": 2}
    severities = [order[f["severity"]] for f in report["findings"]]
    assert severities == sorted(severities)


# --------------------------------------------------------------------------
# Persistence + backwards compatibility
# --------------------------------------------------------------------------

def test_evidence_survives_a_round_trip(tmp_path):
    """Ingest-time facts are unrecoverable from the table, so they must persist."""
    store = _ingest(tmp_path, "schwab_unrealized_gl.csv")
    # A fresh store object re-reads from disk rather than any in-memory state.
    reopened = HoldingsStore(tmp_path / "metadata.db")
    report = reopened.get_ingest_reports("doc0")[0]

    assert report["recognized_as"]
    assert report["header_row"] > 0


def test_table_ingested_before_evidence_existed_still_reports(tmp_path):
    """Tables created without `ingest_evidence` degrade to column findings only.

    Existing Collections predate the `ingest_json` column; they must not error,
    they just can't show the file-level lines nothing recorded.
    """
    # Mixed column: enough non-numeric rows to push it under P0.2's 80%
    # coercion bar, so it lands as TEXT and totals silently truncate. A column
    # of uniformly-formatted currency strings coerces cleanly to REAL and is
    # correctly *not* a hazard.
    rows = [{"Symbol": f"S{i}", "Market Value": "1,234.56"} for i in range(7)]
    rows += [{"Symbol": f"S{i}", "Market Value": "See contract"} for i in range(7, 10)]
    report = _build(tmp_path, ["Symbol", "Market Value"], rows, evidence=None)

    assert report["header_row"] == 0
    assert report["recognized_as"] is None
    # The column-level detection still works without any recorded evidence.
    assert "untrusted_totals" in _codes(report)


def test_re_ingesting_replaces_rather_than_duplicates_evidence(tmp_path):
    store = _ingest(tmp_path, "schwab_unrealized_gl.csv")
    store = _ingest(tmp_path, "schwab_unrealized_gl.csv")
    assert len(store.get_ingest_reports("doc0")) == 1


# --------------------------------------------------------------------------
# Rollup
# --------------------------------------------------------------------------

def test_summary_takes_the_worst_grade():
    reports = [
        {"grade": GRADE_CLEAN, "blocker_count": 0, "warning_count": 0, "row_count": 10},
        {"grade": GRADE_UNRELIABLE, "blocker_count": 1, "warning_count": 0, "row_count": 5},
    ]
    summary = summarize_reports(reports)
    assert summary["grade"] == GRADE_UNRELIABLE
    assert summary["sheet_count"] == 2
    assert summary["row_count"] == 15


def test_summary_of_nothing_is_not_an_alarm():
    summary = summarize_reports([])
    assert summary["grade"] == GRADE_CLEAN
    assert summary["sheet_count"] == 0


def test_warnings_alone_grade_needs_review():
    summary = summarize_reports([
        {"grade": GRADE_NEEDS_REVIEW, "blocker_count": 0, "warning_count": 2, "row_count": 3},
    ])
    assert summary["grade"] == GRADE_NEEDS_REVIEW


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

@pytest.mark.parametrize("role,expected", [
    ("market_value", "Market value"),
    ("pnl", "Gain / loss"),
    ("cost_basis", "Cost basis"),
    ("ticker", "Ticker symbol"),
])
def test_roles_render_in_advisor_english(role, expected):
    assert friendly_role(role) == expected


def test_unknown_role_still_renders_readably():
    """A role added later must not leak a snake_case identifier into the UI."""
    assert friendly_role("some_new_role") == "Some new role"


def test_no_role_has_no_label():
    assert friendly_role(None) is None
