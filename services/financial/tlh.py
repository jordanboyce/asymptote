"""Tax-Loss Harvesting (TLH) primitives.

Four pure functions composed into a household-aware harvest plan:

  scan_unrealized_losses   — underwater Positions in taxable accounts, sorted
                             with short-term losses first so they offset the
                             higher ordinary-income tax rate
  gain_loss_budget         — realized YTD gains/losses + carryforward (when
                             transaction history exists; degrades gracefully)
  suggest_replacements     — non-substantially-identical securities that keep
                             similar exposure (different-issuer ETF of a
                             different index)
  check_wash_sale          — flags a candidate when the same security is held
                             anywhere in the household, including spousal
                             IRA/Roth — the moat over single-account tools

Module-level constants are exported for the test suite. The functions are
synchronous and store-agnostic; they take a HoldingsStore (or list of stores
for the household scan) and operate via its public API.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional


# ---------------------------------------------------------------------------
# Account-type classification
# ---------------------------------------------------------------------------
#
# Tax-loss harvesting only matters in TAXABLE accounts — losses inside an IRA,
# Roth, 401(k), HSA, or 529 produce no offset. The account-name strings come
# straight from the brokerage export and are highly variable across vendors,
# so the check is keyword-based with conservative defaults: when we can't
# confidently tag an account, we treat it as taxable (the LLM will surface the
# uncertainty in its narration).

_TAX_DEFERRED_KEYWORDS = (
    'ira', 'roth', '401k', '401(k)', '403b', '403(b)', 'sep-ira', 'sep ira',
    'simple ira', 'hsa', '529', 'pension', 'annuity', 'tsa', 'tda',
    'inherited ira', 'beneficiary ira', 'rollover ira', 'sarsep',
)

# Wash-sale window per IRS Pub 550: 30 days before AND 30 days after the sale.
WASH_SALE_WINDOW_DAYS = 30

# Holding-period boundary between short-term and long-term capital gains.
LONG_TERM_HOLDING_DAYS = 365

# Default minimum unrealized loss (in dollars) for surfacing a candidate.
DEFAULT_MIN_LOSS = 500.0


def is_tax_deferred(account_name: Optional[str]) -> bool:
    """True when the account name matches a tax-deferred / tax-free bucket.

    Conservative — when nothing matches we return False (treated as taxable),
    so the harvest plan over-includes rather than silently dropping a real
    candidate.
    """
    if not account_name:
        return False
    lowered = str(account_name).lower()
    return any(kw in lowered for kw in _TAX_DEFERRED_KEYWORDS)


# ---------------------------------------------------------------------------
# Curated replacement-ETF table
# ---------------------------------------------------------------------------
#
# The "substantially identical" test in IRS Pub 550 has no bright line, but
# practitioner consensus is that a different-issuer ETF tracking a different
# index is safe (SPY ↔ IVV ↔ VOO all track S&P 500 and ARE substantially
# identical to each other; SPY ↔ VTI is broader and considered safe).
#
# Each entry is a small list of candidate replacements ordered by closeness
# of exposure, with a `rationale` string the LLM can echo to the advisor.
# When a symbol isn't in the table, we fall back to a sector-ETF proxy via
# get_security_classification — see suggest_replacements().

_REPLACEMENT_TABLE: Dict[str, List[Dict[str, str]]] = {
    # ── US large-cap broad ─────────────────────────────────────────────────
    'SPY':  [
        {'symbol': 'VTI',  'name': 'Vanguard Total Stock Market ETF', 'rationale': 'Broader US equity (CRSP US Total Market) — different issuer, different index.'},
        {'symbol': 'ITOT', 'name': 'iShares Core S&P Total US Stock Market ETF', 'rationale': 'Broad US equity, S&P Total Market — different from S&P 500.'},
        {'symbol': 'SCHB', 'name': 'Schwab US Broad Market ETF', 'rationale': 'Dow Jones US Broad Stock Market — different issuer and index.'},
    ],
    'IVV':  [
        {'symbol': 'VTI',  'name': 'Vanguard Total Stock Market ETF', 'rationale': 'Broader US equity index, different issuer.'},
        {'symbol': 'SCHB', 'name': 'Schwab US Broad Market ETF', 'rationale': 'Different issuer and index.'},
    ],
    'VOO':  [
        {'symbol': 'VTI',  'name': 'Vanguard Total Stock Market ETF', 'rationale': 'Same issuer but different (broader) index — safer than another S&P 500 fund.'},
        {'symbol': 'ITOT', 'name': 'iShares Core S&P Total US Stock Market ETF', 'rationale': 'Different issuer, different (broader) S&P index.'},
    ],
    # ── US large-cap growth / value ────────────────────────────────────────
    'VUG':  [
        {'symbol': 'IWF',  'name': 'iShares Russell 1000 Growth ETF', 'rationale': 'Russell 1000 Growth vs CRSP Large-Cap Growth — different issuer, different index.'},
        {'symbol': 'SCHG', 'name': 'Schwab US Large-Cap Growth ETF', 'rationale': 'Dow Jones Large-Cap Growth — different issuer.'},
    ],
    'VTV':  [
        {'symbol': 'IWD',  'name': 'iShares Russell 1000 Value ETF', 'rationale': 'Russell 1000 Value vs CRSP Large-Cap Value — different issuer, different index.'},
        {'symbol': 'SCHV', 'name': 'Schwab US Large-Cap Value ETF', 'rationale': 'Different issuer and index.'},
    ],
    # ── US tech / nasdaq ───────────────────────────────────────────────────
    'QQQ':  [
        {'symbol': 'VGT',  'name': 'Vanguard Information Technology ETF', 'rationale': 'Tech-sector exposure via a different issuer and a sector index, not the Nasdaq-100.'},
        {'symbol': 'XLK',  'name': 'Technology Select Sector SPDR Fund', 'rationale': 'Tech sector vs Nasdaq-100 — different index, different issuer.'},
    ],
    # ── International developed ────────────────────────────────────────────
    'VEA':  [
        {'symbol': 'IEFA', 'name': 'iShares Core MSCI EAFE ETF', 'rationale': 'MSCI EAFE IMI vs FTSE Developed All Cap ex US — different issuer and index.'},
        {'symbol': 'SCHF', 'name': 'Schwab International Equity ETF', 'rationale': 'FTSE Developed ex US — different issuer.'},
    ],
    'EFA':  [
        {'symbol': 'VEA',  'name': 'Vanguard FTSE Developed Markets ETF', 'rationale': 'FTSE Developed vs MSCI EAFE — different issuer, different index.'},
        {'symbol': 'SCHF', 'name': 'Schwab International Equity ETF', 'rationale': 'Different issuer and index.'},
    ],
    # ── Emerging markets ───────────────────────────────────────────────────
    'VWO':  [
        {'symbol': 'IEMG', 'name': 'iShares Core MSCI Emerging Markets ETF', 'rationale': 'MSCI EM vs FTSE EM — different issuer, different index (note FTSE excludes South Korea).'},
        {'symbol': 'SCHE', 'name': 'Schwab Emerging Markets Equity ETF', 'rationale': 'FTSE EM, different issuer.'},
    ],
    'EEM':  [
        {'symbol': 'VWO',  'name': 'Vanguard FTSE Emerging Markets ETF', 'rationale': 'FTSE EM vs MSCI EM — different issuer, different index.'},
        {'symbol': 'IEMG', 'name': 'iShares Core MSCI Emerging Markets ETF', 'rationale': 'Same MSCI family but Core variant — note overlap risk; prefer VWO.'},
    ],
    # ── US bond aggregate ──────────────────────────────────────────────────
    'AGG':  [
        {'symbol': 'BND',  'name': 'Vanguard Total Bond Market ETF', 'rationale': 'Bloomberg US Agg Float Adjusted vs Bloomberg US Agg — different issuer, near-identical exposure.'},
        {'symbol': 'SCHZ', 'name': 'Schwab US Aggregate Bond ETF', 'rationale': 'Bloomberg US Agg — different issuer.'},
    ],
    'BND':  [
        {'symbol': 'AGG',  'name': 'iShares Core US Aggregate Bond ETF', 'rationale': 'Bloomberg US Agg vs Float Adjusted variant — different issuer.'},
        {'symbol': 'SCHZ', 'name': 'Schwab US Aggregate Bond ETF', 'rationale': 'Different issuer.'},
    ],
    # ── Treasury ladders ───────────────────────────────────────────────────
    'TLT':  [
        {'symbol': 'VGLT', 'name': 'Vanguard Long-Term Treasury ETF', 'rationale': '20+ Year Treasury exposure via a different issuer.'},
    ],
    'IEF':  [
        {'symbol': 'VGIT', 'name': 'Vanguard Intermediate-Term Treasury ETF', 'rationale': '7-10 Year Treasury exposure via a different issuer.'},
    ],
}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """One harvest candidate — a position underwater in a taxable account."""
    symbol: Optional[str]
    name: Optional[str]
    account: Optional[str]
    market_value: float
    cost_basis: float
    unrealized_loss: float           # positive number — dollars of loss
    loss_pct: float                  # % decline from cost basis (positive)
    holding_period: str              # 'short_term' | 'long_term' | 'unknown'
    acquisition_date: Optional[str]  # ISO date when known, else None
    source: str                      # source filename for traceability


@dataclass
class Budget:
    """Realized gain/loss budget — capacity to absorb harvested losses."""
    year: int
    realized_short_term: Optional[float]  # None when transaction history absent
    realized_long_term: Optional[float]
    carryforward_loss: Optional[float]    # prior-year unused losses
    available_to_offset: Optional[float]
    source: str                           # 'transactions' | 'unknown'
    note: str                             # human-readable degradation message


@dataclass
class Replacement:
    """A non-substantially-identical security that preserves similar exposure."""
    symbol: str
    name: str
    rationale: str
    similarity: str  # 'different_issuer_index' | 'sector_proxy' | 'asset_class_proxy'


@dataclass
class WashSaleStatus:
    """Result of scanning the household for substantially-identical purchases."""
    symbol: str
    status: str                    # 'clear' | 'potential' | 'confirmed'
    accounts_holding: List[str]    # account labels where the symbol currently appears
    note: str                      # explanation suitable for the advisor
    lookback_days: int = WASH_SALE_WINDOW_DAYS
    forward_days: int = WASH_SALE_WINDOW_DAYS


# ---------------------------------------------------------------------------
# Internal SQL helpers (mirror brief_generator's patterns)
# ---------------------------------------------------------------------------


def _role_map(table_meta: Dict[str, Any]) -> Dict[str, str]:
    roles: Dict[str, str] = {}
    for col in table_meta.get('columns', []):
        role = col.get('role')
        if role and role not in roles:
            roles[role] = col['sql_name']
    return roles


def _prefer_view(db_path: str, base_table: str, role_map: Dict[str, str]) -> str:
    """Use the `__by_symbol` rollup view when present so lots don't double-count."""
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


