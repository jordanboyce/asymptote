"""HTTP-level tests for the Trust Report endpoint.

The module tests in `test_ingest_report.py` prove the report is correct. These
prove the shape the frontend binds to — `IngestReportPanel.vue` reads
`summary.grade`, `summary.headline` and `reports[].findings[]` directly, so a
rename on the Python side would silently blank the card rather than fail.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


class _StubIndexer:
    """Just enough of the indexer for the route: a vector_store with a store."""

    def __init__(self, holdings_store):
        self.vector_store = type("_VS", (), {"holdings_store": holdings_store})()


@pytest.fixture
def client_with_holdings(tmp_path, monkeypatch):
    """TestClient wired to a real HoldingsStore holding the messy fixture."""
    from fastapi.testclient import TestClient
    import main

    from services.document_extractor import DocumentExtractor
    from services.financial.holdings_store import HoldingsStore
    from services.indexing.indexer import _ingest_evidence

    store = HoldingsStore(tmp_path / "metadata.db")
    sheets = DocumentExtractor().extract_tabular_sheets(FIXTURES / "messy_unrealized_gl.csv")
    for sheet in sheets:
        store.create_table(
            document_id="doc-messy",
            filename="messy_unrealized_gl.csv",
            columns=sheet["columns"],
            rows=sheet["rows"],
            sheet_name=sheet["sheet_name"],
            role_overrides=sheet.get("role_overrides") or {},
            type_overrides=sheet.get("type_overrides") or {},
            ingest_evidence=_ingest_evidence(sheet),
        )

    monkeypatch.setattr(main, "get_indexer", lambda cid: _StubIndexer(store))
    monkeypatch.setattr(main, "require_collection_access", lambda *a, **k: "owner")
    return TestClient(main.app)


def test_endpoint_returns_the_shape_the_card_binds_to(client_with_holdings):
    r = client_with_holdings.get("/api/collections/c1/ingest-report")
    assert r.status_code == 200
    body = r.json()

    assert body["collection_id"] == "c1"
    assert body["summary"]["grade"] == "unreliable"
    assert body["summary"]["sheet_count"] == 1
    assert body["summary"]["headline"]

    report = body["reports"][0]
    for key in (
        "filename", "row_count", "column_count", "columns_understood",
        "grade", "findings", "understood", "unnamed", "table_name",
    ):
        assert key in report, f"card binds to `{key}` and it is missing"

    finding = report["findings"][0]
    assert finding["severity"] == "blocker"
    for key in ("code", "headline", "detail", "column"):
        assert key in finding


def test_scoping_to_a_document_works(client_with_holdings):
    r = client_with_holdings.get(
        "/api/collections/c1/ingest-report", params={"document_id": "doc-messy"}
    )
    assert r.status_code == 200
    assert r.json()["document_id"] == "doc-messy"
    assert len(r.json()["reports"]) == 1


def test_unknown_document_reports_nothing_rather_than_erroring(client_with_holdings):
    """An upload with no tables (audio, prose PDF) must not 500 the card."""
    r = client_with_holdings.get(
        "/api/collections/c1/ingest-report", params={"document_id": "nope"}
    )
    assert r.status_code == 200
    assert r.json()["reports"] == []
    # And an empty report set is explicitly not an alarm.
    assert r.json()["summary"]["grade"] == "clean"
    assert r.json()["summary"]["sheet_count"] == 0
