"""Financial column-role detection.

Maps a column name to a semantic role (``ticker``, ``market_value``, ``pnl``,
…) using regex patterns. Used directly by
:class:`~services.financial.holdings_store.HoldingsStore` during ingest, and
by the privacy preflight code that decides whether a column is sensitive.
"""

from __future__ import annotations

import re
from typing import Optional

# (role_name, name_regex) — matched against a normalised column name
_ROLE_PATTERNS = [
    ('ticker',        r'^(ticker|symbol|sym|security_?(id|code|symbol))$'),
    ('cusip',         r'^(cusip)$'),
    ('isin',          r'^(isin)$'),
    ('name',          r'^(name|security_?name|description|desc|holding|instrument|issuer)$'),
    ('quantity',      r'^(qty|quantity|shares|units|contracts|position|balance_units)$'),
    ('price',         r'^(price|px|last|close|mark|nav|unit_?price|last_?price)$'),
    ('cost_basis',    r'^(cost|cost_?basis|book|book_?value|purchase_?price|avg_?cost|acquisition_?cost|unit_?cost|original_?total_?cost|current_?total_?cost)$'),
    ('market_value',  r'^(market_?value|mv|value|notional|exposure|balance|position_?value|market_?val)$'),
    ('pnl',           r'^(pnl|p_?l|gain|gain_?loss|unrealized|realized|profit|total_?return_?dollar)$'),
    ('weight',        r'^(weight|wt|alloc|allocation|pct|percent_?of_?portfolio|port_?weight)$'),
    ('asset_class',   r'^(asset_?class|class|type|instrument_?type|category|security_?type)$'),
    ('sector',        r'^(sector|industry|gics|sub_?sector|industry_?group)$'),
    ('region',        r'^(region|country|geo|geography|domicile)$'),
    ('currency',      r'^(ccy|currency|fx|curr)$'),
    ('date',          r'^(date|as_?of|trade_?date|settle_?date|report_?date|period|valuation_?date)$'),
    ('return',        r'^(return|ret|yield|perf|performance|ytd|mtd|qtd|total_?return|rtn|current_?yield|gain_?loss_?pct)$'),
    ('account',       r'^(account|acct|portfolio|fund|strategy|sleeve)$'),
    ('maturity',      r'^(maturity|maturity_?date|expiry|expiration)$'),
    ('coupon',        r'^(coupon|coupon_?rate|rate|projected_?annual_?income)$'),
    ('rating',        r'^(rating|credit_?rating|moody|s_?p_?rating)$'),
]


def detect_financial_role(col_name: str, col_type: str) -> Optional[str]:
    """Detect the semantic financial role of a column from its name.

    Returns a role string (e.g. ``'ticker'``, ``'market_value'``) or ``None``.
    ``col_type`` is available for future type-gated rules but is not used now.
    """
    normalized = re.sub(r'[^a-z0-9]+', '_', col_name.lower()).strip('_')
    for role, pattern in _ROLE_PATTERNS:
        if re.match(pattern, normalized):
            return role
    return None