def _holding_period(acq_date: Optional[str], reference: Optional[date] = None) -> str:
    """Classify a holding's period as short_term / long_term / unknown.

    Short-term = held one year or less. Acquisition date is ISO-shaped or
    common US date strings from the brokerage export.
    """
    if not acq_date:
        return 'unknown'
    parsed: Optional[date] = None
    s = str(acq_date).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y', '%Y/%m/%d', '%d-%b-%Y'):
        try:
            parsed = datetime.strptime(s, fmt).date()
            break
        except ValueError:
            continue
    if parsed is None:
        # ISO datetime fallback
        try:
            parsed = datetime.fromisoformat(s.replace('Z', '+00:00')).date()
        except (ValueError, TypeError):
            return 'unknown'
    today = reference or datetime.now(tz=timezone.utc).date()
    days_held = (today - parsed).days
    if days_held < 0:
        return 'unknown'
    return 'long_term' if days_held > LONG_TERM_HOLDING_DAYS else 'short_term'


def _detect_acquisition_col(table_meta: Dict[str, Any]) -> Optional[str]:
    """Best-effort lookup of an acquisition / open-date column on a Holdings table.

    Vendors don't agree on a name and the role detector doesn't tag this column
    today (it's lot-only data, not a Position-level role). Match a small set of
    well-known patterns so short-term vs long-term classification works on the
    files where the data exists, and degrade to 'unknown' otherwise.
    """
    candidates_norm = {
        'acquisition_date', 'acquired_date', 'open_date', 'date_acquired',
        'purchase_date', 'trade_date', 'tax_lot_open_date', 'lot_open_date',
        'original_purchase_date',
    }
    for col in table_meta.get('columns', []):
        original = (col.get('name') or '').strip().lower()
        norm = re.sub(r'[\s_\-/]+', '_', original).strip('_')
        if norm in candidates_norm:
            return col['sql_name']
    return None


