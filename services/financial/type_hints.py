"""Financial-format type detection and number parsing.

Recognises currency- and percent-formatted cells in brokerage exports
(``$1,234.56``, ``(408.80)``, ``12.5%``, ``100 USD``, ``2.3M``). Exposed as
two :class:`~services.tabular.inference.TypeExtension` instances that
:class:`~services.financial.holdings_store.HoldingsStore` layers onto the
generic tabular inference.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from services.tabular.inference import NULL_MARKERS, TypeExtension


_CURRENCY_RE = re.compile(
    r'^\s*[$€£¥₹]\s*-?[\d,]*\.?\d+\s*$'
    r'|^\s*\(?\s*[$€£¥₹]\s*[\d,]*\.?\d+\s*\)?\s*$'
    r'|^\s*-?[\d,]*\.?\d+\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|HKD|SGD)\s*$',
    re.IGNORECASE,
)

_PERCENT_RE = re.compile(r'^\s*-?[\d,]*\.?\d+\s*%\s*$')

_KMB_RE = re.compile(r'^(-?[\d,]*\.?\d+)\s*([kmb])$', re.IGNORECASE)


def _parse_number(val: Any) -> Optional[float]:
    """Parse a financial number string to float.

    Handles:
    - Currency symbols: ``$``, ``€``, ``£``, etc.
    - Thousands commas: ``"1,234.56"`` → ``1234.56``
    - ISO currency code suffix: ``"100 USD"`` → ``100.0``
    - Accounting-negative: ``"(1,234.56)"`` or ``"$(1,234.56)"`` → ``-1234.56``
    - Percent sign: stripped (caller decides whether to divide by 100)
    - K/M/B suffix: ``"1.5K"`` → ``1500.0``, ``"2.3M"`` → ``2_300_000.0``
    - Null markers (``'-'``, ``'N/A'``, ``'Provide'``, etc.) → ``None``
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in NULL_MARKERS:
        return None

    # Detect accounting negative BEFORE stripping parens
    negative = False
    if s.startswith('(') and s.endswith(')'):
        negative = True
        s = s[1:-1].strip()

    s = re.sub(r'[$€£¥₹,\s]', '', s)
    s = re.sub(r'(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|HKD|SGD)$', '', s, flags=re.IGNORECASE).strip()

    if s.endswith('%'):
        s = s[:-1].strip()

    kmb = _KMB_RE.match(s)
    if kmb:
        base_str = kmb.group(1).replace(',', '')
        suffix = kmb.group(2).upper()
        try:
            base = float(base_str)
        except ValueError:
            return None
        multiplier = {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}[suffix]
        result = base * multiplier
        return -result if negative else result

    try:
        result = float(s)
        return -result if negative else result
    except ValueError:
        return None


def _detect_currency(s: str) -> bool:
    """Return True if *s* looks like a currency-formatted value."""
    return bool(_CURRENCY_RE.match(s))


def _detect_percent(s: str) -> bool:
    """Return True if *s* looks like a percentage value."""
    return bool(_PERCENT_RE.match(s))


CURRENCY_EXTENSION = TypeExtension(
    name='currency',
    sqlite_type='REAL',
    detector=_detect_currency,
    coerce=_parse_number,
)

PERCENT_EXTENSION = TypeExtension(
    name='percent',
    sqlite_type='REAL',
    detector=_detect_percent,
    coerce=_parse_number,
)

FINANCIAL_TYPE_EXTENSIONS = (CURRENCY_EXTENSION, PERCENT_EXTENSION)
