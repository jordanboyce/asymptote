"""Unit and integration tests for Anthropic native-citations wiring.

Covers v4.4.4 §"Native Citations API":

* AnthropicProvider injects document blocks onto the first user message.
* AnthropicProvider parses ``char_location`` citations off response text blocks.
* services.chat.context builds documents+metadata from filtered_results
  and routes them through ChatTurn for Anthropic; non-Anthropic providers
  fall back to the prose RETRIEVED CONTEXT block.
* engine.run_agentic enriches provider citations with document_id /
  chunk_id / page_number / filename via the parallel metadata array.

Run with:
    pytest tests/test_native_citations.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any, AsyncIterator, Iterator

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ai_service import AnthropicProvider
from services.chat import context as context_mod
from services.chat import engine as engine_mod
from services.chat.engine import ChatTurn, run_agentic
from tests._fake_ai_provider import FakeAIProvider


def _drain(agen: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    async def _go():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.run(_go())


# -- AnthropicProvider helpers -------------------------------------------

def _bare_provider() -> AnthropicProvider:
    """Construct an AnthropicProvider without hitting the SDK constructor.

    The provider's helper methods don't touch self.client, so we can skip
    __init__ and avoid the API-key import path for unit tests.
    """
    return AnthropicProvider.__new__(AnthropicProvider)


def test_inject_documents_prepends_to_string_user_message():
    p = _bare_provider()
    docs = [
        {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "x"}, "title": "A"},
    ]
    messages = [{"role": "user", "content": "hello"}]
    out = p._inject_documents(messages, docs)

    assert len(out) == 1
    blocks = out[0]["content"]
    assert blocks[0]["type"] == "document"
    assert blocks[-1] == {"type": "text", "text": "hello"}


def test_inject_documents_prepends_to_block_list_user_message():
    p = _bare_provider()
    docs = [{"type": "document", "title": "A"}]
    existing = [{"type": "text", "text": "hi"}]
    messages = [{"role": "user", "content": existing}]
    out = p._inject_documents(messages, docs)

    assert out[0]["content"] == [docs[0], {"type": "text", "text": "hi"}]


def test_inject_documents_no_op_when_empty():
    p = _bare_provider()
    messages = [{"role": "user", "content": "hi"}]
    assert p._inject_documents(messages, []) is messages
    assert p._inject_documents(messages, None) is messages


def test_inject_documents_targets_only_first_user_message():
    p = _bare_provider()
    docs = [{"type": "document", "title": "A"}]
    messages = [
        {"role": "assistant", "content": "earlier turn"},
        {"role": "user", "content": "first user"},
        {"role": "user", "content": "second user"},
    ]
    out = p._inject_documents(messages, docs)
    # Only the first user message gets the documents prepended.
    assert out[0]["content"] == "earlier turn"
    assert out[1]["content"][0]["type"] == "document"
    assert out[2]["content"] == "second user"


def test_parse_citation_block_char_location():
    p = AnthropicProvider
    citation = SimpleNamespace(
        type="char_location",
        document_index=2,
        document_title="report.pdf — p.4",
        cited_text="The portfolio is balanced.",
        start_char_index=120,
        end_char_index=146,
    )
    parsed = p._parse_citation_block(citation)
    assert parsed == {
        "type": "char_location",
        "document_index": 2,
        "document_title": "report.pdf — p.4",
        "cited_text": "The portfolio is balanced.",
        "start_char_index": 120,
        "end_char_index": 146,
    }


def test_parse_citation_block_skips_unsupported_types():
    p = AnthropicProvider
    citation = SimpleNamespace(type="page_location", document_index=0)
    assert p._parse_citation_block(citation) is None


# -- context._build_citation_documents -----------------------------------

class _FakeResult:
    def __init__(self, *, document_id, chunk_id, page_number, filename, text_snippet):
        self.document_id = document_id
        self.chunk_id = chunk_id
        self.page_number = page_number
        self.filename = filename
        self.text_snippet = text_snippet


def test_build_citation_documents_shape_and_metadata():
    results = [
        _FakeResult(document_id="d1", chunk_id="c1", page_number=3,
                    filename="brief.pdf", text_snippet="alpha"),
        _FakeResult(document_id="d2", chunk_id="c2", page_number=7,
                    filename="notes.pdf", text_snippet="beta"),
    ]
    docs, meta = context_mod._build_citation_documents(results)

    assert len(docs) == 2
    assert docs[0]["type"] == "document"
    assert docs[0]["source"] == {"type": "text", "media_type": "text/plain", "data": "alpha"}
    assert docs[0]["citations"] == {"enabled": True}
    assert docs[0]["title"] == "brief.pdf — p.3"

    assert meta == [
        {"document_id": "d1", "chunk_id": "c1", "page_number": 3, "filename": "brief.pdf"},
        {"document_id": "d2", "chunk_id": "c2", "page_number": 7, "filename": "notes.pdf"},
    ]


def test_build_citation_documents_skips_empty_text():
    results = [
        _FakeResult(document_id="d1", chunk_id="c1", page_number=1,
                    filename="x.pdf", text_snippet=""),
        _FakeResult(document_id="d2", chunk_id="c2", page_number=2,
                    filename="y.pdf", text_snippet="real"),
    ]
    docs, meta = context_mod._build_citation_documents(results)
    assert len(docs) == 1 and docs[0]["source"]["data"] == "real"
    assert meta == [{"document_id": "d2", "chunk_id": "c2", "page_number": 2, "filename": "y.pdf"}]


# -- engine.run_agentic enrichment ---------------------------------------

class _CitingFakeProvider(FakeAIProvider):
    """Fake provider that records and replays citation-bearing responses."""

    def __init__(self):
        super().__init__(native_tools=True)
        self.tools_calls_documents: list[Any] = []
        self.stream_calls_documents: list[Any] = []
        self._stream_citations: list[list[dict[str, Any]]] = []

    def queue_stream_chat_with_citation(
        self, chunks: list[str], citations: list[dict[str, Any]],
    ) -> None:
        self.stream_chat_responses.append(list(chunks))
        self._stream_citations.append(list(citations))

    def queue_tools_response(self, *, citations=None, **kwargs):  # type: ignore[override]
        super().queue_tools_response(**kwargs)
        # Attach citations to the just-queued response so the engine can read them.
        self.tools_responses[-1]["citations"] = citations or []

    def complete_with_tools(self, messages, tools, max_tokens, model, system=None, documents=None):
        self.tools_calls_documents.append(documents)
        return super().complete_with_tools(messages, tools, max_tokens, model, system)

    def stream_chat(self, messages, max_tokens, model, system=None, documents=None):
        self.stream_calls_documents.append(documents)
        if not self.stream_chat_responses:
            yield {"done": True, "usage": {"input_tokens": 0, "output_tokens": 0, "model": model}}
            return
        chunks = self.stream_chat_responses.pop(0)
        citations = self._stream_citations.pop(0) if self._stream_citations else []
        for chunk in chunks:
            yield {"delta": chunk}
        for c in citations:
            yield {"citation": c}
        yield {"done": True, "usage": {"input_tokens": 1, "output_tokens": len(chunks), "model": model}}


def _make_anthropic_like(fake: FakeAIProvider) -> FakeAIProvider:
    """Re-class the fake so isinstance(fake, AnthropicProvider) is True.

    The engine gates the documents kwarg on isinstance(provider,
    AnthropicProvider). For tests we splice AnthropicProvider into the MRO
    rather than constructing the real provider (which needs an API key).
    """
    fake.__class__ = type(
        "FakeAnthropic",
        (fake.__class__, AnthropicProvider),
        {},
    )
    return fake


def test_run_agentic_enriches_citation_events_with_metadata():
    fake = _make_anthropic_like(_CitingFakeProvider())
    fake.queue_tools_response(tool_calls=[])  # detection: no tools needed
    fake.queue_stream_chat_with_citation(
        chunks=["Citing now."],
        citations=[{
            "type": "char_location",
            "document_index": 1,
            "document_title": "notes.pdf — p.7",
            "cited_text": "balanced portfolio",
            "start_char_index": 5,
            "end_char_index": 23,
        }],
    )

    documents = [
        {"type": "document", "title": "brief.pdf — p.3"},
        {"type": "document", "title": "notes.pdf — p.7"},
    ]
    document_metadata = [
        {"document_id": "d1", "chunk_id": "c1", "page_number": 3, "filename": "brief.pdf"},
        {"document_id": "d2", "chunk_id": "c2", "page_number": 7, "filename": "notes.pdf"},
    ]

    turn = ChatTurn(
        provider=fake,
        system_text="sys",
        messages=[{"role": "user", "content": "what was said?"}],
        model="fake-fast",
        documents=documents,
        document_metadata=document_metadata,
    )
    events = _drain(run_agentic(turn))

    citations = [e for e in events if e["type"] == "citation"]
    assert len(citations) == 1
    c = citations[0]
    assert c["document_id"] == "d2"
    assert c["chunk_id"] == "c2"
    assert c["page_number"] == 7
    assert c["filename"] == "notes.pdf"
    assert c["cited_text"] == "balanced portfolio"
    assert c["start_char"] == 5 and c["end_char"] == 23

    # The Anthropic-shaped documents made it through to the provider on
    # both the detection turn and the streaming final-answer turn.
    assert fake.tools_calls_documents == [documents]
    assert fake.stream_calls_documents == [documents]


def test_run_agentic_skips_documents_for_non_anthropic_providers():
    """Plain FakeAIProvider is not an AnthropicProvider, so the engine must
    not pass ``documents=`` (which would TypeError on providers that don't
    declare the kwarg)."""
    fake = FakeAIProvider()
    fake.queue_tools_response(tool_calls=[])
    fake.queue_stream_chat_chunks(["plain answer"])

    turn = ChatTurn(
        provider=fake,
        system_text="sys",
        messages=[{"role": "user", "content": "hi"}],
        model="fake-fast",
        # documents/metadata populated, but provider isn't Anthropic.
        documents=[{"type": "document"}],
        document_metadata=[{"document_id": "d1"}],
    )
    events = _drain(run_agentic(turn))
    # No citation events because non-Anthropic providers don't emit them.
    assert not any(e["type"] == "citation" for e in events)