# ---------------------------------------------------------------------------
# 1. scan_unrealized_losses
# ---------------------------------------------------------------------------


def scan_unrealized_losses(
    store: Any,
    collection_id: Optional[str] = None,
    min_loss: float = DEFAULT_MIN_LOSS,
    min_loss_pct: Optional[float] = None,
    taxable_only: bool = True,
) -> List[Candidate]:
    """Walk every Holdings table and return underwater Positions.

    Parameters
    ----------
    store:
        A HoldingsStore for one Collection (one household).
    collection_id:
        Carried through for traceability only — does not affect the scan.
    min_loss:
        Minimum unrealized loss (dollars). Default 500. A position with a
        $200 paper loss isn't worth a trade ticket.
    min_loss_pct:
        Optional. When set, filter to positions where the loss is at least
        this percentage of cost basis. Useful for "show me anything down 10%
        or more" queries.
    taxable_only:
        When True (default), excludes IRA / Roth / 401(k) / HSA / 529 / etc.
        Losses in those accounts produce no tax offset. The detector is
        conservative — when an account name is ambiguous it's TREATED AS
        TAXABLE so we don't silently drop a real candidate.

    Returns
    -------
    list[Candidate], sorted with short-term losses first (offset higher
    ordinary-income rates), then by descending absolute loss.
    """
    candidates: List[Candidate] = []

    for table_meta in store.list_tables():
        if not table_meta.get('financial_roles'):
            continue
        roles = _role_map(table_meta)
        mv_col = roles.get('market_value')
        cb_col = roles.get('cost_basis')
        if not (mv_col and cb_col):
            continue

        ticker_col = roles.get('ticker')
        name_col = roles.get('name') or ticker_col or roles.get('cusip')
        account_col = roles.get('account')
        # Acquisition date sits at the lot level; query the BASE table when
        # we have one so we can carry the date alongside per-symbol losses.
        # The rollup view drops non-summable columns.
        base_table = table_meta['table_name']
        acq_col = _detect_acquisition_col(table_meta)
        # When we have acquisition data, query the lot-level table so we can
        # pull ST/LT classification per row. Otherwise prefer the rollup.
        table = base_table if acq_col else _prefer_view(str(store.db_path), base_table, roles)
        filename = table_meta.get('filename', base_table)

        select_parts = [
            f'"{mv_col}" AS market_value',
            f'"{cb_col}" AS cost_basis',
        ]
        if name_col:
            select_parts.append(f'"{name_col}" AS name')
        if ticker_col and ticker_col != name_col:
            select_parts.append(f'"{ticker_col}" AS symbol')
        if account_col:
            select_parts.append(f'"{account_col}" AS account')
        if acq_col:
            select_parts.append(f'"{acq_col}" AS acquisition_date')

        loss_expr = f'("{cb_col}" - "{mv_col}")'
        sql = (
            f'SELECT {", ".join(select_parts)} FROM "{table}" '
            f'WHERE "{mv_col}" IS NOT NULL AND "{cb_col}" IS NOT NULL '
            f'AND "{mv_col}" < "{cb_col}" '
            f'AND {loss_expr} >= ?'
        )

        uri = f'file:{store.db_path}?mode=ro'
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, (min_loss,)).fetchall()
            except sqlite3.Error:
                continue

        for row in rows:
            mv = row['market_value']
            cb = row['cost_basis']
            if mv is None or cb is None:
                continue
            loss = float(cb) - float(mv)
            if loss <= 0:
                continue
            account = row['account'] if 'account' in row.keys() else None
            if taxable_only and is_tax_deferred(account):
                continue
            if min_loss_pct is not None and cb > 0:
                pct = loss / float(cb) * 100.0
                if pct < min_loss_pct:
                    continue
            acq_iso = row['acquisition_date'] if 'acquisition_date' in row.keys() else None
            holding_period = _holding_period(acq_iso) if acq_iso else 'unknown'
            symbol = row['symbol'] if 'symbol' in row.keys() else None
            name = row['name'] if 'name' in row.keys() else None
            candidates.append(Candidate(
                symbol=str(symbol).strip() if symbol else None,
                name=str(name).strip() if name else None,
                account=str(account).strip() if account else None,
                market_value=float(mv),
                cost_basis=float(cb),
                unrealized_loss=loss,
                loss_pct=(loss / float(cb) * 100.0) if cb > 0 else 0.0,
                holding_period=holding_period,
                acquisition_date=acq_iso if acq_iso else None,
                source=filename,
            ))

    # Short-term losses first (they offset higher ordinary-income rates), then
    # by absolute loss size. 'unknown' sorts after long-term so the advisor
    # sees confirmed offsets at the top.
    period_rank = {'short_term': 0, 'long_term': 1, 'unknown': 2}
    candidates.sort(key=lambda c: (period_rank[c.holding_period], -c.unrealized_loss))
    return candidates


