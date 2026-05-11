"""Snapshot diff helper for ingestion regression tests (v4.1 P0.7).

Each fixture under ``tests/fixtures/ingest/`` has a paired snapshot under
``tests/fixtures/snapshots/`` capturing the deterministic outputs of the
ingestion pipeline:

  * ``column_count`` and ``column_names`` (the result of header detection)
  * ``roles``: ``sql_name → role`` for columns the role inference detected
  * ``role_sources``: ``sql_name → "profile" | "heuristic" | "llm"``
  * ``column_types``: ``sql_name → "real" | "text" | "date" | "integer"``
  * ``vendor_profile``: display name of the matched vendor profile, or ``None``
  * ``aggregate``: a single canonical aggregate (``total_market_value`` for
    holdings files); ``None`` when the metric is not applicable.

The snapshot is *frozen* — drift in header detection, role mapping, or the
aggregate causes a loud ``AssertionError`` so a human has to look at the
diff before accepting it.

Use ``UPDATE_SNAPSHOTS=1 pytest tests/test_ingest.py`` to regenerate the
snapshots after a deliberate change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


SNAPSHOTS = Path(__file__).parent / "fixtures" / "snapshots"


def build_snapshot(
    schema: Dict[str, Any],
    *,
    aggregate_metric: str | None = "total_market_value",
    aggregate_value: Any = None,
    vendor_profile: str | None = None,
) -> Dict[str, Any]:
    """Reduce a schema dict + canonical aggregate to a stable, JSON-friendly
    snapshot. Floats are rounded so trivial pricing-engine drift doesn't fail
    the assertion."""
    columns = schema.get("columns", [])
    roles = {c["sql_name"]: c["role"] for c in columns if c.get("role")}
    role_sources = {
        c["sql_name"]: c["role_source"]
        for c in columns
        if c.get("role") and c.get("role_source")
    }
    column_types = {c["sql_name"]: c["type"] for c in columns}
    column_names = [c["name"] for c in columns]

    rounded_aggregate: Any = None
    if isinstance(aggregate_value, (int, float)):
        # Round to whole dollars — fixtures are anonymized but values are
        # plausible enough that rounding makes assertions stable across
        # tiny numeric coercion changes.
        rounded_aggregate = round(float(aggregate_value))

    return {
        "filename": schema.get("filename"),
        "column_count": schema.get("column_count"),
        "row_count": schema.get("row_count"),
        "column_names": column_names,
        "column_types": column_types,
        "roles": roles,
        "role_sources": role_sources,
        "vendor_profile": vendor_profile,
        "aggregate_metric": aggregate_metric,
        "aggregate_value": rounded_aggregate,
    }


def assert_snapshot(name: str, actual: Dict[str, Any]) -> None:
    """Compare ``actual`` to the on-disk snapshot named ``{name}.json``.

    Behaviour:
      * Missing snapshot file → fail with a clear message instructing the
        user to run with ``UPDATE_SNAPSHOTS=1`` (we don't auto-create on
        first run, that would silently bake in whatever the pipeline does
        today, including bugs).
      * ``UPDATE_SNAPSHOTS=1`` set → write ``actual`` to disk and pass.
      * Otherwise → load expected, diff field-by-field, raise on any
        difference with both expected and actual sides printed.
    """
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS / f"{name}.json"

    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.write_text(
            json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return

    if not path.exists():
        raise AssertionError(
            f"Snapshot {path.name} does not exist. Re-run with "
            f"UPDATE_SNAPSHOTS=1 pytest tests/test_ingest.py to seed it, "
            f"then review the JSON before committing."
        )

    expected = json.loads(path.read_text(encoding="utf-8"))
    if expected != actual:
        diff_lines = _diff_lines(expected, actual)
        raise AssertionError(
            f"Snapshot mismatch for {path.name}:\n"
            + "\n".join(diff_lines)
            + f"\n\nIf the change is intentional, re-run with "
            f"UPDATE_SNAPSHOTS=1 to refresh the snapshot."
        )


def _diff_lines(expected: Dict[str, Any], actual: Dict[str, Any]) -> list[str]:
    """Cheap field-level diff — we only ever compare two flat-ish dicts."""
    out: list[str] = []
    keys = sorted(set(expected) | set(actual))
    for k in keys:
        ev = expected.get(k, "<MISSING>")
        av = actual.get(k, "<MISSING>")
        if ev != av:
            out.append(f"  {k}:")
            out.append(f"    expected: {json.dumps(ev, sort_keys=True)}")
            out.append(f"    actual:   {json.dumps(av, sort_keys=True)}")
    return out
