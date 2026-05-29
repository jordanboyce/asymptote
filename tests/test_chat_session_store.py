"""Tests for services.chat.session_store — Slice B persistence layer."""

from __future__ import annotations

import sqlite3
import time

import pytest

from services.chat.session_store import (
    DEFAULT_MAX_MESSAGES_PER_SESSION,
    ChatSessionStore,
)


# ──────────────────────────────────────────────────────────────────────────
# Session CRUD
# ──────────────────────────────────────────────────────────────────────────


def test_create_session_returns_row_with_defaults(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    session = store.create_session(collection_id="c1")

    assert session["collection_id"] == "c1"
    assert session["title"] == "New chat"
    assert session["archived"] is False
    assert session["created_at"] == session["updated_at"]
    assert session["id"].startswith("s_")


def test_create_session_honours_supplied_id(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    session = store.create_session(collection_id="c1", session_id="s_pre_minted")
    assert session["id"] == "s_pre_minted"
    fetched = store.get_session("s_pre_minted")
    assert fetched is not None
    assert fetched["id"] == "s_pre_minted"


def test_create_session_normalises_blank_title(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    session = store.create_session(collection_id="c1", title="   ")
    assert session["title"] == "New chat"


def test_create_session_idempotent_on_existing_id(tmp_path):
    """Frontend's 'ensure session on server' path fires on every mutation;
    re-calling create_session with an existing id should return the stored
    row rather than raising. Also must not overwrite the existing title."""
    store = ChatSessionStore(tmp_path / "metadata.db")
    first = store.create_session(
        collection_id="c1", session_id="s_fixed", title="kept",
    )
    again = store.create_session(
        collection_id="c1", session_id="s_fixed", title="ignored",
    )
    assert again["id"] == first["id"]
    assert again["title"] == "kept"
    assert again["created_at"] == first["created_at"]


def test_list_sessions_orders_by_updated_at_desc(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    s1 = store.create_session(collection_id="c1", title="first")
    # Tiny sleep so the second insert's CURRENT_TIMESTAMP is strictly later;
    # without this the ORDER BY ties and the test becomes flaky.
    time.sleep(0.01)
    s2 = store.create_session(collection_id="c1", title="second")

    sessions = store.list_sessions("c1")
    assert [s["id"] for s in sessions] == [s2["id"], s1["id"]]


def test_list_sessions_excludes_other_collections(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    store.create_session(collection_id="alpha")
    store.create_session(collection_id="beta")

    alpha = store.list_sessions("alpha")
    assert len(alpha) == 1
    assert alpha[0]["collection_id"] == "alpha"


def test_rename_session_updates_title_and_timestamp(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    s = store.create_session(collection_id="c1", title="original")
    original_updated = s["updated_at"]
    time.sleep(0.01)

    assert store.rename_session(s["id"], "Revised title") is True
    refetched = store.get_session(s["id"])
    assert refetched["title"] == "Revised title"
    assert refetched["updated_at"] > original_updated


def test_rename_long_title_truncates_to_ellipsis(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    s = store.create_session(collection_id="c1")
    very_long = "a" * 200
    store.rename_session(s["id"], very_long)
    title = store.get_session(s["id"])["title"]
    assert len(title) <= 60
    assert title.endswith("…")


def test_rename_session_returns_false_for_unknown(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    assert store.rename_session("does-not-exist", "x") is False


def test_delete_session_cascades_to_messages(tmp_path):
    """ON DELETE CASCADE via foreign_keys=ON is enforced per connection."""
    store = ChatSessionStore(tmp_path / "metadata.db")
    s = store.create_session(collection_id="c1")
    store.add_message(session_id=s["id"], role="user", content="hi")
    store.add_message(session_id=s["id"], role="assistant", content="hello")

    assert store.delete_session(s["id"]) is True
    assert store.get_session(s["id"]) is None
    assert store.list_messages(s["id"]) == []

    # Re-create the same id — must not resurrect the orphan messages.
    store.create_session(collection_id="c1", session_id=s["id"])
    assert store.list_messages(s["id"]) == []


# ──────────────────────────────────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────────────────────────────────


def test_add_message_round_trips_metadata(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    s = store.create_session(collection_id="c1")
    metadata = {
        "sources": [{"document_id": "d1", "page_number": 3}],
        "aiUsage": {"input_tokens": 1200, "output_tokens": 240},
        "structuredResults": [{"tool": "search_documents", "args": {"query": "q"}}],
    }
    msg = store.add_message(
        session_id=s["id"],
        role="assistant",
        content="Here is what I found.",
        metadata=metadata,
    )
    assert msg["metadata"] == metadata

    fetched = store.list_messages(s["id"])
    assert len(fetched) == 1
    assert fetched[0]["metadata"] == metadata


def test_add_message_rejects_unknown_session(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    with pytest.raises(ValueError, match="Unknown session_id"):
        store.add_message(session_id="missing", role="user", content="hi")


def test_first_user_message_auto_titles_session(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    s = store.create_session(collection_id="c1")
    assert s["title"] == "New chat"

    store.add_message(
        session_id=s["id"],
        role="user",
        content="What's the largest position in the trust account?",
    )
    refetched = store.get_session(s["id"])
    assert refetched["title"] == "What's the largest position in the trust account?"


def test_auto_title_does_not_overwrite_manual_rename(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    s = store.create_session(collection_id="c1")
    store.rename_session(s["id"], "TLH review")

    store.add_message(
        session_id=s["id"],
        role="user",
        content="anything tax-loss-harvestable above $500?",
    )
    assert store.get_session(s["id"])["title"] == "TLH review"


def test_messages_returned_in_insertion_order(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    s = store.create_session(collection_id="c1")
    for i in range(5):
        store.add_message(session_id=s["id"], role="user", content=f"msg-{i}")

    msgs = store.list_messages(s["id"])
    assert [m["content"] for m in msgs] == [f"msg-{i}" for i in range(5)]


def test_messages_pruned_when_session_exceeds_cap(tmp_path):
    """Beyond the cap, the oldest rows roll off — newest survive."""
    store = ChatSessionStore(tmp_path / "metadata.db")
    s = store.create_session(collection_id="c1")
    cap = 5
    for i in range(cap + 3):  # 3 over the cap
        store.add_message(
            session_id=s["id"],
            role="user",
            content=f"msg-{i}",
            max_messages_per_session=cap,
        )

    msgs = store.list_messages(s["id"])
    assert len(msgs) == cap
    # Oldest 3 should be gone (msg-0, msg-1, msg-2).
    assert [m["content"] for m in msgs] == [f"msg-{i}" for i in range(3, cap + 3)]


def test_default_cap_constant_matches_frontend(tmp_path):
    # Acts as a guardrail against the server- and browser-side caps drifting
    # apart. If you change MAX_MESSAGES_PER_SESSION on either side, update
    # the other and adjust this constant.
    assert DEFAULT_MAX_MESSAGES_PER_SESSION == 100


# ──────────────────────────────────────────────────────────────────────────
# Foreign-key + isolation
# ──────────────────────────────────────────────────────────────────────────


def test_messages_do_not_leak_across_sessions(tmp_path):
    store = ChatSessionStore(tmp_path / "metadata.db")
    s1 = store.create_session(collection_id="c1")
    s2 = store.create_session(collection_id="c1")
    store.add_message(session_id=s1["id"], role="user", content="from s1")
    store.add_message(session_id=s2["id"], role="user", content="from s2")

    assert [m["content"] for m in store.list_messages(s1["id"])] == ["from s1"]
    assert [m["content"] for m in store.list_messages(s2["id"])] == ["from s2"]


def test_store_shares_db_file_with_other_per_collection_stores(tmp_path):
    """The whole reason this lives at metadata.db is co-located storage —
    open the same file from a second connection and confirm both tables
    exist. Mirrors how MeetingNotesStore and HoldingsStore coexist."""
    db_path = tmp_path / "metadata.db"
    ChatSessionStore(db_path)

    with sqlite3.connect(db_path) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "chat_sessions" in names
    assert "chat_messages" in names