# ---------------------------------------------------------------------------
# 2. gain_loss_budget
# ---------------------------------------------------------------------------


def gain_loss_budget(
    store: Any,
    collection_id: Optional[str] = None,
    year: Optional[int] = None,
) -> Budget:
    """Estimate realized YTD gains/losses + carryforward capacity.

    Brokerage Holdings exports almost never carry transaction history — the
    columns are point-in-time. This function looks for an explicit
    `realized_gain` / `realized_loss` role (rare) and otherwise returns a
    Budget with `source='unknown'` and a note pointing the advisor at the
    1099-B / Realized G/L export. Keeping the function in the API now means
    the LLM and the UI can render the right placeholder without us shipping
    a downstream change when transaction support lands.
    """
    target_year = year or datetime.now(tz=timezone.utc).year
    realized_st: Optional[float] = None
    realized_lt: Optional[float] = None

    for table_meta in store.list_tables():
        roles = _role_map(table_meta)
        # Look for a column whose original header contains "realized" — the
        # role detector folds these into `pnl` today, so we re-scan the raw
        # column names rather than relying on roles.
        realized_cols: List[str] = []
        for col in table_meta.get('columns', []):
            original = (col.get('name') or '').lower()
            if 'realized' in original and 'unrealized' not in original:
                realized_cols.append(col['sql_name'])
        if not realized_cols:
            continue

        table = table_meta['table_name']
        uri = f'file:{store.db_path}?mode=ro'
        with sqlite3.connect(uri, uri=True) as conn:
            for sql_col in realized_cols:
                try:
                    val = conn.execute(
                        f'SELECT SUM("{sql_col}") FROM "{table}"'
                    ).fetchone()[0]
                except sqlite3.Error:
                    continue
                if val is None:
                    continue
                # Heuristic: column name carrying 'short' goes into ST bucket;
                # otherwise treat as LT (or aggregate when the column is just
                # "realized_gain"). Conservative — when uncertain we attribute
                # to LT so the harvest plan understates ST offset capacity.
                lower_name = sql_col.lower()
                if 'short' in lower_name or 'st_' in lower_name:
                    realized_st = (realized_st or 0.0) + float(val)
                elif 'long' in lower_name or 'lt_' in lower_name:
                    realized_lt = (realized_lt or 0.0) + float(val)
                else:
                    realized_lt = (realized_lt or 0.0) + float(val)

    if realized_st is None and realized_lt is None:
        return Budget(
            year=target_year,
            realized_short_term=None,
            realized_long_term=None,
            carryforward_loss=None,
            available_to_offset=None,
            source='unknown',
            note=(
                'No realized-gain columns found in this collection. Upload a '
                '1099-B or a Realized Gain/Loss export to estimate the '
                'harvest budget. Until then, treat every candidate loss as '
                'ordinary capacity, capped by the IRS $3,000/yr ordinary-'
                'income offset for net losses above the year\'s realized gains.'
            ),
        )

    available = (realized_st or 0.0) + (realized_lt or 0.0)
    return Budget(
        year=target_year,
        realized_short_term=realized_st,
        realized_long_term=realized_lt,
        carryforward_loss=None,
        available_to_offset=available,
        source='transactions',
        note=(
            f'Realized YTD gains: ST {realized_st or 0:,.0f}, '
            f'LT {realized_lt or 0:,.0f}. Net positive realized gains can be '
            f'offset $-for-$ by harvested losses; net losses above gains can '
            f'offset up to $3,000 of ordinary income, with the remainder '
            f'carrying forward.'
        ),
    )


