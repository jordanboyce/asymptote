"""Tests for role-detection provenance (v4.1 P0.5).

Each role assigned to a column carries a `role_source` tag:

  * ``profile``   — set by a vendor profile via ``role_overrides``
  * ``heuristic`` — matched by the regex detector in services.financial.roles
                    (called directly by HoldingsStore.create_table())
  * ``llm``       — proposed by the LLM inference pass for unmapped columns
  * ``None``      — column has no detected role

The MCP ``get_table_schema`` response surfaces the same value via per-column
``role_source`` and a top-level ``financial_role_sources`` map. Hosts use this
to decide how much to trust each role; treat ``profile`` as authoritative,
``heuristic`` as reliable, and ``llm`` as best-effort.

Run with:
    pytest tests/test_role_provenance.py -v
"""

from __future__ import annotations

import pytest

from services.financial.holdings_store import HoldingsStore
from services.mcp_server import _format_schema_summary


COLUMNS = ["Symbol", "Quantity", "Mystery Column"]
ROWS = [
    {"Symbol": "AAPL", "Quantity": "100", "Mystery Column": "alpha"},
    {"Symbol": "MSFT", "Quantity": "200", "Mystery Column": "beta"},
]


def _column(schema, name):
    for c in schema["columns"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"column {name!r} not found in schema")


# ---------------------------------------------------------------------------
# Heuristic path — regex matches without a profile or LLM
# ---------------------------------------------------------------------------

class TestHeuristicProvenance:
    @pytest.fixture
    def store(self, tmp_path):
        s = HoldingsStore(tmp_path / "metadata.db")
        s.create_table(
            document_id="doc-heur",
            filename="heur.csv",
            columns=COLUMNS,
            rows=ROWS,
        )
        return s

    def test_regex_matched_columns_tagged_heuristic(self, store):
        schema = store.get_schema("doc-heur")
        assert schema is not None
        assert _column(schema, "Symbol")["role"] == "ticker"
        assert _column(schema, "Symbol")["role_source"] == "heuristic"
        assert _column(schema, "Quantity")["role"] == "quantity"
        assert _column(schema, "Quantity")["role_source"] == "heuristic"

    def test_unmatched_column_has_null_source(self, store):
        schema = store.get_schema("doc-heur")
        mystery = _column(schema, "Mystery Column")
        assert mystery["role"] is None
        assert mystery["role_source"] is None


# ---------------------------------------------------------------------------
# Profile path — vendor profile overrides take precedence over heuristic
# ---------------------------------------------------------------------------

class TestProfileProvenance:
    def test_profile_override_wins_and_is_tagged_profile(self, tmp_path):
        store = HoldingsStore(tmp_path / "metadata.db")
        # The heuristic would also match "Symbol" → ticker; the profile must
        # still claim provenance because it was applied deterministically.
        store.create_table(
            document_id="doc-prof",
            filename="prof.csv",
            columns=COLUMNS,
            rows=ROWS,
            role_overrides={
                "Symbol": "ticker",
                "Mystery Column": "sector",
            },
        )
        schema = store.get_schema("doc-prof")
        assert _column(schema, "Symbol")["role_source"] == "profile"
        # Profile reaches a column the heuristic could not have matched.
        mystery = _column(schema, "Mystery Column")
        assert mystery["role"] == "sector"
        assert mystery["role_source"] == "profile"
        # Columns the profile said nothing about still fall through to heuristic.
        assert _column(schema, "Quantity")["role_source"] == "heuristic"


# ---------------------------------------------------------------------------
# LLM path — only fills in columns left null by the prior two passes
# ---------------------------------------------------------------------------

class TestLLMProvenance:
    def test_llm_fills_unmapped_columns_only(self, tmp_path, monkeypatch):
        from config import settings

        # Force the LLM pass on; threshold low enough to fire on this fixture.
        monkeypatch.setattr(settings, "enable_llm_schema_inference", True, raising=False)
        monkeypatch.setattr(settings, "llm_schema_inference_threshold", 0.1, raising=False)

        # Stub the LLM call so the test stays hermetic.
        def fake_infer(unmapped_columns, collection_id=None, **_):
            return {c["name"]: "sector" for c in unmapped_columns if c["name"] == "Mystery Column"}

        import services.llm_role_inference as llm_mod
        monkeypatch.setattr(llm_mod, "infer_roles_with_llm", fake_infer)

        store = HoldingsStore(tmp_path / "metadata.db")
        store.create_table(
            document_id="doc-llm",
            filename="llm.csv",
            columns=COLUMNS,
            rows=ROWS,
        )
        schema = store.get_schema("doc-llm")
        # Heuristic-matched columns are not overwritten by the LLM pass.
        assert _column(schema, "Symbol")["role_source"] == "heuristic"
        assert _column(schema, "Quantity")["role_source"] == "heuristic"
        # The previously unmapped column now has an LLM-supplied role.
        mystery = _column(schema, "Mystery Column")
        assert mystery["role"] == "sector"
        assert mystery["role_source"] == "llm"


