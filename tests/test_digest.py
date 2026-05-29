"""Tests for services.digest — weekly advisor digest computation, rendering,
delivery orchestrator, and the scheduled tick.

No real DB / no indexer manager. Monkeypatches the digest module's seams:

* ``_get_meeting_notes_store`` and ``_get_holdings_store`` — the two helpers
  ``compute_weekly_digest`` uses to reach into a collection's stores.
* ``app_db.get_all_collections`` — to inject the fake collection list.
* ``services.email.send_email`` — to capture sent mails without hitting Resend.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services import digest as digest_module
from services.digest import (
    CollectionSignals,
    DigestPayload,
    compute_weekly_digest,
    run_scheduled_digest_tick,
    send_digest_for_user,
)
from services.digest_template import render_digest_html, render_digest_text
from services.email import EmailResult


NOW = datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc)


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


# ─── compute_weekly_digest ──────────────────────────────────────────────────


def _patch_collections(monkeypatch, collections):
    from services.app_database import app_db
    monkeypatch.setattr(app_db, "get_all_collections", lambda owner_id=None: collections)


def _patch_meeting_notes_store(monkeypatch, by_collection):
    """by_collection: dict[collection_id, FakeMeetingNotesStore | None]."""
    monkeypatch.setattr(
        digest_module, "_get_meeting_notes_store", lambda cid: by_collection.get(cid)
    )


def _patch_holdings_store(monkeypatch, by_collection):
    monkeypatch.setattr(
        digest_module, "_get_holdings_store", lambda cid: by_collection.get(cid)
    )


def _patch_tlh_scan(monkeypatch, by_store_id):
    """Patch services.financial.tlh.scan_unrealized_losses.

    by_store_id maps the FakeHoldingsStore *id()* to a list of dataclass-like
    candidates (SimpleNamespace works since compute_weekly_digest uses getattr).
    """
    import services.financial.tlh as tlh_mod

    def fake_scan(store, **_kwargs):
        return by_store_id.get(id(store), [])

    monkeypatch.setattr(tlh_mod, "scan_unrealized_losses", fake_scan)


def test_new_action_items_within_week_window(monkeypatch):
    week_ago = NOW - timedelta(days=7)
    just_inside = (NOW - timedelta(days=3)).isoformat()
    just_outside = (NOW - timedelta(days=10)).isoformat()
    items = [
        {"description": "Recent item", "status": "open", "extracted_at": just_inside},
        {"description": "Old item", "status": "open", "extracted_at": just_outside},
    ]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})

    payload = compute_weekly_digest("user@example.com", now=NOW)
    assert payload.total_new_action_items == 1
    assert payload.collections[0].new_action_items[0]["description"] == "Recent item"


def test_aging_items_outside_threshold(monkeypatch):
    aging = (NOW - timedelta(days=45)).isoformat()
    items = [{"description": "Stale item", "status": "open", "extracted_at": aging}]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})

    payload = compute_weekly_digest("user@example.com", now=NOW)
    assert payload.total_aging_action_items == 1
    assert payload.collections[0].aging_action_items[0]["description"] == "Stale item"


def test_action_items_in_aging_grace_window_excluded(monkeypatch):
    """Items between 7 days (new cutoff) and 30 days (aging cutoff) appear nowhere."""
    in_grace = (NOW - timedelta(days=14)).isoformat()
    items = [{"description": "Mid-aged item", "status": "open", "extracted_at": in_grace}]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})

    payload = compute_weekly_digest("user@example.com", now=NOW)
    assert payload.total_new_action_items == 0
    assert payload.total_aging_action_items == 0


def test_tlh_candidates_without_symbol_are_filtered(monkeypatch):
    """Candidates with empty/None symbol shouldn't appear in the digest —
    they're real holdings rows but an advisor can't act on them."""
    candidates = [
        SimpleNamespace(symbol="EDV", account="Joint Taxable", unrealized_loss=16735.0,
                        loss_pct=34.2, holding_period="long_term"),
        SimpleNamespace(symbol=None, account="Joint Taxable", unrealized_loss=17325.0,
                        loss_pct=10.5, holding_period="short_term"),
        SimpleNamespace(symbol="", account="Joint Taxable", unrealized_loss=3171.0,
                        loss_pct=6.3, holding_period="short_term"),
        SimpleNamespace(symbol="   ", account="Joint Taxable", unrealized_loss=2909.0,
                        loss_pct=2.6, holding_period="short_term"),
    ]
    fake_holdings = FakeHoldingsStore()
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": None})
    _patch_holdings_store(monkeypatch, {"c1": fake_holdings})
    _patch_tlh_scan(monkeypatch, {id(fake_holdings): candidates})

    payload = compute_weekly_digest("user@example.com", now=NOW)
    surfaced = payload.collections[0].tlh_candidates
    assert len(surfaced) == 1
    assert surfaced[0]["symbol"] == "EDV"


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
    _patch_tlh_scan(monkeypatch, {id(fake_holdings): candidates})

    payload = compute_weekly_digest("user@example.com", now=NOW)
    assert len(payload.collections[0].tlh_candidates) == 2
    assert payload.total_tlh_dollars == pytest.approx(6070.0)
    assert payload.collections[0].tlh_candidates[0]["symbol"] == "PKST"
    assert payload.collections[0].tlh_candidates[0]["holding_period"] == "short_term"


def test_empty_collections_returns_quiet_payload(monkeypatch):
    _patch_collections(monkeypatch, [])
    payload = compute_weekly_digest("user@example.com", now=NOW)
    assert payload.has_anything is False
    assert payload.total_new_action_items == 0
    assert payload.total_aging_action_items == 0
    assert payload.total_tlh_dollars == 0


def test_collection_with_no_signals_marked_empty(monkeypatch):
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Quiet"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore([])})
    _patch_holdings_store(monkeypatch, {"c1": None})

    payload = compute_weekly_digest("user@example.com", now=NOW)
    assert len(payload.collections) == 1
    assert payload.collections[0].is_empty
    assert payload.has_anything is False


def test_store_lookup_failure_doesnt_explode(monkeypatch):
    class ThrowingStore:
        def list_action_items(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    _patch_collections(monkeypatch, [{"id": "c1", "name": "Crashy"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": ThrowingStore()})
    _patch_holdings_store(monkeypatch, {"c1": None})

    payload = compute_weekly_digest("user@example.com", now=NOW)
    assert payload.has_anything is False  # the throw was logged, collection skipped


def test_closed_action_items_excluded(monkeypatch):
    """list_action_items is called with status='open'; closed items don't surface
    because the fake store honors the filter."""
    just_inside = (NOW - timedelta(days=3)).isoformat()
    items = [
        {"description": "Open", "status": "open", "extracted_at": just_inside},
        {"description": "Done", "status": "closed", "extracted_at": just_inside},
    ]
    _patch_collections(monkeypatch, [{"id": "c1", "name": "Henderson"}])
    _patch_meeting_notes_store(monkeypatch, {"c1": FakeMeetingNotesStore(items)})
    _patch_holdings_store(monkeypatch, {"c1": None})

    payload = compute_weekly_digest("user@example.com", now=NOW)
    assert payload.total_new_action_items == 1
    assert payload.collections[0].new_action_items[0]["description"] == "Open"


# ─── Rendering ─────────────────────────────────────────────────────────────


def _sample_payload():
    return DigestPayload(
        user_id="user@example.com",
        generated_at=NOW.isoformat(),
        period_label="May 13 - May 20, 2026",
        collections=[
            CollectionSignals(
                collection_id="c1",
                collection_name="Henderson Family",
                new_action_items=[{"description": "Review 529", "assignee": "advisor", "due_date": "2026-06-01"}],
                aging_action_items=[{"description": "Roth ladder", "assignee": "advisor", "due_date": None}],
                tlh_candidates=[
                    {"symbol": "PKST", "account": "Joint Taxable", "unrealized_loss": 4250.0,
                     "loss_pct": 18.3, "holding_period": "short_term"},
                ],
            ),
        ],
    )


def test_html_render_contains_signals():
    html = render_digest_html(_sample_payload())
    assert "Henderson Family" in html
    assert "Review 529" in html
    assert "Roth ladder" in html
    assert "PKST" in html
    assert "$4,250" in html
    assert "May 13 - May 20, 2026" in html


def test_text_render_contains_signals():
    text = render_digest_text(_sample_payload())
    assert "Henderson Family" in text
    assert "Review 529 funding" not in text  # confirm we didn't accidentally append text
    assert "Review 529" in text
    assert "PKST" in text
    assert "$4,250" in text


def test_quiet_payload_renders_friendly():
    payload = DigestPayload(
        user_id="user@example.com",
        generated_at=NOW.isoformat(),
        period_label="May 13 - May 20, 2026",
        collections=[],
    )
    html = render_digest_html(payload)
    text = render_digest_text(payload)
    assert "Nothing" in html or "nothing" in html
    assert "Nothing" in text or "nothing" in text


def test_preview_banner_shown_when_requested():
    html = render_digest_html(_sample_payload(), is_preview=True)
    assert "Preview" in html


def test_empty_collection_card_skipped():
    payload = DigestPayload(
        user_id="user@example.com",
        generated_at=NOW.isoformat(),
        period_label="May 13 - May 20, 2026",
        collections=[
            CollectionSignals(collection_id="c1", collection_name="Henderson"),  # all empty
            CollectionSignals(
                collection_id="c2",
                collection_name="Park",
                new_action_items=[{"description": "Call CPA", "assignee": "client"}],
            ),
        ],
    )
    html = render_digest_html(payload)
    assert "Park" in html
    # Henderson card should not be rendered (empty)
    assert "Henderson" not in html


# ─── send_digest_for_user ───────────────────────────────────────────────────


def test_send_falls_back_to_user_id_when_no_override(monkeypatch):
    _patch_collections(monkeypatch, [])

    sent_kwargs = {}
    def fake_send(**kwargs):
        sent_kwargs.update(kwargs)
        return EmailResult(ok=True, id="msg_1")

    # send_digest_for_user does a local `from services.email import send_email`,
    # so the patched binding lives on the services.email module.
    import services.email as email_mod
    monkeypatch.setattr(email_mod, "send_email", fake_send)

    result = send_digest_for_user("advisor@firm.com")
    assert result["ok"] is True
    assert result["recipient"] == "advisor@firm.com"
    assert sent_kwargs["to"] == "advisor@firm.com"
    assert "Finn weekly digest" in sent_kwargs["subject"]


def test_send_uses_email_override_when_set(monkeypatch):
    from services.app_database import app_db

    _patch_collections(monkeypatch, [])
    monkeypatch.setattr(app_db, "get_digest_preferences", lambda uid: {
        **app_db.DEFAULT_DIGEST_PREFERENCES,
        "enabled": True,
        "email_override": "ops@firm.com",
    })

    sent_kwargs = {}
    def fake_send(**kwargs):
        sent_kwargs.update(kwargs)
        return EmailResult(ok=True, id="msg_2")

    import services.email as email_mod
    monkeypatch.setattr(email_mod, "send_email", fake_send)

    result = send_digest_for_user("advisor@firm.com")
    assert result["ok"] is True
    assert sent_kwargs["to"] == "ops@firm.com"


def test_send_fails_gracefully_when_no_email(monkeypatch):
    from services.app_database import app_db

    _patch_collections(monkeypatch, [])
    monkeypatch.setattr(app_db, "get_digest_preferences", lambda uid: app_db.DEFAULT_DIGEST_PREFERENCES)

    # user_id "default" has no @ — should fail
    result = send_digest_for_user("default")
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

    result = send_digest_for_user("advisor@firm.com", is_preview=True)
    assert result["ok"] is True
    assert sent_kwargs["subject"].startswith("[Preview]")


# ─── run_scheduled_digest_tick ──────────────────────────────────────────────


def test_tick_sends_when_weekday_hour_matches(monkeypatch):
    from services.app_database import app_db

    # NY is UTC-4 in May 2026 (DST). Tuesday 5/19 16:00 UTC == Tuesday 12:00 NY
    fake_now = datetime(2026, 5, 19, 16, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(app_db, "list_digest_optins", lambda: [
        {"user_id": "advisor@firm.com", "enabled": True, "weekday": 1, "hour": 12, "email_override": None},
    ])
    sent = []
    monkeypatch.setattr(
        digest_module, "send_digest_for_user",
        lambda uid, **kw: sent.append(uid) or {"ok": True, "id": "msg", "recipient": uid, "payload": None}
    )
    monkeypatch.setattr(app_db, "set_digest_preferences", lambda uid, prefs: {})

    result = run_scheduled_digest_tick(now=fake_now)
    assert result["sent"] == 1
    assert sent == ["advisor@firm.com"]


def test_tick_skips_when_weekday_hour_doesnt_match(monkeypatch):
    from services.app_database import app_db

    # Same fake now, user wants Friday 16:00 NY — not matching Tuesday 12:00 NY
    fake_now = datetime(2026, 5, 19, 16, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(app_db, "list_digest_optins", lambda: [
        {"user_id": "advisor@firm.com", "enabled": True, "weekday": 4, "hour": 16, "email_override": None},
    ])
    sent = []
    monkeypatch.setattr(
        digest_module, "send_digest_for_user",
        lambda uid, **kw: sent.append(uid) or {"ok": True}
    )

    result = run_scheduled_digest_tick(now=fake_now)
    assert result["sent"] == 0
    assert result["skipped"] == 1
    assert sent == []


def test_tick_respects_last_sent_idempotency(monkeypatch):
    from services.app_database import app_db

    # NY time at this instant
    fake_now = datetime(2026, 5, 19, 16, 0, 0, tzinfo=timezone.utc)  # Tuesday 12:00 NY
    last_sent = datetime(2026, 5, 19, 11, 50, 0).isoformat()  # 10 min ago, NY-naive

    monkeypatch.setattr(app_db, "list_digest_optins", lambda: [
        {"user_id": "advisor@firm.com", "enabled": True, "weekday": 1, "hour": 12,
         "email_override": None, "last_sent_at": last_sent},
    ])
    sent = []
    monkeypatch.setattr(
        digest_module, "send_digest_for_user",
        lambda uid, **kw: sent.append(uid) or {"ok": True}
    )

    result = run_scheduled_digest_tick(now=fake_now)
    assert result["sent"] == 0  # idempotency guard fired
    assert sent == []


def test_tick_failed_send_increments_failed_count(monkeypatch):
    from services.app_database import app_db

    fake_now = datetime(2026, 5, 19, 16, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(app_db, "list_digest_optins", lambda: [
        {"user_id": "advisor@firm.com", "enabled": True, "weekday": 1, "hour": 12, "email_override": None},
    ])
    monkeypatch.setattr(
        digest_module, "send_digest_for_user",
        lambda uid, **kw: {"ok": False, "error": "Resend rejected"}
    )

    result = run_scheduled_digest_tick(now=fake_now)
    assert result["failed"] == 1
    assert result["sent"] == 0
