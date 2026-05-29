"""Tests for services.meeting_notes — v4.5 audio → structured notes."""

from __future__ import annotations

import json

import pytest

from services.meeting_notes import (
    ActionItem,
    MeetingNotes,
    MeetingNotesStore,
    build_meeting_context,
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


# ──────────────────────────────────────────────────────────────────────────
# build_meeting_context — the brief-side aggregator
# ──────────────────────────────────────────────────────────────────────────


class _FakeMetadataStore:
    """Minimal stub matching the attributes build_meeting_context reads."""

    def __init__(self, docs):
        self._docs = list(docs)

    def list_documents(self):
        return list(self._docs)


def test_build_meeting_context_empty_when_no_meetings(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    md = _FakeMetadataStore([])

    ctx = build_meeting_context(
        meeting_notes_store=store,
        metadata_store=md,
        collection_id="empty-coll",
    )
    assert ctx == {
        "meetings_count": 0,
        "last_meeting": None,
        "open_action_items": [],
        "total_open_action_items": 0,
        "open_follow_up_questions": [],
    }


def test_build_meeting_context_picks_most_recent_meeting(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    md = _FakeMetadataStore([
        {"document_id": "doc-1", "filename": "Meeting Notes - 2026-04-01.md"},
        {"document_id": "doc-2", "filename": "Meeting Notes - 2026-05-15.md"},
    ])

    # Save in non-chronological order to confirm the helper relies on
    # list_for_collection's DESC ordering, not insertion order.
    store.save(
        document_id="doc-1",
        collection_id="c",
        notes=MeetingNotes(
            client_concerns=["Old concern"],
            decisions=["Old decision"],
            follow_up_questions=["Old follow-up"],
            raw_transcript_doc_id="doc-1",
        ),
    )
    store.save(
        document_id="doc-2",
        collection_id="c",
        notes=MeetingNotes(
            client_concerns=["Fresh worry"],
            decisions=["Fresh decision"],
            follow_up_questions=["Verify with HR"],
            sentiment_notes="Engaged.",
            raw_transcript_doc_id="doc-2",
        ),
    )

    ctx = build_meeting_context(
        meeting_notes_store=store,
        metadata_store=md,
        collection_id="c",
    )
    assert ctx["meetings_count"] == 2
    assert ctx["last_meeting"]["document_id"] == "doc-2"
    assert ctx["last_meeting"]["filename"] == "Meeting Notes - 2026-05-15.md"
    assert ctx["last_meeting"]["client_concerns"] == ["Fresh worry"]
    assert ctx["last_meeting"]["sentiment_notes"] == "Engaged."
    # Follow-ups come from the most recent meeting only.
    assert ctx["open_follow_up_questions"] == ["Verify with HR"]


def test_build_meeting_context_aggregates_open_action_items_with_filename(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    md = _FakeMetadataStore([
        {"document_id": "doc-a", "filename": "Meeting - April.md"},
        {"document_id": "doc-b", "filename": "Meeting - May.md"},
    ])

    store.save(
        document_id="doc-a",
        collection_id="c",
        notes=MeetingNotes(
            action_items=[
                ActionItem(description="Send IPS", assignee="advisor", status="open"),
                ActionItem(description="Closed task", assignee="client", status="closed"),
            ],
            raw_transcript_doc_id="doc-a",
        ),
    )
    store.save(
        document_id="doc-b",
        collection_id="c",
        notes=MeetingNotes(
            action_items=[
                ActionItem(description="Schedule call", assignee="advisor", status="open"),
            ],
            raw_transcript_doc_id="doc-b",
        ),
    )

    ctx = build_meeting_context(
        meeting_notes_store=store,
        metadata_store=md,
        collection_id="c",
    )

    assert ctx["total_open_action_items"] == 2
    by_desc = {it["description"]: it for it in ctx["open_action_items"]}
    assert "Send IPS" in by_desc
    assert "Schedule call" in by_desc
    assert "Closed task" not in by_desc
    # Each item is decorated with the source filename for the brief UI.
    assert by_desc["Send IPS"]["source_filename"] == "Meeting - April.md"
    assert by_desc["Schedule call"]["source_filename"] == "Meeting - May.md"


def test_build_meeting_context_respects_max_action_items_cap(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    md = _FakeMetadataStore([{"document_id": "doc-1", "filename": "m.md"}])
    store.save(
        document_id="doc-1",
        collection_id="c",
        notes=MeetingNotes(
            action_items=[
                ActionItem(description=f"Task {i}", status="open") for i in range(15)
            ],
            raw_transcript_doc_id="doc-1",
        ),
    )

    ctx = build_meeting_context(
        meeting_notes_store=store,
        metadata_store=md,
        collection_id="c",
        max_action_items=5,
    )
    assert len(ctx["open_action_items"]) == 5
    # total reflects every open item, not just the truncated page — UI uses
    # this to render "showing 5 of 15".
    assert ctx["total_open_action_items"] == 15


def test_build_meeting_context_survives_metadata_store_failure(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")

    class BoomMetadataStore:
        def list_documents(self):
            raise RuntimeError("backing store unavailable")

    store.save(
        document_id="doc-1",
        collection_id="c",
        notes=MeetingNotes(
            client_concerns=["A concern"],
            action_items=[ActionItem(description="A task", status="open")],
            raw_transcript_doc_id="doc-1",
        ),
    )

    ctx = build_meeting_context(
        meeting_notes_store=store,
        metadata_store=BoomMetadataStore(),
        collection_id="c",
    )
    # Filename map is empty, but the rest of the brief context is intact.
    assert ctx["meetings_count"] == 1
    assert ctx["last_meeting"]["client_concerns"] == ["A concern"]
    assert ctx["last_meeting"]["filename"] == ""
    assert ctx["open_action_items"][0]["source_filename"] == ""


# ──────────────────────────────────────────────────────────────────────────
# Slice C — standalone action items (chat-originated + manual)
# ──────────────────────────────────────────────────────────────────────────

def test_add_action_item_persists_and_returns_row(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    row = store.add_action_item(
        collection_id="c",
        description="  Call Henderson re: 401k rollover  ",
        assignee="advisor",
        due_date="2026-05-30",
        source_kind="chat",
        source_session_id="s_abc",
        source_message_id="m_42",
        source_excerpt="Per the answer above, we should follow up.",
    )
    # Description trimmed, defaults filled in.
    assert row["description"] == "Call Henderson re: 401k rollover"
    assert row["status"] == "open"
    assert row["source_kind"] == "chat"
    assert row["source_session_id"] == "s_abc"
    assert row["source_message_id"] == "m_42"
    assert isinstance(row["id"], int)

    fetched = store.get_action_item(row["id"])
    assert fetched is not None
    assert fetched["description"] == row["description"]
    assert fetched["created_at"] == row["created_at"]


def test_add_action_item_rejects_empty_description(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    with pytest.raises(ValueError):
        store.add_action_item(collection_id="c", description="   ")


def test_update_action_item_patches_fields_and_bumps_updated_at(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    row = store.add_action_item(collection_id="c", description="Initial")
    updated = store.update_action_item(
        row["id"],
        description="Renamed",
        assignee="client",
        due_date="2026-06-15",
        status="closed",
    )
    assert updated is not None
    assert updated["description"] == "Renamed"
    assert updated["assignee"] == "client"
    assert updated["due_date"] == "2026-06-15"
    assert updated["status"] == "closed"
    # updated_at must move; created_at must not.
    assert updated["updated_at"] >= row["updated_at"]
    assert updated["created_at"] == row["created_at"]


def test_update_action_item_clears_assignee_when_empty_string(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    row = store.add_action_item(
        collection_id="c", description="Task", assignee="advisor"
    )
    updated = store.update_action_item(row["id"], assignee="")
    assert updated is not None
    assert updated["assignee"] is None


def test_update_action_item_rejects_invalid_status(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    row = store.add_action_item(collection_id="c", description="Task")
    with pytest.raises(ValueError):
        store.update_action_item(row["id"], status="archived")


def test_update_action_item_returns_none_for_missing_id(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    assert store.update_action_item(9999, status="closed") is None


def test_delete_action_item_removes_row(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    row = store.add_action_item(collection_id="c", description="Task")
    assert store.delete_action_item(row["id"]) is True
    assert store.get_action_item(row["id"]) is None
    # Idempotent on a re-call.
    assert store.delete_action_item(row["id"]) is False


def test_list_action_items_merges_standalone_with_meeting_items(tmp_path):
    """The unified action-item view must include both transcript items
    (JSON column) and standalone items (action_items table)."""
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes())
    store.add_action_item(
        collection_id="c",
        description="Chat-originated follow-up",
        source_kind="chat",
        source_session_id="s_1",
    )

    open_items = store.list_action_items("c", status="open")
    descriptions = {it["description"] for it in open_items}
    assert descriptions == {"Send IPS", "Chat-originated follow-up"}
    origins = {it["origin"] for it in open_items}
    assert origins == {"meeting", "chat"}

    # Item IDs must distinguish the two origins.
    for item in open_items:
        if item["origin"] == "meeting":
            assert isinstance(item["item_id"], str)
            assert item["item_id"].startswith("meeting:doc-1:")
        else:
            assert isinstance(item["item_id"], int)


def test_list_action_items_standalone_respects_status_filter(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    row = store.add_action_item(collection_id="c", description="Task")
    store.update_action_item(row["id"], status="closed")
    store.add_action_item(collection_id="c", description="Still open")

    open_only = store.list_action_items("c", status="open")
    closed_only = store.list_action_items("c", status="closed")
    assert {it["description"] for it in open_only} == {"Still open"}
    assert {it["description"] for it in closed_only} == {"Task"}


def test_list_action_items_standalone_respects_assignee_filter(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.add_action_item(collection_id="c", description="A", assignee="advisor")
    store.add_action_item(collection_id="c", description="B", assignee="client")

    advisor_items = store.list_action_items("c", status=None, assignee="ADVISOR")
    assert {it["description"] for it in advisor_items} == {"A"}


def test_list_action_items_isolates_collections(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.add_action_item(collection_id="alpha", description="From alpha")
    store.add_action_item(collection_id="beta", description="From beta")
    items_alpha = store.list_action_items("alpha", status=None)
    items_beta = store.list_action_items("beta", status=None)
    assert {it["description"] for it in items_alpha} == {"From alpha"}
    assert {it["description"] for it in items_beta} == {"From beta"}


def test_update_meeting_action_item_status_in_place(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes())
    # Sample notes: index 0 is "Send IPS" (open), index 1 is "Forward 1099" (closed).
    updated = store.update_meeting_action_item_status("doc-1", 0, "closed")
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["description"] == "Send IPS"

    # Sanity: list_action_items now sees the new status.
    closed = store.list_action_items("c", status="closed")
    descriptions = {it["description"] for it in closed}
    assert "Send IPS" in descriptions


def test_update_meeting_action_item_status_rejects_invalid(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes())
    with pytest.raises(ValueError):
        store.update_meeting_action_item_status("doc-1", 0, "archived")


def test_update_meeting_action_item_status_returns_none_for_unknown_doc(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    assert store.update_meeting_action_item_status("missing", 0, "closed") is None


def test_update_meeting_action_item_status_returns_none_for_bad_index(tmp_path):
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes())
    assert store.update_meeting_action_item_status("doc-1", 99, "closed") is None


def test_update_meeting_action_item_mirrors_into_raw_json(tmp_path):
    """raw_json is the audit-trail blob; status edits need to land there too
    so re-reading the row through that field shows the same state."""
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes())
    store.update_meeting_action_item_status("doc-1", 0, "closed")

    import sqlite3
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT raw_json FROM {store.TABLE} WHERE document_id = ?",
            ("doc-1",),
        ).fetchone()
    payload = json.loads(row["raw_json"])
    assert payload["action_items"][0]["status"] == "closed"


def test_build_meeting_context_includes_standalone_action_items(tmp_path):
    """Brief's meeting-context block surfaces standalone items alongside
    transcript-extracted ones — advisor expects to see chat-originated TODOs
    in the same brief view."""
    store = MeetingNotesStore(tmp_path / "metadata.db")
    store.save(document_id="doc-1", collection_id="c", notes=_sample_notes())
    store.add_action_item(
        collection_id="c",
        description="Chat-saved follow-up",
        source_kind="chat",
    )
    ctx = build_meeting_context(
        meeting_notes_store=store,
        metadata_store=_FakeMetadataStore({"doc-1": "Henderson 2026-04-15.mp3"}),
        collection_id="c",
    )
    descriptions = {it["description"] for it in ctx["open_action_items"]}
    assert "Send IPS" in descriptions
    assert "Chat-saved follow-up" in descriptions
    # Standalone item has no source filename (no document_id).
    chat_item = next(
        it for it in ctx["open_action_items"] if it["description"] == "Chat-saved follow-up"
    )
    assert chat_item["source_filename"] == ""
