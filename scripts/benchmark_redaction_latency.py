#!/usr/bin/env python
"""Measure what the chat-context redaction boundary costs per turn.

Presidio now runs over every retrieved chunk on every chat turn (see
``services/chat/context.py::_redact_for_llm``). Before that landed, only the
MCP ``search_collection`` path paid this. The cost was never measured, so if
turns feel slow this is the first suspect — and "first suspect" is a bad place
for a number nobody has.

What this measures is the redaction pass alone: engine warm-up, then steady-
state per-chunk latency over transcript-shaped prose (the worst case — meeting
transcripts are dense with names), and the per-turn total at the configured
``default_top_k``. It deliberately does *not* measure retrieval, embedding, or
the model call; those dominate a real turn and would hide the thing being
looked at.

    python scripts/benchmark_redaction_latency.py
    python scripts/benchmark_redaction_latency.py --runs 20 --top-k 10

Exits non-zero if the per-turn median exceeds ``--budget-ms``, so this can be
wired into CI later if the number ever starts mattering.
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


# Transcript-shaped prose: names, an account number, a dollar figure, a date.
# Roughly `settings.chunk_size` characters, which is what retrieval actually
# hands the prompt builder.
_TRANSCRIPT = """
Robert Henderson opened by saying he and Margaret had talked it over across the
weekend and wanted to move ahead with the Roth conversion before year end. I
walked him through the bracket math again. Margaret Henderson asked whether the
conversion would affect their Medicare premiums two years out, which it will,
and we agreed to model both the partial and the full conversion before the next
meeting. Robert mentioned that his brother, Daniel Henderson, had used a
different advisor at Pershing and had a bad experience with a similar
conversion, so he wants the numbers in writing. Their joint account, X12345678,
currently holds about $1.24 million, and the taxable account at 4471-90228 has
the concentrated Glassworks position we have been trying to unwind since March.
Margaret asked me to email a summary to margaret.henderson@example.com rather
than the shared address. We scheduled the follow-up for the 14th at two o'clock
at their home in Fairfield. Action items: I send the bracket model by Friday,
Robert sends over the 2025 return from his accountant Patricia Nakamura, and we
revisit the Glassworks concentration once the conversion path is settled.
""".strip()


def _sample(n: int) -> str:
    """A chunk of roughly the configured chunk size, built from the transcript."""
    from config import settings

    target = int(getattr(settings, "chunk_size", 1000))
    body = _TRANSCRIPT
    while len(body) < target:
        body += "\n\n" + _TRANSCRIPT
    # Vary the text per chunk so the engine's per-call caches don't flatter it.
    return f"[chunk {n}] " + body[:target]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=20, help="steady-state samples")
    parser.add_argument(
        "--top-k", type=int, default=None,
        help="chunks per turn (default: settings.default_top_k)",
    )
    parser.add_argument(
        "--budget-ms", type=float, default=1500.0,
        help="fail if the per-turn median exceeds this",
    )
    args = parser.parse_args()

    from config import settings
    from services.privacy.redaction_engine import redaction_engine

    top_k = args.top_k or int(getattr(settings, "default_top_k", 10))

    if not getattr(settings, "enable_pii_redaction", False):
        print("enable_pii_redaction is off — turning it on for this measurement.")
        settings.enable_pii_redaction = True

    if not redaction_engine.available:
        print("FAIL: the redaction engine is not available; nothing to measure.")
        return 2

    from services.privacy.redaction_middleware import redact_text_for_ai

    # Redaction writes to the audit log, and the audit log is a compliance
    # record — a benchmark must not leave 300 synthetic client names in it for
    # the Boundary Report to render at a prospect. Point the singleton at a
    # throwaway file for the duration. The write still happens, so the cost
    # being measured stays honest.
    import services.privacy.redaction_log as rl_mod
    from services.privacy.redaction_log import RedactionLog

    scratch = tempfile.mkdtemp(prefix="finn-redaction-bench-")
    real_log = rl_mod.redaction_log
    rl_mod.redaction_log = RedactionLog(db_path=Path(scratch) / "redaction_log.db")

    try:
        return _measure(args, top_k, redact_text_for_ai)
    finally:
        rl_mod.redaction_log = real_log
        shutil.rmtree(scratch, ignore_errors=True)


def _measure(args, top_k: int, redact_text_for_ai) -> int:
    import statistics

    # Warm-up is reported separately, not averaged in. Presidio loads a spaCy
    # model on first use; folding that into the mean would make a one-time cost
    # look like a per-turn cost and overstate the boundary by an order of
    # magnitude.
    t0 = time.perf_counter()
    redact_text_for_ai(_sample(0), collection_id=None, source_label="benchmark")
    warmup_ms = (time.perf_counter() - t0) * 1000

    per_chunk: list[float] = []
    for i in range(1, args.runs + 1):
        text = _sample(i)
        t0 = time.perf_counter()
        redact_text_for_ai(text, collection_id=None, source_label="benchmark")
        per_chunk.append((time.perf_counter() - t0) * 1000)

    median = statistics.median(per_chunk)
    p95 = _percentile(per_chunk, 95)
    turn_median = median * top_k
    chars = len(_sample(1))

    print()
    print("Chat-context redaction — steady-state cost")
    print("-" * 52)
    print(f"  chunk size            {chars:,} chars (settings.chunk_size)")
    print(f"  samples               {args.runs}")
    print(f"  engine warm-up        {warmup_ms:,.0f} ms   (once per process)")
    print()
    print(f"  per chunk   median    {median:,.1f} ms")
    print(f"              p95       {p95:,.1f} ms")
    print(f"              max       {max(per_chunk):,.1f} ms")
    print()
    print(f"  per turn    top_k={top_k:<3}      {turn_median:,.0f} ms median")
    print(f"              p95 chunks     {p95 * top_k:,.0f} ms")
    print("-" * 52)
    print(
        "  Retrieval, embedding, and the model call are excluded — this is\n"
        "  the redaction pass only. Compare against the model's time to first\n"
        "  token before deciding whether it is worth optimizing."
    )
    print()

    if turn_median > args.budget_ms:
        print(
            f"OVER BUDGET: {turn_median:,.0f} ms per turn exceeds "
            f"{args.budget_ms:,.0f} ms."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
