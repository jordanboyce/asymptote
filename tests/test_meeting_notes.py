"""Tests for services.meeting_notes — v4.5 audio → structured notes."""

from __future__ import annotations

import json

import pytest

from services.meeting_notes import (
    ActionItem,
    MeetingNotes,
    MeetingNotesStore,
    extract_meeting_notes,
    transcript_text_from_chunks,
)
from tests._fake_ai_provider import FakeAIProvider


# ──────────────────────────────────────────────────────────────────────────
# Dataclass round-trip
# ──────────────────────────────────────────────────────────────────────────

def test_action_item_to_dict_round_trip():
    ai = ActionItem(
        description="Send revised IPS",
        assignee="advisor",
        due_date="2026-05-20",
        status="open",
    )
    assert ActionItem.from_dict(ai.to_dict()) == ai


def test_action_item_defaults_open_status():
    ai = ActionItem.from_dict({"description": "Call client"})
    assert ai.status == "open"
    assert ai.assignee is None
    assert ai.due_date is None


def test_action_item_strips_whitespace_and_normalises_status():
    ai = ActionItem.from_dict({
        "description": "  Send PDF  ",
        "status": "  CLOSED ",
        "assignee": " client ",
    })
    assert ai.description == "Send PDF"
    assert ai.status == "closed"
    assert ai.assignee == "client"


# ──────────────────────────────────────────────────────────────────────────
# extract_meeting_notes — pure function with FakeAIProvider
# ──────────────────────────────────────────────────────────────────────────

_GOOD_LLM_RESPONSE = json.dumps({
    "client_concerns": [
        "Worried about market volatility heading into retirement",
        "Wants to consolidate accounts",
    ],
    "decisions": [
        "Rebalance to 60/40 next month",
        "Open Roth conversion ladder",
    ],
    "action_items": [
        {
            "description": "Send revised IPS for signature",
            "assignee": "advisor",
            "due_date": "2026-05-20",
            "status": "open",
        },
        {
            "description": "Forward last two 1099s",
            "assignee": "client",
            "due_date": "next meeting",
            "status": "open",
        },
    ],
    "follow_up_questions": [
        "Confirm 401(k) rollover eligibility with HR",
    ],
    "sentiment_notes": "Cautiously optimistic; engaged.",
})


def _provider_returning(text: str) -> FakeAIProvider:
    fake = FakeAIProvider()
    fake.queue_complete_response(text=text)
    return fake


def test_extract_returns_structured_notes_from_clean_json():
    fake = _provider_returning(_GOOD_LLM_RESPONSE)
    notes = extract_meeting_notes(
        "Transcript body — advisor and client discuss retirement plans.",
        collection_id="henderson",
        provider=fake,
        document_id="doc-123",
    )

    assert notes.client_concerns == [
        "Worried about market volatility heading into retirement",
        "Wants to consolidate accounts",
    ]
    assert notes.decisions == [
        "Rebalance to 60/40 next month",
        "Open Roth conversion ladder",
    ]
    assert len(notes.action_items) == 2
    assert notes.action_items[0].description == "Send revised IPS for signature"
    assert notes.action_items[0].assignee == "advisor"
    assert notes.action_items[1].assignee == "client"
    assert notes.follow_up_questions == ["Confirm 401(k) rollover eligibility with HR"]
    assert notes.sentiment_notes == "Cautiously optimistic; engaged."
    assert notes.raw_transcript_doc_id == "doc-123"


def test_extract_uses_provider_fast_model_by_default():
    fake = _provider_returning(_GOOD_LLM_RESPONSE)
    extract_meeting_notes(
        "non-empty transcript",
        collection_id="c1",
        provider=fake,
    )
    assert fake.complete_calls[0]["model"] == FakeAIProvider.FAST_MODEL


def test_extract_honors_explicit_model_override():
    fake = _provider_returning(_GOOD_LLM_RESPONSE)
    extract_meeting_notes(
        "non-empty transcript",
        collection_id="c1",
        provider=fake,
        model="claude-opus-4-7",
    )
    assert fake.complete_calls[0]["model"] == "claude-opus-4-7"


def test_extract_strips_markdown_fences():
    fenced = "```json\n" + _GOOD_LLM_RESPONSE + "\n```"
    fake = _provider_returning(fenced)
    notes = extract_meeting_notes(
        "non-empty transcript",
        collection_id="c1",
        provider=fake,
    )
    assert notes.decisions == [
        "Rebalance to 60/40 next month",
        "Open Roth conversion ladder",
    ]


