"""Portfolio metric computation for financial tabular data.

Provides compute_financial_metric() which operates on top of a HoldingsStore
instance using only its public API (get_schema, execute_query).  All metric
logic is contained here — the HoldingsStore has no knowledge of these.

P0.6 — Numeric sanity guards
------------------------------
After computing any aggregate, this module checks for common signs that
numeric coercion silently failed upstream:

* **near_zero_sum** — SUM of a market_value / pnl / cost_basis column is
  suspiciously close to zero while the table has many rows.  Almost always
  means the column is stored as TEXT and SQLite coerced each string to 0.
* **null_aggregate** — the aggregate result is NULL, which happens when the
  column is TEXT and SQLite's SUM / AVG returns NULL instead of a number.
* **weight_sum_outlier** — for breakdown metrics, if the total portfolio
  weight does not sum to ~100% the lot-rollup or coercion likely lost data.

Warnings are returned as a ``warnings`` list on the result dict.  The result
is always returned — Finn never suppresses data, but it never ships
silently wrong numbers either.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

AVAILABLE_METRICS: Dict[str, str] = {
    'row_count':                'Number of rows in the table',
    'total_market_value':       'Sum of the market_value column across all rows',
    'total_cost_basis':         'Sum of the cost_basis column across all rows',
    'total_pnl':                'Sum of the pnl column across all rows',
    'top_holdings':             'Top N holdings by market_value (param: limit)',
    'bottom_holdings':          'Bottom N holdings by market_value (param: limit)',
    'largest_gains':            'Top N rows by pnl (param: limit)',
    'largest_losses':           'Bottom N rows by pnl (param: limit)',
    'concentration':            'Share of total market_value held by top N positions (param: limit)',
    'breakdown_by_sector':      'Total market_value grouped by sector',
    'breakdown_by_asset_class': 'Total market_value grouped by asset_class',
    'breakdown_by_region':      'Total market_value grouped by region',
    'breakdown_by_currency':    'Total market_value grouped by currency',
    'weighted_return':          'Market-value-weighted average return',
    'summary_statistics':       'Min/max/mean/sum/count for every numeric column',
}


def _sanity_check_sum(
    value: Any,
    column: str,
    row_count: int,
    warnings: List[str],
) -> None:
    """P0.6: warn when a SUM aggregate looks like coercion silently failed."""
    if value is None:
        warnings.append(
            f"aggregation_null: SUM(\"{column}\") returned NULL — column is likely "
            f"stored as TEXT (numeric coercion failed at ingest). "
            f"Try re-ingesting the file after fixing the source format."
        )
    elif row_count > 5 and abs(value) < 0.01:
        warnings.append(
            f"aggregation_near_zero: SUM(\"{column}\") = {value} across {row_count} rows. "
            f"If the source values are non-zero this usually means the column was ingested "
            f"as TEXT and SQLite coerced each string to 0. "
            f"Re-ingest or use query_table with CAST to verify."
        )


def _sanity_check_breakdown(groups: List[Dict[str, Any]], warnings: List[str]) -> None:
    """P0.6: warn when portfolio weights don't sum to ~100%."""
    pcts = [g.get('pct') for g in groups if g.get('pct') is not None]
    if pcts:
        total_pct = sum(pcts)
        if total_pct < 80.0 or total_pct > 120.0:
            warnings.append(
                f"weight_sum_outlier: breakdown percentages sum to {total_pct:.1f}% "
                f"(expected ~100%). This may indicate lot-level rows are being double-counted "
                f"or that some rows have NULL market_value. "
                f"Consider using the __by_symbol rollup view for aggregations."
            )