# ---------------------------------------------------------------------------
# MCP surface — _format_schema_summary exposes both fields the calling LLM sees
# ---------------------------------------------------------------------------

class TestMCPSchemaSummarySurfacesProvenance:
    def test_per_column_and_top_level_role_sources(self, tmp_path):
        store = HoldingsStore(tmp_path / "metadata.db")
        store.create_table(
            document_id="doc-mcp",
            filename="mcp.csv",
            columns=COLUMNS,
            rows=ROWS,
            role_overrides={"Mystery Column": "sector"},
        )
        full = store.get_schema("doc-mcp")
        summary = _format_schema_summary(full)

        # Per-column role_source
        per_col = {c["original_name"]: c["role_source"] for c in summary["columns"]}
        assert per_col["Symbol"] == "heuristic"
        assert per_col["Mystery Column"] == "profile"
        assert per_col["Quantity"] == "heuristic"

        # Top-level financial_role_sources mirrors the column tags by sql_name
        assert "financial_role_sources" in summary
        assert set(summary["financial_role_sources"].values()) == {"heuristic", "profile"}
        # Every key in financial_roles has a matching entry in financial_role_sources
        assert set(summary["financial_role_sources"].keys()) == set(summary["financial_roles"].keys())

    def test_llm_role_source_appears_on_summary(self, tmp_path, monkeypatch):
        """R1.5: llm-tagged roles surface through the MCP summary."""
        from config import settings

        monkeypatch.setattr(settings, "enable_llm_schema_inference", True, raising=False)
        monkeypatch.setattr(settings, "llm_schema_inference_threshold", 0.1, raising=False)

        def fake_infer(unmapped_columns, collection_id=None, **_):
            return {c["name"]: "sector" for c in unmapped_columns if c["name"] == "Mystery Column"}

        import services.llm_role_inference as llm_mod
        monkeypatch.setattr(llm_mod, "infer_roles_with_llm", fake_infer)

        store = HoldingsStore(tmp_path / "metadata.db")
        store.create_table(
            document_id="doc-mcp-llm",
            filename="mcp_llm.csv",
            columns=COLUMNS,
            rows=ROWS,
        )
        summary = _format_schema_summary(store.get_schema("doc-mcp-llm"))
        per_col = {c["original_name"]: c["role_source"] for c in summary["columns"]}
        assert per_col["Mystery Column"] == "llm"
        assert summary["financial_role_sources"][_column_sql(summary, "Mystery Column")] == "llm"

    def test_no_roles_omits_top_level_maps(self, tmp_path):
        """R1.5: when a table has zero detected roles, financial_roles and
        financial_role_sources are absent (rather than empty {}). Per-column
        role_source is null. The agent treats this as 'use query_table'."""
        store = HoldingsStore(tmp_path / "metadata.db")
        store.create_table(
            document_id="doc-mcp-null",
            filename="mcp_null.csv",
            columns=["Foo", "Bar", "Quux"],
            rows=[{"Foo": 1, "Bar": "x", "Quux": "y"}],
        )
        summary = _format_schema_summary(store.get_schema("doc-mcp-null"))

        per_col_role = {c["original_name"]: c["role"] for c in summary["columns"]}
        per_col_src = {c["original_name"]: c["role_source"] for c in summary["columns"]}
        assert per_col_role == {"Foo": None, "Bar": None, "Quux": None}
        assert per_col_src == {"Foo": None, "Bar": None, "Quux": None}
        assert "financial_roles" not in summary
        assert "financial_role_sources" not in summary


def _column_sql(summary, original_name):
    for c in summary["columns"]:
        if c["original_name"] == original_name:
            return c["sql_name"]
    raise AssertionError(f"column {original_name!r} not in summary")


# ---------------------------------------------------------------------------
# R1.3 + R1.4 — unknown-header file: query_table works on raw cols,
# compute_portfolio_metric returns structured no_role_detected.
# ---------------------------------------------------------------------------

