"""Retrieval evaluation harness.

A small, dependency-light toolkit for measuring retrieval quality against a
hand-authored golden set. The metric math (`evaluate_case`, `aggregate`) is pure
and unit-testable without building an index; `run_evaluation` wires it to a live
indexer. See `services/eval/retrieval_eval.py`.
"""

from .retrieval_eval import (
    evaluate_case,
    aggregate,
    run_evaluation,
    load_golden_set,
)

__all__ = [
    "evaluate_case",
    "aggregate",
    "run_evaluation",
    "load_golden_set",
]