def compute_financial_metric(
    store: Any,  # HoldingsStore — typed as Any to avoid circular import
    identifier: str,
    metric: str,
    limit: int = 10,
    group_by_symbol: bool = True,
    identifier_type: str | None = None,
) -> Dict[str, Any]:
    """Compute a canned portfolio metric against an ingested Holdings table.

    Parameters
    ----------
    store:
        A ``HoldingsStore`` instance.
    identifier:
        A table_name, filename, or document_id recognised by the store.
    metric:
        One of the keys in ``AVAILABLE_METRICS``.
    limit:
        Row cap for ranking metrics (top_holdings, largest_gains, etc.).
    group_by_symbol:
        When ``True`` (default) and a ``__by_symbol`` rollup view exists for
        the table, metrics are computed against the rolled-up view.  This
        prevents lot-level rows from inflating totals.  Set to ``False`` to
        query the raw lot-level table.
    identifier_type:
        Optional. Restrict schema lookup to a single identifier column —
        "table_name", "filename", or "document_id".  When ``None`` (default),
        all three are searched.
    """
    schema = store.get_schema(identifier, identifier_type=identifier_type)
    if not schema:
        raise ValueError(f"No structured table found for '{identifier}'")

    role_to_col: Dict[str, str] = {}
    for c in schema['columns']:
        if c.get('role') and c['role'] not in role_to_col:
            role_to_col[c['role']] = c['sql_name']

    base_table = schema['table_name']
    row_count = schema.get('row_count', 0)
    limit = max(1, min(int(limit), 1000))

    # P0.3: prefer the __by_symbol rollup view when it exists
    table = base_table
    if group_by_symbol and any(r in role_to_col for r in ('ticker', 'cusip', 'isin')):
        view_name = f'{base_table}__by_symbol'
        uri = f'file:{store.db_path}?mode=ro'
        with sqlite3.connect(uri, uri=True) as _conn:
            row = _conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view' AND name=?",
                (view_name,),
            ).fetchone()
            if row:
                table = view_name

    warnings: List[str] = []

    def require(role: str) -> str:
        col = role_to_col.get(role)
        if not col:
            raise ValueError(
                f"Metric '{metric}' requires a column with role '{role}' but none "
                f"was detected in {schema['filename']}. Detected roles: "
                f"{sorted(role_to_col.keys()) or 'none'}"
            )
        return col

    uri = f'file:{store.db_path}?mode=ro'
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row

        def scalar(sql: str, params=()) -> Any:
            cur = conn.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None

        def fetch_all(sql: str, params=()):
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return cols, [list(r) for r in cur.fetchall()]

        m = metric.lower()

        if m == 'row_count':
            return {'metric': metric, 'filename': schema['filename'],
                    'value': scalar(f'SELECT COUNT(*) FROM "{table}"'),
                    'table_used': table}

        if m == 'total_market_value':
            col = require('market_value')
            value = scalar(f'SELECT SUM("{col}") FROM "{table}"')
            _sanity_check_sum(value, col, row_count, warnings)
            result = {'metric': metric, 'filename': schema['filename'],
                      'column': col, 'value': value, 'table_used': table}
            if warnings:
                result['warnings'] = warnings
            return result

        if m == 'total_cost_basis':
            col = require('cost_basis')
            value = scalar(f'SELECT SUM("{col}") FROM "{table}"')
            _sanity_check_sum(value, col, row_count, warnings)
            result = {'metric': metric, 'filename': schema['filename'],
                      'column': col, 'value': value, 'table_used': table}
            if warnings:
                result['warnings'] = warnings
            return result

        if m == 'total_pnl':
            col = require('pnl')
            value = scalar(f'SELECT SUM("{col}") FROM "{table}"')
            _sanity_check_sum(value, col, row_count, warnings)
            result = {'metric': metric, 'filename': schema['filename'],
                      'column': col, 'value': value, 'table_used': table}
            if warnings:
                result['warnings'] = warnings
            return result

        if m in ('top_holdings', 'bottom_holdings'):
            mv = require('market_value')
            name_col = role_to_col.get('name') or role_to_col.get('ticker')
            order = 'DESC' if m == 'top_holdings' else 'ASC'
            select_cols = [f'"{mv}"']
            if name_col:
                select_cols.insert(0, f'"{name_col}"')
            sql = (
                f'SELECT {", ".join(select_cols)} FROM "{table}" '
                f'WHERE "{mv}" IS NOT NULL ORDER BY "{mv}" {order} LIMIT ?'
            )
            cols, rows = fetch_all(sql, (limit,))
            return {'metric': metric, 'filename': schema['filename'],
                    'columns': cols, 'rows': rows, 'table_used': table}

        if m in ('largest_gains', 'largest_losses'):
            pnl = require('pnl')
            name_col = role_to_col.get('name') or role_to_col.get('ticker')
            order = 'DESC' if m == 'largest_gains' else 'ASC'
            select_cols = [f'"{pnl}"']
            if name_col:
                select_cols.insert(0, f'"{name_col}"')
            sql = (
                f'SELECT {", ".join(select_cols)} FROM "{table}" '
                f'WHERE "{pnl}" IS NOT NULL ORDER BY "{pnl}" {order} LIMIT ?'
            )
            cols, rows = fetch_all(sql, (limit,))
            return {'metric': metric, 'filename': schema['filename'],
                    'columns': cols, 'rows': rows, 'table_used': table}

        if m == 'concentration':
            mv = require('market_value')
            total = scalar(f'SELECT SUM("{mv}") FROM "{table}"')
            top = scalar(
                f'SELECT SUM(v) FROM (SELECT "{mv}" AS v FROM "{table}" '
                f'WHERE "{mv}" IS NOT NULL ORDER BY "{mv}" DESC LIMIT ?)',
                (limit,),
            )
            pct = (top / total * 100.0) if total and top is not None else None
            _sanity_check_sum(total, mv, row_count, warnings)
            result = {
                'metric': metric,
                'filename': schema['filename'],
                'limit': limit,
                'top_n_value': top,
                'total_value': total,
                'concentration_pct': pct,
                'table_used': table,
            }
            if warnings:
                result['warnings'] = warnings
            return result

        if m in ('breakdown_by_sector', 'breakdown_by_asset_class',
                 'breakdown_by_region', 'breakdown_by_currency'):
            group_role = m.replace('breakdown_by_', '')
            if group_role in role_to_col:
                result = _breakdown(conn, schema, table, group_role, 'market_value')
            elif group_role in ('sector', 'asset_class') and 'ticker' in role_to_col:
                # Source file lacks a sector/asset_class column — fall through
                # to v4.2 classification enrichment so the metric still works.
                # Roles outside this set (region, currency) need explicit data.
                result = _breakdown_via_classification(
                    conn, schema, table, group_role, role_to_col,
                )
            else:
                raise ValueError(
                    f"No column with role '{group_role}' detected in {schema['filename']}. "
                    f"Detected roles: {sorted(role_to_col.keys()) or 'none'}. "
                    f"Use query_table for hand-written aggregation, or call "
                    f"get_security_classification / enrich_holdings to attach "
                    f"sector data first."
                )
            _sanity_check_breakdown(result.get('groups', []), warnings)
            if warnings:
                result['warnings'] = warnings
            result['table_used'] = table
            return result

        if m == 'weighted_return':
            ret = require('return')
            mv = require('market_value')
            total_mv = scalar(
                f'SELECT SUM("{mv}") FROM "{table}" '
                f'WHERE "{ret}" IS NOT NULL AND "{mv}" IS NOT NULL'
            )
            weighted = scalar(
                f'SELECT SUM("{ret}" * "{mv}") FROM "{table}" '
                f'WHERE "{ret}" IS NOT NULL AND "{mv}" IS NOT NULL'
            )
            value = (weighted / total_mv) if total_mv else None
            _sanity_check_sum(total_mv, mv, row_count, warnings)
            result = {
                'metric': metric,
                'filename': schema['filename'],
                'return_column': ret,
                'weight_column': mv,
                'weighted_return': value,
                'table_used': table,
            }
            if warnings:
                result['warnings'] = warnings
            return result

        if m == 'summary_statistics':
            stats = []
            for c in schema['columns']:
                if c['type'] in ('integer', 'real', 'currency', 'percent'):
                    col = c['sql_name']
                    row = conn.execute(
                        f'SELECT MIN("{col}"), MAX("{col}"), AVG("{col}"), '
                        f'SUM("{col}"), COUNT("{col}") FROM "{table}"'
                    ).fetchone()
                    col_warnings: List[str] = []
                    _sanity_check_sum(row[3], col, row_count, col_warnings)
                    entry: Dict[str, Any] = {
                        'column': col,
                        'role': c.get('role'),
                        'type': c['type'],
                        'min': row[0],
                        'max': row[1],
                        'avg': row[2],
                        'sum': row[3],
                        'count': row[4],
                    }
                    if col_warnings:
                        entry['warnings'] = col_warnings
                    stats.append(entry)
            return {'metric': metric, 'filename': schema['filename'],
                    'columns': stats, 'table_used': table}

        raise ValueError(
            f"Unknown metric '{metric}'. Available: {sorted(AVAILABLE_METRICS.keys())}"
        )


