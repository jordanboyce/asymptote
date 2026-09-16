"""Research quality mechanics, scope enforcement and transport integration.

Deterministic fixtures exercise ranking, not a claim about a model's quality.
"""
import asyncio
import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.research_search import research


def hit(chunk, doc=None, text=None, score=0.5):
    return {"chunk_id": chunk, "document_id": doc or chunk, "collection_id": "ops",
            "filename": f"{doc or chunk}.md", "page_number": 1,
            "excerpt": text or f"Evidence from {chunk}", "similarity_score": score,
            "sensitivity": "internal"}


def test_rank_fusion_rewards_agreement_without_comparing_raw_scores():
    def search(query, mode):
        return [hit("outlier", score=100), hit("shared", score=0.3)] if mode == "hybrid" else [hit("shared", score=0.001)]
    result = research("return rules", [], search, lambda _: [])
    assert result["results"][0]["chunk_id"] == "shared"
    assert result["results"][0]["retrieval_modes"] == ["hybrid", "keyword"]
    assert result["ranking"] == "reciprocal_rank_fusion"


def test_queries_are_deduplicated_but_negation_and_identifiers_survive():
    calls, literals = [], []
    def search(q, mode):
        calls.append((q, mode))
        return [hit("x")]
    result = research('Does EQ-17 permit "late returns"?', ['exceptions', 'Exceptions', 'does not permit'],
                      search, lambda p: literals.append(p) or [])
    assert result["queries"] == ['Does EQ-17 permit "late returns"?', 'exceptions', 'does not permit']
    assert literals == ["late returns", "EQ-17"]
    assert len(calls) == 4
    assert all(c["status"] == "retrieved" for c in result["coverage"])


def test_duplicate_hits_in_one_branch_do_not_boost_ranking():
    result = research("rules", [], lambda *a: [hit("a"), hit("a"), hit("b")], lambda _: [])
    assert result["candidate_count"] == 2
    assert result["results"][0]["fusion_score"] == round(2 / 61, 8)


def test_document_diversity_excerpt_budget_and_stable_ids():
    hits = [hit(f"c{i}", doc="long-policy" if i < 5 else f"doc{i}", text="x" * 1800) for i in range(8)]
    result = research("rules", [], lambda *a: hits, lambda _: [], max_context_chars=4000, max_per_document=1)
    assert max(Counter(r["document_id"] for r in result["results"]).values()) == 1
    assert sum(len(r["excerpt"]) for r in result["results"]) == 4000
    assert result["results"][-1]["excerpt_truncated"]
    again = research("rules", [], lambda *a: hits, lambda _: [], top_k=1)
    assert result["results"][0]["evidence_id"] == again["results"][0]["evidence_id"]
    assert result["suggested_next"][0]["arguments"]["chunk_id"] == "c0"


def test_coverage_distinguishes_missing_evidence_and_failed_searches():
    def search(query, mode):
        if query == "exceptions":
            raise RuntimeError("SECRET connection info")
        return []
    result = research("rules", ["exceptions"], search, lambda _: [])
    assert [c["status"] for c in result["coverage"]] == ["no_selected_evidence", "search_failed"]
    assert result["partial_failure"]
    assert "SECRET" not in str(result)
    assert result["results"] == []


@pytest.mark.parametrize("kwargs", [
    {"query": ""}, {"subqueries": ["a", "b", "c", "d"]},
    {"subqueries": "not a list"}, {"query": "x" * 1001},
    {"top_k": 50}, {"top_k": True}, {"max_context_chars": 50}, {"max_per_document": 0},
])
def test_invalid_budgets_are_rejected_before_retrieval(kwargs):
    search = Mock()
    with pytest.raises(ValueError):
        research(search=search, find=Mock(), **({"query": "rules", "subqueries": []} | kwargs))
    search.assert_not_called()


@pytest.fixture()
def mcp_env(monkeypatch):
    from services import mcp_server as mcp
    rows = [("outside", "doc-b", "b.md", 1, "EQ-17 excluded"),
            ("hidden", "doc-hidden", "hidden.md", 1, "EQ-17 hidden"),
            ("allowed", "doc-a", "a.md", 2, "EQ-17 allowed exception")]
    metadata = SimpleNamespace(
        get_hidden_document_ids=lambda: {"doc-hidden"},
        get_filtered_chunk_ids=Mock(return_value={"allowed", "hidden"}),
        iter_chunk_texts=lambda **k: iter(rows),
        get_document_info=lambda _: {"sensitivity": "confidential"},
    )
    result = SimpleNamespace(filename="a.md", document_id="doc-a", chunk_id="allowed", page_number=2,
                             text_snippet="EQ-17 allowed exception", similarity_score=0.7,
                             source_format="md", source_type="upload", source_path=None, sensitivity="confidential")
    indexer = SimpleNamespace(vector_store=SimpleNamespace(metadata_store=metadata), search=Mock(return_value={"results": [result]}))
    monkeypatch.setattr(mcp, "_resolve_collection_id", lambda _: "ops")
    monkeypatch.setattr(mcp, "get_request_mcp_profile", lambda: {"include_sources": True})
    monkeypatch.setattr(mcp.indexer_manager, "get_indexer", lambda _: indexer)
    monkeypatch.setattr(mcp.collection_service, "get_collection", lambda _: {"name": "Operations", "sensitivity": "internal"})
    return mcp, indexer, metadata


