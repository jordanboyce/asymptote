"""Tests for services.morning_brief — daily morning brief computation,
rendering, delivery orchestrator, and the scheduled tick.

No real DB / no indexer manager. Monkeypatches the same seams as
``tests/test_digest.py`` plus a new ``_get_metadata_store`` seam.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services import morning_brief as morning_brief_module
from services.morning_brief import (
    CollectionMorningSignals,
    MorningBriefPayload,
    compute_morning_brief,
    run_scheduled_morning_brief_tick,
    send_morning_brief_for_user,
)
from services.morning_brief_template import (
    render_morning_brief_html, render_morning_brief_text,
)
from services.email import EmailResult


# Wednesday 2026-05-20 at 14:00 UTC == 10:00 America/New_York (DST).
NOW = datetime(2026, 5, 20, 14, 0, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


# ─── Fake stores ────────────────────────────────────────────────────────────


class FakeMeetingNotesStore:
    def __init__(self, action_items):
        self._items = list(action_items)

    def list_action_items(self, collection_id, *, status=None, assignee=None):
        out = []
        for item in self._items:
            if status and (item.get("status") or "open").lower() != status:
                continue
            out.append(item)
        return out


class FakeHoldingsStore:
    """Placeholder — never read directly; tlh.scan_unrealized_losses is patched."""


class FakeMetadataStore:
    def __init__(self, docs):
        self._docs = list(docs)

    def list_documents(self):
        return list(self._docs)


# ─── Patch helpers ──────────────────────────────────────────────────────────


def _patch_collections(monkeypatch, collections):
    from services.app_database import app_db
    monkeypatch.setattr(app_db, "get_all_collections", lambda owner_id=None: collections)


def _patch_meeting_notes_store(monkeypatch, by_collection):
    monkeypatch.setattr(
        morning_brief_module, "_get_meeting_notes_store", lambda cid: by_collection.get(cid)
    )


def _patch_holdings_store(monkeypatch, by_collection):
    monkeypatch.setattr(
        morning_brief_module, "_get_holdings_store", lambda cid: by_collection.get(cid)
    )


def _patch_metadata_store(monkeypatch, by_collection):
    monkeypatch.setattr(
        morning_brief_module, "_get_metadata_store", lambda cid: by_collection.get(cid)
    )


def _patch_tlh_scan(monkeypatch, by_store_id):
    import services.financial.tlh as tlh_mod

    def fake_scan(store, **_kwargs):
        return by_store_id.get(id(store), [])

    monkeypatch.setattr(tlh_mod, "scan_unrealized_losses", fake_scan)


def _isodate(d):
    return d.isoformat()


# ─── compute_morning_brief: action item bucketing ──────────────────────────


def test_overdue_action_items_bucketed(monkeypatch):
    yesterday = TODAY - timedelta(days=1)
    items = [{"description": "Call CPA", "status": "open", "due_date": _isodate(yesterday)}]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})
    _patch_metadata_store(monkeypatch, {"c1": None})

    payload = compute_morning_brief("user@example.com", now=NOW)
    assert payload.total_overdue == 1
    assert payload.total_due_today == 0
    assert payload.collections[0].overdue_action_items[0]["description"] == "Call CPA"


def test_due_today_action_items_bucketed(monkeypatch):
    items = [{"description": "Send IPS", "status": "open", "due_date": _isodate(TODAY)}]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})
    _patch_metadata_store(monkeypatch, {"c1": None})

    payload = compute_morning_brief("user@example.com", now=NOW)
    assert payload.total_due_today == 1
    assert payload.collections[0].due_today_action_items[0]["description"] == "Send IPS"


def test_due_this_week_action_items_bucketed(monkeypatch):
    items = [
        {"description": "Quarter review", "status": "open", "due_date": _isodate(TODAY + timedelta(days=3))},
        {"description": "Out of window", "status": "open", "due_date": _isodate(TODAY + timedelta(days=14))},
    ]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})
    _patch_metadata_store(monkeypatch, {"c1": None})

    payload = compute_morning_brief("user@example.com", now=NOW)
    assert payload.total_due_this_week == 1
    assert payload.collections[0].due_this_week_action_items[0]["description"] == "Quarter review"


def test_fresh_action_items_no_due_date(monkeypatch):
    """No due_date AND extracted in the last 24h → surfaces as 'fresh'."""
    just_inside = (NOW - timedelta(hours=8)).isoformat()
    items = [
        {"description": "Review 401k", "status": "open", "due_date": None, "extracted_at": just_inside},
    ]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})
    _patch_metadata_store(monkeypatch, {"c1": None})

    payload = compute_morning_brief("user@example.com", now=NOW)
    assert payload.total_fresh == 1
    assert payload.collections[0].fresh_action_items[0]["description"] == "Review 401k"


def test_undated_old_action_items_excluded(monkeypatch):
    """No due_date AND extracted > 24h ago → silently dropped to avoid noise."""
    way_old = (NOW - timedelta(days=10)).isoformat()
    items = [
        {"description": "Ancient", "status": "open", "due_date": None, "extracted_at": way_old},
    ]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})
    _patch_metadata_store(monkeypatch, {"c1": None})

    payload = compute_morning_brief("user@example.com", now=NOW)
    assert payload.has_anything is False


def test_unparseable_due_date_falls_through_to_fresh(monkeypatch):
    """'next meeting' isn't a date — should land in fresh if extracted recently."""
    just_inside = (NOW - timedelta(hours=8)).isoformat()
    items = [
        {"description": "Defer", "status": "open", "due_date": "next meeting", "extracted_at": just_inside},
    ]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})
    _patch_metadata_store(monkeypatch, {"c1": None})

    payload = compute_morning_brief("user@example.com", now=NOW)
    assert payload.total_fresh == 1


