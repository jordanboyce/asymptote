"""Redaction of RAG chunks on the way to an external LLM.

This is the half of the redaction boundary the MCP middleware never covered.
Tool results exit through ``mcp_server._redact`` and tabular holdings data is
column-sanitized at ingest, but retrieved chunks were read straight off the
vector store into the chat system prompt. Meeting transcripts are the worst
case: they are almost entirely client names.

The invariant these tests pin is two-sided:

* what travels to the model is redacted, and
* ``filtered_results`` is *not* mutated, because those same objects are
  streamed to the frontend as the sources panel, where the advisor is
  authorized to see the real text.

Getting only the first half right would be a leak; getting only the second
half right would silently blank the advisor's own evidence panel.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from services.chat.context import (
    _build_citation_documents,
    _format_retrieved_context,
)
from services.privacy.redaction_engine import _stable_index, redaction_engine


COLLECTION = "__chat_ctx_test__"

# "Robert Henderson" clears the PERSON threshold (0.85) that the default
# profile raises to keep tickers like GLW/TXN from being scrubbed.
RAW_TEXT = "Robert Henderson called about his account 123456789."
RAW_FILENAME = "Robert Henderson meeting notes.pdf"


@dataclass
class FakeResult:
    """Mirrors the search-result shape ``build_chat_turn`` consumes."""

    filename: str = RAW_FILENAME
    page_number: int = 3
    text_snippet: str = RAW_TEXT
    document_id: str = "doc-1"
    chunk_id: str = "chunk-1"


requires_presidio = pytest.mark.skipif(
    not redaction_engine.available,
    reason="presidio-analyzer / presidio-anonymizer not installed",
)


# ---------------------------------------------------------------------------
# Prose path — every non-Anthropic provider, including the Ollama Cloud default
# ---------------------------------------------------------------------------


@requires_presidio
def test_prose_context_redacts_person_and_account():
    out = _format_retrieved_context([FakeResult()], COLLECTION)

    assert "Robert Henderson" not in out
    assert "123456789" not in out


@requires_presidio
def test_prose_context_redacts_filename():
    """Custodian exports embed client names in the filename itself."""
    out = _format_retrieved_context([FakeResult()], COLLECTION)

    assert "Robert Henderson meeting notes.pdf" not in out


@requires_presidio
def test_prose_context_keeps_source_numbering():
    """Redaction must not disturb the [Source N] tags the prompt cites by."""
    out = _format_retrieved_context([FakeResult(), FakeResult()], COLLECTION)

    assert "[Source 1:" in out
    assert "[Source 2:" in out


@requires_presidio
def test_prose_context_does_not_mutate_results():
    """The sources panel shown to the advisor must keep the real text."""
    results = [FakeResult()]
    _format_retrieved_context(results, COLLECTION)

    assert results[0].text_snippet == RAW_TEXT
    assert results[0].filename == RAW_FILENAME


def test_prose_context_empty_results():
    assert _format_retrieved_context([], COLLECTION) == "No relevant context found."


# ---------------------------------------------------------------------------
# Citation path — Anthropic native `document` blocks
# ---------------------------------------------------------------------------


@requires_presidio
def test_citation_documents_redact_text_and_title():
    documents, metadata = _build_citation_documents([FakeResult()], COLLECTION)

    assert len(documents) == 1
    assert "Robert Henderson" not in documents[0]["source"]["data"]
    assert "123456789" not in documents[0]["source"]["data"]
    assert "Robert Henderson" not in documents[0]["title"]
    # Citations stay enabled — char_location offsets resolve against the
    # block as sent, and deep-linking uses the metadata below, not offsets.
    assert documents[0]["citations"] == {"enabled": True}
    assert metadata[0]["document_id"] == "doc-1"
    assert metadata[0]["chunk_id"] == "chunk-1"


@requires_presidio
def test_citation_documents_do_not_mutate_results():
    results = [FakeResult()]
    _build_citation_documents(results, COLLECTION)

    assert results[0].text_snippet == RAW_TEXT
    assert results[0].filename == RAW_FILENAME


def test_citation_documents_skip_empty_chunks():
    documents, metadata = _build_citation_documents(
        [FakeResult(text_snippet="")], COLLECTION
    )

    assert documents == []
    assert metadata == []


# ---------------------------------------------------------------------------
# Pseudonym stability
# ---------------------------------------------------------------------------


def test_stable_index_is_pure():
    assert _stable_index("Robert Henderson", 14) == _stable_index(
        "Robert Henderson", 14
    )
    assert 0 <= _stable_index("Robert Henderson", 14) < 14


def test_pseudonym_stable_across_processes():
    """The bug this guards: builtin hash() is salted per process.

    ``consistent_pseudonym`` promises an advisor reading two briefs a week
    apart sees the same stand-in name. With ``hash()`` that held only within
    a single run, so a restart silently renamed every client. Two runs under
    different PYTHONHASHSEED values must agree.
    """
    script = textwrap.dedent(
        """
        from services.privacy.redaction_engine import _stable_index
        print(_stable_index("Robert Henderson", 14),
              _stable_index("Robert Henderson_last", 14))
        """
    )
    repo_root = Path(__file__).resolve().parent.parent
    outs = []
    for seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(repo_root),
        )
        assert proc.returncode == 0, proc.stderr
        outs.append(proc.stdout.strip())

    assert outs[0] == outs[1], f"pseudonym index drifted across seeds: {outs}"


# ---------------------------------------------------------------------------
# Audit trail — the CCO's evidence that the boundary ran
# ---------------------------------------------------------------------------


@requires_presidio
def test_chat_context_redactions_are_audit_logged():
    from services.privacy.redaction_log import redaction_log

    _format_retrieved_context([FakeResult()], COLLECTION)

    events = redaction_log.get_recent(collection_id=COLLECTION, limit=50)
    assert events, "chat-context redaction wrote no audit events"
    assert any(e.get("tool_name") == "chat_context" for e in events), (
        "redactions must be attributable to the chat-context boundary"
    )