def test_research_filters_reach_every_branch_and_exact_match_limit(mcp_env):
    mcp, indexer, metadata = mcp_env
    filters = {"document_ids": ["doc-a"]}
    result = mcp.research_documents_sync("EQ-17", "ops", subqueries=["exceptions"], filters=filters)
    assert len(indexer.search.call_args_list) == 3
    assert all(c.kwargs["filters"] == filters for c in indexer.search.call_args_list)
    metadata.get_filtered_chunk_ids.assert_called_with(**filters)
    assert [r["document_id"] for r in result["results"]] == ["doc-a"]
    assert "literal" in result["results"][0]["retrieval_modes"]
    exact = mcp.find_in_documents_sync("EQ-17", collection_id="ops", max_results=1, filters=filters)
    assert exact["matches"][0]["document_id"] == "doc-a"
    assert exact["matches"][0]["sensitivity"] == "confidential"
    assert "doc-hidden" not in str(exact)


def test_empty_filters_result_and_denied_scope_do_not_search(mcp_env, monkeypatch):
    mcp, indexer, metadata = mcp_env
    metadata.get_filtered_chunk_ids.return_value = set()
    assert mcp.find_in_documents_sync("EQ-17", filters={"document_ids": ["missing"]})["matches"] == []
    def denied(_):
        raise ValueError("Collection not found")
    monkeypatch.setattr(mcp, "_resolve_collection_id", denied)
    with pytest.raises(ValueError, match="not found"):
        mcp.research_documents_sync("EQ-17", "private")
    indexer.search.assert_not_called()


def test_mcp_registration_and_async_tool_return_structured_results(mcp_env):
    mcp, _, _ = mcp_env
    registered = asyncio.run(mcp._asymptote_mcp.list_tools())
    tool = next(t for t in registered if t.name == "research_documents")
    assert tool.annotations.readOnlyHint is True
    assert tool.inputSchema["required"] == ["query"]
    assert "subqueries" in tool.inputSchema["properties"]
    result = asyncio.run(mcp.research_documents("EQ-17", filters={"document_ids": ["doc-a"]}))
    assert result["total_results"] == 1
    assert result["results"][0]["chunk_id"] == "allowed"
    content, structured = asyncio.run(mcp._asymptote_mcp.call_tool(
        "research_documents", {"query": "EQ-17", "filters": {"document_ids": ["doc-a"]}}))
    assert structured["results"][0]["chunk_id"] == "allowed"
    assert json.loads(content[0].text) == structured


def test_standard_search_table_inlining_honors_document_selection(mcp_env, monkeypatch):
    mcp, _, _ = mcp_env
    collect = Mock(return_value=([], {}))
    monkeypatch.setattr(mcp, "collect_structured_tables", collect)
    monkeypatch.setattr(mcp.indexer_manager, "get_collection_stats", lambda _: {})
    mcp.search_collection_sync("rules", filters={"document_ids": ["doc-a"]})
    collect.assert_called_once_with(["ops"], ["doc-a"])
    collect.reset_mock()
    mcp.search_collection_sync("rules", filters={"source_formats": ["pdf"]})
    collect.assert_not_called()


@pytest.mark.parametrize("tool,fields", [
    ("research_documents", {"query": "exceptions"}),
    ("find_in_documents", {"pattern": "EQ-17"}),
])
def test_chat_tools_cannot_expand_selected_sources(mcp_env, monkeypatch, tool, fields):
    from services.structured_chat import execute_tool_calls
    from services.agent_tools import tool_names, openai_tools, anthropic_tools
    from services.chat_evidence import ChatEvidence
    mcp, _, _ = mcp_env
    context = {"collection_id": "ops", "document_ids": ["doc-a"]}
    result = execute_tool_calls([{"tool": tool, **fields}], context)
    assert "error" not in result[0], result
    assert result[0]["args"]["filters"] == {"document_ids": ["doc-a"]}
    evidence = ChatEvidence([])
    evidence.observe(result)
    assert evidence.results[0][0].document_id == "doc-a"
    assert all(t in tool_names() for t in ["research_documents", "find_in_documents"])
    assert len(openai_tools()) == len(anthropic_tools())
    denied = execute_tool_calls([{"tool": tool, **fields, "collection_id": "other"}], context)
    assert "cannot switch collections" in denied[0]["error"]
    denied_ids = execute_tool_calls([{"tool": tool, **fields, "filters": {"document_ids": ["doc-b"]}}], context)
    assert "outside the sources" in denied_ids[0]["error"]
