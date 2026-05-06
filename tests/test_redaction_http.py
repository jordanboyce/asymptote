"""HTTP-level tests for the new redaction endpoints introduced in §9.1
of the advisor-desktop-ux spec — the chat-side summary pill and the
Note-of-Record dry-run preview both depend on these.
"""

from __future__ import annotations

import os
import sys
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """Real FastAPI TestClient over main.app, with the redaction log DB
    pointed at a tmp file so it doesn't smear across the dev `data/`."""
    tmp_dir = tmp_path_factory.mktemp("redaction_http")
    db_path = tmp_dir / "redaction_log.db"

    from services.privacy.redaction_log import RedactionLog
    import services.privacy.redaction_log as rl_mod

    rl_mod.redaction_log = RedactionLog(db_path=db_path)

    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app), rl_mod.redaction_log


def _seed(log, *, collection_id="collection-A", session_id="sess-1"):
    """Insert a fixed mix of redactions so summary/log endpoints have data."""
    from services.privacy.redaction_engine import RedactionDetail

    log.log_redactions(
        [
            RedactionDetail("PERSON", 0, 5, 0.9, "Alice", "[PERSON]"),
            RedactionDetail("PERSON", 10, 13, 0.85, "Bob", "[PERSON]"),
            RedactionDetail("EMAIL_ADDRESS", 20, 40, 0.99, "a@b.com", "[EMAIL_ADDRESS]"),
        ],
        session_id=session_id,
        collection_id=collection_id,
        tool_name="notes_output",
    )


def test_redactions_summary_filters_by_collection(client):
    c, log = client
    _seed(log, collection_id="collection-summary-A", session_id="sumsess-1")
    _seed(log, collection_id="collection-summary-B", session_id="sumsess-2")

    r = c.get(
        "/api/redactions/summary",
        params={"collection_id": "collection-summary-A"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["collection_id"] == "collection-summary-A"
    assert data["total_redactions"] == 3
    assert data["by_entity_type"]["PERSON"] == 2
    assert data["by_entity_type"]["EMAIL_ADDRESS"] == 1
    assert data["by_tool"] == {"notes_output": 3}


def test_redactions_summary_with_since(client):
    c, log = client

    cutoff = datetime.now(timezone.utc).isoformat()

    # Insert one redaction strictly before the cutoff so it must be excluded.
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with sqlite3.connect(log.db_path) as conn:
        conn.execute(
            "INSERT INTO redaction_log (timestamp, session_id, collection_id, "
            "tool_name, document_id, entity_type, original_text, replacement, "
            "start_char, end_char, score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (old_ts, "old-session", "collection-since", "notes_output", None,
             "PERSON", "Stale", "[PERSON]", 0, 5, 0.9),
        )

    _seed(log, collection_id="collection-since", session_id="sumsess-3")

    r = c.get(
        "/api/redactions/summary",
        params={"collection_id": "collection-since", "since": cutoff},
    )
    assert r.status_code == 200
    data = r.json()
    # The "old" event is excluded — only the freshly-seeded 3.
    assert data["total_redactions"] == 3


def test_redactions_log_strips_original_text(client):
    c, log = client
    _seed(log, collection_id="collection-log", session_id="logsess")

    r = c.get(
        "/api/redactions/log",
        params={"collection_id": "collection-log", "limit": 10},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total_returned"] >= 3
    for ev in data["events"]:
        # Original PII must never appear in the response — it stays local-only.
        assert "original_text" not in ev
        assert ev["entity_type"]
        assert ev["replacement"]


def test_redactions_dry_run_returns_redacted_text(client, monkeypatch):
    c, _log = client

    # Fake redaction engine — keeps the test independent of Presidio install.
    class _FakeResult:
        def __init__(self, redacted_text, details):
            self.redacted_text = redacted_text
            self.details = details
        @property
        def had_pii(self):
            return bool(self.details)

    class _FakeDetail:
        def __init__(self, **kw):
            self.entity_type = kw["entity_type"]
            self.start = kw["start"]
            self.end = kw["end"]
            self.score = kw["score"]
            self.replacement = kw["replacement"]

    class _FakeEngine:
        available = True
        def redact_text(self, text, collection_id=None):
            return _FakeResult(
                redacted_text="Hello [PERSON]",
                details=[_FakeDetail(
                    entity_type="PERSON", start=6, end=10,
                    score=0.95, replacement="[PERSON]",
                )],
            )

    import services.privacy.redaction_engine as engine_mod
    monkeypatch.setattr(engine_mod, "redaction_engine", _FakeEngine())

    from config import settings
    monkeypatch.setattr(settings, "enable_pii_redaction", True, raising=False)

    r = c.post(
        "/api/redactions/dry-run",
        json={"text": "Hello Jane", "collection_id": "collection-dryrun"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["original"] == "Hello Jane"
    assert data["redacted"] == "Hello [PERSON]"
    assert data["had_pii"] is True
    assert data["entity_count"] == 1
    assert data["entities"][0]["entity_type"] == "PERSON"
    assert data["entities"][0]["replacement"] == "[PERSON]"


def test_redactions_dry_run_passthrough_when_disabled(client, monkeypatch):
    c, _log = client
    from config import settings
    monkeypatch.setattr(settings, "enable_pii_redaction", False, raising=False)

    r = c.post(
        "/api/redactions/dry-run",
        json={"text": "Hello Jane Smith", "collection_id": None},
    )
    assert r.status_code == 200
    data = r.json()
    # Redaction disabled: input passes through, no entities reported.
    assert data["original"] == "Hello Jane Smith"
    assert data["redacted"] == "Hello Jane Smith"
    assert data["had_pii"] is False
    assert data["entity_count"] == 0
