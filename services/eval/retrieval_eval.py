"""Golden-set retrieval evaluation: recall@k, precision@k, hit-rate, MRR.

Why this exists
---------------
Retrieval quality is the foundation of every grounded answer this tool gives,
yet chunk size, the semantic/keyword blend, and the reranker were all tuned by
feel. This harness turns that into measurement: author a small set of
question → expected-source judgments once, then score any retrieval change
against it. A tweak that helps recall shows up as a number; a regression does
too.

Golden-set format (JSON)
------------------------
A list of cases. Each case names a query and which sources *should* come back.
Relevance can be expressed by document id, filename, or chunk id — whichever is
convenient — and several may be listed (a hit on any one counts).

    [
      {
        "query": "what was the termination clause",
        "relevant": {"filenames": ["msa_2024.pdf"]},
        "mode": "hybrid"                      // optional; overrides default
      },
      {
        "query": "Q3 headcount by region",
        "relevant": {"document_ids": ["doc-abc123"]},
        "filters": {"source_formats": ["csv"]}  // optional; same shape as search
      }
    ]

The metric functions (`evaluate_case`, `aggregate`) are pure — they take ranked
identifier sets, not an index — so they unit-test without any embeddings.

CLI
---
    python -m services.eval.retrieval_eval --collection default \
        --golden tests/fixtures/golden_set.example.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set

DEFAULT_K_VALUES: tuple[int, ...] = (1, 3, 5, 10)


# --------------------------------------------------------------------------- #
# Identifier tagging — lets a result match by document id, filename, OR chunk
# id without the caller having to declare which field they meant.
# --------------------------------------------------------------------------- #
def relevant_ids_for_case(case: Dict[str, Any]) -> Set[str]:
    """Build the tagged set of identifiers considered relevant for a case."""
    rel = case.get("relevant") or {}
    ids: Set[str] = set()
    for did in rel.get("document_ids", []) or []:
        ids.add(f"doc:{did}")
    for fn in rel.get("filenames", []) or []:
        ids.add(f"file:{str(fn).lower()}")
    for cid in rel.get("chunk_ids", []) or []:
        ids.add(f"chunk:{cid}")
    return ids


def ids_for_result(result: Any) -> Set[str]:
    """Build the tagged identifier set for one retrieved result (obj or dict)."""
    def _get(key: str) -> Any:
        if isinstance(result, dict):
            return result.get(key)
        return getattr(result, key, None)

    ids: Set[str] = set()
    did = _get("document_id")
    if did:
        ids.add(f"doc:{did}")
    fn = _get("filename")
    if fn:
        ids.add(f"file:{str(fn).lower()}")
    cid = _get("chunk_id")
    if cid:
        ids.add(f"chunk:{cid}")
    return ids


# --------------------------------------------------------------------------- #
# Pure metric math
# --------------------------------------------------------------------------- #
def evaluate_case(
    retrieved: Sequence[Set[str]],
    relevant: Set[str],
    k_values: Iterable[int] = DEFAULT_K_VALUES,
) -> Dict[str, float]:
    """Score one query.

    Args:
        retrieved: ranked list of tagged-identifier sets, best result first.
        relevant: tagged identifiers considered relevant for this query.
        k_values: cutoffs to report.

    Returns a dict with `mrr`, `num_relevant`, and `recall@k` / `precision@k` /
    `hit@k` for each k. With no relevant ids declared, recall/hit are 0.0 (a
    malformed case scores zero rather than silently passing).
    """
    k_values = sorted(set(int(k) for k in k_values))
    metrics: Dict[str, float] = {"num_relevant": float(len(relevant))}

    # Reciprocal rank: 1 / (rank of first relevant hit).
    rr = 0.0
    for rank, ids in enumerate(retrieved, start=1):
        if ids & relevant:
            rr = 1.0 / rank
            break
    metrics["mrr"] = rr

    for k in k_values:
        topk = retrieved[:k]
        found: Set[str] = set()
        hit_positions = 0
        for ids in topk:
            overlap = ids & relevant
            if overlap:
                found |= overlap
                hit_positions += 1
        metrics[f"recall@{k}"] = (len(found) / len(relevant)) if relevant else 0.0
        metrics[f"precision@{k}"] = (hit_positions / k) if k else 0.0
        metrics[f"hit@{k}"] = 1.0 if found else 0.0

    return metrics


def aggregate(
    per_case: Sequence[Dict[str, float]],
    k_values: Iterable[int] = DEFAULT_K_VALUES,
) -> Dict[str, float]:
    """Mean each metric across cases. Empty input yields zeros."""
    k_values = sorted(set(int(k) for k in k_values))
    keys = ["mrr"] + [
        f"{m}@{k}" for k in k_values for m in ("recall", "precision", "hit")
    ]
    n = len(per_case)
    if n == 0:
        return {k: 0.0 for k in keys}
    return {
        key: sum(float(c.get(key, 0.0)) for c in per_case) / n for key in keys
    }


# --------------------------------------------------------------------------- #
# Golden-set loading + live runner
# --------------------------------------------------------------------------- #
def load_golden_set(path: str | Path) -> List[Dict[str, Any]]:
    """Load and minimally validate a golden-set JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "cases" in data:
        data = data["cases"]
    if not isinstance(data, list):
        raise ValueError("Golden set must be a JSON list of cases (or {'cases': [...]}).")
    for i, case in enumerate(data):
        if not isinstance(case, dict) or not case.get("query"):
            raise ValueError(f"Case {i} is missing a 'query'.")
        if not relevant_ids_for_case(case):
            raise ValueError(
                f"Case {i} ('{case.get('query')}') declares no relevant sources "
                "(need relevant.document_ids / filenames / chunk_ids)."
            )
    return data


