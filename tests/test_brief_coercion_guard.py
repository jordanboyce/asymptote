"""The meeting brief must never report a coerced total.

`messy_unrealized_gl.csv` is the fixture the whole product argument rests on:
two restricted rows hold "See note 3" / "Priced monthly", which drops the
`Market Value` column under the numeric bar so it stays TEXT. SQLite then
truncates every "27,431.50" to 27 and `SUM()` returns $203 for a $207,727
book.

The brief ran raw `SUM()` in its own SQL, so the pre-meeting page — the one
number an advisor reads out loud — reported $203. These tests pin that shut.

`compute_portfolio_metric` was long believed to be guarded already. It was
not. What it had was `_sanity_check_sum`, a value heuristic that fires on
NULL and near-zero sums; 203.0 is neither, so the tool returned it as a clean
number with no warning on its default path. That is covered at the bottom of
this file now. Anything that validates an aggregate must check the column's
storage *type*, not the plausibility of the result.

The second test is the subtle one: the `__by_symbol` rollup view *launders*
the bug. Its column is defined as `SUM("Market_Value")`, so downstream the
value arrives already coerced and typed REAL — sampling the view finds a
clean numeric column and reports nothing. The guard has to sample the base
table or it silently passes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.brief_generator import generate_meeting_brief
from services.document_extractor import DocumentExtractor
from services.financial.holdings_store import HoldingsStore
from services.indexing.indexer import _ingest_evidence

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"

# The real total, from summing the column by hand.
TRUE_TOTAL = 207_727.45
# What an unguarded SUM() returns instead.
COERCED_TOTAL = 203.0


def _store_with(tmp_path, fixture_name: str, document_id: str = "doc-1") -> HoldingsStore:
    store = HoldingsStore(tmp_path / "metadata.db")
    sheets = DocumentExtractor().extract_tabular_sheets(FIXTURES / fixture_name)
    for sheet in sheets:
        store.create_table(
            document_id=document_id,
            filename=fixture_name,
            columns=sheet["columns"],
            rows=sheet["rows"],
            sheet_name=sheet["sheet_name"],
            role_overrides=sheet.get("role_overrides") or {},
            type_overrides=sheet.get("type_overrides") or {},
            ingest_evidence=_ingest_evidence(sheet),
        )
    return store


@pytest.fixture
def messy_store(tmp_path):
    return _store_with(tmp_path, "messy_unrealized_gl.csv")


# ── the headline behaviour ────────────────────────────────────────────────


def test_brief_withholds_the_coerced_total_rather_than_reporting_it(messy_store):
    brief = generate_meeting_brief(messy_store, collection_id="c1")
    summary = brief["household_summary"]

    assert summary["total_market_value"] is None, (
        "the brief reported a market-value total for a column that coerces — "
        "this is the $203 bug"
    )
    assert summary["total_market_value_reliable"] is False
    assert summary["total_market_value"] != COERCED_TOTAL


def test_the_warning_names_the_column_and_says_not_to_report_it(messy_store):
    brief = generate_meeting_brief(messy_store, collection_id="c1")

    assert brief["warnings"], "no coercion warning was raised for the messy fixture"
    entry = next(w for w in brief["warnings"] if w["column"] == "Market_Value")
    assert entry["filename"] == "messy_unrealized_gl.csv"
    assert "DO NOT report this number" in entry["warning"]
    assert "27,431.50" in entry["warning"]


def test_the_raw_value_is_still_visible_in_sources_for_diagnosis(messy_store):
    """Withheld from the total, but not hidden — the advisor can see what it did."""
    brief = generate_meeting_brief(messy_store, collection_id="c1")
    source = brief["household_summary"]["sources"][0]

    assert source["market_value"] == COERCED_TOTAL
    assert source["reliable"] is False


def test_derived_pnl_inherits_the_unreliability(messy_store):
    """P&L derived from a bad market value is off by the size of the portfolio."""
    brief = generate_meeting_brief(messy_store, collection_id="c1")
    summary = brief["household_summary"]

    assert summary["total_unrealized_pnl"] is None
    assert summary["total_unrealized_pnl_reliable"] is False


def test_cost_basis_is_a_real_column_and_survives(messy_store):
    """The guard is surgical: only the broken column is withheld."""
    brief = generate_meeting_brief(messy_store, collection_id="c1")
    summary = brief["household_summary"]

    assert summary["total_cost_basis"] is not None
    assert summary["total_cost_basis"] > 0
    assert summary.get("total_cost_basis_reliable") is not False


# ── the rollup view must not launder the bug ──────────────────────────────


def test_the_by_symbol_view_exists_and_would_hide_the_coercion(messy_store):
    """Documents *why* the guard samples the base table.

    If this assertion ever flips — if the view stops presenting the coerced
    column as clean REAL — the base-table sampling in `_guard` can be
    revisited. Until then it is load-bearing.
    """
    with sqlite3.connect(messy_store.db_path) as conn:
        views = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view' AND name LIKE '%__by_symbol'"
            ).fetchall()
        ]
        assert views, "expected a __by_symbol rollup view for this fixture"

        types = {
            r[0] for r in conn.execute(
                f'SELECT DISTINCT typeof("Market_Value") FROM "{views[0]}"'
            ).fetchall()
        }

    # The view's column reads as clean numeric even though the values are
    # wrong. NULLs are fine — they're rows the rollup dropped, not TEXT.
    assert "text" not in types, (
        f"view no longer coerces Market_Value (types={types}) — re-check _guard"
    )
    assert types & {"integer", "real"}, f"view has no numeric values at all (types={types})"


def test_guard_on_the_view_alone_would_have_missed_it(messy_store):
    """The negative control for the bug this module exists to prevent."""
    from services.tabular.aggregate_guard import check_aggregate_coercion

    with sqlite3.connect(messy_store.db_path) as conn:
        view = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' AND name LIKE '%__by_symbol'"
        ).fetchone()[0]
        base = view.replace("__by_symbol", "")

        via_view = check_aggregate_coercion(conn, f'SELECT SUM("Market_Value") FROM "{view}"')
        via_base = check_aggregate_coercion(conn, f'SELECT SUM("Market_Value") FROM "{base}"')

    assert via_view == [], "view unexpectedly flagged — the premise of _guard changed"
    assert via_base, "base table must flag the coercion"


# ── precision: clean files must stay silent ───────────────────────────────


@pytest.mark.parametrize("fixture", ["pershing_unrealized_gl.csv", "euro_semicolon_holdings.csv"])
def test_clean_fixtures_produce_no_warnings_and_a_real_total(tmp_path, fixture):
    """The guard is precise, not noisy. A false alarm here breaks the demo."""
    if not (FIXTURES / fixture).exists():
        pytest.skip(f"{fixture} not present")

    store = _store_with(tmp_path, fixture, document_id=f"doc-{fixture}")
    brief = generate_meeting_brief(store, collection_id="c1")

    assert brief["warnings"] == []
    summary = brief["household_summary"]
    assert summary.get("total_market_value_reliable") is not False
    assert summary["total_market_value"] is not None
    assert summary["total_market_value"] > 0


# ── the same bug, one surface over ────────────────────────────────────────
#
# The module docstring above says compute_portfolio_metric "was already
# guarded". It was not, and the guard it did have could not have caught this.
# `_sanity_check_sum` is a *value* heuristic: it fires when a SUM comes back
# NULL or near zero. The coerced total is 203.0 -- neither -- so it sailed
# through as a clean number with no warning attached, on the default code
# path, for the exact question the demo asks. Verified against a live
# container before the fix.


@pytest.fixture
def clean_store(tmp_path):
    return _store_with(tmp_path, "pershing_unrealized_gl.csv")


def test_metric_tool_withholds_the_coerced_total(messy_store):
    from services.financial.metrics import compute_financial_metric

    result = compute_financial_metric(
        messy_store, "messy_unrealized_gl.csv", "total_market_value"
    )

    assert result["value"] is None, (
        "compute_portfolio_metric returned a market-value total for a column "
        "that coerces -- this is the $203 bug on the tool surface"
    )
    assert result["value"] != COERCED_TOTAL
    assert result["reliable"] is False
    assert result["value_withheld"] is True
    assert any("DO NOT report this number" in w for w in result["warnings"])


def test_metric_tool_guard_survives_the_rollup_view(messy_store):
    """The negative control: group_by_symbol=True reads the laundering view.

    The `__by_symbol` view declares its column as SUM("Market_Value"), so the
    truncated value arrives typed REAL and a guard that samples the queried
    table finds nothing wrong. The guard has to sample the base table.
    """
    from services.financial.metrics import compute_financial_metric

    result = compute_financial_metric(
        messy_store, "messy_unrealized_gl.csv", "total_market_value",
        group_by_symbol=True,
    )

    assert result["table_used"].endswith("__by_symbol"), "expected the view path"
    assert result["value"] is None
    assert result["reliable"] is False


def test_metric_tool_withholds_derived_concentration(messy_store):
    """Numerator and denominator share the broken column, so the ratio goes too."""
    from services.financial.metrics import compute_financial_metric

    result = compute_financial_metric(
        messy_store, "messy_unrealized_gl.csv", "concentration"
    )

    assert result["concentration_pct"] is None
    assert result["total_value"] is None
    assert result["top_n_value"] is None
    assert result["reliable"] is False


def test_metric_tool_stays_quiet_on_a_clean_export(clean_store):
    """Precision, not noise -- a clean vendor file must report its total.

    This is the property a guard change must not break: the clean Pershing
    fixture has to come back with a real number and no warning at all.
    """
    from services.financial.metrics import compute_financial_metric

    result = compute_financial_metric(
        clean_store, "pershing_unrealized_gl.csv", "total_market_value"
    )

    assert result["value"] is not None
    assert result["value"] > 0
    assert result.get("reliable") is not False
    assert result.get("value_withheld") is not True
    assert not result.get("warnings")


# ── the aggregate lock ────────────────────────────────────────────────────
#
# Guarding the tool is only half the job: a small table gets inlined into the
# prompt as JSONL, and the prompt used to tell the model to sum it by hand and
# "show your arithmetic". It did, and it was wrong -- three identical live
# turns on this 14-row fixture returned $186,325.45, $184,211.45 and a correct
# refusal, against a true $207,727.45. No tool was called, so no guard ran.
# Since the inline threshold is 150 rows, that was the path essentially every
# real client export took.


def _money_table():
    return {
        "filename": "holdings.csv",
        "row_count": 12,
        "column_count": 4,
        "columns": [
            {"name": "Symbol", "role": "ticker"},
            {"name": "Market Value", "role": "market_value"},
        ],
    }


def _plain_table():
    return {
        "filename": "attendees.csv",
        "row_count": 12,
        "column_count": 2,
        "columns": [
            {"name": "Name", "role": None},
            {"name": "Email", "role": None},
        ],
    }


def test_money_bearing_table_is_detected_by_role():
    from services.structured_chat import table_is_money_bearing

    assert table_is_money_bearing(_money_table())
    assert not table_is_money_bearing(_plain_table())
    assert not table_is_money_bearing({"columns": []})
    assert not table_is_money_bearing({})


def test_cost_basis_and_pnl_also_lock():
    from services.structured_chat import table_is_money_bearing

    for role in ("cost_basis", "pnl"):
        assert table_is_money_bearing({"columns": [{"name": "c", "role": role}]}), role


def test_prompt_no_longer_tells_the_model_to_hand_sum_money():
    """The old instruction and the fix cannot both be present."""
    from services.chat.context import _assemble_system_prompt

    prompt = _assemble_system_prompt(
        collection_overview="",
        expertise_block="",
        inline_block="STRUCTURED TABLES (AUTHORITATIVE FULL DATA):\n...",
        context_text="",
        tables_block="",
        doc_filter_count=None,
        native_citations=False,
        context_addendum="",
    )

    assert "AGGREGATE LOCK" in prompt
    # The instruction that produced the wrong totals.
    assert "answer DIRECTLY from the JSONL rows and show your arithmetic" not in prompt