# ---------------------------------------------------------------------------
# 3. suggest_replacements
# ---------------------------------------------------------------------------


def suggest_replacements(
    symbol: str,
    asset_class: Optional[str] = None,
    sector: Optional[str] = None,
    classify_fn: Optional[Any] = None,
) -> List[Replacement]:
    """Suggest non-substantially-identical replacements that preserve exposure.

    Resolution order:

      1. Curated table — covers the common ETFs (SPY, QQQ, AGG, etc.) where a
         specific different-issuer / different-index swap is well-known.
      2. Sector proxy — when the symbol classifies into a sector via
         get_security_classification, suggest the SPDR sector ETF.
      3. Asset-class proxy — last resort, broad-market ETF for the asset
         class.

    Parameters
    ----------
    symbol:
        The ticker being harvested.
    asset_class / sector:
        Optional hints when the caller already has them (e.g. from the
        Candidate row). When omitted, classification is fetched lazily via
        ``classify_fn`` (defaults to get_security_classification).
    classify_fn:
        Injection point for tests — same signature as
        services.market_data.classification.get_security_classification.
    """
    if not symbol:
        return []
    sym = str(symbol).strip().upper()

    if sym in _REPLACEMENT_TABLE:
        return [
            Replacement(
                symbol=r['symbol'],
                name=r['name'],
                rationale=r['rationale'],
                similarity='different_issuer_index',
            )
            for r in _REPLACEMENT_TABLE[sym]
        ]

    # Lazy classification fetch — only if hints weren't passed.
    if asset_class is None or sector is None:
        if classify_fn is None:
            try:
                from services.market_data.classification import get_security_classification
                classify_fn = get_security_classification
            except Exception:
                classify_fn = None
        if classify_fn is not None:
            try:
                payload = classify_fn(sym) or {}
            except Exception:
                payload = {}
            if 'error' not in payload:
                asset_class = asset_class or payload.get('asset_class')
                sector = sector or payload.get('sector')

    sector_etf = _SECTOR_PROXY.get((sector or '').strip().lower())
    if sector_etf:
        return [
            Replacement(
                symbol=sector_etf['symbol'],
                name=sector_etf['name'],
                rationale=(
                    f"Sector proxy for {sector}. Holds many issuers in the same "
                    f"sector — preserves sector beta without being substantially "
                    f"identical to {sym}."
                ),
                similarity='sector_proxy',
            ),
        ]

    asset_etf = _ASSET_CLASS_PROXY.get((asset_class or '').strip().lower())
    if asset_etf:
        return [
            Replacement(
                symbol=asset_etf['symbol'],
                name=asset_etf['name'],
                rationale=(
                    f"Broad {asset_class} proxy. Last-resort swap when no closer "
                    f"match exists; expect more tracking error vs {sym}."
                ),
                similarity='asset_class_proxy',
            ),
        ]

    return []


