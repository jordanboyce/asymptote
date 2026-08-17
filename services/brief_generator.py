"""Meeting Brief generator for Finn.

Given a HoldingsStore instance, scans every table with detected financial
roles and computes a multi-section pre-meeting brief:

  household_summary     — total market value, cost basis, unrealized P&L
  accounts              — breakdown by account column (when present)
  top_positions         — top N Positions by market value
  tax_loss_candidates   — underwater Positions above the loss threshold
  concentration_alerts  — any single Position above X% of the portfolio
  cash_drag_alerts      — cash / money-market Positions above threshold
  sector_allocation     — market value by sector (when sector role present)
  generated_at          — ISO timestamp

The function is intentionally synchronous and store-agnostic — it works on
any HoldingsStore regardless of the Collection it came from.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_CASH_KEYWORDS = frozenset({
    'cash', 'money market', 'mmf', 'sweep', 'fdic', 'treasury bill',
    't-bill', 'tbill', 'money mkt', 'mny mkt', 'cash & equiv',
    'cash and equiv', 'cash equivalent',
})

_DEFAULT_THRESHOLDS = {
    'tax_loss_min': 500.0,
    'concentration_pct': 10.0,
    'cash_drag_min': 50_000.0,
    'top_n': 10,
}

# Some brokerage exports (e.g. an XLSX→CSV pass through "ExportExcel" on
# Pershing's URGL report) leave the symbol column literally containing the
# word "Symbol" in every data cell — the column-header label leaking into the
# body. Null those values out at the SELECT layer so the brief surfaces the
# (still-correct) name field instead of "Symbol" in every row.
_HEADER_LITERALS_SQL = (
    "'SYMBOL', 'TICKER', 'CUSIP', 'ISIN', 'NAME', 'DESCRIPTION', "
    "'SECURITY IDENTIFIER', 'SECURITY DESCRIPTION', 'SECURITY ID'"
)


def _safe_label_select(col: str, alias: str) -> str:
    """SQL fragment that emits NULL when col's value is a column-header literal."""
    return (
        f"CASE WHEN UPPER(TRIM(COALESCE(CAST(\"{col}\" AS TEXT), ''))) "
        f"IN ({_HEADER_LITERALS_SQL}) THEN NULL ELSE \"{col}\" END AS {alias}"
    )


def _dedupe_key(row: Dict[str, Any]) -> Optional[str]:
    """Pick a stable identity key for a position row, preferring ticker → name."""
    for field in ('ticker', 'name'):
        val = row.get(field)
        if val is None:
            continue
        s = str(val).strip().upper()
        if s:
            return s
    return None


def _dedupe_positions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge rows that describe the same security across multiple tables.

    Same security appearing in multiple files (e.g. HBIL positions + Pershing
    URGL of the same household) gets collapsed into one row. The merged row
    keeps the maximum market_value seen and prefers non-null values for every
    other field, so a row with cost_basis/unrealized_pnl absorbs a positions-
    only sibling. Ungroupable rows (no ticker, no name) pass through.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    passthrough: List[Dict[str, Any]] = []
    for row in rows:
        key = _dedupe_key(row)
        if key is None:
            passthrough.append(dict(row))
            continue
        if key not in merged:
            merged[key] = dict(row)
            continue
        existing = merged[key]
        for field, value in row.items():
            if value is None:
                continue
            if field == 'market_value':
                cur = existing.get('market_value')
                if cur is None or (isinstance(value, (int, float)) and value > cur):
                    existing['market_value'] = value
            elif field == 'source':
                existing_source = existing.get('source')
                if existing_source and existing_source != value:
                    sources = existing_source.split(' + ') if isinstance(existing_source, str) else [str(existing_source)]
                    if value not in sources:
                        sources.append(value)
                        existing['source'] = ' + '.join(sources)
                else:
                    existing['source'] = value
            elif existing.get(field) is None:
                existing[field] = value
    return list(merged.values()) + passthrough


