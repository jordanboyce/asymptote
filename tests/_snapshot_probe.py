"""One-shot script to emit exact snapshot values for all 5 fixtures.

Run as: python tests/_snapshot_probe.py
(Not a pytest test — just a probe to establish ground truth.)
"""
from __future__ import annotations
import sys, tempfile, sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"

# ---- minimal ingest (mirrors _ingest_csv in test_ingest.py) ---------------
import importlib, csv as _csv, re as _re, io as _io
import pandas as _pd
from services.ingest_profiles import detect_profile, apply_profile
from services.structured_store import StructuredStore

_NUM_RE = _re.compile(r'^[\s$€£¥₹(]?-?[\d,]+\.?\d*\s*[%KMBkmb]?\s*[)%]?$')


def _extract(csv_path: Path):
    try:
        from services.ingest_profiles.netx360 import is_netx360_hbil, preprocess_hbil
        if is_netx360_hbil(csv_path):
            columns, rows = preprocess_hbil(csv_path)
            profile = detect_profile(csv_path.name, columns, csv_path)
            ro, to = {}, {}
            if profile:
                _, rows, ro, to = apply_profile(profile, columns, rows)
            return [{"sheet_name": "", "columns": columns, "rows": rows,
                     "role_overrides": ro, "type_overrides": to,
                     "vendor_profile": profile.get("display_name") if profile else None}]
    except Exception as e:
        print(f"  HBIL attempt failed: {e}")

    raw_rows = []
    with open(csv_path, encoding='utf-8', errors='replace') as fh:
        for i, row in enumerate(_csv.reader(fh)):
            if i >= 30: break
            raw_rows.append([str(v).strip() for v in row])
    max_cols = max((len(r) for r in raw_rows), default=1)
    padded = [r + [''] * (max_cols - len(r)) for r in raw_rows]
    best_row, best_score = 0, -1.0
    for i in range(min(len(padded) - 1, 29)):
        cells = [c for c in padded[i] if c]
        if not cells: continue
        non_num = sum(1 for c in cells if not _NUM_RE.match(c)) / len(cells)
        nc = [c for c in padded[i + 1] if c]
        next_num = sum(1 for c in nc if _NUM_RE.match(c)) / max(len(nc), 1)
        score = non_num * (1 + next_num)
        if score > best_score:
            best_score, best_row = score, i
    header_row = best_row if best_score >= 0.3 else 0
    if header_row > 0:
        df = _pd.read_csv(csv_path, skiprows=list(range(header_row)), header=0, on_bad_lines='skip')
    else:
        df = _pd.read_csv(csv_path, on_bad_lines='skip')
    columns = [str(c) for c in df.columns]
    rows = []
    for _, row in df.iterrows():
        d = {}
        for col in columns:
            val = row[col]
            d[col] = None if _pd.isna(val) else (val if isinstance(val, (int, float, bool)) else str(val))
        rows.append(d)
    profile = detect_profile(csv_path.name, columns, csv_path)
    ro, to = {}, {}
    if profile:
        _, rows, ro, to = apply_profile(profile, columns, rows)
    return [{"sheet_name": "", "columns": columns, "rows": rows,
             "role_overrides": ro, "type_overrides": to,
             "vendor_profile": profile.get("display_name") if profile else None}]


def _ingest(csv_path):
    sheets = _extract(csv_path)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    store = StructuredStore(db_path)
    for sh in sheets:
        store.create_table("test_doc", csv_path.name, sh["columns"], sh["rows"],
                           sh["sheet_name"], sh.get("role_overrides") or {}, sh.get("type_overrides") or {})
    return store


for fixture in sorted(FIXTURES.glob("*.csv")):
    print(f"\n{'='*60}")
    print(f"FIXTURE: {fixture.name}")
    try:
        store = _ingest(fixture)
        schema = store.get_schema(fixture.name)
        if schema is None:
            print("  ERROR: no schema")
            continue
        print(f"  table_name   : {schema['table_name']}")
        print(f"  row_count    : {schema['row_count']}")
        print(f"  column_count : {schema['column_count']}")
        print(f"  columns:")
        for c in schema["columns"]:
            role = c.get("role", "")
            print(f"    {c['name']!r:40s}  type={c['type']!r:12s}  role={role!r}")

        # aggregates
        from services.financial.metrics import compute_financial_metric
        for metric in ("total_market_value", "total_cost_basis", "total_pnl"):
            try:
                r = compute_financial_metric(store, fixture.name, metric)
                print(f"  {metric}: {r.get('value')}  warnings={r.get('warnings')}")
            except Exception as e:
                print(f"  {metric}: ERROR {e}")
    except Exception as e:
        print(f"  FATAL: {e}")
        import traceback; traceback.print_exc()
