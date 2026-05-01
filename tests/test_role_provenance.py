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