_SECTOR_PROXY: Dict[str, Dict[str, str]] = {
    'technology':                {'symbol': 'XLK',  'name': 'Technology Select Sector SPDR'},
    'information technology':    {'symbol': 'XLK',  'name': 'Technology Select Sector SPDR'},
    'healthcare':                {'symbol': 'XLV',  'name': 'Health Care Select Sector SPDR'},
    'financial services':        {'symbol': 'XLF',  'name': 'Financial Select Sector SPDR'},
    'financials':                {'symbol': 'XLF',  'name': 'Financial Select Sector SPDR'},
    'consumer cyclical':         {'symbol': 'XLY',  'name': 'Consumer Discretionary Select Sector SPDR'},
    'consumer discretionary':    {'symbol': 'XLY',  'name': 'Consumer Discretionary Select Sector SPDR'},
    'consumer defensive':        {'symbol': 'XLP',  'name': 'Consumer Staples Select Sector SPDR'},
    'consumer staples':          {'symbol': 'XLP',  'name': 'Consumer Staples Select Sector SPDR'},
    'energy':                    {'symbol': 'XLE',  'name': 'Energy Select Sector SPDR'},
    'industrials':               {'symbol': 'XLI',  'name': 'Industrial Select Sector SPDR'},
    'utilities':                 {'symbol': 'XLU',  'name': 'Utilities Select Sector SPDR'},
    'real estate':               {'symbol': 'XLRE', 'name': 'Real Estate Select Sector SPDR'},
    'basic materials':           {'symbol': 'XLB',  'name': 'Materials Select Sector SPDR'},
    'materials':                 {'symbol': 'XLB',  'name': 'Materials Select Sector SPDR'},
    'communication services':    {'symbol': 'XLC',  'name': 'Communication Services Select Sector SPDR'},
}

_ASSET_CLASS_PROXY: Dict[str, Dict[str, str]] = {
    'equity':       {'symbol': 'VTI', 'name': 'Vanguard Total Stock Market ETF'},
    'etf':          {'symbol': 'VTI', 'name': 'Vanguard Total Stock Market ETF'},
    'mutual_fund':  {'symbol': 'VTI', 'name': 'Vanguard Total Stock Market ETF'},
    'fixed_income': {'symbol': 'BND', 'name': 'Vanguard Total Bond Market ETF'},
    'bond':         {'symbol': 'BND', 'name': 'Vanguard Total Bond Market ETF'},
}


# ---------------------------------------------------------------------------
# 4. check_wash_sale
# ---------------------------------------------------------------------------