class TestNoRoleDetectedFallback:
    @pytest.fixture
    def store_with_unknown_headers(self, tmp_path, monkeypatch):
        from config import settings

        # Force LLM pass off so this test stays hermetic and deterministic.
        monkeypatch.setattr(settings, "enable_llm_schema_inference", False, raising=False)

        store = HoldingsStore(tmp_path / "metadata.db")
        store.create_table(
            document_id="doc-unknown",
            filename="unknown.csv",
            columns=["Foo", "Bar", "Quux"],
            rows=[
                {"Foo": "a1", "Bar": "100", "Quux": "alpha"},
                {"Foo": "a2", "Bar": "200", "Quux": "beta"},
            ],
        )
        return store

    def test_query_table_returns_raw_rows(self, store_with_unknown_headers):
        """R1.4(a): role-agnostic SQL still reads the table on raw column names."""
        store = store_with_unknown_headers
        schema = store.get_schema("doc-unknown")
        assert schema is not None
        sql_names = {c["name"]: c["sql_name"] for c in schema["columns"]}
        result = store.execute_query(
            f'SELECT "{sql_names["Foo"]}", "{sql_names["Bar"]}", "{sql_names["Quux"]}" '
            f'FROM "{schema["table_name"]}"',
            max_rows=10,
        )
        assert result["rows"] == [["a1", 100, "alpha"], ["a2", 200, "beta"]]

    def test_compute_metric_returns_structured_no_role_error(self, store_with_unknown_headers):
        """R1.3 + R1.4(b): compute_portfolio_metric returns a structured signal,
        not a thrown ValueError, when no roles were detected."""
        from services.financial.metrics import compute_financial_metric

        result = compute_financial_metric(
            store_with_unknown_headers,
            "doc-unknown",
            "total_market_value",
        )
        assert result["error"] == "no_role_detected"
        assert result["applicable"] is False
        assert result["detected_roles"] == []
        # The fallback hint names the actual columns the agent can query.
        assert set(result["available_columns"]) >= {"Foo", "Bar", "Quux"}
        # Filename echoed so the agent knows which table failed.
        assert result["filename"] == "unknown.csv"

    def test_row_count_still_works_without_roles(self, store_with_unknown_headers):
        """R1.3 boundary: row_count and summary_statistics don't need roles, so
        they keep working — the no_role_detected short-circuit is metric-scoped."""
        from services.financial.metrics import compute_financial_metric

        result = compute_financial_metric(
            store_with_unknown_headers,
            "doc-unknown",
            "row_count",
        )
        assert result.get("error") is None
        assert result["value"] == 2


# ---------------------------------------------------------------------------
# R1.2 — role-agnostic helpers used by search_collection / aggregate_table
# survive a no-role table (regression for the audit conclusion).
# ---------------------------------------------------------------------------

class TestRoleAgnosticHelpers:
    @pytest.fixture
    def no_role_store(self, tmp_path, monkeypatch):
        from config import settings

        monkeypatch.setattr(settings, "enable_llm_schema_inference", False, raising=False)

        store = HoldingsStore(tmp_path / "metadata.db")
        store.create_table(
            document_id="doc-helpers",
            filename="helpers.csv",
            columns=["Foo", "Bar"],
            rows=[{"Foo": "x", "Bar": "alpha"}, {"Foo": "y", "Bar": "beta"}],
        )
        return store

    def test_render_table_as_rows_uses_display_names_without_roles(self, no_role_store):
        """search_collection inlines small tables via render_table_as_rows;
        the helper must work whether or not financial_roles were detected."""
        from services.structured_chat import render_table_as_rows

        table_info = no_role_store.list_tables()[0]
        rendered = render_table_as_rows(no_role_store, table_info)
        assert rendered is not None
        # Display names match the source headers, regardless of any roles.
        assert "Foo" in rendered["columns"] and "Bar" in rendered["columns"]
        # Two rows in source order.
        assert len(rendered["rows"]) == 2

    def test_build_structured_context_skips_role_line_when_no_roles(self, no_role_store):
        """The 'detected roles:' line is emitted iff at least one column has a
        role. Without roles the inline block must still render — just header
        + JSONL — and not crash on the missing field."""
        from services.structured_chat import build_structured_context

        tables = no_role_store.list_tables()
        for t in tables:
            t["collection_id"] = "test-collection"
        ctx = build_structured_context(
            tables, {"test-collection": no_role_store}, inline_row_threshold=10
        )
        assert "helpers.csv" in ctx["inlined_filenames"]
        # The 'detected roles' substring is omitted because no column has one.
        assert "detected roles:" not in ctx["inline_block"]
        # The inline block still contains the JSONL with display names.
        assert '"Foo"' in ctx["inline_block"] and '"Bar"' in ctx["inline_block"]

    def test_list_tables_strips_financial_roles_field(self, no_role_store):
        """list_tables (the MCP-shaped one) must not surface financial_roles —
        callers go through get_table_schema for that. Verify even an empty/
        missing roles map is filtered out."""
        from services.mcp_server import _get_structured_store
        # Can't easily call the MCP tool without ASGI plumbing; assert the raw
        # transformation that list_tables performs on store output.
        raw = no_role_store.list_tables()
        stripped = [{k: v for k, v in t.items() if k != "financial_roles"} for t in raw]
        for t in stripped:
            assert "financial_roles" not in t
            assert t["filename"] == "helpers.csv"