def _dedupe_alerts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dedupe alert rows by name; keep the entry with the highest market_value."""
    merged: Dict[str, Dict[str, Any]] = {}
    passthrough: List[Dict[str, Any]] = []
    for row in rows:
        name = row.get('name')
        if name is None:
            passthrough.append(dict(row))
            continue
        key = str(name).strip().upper()
        if not key:
            passthrough.append(dict(row))
            continue
        cur = merged.get(key)
        if cur is None or (row.get('market_value') or 0) > (cur.get('market_value') or 0):
            merged[key] = dict(row)
    return list(merged.values()) + passthrough


def _is_cash_like(name: str, asset_class: str | None = None) -> bool:
    """Return True when a position looks like a cash / money-market entry.

    Checks the asset class as well as the name, because the name on its own is
    not reliable. A Pershing money-market sweep comes through as "AIGI FUND"
    with a Security Type of "Money Market Funds" -- no cash keyword appears in
    the name at all. Reading only the name had two consequences on every such
    export, both of them quiet:

    - the sweep was judged against ``max_single_position_pct`` and reported as
      a concentration breach, which is not a meaningful reading of a cash
      vehicle and looks naive in front of a prospect; and
    - ``check_cash_policy`` received ``cash_market_value=None`` and recorded
      "no cash position identified", so the client's own min/max cash bands
      never evaluated -- the check reported itself as unrun rather than
      wrong, but an advisor who set those bands never found out they were
      being skipped.
    """
    for value in (name, asset_class):
        if not value:
            continue
        lower = str(value).lower().strip()
        if any(kw in lower for kw in _CASH_KEYWORDS):
            return True
    return False


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _guard(
    conn: sqlite3.Connection,
    base_table: str,
    column: str,
    filename: str,
    result: Dict[str, Any],
) -> List[str]:
    """Run the P0.6 coercion guard over a column; record any warnings.

    The guard runs against ``base_table`` — the raw ingested table — and never
    against the ``__by_symbol`` rollup view, even though the value query
    usually reads the view. The view launders the bug: it defines its column
    as ``SUM("Market_Value")``, so a TEXT column full of "27,431.50" arrives
    downstream already coerced to 27 and typed REAL. Sampling the view finds a
    clean numeric column and reports nothing, while the number it produced is
    off by three orders of magnitude. Sampling the base table sees the TEXT.

    Returns the warnings so the caller can decide whether to trust the value
    it just computed. Warnings also accumulate on ``result['warnings']`` so
    every consumer of the brief sees them without having to ask.
    """
    from services.tabular.aggregate_guard import check_aggregate_coercion

    guard_sql = f'SELECT SUM("{column}") FROM "{base_table}"'
    warnings = check_aggregate_coercion(conn, guard_sql)
    for warning in warnings:
        entry = {'filename': filename, 'column': column, 'warning': warning}
        if entry not in result['warnings']:
            result['warnings'].append(entry)
    return warnings


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> List[Any]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return cols, [dict(zip(cols, r)) for r in cur.fetchall()]


def _role_map(table_meta: Dict[str, Any]) -> Dict[str, str]:
    """Return role → sql_name mapping for a table entry from list_tables()."""
    roles: Dict[str, str] = {}
    for col in table_meta.get('columns', []):
        role = col.get('role')
        if role and role not in roles:
            roles[role] = col['sql_name']
    return roles


def _prefer_view(db_path: str, base_table: str, role_map: Dict[str, str]) -> str:
    """Use the __by_symbol rollup view when it exists (avoids lot double-counting)."""
    if not any(r in role_map for r in ('ticker', 'cusip', 'isin')):
        return base_table
    view_name = f'{base_table}__by_symbol'
    uri = f'file:{db_path}?mode=ro'
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' AND name=?",
            (view_name,),
        ).fetchone()
    return view_name if row else base_table


def _process_table(
    db_path: str,
    table_meta: Dict[str, Any],
    thresholds: Dict[str, Any],
    result: Dict[str, Any],
) -> None:
    """Fold one financial table into the growing brief result dict."""
    roles = _role_map(table_meta)
    base_table = table_meta['table_name']
    table = _prefer_view(db_path, base_table, roles)
    filename = table_meta.get('filename', base_table)

    mv_col = roles.get('market_value')
    cb_col = roles.get('cost_basis')
    pnl_col = roles.get('pnl')
    sector_col = roles.get('sector')
    asset_class_col = roles.get('asset_class')
    account_col = roles.get('account')
    name_col = roles.get('name') or roles.get('ticker') or roles.get('cusip')
    ticker_col = roles.get('ticker')

    top_n = int(thresholds['top_n'])
    tax_loss_min = float(thresholds['tax_loss_min'])
    concentration_pct = float(thresholds['concentration_pct'])
    cash_drag_min = float(thresholds['cash_drag_min'])

    uri = f'file:{db_path}?mode=ro'
    with sqlite3.connect(uri, uri=True) as conn:

        # ── household summary ──────────────────────────────────────────────
        #
        # Every total here goes through the P0.6 coercion guard before it is
        # believed. Without it this function is the exact bug Finn exists to
        # prevent: a `Market Value` column left TEXT (because two restricted
        # rows read "See contract") makes SUM() truncate "27,431.50" to 27,
        # and the brief reports $203 for a $207,727 book with no warning.
        # A wrong total on a meeting brief is worse than no total.
        if mv_col:
            total_mv = _scalar(conn, f'SELECT SUM("{mv_col}") FROM "{table}"')
            hs = result['household_summary']
            mv_warnings = _guard(conn, base_table, mv_col, filename, result)
            if mv_warnings:
                hs['total_market_value_reliable'] = False
                hs['sources'].append({
                    'filename': filename,
                    'market_value': total_mv,
                    'reliable': False,
                })
            else:
                hs['total_market_value'] = (hs.get('total_market_value') or 0) + (total_mv or 0)
                hs['sources'].append({'filename': filename, 'market_value': total_mv})

        if cb_col:
            total_cb = _scalar(conn, f'SELECT SUM("{cb_col}") FROM "{table}"')
            hs = result['household_summary']
            if _guard(conn, base_table, cb_col, filename, result):
                hs['total_cost_basis_reliable'] = False
            else:
                hs['total_cost_basis'] = (hs.get('total_cost_basis') or 0) + (total_cb or 0)

        if pnl_col:
            total_pnl = _scalar(conn, f'SELECT SUM("{pnl_col}") FROM "{table}"')
            hs = result['household_summary']
            if _guard(conn, base_table, pnl_col, filename, result):
                hs['total_unrealized_pnl_reliable'] = False
            else:
                hs['total_unrealized_pnl'] = (hs.get('total_unrealized_pnl') or 0) + (total_pnl or 0)
        elif mv_col and cb_col:
            # Derive P&L from market_value - cost_basis when no explicit pnl
            # column. Both operands were just guarded above; a derived figure
            # inherits the unreliability of whichever side failed, and a
            # derived P&L off by the size of the market value is the most
            # misleading number on the page.
            hs = result['household_summary']
            if hs.get('total_market_value_reliable') is False or hs.get('total_cost_basis_reliable') is False:
                hs['total_unrealized_pnl_reliable'] = False
            else:
                derived_pnl = _scalar(
                    conn,
                    f'SELECT SUM("{mv_col}") - SUM("{cb_col}") FROM "{table}" '
                    f'WHERE "{mv_col}" IS NOT NULL AND "{cb_col}" IS NOT NULL',
                )
                hs['total_unrealized_pnl'] = (hs.get('total_unrealized_pnl') or 0) + (derived_pnl or 0)

        # ── account breakdown ──────────────────────────────────────────────
        if account_col and mv_col:
            _, acct_rows = _rows(
                conn,
                f'SELECT "{account_col}" AS account, SUM("{mv_col}") AS market_value '
                f'FROM "{table}" WHERE "{account_col}" IS NOT NULL '
                f'GROUP BY "{account_col}" ORDER BY market_value DESC',
            )
            for ar in acct_rows:
                acct_key = str(ar['account'])
                existing = next(
                    (a for a in result['accounts'] if a['account'] == acct_key), None
                )
                if existing:
                    existing['market_value'] = (existing['market_value'] or 0) + (ar['market_value'] or 0)
                else:
                    result['accounts'].append({
                        'account': acct_key,
                        'market_value': ar['market_value'],
                        'source': filename,
                    })

        # ── top N positions ────────────────────────────────────────────────
        if mv_col and name_col:
            select_parts = [_safe_label_select(name_col, 'name'), f'"{mv_col}" AS market_value']
            if cb_col:
                select_parts.append(f'"{cb_col}" AS cost_basis')
            if pnl_col:
                select_parts.append(f'"{pnl_col}" AS unrealized_pnl')
            if ticker_col and ticker_col != name_col:
                select_parts.append(_safe_label_select(ticker_col, 'ticker'))
            if sector_col:
                select_parts.append(f'"{sector_col}" AS sector')
            # Carry the asset class through. Without it, cash detection can
            # only look at the security name, and a Pershing money-market
            # sweep is named "AIGI FUND" -- no cash keyword anywhere in it.
            # See _is_cash_like.
            if asset_class_col and asset_class_col not in (name_col, sector_col):
                select_parts.append(f'"{asset_class_col}" AS asset_class')

            _, top_rows = _rows(
                conn,
                f'SELECT {", ".join(select_parts)} FROM "{table}" '
                f'WHERE "{mv_col}" IS NOT NULL ORDER BY "{mv_col}" DESC LIMIT ?',
                (top_n,),
            )
            for tr in top_rows:
                tr['source'] = filename
            result['top_positions'].extend(top_rows)

        # ── tax loss candidates ────────────────────────────────────────────
        if mv_col and cb_col and name_col:
            loss_expr = f'("{cb_col}" - "{mv_col}")'
            select_parts = [
                _safe_label_select(name_col, 'name'),
                f'"{mv_col}" AS market_value',
                f'"{cb_col}" AS cost_basis',
                f'{loss_expr} AS unrealized_loss',
            ]
            if ticker_col and ticker_col != name_col:
                select_parts.append(_safe_label_select(ticker_col, 'ticker'))

            _, loss_rows = _rows(
                conn,
                f'SELECT {", ".join(select_parts)} FROM "{table}" '
                f'WHERE "{mv_col}" < "{cb_col}" '
                f'AND {loss_expr} >= ? '
                f'ORDER BY unrealized_loss DESC',
                (tax_loss_min,),
            )
            for lr in loss_rows:
                lr['source'] = filename
            result['tax_loss_candidates'].extend(loss_rows)

        # ── concentration alerts ───────────────────────────────────────────
        if mv_col and name_col:
            total_mv_for_conc = _scalar(
                conn, f'SELECT SUM("{mv_col}") FROM "{table}" WHERE "{mv_col}" IS NOT NULL'
            )
            if total_mv_for_conc and total_mv_for_conc > 0:
                _, pos_rows = _rows(
                    conn,
                    f'SELECT {_safe_label_select(name_col, "name")}, "{mv_col}" AS market_value '
                    f'FROM "{table}" WHERE "{mv_col}" IS NOT NULL ORDER BY "{mv_col}" DESC',
                )
                for pr in pos_rows:
                    mv = pr.get('market_value') or 0
                    pct = (mv / total_mv_for_conc * 100.0)
                    if pct >= concentration_pct:
                        result['concentration_alerts'].append({
                            'name': pr['name'],
                            'market_value': mv,
                            'pct_of_portfolio': round(pct, 2),
                            'threshold_pct': concentration_pct,
                            'source': filename,
                        })

        # ── cash drag alerts ───────────────────────────────────────────────
        if mv_col and name_col:
            _, all_pos = _rows(
                conn,
                f'SELECT {_safe_label_select(name_col, "name")}, "{mv_col}" AS market_value '
                f'FROM "{table}" WHERE "{mv_col}" IS NOT NULL AND "{mv_col}" > 0',
            )
            for pos in all_pos:
                if _is_cash_like(str(pos.get('name') or ''), pos.get('asset_class')):
                    mv = pos.get('market_value') or 0
                    if mv >= cash_drag_min:
                        result['cash_drag_alerts'].append({
                            'name': pos['name'],
                            'market_value': mv,
                            'threshold': cash_drag_min,
                            'source': filename,
                        })

        # ── sector allocation ──────────────────────────────────────────────
        if sector_col and mv_col:
            _, sec_rows = _rows(
                conn,
                f'SELECT "{sector_col}" AS sector, SUM("{mv_col}") AS market_value, '
                f'COUNT(*) AS position_count '
                f'FROM "{table}" WHERE "{sector_col}" IS NOT NULL '
                f'GROUP BY "{sector_col}" ORDER BY market_value DESC',
            )
            for sr in sec_rows:
                sector_key = str(sr['sector'])
                existing = next(
                    (s for s in result['sector_allocation'] if s['sector'] == sector_key), None
                )
                if existing:
                    existing['market_value'] = (existing['market_value'] or 0) + (sr['market_value'] or 0)
                    existing['position_count'] = existing['position_count'] + sr['position_count']
                else:
                    result['sector_allocation'].append({
                        'sector': sector_key,
                        'market_value': sr['market_value'],
                        'position_count': sr['position_count'],
                    })


def generate_meeting_brief(
    store: Any,
    collection_id: Optional[str] = None,
    thresholds: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a pre-meeting portfolio brief from a HoldingsStore.

    Parameters
    ----------
    store:
        A ``HoldingsStore`` instance whose tables will be scanned.
    collection_id:
        Optional identifier included in the output metadata.  Does not
        affect which tables are scanned — the store already targets one
        collection's database.
    thresholds:
        Optional overrides for the alert thresholds:

        - ``tax_loss_min``      — minimum unrealized loss (default 500)
        - ``concentration_pct`` — single-position % threshold (default 10.0)
        - ``cash_drag_min``     — minimum cash balance to flag (default 50000)
        - ``top_n``             — number of top positions to include (default 10)

    Returns
    -------
    dict with keys:
        ``household_summary``, ``accounts``, ``top_positions``,
        ``tax_loss_candidates``, ``concentration_alerts``,
        ``cash_drag_alerts``, ``sector_allocation``, ``generated_at``,
        ``collection_id``, ``tables_scanned``.
    """
    resolved_thresholds = dict(_DEFAULT_THRESHOLDS)
    if thresholds:
        for k in _DEFAULT_THRESHOLDS:
            if k in thresholds and thresholds[k] is not None:
                resolved_thresholds[k] = thresholds[k]

    result: Dict[str, Any] = {
        'collection_id': collection_id,
        'household_summary': {
            'total_market_value': 0.0,
            'total_cost_basis': None,
            'total_unrealized_pnl': None,
            'sources': [],
        },
        'accounts': [],
        'top_positions': [],
        'tax_loss_candidates': [],
        'concentration_alerts': [],
        'cash_drag_alerts': [],
        'sector_allocation': [],
        'tables_scanned': 0,
        'warnings': [],
        'generated_at': datetime.now(tz=timezone.utc).isoformat(),
    }

    all_tables = store.list_tables()
    financial_tables = [t for t in all_tables if t.get('financial_roles')]

    for table_meta in financial_tables:
        _process_table(str(store.db_path), table_meta, resolved_thresholds, result)
        result['tables_scanned'] += 1

    # ── post-processing ────────────────────────────────────────────────────

    # Cross-file dedup: when a Collection has multiple holdings tables
    # describing the same household (e.g. NetX360 HBIL + Pershing URGL of
    # the same accounts), the same security appears in every table. Group
    # by normalised (ticker || name) and merge fields, preferring non-null
    # values so a row from a richer table (with cost_basis / unrealized_pnl)
    # absorbs the sparser row from a positions-only file.
    result['top_positions'] = _dedupe_positions(result['top_positions'])
    result['tax_loss_candidates'] = _dedupe_positions(result['tax_loss_candidates'])
    result['concentration_alerts'] = _dedupe_alerts(result['concentration_alerts'])
    result['cash_drag_alerts'] = _dedupe_alerts(result['cash_drag_alerts'])

    # Sort top_positions globally, keep top_n
    top_n = int(resolved_thresholds['top_n'])
    result['top_positions'].sort(key=lambda p: (p.get('market_value') or 0), reverse=True)
    result['top_positions'] = result['top_positions'][:top_n]

    # Sort tax_loss_candidates by largest loss first
    result['tax_loss_candidates'].sort(
        key=lambda p: (p.get('unrealized_loss') or 0), reverse=True
    )

    # Sort accounts by market value
    result['accounts'].sort(key=lambda a: (a.get('market_value') or 0), reverse=True)

    # Sort sector allocation by market value; compute pct
    total_mv = result['household_summary'].get('total_market_value') or 0
    result['sector_allocation'].sort(
        key=lambda s: (s.get('market_value') or 0), reverse=True
    )
    if total_mv > 0:
        for s in result['sector_allocation']:
            s['pct_of_portfolio'] = round((s.get('market_value') or 0) / total_mv * 100, 2)

    # Null out zero totals for cleaner output
    hs = result['household_summary']
    if hs['total_cost_basis'] == 0:
        hs['total_cost_basis'] = None
    if hs['total_unrealized_pnl'] == 0:
        hs['total_unrealized_pnl'] = None

    # A total that failed the coercion guard is withheld, not shown with an
    # asterisk. $203 rendered next to a caveat still gets read as $203 — and
    # a number on a meeting brief is the one thing an advisor will repeat out
    # loud. Consumers get `*_reliable: False` plus the warning to explain the
    # blank.
    for field in ('total_market_value', 'total_cost_basis', 'total_unrealized_pnl'):
        if hs.get(f'{field}_reliable') is False:
            hs[field] = None
    if hs.get('total_market_value') == 0 and hs.get('total_market_value_reliable') is False:
        hs['total_market_value'] = None

    return result