def check_wash_sale(
    symbol: str,
    stores: Iterable[Any],
    lookback_days: int = WASH_SALE_WINDOW_DAYS,
    forward_days: int = WASH_SALE_WINDOW_DAYS,
    transaction_lookup: Optional[Any] = None,
) -> WashSaleStatus:
    """Scan every household account for a substantially-identical position.

    Without per-trade transaction history (the common case for Holdings-only
    exports), the strongest signal we have is "the security currently appears
    in another household account." If it does, a recent purchase OR a planned
    rebuy in the next 30 days could trigger a wash sale, so we mark the
    candidate as 'potential' and surface the holding accounts.

    When ``transaction_lookup`` is provided, it must be a callable
    ``(symbol, store) -> list[date]`` returning purchase dates within the
    relevant window; we then promote 'potential' to 'confirmed' if any date
    falls inside ``[today - lookback_days, today + forward_days]``.

    The household scope — passing every account in the household, including
    spousal IRA/Roth — is the moat over single-account TLH tools. The IRS
    treats the spouse's wash-sale-window purchase as YOURS for purposes of
    the rule.
    """
    if not symbol:
        return WashSaleStatus(
            symbol='', status='clear', accounts_holding=[],
            note='No symbol provided.',
        )
    sym = str(symbol).strip().upper()
    holding_accounts: List[str] = []
    transaction_dates: List[date] = []

    for store in stores:
        for table_meta in store.list_tables():
            roles = _role_map(table_meta)
            ticker_col = roles.get('ticker')
            account_col = roles.get('account')
            if not ticker_col:
                continue
            base_table = table_meta['table_name']
            table = _prefer_view(str(store.db_path), base_table, roles)

            select_cols = [f'UPPER(TRIM("{ticker_col}")) AS sym']
            if account_col:
                select_cols.append(f'"{account_col}" AS account')

            sql = (
                f'SELECT {", ".join(select_cols)} FROM "{table}" '
                f'WHERE UPPER(TRIM("{ticker_col}")) = ?'
            )
            uri = f'file:{store.db_path}?mode=ro'
            with sqlite3.connect(uri, uri=True) as conn:
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(sql, (sym,)).fetchall()
                except sqlite3.Error:
                    continue
            for row in rows:
                acct = row['account'] if 'account' in row.keys() else None
                label = str(acct).strip() if acct else table_meta.get('filename', base_table)
                if label and label not in holding_accounts:
                    holding_accounts.append(label)

        if transaction_lookup is not None:
            try:
                dates = list(transaction_lookup(sym, store) or [])
            except Exception:
                dates = []
            for d in dates:
                if isinstance(d, date):
                    transaction_dates.append(d)

    today = datetime.now(tz=timezone.utc).date()
    window_start = today - timedelta(days=lookback_days)
    window_end = today + timedelta(days=forward_days)
    in_window = [d for d in transaction_dates if window_start <= d <= window_end]

    if in_window:
        return WashSaleStatus(
            symbol=sym,
            status='confirmed',
            accounts_holding=holding_accounts,
            note=(
                f'WASH-SALE TRIGGERED: {len(in_window)} purchase(s) of {sym} '
                f'found within the {lookback_days}d-before / {forward_days}d-'
                f'after window across the household. Selling at a loss now '
                f'will disallow the loss; basis transfers to the replacement '
                f'shares. Wait until '
                f'{(max(in_window) + timedelta(days=forward_days + 1)).isoformat()} '
                f'or pick a non-substantially-identical replacement.'
            ),
            lookback_days=lookback_days,
            forward_days=forward_days,
        )

    if holding_accounts:
        return WashSaleStatus(
            symbol=sym,
            status='potential',
            accounts_holding=holding_accounts,
            note=(
                f'{sym} is currently held in: {", ".join(holding_accounts)}. '
                f'Without trade history we cannot confirm whether a purchase '
                f'happened in the {lookback_days}d-before window or whether a '
                f'rebuy is planned in the {forward_days}d-after window. The '
                f'wash-sale rule applies across the entire household — including '
                f'spousal IRA / Roth — so confirm no buys (e.g. dividend '
                f'reinvestment, automatic contributions) occurred recently in '
                f'any of these accounts before harvesting.'
            ),
            lookback_days=lookback_days,
            forward_days=forward_days,
        )

    return WashSaleStatus(
        symbol=sym,
        status='clear',
        accounts_holding=[],
        note=(
            f'{sym} not found in any other household account. Wash-sale '
            f'risk from current household holdings is low, but still confirm '
            f'no recent purchases (DRIP, automatic investing, manual buys '
            f'in the {lookback_days}d window) before placing the sell.'
        ),
        lookback_days=lookback_days,
        forward_days=forward_days,
    )


# ---------------------------------------------------------------------------
# Composer — feeds the MCP tool
# ---------------------------------------------------------------------------


@dataclass
class HarvestPlan:
    collection_id: Optional[str]
    generated_at: str
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    budget: Dict[str, Any] = field(default_factory=dict)
    wash_sale_warnings: List[Dict[str, Any]] = field(default_factory=list)
    guardrails: List[str] = field(default_factory=list)
    totals: Dict[str, Any] = field(default_factory=dict)


