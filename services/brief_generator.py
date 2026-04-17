"""Meeting Brief generator for Asymptote.

Given a StructuredStore instance, scans every table with detected financial
roles and computes a multi-section pre-meeting brief:

  household_summary     — total market value, cost basis, unrealized P&L
  accounts              — breakdown by account column (when present)
  top_positions         — top N holdings by market value
  tax_loss_candidates   — underwater positions above the loss threshold
  concentration_alerts  — any single position above X% of the portfolio
  cash_drag_alerts      — cash / money-market positions above threshold
  sector_allocation     — market value by sector (when sector role present)
  generated_at          — ISO timestamp

The function is intentionally synchronous and store-agnostic — it works on
any StructuredStore regardless of the collection it came from.
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


def _is_cash_like(name: str) -> bool:
    """Return True when a position name looks like a cash / money-market entry."""
    if not name:
        return False
    lower = name.lower().strip()
    return any(kw in lower for kw in _CASH_KEYWORDS)


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


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
        if mv_col:
            total_mv = _scalar(conn, f'SELECT SUM("{mv_col}") FROM "{table}"')
            hs = result['household_summary']
            hs['total_market_value'] = (hs.get('total_market_value') or 0) + (total_mv or 0)
            hs['sources'].append({'filename': filename, 'market_value': total_mv})

        if cb_col:
            total_cb = _scalar(conn, f'SELECT SUM("{cb_col}") FROM "{table}"')
            hs = result['household_summary']
            hs['total_cost_basis'] = (hs.get('total_cost_basis') or 0) + (total_cb or 0)

        if pnl_col:
            total_pnl = _scalar(conn, f'SELECT SUM("{pnl_col}") FROM "{table}"')
            hs = result['household_summary']
            hs['total_unrealized_pnl'] = (hs.get('total_unrealized_pnl') or 0) + (total_pnl or 0)
        elif mv_col and cb_col:
            # Derive P&L from market_value - cost_basis when no explicit pnl col
            derived_pnl = _scalar(
                conn,
                f'SELECT SUM("{mv_col}") - SUM("{cb_col}") FROM "{table}" '
                f'WHERE "{mv_col}" IS NOT NULL AND "{cb_col}" IS NOT NULL',
            )
            hs = result['household_summary']
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
            select_parts = [f'"{name_col}" AS name', f'"{mv_col}" AS market_value']
            if cb_col:
                select_parts.append(f'"{cb_col}" AS cost_basis')
            if pnl_col:
                select_parts.append(f'"{pnl_col}" AS unrealized_pnl')
            if ticker_col and ticker_col != name_col:
                select_parts.append(f'"{ticker_col}" AS ticker')
            if sector_col:
                select_parts.append(f'"{sector_col}" AS sector')

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
                f'"{name_col}" AS name',
                f'"{mv_col}" AS market_value',
                f'"{cb_col}" AS cost_basis',
                f'{loss_expr} AS unrealized_loss',
            ]
            if ticker_col and ticker_col != name_col:
                select_parts.append(f'"{ticker_col}" AS ticker')

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
                    f'SELECT "{name_col}" AS name, "{mv_col}" AS market_value '
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
                f'SELECT "{name_col}" AS name, "{mv_col}" AS market_value '
                f'FROM "{table}" WHERE "{mv_col}" IS NOT NULL AND "{mv_col}" > 0',
            )
            for pos in all_pos:
                if _is_cash_like(str(pos.get('name') or '')):
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
    """Generate a pre-meeting portfolio brief from a StructuredStore.

    Parameters
    ----------
    store:
        A ``StructuredStore`` instance whose tables will be scanned.
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
        'generated_at': datetime.now(tz=timezone.utc).isoformat(),
    }

    all_tables = store.list_tables()
    financial_tables = [t for t in all_tables if t.get('financial_roles')]

    for table_meta in financial_tables:
        _process_table(str(store.db_path), table_meta, resolved_thresholds, result)
        result['tables_scanned'] += 1

    # ── post-processing ────────────────────────────────────────────────────

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

    return result