def run_evaluation(
    indexer: Any,
    cases: Sequence[Dict[str, Any]],
    k_values: Iterable[int] = DEFAULT_K_VALUES,
    default_mode: str = "hybrid",
) -> Dict[str, Any]:
    """Run every case through a live indexer and score it.

    `indexer` only needs a `.search(query, top_k, mode, filters) -> {"results": [...]}`
    method, so a fake is trivial to substitute in tests.
    """
    from models.schemas import SearchMode  # local import: keep module light

    k_values = sorted(set(int(k) for k in k_values))
    max_k = max(k_values) if k_values else 10

    per_case: List[Dict[str, Any]] = []
    for case in cases:
        mode_str = case.get("mode") or default_mode
        try:
            mode = SearchMode(mode_str)
        except ValueError:
            mode = SearchMode(default_mode)

        search_out = indexer.search(
            query=case["query"],
            top_k=max_k,
            mode=mode,
            filters=case.get("filters"),
        )
        results = search_out.get("results", []) if isinstance(search_out, dict) else search_out
        retrieved = [ids_for_result(r) for r in results]
        relevant = relevant_ids_for_case(case)
        scored = evaluate_case(retrieved, relevant, k_values)
        per_case.append({
            "query": case["query"],
            "mode": mode.value,
            "num_retrieved": len(retrieved),
            **scored,
        })

    return {
        "num_cases": len(cases),
        "k_values": list(k_values),
        "aggregate": aggregate(per_case, k_values),
        "cases": per_case,
    }


def format_report(report: Dict[str, Any]) -> str:
    """Render an evaluation report as a compact text table."""
    agg = report["aggregate"]
    ks = report["k_values"]
    lines = [
        f"Retrieval evaluation — {report['num_cases']} case(s)",
        f"  MRR: {agg['mrr']:.3f}",
        "  " + "  ".join(f"recall@{k}={agg[f'recall@{k}']:.3f}" for k in ks),
        "  " + "  ".join(f"hit@{k}={agg[f'hit@{k}']:.3f}" for k in ks),
        "  " + "  ".join(f"prec@{k}={agg[f'precision@{k}']:.3f}" for k in ks),
    ]
    weak = [c for c in report["cases"] if c.get(f"recall@{max(ks)}", 0.0) == 0.0]
    if weak:
        lines.append(f"  {len(weak)} case(s) with zero recall@{max(ks)}:")
        for c in weak[:10]:
            lines.append(f"    - {c['query']}")
    return "\n".join(lines)


def _main(argv: List[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run retrieval evaluation against a golden set.")
    parser.add_argument("--collection", default="default", help="Collection id to search.")
    parser.add_argument("--golden", required=True, help="Path to golden-set JSON.")
    parser.add_argument("--mode", default="hybrid", choices=["semantic", "keyword", "hybrid"])
    parser.add_argument("--k", default="1,3,5,10", help="Comma-separated k cutoffs.")
    parser.add_argument("--json", action="store_true", help="Emit full JSON report.")
    args = parser.parse_args(argv)

    k_values = tuple(int(x) for x in args.k.split(",") if x.strip())
    cases = load_golden_set(args.golden)

    from services.indexer_manager import indexer_manager

    indexer = indexer_manager.get_indexer(args.collection)
    report = run_evaluation(indexer, cases, k_values=k_values, default_mode=args.mode)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
