from types import SimpleNamespace
import json
from unittest.mock import Mock

import pytest

from services.chat_evidence import ChatEvidence


def passage(text="Original", doc="doc-1"):
    return SimpleNamespace(document_id=doc, filename="policy.pdf", page_number=1,
                           text_snippet=text, similarity_score=0.8, sensitivity="internal")


def search(text="Discovered", collection="ops", doc="doc-2"):
    return {"tool": "search_documents", "args": {"collection_id": collection}, "result": {
        "collection_id": collection, "results": [{"document_id": doc, "filename": "policy.pdf",
        "page_number": 1, "excerpt": text, "similarity_score": 0.7, "sensitivity": "internal"}],
    }}


def test_agent_search_extends_initial_citations_without_renumbering():
    evidence = ChatEvidence([(passage(), "ops")])
    discovered = search()
    evidence.observe([discovered])
    assert discovered["result"]["results"][0]["citation"] == "[Source 2]"
    assert evidence.results[0][0].text_snippet == "Original"
    assert evidence.results[1][0].text_snippet == "Discovered"
    assert evidence.results[1][0].sensitivity == "internal"
    # Repeated tool searches keep a stable number; identical filenames in a
    # different collection remain different sources.
    evidence.observe([discovered])
    other = search(collection="parks")
    evidence.observe([other])
    assert len(evidence.results) == 3
    assert other["result"]["results"][0]["citation"] == "[Source 3]"


def test_context_fetch_registers_each_authorized_page():
    evidence = ChatEvidence([])
    result = {"tool": "get_document_context", "result": {
        "collection_id": "ops", "document_id": "doc", "filename": "handbook.pdf",
        "sensitivity": "restricted", "chunks": [
            {"page_number": 2, "text": "A requirement"},
            {"page_number": 3, "text": "An exception"},
        ],
    }}
    evidence.observe([result])
    assert [r.page_number for r, _ in evidence.results] == [2, 3]
    assert result["result"]["chunks"][1]["citation"] == "[Source 2]"
    assert evidence.results[0][0].sensitivity == "restricted"


def test_failed_empty_and_table_results_do_not_become_document_evidence():
    evidence = ChatEvidence([])
    failed = search()
    failed["error"] = "Not authorized"
    empty = search(text="")
    metadata_only = search()
    del metadata_only["result"]["results"][0]["excerpt"]
    evidence.observe([failed, empty, metadata_only, {"tool": "query_table", "result": {"rows": [[1]]}}])
    assert evidence.results == []


def test_duplicate_initial_passages_keep_prompt_numbering():
    evidence = ChatEvidence([(passage(), "ops"), (passage(), "ops")])
    repeat = search(text="Original", doc="doc-1")
    evidence.observe([repeat])
    assert repeat["result"]["results"][0]["citation"] == "[Source 1]"
    assert len(evidence.results) == 2


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_chat_routes_return_agent_evidence_and_honor_reranking(monkeypatch, streaming, native):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import chat

    first, second = passage("First", "a"), passage("Second", "b")
    provider = Mock()
    provider.supports_native_tools.return_value = native
    provider.complete_with_tools.side_effect = [
        {"text": "", "tool_calls": [{"name": "search_documents", "input": {"query": "exceptions"}, "id": "t1"}],
         "assistant_message": {"role": "assistant", "content": "Search"}, "usage": {}},
        {"text": "An exception. [Source 3]", "tool_calls": [], "usage": {}},
    ]
    provider.complete.side_effect = [
        {"text": '<tool_call>{"tool":"search_documents","query":"exceptions"}</tool_call>', "usage": {}},
        {"text": "An exception. [Source 3]", "usage": {}},
    ]
    ai = SimpleNamespace(quality_model="fake", fast_model="fake", rerank_results=Mock(return_value={
        "reranked_indices": [1, 0], "usage": {"input_tokens": 7, "output_tokens": 2, "model": "fake"},
    }))
    monkeypatch.setattr(chat, "create_provider", lambda *a, **k: provider)
    monkeypatch.setattr(chat, "AIService", lambda **k: ai)
    monkeypatch.setattr(chat, "get_indexer", lambda *a: SimpleNamespace(search=lambda **k: {"results": [first, second]}))
    monkeypatch.setattr(chat, "collect_structured_tables", lambda *a: ([], {}))
    monkeypatch.setattr(chat, "_build_collection_overview", lambda *a: "Two documents")
    monkeypatch.setattr(chat.expertise_store, "get_packs_for_collection", lambda *a: [])
    monkeypatch.setattr(chat.collection_service, "get_collection", lambda *a: {"name": "Operations"})
    monkeypatch.setattr(chat, "_cache_context", lambda *a: {"scope_key": "test"})
    cache_write = Mock()
    monkeypatch.setattr(chat, "_cache_store", cache_write)
    monkeypatch.setattr(chat, "check_daily_budget", lambda *a: None)
    monkeypatch.setattr(chat, "record_chat_usage", lambda *a, **k: None)
    monkeypatch.setattr(chat, "execute_tool_calls", lambda *a, **k: [search()])
    app = FastAPI()
    app.include_router(chat.router)
    with TestClient(app) as client:
        response = client.post('/api/chat' + ('/stream' if streaming else ''), json={
            "messages": [{"role": "user", "content": "Any exceptions?"}],
            "provider": "ollama", "rerank": True, "use_cache": False,
        })
    assert response.status_code == 200, response.text
    if streaming:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert not [event for event in events if event["type"] == "error"], events
        body = next(event for event in events if event["type"] == "done")
        assert body["usage"]["input_tokens"] == 7
    else:
        body = response.json()
        assert body["ai_usage"]["total_input_tokens"] == 7
    assert [s["text_snippet"] for s in body["sources"]] == ["Second", "First", "Discovered"]
    ai.rerank_results.assert_called_once()
    cache_write.assert_not_called()  # Agent tool dependencies aren't a single-query cache entry.
    # The final model call actually saw the stable citation, not just the UI.
    final_call = provider.complete_with_tools.call_args if native else provider.complete.call_args
    assert "[Source 3]" in str(final_call)


