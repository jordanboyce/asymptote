"""Tests for services.collection_context — Collection-kind detection +
tool filter + system-prompt addendum.

Built around a lightweight fake indexer that mirrors the real
``vector_store.holdings_store.list_tables()`` shape and exposes
``metadata_db_path`` so the documents-count query against the real SQLite
file works end-to-end. Mirrors the fixture style already used in
test_meeting_notes.py.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

import pytest

from services.agent_tools import anthropic_tools, openai_tools
from services.collection_context import (
    CollectionContext,
    detect_collection_kind,
    filter_tool_specs,
    system_prompt_addendum,
    tools_for_kind,
)


# ──────────────────────────────────────────────────────────────────────────
# Fakes — minimum surface for detect_collection_kind to consume
# ──────────────────────────────────────────────────────────────────────────

class FakeHoldingsStore:
    def __init__(self, tables):
        self._tables = tables

    def list_tables(self):
        return list(self._tables)


@dataclass
class FakeVectorStore:
    holdings_store: Any
    metadata_db_path: Any


@dataclass
class FakeIndexer:
    vector_store: FakeVectorStore


def _seed_documents_table(db_path, documents):
    """Create a minimal documents table the detector can count against.

    Real MetadataStore creates more columns, but the detector only reads
    ``extraction_method`` and the total count — keep the fixture tight.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                num_pages INTEGER DEFAULT 1,
                num_chunks INTEGER DEFAULT 1,
                upload_timestamp TEXT,
                source_format TEXT,
                extraction_method TEXT
            )
        """)
        for d in documents:
            conn.execute(
                "INSERT INTO documents (document_id, filename, upload_timestamp, "
                "source_format, extraction_method) VALUES (?, ?, '2026-05-10', ?, ?)",
                (d["document_id"], d["filename"], d.get("source_format"), d.get("extraction_method")),
            )
        conn.commit()


def _make_indexer(tmp_path, *, tables=None, documents=None):
    """Build a FakeIndexer pointing at a freshly-seeded metadata.db."""
    db_path = tmp_path / "metadata.db"
    _seed_documents_table(db_path, documents or [])
    return FakeIndexer(
        vector_store=FakeVectorStore(
            holdings_store=FakeHoldingsStore(tables or []),
            metadata_db_path=db_path,
        ),
    )


# ──────────────────────────────────────────────────────────────────────────
# detect_collection_kind
# ──────────────────────────────────────────────────────────────────────────

def test_empty_collection_is_general(tmp_path):
    indexer = _make_indexer(tmp_path)
    ctx = detect_collection_kind(indexer, "c1")
    assert ctx.kind == "general"
    assert ctx.financial_table_count == 0
    assert ctx.transcript_count == 0
    assert ctx.document_count == 0


def test_general_when_only_text_docs(tmp_path):
    indexer = _make_indexer(
        tmp_path,
        documents=[
            {"document_id": "d1", "filename": "policy.pdf", "extraction_method": "text"},
            {"document_id": "d2", "filename": "contract.docx", "extraction_method": "text"},
        ],
    )
    ctx = detect_collection_kind(indexer, "c1")
    assert ctx.kind == "general"
    assert ctx.document_count == 2
    assert ctx.financial_table_count == 0
    assert ctx.transcript_count == 0


def test_financial_when_holdings_tables_with_roles(tmp_path):
    indexer = _make_indexer(
        tmp_path,
        tables=[
            {
                "filename": "schwab_positions.csv",
                "financial_roles": {"ticker": "ticker", "market_value": "market_value"},
            },
        ],
        documents=[
            {"document_id": "d1", "filename": "schwab_positions.csv", "extraction_method": "text"},
        ],
    )
    ctx = detect_collection_kind(indexer, "c1")
    assert ctx.kind == "financial"
    assert ctx.financial_table_count == 1


def test_holdings_without_roles_is_not_financial(tmp_path):
    # An ingested CSV that didn't get role-mapped (e.g. an arbitrary export)
    # shouldn't flip the collection into "financial" — that's the whole point
    # of the role-aware detection.
    indexer = _make_indexer(
        tmp_path,
        tables=[
            {"filename": "random.csv", "financial_roles": {}},
        ],
        documents=[
            {"document_id": "d1", "filename": "random.csv", "extraction_method": "text"},
        ],
    )
    ctx = detect_collection_kind(indexer, "c1")
    assert ctx.kind == "general"
    assert ctx.financial_table_count == 0


def test_meetings_when_only_transcripts(tmp_path):
    indexer = _make_indexer(
        tmp_path,
        documents=[
            {"document_id": "t1", "filename": "review.mp3", "extraction_method": "whisper"},
            {"document_id": "t2", "filename": "check-in.wav", "extraction_method": "whisper"},
        ],
    )
    ctx = detect_collection_kind(indexer, "c1")
    assert ctx.kind == "meetings"
    assert ctx.transcript_count == 2
    assert ctx.financial_table_count == 0


def test_mixed_when_holdings_and_transcripts(tmp_path):
    indexer = _make_indexer(
        tmp_path,
        tables=[
            {
                "filename": "pershing.csv",
                "financial_roles": {"ticker": "ticker", "market_value": "market_value"},
            },
        ],
        documents=[
            {"document_id": "d1", "filename": "pershing.csv", "extraction_method": "text"},
            {"document_id": "t1", "filename": "review.mp3", "extraction_method": "whisper"},
        ],
    )
    ctx = detect_collection_kind(indexer, "c1")
    assert ctx.kind == "mixed"
    assert ctx.financial_table_count == 1
    assert ctx.transcript_count == 1


def test_extraction_method_case_insensitive(tmp_path):
    # The trigger check normalises to lowercase; detection should match.
    indexer = _make_indexer(
        tmp_path,
        documents=[
            {"document_id": "t1", "filename": "review.mp3", "extraction_method": "WHISPER"},
        ],
    )
    ctx = detect_collection_kind(indexer, "c1")
    assert ctx.kind == "meetings"


def test_indexer_without_holdings_store_falls_back(tmp_path):
    # Detector must degrade rather than raise when the indexer is malformed.
    @dataclass
    class StoreLessIndexer:
        vector_store: Any
    vs = FakeVectorStore(holdings_store=None, metadata_db_path=tmp_path / "metadata.db")
    _seed_documents_table(vs.metadata_db_path, [])
    indexer = StoreLessIndexer(vector_store=vs)
    # holdings_store=None will raise on .list_tables — must be caught
    ctx = detect_collection_kind(indexer, "c1")
    assert ctx.kind == "general"


def test_indexer_with_missing_documents_table_degrades(tmp_path):
    # Brand-new collection where the metadata.db hasn't been initialised yet
    # — detector should not crash, just return zeros.
    db_path = tmp_path / "metadata.db"
    db_path.touch()  # empty file
    indexer = FakeIndexer(
        vector_store=FakeVectorStore(
            holdings_store=FakeHoldingsStore([]),
            metadata_db_path=db_path,
        ),
    )
    ctx = detect_collection_kind(indexer, "c1")
    assert ctx.kind == "general"
    assert ctx.document_count == 0


# ──────────────────────────────────────────────────────────────────────────
# tools_for_kind
# ──────────────────────────────────────────────────────────────────────────

_BASE_NAMES = {
    "search_documents",
    "get_document_context",
    "list_collections",
    "get_collection_info",
}

_FINANCIAL_NAMES = {
    "list_tables", "get_table_schema", "get_table_rows", "query_table",
    "aggregate_table", "compute_portfolio_metric", "get_price_history",
    "get_security_classification", "get_company_profile", "get_company_news",
}


def test_general_kind_gets_base_tools_only():
    allowed = tools_for_kind("general")
    assert allowed == _BASE_NAMES


def test_meetings_kind_gets_base_tools_only():
    allowed = tools_for_kind("meetings")
    assert allowed == _BASE_NAMES


def test_financial_kind_gets_base_plus_financial_tools():
    allowed = tools_for_kind("financial")
    assert _BASE_NAMES.issubset(allowed)
    assert _FINANCIAL_NAMES.issubset(allowed)


def test_mixed_kind_gets_all_tools():
    allowed = tools_for_kind("mixed")
    assert allowed == tools_for_kind("financial")


def test_unknown_kind_returns_base_safely():
    # Defensive: an unrecognised kind string should at least keep base tools
    # available rather than blowing up.
    allowed = tools_for_kind("nonsense-kind")
    assert allowed == _BASE_NAMES


# ──────────────────────────────────────────────────────────────────────────
# filter_tool_specs — must handle both provider shapes
# ──────────────────────────────────────────────────────────────────────────

def test_filter_anthropic_shape_keeps_only_allowed():
    specs = anthropic_tools()
    filtered = filter_tool_specs(specs, allowed={"search_documents", "list_tables"})
    names = {s["name"] for s in filtered}
    assert names == {"search_documents", "list_tables"}


def test_filter_openai_shape_keeps_only_allowed():
    specs = openai_tools()
    filtered = filter_tool_specs(specs, allowed={"search_documents", "list_tables"})
    names = {s["function"]["name"] for s in filtered}
    assert names == {"search_documents", "list_tables"}


def test_filter_general_kind_strips_financial_tools():
    specs = anthropic_tools()
    allowed = tools_for_kind("general")
    filtered = filter_tool_specs(specs, allowed=allowed)
    names = {s["name"] for s in filtered}
    # No financial tools survive
    assert names.isdisjoint(_FINANCIAL_NAMES)
    # Base tools all present (when they exist in the canonical list)
    available_base = {s["name"] for s in specs if s["name"] in _BASE_NAMES}
    assert names == available_base


def test_filter_financial_kind_keeps_financial_tools():
    specs = anthropic_tools()
    allowed = tools_for_kind("financial")
    filtered = filter_tool_specs(specs, allowed=allowed)
    names = {s["name"] for s in filtered}
    # Every financial tool that's in the canonical list survives
    available_fin = {s["name"] for s in specs if s["name"] in _FINANCIAL_NAMES}
    assert available_fin.issubset(names)


def test_filter_empty_allowed_returns_empty():
    assert filter_tool_specs(anthropic_tools(), allowed=set()) == []


# ──────────────────────────────────────────────────────────────────────────
# system_prompt_addendum
# ──────────────────────────────────────────────────────────────────────────

def test_addendum_financial_mentions_structured_query_tools():
    ctx = CollectionContext(
        collection_id="c1", kind="financial",
        financial_table_count=2, transcript_count=0, document_count=2,
    )
    text = system_prompt_addendum(ctx)
    assert "FINANCIAL" in text
    assert "query_table" in text
    assert "compute_portfolio_metric" in text


def test_addendum_meetings_mentions_search_and_no_brokerage_data():
    ctx = CollectionContext(
        collection_id="c1", kind="meetings",
        financial_table_count=0, transcript_count=3, document_count=3,
    )
    text = system_prompt_addendum(ctx)
    assert "MEETINGS" in text
    assert "search_documents" in text
    assert "no brokerage" in text.lower()


def test_addendum_general_mentions_search_and_no_brokerage_holdings():
    ctx = CollectionContext(
        collection_id="c1", kind="general",
        financial_table_count=0, transcript_count=0, document_count=5,
    )
    text = system_prompt_addendum(ctx)
    assert "GENERAL" in text
    assert "search_documents" in text
    assert "no detected brokerage" in text.lower()


def test_addendum_empty_collection_says_so():
    ctx = CollectionContext(
        collection_id="c1", kind="general",
        financial_table_count=0, transcript_count=0, document_count=0,
    )
    text = system_prompt_addendum(ctx)
    assert "EMPTY" in text or "empty" in text


def test_addendum_mixed_mentions_both_sides():
    ctx = CollectionContext(
        collection_id="c1", kind="mixed",
        financial_table_count=1, transcript_count=2, document_count=3,
    )
    text = system_prompt_addendum(ctx)
    assert "BOTH" in text
    assert "transcript" in text.lower()
    assert "table" in text.lower() or "brokerage" in text.lower()


# ──────────────────────────────────────────────────────────────────────────
# System prompt branching — advisor-POV vs neutral document-analyst
# ──────────────────────────────────────────────────────────────────────────
#
# The chat-with-documents experience (Finn's original use case) must persist
# alongside the financial-advisor pivot. On general / meetings collections,
# the system prompt should NOT pitch the user as an advisor or warn about
# wash-sale rules — it should just help the user reason about their own
# documents.

from services.chat.context import _build_base_framing, _assemble_system_prompt  # noqa: E402


@pytest.mark.parametrize("kind", ["financial", "mixed"])
def test_financial_prompt_keeps_advisor_framing(kind):
    parts = _build_base_framing(is_financial=True, native_citations=False)
    text = " ".join(parts).lower()
    assert "financial advisor" in text
    assert "client" in text
    assert "third person" in text
    assert "advisor" in text


@pytest.mark.parametrize("kind", ["general", "meetings"])
def test_neutral_prompt_drops_advisor_framing(kind):
    parts = _build_base_framing(is_financial=False, native_citations=False)
    text = " ".join(parts).lower()
    # The pivot-era framing must NOT appear on general/meetings collections
    assert "financial advisor" not in text
    assert "the advisor" not in text
    assert "third person" not in text
    assert "portfolio" not in text
    assert "holdings" not in text
    # But the "don't speculate when data is missing" rule still applies —
    # under different prose. Look for the literal "I don't have …" / "I
    # couldn't find …" prefix that drives the refusal behaviour.
    assert "don't have" in text or "couldn't find" in text


def test_financial_assembled_prompt_includes_wash_sale_reminder():
    text = _assemble_system_prompt(
        collection_overview="(no docs)",
        expertise_block="",
        inline_block="",
        context_text="",
        tables_block="",
        kind="financial",
    )
    assert "wash-sale" in text.lower()
    assert "find_tax_loss_candidates" in text


def test_neutral_assembled_prompt_drops_wash_sale_reminder():
    text = _assemble_system_prompt(
        collection_overview="(no docs)",
        expertise_block="",
        inline_block="",
        context_text="",
        tables_block="",
        kind="general",
    )
    assert "wash-sale" not in text.lower()
    assert "find_tax_loss_candidates" not in text
    assert "financial advisor" not in text.lower()


def test_mixed_assembled_prompt_keeps_advisor_framing_and_wash_sale():
    text = _assemble_system_prompt(
        collection_overview="(no docs)",
        expertise_block="",
        inline_block="",
        context_text="",
        tables_block="",
        kind="mixed",
    )
    assert "wash-sale" in text.lower()
    assert "financial advisor" in text.lower()


def test_neutral_doc_filter_message_uses_user_not_advisor():
    text = _assemble_system_prompt(
        collection_overview="(no docs)",
        expertise_block="",
        inline_block="",
        context_text="",
        tables_block="",
        doc_filter_count=3,
        kind="general",
    )
    assert "the user has restricted" in text.lower()
    assert "the advisor has restricted" not in text.lower()


def test_financial_doc_filter_message_uses_advisor():
    text = _assemble_system_prompt(
        collection_overview="(no docs)",
        expertise_block="",
        inline_block="",
        context_text="",
        tables_block="",
        doc_filter_count=3,
        kind="financial",
    )
    assert "the advisor has restricted" in text.lower()


def test_unknown_kind_defaults_to_neutral_framing():
    """Defensive: an unrecognised kind shouldn't accidentally trigger the
    financial framing — neutral is the safe default."""
    text = _assemble_system_prompt(
        collection_overview="(no docs)",
        expertise_block="",
        inline_block="",
        context_text="",
        tables_block="",
        kind="something-new",
    )
    assert "financial advisor" not in text.lower()


# ──────────────────────────────────────────────────────────────────────────
# Search-economy + answer-economy directives
# ──────────────────────────────────────────────────────────────────────────
#
# Regression: a default-collection turn on a technical PDF set ran 5 searches
# and emitted ~6500 output tokens to answer a one-line definition question.
# The agentic loop
# defaults to fanning out searches and writing long-form essays unless the
# system prompt says otherwise. Pin the two directives that throttle this
# so future prompt edits can't silently bring the verbosity back.

@pytest.mark.parametrize("is_financial", [True, False])
def test_base_framing_tells_model_to_stop_searching_after_a_good_hit(is_financial):
    parts = _build_base_framing(
        is_financial=is_financial, native_citations=False,
    )
    text = " ".join(parts).lower()
    # Look for both the "few searches" and "stop when grounded" halves so the
    # rule can't degrade into "search a lot, but stop eventually".
    assert "one or two" in text and "searches" in text, (
        "Search-economy directive missing — model will fan out searches"
    )
    assert "stop searching" in text, (
        "Stop-searching directive missing — model won't terminate the loop"
    )


@pytest.mark.parametrize("is_financial", [True, False])
def test_base_framing_tells_model_to_be_concise(is_financial):
    parts = _build_base_framing(
        is_financial=is_financial, native_citations=False,
    )
    text = " ".join(parts).lower()
    # Pin the intent, not the wording: the directive has been phrased as
    # "answer concisely" and (since the front-loaded rewrite) as "front-load
    # the takeaway ... no preamble". Either satisfies the guard; what matters
    # is that *some* brevity directive survives future prompt edits.
    assert "front-load" in text or "concise" in text, (
        "Answer-economy directive missing — output will balloon"
    )
    assert "no preamble" in text, (
        "No-preamble directive missing — model will restate the question"
    )
    # Length-fits-the-question is the key qualifier that keeps brevity from
    # meaning "always one sentence". Pin it so future edits don't drop it and
    # accidentally muzzle long-form answers when they're warranted.
    assert "fit the question" in text


def test_neutral_addendum_avoids_financial_advisor_framing():
    """The general-kind addendum used to suggest 'upload a brokerage export'
    on empty collections — wrong default when the user is a researcher who
    just installed the app. Should stay persona-neutral."""
    empty_ctx = CollectionContext(
        collection_id="c1", kind="general",
        financial_table_count=0, transcript_count=0, document_count=0,
    )
    text = system_prompt_addendum(empty_ctx)
    assert "brokerage" not in text.lower()
    assert "advisor" not in text.lower()
    assert "wash-sale" not in text.lower()
