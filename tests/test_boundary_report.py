"""Tests for the Boundary Report — the redaction equivalent of the Trust Report.

Two invariants carry the weight here:

1. **Originals are opt-in.** The report is the only surface that can return
   real PII, and it must not do so unless the caller passed ``reveal``. The
   HTTP test asserts against the *serialized* response rather than a field, so
   a future nested structure can't reintroduce the leak somewhere the field
   check wouldn't look.
2. **Distinct people stay distinct.** Under ``entity_type`` style every name
   collapses to ``[PERSON]``. If grouping keyed on the replacement alone, three
   clients would merge into one row reading "mentioned 3 times" — a report that
   understates how many people were involved is worse than no report.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def log(tmp_path, monkeypatch):
    """A fresh audit log, wired in as the module singleton the report reads."""
    from services.privacy.redaction_log import RedactionLog
    import services.privacy.redaction_log as rl_mod

    fresh = RedactionLog(db_path=tmp_path / "redaction_log.db")
    monkeypatch.setattr(rl_mod, "redaction_log", fresh)
    return fresh


def _detail(entity_type, original, replacement, score=0.95):
    from services.privacy.redaction_engine import RedactionDetail

    return RedactionDetail(entity_type, 0, len(original), score, original, replacement)


def _seed(log, details, *, tool_name, collection_id="col-A", session_id="sess-1"):
    log.log_redactions(
        details,
        session_id=session_id,
        collection_id=collection_id,
        tool_name=tool_name,
    )


# ── Boundary classification ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool_name,expected",
    [
        ("chat_context", "retrieved_text"),
        ("notes_output", "generated_text"),
        ("followup_output", "generated_text"),
        ("search_collection", "tool_result"),
        ("get_meeting_notes", "tool_result"),
        (None, "other"),
        ("", "other"),
    ],
)
def test_classify_boundary(tool_name, expected):
    from services.privacy.boundary_report import classify_boundary

    assert classify_boundary(tool_name) == expected


def test_report_groups_events_by_boundary(log):
    from services.privacy.boundary_report import build_boundary_report

    _seed(log, [_detail("PERSON", "Robert Henderson", "Marcus Whitfield")],
          tool_name="chat_context")
    _seed(log, [_detail("FINANCIAL_ACCOUNT", "X12345678", "[ACCOUNT_NUMBER]")],
          tool_name="query_table")
    _seed(log, [_detail("EMAIL_ADDRESS", "rh@example.com", "[EMAIL_ADDRESS]")],
          tool_name="notes_output")

    report = build_boundary_report(collection_id="col-A")

    keys = [b["key"] for b in report["boundaries"]]
    assert keys == ["retrieved_text", "tool_result", "generated_text"]
    assert report["total_redactions"] == 3
    assert report["distinct_values"] == 3

    tool_boundary = next(b for b in report["boundaries"] if b["key"] == "tool_result")
    assert tool_boundary["tools"] == ["query_table"]


def test_boundaries_with_no_events_are_omitted(log):
    from services.privacy.boundary_report import build_boundary_report

    _seed(log, [_detail("PERSON", "Robert Henderson", "Marcus Whitfield")],
          tool_name="chat_context")

    report = build_boundary_report(collection_id="col-A")
    assert [b["key"] for b in report["boundaries"]] == ["retrieved_text"]


# ── Collapsing repeated mentions ─────────────────────────────────────────────


def test_repeated_mentions_collapse_into_one_substitution(log):
    from services.privacy.boundary_report import build_boundary_report

    _seed(
        log,
        [_detail("PERSON", "Robert Henderson", "Marcus Whitfield") for _ in range(4)],
        tool_name="chat_context",
    )

    report = build_boundary_report(collection_id="col-A")
    boundary = report["boundaries"][0]

    assert boundary["count"] == 4
    assert boundary["distinct_count"] == 1
    assert len(boundary["substitutions"]) == 1
    assert boundary["substitutions"][0]["occurrences"] == 4
    assert report["total_redactions"] == 4
    assert report["distinct_values"] == 1


def test_distinct_originals_stay_distinct_under_entity_type_style(log):
    """Three clients, one replacement token. They must not merge into one row."""
    from services.privacy.boundary_report import build_boundary_report

    _seed(
        log,
        [
            _detail("PERSON", "Robert Henderson", "[PERSON]"),
            _detail("PERSON", "Alice Chen", "[PERSON]"),
            _detail("PERSON", "Dmitri Volkov", "[PERSON]"),
        ],
        tool_name="chat_context",
    )

    report = build_boundary_report(collection_id="col-A")
    assert report["distinct_values"] == 3
    assert len(report["boundaries"][0]["substitutions"]) == 3


def test_substitutions_sort_by_occurrence_descending(log):
    from services.privacy.boundary_report import build_boundary_report

    _seed(
        log,
        [_detail("PERSON", "Alice Chen", "Nadia Brookes")]
        + [_detail("PERSON", "Robert Henderson", "Marcus Whitfield")] * 3,
        tool_name="chat_context",
    )

    subs = build_boundary_report(collection_id="col-A")["boundaries"][0]["substitutions"]
    assert [s["occurrences"] for s in subs] == [3, 1]


def test_first_and_last_seen_span_the_mentions(log):
    from services.privacy.boundary_report import build_boundary_report

    early = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    late = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(log.db_path) as conn:
        for ts in (late, early):
            conn.execute(
                "INSERT INTO redaction_log (timestamp, session_id, collection_id, "
                "tool_name, document_id, entity_type, original_text, replacement, "
                "start_char, end_char, score) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ts, "sess-1", "col-A", "chat_context", None, "PERSON",
                 "Robert Henderson", "Marcus Whitfield", 0, 16, 0.9),
            )

    sub = build_boundary_report(collection_id="col-A")["boundaries"][0]["substitutions"][0]
    assert sub["first_seen"] == early
    assert sub["last_seen"] == late


# ── The reveal gate ──────────────────────────────────────────────────────────


def test_originals_absent_unless_revealed(log):
    from services.privacy.boundary_report import build_boundary_report

    _seed(log, [_detail("PERSON", "Robert Henderson", "Marcus Whitfield")],
          tool_name="chat_context")

    report = build_boundary_report(collection_id="col-A")
    sub = report["boundaries"][0]["substitutions"][0]

    assert "original" not in sub
    assert report["originals_included"] is False
    assert "Robert Henderson" not in json.dumps(report)
    # The stand-in is still shown — that's what actually crossed.
    assert sub["replacement"] == "Marcus Whitfield"


def test_originals_present_when_revealed(log):
    from services.privacy.boundary_report import build_boundary_report

    _seed(log, [_detail("PERSON", "Robert Henderson", "Marcus Whitfield")],
          tool_name="chat_context")

    report = build_boundary_report(collection_id="col-A", reveal=True)
    sub = report["boundaries"][0]["substitutions"][0]

    assert sub["original"] == "Robert Henderson"
    assert report["originals_included"] is True


# ── Filters ──────────────────────────────────────────────────────────────────


def test_report_filters_by_collection(log):
    from services.privacy.boundary_report import build_boundary_report

    _seed(log, [_detail("PERSON", "Robert Henderson", "Marcus Whitfield")],
          tool_name="chat_context", collection_id="col-A")
    _seed(log, [_detail("PERSON", "Alice Chen", "Nadia Brookes")],
          tool_name="chat_context", collection_id="col-B")

    assert build_boundary_report(collection_id="col-A")["total_redactions"] == 1
    assert build_boundary_report()["total_redactions"] == 2


def test_report_filters_by_since(log):
    from services.privacy.boundary_report import build_boundary_report

    old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with sqlite3.connect(log.db_path) as conn:
        conn.execute(
            "INSERT INTO redaction_log (timestamp, session_id, collection_id, "
            "tool_name, document_id, entity_type, original_text, replacement, "
            "start_char, end_char, score) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (old_ts, "sess-old", "col-A", "chat_context", None, "PERSON",
             "Yesterday Person", "[PERSON]", 0, 5, 0.9),
        )

    cutoff = datetime.now(timezone.utc).isoformat()
    _seed(log, [_detail("PERSON", "Robert Henderson", "Marcus Whitfield")],
          tool_name="chat_context")

    assert build_boundary_report(collection_id="col-A")["total_redactions"] == 2
    windowed = build_boundary_report(collection_id="col-A", since=cutoff)
    assert windowed["total_redactions"] == 1
    assert windowed["boundaries"][0]["substitutions"][0]["replacement"] == "Marcus Whitfield"


def test_report_filters_by_session(log):
    from services.privacy.boundary_report import build_boundary_report

    _seed(log, [_detail("PERSON", "Robert Henderson", "Marcus Whitfield")],
          tool_name="chat_context", session_id="sess-1")
    _seed(log, [_detail("PERSON", "Alice Chen", "Nadia Brookes")],
          tool_name="chat_context", session_id="sess-2")

    report = build_boundary_report(collection_id="col-A", session_id="sess-2")
    assert report["total_redactions"] == 1
    assert report["boundaries"][0]["substitutions"][0]["replacement"] == "Nadia Brookes"


# ── Plain-language surface ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "entity_type,expected",
    [
        ("PERSON", "Name"),
        ("FINANCIAL_ACCOUNT", "Account number"),
        ("US_SSN", "Social Security number"),
        ("CUSIP_IN_CONTEXT", "Security identifier"),
        ("SOMETHING_NEW", "Something new"),
        (None, "Identifier"),
    ],
)
def test_entity_label_is_plain_language(entity_type, expected):
    from services.privacy.boundary_report import entity_label

    assert entity_label(entity_type) == expected
    # No Presidio jargon reaches the panel.
    assert "_" not in entity_label(entity_type)


def test_headline_when_nothing_redacted(log):
    from services.privacy.boundary_report import build_boundary_report

    report = build_boundary_report(collection_id="col-empty")
    assert report["total_redactions"] == 0
    assert report["boundaries"] == []
    assert "Nothing has needed redacting" in report["headline"]


def test_headline_warns_when_redaction_disabled(log, monkeypatch):
    from config import settings
    from services.privacy.boundary_report import build_boundary_report

    monkeypatch.setattr(settings, "enable_pii_redaction", False, raising=False)
    report = build_boundary_report(collection_id="col-A")

    assert report["enabled"] is False
    assert "switched off" in report["headline"]


def test_ingest_time_control_is_declared_even_though_it_logs_nothing(log):
    """COMPLIANCE.md names three boundaries; one has nothing to log by design."""
    from services.privacy.boundary_report import build_boundary_report

    report = build_boundary_report(collection_id="col-A")
    assert report["unlogged_controls"]
    assert any(
        "never lands" in c["description"] for c in report["unlogged_controls"]
    )


# ── HTTP surface ─────────────────────────────────────────────────────────────


@pytest.fixture
def client(log):
    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app)


def test_http_report_hides_originals_by_default(client, log):
    _seed(log, [_detail("PERSON", "Robert Henderson", "Marcus Whitfield")],
          tool_name="chat_context", collection_id="col-http")

    r = client.get(
        "/api/redactions/boundary-report",
        params={"collection_id": "col-http"},
    )
    assert r.status_code == 200
    # Assert against the whole serialized body, not a single field — a nested
    # structure added later must not smuggle the original back in.
    assert "Robert Henderson" not in r.text
    assert "Marcus Whitfield" in r.text
    assert r.json()["originals_included"] is False


def test_http_report_reveals_originals_on_request(client, log):
    _seed(log, [_detail("PERSON", "Robert Henderson", "Marcus Whitfield")],
          tool_name="chat_context", collection_id="col-http-reveal")

    r = client.get(
        "/api/redactions/boundary-report",
        params={"collection_id": "col-http-reveal", "reveal": "true"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["originals_included"] is True
    sub = data["boundaries"][0]["substitutions"][0]
    assert sub["original"] == "Robert Henderson"
    assert sub["replacement"] == "Marcus Whitfield"


def test_http_report_shape_is_stable_when_empty(client):
    r = client.get(
        "/api/redactions/boundary-report",
        params={"collection_id": "col-nothing-here"},
    )
    assert r.status_code == 200
    data = r.json()
    for key in (
        "headline", "total_redactions", "distinct_values", "boundaries",
        "unlogged_controls", "enabled", "originals_included",
    ):
        assert key in data
    assert data["boundaries"] == []