def test_reranking_keeps_provenance_and_recovers_from_provider_failure():
    from api.chat import _rerank_context

    results = [passage("a"), passage("b")]
    ai = Mock()
    ai.rerank_results.return_value = {"reranked_indices": [1, 1, 55, -1, "0", False, 0]}
    ranked, collections, _ = _rerank_context(ai, "q", results, ["a", "b"], 5)
    assert ranked == [results[1], results[0]]
    assert collections == ["b", "a"]
    ai.rerank_results.side_effect = RuntimeError("Unavailable")
    assert _rerank_context(ai, "q", results, ["a", "b"], 5) == (results, ["a", "b"], None)


def test_document_context_preserves_label_and_rejects_quarantined_source(monkeypatch):
    from services import mcp_server as mcp

    info = {"filename": "policy.pdf", "sensitivity": "restricted", "policy_status": "clean"}
    metadata = SimpleNamespace(
        get_document_info=lambda _: info,
        get_chunks_by_document=lambda _: [{"page_number": 1, "text": "Allowed passage", "chunk_id": "c"}],
    )
    monkeypatch.setattr(mcp, "_resolve_collection_id", lambda _: "ops")
    monkeypatch.setattr(mcp.indexer_manager, "get_indexer", lambda _: SimpleNamespace(vector_store=SimpleNamespace(metadata_store=metadata)))
    monkeypatch.setattr(mcp.collection_service, "get_collection", lambda _: {"sensitivity": "internal"})
    result = mcp.get_document_context("doc", collection_id="ops")
    assert result["sensitivity"] == "restricted"
    info["policy_status"] = "quarantined"
    with pytest.raises(ValueError, match="not found"):
        mcp.get_document_context("doc", collection_id="ops")


def test_document_context_sends_each_passage_once(monkeypatch):
    """A max-size read must fit Claude Code's MCP result limit (~25k tokens)."""
    from services import mcp_server as mcp

    chunks = [{"page_number": i // 4 + 1, "text": "x" * 1000, "chunk_id": f"c{i}", "chunk_index": i}
              for i in range(200)]
    metadata = SimpleNamespace(
        get_document_info=lambda _: {"filename": "book.pdf", "policy_status": "clean"},
        get_chunks_by_document=lambda _: chunks,
    )
    monkeypatch.setattr(mcp, "_resolve_collection_id", lambda _: "ops")
    monkeypatch.setattr(mcp.indexer_manager, "get_indexer", lambda _: SimpleNamespace(vector_store=SimpleNamespace(metadata_store=metadata)))
    monkeypatch.setattr(mcp.collection_service, "get_collection", lambda _: {})
    result = mcp.get_document_context("doc", max_chars=40000, collection_id="ops")
    assert result["truncated"] and "text" not in result
    assert result["total_chars"] == sum(len(c["text"]) for c in result["chunks"]) <= 40000
    assert len(json.dumps(result)) < 45000