def test_extract_returns_empty_notes_on_unparseable_json():
    fake = _provider_returning("not even close to JSON")
    notes = extract_meeting_notes(
        "non-empty transcript",
        collection_id="c1",
        provider=fake,
        document_id="doc-x",
    )
    assert notes.client_concerns == []
    assert notes.decisions == []
    assert notes.action_items == []
    assert notes.follow_up_questions == []
    assert notes.sentiment_notes is None
    assert notes.raw_transcript_doc_id == "doc-x"


def test_extract_returns_empty_notes_on_top_level_array():
    # A JSON array is valid JSON but not the expected shape.
    fake = _provider_returning('[{"description": "do thing"}]')
    notes = extract_meeting_notes(
        "non-empty transcript",
        collection_id="c1",
        provider=fake,
    )
    assert notes.action_items == []
    assert notes.decisions == []


def test_extract_skips_empty_transcript_without_calling_llm():
    fake = FakeAIProvider()  # no queued response — would error if called
    notes = extract_meeting_notes(
        "   ",
        collection_id="c1",
        provider=fake,
        document_id="doc-empty",
    )
    assert notes.client_concerns == []
    assert notes.action_items == []
    assert notes.raw_transcript_doc_id == "doc-empty"
    assert fake.complete_calls == []


def test_extract_handles_provider_exception_without_raising():
    class BoomProvider(FakeAIProvider):
        def complete(self, prompt, max_tokens, model):
            raise RuntimeError("API down")

    notes = extract_meeting_notes(
        "non-empty transcript",
        collection_id="c1",
        provider=BoomProvider(),
        document_id="doc-y",
    )
    assert notes.client_concerns == []
    assert notes.raw_transcript_doc_id == "doc-y"


def test_extract_coerces_string_action_items_to_objects():
    response = json.dumps({
        "client_concerns": [],
        "decisions": [],
        "action_items": ["Send IPS", "Call broker"],
        "follow_up_questions": [],
        "sentiment_notes": None,
    })
    fake = _provider_returning(response)
    notes = extract_meeting_notes(
        "non-empty transcript",
        collection_id="c1",
        provider=fake,
    )
    assert [a.description for a in notes.action_items] == ["Send IPS", "Call broker"]
    assert all(a.status == "open" for a in notes.action_items)


def test_extract_drops_action_items_with_empty_description():
    response = json.dumps({
        "client_concerns": [],
        "decisions": [],
        "action_items": [
            {"description": "  ", "assignee": "advisor"},
            {"description": "Valid", "assignee": "client"},
        ],
        "follow_up_questions": [],
        "sentiment_notes": None,
    })
    fake = _provider_returning(response)
    notes = extract_meeting_notes(
        "non-empty transcript",
        collection_id="c1",
        provider=fake,
    )
    assert len(notes.action_items) == 1
    assert notes.action_items[0].description == "Valid"


def test_extract_prompt_includes_untrusted_content_guardrail():
    fake = _provider_returning(_GOOD_LLM_RESPONSE)
    extract_meeting_notes(
        "transcript",
        collection_id="c1",
        provider=fake,
    )
    sent = fake.complete_calls[0]["prompt"]
    assert "UNTRUSTED" in sent
    assert "prompt-injection" in sent or "ignore previous instructions" in sent.lower()


def test_extract_prompt_frames_output_advisor_pov():
    fake = _provider_returning(_GOOD_LLM_RESPONSE)
    extract_meeting_notes(
        "transcript",
        collection_id="c1",
        provider=fake,
    )
    sent = fake.complete_calls[0]["prompt"]
    assert "advisor" in sent.lower()


# ──────────────────────────────────────────────────────────────────────────
# MeetingNotesStore round-trip + queries
# ──────────────────────────────────────────────────────────────────────────

def _sample_notes(doc_id: str = "doc-123") -> MeetingNotes:
    return MeetingNotes(
        client_concerns=["Volatility worry"],
        decisions=["Rebalance to 60/40"],
        action_items=[
            ActionItem(description="Send IPS", assignee="advisor", status="open"),
            ActionItem(description="Forward 1099", assignee="client", status="closed"),
        ],
        follow_up_questions=["Verify 401(k) rollover eligibility"],
        sentiment_notes="Engaged.",
        raw_transcript_doc_id=doc_id,
    )