def test_future_due_date_out_of_week_excluded(monkeypatch):
    """An item due 60 days out doesn't surface in today's brief."""
    items = [{"description": "Far future", "status": "open", "due_date": _isodate(TODAY + timedelta(days=60))}]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})
    _patch_metadata_store(monkeypatch, {"c1": None})

    payload = compute_morning_brief("user@example.com", now=NOW)
    assert payload.has_anything is False


def test_closed_action_items_excluded(monkeypatch):
    items = [
        {"description": "Open work", "status": "open", "due_date": _isodate(TODAY)},
        {"description": "Done", "status": "closed", "due_date": _isodate(TODAY)},
    ]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})
    _patch_metadata_store(monkeypatch, {"c1": None})

    payload = compute_morning_brief("user@example.com", now=NOW)
    assert payload.total_due_today == 1
    assert payload.collections[0].due_today_action_items[0]["description"] == "Open work"


# ─── compute_morning_brief: TLH ─────────────────────────────────────────────


def test_tlh_candidates_aggregate(monkeypatch):
    candidates = [
        SimpleNamespace(symbol="PKST", account="Joint Taxable", unrealized_loss=4250.0,
                        loss_pct=18.3, holding_period="short_term"),
        SimpleNamespace(symbol="WBA", account="Joint Taxable", unrealized_loss=1820.0,
                        loss_pct=9.7, holding_period="long_term"),
    ]
    fake_holdings = FakeHoldingsStore()
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": None})
    _patch_holdings_store(monkeypatch, {"c1": fake_holdings})
    _patch_metadata_store(monkeypatch, {"c1": None})
    _patch_tlh_scan(monkeypatch, {id(fake_holdings): candidates})

    payload = compute_morning_brief("user@example.com", now=NOW)
    assert len(payload.collections[0].tlh_candidates) == 2
    assert payload.total_tlh_dollars == pytest.approx(6070.0)


def test_tlh_candidates_without_symbol_filtered(monkeypatch):
    """Same rule as the digest — empty/None/whitespace symbol is dropped."""
    candidates = [
        SimpleNamespace(symbol="EDV", account="Joint Taxable", unrealized_loss=16735.0,
                        loss_pct=34.2, holding_period="long_term"),
        SimpleNamespace(symbol=None, account="Joint", unrealized_loss=17325.0,
                        loss_pct=10.5, holding_period="short_term"),
        SimpleNamespace(symbol="   ", account="Joint", unrealized_loss=2909.0,
                        loss_pct=2.6, holding_period="short_term"),
    ]
    fake_holdings = FakeHoldingsStore()
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": None})
    _patch_holdings_store(monkeypatch, {"c1": fake_holdings})
    _patch_metadata_store(monkeypatch, {"c1": None})
    _patch_tlh_scan(monkeypatch, {id(fake_holdings): candidates})

    payload = compute_morning_brief("user@example.com", now=NOW)
    surfaced = payload.collections[0].tlh_candidates
    assert len(surfaced) == 1
    assert surfaced[0]["symbol"] == "EDV"


# ─── compute_morning_brief: new documents ──────────────────────────────────