_GUARDRAILS = [
    'Wash-sale rules apply across the entire household — including spousal '
    'IRA, Roth, and 401(k) accounts. Confirm no substantially-identical '
    'purchases (including DRIP and automatic investing) occurred in the '
    '30 days before, or are planned in the 30 days after, the sell.',
    'Tax savings shown are GROSS estimates. Transaction costs, bid/ask '
    'spreads, and replacement-fund tracking error reduce the net benefit. '
    'Small candidates often don\'t clear the breakeven.',
    'Harvesting RESETS cost basis to the new lower price — this defers tax, '
    'it does not eliminate it. Future appreciation creates a larger gain on '
    'eventual sale; the benefit is the time value of the deferred tax plus '
    'rate arbitrage between offset year and realisation year.',
    'Net losses above realised gains offset only $3,000 of ordinary income '
    'per year; the remainder carries forward indefinitely. Plan harvest '
    'size against current-year realised gains, not in isolation.',
]


def build_harvest_plan(
    store: Any,
    collection_id: Optional[str] = None,
    household_stores: Optional[List[Any]] = None,
    min_loss: float = DEFAULT_MIN_LOSS,
    min_loss_pct: Optional[float] = None,
    max_candidates: int = 25,
    classify_fn: Optional[Any] = None,
) -> HarvestPlan:
    """Compose the four primitives into a complete harvest plan.

    Parameters
    ----------
    store:
        HoldingsStore for the active collection (the household).
    household_stores:
        Optional additional stores to include in the wash-sale check (e.g.
        when an advisor splits a household across collections — common when
        a spouse has a separately-titled brokerage relationship). Defaults
        to ``[store]``.
    """
    candidates = scan_unrealized_losses(
        store,
        collection_id=collection_id,
        min_loss=min_loss,
        min_loss_pct=min_loss_pct,
        taxable_only=True,
    )[:max_candidates]

    budget = gain_loss_budget(store, collection_id=collection_id)

    wash_check_stores = household_stores or [store]

    cand_payload: List[Dict[str, Any]] = []
    wash_warnings: List[Dict[str, Any]] = []
    total_loss = 0.0
    short_term_loss = 0.0
    long_term_loss = 0.0

    for cand in candidates:
        replacements = suggest_replacements(
            cand.symbol or '',
            classify_fn=classify_fn,
        ) if cand.symbol else []
        wash = check_wash_sale(
            cand.symbol or '',
            stores=wash_check_stores,
        ) if cand.symbol else WashSaleStatus(
            symbol='', status='clear', accounts_holding=[],
            note='No symbol on this candidate; wash-sale check skipped.',
        )

        cand_payload.append({
            'symbol': cand.symbol,
            'name': cand.name,
            'account': cand.account,
            'market_value': cand.market_value,
            'cost_basis': cand.cost_basis,
            'unrealized_loss': cand.unrealized_loss,
            'loss_pct': round(cand.loss_pct, 2),
            'holding_period': cand.holding_period,
            'acquisition_date': cand.acquisition_date,
            'source': cand.source,
            'replacements': [
                {
                    'symbol': r.symbol,
                    'name': r.name,
                    'rationale': r.rationale,
                    'similarity': r.similarity,
                }
                for r in replacements
            ],
            'wash_sale': {
                'status': wash.status,
                'accounts_holding': wash.accounts_holding,
                'note': wash.note,
            },
        })

        total_loss += cand.unrealized_loss
        if cand.holding_period == 'short_term':
            short_term_loss += cand.unrealized_loss
        elif cand.holding_period == 'long_term':
            long_term_loss += cand.unrealized_loss

        if wash.status in ('potential', 'confirmed'):
            wash_warnings.append({
                'symbol': cand.symbol,
                'status': wash.status,
                'accounts_holding': wash.accounts_holding,
                'note': wash.note,
            })

    return HarvestPlan(
        collection_id=collection_id,
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        candidates=cand_payload,
        budget={
            'year': budget.year,
            'realized_short_term': budget.realized_short_term,
            'realized_long_term': budget.realized_long_term,
            'carryforward_loss': budget.carryforward_loss,
            'available_to_offset': budget.available_to_offset,
            'source': budget.source,
            'note': budget.note,
        },
        wash_sale_warnings=wash_warnings,
        guardrails=list(_GUARDRAILS),
        totals={
            'candidate_count': len(cand_payload),
            'total_unrealized_loss': total_loss,
            'short_term_loss': short_term_loss,
            'long_term_loss': long_term_loss,
            'unknown_period_loss': total_loss - short_term_loss - long_term_loss,
        },
    )
