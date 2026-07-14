"""Unit tests for the retrieval evaluation harness.

The metric math is pure, so these run with no index, no embeddings, and no
network — a fake indexer returns canned results.
"""

import json

import pytest

from services.eval.retrieval_eval import (
    aggregate,
    evaluate_case,
    ids_for_result,
    load_golden_set,
    relevant_ids_for_case,
    run_evaluation,
)


# --------------------------------------------------------------------------- #
# Identifier tagging
# --------------------------------------------------------------------------- #
def test_relevant_ids_union_across_fields():
    case = {
        "query": "q",
        "relevant": {
            "document_ids": ["d1"],
            "filenames": ["Report.PDF"],
            "chunk_ids": ["c9"],
        },
    }
    assert relevant_ids_for_case(case) == {"doc:d1", "file:report.pdf", "chunk:c9"}


def test_ids_for_result_handles_dict_and_object():
    as_dict = {"document_id": "d1", "filename": "A.pdf", "chunk_id": "c1"}
    assert ids_for_result(as_dict) == {"doc:d1", "file:a.pdf", "chunk:c1"}

    class R:
        document_id = "d2"
        filename = "B.PDF"
        chunk_id = None

    assert ids_for_result(R()) == {"doc:d2", "file:b.pdf"}


# --------------------------------------------------------------------------- #
# Pure metrics
# --------------------------------------------------------------------------- #
def test_perfect_hit_at_rank_one():
    retrieved = [{"doc:a"}, {"doc:b"}, {"doc:c"}]
    m = evaluate_case(retrieved, {"doc:a"}, k_values=(1, 3))
    assert m["mrr"] == 1.0
    assert m["recall@1"] == 1.0
    assert m["hit@1"] == 1.0
    assert m["recall@3"] == 1.0


def test_hit_at_rank_three_gives_partial_mrr():
    retrieved = [{"doc:x"}, {"doc:y"}, {"doc:a"}]
    m = evaluate_case(retrieved, {"doc:a"}, k_values=(1, 3))
    assert m["mrr"] == pytest.approx(1 / 3)
    assert m["recall@1"] == 0.0
    assert m["hit@1"] == 0.0
    assert m["recall@3"] == 1.0
    assert m["hit@3"] == 1.0


def test_recall_with_multiple_relevant():
    # Two relevant docs; only one in top 2 -> recall@2 = 0.5
    retrieved = [{"doc:a"}, {"doc:z"}, {"doc:b"}]
    m = evaluate_case(retrieved, {"doc:a", "doc:b"}, k_values=(2, 3))
    assert m["recall@2"] == pytest.approx(0.5)
    assert m["recall@3"] == 1.0
    assert m["precision@2"] == pytest.approx(0.5)


def test_no_hit_scores_zero():
    retrieved = [{"doc:x"}, {"doc:y"}]
    m = evaluate_case(retrieved, {"doc:a"}, k_values=(1, 2))
    assert m["mrr"] == 0.0
    assert m["recall@2"] == 0.0
    assert m["hit@2"] == 0.0


def test_empty_relevant_set_scores_zero_not_crash():
    m = evaluate_case([{"doc:a"}], set(), k_values=(1,))
    assert m["recall@1"] == 0.0
    assert m["hit@1"] == 0.0


def test_aggregate_means_across_cases():
    c1 = evaluate_case([{"doc:a"}], {"doc:a"}, k_values=(1,))  # recall@1 = 1
    c2 = evaluate_case([{"doc:x"}], {"doc:a"}, k_values=(1,))  # recall@1 = 0
    agg = aggregate([c1, c2], k_values=(1,))
    assert agg["recall@1"] == pytest.approx(0.5)
    assert agg["mrr"] == pytest.approx(0.5)


def test_aggregate_empty_is_zeroed():
    agg = aggregate([], k_values=(1, 3))
    assert agg["recall@1"] == 0.0
    assert agg["mrr"] == 0.0


# --------------------------------------------------------------------------- #
# Golden-set loading
# --------------------------------------------------------------------------- #
def test_load_golden_set_accepts_list_and_wrapped(tmp_path):
    cases = [{"query": "q", "relevant": {"filenames": ["a.pdf"]}}]
    p = tmp_path / "g.json"
    p.write_text(json.dumps(cases), encoding="utf-8")
    assert load_golden_set(p) == cases

    p2 = tmp_path / "g2.json"
    p2.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    assert load_golden_set(p2) == cases


def test_load_golden_set_rejects_missing_query(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps([{"relevant": {"filenames": ["a.pdf"]}}]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_golden_set(p)


def test_load_golden_set_rejects_no_relevance(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps([{"query": "q", "relevant": {}}]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_golden_set(p)


def test_example_fixture_is_valid():
    # The shipped example must always parse so docs stay trustworthy.
    cases = load_golden_set("tests/fixtures/golden_set.example.json")
    assert len(cases) >= 1


# --------------------------------------------------------------------------- #
# Live runner against a fake indexer
# --------------------------------------------------------------------------- #
class _FakeIndexer:
    """Minimal stand-in: returns a fixed ranked list keyed by query."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def search(self, query, top_k, mode, filters=None):
        self.calls.append({"query": query, "top_k": top_k, "mode": mode.value, "filters": filters})
        results = self._responses.get(query, [])
        return {"results": results[:top_k]}


def test_run_evaluation_scores_and_passes_filters():
    responses = {
        "find the msa": [
            {"document_id": "d2", "filename": "other.pdf", "chunk_id": "c1"},
            {"document_id": "d1", "filename": "msa_2024.pdf", "chunk_id": "c2"},
        ],
    }
    indexer = _FakeIndexer(responses)
    cases = [
        {
            "query": "find the msa",
            "relevant": {"filenames": ["msa_2024.pdf"]},
            "mode": "keyword",
            "filters": {"source_formats": ["pdf"]},
        }
    ]
    report = run_evaluation(indexer, cases, k_values=(1, 3))

    assert report["num_cases"] == 1
    case = report["cases"][0]
    assert case["recall@3"] == 1.0
    assert case["recall@1"] == 0.0
    assert case["mode"] == "keyword"
    # filters and mode threaded through to the indexer call
    assert indexer.calls[0]["filters"] == {"source_formats": ["pdf"]}
    assert indexer.calls[0]["mode"] == "keyword"
    assert report["aggregate"]["recall@3"] == 1.0