def test_store_save_and_get_round_trip(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(
        document_id="doc-1",
        collection_id="henderson",
        notes=_sample_notes("doc-1"),
    )
    record = store.get("doc-1")
    assert record is not None
    assert record["document_id"] == "doc-1"
    assert record["collection_id"] == "henderson"
    assert record["client_concerns"] == ["Volatility worry"]
    assert record["decisions"] == ["Rebalance to 60/40"]
    assert len(record["action_items"]) == 2
    assert record["action_items"][0]["description"] == "Send IPS"
    assert record["sentiment_notes"] == "Engaged."


def test_store_save_is_idempotent_per_document(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes("doc-1"))

    updated = MeetingNotes(
        client_concerns=["New concern"],
        decisions=["New decision"],
        action_items=[],
        follow_up_questions=[],
        sentiment_notes=None,
        raw_transcript_doc_id="doc-1",
    )
    store.save(document_id="doc-1", collection_id="c", notes=updated)

    listing = store.list_for_collection("c")
    assert len(listing) == 1
    assert listing[0]["client_concerns"] == ["New concern"]
    assert listing[0]["decisions"] == ["New decision"]


def test_store_lists_only_matching_collection(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-a", collection_id="alpha", notes=_sample_notes("doc-a"))
    store.save(document_id="doc-b", collection_id="beta", notes=_sample_notes("doc-b"))

    alpha = store.list_for_collection("alpha")
    assert len(alpha) == 1
    assert alpha[0]["document_id"] == "doc-a"


def test_store_has_notes_returns_true_after_save(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    assert store.has_notes("doc-1") is False
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes())
    assert store.has_notes("doc-1") is True


def test_list_action_items_defaults_to_open(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes())

    open_items = store.list_action_items("c")
    assert len(open_items) == 1
    assert open_items[0]["description"] == "Send IPS"
    assert open_items[0]["document_id"] == "doc-1"


def test_list_action_items_status_none_returns_all(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes())

    all_items = store.list_action_items("c", status=None)
    assert {it["status"] for it in all_items} == {"open", "closed"}


def test_list_action_items_filters_by_assignee_substring(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes())

    client_items = store.list_action_items("c", status=None, assignee="CLIENT")
    assert len(client_items) == 1
    assert client_items[0]["assignee"] == "client"


def test_list_action_items_across_multiple_meetings(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes("doc-1"))
    store.save(
        document_id="doc-2",
        collection_id="c",
        notes=MeetingNotes(
            action_items=[ActionItem(description="Schedule call", assignee="advisor")],
            raw_transcript_doc_id="doc-2",
        ),
    )

    open_items = store.list_action_items("c", status="open")
    assert {it["description"] for it in open_items} == {"Send IPS", "Schedule call"}
    assert {it["document_id"] for it in open_items} == {"doc-1", "doc-2"}


# ──────────────────────────────────────────────────────────────────────────
# transcript_text_from_chunks helper
# ──────────────────────────────────────────────────────────────────────────

def test_transcript_text_from_chunks_concatenates_in_order():
    chunks = [
        {"text": "Hello, thanks for joining."},
        {"text": "  "},
        {"text": "We covered the IPS revision."},
    ]
    result = transcript_text_from_chunks(chunks)
    assert result == "Hello, thanks for joining.\n\nWe covered the IPS revision."


def test_transcript_text_from_chunks_handles_empty_input():
    assert transcript_text_from_chunks([]) == ""
    assert transcript_text_from_chunks([{"text": "  "}, {"text": ""}]) == ""


# ──────────────────────────────────────────────────────────────────────────
# End-to-end: extract → save → MCP-shape read
# ──────────────────────────────────────────────────────────────────────────

def test_full_round_trip_extract_save_list(tmp_path):
    fake = _provider_returning(_GOOD_LLM_RESPONSE)
    notes = extract_meeting_notes(
        "Real-feeling transcript text.",
        collection_id="henderson",
        provider=fake,
        document_id="doc-meeting-1",
    )

    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(
        document_id="doc-meeting-1",
        collection_id="henderson",
        notes=notes,
    )

    # MCP get_meeting_notes path
    listing = store.list_for_collection("henderson")
    assert len(listing) == 1
    assert listing[0]["decisions"] == [
        "Rebalance to 60/40 next month",
        "Open Roth conversion ladder",
    ]

    # MCP list_action_items path
    open_items = store.list_action_items("henderson")
    assert len(open_items) == 2
    descriptions = {it["description"] for it in open_items}
    assert "Send revised IPS for signature" in descriptions
    assert "Forward last two 1099s" in descriptions