def _breakdown_via_classification(
    conn: sqlite3.Connection,
    schema: Dict[str, Any],
    table: str,
    group_role: str,  # 'sector' or 'asset_class'
    role_to_col: Dict[str, str],
    classify_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Group market_value by sector/asset_class when the source file lacks
    that column, by classifying each ticker via the v4.2 enrichment feed.

    Result shape mirrors `_breakdown` plus an `enriched_via` marker so the
    calling LLM (and the user) can see the sector data didn't come from the
    file itself.
    """
    if classify_fn is None:
        from services.market_data.classification import get_security_classification
        classify_fn = get_security_classification

    if 'market_value' not in role_to_col:
        raise ValueError(
            f"No column with role 'market_value' detected in {schema['filename']}"
        )

    ticker_col = role_to_col['ticker']
    mv_col = role_to_col['market_value']

    sql = (
        f'SELECT TRIM("{ticker_col}") AS sym, SUM("{mv_col}") AS total '
        f'FROM "{table}" '
        f'WHERE TRIM(COALESCE("{ticker_col}", \'\')) != \'\' '
        f'  AND "{mv_col}" IS NOT NULL '
        f'GROUP BY sym'
    )
    rows = conn.execute(sql).fetchall()

    bucket_totals: Dict[str, float] = {}
    bucket_counts: Dict[str, int] = {}
    unclassified_total = 0.0
    unclassified_count = 0
    classified_symbols = 0
    for row in rows:
        sym = (row[0] or '').strip().upper()
        total = row[1]
        if not sym or total is None:
            continue
        try:
            payload = classify_fn(sym) or {}
        except Exception:
            payload = {}
        bucket = payload.get(group_role) if 'error' not in payload else None
        if not bucket:
            unclassified_total += float(total or 0)
            unclassified_count += 1
            continue
        classified_symbols += 1
        bucket_totals[bucket] = bucket_totals.get(bucket, 0.0) + float(total)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    groups = [
        {'group': name, 'total': total, 'count': bucket_counts[name]}
        for name, total in sorted(bucket_totals.items(), key=lambda kv: -kv[1])
    ]
    if unclassified_count:
        groups.append({
            'group': 'Unclassified',
            'total': unclassified_total,
            'count': unclassified_count,
        })

    total_sum = sum((g['total'] or 0) for g in groups)
    for g in groups:
        g['pct'] = (
            (g['total'] / total_sum * 100.0) if total_sum and g['total'] else None
        )

    return {
        'metric': f'breakdown_by_{group_role}',
        'filename': schema['filename'],
        'group_column': None,  # no source-file column — derived from ticker
        'value_column': mv_col,
        'groups': groups,
        'total': total_sum,
        'enriched_via': 'classification',
        'classified_symbols': classified_symbols,
        'unclassified_symbols': unclassified_count,
        'note': (
            f"Source file has no '{group_role}' column. Each symbol was "
            f"classified via get_security_classification (yfinance-backed) "
            f"and aggregated. Symbols that couldn't be classified are grouped "
            f"under 'Unclassified'."
        ),
    }


def _breakdown(
    conn: sqlite3.Connection,
    schema: Dict[str, Any],
    table: str,
    group_role: str,
    value_role: str,
) -> Dict[str, Any]:
    role_to_col = {c['role']: c['sql_name'] for c in schema['columns'] if c.get('role')}
    if group_role not in role_to_col:
        raise ValueError(
            f"No column with role '{group_role}' detected in {schema['filename']}"
        )
    if value_role not in role_to_col:
        raise ValueError(
            f"No column with role '{value_role}' detected in {schema['filename']}"
        )

    g = role_to_col[group_role]
    v = role_to_col[value_role]
    sql = (
        f'SELECT "{g}" AS group_key, SUM("{v}") AS total, COUNT(*) AS count '
        f'FROM "{table}" WHERE "{g}" IS NOT NULL GROUP BY "{g}" ORDER BY total DESC'
    )
    cur = conn.execute(sql)
    groups = [{'group': r[0], 'total': r[1], 'count': r[2]} for r in cur.fetchall()]
    total_sum = sum((grp['total'] or 0) for grp in groups)
    for grp in groups:
        grp['pct'] = (
            (grp['total'] / total_sum * 100.0) if total_sum and grp['total'] else None
        )
    return {
        'metric': f'breakdown_by_{group_role}',
        'filename': schema['filename'],
        'group_column': g,
        'value_column': v,
        'groups': groups,
        'total': total_sum,
    }