def test_new_documents_within_24h(monkeypatch):
    just_inside = (NOW - timedelta(hours=10)).isoformat()
    just_outside = (NOW - timedelta(hours=30)).isoformat()
    docs = [
        {"filename": "schwab_q1.pdf", "document_id": "d1", "num_pages": 3,
         "upload_timestamp": just_inside},
        {"filename": "old.pdf", "document_id": "d0", "num_pages": 1,
         "upload_timestamp": just_outside},
    ]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": None})
    _patch_holdings_store(monkeypatch, {"c1": None})
    _patch_metadata_store(monkeypatch, {"c1": FakeMetadataStore(docs)})

    payload = compute_morning_brief("user@example.com", now=NOW)
    assert payload.total_new_documents == 1
    assert payload.collections[0].new_documents[0]["filename"] == "schwab_q1.pdf"


def test_new_documents_capped_per_collection(monkeypatch):
    just_inside = (NOW - timedelta(hours=2)).isoformat()
    docs = [
        {"filename": f"f{i}.pdf", "document_id": f"d{i}", "num_pages": 1,
         "upload_timestamp": just_inside}
        for i in range(20)
    ]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": None})
    _patch_holdings_store(monkeypatch, {"c1": None})
    _patch_metadata_store(monkeypatch, {"c1": FakeMetadataStore(docs)})

    payload = compute_morning_brief("user@example.com", now=NOW)
    # MAX_NEW_DOCUMENTS_PER_COLLECTION = 5
    assert payload.total_new_documents == 5


# ─── compute_morning_brief: resilience ──────────────────────────────────────


def test_empty_collections_returns_quiet_payload(monkeypatch):
    _patch_collections(monkeypatch, [])
    payload = compute_morning_brief("user@example.com", now=NOW)
    assert payload.has_anything is False
    assert payload.total_overdue == 0


def test_store_lookup_failure_doesnt_explode(monkeypatch):
    class ThrowingStore:
        def list_action_items(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    _patch_collections(monkeypatch, [{"id": "c1", "name": "Crashy"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": ThrowingStore()})
    _patch_holdings_store(monkeypatch, {"c1": None})
    _patch_metadata_store(monkeypatch, {"c1": None})

    payload = compute_morning_brief("user@example.com", now=NOW)
    assert payload.has_anything is False


# ─── Rendering ─────────────────────────────────────────────────────────────


def _sample_payload():
    return MorningBriefPayload(
        user_id="user@example.com",
        generated_at=NOW.isoformat(),
        date_label="Wednesday, May 20, 2026",
        collections=[
            CollectionMorningSignals(
                collection_id="c1",
                collection_name="Henderson Family",
                overdue_action_items=[{"description": "Call CPA", "assignee": "advisor", "due_date": "2026-05-19"}],
                due_today_action_items=[{"description": "Send IPS", "assignee": "advisor", "due_date": "2026-05-20"}],
                fresh_action_items=[{"description": "Review 401k contribution change"}],
                tlh_candidates=[
                    {"symbol": "PKST", "account": "Joint Taxable", "unrealized_loss": 4250.0,
                     "loss_pct": 18.3, "holding_period": "short_term"},
                ],
                new_documents=[
                    {"filename": "schwab_q1.pdf", "document_id": "d1", "num_pages": 3,
                     "upload_timestamp": NOW.isoformat()},
                ],
            ),
        ],
    )


def test_html_render_contains_signals():
    html = render_morning_brief_html(_sample_payload())
    assert "Henderson Family" in html
    assert "Call CPA" in html
    assert "Send IPS" in html
    assert "Review 401k" in html
    assert "PKST" in html
    assert "$4,250" in html
    assert "schwab_q1.pdf" in html
    assert "Wednesday, May 20, 2026" in html
    # Section labels should appear
    assert "Overdue" in html
    assert "Due today" in html


def test_text_render_contains_signals():
    text = render_morning_brief_text(_sample_payload())
    assert "Henderson Family" in text
    assert "OVERDUE" in text
    assert "Due today" in text
    assert "Call CPA" in text
    assert "PKST" in text
    assert "schwab_q1.pdf" in text


def test_quiet_payload_renders_friendly():
    payload = MorningBriefPayload(
        user_id="user@example.com",
        generated_at=NOW.isoformat(),
        date_label="Wednesday, May 20, 2026",
        collections=[],
    )
    html = render_morning_brief_html(payload)
    text = render_morning_brief_text(payload)
    assert "Nothing" in html or "nothing" in html
    assert "Nothing" in text or "nothing" in text


def test_preview_banner_shown_when_requested():
    html = render_morning_brief_html(_sample_payload(), is_preview=True)
    assert "Preview" in html


def test_empty_collection_card_skipped():
    payload = MorningBriefPayload(
        user_id="user@example.com",
        generated_at=NOW.isoformat(),
        date_label="Wednesday, May 20, 2026",
        collections=[
            CollectionMorningSignals(collection_id="c1", collection_name="Henderson"),  # empty
            CollectionMorningSignals(
                collection_id="c2",
                collection_name="Park",
                due_today_action_items=[{"description": "Call CPA", "assignee": "client"}],
            ),
        ],
    )
    html = render_morning_brief_html(payload)
    assert "Park" in html
    assert "Henderson" not in html


# ─── send_morning_brief_for_user ───────────────────────────────────────────


def test_send_falls_back_to_user_id_when_no_override(monkeypatch):
    _patch_collections(monkeypatch, [])

    sent_kwargs = {}
    def fake_send(**kwargs):
        sent_kwargs.update(kwargs)
        return EmailResult(ok=True, id="msg_1")

    import services.email as email_mod
    monkeypatch.setattr(email_mod, "send_email", fake_send)

    result = send_morning_brief_for_user("advisor@firm.com")
    assert result["ok"] is True
    assert result["recipient"] == "advisor@firm.com"
    assert sent_kwargs["to"] == "advisor@firm.com"
    assert "Finn morning brief" in sent_kwargs["subject"]


def test_send_uses_email_override_when_set(monkeypatch):
    from services.app_database import app_db

    _patch_collections(monkeypatch, [])
    monkeypatch.setattr(app_db, "get_morning_brief_preferences", lambda uid: {
        **app_db.DEFAULT_MORNING_BRIEF_PREFERENCES,
        "enabled": True,
        "email_override": "ops@firm.com",
    })

    sent_kwargs = {}
    def fake_send(**kwargs):
        sent_kwargs.update(kwargs)
        return EmailResult(ok=True, id="msg_2")

    import services.email as email_mod
    monkeypatch.setattr(email_mod, "send_email", fake_send)

    result = send_morning_brief_for_user("advisor@firm.com")
    assert result["ok"] is True
    assert sent_kwargs["to"] == "ops@firm.com"


def test_send_fails_gracefully_when_no_email(monkeypatch):
    from services.app_database import app_db

    _patch_collections(monkeypatch, [])
    monkeypatch.setattr(app_db, "get_morning_brief_preferences", lambda uid: app_db.DEFAULT_MORNING_BRIEF_PREFERENCES)

    result = send_morning_brief_for_user("default")
    assert result["ok"] is False
    assert "email" in (result["error"] or "").lower()


def test_send_preview_marks_subject(monkeypatch):
    _patch_collections(monkeypatch, [])

    sent_kwargs = {}
    def fake_send(**kwargs):
        sent_kwargs.update(kwargs)
        return EmailResult(ok=True, id="msg_3")

    import services.email as email_mod
    monkeypatch.setattr(email_mod, "send_email", fake_send)

    result = send_morning_brief_for_user("advisor@firm.com", is_preview=True)
    assert result["ok"] is True
    assert sent_kwargs["subject"].startswith("[Preview]")


# ─── run_scheduled_morning_brief_tick ──────────────────────────────────────


def test_tick_sends_when_hour_matches(monkeypatch):
    from services.app_database import app_db

    # NY is UTC-4 in May 2026 (DST). Wed 5/20 14:00 UTC == Wed 10:00 NY
    fake_now = datetime(2026, 5, 20, 14, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(app_db, "list_morning_brief_optins", lambda: [
        {"user_id": "advisor@firm.com", "enabled": True, "hour": 10, "weekdays_only": True,
         "email_override": None},
    ])
    sent = []
    monkeypatch.setattr(
        morning_brief_module, "send_morning_brief_for_user",
        lambda uid, **kw: sent.append(uid) or {"ok": True, "id": "msg", "recipient": uid, "payload": None}
    )
    monkeypatch.setattr(app_db, "set_morning_brief_preferences", lambda uid, prefs: {})

    result = run_scheduled_morning_brief_tick(now=fake_now)
    assert result["sent"] == 1
    assert sent == ["advisor@firm.com"]


def test_tick_skips_when_hour_doesnt_match(monkeypatch):
    from services.app_database import app_db

    # Wed 14:00 UTC == 10:00 NY; user wants 7am NY → skip
    fake_now = datetime(2026, 5, 20, 14, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(app_db, "list_morning_brief_optins", lambda: [
        {"user_id": "advisor@firm.com", "enabled": True, "hour": 7, "weekdays_only": True,
         "email_override": None},
    ])
    sent = []
    monkeypatch.setattr(
        morning_brief_module, "send_morning_brief_for_user",
        lambda uid, **kw: sent.append(uid) or {"ok": True}
    )

    result = run_scheduled_morning_brief_tick(now=fake_now)
    assert result["sent"] == 0
    assert result["skipped"] == 1
    assert sent == []


def test_tick_skips_weekend_when_weekdays_only(monkeypatch):
    from services.app_database import app_db

    # Saturday 2026-05-23 14:00 UTC == 10:00 NY — should skip since weekdays_only
    fake_now = datetime(2026, 5, 23, 14, 0, 0, tzinfo=timezone.utc)
    assert fake_now.weekday() == 5  # sanity

    monkeypatch.setattr(app_db, "list_morning_brief_optins", lambda: [
        {"user_id": "advisor@firm.com", "enabled": True, "hour": 10, "weekdays_only": True,
         "email_override": None},
    ])
    sent = []
    monkeypatch.setattr(
        morning_brief_module, "send_morning_brief_for_user",
        lambda uid, **kw: sent.append(uid) or {"ok": True}
    )

    result = run_scheduled_morning_brief_tick(now=fake_now)
    assert result["sent"] == 0
    assert result["skipped"] == 1


def test_tick_sends_on_weekend_when_weekdays_only_off(monkeypatch):
    from services.app_database import app_db

    fake_now = datetime(2026, 5, 23, 14, 0, 0, tzinfo=timezone.utc)  # Sat 10:00 NY

    monkeypatch.setattr(app_db, "list_morning_brief_optins", lambda: [
        {"user_id": "advisor@firm.com", "enabled": True, "hour": 10, "weekdays_only": False,
         "email_override": None},
    ])
    sent = []
    monkeypatch.setattr(
        morning_brief_module, "send_morning_brief_for_user",
        lambda uid, **kw: sent.append(uid) or {"ok": True, "id": "msg", "recipient": uid, "payload": None}
    )
    monkeypatch.setattr(app_db, "set_morning_brief_preferences", lambda uid, prefs: {})

    result = run_scheduled_morning_brief_tick(now=fake_now)
    assert result["sent"] == 1


def test_tick_respects_last_sent_idempotency(monkeypatch):
    from services.app_database import app_db

    # Wed 14:00 UTC == 10:00 NY; last sent only 10 hours ago → still inside the 23h window
    fake_now = datetime(2026, 5, 20, 14, 0, 0, tzinfo=timezone.utc)
    last_sent = datetime(2026, 5, 20, 0, 0, 0).isoformat()  # 10h ago, NY-naive

    monkeypatch.setattr(app_db, "list_morning_brief_optins", lambda: [
        {"user_id": "advisor@firm.com", "enabled": True, "hour": 10, "weekdays_only": True,
         "email_override": None, "last_sent_at": last_sent},
    ])
    sent = []
    monkeypatch.setattr(
        morning_brief_module, "send_morning_brief_for_user",
        lambda uid, **kw: sent.append(uid) or {"ok": True}
    )

    result = run_scheduled_morning_brief_tick(now=fake_now)
    assert result["sent"] == 0
    assert sent == []


def test_tick_sends_again_after_23h(monkeypatch):
    """The next morning's tick must fire — confirm the 23h window honors that."""
    from services.app_database import app_db

    fake_now = datetime(2026, 5, 20, 14, 0, 0, tzinfo=timezone.utc)  # Wed 10:00 NY
    # Last sent yesterday morning at the same NY hour
    last_sent = datetime(2026, 5, 19, 10, 0, 0).isoformat()

    monkeypatch.setattr(app_db, "list_morning_brief_optins", lambda: [
        {"user_id": "advisor@firm.com", "enabled": True, "hour": 10, "weekdays_only": True,
         "email_override": None, "last_sent_at": last_sent},
    ])
    sent = []
    monkeypatch.setattr(
        morning_brief_module, "send_morning_brief_for_user",
        lambda uid, **kw: sent.append(uid) or {"ok": True, "id": "msg", "recipient": uid, "payload": None}
    )
    monkeypatch.setattr(app_db, "set_morning_brief_preferences", lambda uid, prefs: {})

    result = run_scheduled_morning_brief_tick(now=fake_now)
    assert result["sent"] == 1


def test_tick_failed_send_increments_failed_count(monkeypatch):
    from services.app_database import app_db

    fake_now = datetime(2026, 5, 20, 14, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(app_db, "list_morning_brief_optins", lambda: [
        {"user_id": "advisor@firm.com", "enabled": True, "hour": 10, "weekdays_only": True,
         "email_override": None},
    ])
    monkeypatch.setattr(
        morning_brief_module, "send_morning_brief_for_user",
        lambda uid, **kw: {"ok": False, "error": "Resend rejected"}
    )

    result = run_scheduled_morning_brief_tick(now=fake_now)
    assert result["failed"] == 1
    assert result["sent"] == 0
